"""Webserver des KI-Rückruf-Bots (FastAPI).

Endpunkte:
  GET  /health                 – läuft der Server? welche Einstellungen fehlen?
  POST /webhook/retell         – Retell meldet hier Gesprächsergebnisse
  GET  /admin/status           – letzte Aufträge (Header X-Admin-Token)
  GET  /admin/propstack-check  – Verbindungstest zu Propstack, listet Nutzer und Aktivitätstypen
  POST /admin/testanruf        – sofort einen bestimmten Kontakt anrufen (Testmodus: nur TEST_NUMBERS)
  POST /admin/retell-sync      – Leitfaden und Auswertungsfelder in den Retell-Agenten übertragen
"""
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
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
    return {"ok": True, "mode": settings.mode, "fehlt": settings.problems(), "propstack": selfcheck}


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


@app.post("/admin/retell-sync")
def retell_sync(publish: bool = False, x_admin_token: str | None = Header(default=None)):
    _admin(x_admin_token)
    if not bot.retell or not settings.retell_agent_id or not settings.public_base_url:
        raise HTTPException(400, "RETELL_API_KEY, RETELL_AGENT_ID und PUBLIC_BASE_URL werden benötigt")
    agent = bot.retell.get_agent(settings.retell_agent_id)
    engine = agent.get("response_engine") or {}
    if engine.get("type") != "retell-llm":
        raise HTTPException(400, f"Agent nutzt '{engine.get('type')}', erwartet wird ein Single-Prompt-Agent (retell-llm)")
    bot.retell.update_llm(engine["llm_id"], retell_agent.llm_update_body())
    updated = bot.retell.update_agent(settings.retell_agent_id,
                                      retell_agent.agent_update_body(f"{settings.public_base_url}/webhook/retell"))
    result = {"aktualisiert": True, "version": updated.get("version")}
    if publish and updated.get("version") is not None:
        bot.retell.publish_agent(settings.retell_agent_id, int(updated["version"]))
        result["veroeffentlicht"] = True
    return result
