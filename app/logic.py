"""Reine Geschäftslogik ohne Netzwerkzugriff – gut testbar.

- Telefonnummern normalisieren und auswählen
- Anrufzeiten (Mo–Fr, Sa, keine Sonn- und NRW-Feiertage)
- Gesprächsergebnis aus dem Retell-Webhook bestimmen
- Plan, was in Propstack geschrieben wird (Notiz, Aufgabe, Merkmale)
"""
from __future__ import annotations

import html
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

import holidays
import phonenumbers

# ---------------------------------------------------------------------------
# Telefonnummern
# ---------------------------------------------------------------------------

PHONE_FIELDS = ("home_cell", "office_cell", "home_phone", "office_phone", "phone")


def normalize_phone(raw: str | None, region: str = "DE") -> str | None:
    """Gibt die Nummer im E.164-Format zurück (+49...) oder None, wenn ungültig."""
    if not raw:
        return None
    raw = str(raw).strip()
    if raw.startswith("00"):
        raw = "+" + raw[2:]
    try:
        num = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException:
        return None
    if not phonenumbers.is_valid_number(num):
        return None
    return phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164)


def pick_phone(contact: dict) -> str | None:
    """Erste gültige Nummer, Mobilnummern zuerst."""
    for f in PHONE_FIELDS:
        n = normalize_phone(contact.get(f))
        if n:
            return n
    return None


# ---------------------------------------------------------------------------
# Anrede
# ---------------------------------------------------------------------------

def anrede(salutation: str | None, first: str | None, last: str | None, fallback: str = "") -> str:
    """'Frau Weber' / 'Herr Meyer'; ohne Anrede der volle Name."""
    last = (last or "").strip()
    first = (first or "").strip()
    if last and salutation in ("ms", "mrs"):
        return f"Frau {last}"
    if last and salutation == "mr":
        return f"Herr {last}"
    full = f"{first} {last}".strip()
    return full or fallback


def anrede_dativ(anrede_text: str) -> str:
    """'Herr Meyer' -> 'Herrn Meyer' (für 'Spreche ich mit ...?'). 'Frau Weber' bleibt."""
    t = (anrede_text or "").strip()
    if t.startswith("Herr ") and not t.startswith("Herrn "):
        return "Herrn " + t[5:]
    return t


# ---------------------------------------------------------------------------
# Anrufzeiten
# ---------------------------------------------------------------------------

def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


@dataclass
class CallWindow:
    tz: str = "Europe/Berlin"
    weekday_start: str = "09:00"
    weekday_end: str = "19:00"
    saturday_start: str = "10:00"
    saturday_end: str = "14:00"

    def __post_init__(self):
        self.zone = ZoneInfo(self.tz)
        self._hol = holidays.Germany(subdiv="NW")

    def _bounds(self, d) -> tuple[time, time] | None:
        if d in self._hol:
            return None
        wd = d.weekday()
        if wd < 5:
            return _hm(self.weekday_start), _hm(self.weekday_end)
        if wd == 5:
            return _hm(self.saturday_start), _hm(self.saturday_end)
        return None

    def is_open(self, when: datetime) -> bool:
        local = when.astimezone(self.zone)
        b = self._bounds(local.date())
        return bool(b) and b[0] <= local.time() < b[1]

    def next_open(self, when: datetime) -> datetime:
        """Frühester erlaubter Zeitpunkt ab `when` (inklusive)."""
        local = when.astimezone(self.zone)
        for i in range(0, 14):
            day = (local + timedelta(days=i)).date()
            b = self._bounds(day)
            if not b:
                continue
            start = datetime.combine(day, b[0], self.zone)
            end = datetime.combine(day, b[1], self.zone)
            if i == 0 and local >= end:
                continue
            return max(start, local) if i == 0 else start
        return local  # sollte nie passieren

    def task_due(self, when: datetime, hours: float) -> datetime:
        """Fälligkeit für eine Makler-Aufgabe: `hours` später, aber in Bürozeiten."""
        due = when + timedelta(hours=hours)
        if self.is_open(due):
            return due
        return self.next_open(due) + timedelta(hours=1)


# ---------------------------------------------------------------------------
# Gesprächsergebnis
# ---------------------------------------------------------------------------

