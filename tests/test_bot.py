import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import httpx
import pytest

from app.clients import PropstackClient, RetellClient, verify_retell_signature
from app.config import Settings
from app.db import DB
from app.logic import CallWindow, anrede, build_plan, classify_call, normalize_phone, pick_phone
from app.worker import Bot

BER = ZoneInfo("Europe/Berlin")
G = dict(q=725637, k=725638, n=725639, f=725663, v=725664)


# ---------------------------------------------------------------------------
# Hilfen: gefälschtes Propstack und Retell
# ---------------------------------------------------------------------------
class FakeAPIs:
    def __init__(self):
        self.contacts = {
            1: {"id": 1, "salutation": "ms", "first_name": "Anna", "last_name": "Weber", "name": "Anna Weber",
                "home_cell": "0151 23456789", "groups": []},
        }
        self.units = {10: {"id": 10, "title": {"value": "Helle 3-Zimmer-Wohnung in Wersten"}, "city": "Düsseldorf",
                           "broker_id": 7}}
        self.brokers = [{"id": 7, "salutation": "ms", "first_name": "Lisa", "last_name": "Matthijsen",
                         "name": "Lisa Matthijsen"}]
        self.inquiries = []
        self.posts, self.puts, self.calls = [], [], []
        self.call_counter = 0

    def propstack(self, req: httpx.Request):
        p = req.url.path.replace("/v1", "")
        if req.method == "GET" and p == "/activities":
            return httpx.Response(200, json={"data": self.inquiries})
        if req.method == "GET" and p.startswith("/contacts/"):
            return httpx.Response(200, json=self.contacts[int(p.split("/")[-1])])
        if req.method == "GET" and p.startswith("/units/"):
            return httpx.Response(200, json=self.units[int(p.split("/")[-1])])
        if req.method == "GET" and p == "/brokers":
            return httpx.Response(200, json=self.brokers)
        if req.method == "POST" and p == "/tasks":
            self.posts.append(json.loads(req.content))
            return httpx.Response(201, json={"id": len(self.posts)})
        if req.method == "PUT" and p.startswith("/contacts/"):
            self.puts.append(json.loads(req.content))
            return httpx.Response(200, json={"id": 1})
        return httpx.Response(404)

    def retell(self, req: httpx.Request):
        if req.url.path == "/v2/create-phone-call":
            self.call_counter += 1
            self.calls.append(json.loads(req.content))
            return httpx.Response(201, json={"call_id": f"call{self.call_counter}"})
        return httpx.Response(404)


def make_bot(now, mode="live", **kw):
    apis = FakeAPIs()
    s = Settings()
    s.mode = mode
    s.propstack_api_key = "x"
    s.retell_api_key = "r"
    s.retell_agent_id = "agent"
    s.retell_from_number = "+4921100000"
    s.test_numbers = kw.get("test_numbers", [])
    s.inquiry_delay_min = 5
    s.default_broker_id = 0
    clock = {"now": now}
    ps = PropstackClient("x", "https://api.propstack.de/v1", dry=(mode == "dry"),
                         transport=httpx.MockTransport(apis.propstack))
    rt = RetellClient("r", transport=httpx.MockTransport(apis.retell))
    bot = Bot(s, DB(":memory:"), ps, rt, clock=lambda: clock["now"])
    return bot, apis, clock


def call_payload(call_id, job_id, reason="agent_hangup", data=None, voicemail=False, summary="Kurz gesprochen."):
    return {"call_id": call_id, "metadata": {"job_id": job_id}, "disconnection_reason": reason,
            "duration_ms": 120000, "call_status": "ended",
            "call_analysis": {"in_voicemail": voicemail, "call_summary": summary, "custom_analysis_data": data or {}}}


# Montag, 26.10.2026, 10:00 Uhr Berlin
MON10 = datetime(2026, 10, 26, 10, 0, tzinfo=BER)


# ---------------------------------------------------------------------------
# Telefonnummern und Anrede
# ---------------------------------------------------------------------------
def test_normalize_phone():
    assert normalize_phone("0151 23456789") == "+4915123456789"
    assert normalize_phone("0049 211 1234567") == "+492111234567"
    assert normalize_phone("+49 (0)211 1234567") == "+492111234567"
    assert normalize_phone("123") is None
    assert normalize_phone(None) is None


