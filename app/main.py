"""Webserver des KI-Rückruf-Bots (FastAPI).

Endpunkte:
  GET  /health                 – läuft der Server? welche Einstellungen fehlen?
  POST /webhook/retell         – Retell meldet hier Gesprächsergebnisse
  GET  /admin/status           – letzte Aufträge (Header X-Admin-Token)
  GET  /admin/propstack-check  – Verbindungstest zu Propstack, listet Nutzer und Aktivitätstypen
  POST /admin/testanruf        – sofort einen bestimmten Kontakt anrufen (Testmodus: nur TEST_NUMBERS)
  POST /admin/retell-sync      – Leitfaden und Auswertungsfelder in den Retell-Agenten übertragen
                                 (passiert auch automatisch beim Start)
  GET  /admin                  – Testseite: Demo-Anruf an eine Nummer aus TEST_NUMBERS
"""
from __future__ import annotations

import asyncio
import hmac
import html
import json
import time
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import BackgroundTasks, FastAPI, Form, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from . import retell_agent
from .clients import PropstackClient, RetellClient, verify_retell_signature
from .config import settings
from .db import DB
from .worker import Bot

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("rueckruf")


def build_bot() -> Bot:
    db = DB(settings.db_path)
    ps = PropstackClient(settings.propstack_api_key, settings.propstack_base_url, dry=settings.dry)
    rt = RetellClient(settings.retell_api_key, settings.retell_base_url) if settings.retell_api_key else None
    return Bot(settings, db, ps, rt)


bot: Bot | None = None
selfcheck: dict = {"status": "läuft noch"}
retell_status: dict = {"status": "noch nicht abgeglichen"}


def run_selfcheck() -> None:
    """Prüft beim Start, ob Propstack mit dem Schlüssel erreichbar ist (ohne Kundendaten auszugeben)."""
    global selfcheck
    res: dict = {}

    def probe(name, fn):
        try:
            res[name] = fn()
        except Exception as e:  # noqa: BLE001
            code = getattr(getattr(e, "response", None), "status_code", None)
            res[name] = f"Fehler {code}" if code else f"Fehler: {type(e).__name__}"

    probe("nutzer", lambda: len(bot.ps.brokers()))
    probe("aktivitaetstypen", lambda: len(bot.ps.activity_types()))
    probe("anfragen_7_tage", lambda: len(bot.ps.inquiries_since(bot.clock() - timedelta(days=7), max_pages=3)))
    res["ok"] = all(isinstance(v, int) for v in res.values())
    selfcheck = res
    log.info("Propstack-Selbsttest: %s", res)


async def _loop(name: str, fn, every: int):
    while True:
        try:
            await asyncio.to_thread(fn)
        except Exception:  # noqa: BLE001
            log.exception("Fehler in %s", name)
        await asyncio.sleep(every)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global bot
    bot = build_bot()
    problems = settings.problems()
    tasks = []
    if settings.propstack_api_key:
        tasks.append(asyncio.create_task(asyncio.to_thread(run_selfcheck)))
    if settings.retell_api_key and settings.retell_agent_id and settings.public_base_url:
        tasks.append(asyncio.create_task(asyncio.to_thread(run_retell_sync)))
    if problems:
        log.warning("Bot läuft NICHT automatisch – es fehlt: %s", "; ".join(problems))
    else:
        log.info("Bot startet im Modus '%s'", settings.mode)
        tasks.append(asyncio.create_task(_loop("Anfragen", bot.poll_inquiries, settings.poll_interval_sec)))
        tasks.append(asyncio.create_task(_loop("Anrufe", bot.tick, settings.tick_interval_sec)))
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Immobye KI-Rückruf", lifespan=lifespan)


def _admin(token: str | None):
    if not settings.admin_token or token != settings.admin_token:
        raise HTTPException(status_code=401, detail="Admin-Token fehlt oder falsch")


