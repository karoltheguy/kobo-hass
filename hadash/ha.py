"""Home Assistant REST client, plus an in-memory fake for development."""

from __future__ import annotations

import random
import threading

import requests


class HAError(RuntimeError):
    pass


class HAClient:
    """Minimal REST client. States are fetched in one call to /api/states."""

    def __init__(self, url: str, token: str, timeout: float = 5.0):
        if not url or not token:
            raise HAError("Home Assistant URL and token are required (or set mock: true)")
        self.url = url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        )

    def get_states(self, entity_ids: list[str]) -> dict[str, dict]:
        try:
            resp = self.session.get(f"{self.url}/api/states", timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise HAError(f"cannot read states: {exc}") from exc
        wanted = set(entity_ids)
        return {s["entity_id"]: s for s in resp.json() if s.get("entity_id") in wanted}

    def call_service(self, domain: str, service: str, data: dict) -> None:
        try:
            resp = self.session.post(
                f"{self.url}/api/services/{domain}/{service}", json=data, timeout=self.timeout
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise HAError(f"{domain}.{service} failed: {exc}") from exc


class FakeHA:
    """Pretends to be HA so the dashboard can be built without one."""

    def __init__(self):
        self._states: dict[str, dict] = {}
        self._lock = threading.Lock()

    def _seed(self, entity_id: str) -> dict:
        domain = entity_id.split(".", 1)[0]
        if domain in ("light", "switch", "fan", "input_boolean"):
            state = {"state": random.choice(["on", "off"]), "attributes": {}}
        elif domain == "climate":
            state = {
                "state": "heat",
                "attributes": {"current_temperature": 20.5, "temperature": 21,
                               "target_temp_step": 0.5, "min_temp": 7, "max_temp": 30,
                               "preset_mode": "home"},
            }
        elif domain == "sensor":
            state = {"state": f"{random.uniform(18, 24):.1f}", "attributes": {}}
        elif domain == "cover":
            state = {"state": "closed", "attributes": {}}
        elif domain == "lock":
            state = {"state": "locked", "attributes": {}}
        elif domain == "binary_sensor":
            state = {"state": "off", "attributes": {}}
        elif domain == "weather":
            state = {"state": "partlycloudy", "attributes": {"temperature": 8.5}}
        else:
            state = {"state": "unknown", "attributes": {}}
        state["entity_id"] = entity_id
        return state

    def get_states(self, entity_ids: list[str]) -> dict[str, dict]:
        with self._lock:
            for eid in entity_ids:
                if eid not in self._states:
                    self._states[eid] = self._seed(eid)
            return {eid: dict(self._states[eid]) for eid in entity_ids}

    def call_service(self, domain: str, service: str, data: dict) -> None:
        entity_ids = data.get("entity_id") or []
        if isinstance(entity_ids, str):
            entity_ids = [entity_ids]
        with self._lock:
            for eid in entity_ids:
                st = self._states.setdefault(eid, self._seed(eid))
                if service == "toggle":
                    st["state"] = {"on": "off", "off": "on", "open": "closed",
                                   "closed": "open", "locked": "unlocked",
                                   "unlocked": "locked"}.get(st["state"], st["state"])
                elif service in ("turn_on", "open_cover", "unlock"):
                    st["state"] = {"turn_on": "on", "open_cover": "open", "unlock": "unlocked"}[service]
                elif service in ("turn_off", "close_cover", "lock"):
                    st["state"] = {"turn_off": "off", "close_cover": "closed", "lock": "locked"}[service]
                elif service == "set_temperature" and "temperature" in data:
                    st["attributes"]["temperature"] = data["temperature"]
                elif service == "set_preset_mode" and "preset_mode" in data:
                    st["attributes"]["preset_mode"] = data["preset_mode"]
