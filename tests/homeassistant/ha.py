"""Minimal Home Assistant REST helper for the lab (onboarding, MQTT config flow, templates, services, states)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://homeassistant:8123"
CID = "http://runner/"


class HA:
    def __init__(self, base: str = BASE):
        self.base, self.token = base, None

    def call(self, method, path, data=None, form=False, timeout=30):
        h, body = {}, None
        if data is not None:
            body = urllib.parse.urlencode(data).encode() if form else json.dumps(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded" if form else "application/json"
        if self.token:
            h["Authorization"] = "Bearer " + self.token
        req = urllib.request.Request(self.base + path, body, h, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                try:
                    return r.status, json.loads(raw or b"null")
                except ValueError:
                    return r.status, raw.decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:500]

    def wait_up(self, timeout=300):
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                s, _ = self.call("GET", "/api/onboarding" if not self.token else "/api/", timeout=5)
                if s in (200, 201):
                    return time.time() - t0
            except OSError:
                pass
            time.sleep(2)
        raise TimeoutError("Home Assistant did not come up")

    def onboard(self, user="lab", password="lab-only-ha-password"):
        s, d = self.call("POST", "/api/onboarding/users", {"client_id": CID, "name": "Lab", "username": user, "password": password,
                                                            "language": "en"})
        assert s == 200, d
        s, tok = self.call("POST", "/auth/token", {"grant_type": "authorization_code", "code": d["auth_code"], "client_id": CID}, form=True)
        assert s == 200, tok
        self.token = tok["access_token"]
        for step, body in (("core_config", {}), ("analytics", {}), ("integration", {"client_id": CID, "redirect_uri": CID + "cb"})):
            s, d = self.call("POST", "/api/onboarding/" + step, body)
            assert s == 200, (step, d)

    def add_mqtt(self, broker, port, username, password):
        s, f = self.call("POST", "/api/config/config_entries/flow", {"handler": "mqtt", "show_advanced_options": False})
        assert s == 200, f
        values = {"broker": broker, "port": port, "username": username, "password": password, "protocol": "3.1.1"}
        for _ in range(5):
            if f.get("type") == "create_entry":
                return f
            data = {}
            for x in f.get("data_schema", []):
                if x["name"] in values:
                    data[x["name"]] = values[x["name"]]
                elif x.get("type") == "expandable":
                    # explicit choices (lab broker = plain TCP on an isolated network; TLS verification is never "insecure")
                    explicit = {"set_client_cert": False, "set_ca_cert": "off", "transport": "tcp"}
                    data[x["name"]] = {y["name"]: explicit.get(y["name"], y.get("default")) for y in x.get("schema", [])
                                       if y.get("required")}
            s, f = self.call("POST", f"/api/config/config_entries/flow/{f['flow_id']}", data)
            assert s == 200, f
        raise AssertionError(f"MQTT flow did not finish: {f}")

    def template(self, tpl: str) -> str:
        s, d = self.call("POST", "/api/template", {"template": tpl})
        assert s == 200, d
        return d if isinstance(d, str) else json.dumps(d)

    def mqtt_entities(self) -> list[str]:
        return json.loads(self.template("{{ integration_entities('mqtt') | list | tojson }}"))

    def state(self, entity_id: str) -> dict | None:
        s, d = self.call("GET", f"/api/states/{entity_id}")
        return d if s == 200 else None

    def service(self, domain, service, data):
        s, d = self.call("POST", f"/api/services/{domain}/{service}", data)
        assert s == 200, d
        return d

    def entity_map(self, device_name: str, iid: str | None = None) -> dict[str, str]:
        """{entity name: entity_id} of OUR device: matched by the HA device identifier ('mqtt', 'hifibridge_<iid>') when the
        instance id is known (robust against other devices with the same name), else by the friendly-name prefix."""
        out = {}
        for e in self.mqtt_entities():
            st = self.state(e)
            fn = (st or {}).get("attributes", {}).get("friendly_name", "")
            if iid is not None:
                ids = self.template("{{ device_attr('" + e + "', 'identifiers') | list | tojson }}")
                if f"hifibridge_{iid}" not in ids:
                    continue
            if fn.startswith(device_name + " "):
                out[fn[len(device_name) + 1:]] = e
        return out

    def wait(self, cond, timeout=30, step=1.0):
        t0 = time.time()
        last = None
        while time.time() - t0 < timeout:
            try:
                last = cond()
                if last:
                    return last
            except (AssertionError, OSError, KeyError, ValueError):
                pass
            time.sleep(step)
        return last
