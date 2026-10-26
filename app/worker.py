"""Der eigentliche Ablauf: Anfragen holen, anrufen, Ergebnis nach Propstack schreiben."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from .clients import PropstackClient, RetellClient
from .config import Settings
from .db import DB, iso, now_iso, parse_iso
from .logic import (
    CallWindow, anrede, anrede_dativ, build_plan, classify_call, next_attempt_at, pick_phone,
)

log = logging.getLogger("rueckruf")

STUCK_AFTER_MIN = 20       # danach wird bei Retell nachgefragt
GIVE_UP_AFTER_MIN = 90     # danach gilt der Anruf als technisch gescheitert


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _title(unit: dict) -> str:
    t = unit.get("title")
    if isinstance(t, dict):
        t = t.get("value")
    return (t or unit.get("short_address") or unit.get("name") or "").strip()


def _group_ids(contact: dict) -> set[int]:
    ids = set(contact.get("group_ids") or [])
    for g in contact.get("groups") or []:
        if isinstance(g, dict) and g.get("id"):
            ids.add(g["id"])
    return ids


class Bot:
    def __init__(self, s: Settings, db: DB, propstack: PropstackClient, retell: RetellClient | None, clock=utcnow):
        self.s = s
        self.db = db
        self.ps = propstack
        self.retell = retell
        self.clock = clock
        self.window = CallWindow(s.timezone, s.weekday_start, s.weekday_end, s.saturday_start, s.saturday_end)

    # ------------------------------------------------------------------
    # 1) Neue Anfragen abholen
    # ------------------------------------------------------------------
    def poll_inquiries(self) -> int:
        now = self.clock()
        last = parse_iso(self.db.get_state("last_poll"))
        if last is None:
            # Erster Start: alte Anfragen nicht nachträglich abtelefonieren
            self.db.set_state("last_poll", iso(now))
            log.info("Erster Start – Anfragen ab jetzt werden bearbeitet.")
            return 0
        since = last - timedelta(minutes=10)  # Überlappung, Duplikate fängt die DB ab
        items = self.ps.inquiries_since(since)
        n = 0
        for it in items:
            if self._handle_inquiry(it, now):
                n += 1
        self.db.set_state("last_poll", iso(now))
        return n

    def _handle_inquiry(self, it: dict, now: datetime) -> bool:
        aid = it.get("id")
        if not aid or self.db.job_exists(aid):
            return False
        created = parse_iso(it.get("created_at")) or now
        if created < now - timedelta(hours=self.s.max_inquiry_age_hours):
            return False
        clients = it.get("client_ids") or []
        props = it.get("property_ids") or []
        if not clients:
            return False
        self.enqueue(activity_id=aid, client_id=clients[0], property_id=props[0] if props else None,
                     broker_id=it.get("broker_id"), created=created)
        return True

    def enqueue(self, *, activity_id, client_id, property_id, broker_id, created: datetime, force: bool = False) -> int:
        """Prüft, ob angerufen werden darf, und legt den Auftrag an."""
        def skip(reason: str, phone=None) -> int:
            log.info("Anfrage %s übersprungen: %s", activity_id, reason)
            return self.db.add_job(activity_id=activity_id, client_id=client_id, property_id=property_id,
                                   broker_id=broker_id, phone=phone, status="skipped", note=reason)

        if not force and self.db.talked_before(client_id, property_id):
            return skip("bereits angerufen bzw. Auftrag läuft")
        contact = self.ps.contact(client_id)
        if contact.get("archived"):
            return skip("Kontakt archiviert")
        if contact.get("gdpr_status") == 3:
            return skip("DSGVO-Einwilligung widerrufen")
        if self.s.group_nicht_anrufen in _group_ids(contact):
            return skip("Merkmal 'KI: nicht anrufen'")
        phone = pick_phone(contact)
        if not phone:
            self._note_only(client_id, property_id, "KI-Rückruf: keine gültige Telefonnummer",
                            "Der KI-Assistent konnte nicht anrufen, weil keine gültige Telefonnummer hinterlegt ist.")
            return skip("keine gültige Telefonnummer")
        if self.db.is_blocked(phone):
            return skip("Nummer auf interner Sperrliste", phone)
        if self.s.mode == "test" and phone not in self.s.test_numbers:
            return skip("Testmodus: Nummer nicht in TEST_NUMBERS", phone)

        if force:
            when = self.clock()
        else:
            # Wartezeit zählt ab Eingang der Anfrage
            when = self.window.next_open(max(created + timedelta(minutes=self.s.inquiry_delay_min), self.clock()))
        return self.db.add_job(activity_id=activity_id, client_id=client_id, property_id=property_id,
                               broker_id=broker_id, phone=phone, status="pending", next_attempt_at=iso(when))

    # ------------------------------------------------------------------
    # 2) Fällige Anrufe starten
    # ------------------------------------------------------------------
    def tick(self, *, ignore_window: bool = False) -> int:
        self.recover_stuck()
        now = self.clock()
        if not ignore_window and not self.window.is_open(now):
            return 0
        free = self.s.max_parallel_calls - len(self.db.calling_jobs())
        if free <= 0:
            return 0
        started = 0
        for job in self.db.due_jobs(iso(now), free):
            if self.start_call(job):
                started += 1
        return started

    def start_call(self, job) -> bool:
        contact = self.ps.contact(job["client_id"])
        if self.s.group_nicht_anrufen in _group_ids(contact) or self.db.is_blocked(job["phone"]):
            self.db.update_job(job["id"], status="skipped", note="vor dem Anruf gesperrt")
            return False
        unit = self.ps.unit(job["property_id"]) if job["property_id"] else {}
        broker_id = unit.get("broker_id") or job["broker_id"] or contact.get("broker_id") or self.s.default_broker_id
        broker = self.ps.broker(broker_id) or {}
        kunde = anrede(contact.get("salutation"), contact.get("first_name"), contact.get("last_name"),
                       contact.get("name") or "")
        variables = {
            "kunde_anrede": kunde,
            "kunde_anrede_dativ": anrede_dativ(kunde),
            "kunde_name": contact.get("name") or "",
            "objekt_titel": _title(unit) or "Ihre Immobilienanfrage",
            "objekt_ort": unit.get("city") or "",
            "makler_name": anrede(broker.get("salutation"), broker.get("first_name"), broker.get("last_name"),
                                  broker.get("name") or "unser Team"),
            "firma": self.s.firma_gesprochen,
        }
        attempt = job["attempts"] + 1
        if self.s.dry or not self.retell:
            log.info("[dry] Würde %s anrufen (Versuch %s): %s", job["phone"], attempt, variables)
            self.db.update_job(job["id"], status="skipped", attempts=attempt, note="Trockenlauf – nicht angerufen")
            return False
        try:
            res = self.retell.create_call(
                from_number=self.s.retell_from_number, to_number=job["phone"], agent_id=self.s.retell_agent_id,
                variables=variables,
                metadata={"job_id": job["id"], "client_id": job["client_id"], "property_id": job["property_id"],
                          "attempt": attempt},
                idempotency_key=f"job{job['id']}-try{attempt}",
            )
        except Exception as e:  # noqa: BLE001
            log.exception("Anruf konnte nicht gestartet werden (Auftrag %s)", job["id"])
            self.db.update_job(job["id"], attempts=attempt, note=f"Start fehlgeschlagen: {e}"[:300])
            self._after_failed_attempt(self.db.get_job(job["id"]), final_ergebnis="technischer_fehler")
            return False
        self.db.update_job(job["id"], status="calling", attempts=attempt, last_call_id=res.get("call_id"),
                           call_started_at=now_iso(), broker_id=broker_id or None)
        log.info("Anruf gestartet: Auftrag %s, Versuch %s, Call %s", job["id"], attempt, res.get("call_id"))
        return True

    # ------------------------------------------------------------------
    # 3) Ergebnis verarbeiten (aus dem Webhook oder per Nachfrage)
    # ------------------------------------------------------------------
    def handle_call(self, call: dict) -> str:
        call_id = call.get("call_id") or ""
        if not self.db.mark_processed(f"analyzed:{call_id}"):
            return "doppelt"
        meta = call.get("metadata") or {}
        if meta.get("demo"):
            log.info("Demo-Anruf %s beendet: %s", call_id, (call.get("call_analysis") or {}).get("custom_analysis_data"))
            return "demo"
        job = self.db.get_job(int(meta["job_id"])) if meta.get("job_id") else None
        job = job or self.db.job_by_call(call_id)
        if not job:
            log.warning("Unbekannter Anruf %s", call_id)
            return "unbekannt"
        if job["status"] != "calling" or job["last_call_id"] != call_id:
            return "veraltet"

        res = classify_call(call)
        log.info("Auftrag %s: Call %s -> %s %s", job["id"], call_id, res.kind, res.ergebnis or res.reason)
        if res.kind == "retry":
            return self._after_failed_attempt(job, final_ergebnis="nicht_erreicht")
        if res.kind == "tech":
            return self._after_failed_attempt(job, final_ergebnis="technischer_fehler",
                                              extra=f"Technischer Grund: {res.reason}")
        if res.kind == "invalid":
            return self._finish(job, "nummer_ungueltig", extra="Die hinterlegte Nummer ist nicht erreichbar/ungültig.")
        return self._finish(job, res.ergebnis, data=res.data, summary=res.summary)

    def _after_failed_attempt(self, job, *, final_ergebnis: str, extra: str = "") -> str:
        if job["attempts"] < self.s.max_attempts:
            nxt = next_attempt_at(self.clock(), job["attempts"], self.s.retry_delays_min, self.window)
            self.db.update_job(job["id"], status="pending", next_attempt_at=iso(nxt))
            return "erneut"
        return self._finish(job, final_ergebnis, extra=extra)

    def _finish(self, job, ergebnis: str, *, data: dict | None = None, summary: str = "", extra: str = "") -> str:
        objekt = ""
        try:
            if job["property_id"]:
                objekt = _title(self.ps.unit(job["property_id"]))
        except Exception:  # noqa: BLE001
            pass
        plan = build_plan(ergebnis, now=self.clock(), window=self.window, data=data, summary=summary,
                          objekt=objekt, attempts=job["attempts"], extra=extra)
        cid, pid = job["client_id"], job["property_id"]
        self.ps.create_note(title=plan.note_title, body=plan.note_body, client_id=cid, property_id=pid,
                            note_type_id=self.s.note_type_id)
        broker_id = job["broker_id"] or self.s.default_broker_id or None
        for t in plan.tasks:
            self.ps.create_task(title=t.title, body=plan.note_body, due=t.due, client_id=cid, property_id=pid,
                                broker_id=broker_id, note_type_id=self.s.task_type_id)
        groups = [getattr(self.s, k) for k in plan.add_group_keys]
        self.ps.add_groups(cid, groups)
        if plan.block_number and job["phone"]:
            self.db.block(job["phone"], cid)
        self.db.update_job(job["id"], status="done", outcome=ergebnis)
        return ergebnis

    def _note_only(self, client_id, property_id, title, text):
        try:
            self.ps.create_note(title=title, body=text, client_id=client_id, property_id=property_id,
                                note_type_id=self.s.note_type_id)
        except Exception:  # noqa: BLE001
            log.exception("Notiz konnte nicht geschrieben werden")

    # ------------------------------------------------------------------
    # 4) Hängende Anrufe aufräumen (falls ein Webhook verloren ging)
    # ------------------------------------------------------------------
    def recover_stuck(self) -> None:
        if not self.retell:
            return
        now = self.clock()
        for job in self.db.calling_jobs():
            started = parse_iso(job["call_started_at"]) or now
            age = now - started
            if age < timedelta(minutes=STUCK_AFTER_MIN):
                continue
            try:
                call = self.retell.get_call(job["last_call_id"])
            except Exception:  # noqa: BLE001
                call = None
            if call and call.get("call_status") in ("ended", "error", "not_connected") and (
                    call.get("call_analysis") is not None or age > timedelta(minutes=GIVE_UP_AFTER_MIN)):
                self.handle_call(call)
            elif age > timedelta(minutes=GIVE_UP_AFTER_MIN):
                self._after_failed_attempt(job, final_ergebnis="technischer_fehler",
                                           extra="Kein Ergebnis von Retell erhalten.")