# Niemand erreicht -> erneut versuchen
RETRY_REASONS = {"dial_no_answer", "dial_busy", "voicemail_reached", "user_declined", "ivr_reached"}
# Nummer falsch -> nicht erneut versuchen
INVALID_REASONS = {"invalid_destination"}
# Technischer Fehler auf unserer Seite / beim Anbieter
TECH_REASONS = {
    "dial_failed", "telephony_provider_permission_denied", "telephony_provider_unavailable",
    "sip_routing_error", "marked_as_spam", "network_blocked", "concurrency_limit_reached",
    "no_concurrency_fallback", "no_valid_payment", "credit_exhausted", "budget_reached",
    "error_llm_websocket_open", "error_llm_websocket_lost_connection", "error_llm_websocket_runtime",
    "error_llm_websocket_corrupt_payload", "error_no_audio_received", "error_asr", "error_retell",
    "error_unknown", "error_user_not_joined", "registered_call_timeout", "scam_detected",
}

ERGEBNISSE = (
    "qualifiziert", "interesse_ohne_termin", "rueckruf_gewuenscht", "kein_interesse",
    "nicht_anrufen", "falsche_person", "unklar",
)


@dataclass
class CallResult:
    kind: str  # "retry" | "invalid" | "tech" | "talked"
    ergebnis: str = ""  # nur bei "talked"
    data: dict = field(default_factory=dict)
    summary: str = ""
    duration_s: int = 0
    reason: str = ""


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "ja", "yes", "1")
    return bool(v)


def classify_call(call: dict) -> CallResult:
    reason = call.get("disconnection_reason") or ""
    analysis = call.get("call_analysis") or {}
    data = analysis.get("custom_analysis_data") or {}
    summary = analysis.get("call_summary") or ""
    dur = int((call.get("duration_ms") or 0) / 1000)

    if reason in INVALID_REASONS:
        return CallResult("invalid", reason=reason, duration_s=dur)
    if reason in RETRY_REASONS or analysis.get("in_voicemail"):
        return CallResult("retry", reason=reason or "voicemail", duration_s=dur)
    if reason in TECH_REASONS:
        return CallResult("tech", reason=reason, duration_s=dur)

    # Ab hier kam ein Gespräch zustande.
    if reason == "user_requested_dnc":
        erg = "nicht_anrufen"
    else:
        erg = str(data.get("ergebnis") or "").strip().lower()
        if erg not in ERGEBNISSE:
            erg = "unklar"
    # Ein klares "nicht mehr anrufen" geht immer vor
    if _truthy(data.get("nicht_mehr_anrufen")):
        erg = "nicht_anrufen"
    return CallResult("talked", ergebnis=erg, data=data, summary=summary, duration_s=dur, reason=reason)


# ---------------------------------------------------------------------------
# Was nach Propstack geschrieben wird
# ---------------------------------------------------------------------------

ERGEBNIS_TEXT = {
    "qualifiziert": "Qualifiziert – Besichtigung gewünscht",
    "interesse_ohne_termin": "Interesse, noch kein Termin",
    "rueckruf_gewuenscht": "Rückruf gewünscht",
    "kein_interesse": "Kein Interesse mehr",
    "nicht_anrufen": "Möchte nicht mehr angerufen werden",
    "falsche_person": "Falsche Person am Telefon",
    "unklar": "Gespräch ohne klares Ergebnis",
    "nicht_erreicht": "Nicht erreicht",
    "nummer_ungueltig": "Telefonnummer ungültig",
    "technischer_fehler": "Anruf technisch fehlgeschlagen",
}

# Ergebnis -> (Aufgabe anlegen?, Fälligkeit in Std., Merkmal-Schlüssel)
ERGEBNIS_REGELN = {
    "qualifiziert": (True, 2, "group_qualifiziert"),
    "interesse_ohne_termin": (True, 4, None),
    "rueckruf_gewuenscht": (True, 4, None),
    "kein_interesse": (False, 0, "group_kein_interesse"),
    "nicht_anrufen": (False, 0, "group_nicht_anrufen"),
    "falsche_person": (True, 4, None),
    "unklar": (True, 4, None),
    "nicht_erreicht": (False, 0, None),
    "nummer_ungueltig": (True, 4, None),
    "technischer_fehler": (True, 4, None),
}

