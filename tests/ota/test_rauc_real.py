"""INTEGRATION with the REAL RAUC 1.13 service (scripts/run_ota.sh): the bridge's RAUC client, the update manager, the health check,
the D-Bus policy of the image and the bundle script - against `rauc service` with the product's system.conf, the U-Boot backend writing a
FILE-BACKED environment, file-backed slots and throw-away keys.

NOT covered here (no loop device / device-mapper in the container, no board): mounting a bundle, writing a slot, booting it and the
U-Boot boot-count. The installation therefore ends at RAUC's mount step; the tests assert that this failure leaves the device on its slot."""
import json
import os
import shutil
import subprocess
import time

import pytest
from conftest import COMPAT, build_bundle, raw_bundle, sh

from btbridge.ota import health as H
from btbridge.ota.manager import UpdateManager
from btbridge.ota.rauc import RaucClient, RaucError, RaucUnavailable


@pytest.fixture
def client(rauc):
    c = RaucClient(bus=rauc.address, timeout=10)
    yield c
    c.close()


def wait(pred, timeout=20.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = pred()
        if r:
            return r
        time.sleep(0.05)
    raise AssertionError("timeout")


# ---------------------------------------------------------------- the client against real RAUC
def test_properties_slots_and_primary(client):
    p = client.properties()
    assert p["Operation"] == "idle" and p["Compatible"] == COMPAT and p["BootSlot"] == "A" and p["LastError"] == ""
    assert isinstance(p["Progress"], tuple) and len(p["Progress"]) == 3
    slots = {s["bootname"]: s for s in client.slots()}
    assert slots["A"]["state"] == "booted" and slots["A"]["boot-status"] == "good" and slots["B"]["boot-status"] == "bad"
    assert client.primary() == "rootfs.0"


def test_mark_writes_the_u_boot_environment(client, rauc):
    assert rauc.env()["BOOT_ORDER"] == "A B" and rauc.env()["BOOT_B_LEFT"] == "0"
    assert client.mark("good", "booted") == ("rootfs.0", "marked slot rootfs.0 as good")
    assert rauc.env()["BOOT_A_LEFT"] == "3"
    assert client.mark("active", "other")[0] == "rootfs.1"
    env = rauc.env()
    assert env["BOOT_ORDER"].split()[0] == "B" and env["BOOT_B_LEFT"] == "3" and client.primary() == "rootfs.1"
    client.mark("bad", "other")
    env = rauc.env()
    assert env["BOOT_B_LEFT"] == "0" and "B" not in env["BOOT_ORDER"].split() and client.primary() == "rootfs.0"
    with pytest.raises(RaucError):
        client.mark("good", "rootfs.9")


def _copies(rauc):
    """[(serial, crc_ok, vars)] of both environment copies (U-Boot layout: CRC32, serial byte, NUL-separated pairs)."""
    import zlib
    out = []
    for p in rauc.env_files:
        raw = open(p, "rb").read()
        data = raw[5:]
        pairs = dict(x.decode().split("=", 1) for x in data.split(b"\0\0")[0].split(b"\0") if x)
        out.append((raw[4], int.from_bytes(raw[:4], "little") == zlib.crc32(data), pairs))
    return out


def _serials(rauc):
    return [c[0] for c in _copies(rauc)]


def test_rauc_writes_the_older_copy_and_a_torn_newest_copy_keeps_the_previous_state(client, rauc):
    """Review N-6 with the real RAUC 1.13 U-Boot backend and the real fw_setenv on the product's two-copy layout: every write goes to
    the older copy with the next serial; a power cut that tears the newest copy leaves the state of the previous write (found here:
    RAUC activates a slot with TWO writes, so a cut can also lose only the second one). Never a fall back to a default environment."""
    assert _serials(rauc) == [1, 0]
    sh("fw_setenv", "BOOT_A_LEFT", "2")                                 # what U-Boot does at boot: writes copy 2
    assert _serials(rauc) == [1, 2]
    client.mark("good", "booted")                                       # health check: writes copy 1
    assert _serials(rauc) == [3, 2] and rauc.env()["BOOT_A_LEFT"] == "3"
    client.mark("active", "other")                                      # activation of B
    serials = _serials(rauc)
    assert min(serials) >= 3 and abs(serials[0] - serials[1]) == 1 and client.primary() == "rootfs.1"
    newest = serials.index(max(serials))
    older = _copies(rauc)[1 - newest]
    raw = bytearray(open(rauc.env_files[newest], "rb").read())          # power cut during the last write: newest copy torn
    raw[0x2000] ^= 0xFF
    open(rauc.env_files[newest], "wb").write(bytes(raw))
    env = rauc.env()
    assert older[1] and env == older[2] and env["hifi_env"] == "1"      # exactly the previous, CRC-valid state
    assert env["BOOT_ORDER"].split()[0] == {"rootfs.0": "A", "rootfs.1": "B"}[client.primary()]
    client.mark("active", "other")                                      # writes again: the torn copy is replaced
    assert all(c[1] for c in _copies(rauc)) and client.primary() == "rootfs.1"


def test_inspect_verifies_signature_and_purpose_but_not_compatible(client, keys, tmp_path):
    good, out = build_bundle(tmp_path, signer=keys["dev"], name="good")
    info = client.inspect(good)
    assert info["update"]["version"] == "0.4.0" and info["update"]["compatible"] == COMPAT and info["bundle"]["format"] == "verity"
    other, _ = build_bundle(tmp_path, signer=keys["other"], name="other")
    with pytest.raises(RaucError) as e:
        client.inspect(other)                                          # signed by a CA that is not in the keyring
    assert "signature" in str(e.value).lower() or "verif" in str(e.value).lower()
    mail = raw_bundle(tmp_path, keys["mail"], "mail")
    with pytest.raises(RaucError):
        client.inspect(mail)                                           # trusted CA, but no codeSigning purpose (check-purpose=codesign)
    board, _ = build_bundle(tmp_path, signer=keys["dev"], compatible="hifi-bridge-rpi4", name="board")
    assert client.inspect(board)["update"]["compatible"] == "hifi-bridge-rpi4"      # RAUC does NOT refuse here: the manager must
    junk = tmp_path / "junk.raucb"
    junk.write_bytes(os.urandom(70_000))
    with pytest.raises(RaucError):
        client.inspect(str(junk))
    cut = tmp_path / "cut.raucb"
    cut.write_bytes(open(good, "rb").read()[:-5000])
    with pytest.raises(RaucError):
        client.inspect(str(cut))
    flipped = bytearray(open(good, "rb").read())
    flipped[1000] ^= 0xFF
    (tmp_path / "flip.raucb").write_bytes(bytes(flipped))
    # FINDING (real RAUC 1.13): a payload byte changed after signing is NOT detected by InspectBundle - it verifies the signed manifest
    # and the verity root hash only. dm-verity rejects the changed block when the installation reads it (needs a device: G2 P5-*).
    assert client.inspect(str(tmp_path / "flip.raucb"))["update"]["version"] == "0.4.0"


# ---------------------------------------------------------------- the update manager with real RAUC
def manager(tmp_path, rauc, client, **kw):
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    (data / "settings.json").write_text('{"schema_version": 1, "device_name": "Wohnzimmer"}')
    reboots = []
    m = UpdateManager(str(data), client, trust={"mode": "own", "certificates": [], "official_source": None}, version="0.3.0",
                      reboot=lambda: reboots.append(1), current_boot=lambda: "boot-1", log=lambda m: None, monitor_s=0.1, **kw)
    m.reboots = reboots
    return m


def upload(m, path):
    dst = m.begin_upload(os.path.getsize(path))
    with open(path, "rb") as src, open(dst, "wb") as out:
        out.write(src.read())
    return m.finish_upload(os.path.basename(path))


def test_manager_stages_only_signed_bundles_for_this_board(tmp_path, rauc, client, keys):
    m = manager(tmp_path, rauc, client)
    good, _ = build_bundle(tmp_path, signer=keys["dev"], name="good")
    assert upload(m, good)["version"] == "0.4.0" and m.info()["phase"] == "staged"
    from btbridge.ota.manager import UpdateError
    bundles = [(build_bundle(tmp_path, signer=keys["other"], name="other")[0], "signature_invalid"),
               (raw_bundle(tmp_path, keys["mail"], "mail"), "signature_invalid"),
               (build_bundle(tmp_path, signer=keys["dev"], compatible="hifi-bridge-rpi4", name="board")[0], "wrong_compatible")]
    for b, code in bundles:
        with pytest.raises(UpdateError) as e:
            upload(m, b)
        assert e.value.code == code and not os.path.exists(m.bundle_path)
        assert m.info()["error"]["code"] == code


def test_install_failure_keeps_the_device_on_its_slot(tmp_path, rauc, client, keys):
    """The real installation stops at RAUC's mount step here (no loop device). It must end as a clean error: Completed(1), LastError
    shown, bundle kept for a retry, U-Boot environment and primary slot unchanged, a data snapshot taken before."""
    m = manager(tmp_path, rauc, client)
    good, _ = build_bundle(tmp_path, signer=keys["dev"], name="good")
    upload(m, good)
    env_before = rauc.env()
    m.install()
    wait(lambda: m.state.read()["phase"] != "installing")
    i = m.info()
    assert i["phase"] == "staged" and i["error"]["code"] == "install_failed", i
    assert "loop" in i["error"]["detail"] or "mount" in i["error"]["detail"].lower()
    assert rauc.env()["BOOT_ORDER"] == env_before["BOOT_ORDER"] and client.primary() == "rootfs.0"
    assert i["rauc"]["operation"] == "idle" and os.path.exists(m.bundle_path) and i["snapshot"]["version"] == "0.3.0"
    assert "Verified" in rauc.logtext() or "verified" in rauc.logtext()                        # the signature was checked again


def test_rauc_restart_is_survived(tmp_path, rauc, client):
    m = manager(tmp_path, rauc, client)
    assert m.info()["rauc"]["available"]
    rauc.stop()
    assert not m.info()["rauc"]["available"]
    with pytest.raises(RaucUnavailable):
        client.properties()
    rauc.start()
    assert m.info()["rauc"]["available"] and m.info()["rauc"]["booted"] == "A"


def test_manual_slot_switch_and_cancel_with_real_rauc(tmp_path, rauc, client):
    client.mark("good", "other")                                       # pretend B holds an earlier, good release
    data = tmp_path / "data"
    m = manager(tmp_path, rauc, client)
    (data / "ota").mkdir(exist_ok=True)
    (data / "ota" / "slots.json").write_text(json.dumps({"B": {"version": "0.2.0"}}))
    assert m.info()["can_rollback"]
    m.rollback()
    assert client.primary() == "rootfs.1" and rauc.env()["BOOT_ORDER"].split()[0] == "B"
    m.request_reboot()
    assert m.reboots == [1]


def test_health_check_marks_the_booted_slot_good(tmp_path, rauc, client):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Hz(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200); self.send_header("Content-Length", "16"); self.end_headers(); self.wfile.write(b'{"status": "ok"}')
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Hz)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        sh("fw_setenv", "BOOT_A_LEFT", "1")                              # U-Boot decremented the counter during this boot
        code = H.run(str(tmp_path / "hdata"), client, healthz=f"http://127.0.0.1:{srv.server_address[1]}/healthz", data_mount="/",
                     timeout=5, version="0.3.0", current_boot=lambda: "b", log=lambda m: None)
        assert code == 0 and rauc.env()["BOOT_A_LEFT"] == "3"
    finally:
        srv.shutdown(); srv.server_close()