def test_pick_phone_prefers_mobile():
    assert pick_phone({"home_phone": "0211 1234567", "home_cell": "0151 23456789"}) == "+4915123456789"
    assert pick_phone({"office_phone": "kaputt"}) is None


def test_anrede():
    assert anrede("ms", "Anna", "Weber") == "Frau Weber"
    assert anrede("mr", "Tom", "Meyer") == "Herr Meyer"
    assert anrede(None, "Tom", "Meyer") == "Tom Meyer"
    assert anrede(None, None, None, "Kunde") == "Kunde"


# ---------------------------------------------------------------------------
# Anrufzeiten
# ---------------------------------------------------------------------------
def test_window_weekday_and_sunday():
    w = CallWindow()
    assert w.is_open(MON10)
    assert not w.is_open(datetime(2026, 10, 26, 8, 59, tzinfo=BER))
    assert not w.is_open(datetime(2026, 10, 26, 19, 0, tzinfo=BER))
    assert not w.is_open(datetime(2026, 10, 25, 12, 0, tzinfo=BER))  # Sonntag
    assert w.is_open(datetime(2026, 10, 31, 11, 0, tzinfo=BER))      # Samstag
    assert not w.is_open(datetime(2026, 10, 31, 15, 0, tzinfo=BER))


def test_window_holiday_nrw():
    w = CallWindow()
    allerheiligen = datetime(2026, 11, 1, 11, 0, tzinfo=BER)  # Sonntag + Feiertag
    assert not w.is_open(allerheiligen)
    weihnachten = datetime(2026, 12, 25, 11, 0, tzinfo=BER)
    assert not w.is_open(weihnachten)
    assert w.next_open(weihnachten).date().isoformat() == "2026-12-28"


def test_next_open_evening_goes_to_next_morning():
    w = CallWindow()
    nxt = w.next_open(datetime(2026, 10, 26, 20, 0, tzinfo=BER))
    assert nxt.astimezone(BER) == datetime(2026, 10, 27, 9, 0, tzinfo=BER)
    sat_evening = w.next_open(datetime(2026, 10, 31, 15, 0, tzinfo=BER))
    assert sat_evening.astimezone(BER) == datetime(2026, 11, 2, 9, 0, tzinfo=BER)  # über Sonntag/Allerheiligen


# ---------------------------------------------------------------------------
# Ergebnis bestimmen
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("reason,kind", [
    ("dial_no_answer", "retry"), ("dial_busy", "retry"), ("voicemail_reached", "retry"),
    ("invalid_destination", "invalid"), ("credit_exhausted", "tech"), ("agent_hangup", "talked"),
])
def test_classify_reasons(reason, kind):
    assert classify_call({"disconnection_reason": reason}).kind == kind


def test_classify_voicemail_flag_and_dnc():
    assert classify_call({"disconnection_reason": "agent_hangup", "call_analysis": {"in_voicemail": True}}).kind == "retry"
    r = classify_call({"disconnection_reason": "user_requested_dnc"})
    assert (r.kind, r.ergebnis) == ("talked", "nicht_anrufen")
    r = classify_call({"disconnection_reason": "user_hangup",
                       "call_analysis": {"custom_analysis_data": {"ergebnis": "qualifiziert", "nicht_mehr_anrufen": "true"}}})
    assert r.ergebnis == "nicht_anrufen"
    assert classify_call({"disconnection_reason": "user_hangup",
                          "call_analysis": {"custom_analysis_data": {"ergebnis": "quatsch"}}}).ergebnis == "unklar"


# ---------------------------------------------------------------------------
# Schreibplan nach Leitfaden
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("erg,task,group", [
    ("qualifiziert", True, "group_qualifiziert"),
    ("interesse_ohne_termin", True, None),
    ("rueckruf_gewuenscht", True, None),
    ("kein_interesse", False, "group_kein_interesse"),
    ("nicht_anrufen", False, "group_nicht_anrufen"),
    ("falsche_person", True, None),
    ("unklar", True, None),
    ("nicht_erreicht", False, None),
])
def test_plan_matches_leitfaden(erg, task, group):
    p = build_plan(erg, now=MON10, window=CallWindow())
    assert bool(p.tasks) == task
    assert p.add_group_keys == ([group] if group else [])
    assert p.note_body


