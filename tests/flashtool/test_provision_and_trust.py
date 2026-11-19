"""UNIT/INTEGRATION (real HTTP + real TLS on localhost, real device code): the flash tool's per-device secrets, the owner sheet, and the
complete P7 flow - card -> first boot (bootfiles) -> WebUI -> the flash tool authenticates the CA with the trust key and then reaches
HTTPS trusting ONLY that CA. A LAN attacker answering with its own CA is detected."""
import http.server
import json
import secrets
import threading

import pytest

from btbridge import bootfiles, provisioning as P, trustproof
from btbridge.net.access import LocalAccess
from btbridge.net.certs import CertManager
from btbridge.service import ApplianceService
from btbridge.system import SystemMonitor
from btbridge.webui.server import WebUI
from hififlash import provision as FP, trust as FT


def test_generated_documents_are_individual_valid_and_fit_the_slot():
    docs = [FP.new_document("DE", device_name="Küche", wifi=("Home", "wifi-secret-123"))[0] for _ in range(20)]
    for d in docs:
        assert P.validate(d) == [] and len(P.render(d)) == P.SLOT_SIZE
        assert len(d["hotspot"]["psk"].replace("-", "")) == 20 and d["hotspot"]["ssid"].startswith("Hi-Fi Bridge ")
    for field in ("setup_code", "trust_key"):
        assert len({d[field] for d in docs}) == 20                            # never shared between devices
    assert len({d["hotspot"]["psk"] for d in docs}) == 20
    with pytest.raises(P.ProvisionError):
        FP.new_document("DE", hotspot_psk="short")                            # the owner's own hotspot password must be >= 12
    assert "hotspot" not in FP.new_document("DE", hotspot=False)[0]


def test_owner_sheet_contains_what_the_owner_needs_and_no_device_secret():
    doc, key = FP.new_document("AT", wifi=("Home", "wifi-secret-123"))
    for lang in ("en", "de"):
        sheet = FP.owner_sheet(doc, lang)
        assert doc["setup_code"] in sheet and doc["hotspot"]["psk"] in sheet
        assert doc["trust_key"] not in sheet and "wifi-secret-123" not in sheet


@pytest.fixture
def device(tmp_path):
    """A device after its first boot with a card provisioned by the flash tool."""
    data, boot = tmp_path / "data", tmp_path / "boot"; data.mkdir(); boot.mkdir()
    doc, key = FP.new_document("DE", device_name="Wohnzimmer")
    (boot / P.FILE_NAME).write_bytes(P.render(doc))
    bootfiles.run(str(data), str(boot), iterations=1000, log=lambda m: None)
    svc = ApplianceService(str(data), stream_port=0, advertise_host="127.0.0.1", echo=None, auth_iterations=1000,
                           monitor=SystemMonitor(thermal=None, data_dir=str(data)))
    svc.store.update({"mdns_enabled": False})
    svc.access = LocalAccess(svc.store, str(data), svc.instance_id(), 0, 0, log=svc.logs, addresses=lambda: ["127.0.0.1"])
    svc.access.start(); svc.start()
    ui = WebUI(svc, "127.0.0.1", 0, tls_port=0); ui.start()
    yield type("D", (), dict(svc=svc, ui=ui, key=key, doc=doc, boot=boot))
    ui.stop(); svc.stop()


def test_flash_tool_authenticates_the_ca_then_https_works_and_setup_ends_the_key(device):
    der = FT.fetch_verified_ca(f"http://127.0.0.1:{device.ui.port}", device.key)
    fp = trustproof.fingerprint(der)
    assert fp == device.svc.access.certs.ca_info()["sha256"] and fp in (device.boot / "hifi-status.txt").read_text()
    assert FT.check_https(f"https://127.0.0.1:{device.ui.tls_port}", der) == 200       # TLS verified against ONLY this CA
    with pytest.raises(trustproof.TrustProofError):
        FT.fetch_verified_ca(f"http://127.0.0.1:{device.ui.port}", secrets.token_bytes(32))   # someone else's key
    device.svc.auth.complete_setup("c", device.doc["setup_code"], "a good long passphrase")
    with pytest.raises(Exception):                                                  # 404 after the setup
        FT.fetch_verified_ca(f"http://127.0.0.1:{device.ui.port}", device.key)