# ---------------------------------------------------------------- the image's D-Bus policy
CLIENT = """import sys
from btbridge.ota.rauc import RaucClient, RaucUnavailable
c = RaucClient(bus=sys.argv[1], timeout=5)
try:
    print(c.properties()["BootSlot"], c.mark("good", "booted")[0])
except RaucUnavailable as e:
    print("DENIED", e)
"""


def as_user(uid, rauc, code=CLIENT):
    return subprocess.run(["setpriv", f"--reuid={uid}", f"--regid={uid}", "--clear-groups", "python3", "-c", code, rauc.address],
                          capture_output=True, text=True, env={**os.environ, "HOME": "/tmp"}, timeout=30)


def test_policy_allows_only_root_and_the_bridge_user(rauc):
    assert as_user(990, rauc).stdout.split() == ["A", "rootfs.0"]
    denied = as_user(1234, rauc).stdout
    assert denied.startswith("DENIED") and "AccessDenied" in denied     # upstream RAUC alone would allow this user
    setter = """import sys
from jeepney import DBusAddress, new_method_call, MessageType
from jeepney.io.blocking import open_dbus_connection
c = open_dbus_connection(bus=sys.argv[1])
m = new_method_call(DBusAddress("/", bus_name="de.pengutronix.rauc", interface="org.freedesktop.DBus.Properties"), "Set", "ssv",
                    ("de.pengutronix.rauc.Installer", "Operation", ("s", "x")))
r = c.send_and_get_reply(m, timeout=5)
print(r.header.message_type == MessageType.error, r.body)
"""
    out = as_user(990, rauc, setter).stdout
    assert out.startswith("True") and "Rejected send message" in out    # the bridge user can read properties, not set them


