"""Einstellungen des KI-Rückruf-Bots.

Alle Werte kommen aus Umgebungsvariablen (bei Render unter "Environment").
Geheimnisse (API-Schlüssel) stehen nie im Code.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def _list(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [x.strip() for x in raw.split(",") if x.strip()]


@dataclass
class Settings:
    # Betriebsart: "dry" (nichts anrufen, nichts schreiben), "test" (nur Testnummern), "live"
    mode: str = field(default_factory=lambda: os.getenv("MODE", "dry").lower())

    # Propstack
    propstack_api_key: str = field(default_factory=lambda: os.getenv("PROPSTACK_API_KEY", ""))
    propstack_base_url: str = field(default_factory=lambda: os.getenv("PROPSTACK_BASE_URL", "https://api.propstack.de/v1"))
    group_qualifiziert: int = field(default_factory=lambda: _int("PROPSTACK_GROUP_QUALIFIZIERT", 725637))
    group_kein_interesse: int = field(default_factory=lambda: _int("PROPSTACK_GROUP_KEIN_INTERESSE", 725638))
    group_nicht_anrufen: int = field(default_factory=lambda: _int("PROPSTACK_GROUP_NICHT_ANRUFEN", 725639))
    group_finanzierung_ok: int = field(default_factory=lambda: _int("PROPSTACK_GROUP_FINANZIERUNG_OK", 725663))
    group_verkaeufer_lead: int = field(default_factory=lambda: _int("PROPSTACK_GROUP_VERKAEUFER_LEAD", 725664))
    # Aktivitätstypen für Notiz und Aufgabe (0 = ohne Typ, nur Titel)
    note_type_id: int = field(default_factory=lambda: _int("PROPSTACK_NOTE_TYPE_ID", 0))
    task_type_id: int = field(default_factory=lambda: _int("PROPSTACK_TASK_TYPE_ID", 0))
    # Nutzer, dem Aufgaben zugewiesen werden, wenn das Objekt keinen Betreuer hat
    default_broker_id: int = field(default_factory=lambda: _int("PROPSTACK_DEFAULT_BROKER_ID", 0))

    # Retell
    retell_api_key: str = field(default_factory=lambda: os.getenv("RETELL_API_KEY", ""))
    retell_base_url: str = field(default_factory=lambda: os.getenv("RETELL_BASE_URL", "https://api.retellai.com"))
    retell_agent_id: str = field(default_factory=lambda: os.getenv("RETELL_AGENT_ID", ""))
    retell_from_number: str = field(default_factory=lambda: os.getenv("RETELL_FROM_NUMBER", ""))
    # Optional: Stimme des Agenten (Voice-ID aus Retell); leer = Stimme bleibt wie im Dashboard
    retell_voice_id: str = field(default_factory=lambda: os.getenv("RETELL_VOICE_ID", "custom_voice_66963573082e565cb8fb9ccbf6"))  # Elke Collins (ElevenLabs); Pauli: custom_voice_93438b88ef5402fc6eedb54d0d

    # Öffentliche Adresse des Servers (für den Retell-Webhook)
    public_base_url: str = field(default_factory=lambda: os.getenv("PUBLIC_BASE_URL", "").rstrip("/"))
    # Schutz für die Admin-Endpunkte
    admin_token: str = field(default_factory=lambda: os.getenv("ADMIN_TOKEN", ""))

    # Testmodus: nur diese Nummern werden angerufen (E.164, kommagetrennt)
    test_numbers: list[str] = field(default_factory=lambda: _list("TEST_NUMBERS"))

    # Ablauf
    timezone: str = field(default_factory=lambda: os.getenv("TIMEZONE", "Europe/Berlin"))
    poll_interval_sec: int = field(default_factory=lambda: _int("POLL_INTERVAL_SEC", 180))
    tick_interval_sec: int = field(default_factory=lambda: _int("TICK_INTERVAL_SEC", 30))
    inquiry_delay_min: int = field(default_factory=lambda: _int("INQUIRY_DELAY_MIN", 5))
    max_inquiry_age_hours: int = field(default_factory=lambda: _int("MAX_INQUIRY_AGE_HOURS", 48))
    max_attempts: int = field(default_factory=lambda: _int("MAX_ATTEMPTS", 3))
    max_parallel_calls: int = field(default_factory=lambda: _int("MAX_PARALLEL_CALLS", 1))
    # Wartezeit vor Versuch 2 und 3 (Minuten)
    retry_delays_min: list[int] = field(default_factory=lambda: [int(x) for x in (_list("RETRY_DELAYS_MIN") or ["120", "1440"])])
    # Anrufzeiten: Mo-Fr und Sa (HH:MM), Sonntag und NRW-Feiertage nie
    weekday_start: str = field(default_factory=lambda: os.getenv("CALL_WEEKDAY_START", "09:00"))
    weekday_end: str = field(default_factory=lambda: os.getenv("CALL_WEEKDAY_END", "19:00"))
    saturday_start: str = field(default_factory=lambda: os.getenv("CALL_SATURDAY_START", "10:00"))
    saturday_end: str = field(default_factory=lambda: os.getenv("CALL_SATURDAY_END", "14:00"))

    # Speicherort der Datenbank (bei Render auf der Festplatte /var/data)
    db_path: str = field(default_factory=lambda: os.getenv("DB_PATH", "data/rueckruf.sqlite3"))

    firma: str = field(default_factory=lambda: os.getenv("FIRMA_NAME", "Immobye"))
    # So spricht die Stimme den Firmennamen aus ("Immo" + englisch "bye")
    firma_gesprochen: str = field(default_factory=lambda: os.getenv("FIRMA_GESPROCHEN", "Immobai"))

    @property
    def dry(self) -> bool:
        return self.mode == "dry"

    def problems(self) -> list[str]:
        """Fehlende Pflichtangaben für den aktuellen Modus."""
        out = []
        if self.mode not in ("dry", "test", "live"):
            out.append("MODE muss dry, test oder live sein")
        if not self.propstack_api_key:
            out.append("PROPSTACK_API_KEY fehlt")
        if self.mode in ("test", "live"):
            for name in ("retell_api_key", "retell_agent_id", "retell_from_number", "admin_token"):
                if not getattr(self, name):
                    out.append(f"{name.upper()} fehlt")
        if self.mode == "test" and not self.test_numbers:
            out.append("TEST_NUMBERS fehlt (im Testmodus Pflicht)")
        return out


settings = Settings()
