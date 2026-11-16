"""UNIT: P7 trust proof - the device answers with its CA and an HMAC over a fresh nonce; a substituted CA, a replayed answer or a wrong
key are rejected; the endpoint disappears with the setup. Real HTTP against the WebUI."""
import json
import secrets

import pytest

from btbridge import provisioning as P, trustproof as T
from btbridge.net.certs import CertManager
from test_webui import Client
from test_webui_access import ui  # noqa: F401  (fixture: service with LocalAccess and a real device CA)


def test_verify_accepts_the_genuine_answer_and_rejects_substitution_replay_and_wrong_key(tmp_path):
    key = secrets.token_bytes(32)
    ca = CertManager(str(tmp_path / "dev"), "dev00001"); ca.ensure_ca()
    der = ca.ca_der()
    nonce = secrets.token_hex(16)
    doc = T.answer(key, nonce, der)
    assert T.verify(key, nonce, doc) == der
    evil = CertManager(str(tmp_path / "evil"), "evil0001"); evil.ensure_ca()
    forged = {**doc, "ca_der": __import__("base64").b64encode(evil.ca_der()).decode(), "sha256": T.fingerprint(evil.ca_der())}
    with pytest.raises(T.TrustProofError):
        T.verify(key, nonce, forged)                                     # attacker's CA with the genuine mac
    with pytest.raises(T.TrustProofError):
        T.verify(key, secrets.token_hex(16), doc)                        # replay for another nonce
    with pytest.raises(T.TrustProofError):
        T.verify(secrets.token_bytes(32), nonce, doc)                    # wrong key
    with pytest.raises(T.TrustProofError):
        T.verify(key, nonce, {**doc, "sha256": "AA"})
    with pytest.raises(ValueError):
        T.answer(key, "not-hex", der)


def test_endpoint_answers_only_while_the_trust_key_exists(ui, tmp_path):
    key = secrets.token_bytes(32)
    ui.svc.store.set_secret("trust_key", P.encode_trust_key(key))
    c = Client(ui.port)
    nonce = secrets.token_hex(16)
    r = c.get(f"/trust/proof?nonce={nonce}")
    assert r.status == 200 and r.getheader("Cache-Control") == "no-store"
    der = T.verify(key, nonce, json.loads(r.body))
    assert T.fingerprint(der) == ui.svc.access.certs.ca_info()["sha256"]
    assert c.get("/trust/proof?nonce=xyz").status == 400
    ui.svc.store.set_secret("trust_key", None)
    assert c.get(f"/trust/proof?nonce={nonce}").status == 404