@app.get("/health")
def health():
    return {"ok": True, "mode": settings.mode, "fehlt": settings.problems(), "propstack": selfcheck, "retell": retell_status}


@app.post("/webhook/retell")
async def retell_webhook(request: Request, background: BackgroundTasks):
    raw = (await request.body()).decode("utf-8")
    if not verify_retell_signature(raw, settings.retell_api_key, request.headers.get("x-retell-signature")):
        raise HTTPException(status_code=401, detail="Signatur ungültig")
    payload = json.loads(raw)
    if payload.get("event") == "call_analyzed" and bot:
        background.add_task(bot.handle_call, payload.get("call") or {})
    return Response(status_code=204)


@app.get("/admin/status")
def admin_status(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    return {"mode": settings.mode, "jobs": [dict(r) for r in bot.db.recent_jobs(50)]}


@app.get("/admin/propstack-check")
def propstack_check(x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    out: dict = {}
    try:
        out["nutzer"] = [{"id": b["id"], "name": b.get("name")} for b in bot.ps.brokers()]
    except Exception as e:  # noqa: BLE001
        out["nutzer_fehler"] = str(e)
    try:
        out["aktivitaetstypen"] = [{"id": t["id"], "name": t.get("name"), "kategorie": t.get("category")}
                                   for t in bot.ps.activity_types()]
    except Exception as e:  # noqa: BLE001
        out["aktivitaetstypen_fehler"] = str(e)
    try:
        since = bot.clock() - timedelta(days=7)
        out["anfragen_letzte_7_tage"] = len(bot.ps.inquiries_since(since, max_pages=3))
    except Exception as e:  # noqa: BLE001
        out["anfragen_fehler"] = str(e)
    return out


class TestCall(BaseModel):
    client_id: int
    property_id: int | None = None


@app.post("/admin/testanruf")
def testanruf(body: TestCall, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    job_id = bot.enqueue(activity_id=None, client_id=body.client_id, property_id=body.property_id,
                         broker_id=None, created=bot.clock(), force=True)
    job = bot.db.get_job(job_id)
    if job["status"] == "skipped":
        return {"gestartet": False, "grund": job["note"]}
    started = bot.start_call(job)
    return {"gestartet": started, "auftrag": dict(bot.db.get_job(job_id))}


def do_retell_sync(publish: bool = True) -> dict:
    """Überträgt Leitfaden, Auswertungsfelder und Webhook in den Retell-Agenten."""
    if not bot.retell or not settings.retell_agent_id or not settings.public_base_url:
        return {"ok": False, "grund": "RETELL_API_KEY, RETELL_AGENT_ID oder PUBLIC_BASE_URL fehlt"}
    agent = bot.retell.get_agent(settings.retell_agent_id)
    engine = agent.get("response_engine") or {}
    if engine.get("type") != "retell-llm":
        return {"ok": False, "grund": f"Agent nutzt '{engine.get('type')}', erwartet Single-Prompt (retell-llm)"}
    llm = bot.retell.update_llm(engine["llm_id"], retell_agent.llm_update_body())
    body = retell_agent.agent_update_body(f"{settings.public_base_url}/webhook/retell")
    if llm.get("version") is not None:
        body["response_engine"] = {"type": "retell-llm", "llm_id": engine["llm_id"], "version": llm["version"]}
    updated = bot.retell.update_agent(settings.retell_agent_id, body)
    result = {"ok": True, "agent_version": updated.get("version")}
    if publish and updated.get("version") is not None:
        bot.retell.publish_agent(settings.retell_agent_id, int(updated["version"]))
        result["veroeffentlicht"] = True
    return result


def run_retell_sync() -> None:
    global retell_status
    try:
        retell_status = do_retell_sync(publish=True)
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", None)
        detail = ""
        try:
            detail = e.response.text[:200]  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        retell_status = {"ok": False, "grund": f"Fehler {code or type(e).__name__} {detail}".strip()}
    log.info("Retell-Abgleich: %s", retell_status)


@app.post("/admin/retell-sync")
def retell_sync(publish: bool = True, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    run_retell_sync()
    return retell_status


# ---------------------------------------------------------------------------
# Testseite: Demo-Anruf an eine freigegebene Testnummer (ohne Propstack)
# ---------------------------------------------------------------------------
ADMIN_PAGE = """<!doctype html><html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>KI-Rückruf Test</title>
<style>body{font-family:system-ui,sans-serif;max-width:520px;margin:40px auto;padding:0 16px;color:#1b2a41}
h1{font-size:22px}label{display:block;margin:14px 0 4px;font-weight:600}
input,select{width:100%;padding:10px;border:1px solid #c7d2e3;border-radius:8px;font-size:16px;box-sizing:border-box}
button{margin-top:20px;width:100%;padding:12px;border:0;border-radius:8px;background:#1f4fd8;color:#fff;font-size:16px;cursor:pointer}
.hint{color:#5b6b82;font-size:14px}.msg{margin-top:18px;padding:12px;border-radius:8px;background:#eef3ff}</style></head>
<body><h1>Immobye KI-Rückruf – Testanruf</h1>
<p class="hint">Ruft eine freigegebene Testnummer an, damit die Stimme und der Leitfaden geprüft werden können.
In Propstack wird dabei nichts geschrieben.</p>
<form method="post" action="/admin/demo-anruf">
<label>Admin-Token</label><input name="token" type="password" required autocomplete="off">
<label>Testnummer</label><select name="nummer">{optionen}</select>
<label>Anrede im Gespräch</label><input name="kunde" value="Herr Kunzmann">
<label>Objekt</label><input name="objekt" value="Helle 3-Zimmer-Wohnung in Wersten">
<label>Makler</label><input name="makler" value="Herr Gottschalk">
<button type="submit">Jetzt anrufen</button></form>{meldung}</body></html>"""


def _admin_page(meldung: str = "") -> HTMLResponse:
    opts = "".join(f"<option>{html.escape(n)}</option>" for n in settings.test_numbers) or "<option disabled>keine TEST_NUMBERS eingetragen</option>"
    msg = f'<div class="msg">{html.escape(meldung)}</div>' if meldung else ""
    return HTMLResponse(ADMIN_PAGE.replace("{optionen}", opts).replace("{meldung}", msg))


@app.get("/admin", response_class=HTMLResponse)
def admin_page():
    return _admin_page()


@app.post("/admin/demo-anruf", response_class=HTMLResponse)
def demo_anruf(token: str = Form(...), nummer: str = Form(...), kunde: str = Form(""), objekt: str = Form(""),
               makler: str = Form("")):
    if not settings.admin_token or not hmac.compare_digest(token, settings.admin_token):
        return _admin_page("Admin-Token ist falsch.")
    if nummer not in settings.test_numbers:
        return _admin_page("Diese Nummer ist nicht in TEST_NUMBERS freigegeben.")
    if not bot.retell or not settings.retell_agent_id or not settings.retell_from_number:
        return _admin_page("Retell ist noch nicht vollständig eingerichtet.")
    try:
        res = bot.retell.create_call(
            from_number=settings.retell_from_number, to_number=nummer, agent_id=settings.retell_agent_id,
            variables={"kunde_anrede": kunde or "Herr Kunzmann", "kunde_name": kunde,
                       "objekt_titel": objekt or "Ihre Immobilienanfrage", "objekt_ort": "",
                       "makler_name": makler or "unser Team", "firma": settings.firma},
            metadata={"demo": True}, idempotency_key=f"demo-{int(time.time())}",
        )
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", None), "status_code", None)
        detail = ""
        try:
            detail = e.response.text[:300]  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
        return _admin_page(f"Anruf fehlgeschlagen (Fehler {code}): {detail}")
    return _admin_page(f"Anruf gestartet – Ihr Telefon sollte gleich klingeln. (Call-ID {res.get('call_id')})")
