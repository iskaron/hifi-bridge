"""Per-device secrets for the provisioning file (P6) - generated on the owner's computer, shown to the owner once, written to the card.

Nothing here is a shared or default secret. The owner-facing summary contains what the owner must keep (setup code, hotspot name and
password); the trust key is never shown (the tool keeps it only until it has verified the device, P7).
"""
from __future__ import annotations

import secrets

from btbridge import provisioning as P

PSK_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"        # unambiguous, easy to type on a phone


def setup_code() -> str:
    raw = "".join(secrets.choice(P.SETUP_ALPHABET) for _ in range(12))
    return f"{raw[:4]}-{raw[4:8]}-{raw[8:]}"


def hotspot_password() -> str:
    """20 characters from 31 symbols (~99 bits), in groups of five."""
    raw = "".join(secrets.choice(PSK_ALPHABET) for _ in range(20))
    return "-".join(raw[i:i + 5] for i in range(0, 20, 5))


def new_document(country: str, device_name: str | None = None, wifi: tuple[str, str] | None = None, hotspot: bool = True,
                 hotspot_psk: str | None = None, tool_version: str = "hififlash", local_hostname: str | None = None) -> tuple[dict, bytes]:
    """Returns (document, trust_key). A custom hotspot password must satisfy the device's rule (>= 12 printable ASCII)."""
    key = secrets.token_bytes(32)
    doc = {"format": P.FORMAT, "created_by": tool_version, "setup_code": setup_code(), "trust_key": P.encode_trust_key(key),
           "country": country}
    if device_name:
        doc["device_name"] = device_name
    if local_hostname:
        doc["local_hostname"] = local_hostname
    if hotspot:
        doc["hotspot"] = {"ssid": f"Hi-Fi Bridge {secrets.token_hex(2).upper()}", "psk": hotspot_psk or hotspot_password()}
    if wifi:
        doc["wifi"] = {"ssid": wifi[0], "psk": wifi[1]}
    problems = P.validate(doc)
    if problems:
        raise P.ProvisionError(problems)
    return doc, key


def owner_sheet(doc: dict, lang: str = "en") -> str:
    """What the owner must keep (to print or save). No trust key, no Wi-Fi password of the home network."""
    de = lang == "de"
    lines = ["Hi-Fi Bridge", "", ("Einrichtungscode" if de else "Setup code") + f": {doc['setup_code']}"]
    if "hotspot" in doc:
        lines += [("Einrichtungs-WLAN" if de else "Setup Wi-Fi") + f": {doc['hotspot']['ssid']}",
                  ("Passwort" if de else "Password") + f": {doc['hotspot']['psk']}"]
    lines += ["", "Der Code wird ungültig, sobald Sie das Besitzerpasswort setzen." if de
              else "The code becomes invalid as soon as you set the owner password."]
    return "\n".join(lines) + "\n"
