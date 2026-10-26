"""Anbindung an Propstack (API V1) und Retell."""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from datetime import datetime

import httpx

log = logging.getLogger("rueckruf")


class PropstackClient:
    def __init__(self, api_key: str, base_url: str = "https://api.propstack.de/v1", *, dry: bool = False,
                 transport: httpx.BaseTransport | None = None):
        self.dry = dry
        self.http = httpx.Client(
            base_url=base_url, headers={"X-API-KEY": api_key, "Accept": "application/json"},
            timeout=20, transport=transport,
        )
        self._broker_cache: dict[int, dict] = {}

    def _get(self, path: str, **params):
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    # --- lesen ---------------------------------------------------------------
    def inquiries_since(self, since: datetime, max_pages: int = 10) -> list[dict]:
        """Neue Interessenten-Anfragen seit `since`."""
        out: list[dict] = []
        for page in range(1, max_pages + 1):
            data = self._get(
                "/activities", only_inquiries="true", order="asc", per=100, page=page,
                original_created_at_from=since.isoformat(timespec="seconds"),
            )
            items = data.get("data", []) if isinstance(data, dict) else data
            out += items
            if len(items) < 100:
                break
        return out

    def contact(self, client_id: int) -> dict:
        return self._get(f"/contacts/{client_id}")

    def unit(self, property_id: int) -> dict:
        return self._get(f"/units/{property_id}", new=1)

    def broker(self, broker_id: int) -> dict | None:
        if not broker_id:
            return None
        if not self._broker_cache:
            data = self._get("/brokers")
            for b in data if isinstance(data, list) else data.get("data", []):
                self._broker_cache[b["id"]] = b
        return self._broker_cache.get(broker_id)

    def activity_types(self) -> list[dict]:
        data = self._get("/activity_types")
        return data.get("data", data) if isinstance(data, dict) else data

    def brokers(self) -> list[dict]:
        data = self._get("/brokers")
        return data if isinstance(data, list) else data.get("data", [])

    # --- schreiben -----------------------------------------------------------
    def create_note(self, *, title: str, body: str, client_id: int, property_id: int | None, note_type_id: int = 0):
        task = {"title": title, "body": body, "client_ids": [client_id]}
        if property_id:
            task["property_ids"] = [property_id]
        if note_type_id:
            task["note_type_id"] = note_type_id
        return self._post("/tasks", {"task": task})

    def create_task(self, *, title: str, body: str, due: datetime, client_id: int, property_id: int | None,
                    broker_id: int | None, note_type_id: int = 0):
        task = {"is_reminder": True, "title": title, "body": body, "client_ids": [client_id],
                "due_date": due.isoformat(timespec="seconds")}
        if property_id:
            task["property_ids"] = [property_id]
        if broker_id:
            task["broker_id"] = broker_id
        if note_type_id:
            task["note_type_id"] = note_type_id
        return self._post("/tasks", {"task": task})

    def add_groups(self, client_id: int, group_ids: list[int]):
        if not group_ids:
            return None
        if self.dry:
            log.info("[dry] Merkmale %s an Kontakt %s", group_ids, client_id)
            return {"dry": True}
        r = self.http.put(f"/contacts/{client_id}", json={"client": {"add_group_ids": group_ids}})
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, payload: dict):
        if self.dry:
            log.info("[dry] POST %s %s", path, payload)
            return {"dry": True}
        r = self.http.post(path, json=payload)
        r.raise_for_status()
        return r.json()


class RetellClient:
    def __init__(self, api_key: str, base_url: str = "https://api.retellai.com", *,
                 transport: httpx.BaseTransport | None = None):
        self.api_key = api_key
        self.http = httpx.Client(
            base_url=base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=20, transport=transport,
        )

    def create_call(self, *, from_number: str, to_number: str, agent_id: str, variables: dict, metadata: dict,
                    idempotency_key: str) -> dict:
        r = self.http.post("/v2/create-phone-call", json={
            "from_number": from_number, "to_number": to_number, "override_agent_id": agent_id,
            "retell_llm_dynamic_variables": {k: str(v) for k, v in variables.items()},
            "metadata": metadata, "idempotency_key": idempotency_key,
        })
        r.raise_for_status()
        return r.json()

    def get_call(self, call_id: str) -> dict:
        r = self.http.get(f"/v2/get-call/{call_id}")
        r.raise_for_status()
        return r.json()

    def get_agent(self, agent_id: str) -> dict:
        r = self.http.get(f"/get-agent/{agent_id}")
        r.raise_for_status()
        return r.json()

    def update_agent(self, agent_id: str, body: dict) -> dict:
        r = self.http.patch(f"/update-agent/{agent_id}", json=body)
        r.raise_for_status()
        return r.json()

    def update_llm(self, llm_id: str, body: dict) -> dict:
        r = self.http.patch(f"/update-retell-llm/{llm_id}", json=body)
        r.raise_for_status()
        return r.json()

    def publish_agent(self, agent_id: str, version: int) -> dict:
        r = self.http.post(f"/publish-agent-version/{agent_id}", json={"version": version})
        r.raise_for_status()
        return r.json() if r.content else {}


_SIG = re.compile(r"v=(\d+),d=(.*)")


def verify_retell_signature(raw_body: str, api_key: str, signature: str | None, *, now_ms: int | None = None) -> bool:
    """Prüft den X-Retell-Signature-Header (HMAC-SHA256 über Body + Zeitstempel, max. 5 Min. alt)."""
    if not signature or not api_key:
        return False
    m = _SIG.fullmatch(signature.strip())
    if not m:
        return False
    ts, digest = m.group(1), m.group(2)
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - int(ts)) > 5 * 60 * 1000:
        return False
    expected = hmac.new(api_key.encode(), (raw_body + ts).encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, digest)