def test_upstream_policy_alone_would_let_anyone_in(tmp_path, keys):
    """Control experiment: without our policy file, uid 1234 reaches RAUC - so the file above is what protects it."""
    import conftest
    orig = conftest.BOARD
    try:
        fake_board = tmp_path / "board"
        (fake_board / "overlay" / "etc" / "dbus-1" / "system.d").mkdir(parents=True)
        (fake_board / "rauc").mkdir()
        (fake_board / "rauc" / "system.conf.in").write_text(open(os.path.join(orig, "rauc", "system.conf.in")).read())
        shutil.copytree(os.path.join(orig, "uboot"), fake_board / "uboot")
        conftest.BOARD = str(fake_board)
        d = tmp_path / "r2"
        d.mkdir()
        r = conftest.RealRauc(d, keys["dev"]["ca"])
        try:
            assert as_user(1234, r).stdout.split() == ["A", "rootfs.0"]
        finally:
            r.close()
    finally:
        conftest.BOARD = orig


# ---------------------------------------------------------------- the bundle script
def test_bundle_script_is_reproducible_and_creates_development_keys_once(tmp_path, client):
    keys_dir = str(tmp_path / "devkeys")
    b1, out1 = build_bundle(tmp_path, dev_keys=keys_dir, name="one")
    assert "creating DEVELOPMENT update keys" in out1 and oct(os.stat(keys_dir).st_mode & 0o777) == "0o700"
    assert all(oct(os.stat(os.path.join(keys_dir, f)).st_mode & 0o777) == "0o600" for f in ("ca.key", "signing.key"))
    assert "DEVELOPMENT" in sh("openssl", "x509", "-in", os.path.join(keys_dir, "ca.pem"), "-noout", "-subject").stdout
    b2, out2 = build_bundle(tmp_path, dev_keys=keys_dir, name="two")
    assert "creating DEVELOPMENT" not in out2
    pick = lambda out: {k.strip(): v.strip() for k, v in (line.split(":", 1) for line in out.splitlines() if ":" in line and "os_bundle" not in line)}
    i1, i2 = pick(out1), pick(out2)
    assert i1["image sha256"] == i2["image sha256"] and i1["squashfs hash"] == i2["squashfs hash"]           # the content is reproducible
    assert i1["manifest hash"] != i2["manifest hash"]                   # the signed manifest carries the random verity salt and root hash
    assert i1["compatible"] == COMPAT and i1["version"] == "0.4.0"
    assert open(b1, "rb").read() != open(b2, "rb").read()               # the CMS signature carries its signing time
    assert os.path.basename(b1) == f"hifi-bridge-{COMPAT}-0.4.0.raucb"
    b3, out3 = build_bundle(tmp_path, dev_keys=keys_dir, name="three", epoch="1700000999")
    assert pick(out3)["squashfs hash"] != i1["squashfs hash"]           # the time stamp is part of the content
    assert i1["payload hash"] != i2["payload hash"]                     # RAUC salts the dm-verity hash tree randomly per bundle


def test_bundle_script_refuses_a_certificate_without_codesigning(tmp_path, keys):
    with pytest.raises(AssertionError) as e:
        build_bundle(tmp_path, signer=keys["mail"], name="mail")
    assert "no codeSigning purpose" in str(e.value)