FELD_LABELS = [
    ("interesse_aktuell", "Interesse noch aktuell"),
    ("nutzung", "Nutzung"),
    ("finanzierung", "Finanzierung"),
    ("finanzierungspartner_ok", "Kontakt Finanzierungspartner gewünscht"),
    ("eigene_immobilie_verkaufen", "Muss eigene Immobilie verkaufen"),
    ("verkaeufer_ansprache_ok", "Ansprache zum Verkauf der eigenen Immobilie ok"),
    ("kaufzeitraum", "Kaufzeitraum"),
    ("besichtigung_wunschzeiten", "Wunschzeiten Besichtigung"),
    ("rueckruf_wunschzeit", "Wunschzeit Rückruf"),
    ("sonstiges", "Sonstiges"),
]

WERT_TEXT = {
    "selbstnutzung": "Selbstnutzung", "kapitalanlage": "Kapitalanlage",
    "geklaert": "geklärt", "offen": "offen", "unklar": "unklar",
    "ja": "ja", "nein": "nein", True: "ja", False: "nein",
}


@dataclass
class Task:
    title: str
    due: datetime


@dataclass
class WritePlan:
    ergebnis: str
    note_title: str
    note_body: str
    tasks: list[Task] = field(default_factory=list)
    add_group_keys: list[str] = field(default_factory=list)
    block_number: bool = False


def _fmt(v) -> str:
    if isinstance(v, str):
        return html.escape(WERT_TEXT.get(v.strip().lower(), v.strip()))
    return html.escape(str(WERT_TEXT.get(v, v)))


def build_plan(
    ergebnis: str,
    *,
    now: datetime,
    window: CallWindow,
    data: dict | None = None,
    summary: str = "",
    objekt: str = "",
    attempts: int = 1,
    extra: str = "",
) -> WritePlan:
    data = data or {}
    titel = ERGEBNIS_TEXT[ergebnis]
    task_needed, hours, group_key = ERGEBNIS_REGELN[ergebnis]

    zeilen = [f"<b>KI-Rückruf: {html.escape(titel)}</b>"]
    if objekt:
        zeilen.append(f"Objekt: {html.escape(objekt)}")
    zeilen.append(f"Anrufversuche: {attempts}")
    if extra:
        zeilen.append(html.escape(extra))
    angaben = [(label, data.get(key)) for key, label in FELD_LABELS if data.get(key) not in (None, "", [])]
    if angaben:
        zeilen.append("<br><b>Angaben im Gespräch</b>")
        zeilen += [f"{html.escape(label)}: {_fmt(v)}" for label, v in angaben]
    if summary:
        zeilen.append("<br><b>Zusammenfassung</b>")
        zeilen.append(html.escape(summary))
    if ergebnis == "qualifiziert":
        zeilen.append("<br>Hinweis: Besichtigungswunsch ist noch NICHT bestätigt.")
    plan = WritePlan(ergebnis=ergebnis, note_title=f"KI-Rückruf: {titel}", note_body="<br>".join(zeilen))

    if task_needed:
        plan.tasks.append(Task(f"Rückruf Interessent – {titel}", window.task_due(now, hours)))
    if group_key:
        plan.add_group_keys.append(group_key)
    if ergebnis == "nicht_anrufen":
        plan.block_number = True

    # Zusätzlich, unabhängig vom Ergebnis (nur wenn ein Gespräch stattfand)
    if ergebnis not in ("nicht_erreicht", "nummer_ungueltig", "technischer_fehler", "nicht_anrufen"):
        if _truthy(data.get("finanzierungspartner_ok")):
            plan.add_group_keys.append("group_finanzierung_ok")
            plan.tasks.append(Task("Kontakt zum Finanzierungspartner herstellen", window.task_due(now, 24)))
        if _truthy(data.get("verkaeufer_ansprache_ok")):
            plan.add_group_keys.append("group_verkaeufer_lead")
            plan.tasks.append(Task("Verkäufer-Lead: Interessent zur eigenen Immobilie ansprechen", window.task_due(now, 24)))
    return plan


def next_attempt_at(now: datetime, attempts_done: int, delays_min: list[int], window: CallWindow) -> datetime:
    idx = min(attempts_done - 1, len(delays_min) - 1)
    return window.next_open(now + timedelta(minutes=delays_min[idx]))
