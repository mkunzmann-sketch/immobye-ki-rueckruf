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