def test_plan_qualified_due_two_hours_and_extras():
    p = build_plan("qualifiziert", now=MON10, window=CallWindow(),
                   data={"finanzierungspartner_ok": True, "verkaeufer_ansprache_ok": "true",
                         "besichtigung_wunschzeiten": "Donnerstag ab 17 Uhr", "nutzung": "selbstnutzung"})
    assert p.tasks[0].due == MON10 + timedelta(hours=2)
    assert set(p.add_group_keys) == {"group_qualifiziert", "group_finanzierung_ok", "group_verkaeufer_lead"}
    assert len(p.tasks) == 3
    assert "Donnerstag ab 17 Uhr" in p.note_body and "Selbstnutzung" in p.note_body
    assert "NICHT bestätigt" in p.note_body


def test_plan_escapes_html():
    p = build_plan("unklar", now=MON10, window=CallWindow(), summary="<script>x</script>")
    assert "<script>" not in p.note_body


def test_plan_task_due_after_hours_moves_to_morning():
    evening = datetime(2026, 10, 26, 18, 30, tzinfo=BER)
    p = build_plan("qualifiziert", now=evening, window=CallWindow())
    assert p.tasks[0].due.astimezone(BER) == datetime(2026, 10, 27, 10, 0, tzinfo=BER)


# ---------------------------------------------------------------------------
# Webhook-Signatur
# ---------------------------------------------------------------------------
def test_signature():
    body, key, ts = '{"a":1}', "secret", 1_800_000_000_000
    d = hmac.new(key.encode(), (body + str(ts)).encode(), hashlib.sha256).hexdigest()
    assert verify_retell_signature(body, key, f"v={ts},d={d}", now_ms=ts + 1000)
    assert not verify_retell_signature(body, key, f"v={ts},d={d}", now_ms=ts + 6 * 60 * 1000)  # zu alt
    assert not verify_retell_signature(body + " ", key, f"v={ts},d={d}", now_ms=ts)
    assert not verify_retell_signature(body, key, None)


# ---------------------------------------------------------------------------
# Kompletter Ablauf
# ---------------------------------------------------------------------------
def _inquiry(aid=100, created=MON10):
    return {"id": aid, "client_ids": [1], "property_ids": [10], "broker_id": 7,
            "created_at": created.isoformat()}


def test_first_poll_ignores_backlog():
    bot, apis, clock = make_bot(MON10)
    apis.inquiries = [_inquiry()]
    assert bot.poll_inquiries() == 0
    assert bot.db.recent_jobs() == []


def test_full_flow_qualified():
    bot, apis, clock = make_bot(MON10)
    bot.poll_inquiries()
    apis.inquiries = [_inquiry(created=MON10 + timedelta(minutes=1))]
    clock["now"] = MON10 + timedelta(minutes=3)
    assert bot.poll_inquiries() == 1
    assert bot.tick() == 0  # 5 Minuten Wartezeit noch nicht vorbei
    clock["now"] = MON10 + timedelta(minutes=7)
    assert bot.tick() == 1
    call = apis.calls[0]
    assert call["to_number"] == "+4915123456789"
    assert call["retell_llm_dynamic_variables"]["kunde_anrede"] == "Frau Weber"
    assert call["retell_llm_dynamic_variables"]["makler_name"] == "Frau Matthijsen"
    assert "Wersten" in call["retell_llm_dynamic_variables"]["objekt_titel"]

    job = bot.db.recent_jobs()[0]
    res = bot.handle_call(call_payload("call1", job["id"], data={"ergebnis": "qualifiziert",
                                                                   "finanzierungspartner_ok": True}))
    assert res == "qualifiziert"
    notes = [p for p in apis.posts if not p["task"].get("is_reminder")]
    tasks = [p for p in apis.posts if p["task"].get("is_reminder")]
    assert len(notes) == 1 and len(tasks) == 2
    assert tasks[0]["task"]["broker_id"] == 7
    assert apis.puts[0]["client"]["add_group_ids"] == [G["q"], G["f"]]
    # Webhook doppelt -> nichts doppelt geschrieben
    assert bot.handle_call(call_payload("call1", job["id"])) == "doppelt"
    assert len(apis.posts) == 3


