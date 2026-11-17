"""Optional Home Assistant for the demo (profile ``ha``): onboard it with a generated owner password and add the MQTT integration for the
demo's Mosquitto, so that the bridge's MQTT discovery can be tried right away. Credentials are stored in the console's demo volume (0600)
and shown by ``make demo-status``. Nothing leaves the internal demo network.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("DEMO_HA_URL", "http://homeassistant:8123")
CID = "http://console/"
CREDS = os.environ.get("DEMO_HA_CREDS", "/data/home-assistant-owner.json")
MQTT_CREDS = os.environ.get("DEMO_MQTT_CREDS", "/mqtt-creds/credentials.env")


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
            with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310 - fixed http URL on the internal demo network
                raw = r.read()
                try:
                    return r.status, json.loads(raw or b"null")
                except ValueError:
                    return r.status, raw.decode()
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode()[:500]

    def wait_up(self, timeout=600):
        end = time.time() + timeout
        while time.time() < end:
            try:
                s, _ = self.call("GET", "/api/onboarding", timeout=5)
                if s in (200, 201):
                    return
            except OSError:
                pass
            time.sleep(3)
        raise TimeoutError("Home Assistant did not come up")

    def onboard(self, user: str, password: str) -> None:
        s, d = self.call("POST", "/api/onboarding/users", {"client_id": CID, "name": "Demo owner", "username": user,
                                                            "password": password, "language": "de"})
        if s != 200:
            raise RuntimeError(f"onboarding failed: {s} {d}")
        s, tok = self.call("POST", "/auth/token", {"grant_type": "authorization_code", "code": d["auth_code"], "client_id": CID}, form=True)
        if s != 200:
            raise RuntimeError(f"token failed: {s}")
        self.token = tok["access_token"]
        for step, body in (("core_config", {}), ("analytics", {}), ("integration", {"client_id": CID, "redirect_uri": CID + "cb"})):
            self.call("POST", "/api/onboarding/" + step, body)

    def login(self, user: str, password: str) -> None:
        s, f = self.call("POST", "/auth/login_flow", {"client_id": CID, "handler": ["homeassistant", None], "redirect_uri": CID + "cb"})
        s, r = self.call("POST", f"/auth/login_flow/{f['flow_id']}", {"client_id": CID, "username": user, "password": password})
        if r.get("type") != "create_entry":
            raise RuntimeError("login failed")
        s, tok = self.call("POST", "/auth/token", {"grant_type": "authorization_code", "code": r["result"], "client_id": CID}, form=True)
        self.token = tok["access_token"]

    def has_mqtt(self) -> bool:
        s, entries = self.call("GET", "/api/config/config_entries/entry")
        return s == 200 and any(e.get("domain") == "mqtt" for e in entries)

    def add_mqtt(self, broker, port, username, password):
        s, f = self.call("POST", "/api/config/config_entries/flow", {"handler": "mqtt", "show_advanced_options": False})
        values = {"broker": broker, "port": port, "username": username, "password": password, "protocol": "3.1.1"}
        for _ in range(5):
            if f.get("type") == "create_entry":
                return f
            data = {}
            for x in f.get("data_schema", []):
                if x["name"] in values:
                    data[x["name"]] = values[x["name"]]
                elif x.get("type") == "expandable":
                    explicit = {"set_client_cert": False, "set_ca_cert": "off", "transport": "tcp"}
                    data[x["name"]] = {y["name"]: explicit.get(y["name"], y.get("default")) for y in x.get("schema", []) if y.get("required")}
            s, f = self.call("POST", f"/api/config/config_entries/flow/{f['flow_id']}", data)
            if s != 200:
                raise RuntimeError(f"MQTT flow failed: {f}")
        raise RuntimeError("MQTT flow did not finish")


def read_env(path: str) -> dict:
    with open(path) as fh:
        return dict(line.strip().split("=", 1) for line in fh if "=" in line)


def main() -> int:
    ha = HA()
    print("[ha-setup] waiting for Home Assistant (first start takes a few minutes) ...", flush=True)
    ha.wait_up()
    if os.path.exists(CREDS):
        owner = json.load(open(CREDS))
        s, d = ha.call("GET", "/api/onboarding")
        if isinstance(d, list) and not all(x.get("done") for x in d):
            ha.onboard(owner["username"], owner["password"])
        else:
            ha.login(owner["username"], owner["password"])
    else:
        owner = {"username": "demo", "password": secrets.token_urlsafe(15)}
        ha.onboard(owner["username"], owner["password"])
        fd = os.open(CREDS, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(owner, fh)
    if not ha.has_mqtt():
        m = read_env(MQTT_CREDS)
        ha.add_mqtt("mosquitto", 1883, "homeassistant", m["HOMEASSISTANT_PASSWORD"])
        print("[ha-setup] MQTT integration added (broker mosquitto:1883)", flush=True)
    print("[ha-setup] Home Assistant ready; owner login: see make demo-status", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
