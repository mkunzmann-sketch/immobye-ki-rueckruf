import hashlib
import hmac
import json
import time

from fastapi.testclient import TestClient

from app import main
from app.config import settings


def _client(tmp_path):
    settings.db_path = str(tmp_path / "t.sqlite3")
    settings.retell_api_key = "geheim"
    settings.admin_token = "admin"
    settings.propstack_api_key = ""  # -> Hintergrundschleifen starten nicht
    return TestClient(main.app)


def _sig(body: str, key: str) -> str:
    ts = str(int(time.time() * 1000))
    return f"v={ts},d=" + hmac.new(key.encode(), (body + ts).encode(), hashlib.sha256).hexdigest()


def test_health_lists_missing_settings(tmp_path):
    with _client(tmp_path) as c:
        r = c.get("/health").json()
        assert r["ok"] and "PROPSTACK_API_KEY fehlt" in r["fehlt"]


def test_webhook_rejects_bad_signature(tmp_path):
    with _client(tmp_path) as c:
        r = c.post("/webhook/retell", content='{"event":"call_analyzed"}', headers={"x-retell-signature": "v=1,d=x"})
        assert r.status_code == 401


def test_webhook_accepts_valid_signature(tmp_path):
    body = json.dumps({"event": "call_analyzed", "call": {"call_id": "unbekannt", "metadata": {}}})
    with _client(tmp_path) as c:
        r = c.post("/webhook/retell", content=body, headers={"x-retell-signature": _sig(body, "geheim")})
        assert r.status_code == 204


def test_admin_requires_token(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/admin/status").status_code == 401
        assert c.get("/admin/status", headers={"x-admin-token": "admin"}).status_code == 200


def test_admin_page_and_demo_call_guards(tmp_path):
    settings.test_numbers = ["+4915100000000"]
    with _client(tmp_path) as c:
        assert "Testanruf" in c.get("/admin").text
        r = c.post("/admin/demo-anruf", data={"token": "falsch", "nummer": "+4915100000000"})
        assert "Admin-Token ist falsch" in r.text
        r = c.post("/admin/demo-anruf", data={"token": "admin", "nummer": "+491700000000"})
        assert "nicht in TEST_NUMBERS" in r.text


def test_retell_sync_updates_llm_agent_and_publishes(tmp_path):
    import httpx
    from app.clients import RetellClient
    calls = []

    def handler(req):
        calls.append((req.method, req.url.path, json.loads(req.content) if req.content else None))
        if req.url.path.startswith("/get-agent/"):
            return httpx.Response(200, json={"version": 4, "is_published": True,
                                             "response_engine": {"type": "retell-llm", "llm_id": "llm1", "version": 4}})
        if req.url.path.startswith("/create-agent-version/"):
            return httpx.Response(201, json={"version": 5, "is_published": False,
                                             "response_engine": {"type": "retell-llm", "llm_id": "llm1", "version": 5}})
        if req.url.path.startswith("/update-retell-llm/"):
            assert req.url.params.get("version") == "5"
            return httpx.Response(200, json={"version": 5})
        if req.url.path.startswith("/update-agent/"):
            assert req.url.params.get("version") == "5"
            return httpx.Response(200, json={"version": 5})
        return httpx.Response(200, json={})

    with _client(tmp_path):
        settings.retell_agent_id = "agent_x"
        settings.public_base_url = "https://bot.example"
        main.bot.retell = RetellClient("geheim", transport=httpx.MockTransport(handler))
        res = main.do_retell_sync(publish=True)
    assert res == {"ok": True, "agent_version": 5, "veroeffentlicht": True}
    agent_body = [b for m, p, b in calls if p.startswith("/update-agent/")][0]
    assert ("POST", "/create-agent-version/agent_x", {"base_version": 4}) in calls
    assert agent_body["webhook_url"] == "https://bot.example/webhook/retell"
    assert ("POST", "/publish-agent-version/agent_x", {"version": 5}) in calls
    settings.retell_agent_id = ""
    settings.public_base_url = ""