def test_retry_three_times_then_not_reached():
    bot, apis, clock = make_bot(MON10)
    job_id = bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    clock["now"] = MON10 + timedelta(minutes=6)
    for i in range(1, 4):
        assert bot.tick() == 1, f"Versuch {i}"
        res = bot.handle_call(call_payload(f"call{i}", job_id, reason="dial_no_answer"))
        job = bot.db.get_job(job_id)
        if i < 3:
            assert res == "erneut"
            clock["now"] = datetime.fromisoformat(job["next_attempt_at"]) + timedelta(seconds=1)
        else:
            assert res == "nicht_erreicht"
    assert len(apis.calls) == 3
    assert not any(p["task"].get("is_reminder") for p in apis.posts)  # keine Aufgabe bei "nicht erreicht"
    assert apis.puts == []


def test_no_second_call_after_conversation():
    bot, apis, clock = make_bot(MON10)
    job_id = bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    clock["now"] = MON10 + timedelta(minutes=6)
    bot.tick()
    bot.handle_call(call_payload("call1", job_id, data={"ergebnis": "interesse_ohne_termin"}))
    # neue Anfrage zum selben Objekt -> nicht erneut anrufen
    bot.enqueue(activity_id=2, client_id=1, property_id=10, broker_id=7, created=clock["now"])
    assert bot.db.recent_jobs()[0]["status"] == "skipped"


def test_do_not_call_blocks_number():
    bot, apis, clock = make_bot(MON10)
    job_id = bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    clock["now"] = MON10 + timedelta(minutes=6)
    bot.tick()
    assert bot.handle_call(call_payload("call1", job_id, reason="user_requested_dnc")) == "nicht_anrufen"
    assert apis.puts[0]["client"]["add_group_ids"] == [G["n"]]
    assert bot.db.is_blocked("+4915123456789")
    bot.enqueue(activity_id=2, client_id=1, property_id=99, broker_id=7, created=clock["now"])
    assert "Sperrliste" in bot.db.recent_jobs()[0]["note"]


def test_skip_contact_with_merkmal_nicht_anrufen():
    bot, apis, clock = make_bot(MON10)
    apis.contacts[1]["groups"] = [{"id": G["n"], "name": "KI: nicht anrufen"}]
    bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    assert bot.db.recent_jobs()[0]["status"] == "skipped"


def test_no_phone_writes_note_and_skips():
    bot, apis, clock = make_bot(MON10)
    apis.contacts[1]["home_cell"] = None
    bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    assert bot.db.recent_jobs()[0]["status"] == "skipped"
    assert "keine gültige Telefonnummer" in apis.posts[0]["task"]["title"]


def test_test_mode_only_calls_allowlisted():
    bot, apis, clock = make_bot(MON10, mode="test", test_numbers=["+491700000000"])
    bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    assert "TEST_NUMBERS" in bot.db.recent_jobs()[0]["note"]
    bot2, apis2, clock2 = make_bot(MON10, mode="test", test_numbers=["+4915123456789"])
    bot2.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    assert bot2.db.recent_jobs()[0]["status"] == "pending"


def test_dry_mode_calls_nobody_writes_nothing():
    bot, apis, clock = make_bot(MON10, mode="dry")
    bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    clock["now"] = MON10 + timedelta(minutes=6)
    bot.tick()
    assert apis.calls == [] and apis.posts == [] and apis.puts == []


def test_no_calls_outside_window():
    sunday = datetime(2026, 10, 25, 12, 0, tzinfo=BER)
    bot, apis, clock = make_bot(sunday)
    job_id = bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=sunday)
    assert bot.tick() == 0
    job = bot.db.get_job(job_id)
    assert datetime.fromisoformat(job["next_attempt_at"]).astimezone(BER) >= datetime(2026, 10, 26, 9, 0, tzinfo=BER)


def test_parallel_limit():
    bot, apis, clock = make_bot(MON10)
    apis.contacts[2] = dict(apis.contacts[1], id=2, home_cell="0151 99999999")
    bot.enqueue(activity_id=1, client_id=1, property_id=10, broker_id=7, created=MON10)
    bot.enqueue(activity_id=2, client_id=2, property_id=10, broker_id=7, created=MON10)
    clock["now"] = MON10 + timedelta(minutes=6)
    assert bot.tick() == 1
    assert bot.tick() == 0  # erst wenn der erste fertig ist