def test_a_lan_attacker_with_its_own_ca_is_detected(device, tmp_path):
    evil = CertManager(str(tmp_path / "evil"), "evil0001"); evil.ensure_ca()
    genuine = f"http://127.0.0.1:{device.ui.port}"
    import urllib.request

    class Mitm(http.server.BaseHTTPRequestHandler):
        def do_GET(self):                                        # relays to the device but swaps in its own CA
            with urllib.request.urlopen(genuine + self.path) as r:
                doc = json.loads(r.read())
            doc["ca_der"] = __import__("base64").b64encode(evil.ca_der()).decode()
            doc["sha256"] = trustproof.fingerprint(evil.ca_der())
            body = json.dumps(doc).encode()
            self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, *a):
            pass
    srv = http.server.HTTPServer(("127.0.0.1", 0), Mitm)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        with pytest.raises(trustproof.TrustProofError, match="could not prove"):
            FT.fetch_verified_ca(f"http://127.0.0.1:{srv.server_port}", device.key)
    finally:
        srv.shutdown()


def test_cli_provision_then_verify_device(device, tmp_path, capsys):
    from hififlash.__main__ import main
    out, keyf = tmp_path / "hifi-provision.json", tmp_path / "trust.key"
    assert main(["provision", "--country", "DE", "--device-name", "Bad", "--out", str(out), "--key-out", str(keyf), "--lang", "de"]) == 0
    sheet = capsys.readouterr().out
    doc = P.parse(out.read_bytes())
    assert len(out.read_bytes()) == P.SLOT_SIZE and doc["setup_code"] in sheet and "Einrichtungscode" in sheet
    assert oct(keyf.stat().st_mode)[-3:] == "600" and doc["trust_key"] not in sheet
    keyf.write_text(P.encode_trust_key(device.key))                 # the running device was provisioned with this key
    ca_out = tmp_path / "ca.pem"
    assert main(["verify-device", "--url", f"http://127.0.0.1:{device.ui.port}", "--key-file", str(keyf),
                 "--https", f"https://127.0.0.1:{device.ui.tls_port}", "--ca-out", str(ca_out)]) == 0
    text = capsys.readouterr().out
    assert "VERIFIED" in text and "HTTP 200" in text and "BEGIN CERTIFICATE" in ca_out.read_text()
    keyf.write_text(P.encode_trust_key(secrets.token_bytes(32)))
    assert main(["verify-device", "--url", f"http://127.0.0.1:{device.ui.port}", "--key-file", str(keyf)]) == 1
    keyf.write_text(P.encode_trust_key(device.key))                 # verified without saving the CA and without the HTTPS check
    assert main(["verify-device", "--url", f"http://127.0.0.1:{device.ui.port}", "--key-file", str(keyf)]) == 0
    assert "VERIFIED" in capsys.readouterr().out


def test_cli_verify_image(tmp_path, capsys):
    import hashlib
    from hififlash.__main__ import main
    from hififlash import manifest as M
    img = tmp_path / "x.img"; img.write_bytes(b"\x00" * 8192)
    man = {"format": M.FORMAT, "compatible": "c", "version": "1", "image": "x.img", "size": 8192,
           "sha256": hashlib.sha256(img.read_bytes()).hexdigest(), "slot": {"file": P.FILE_NAME, "offset": 512, "size": P.SLOT_SIZE}}
    mf = tmp_path / "m.json"; mf.write_bytes(M.dumps(man))
    assert main(["verify-image", str(img), "--manifest", str(mf)]) == 0
    img.write_bytes(b"\x01" * 8192)
    assert main(["verify-image", str(img), "--manifest", str(mf)]) == 1
    assert main(["verify-image", str(img), "--manifest", str(mf), "--sig", str(mf)]) == 2
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    k = ec.generate_private_key(ec.SECP256R1())
    sig, pub = tmp_path / "m.sig", tmp_path / "p.pem"
    sig.write_bytes(k.sign(mf.read_bytes(), ec.ECDSA(hashes.SHA256())))
    pub.write_bytes(k.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    img.write_bytes(b"\x00" * 8192)
    assert main(["verify-image", str(img), "--manifest", str(mf), "--sig", str(sig), "--key", str(pub)]) == 0
    assert "manifest signed by: publisher" in capsys.readouterr().out
