"""UNIT/INTEGRATION of the privileged write helper with SIMULATED cards only: the strict request protocol, the post-elevation checks
(re-enumeration, identity, safety policy), write/verify/provision through the real server and client over pipes and over the token-
authenticated loopback connect-back, cancel and failure paths (the card is invalidated), and the launch commands (pkexec / osascript / UAC)
built but never executed. No process is started and no device is opened (the ``no_subprocess`` guard is active in every test)."""
import base64
import io
import json
import os
import socket
import threading

import pytest

import flashfixtures as F
from btbridge import provisioning as P
from hififlash import disks as D
from hififlash import provision as FP
from hififlash import writer as W
from hififlash.helper import client as CL
from hififlash.helper import protocol as PR
from hififlash.helper import server as S
from hififlash.helper.__main__ import main as helper_main, read_token
from hififlash.platforms import simulated as SIM

pytestmark = pytest.mark.usefixtures("no_subprocess")
GB = 1000 ** 3


@pytest.fixture
def setup(tmp_path):
    cards = tmp_path / "cards"
    sd = SIM.create(str(cards), "sdcard", 2 * GB, model="SD Card Reader", serial="SD1")
    im = F.image(tmp_path)
    prov = P.render(FP.new_document("DE", device_name="Wohnzimmer")[0])
    adapter = SIM.SimulatedAdapter(str(cards))
    disk = next(d for d in adapter.list_disks() if d.path == sd)
    return type("S", (), dict(cards=cards, sd=sd, im=im, prov=prov, adapter=adapter, disk=disk, tmp=tmp_path))


def request(s, **over):
    r = PR.write_request(s.disk, s.im["path"], s.im["manifest"], s.prov)
    r.update(over)
    return r


def run_inprocess(s, req, cancel=None, progress=None, adapter=None):
    events = []
    ch = CL.inprocess(adapter or s.adapter, "simulated")
    try:
        return CL.run_session(ch, req, progress=progress or (lambda *a: events.append(a)), cancel=cancel), events
    except CL.HelperFailed as e:
        return e, events


# ------------------------------------------------------------------ protocol
def test_protocol_encoding_limits():
    assert PR.decode(PR.encode({"a": 1})) == {"a": 1}
    with pytest.raises(PR.ProtocolError, match="too large"):
        PR.encode({"x": "a" * PR.MAX_LINE})
    with pytest.raises(PR.ProtocolError, match="too large"):
        PR.decode(b"x" * (PR.MAX_LINE + 1))
    for bad in (b"\xff\n", b"nope\n", b"[1]\n"):
        with pytest.raises(PR.ProtocolError):
            PR.decode(bad)
    assert PR.read_message(io.BytesIO(b"")) is None
    with pytest.raises(PR.ProtocolError, match="truncated"):
        PR.read_message(io.BytesIO(b'{"a":1}'))
    with pytest.raises(PR.ProtocolError):
        PR.read_message(io.BytesIO(b"{" + b" " * PR.MAX_LINE + b"}\n"))


def test_request_validation_accepts_only_whole_disks_and_valid_payloads(setup):
    s = setup
    ok = PR.validate_request(request(s), "simulated")
    assert ok.provisioning == s.prov and ok.disk.path == s.sd and not ok.allow_large
    assert PR.validate_request(request(s, provisioning=None), "simulated").provisioning is None
    for plat, good, bad in [("linux", "/dev/sdb", ["/dev/sdb1", "/dev/mmcblk0p1", "/dev/loop0", "/dev/nvme0n1", "/dev/../dev/sda", "/tmp/x"]),
                            ("darwin", "/dev/disk4", ["/dev/disk4s1", "/dev/rdisk4", "/dev/sdb"]),
                            ("windows", "\\\\.\\PhysicalDrive3", ["\\\\.\\C:", "C:\\x", "\\\\.\\PhysicalDrive"])]:
        disk = {"path": good, "name": "x", "size": 2 * GB, "serial": "S"}
        assert PR.validate_request(request(s, disk=disk), plat).disk.path == good
        for b in bad:
            with pytest.raises(PR.ProtocolError, match="whole disk"):
                PR.validate_request(request(s, disk={**disk, "path": b}), plat)
    with pytest.raises(PR.ProtocolError, match="whole disk"):
        PR.validate_request(request(s), "plan9")
    slot = s.im["manifest"]["slot"]["size"]
    cases = [
        ({"extra": 1}, "unexpected"),
        ({"v": 2}, "version"), ({"op": "erase"}, "operation"),
        ({"disk": "x"}, "disk: unexpected"), ({"disk": {"path": s.sd}}, "disk: unexpected"),
        ({"disk": {"path": s.sd, "name": "", "size": 1, "serial": ""}}, "invalid name"),
        ({"disk": {"path": s.sd, "name": "x", "size": True, "serial": ""}}, "invalid name"),
        ({"disk": {"path": s.sd, "name": "x", "size": 0, "serial": ""}}, "invalid name"),
        ({"disk": {"path": s.sd, "name": "x", "size": 5, "serial": 7}}, "invalid name"),
        ({"allow_large": 1}, "boolean"),
        ({"image": "relative.img"}, "absolute"), ({"image": 5}, "absolute"), ({"image": "/a\x00b"}, "absolute"),
        ({"image": "/dev/sda"}, "device"), ({"image": "/proc/self/mem"}, "device"),
        ({"manifest": {"format": "x"}}, "manifest"), ({"manifest": None}, "manifest"),
        ({"provisioning": "!!!"}, "slot-sized"), ({"provisioning": 5}, "slot-sized"),
        ({"provisioning": base64.b64encode(b"x").decode()}, "slot-sized"),
        ({"provisioning": base64.b64encode(P.placeholder(slot)).decode()}, "empty slot"),
        ({"provisioning": base64.b64encode(b'{"format":"x"}'.ljust(slot)).decode()}, "invalid document"),
    ]
    for over, msg in cases:
        with pytest.raises(PR.ProtocolError, match=msg):
            PR.validate_request(request(s, **over), "simulated")
    win = {"path": "\\\\.\\PhysicalDrive3", "name": "PhysicalDrive3", "size": 2 * GB, "serial": "S"}
    assert PR.validate_request(request(s, disk=win, image="C:\\Users\\o\\hifi.img.xz"), "windows").image.startswith("C:")
    for dev in ("\\\\.\\PhysicalDrive0", "\\\\?\\Volume{x}\\a.img", "//./C:"):
        with pytest.raises(PR.ProtocolError, match="device"):
            PR.validate_request(request(s, disk=win, image=dev), "windows")
    with pytest.raises(PR.ProtocolError, match="absolute"):
        PR.validate_request(request(s, disk=win, image="hifi.img"), "windows")


# ------------------------------------------------------------------ server + client end to end
def test_write_verify_provision_through_the_helper(setup):
    s = setup
    result, events = run_inprocess(s, request(s))
    assert result == {"bytes": s.im["manifest"]["size"], "sha256": s.im["manifest"]["sha256"], "provisioned": True}
    with open(s.sd, "rb") as fh:
        raw = fh.read(s.im["manifest"]["size"])
    off = s.im["manifest"]["slot"]["offset"]
    assert raw[off:off + P.SLOT_SIZE] == s.prov and raw[:off] == s.im["data"][:off]
    stages = [e[0] for e in events]
    assert stages[0] == "prepare" and "write" in stages and "verify" in stages
    assert events[-1][1] == events[-1][2]
    assert s.adapter.calls == [("prepare", s.sd), ("open", s.sd), ("finish", s.sd)]


def test_compressed_image_and_no_provisioning(setup, tmp_path):
    s = setup
    xz = F.image(tmp_path / "..", version="0.3.9", compress=True)
    result, _ = run_inprocess(s, request(s, image=xz["path"], manifest=xz["manifest"], provisioning=None))
    assert result["provisioned"] is False


@pytest.mark.parametrize("change,code", [
    ("removed", "disk_gone"), ("swapped", "disk_changed"), ("system", "disk_refused"), ("internal_disk", "disk_refused"),
    ("busy", "unmount_failed"), ("io_error", "write_failed"), ("fake_capacity", "write_failed"), ("missing_image", "image_unreadable"),
    ("image_is_dir", "image_unreadable"), ("tampered", "write_failed")])
def test_post_elevation_checks_and_failures(setup, change, code):
    s = setup
    req = request(s)
    meta_path = s.sd + ".json"
    meta = json.load(open(meta_path))
    if change == "removed":
        os.remove(s.sd)
    elif change == "swapped":
        meta["serial"] = "OTHER"
    elif change == "system":
        meta["system"] = True
    elif change == "internal_disk":
        meta.update(removable=False, transport="sata")
    elif change == "busy":
        meta["busy"] = True
    elif change == "io_error":
        meta["fail_after"] = 3 * F.MB
    elif change == "fake_capacity":
        meta["lie_above"] = 3 * F.MB
    elif change == "missing_image":
        req["image"] = str(s.tmp / "missing.img")
    elif change == "image_is_dir":
        req["image"] = str(s.tmp)
    elif change == "tampered":
        req["manifest"] = {**s.im["manifest"], "sha256": "0" * 64}
    json.dump(meta, open(meta_path, "w"))
    before = open(s.sd, "rb").read(W.INVALIDATE_BYTES) if os.path.exists(s.sd) else None
    with open(s.sd, "r+b") if os.path.exists(s.sd) else open(os.devnull, "rb") as fh:
        if before is not None:
            fh.write(b"\x55" * W.INVALIDATE_BYTES)
    err, _ = run_inprocess(s, req)
    assert isinstance(err, CL.HelperFailed) and err.code == code, err
    if code in ("disk_refused",):
        assert err.reasons and "nothing was written" in err.message
    if code == "write_failed":
        assert "made unbootable" in err.message
        assert open(s.sd, "rb").read(W.INVALIDATE_BYTES) == bytes(W.INVALIDATE_BYTES)
    if code in ("disk_changed", "disk_refused", "unmount_failed", "image_unreadable"):
        assert open(s.sd, "rb").read(W.INVALIDATE_BYTES) == b"\x55" * W.INVALIDATE_BYTES     # untouched
    if code == "unmount_failed":
        assert s.adapter.calls[-1][0] == "finish"                                             # locks released after a failed prepare


def test_large_disk_needs_the_explicit_flag(setup):
    s = setup
    big = SIM.create(str(s.cards), "bigdisk", 300 * GB, serial="B1", transport="usb")
    disk = next(d for d in s.adapter.list_disks() if d.path == big)
    req = PR.write_request(disk, s.im["path"], s.im["manifest"], None)
    err, _ = run_inprocess(s, req)
    assert err.code == "disk_refused" and err.reasons == ["unusually_large_confirm_explicitly"]
    req["allow_large"] = True
    assert run_inprocess(s, req)[0]["bytes"] == s.im["manifest"]["size"]


def test_cancel_during_write_invalidates_the_card(setup):
    s = setup
    cancel = threading.Event()
    err, _ = run_inprocess(s, request(s), cancel=cancel, progress=lambda st, d, t: cancel.set() if st == "write" else None)
    assert isinstance(err, CL.HelperFailed) and err.code == "cancelled" and "unbootable" in err.message
    assert open(s.sd, "rb").read(W.INVALIDATE_BYTES) == bytes(W.INVALIDATE_BYTES)


def test_gui_disappearing_cancels_the_write(setup):
    """EOF from the GUI side (the GUI crashed) stops the helper; the card is invalidated."""
    s = setup
    rin, gui_w = os.pipe()
    gui_r, rout = os.pipe()
    hin, hout = os.fdopen(rin, "rb"), os.fdopen(rout, "wb")
    gw, gr = os.fdopen(gui_w, "wb"), os.fdopen(gui_r, "rb")
    rc = {}
    t = threading.Thread(target=lambda: rc.setdefault("rc", S.serve(hin, hout, s.adapter, "simulated")))
    t.start()
    assert PR.read_message(gr)["event"] == "ready"
    gw.write(PR.encode(request(s))); gw.flush()
    while PR.read_message(gr).get("stage") != "write":
        pass
    gw.close()
    t.join(30)
    hout.close(); gr.close(); hin.close()
    assert rc["rc"] == 1 and open(s.sd, "rb").read(W.INVALIDATE_BYTES) == bytes(W.INVALIDATE_BYTES)


def test_bad_request_internal_error_and_eof_before_request(setup):
    s = setup
    err, _ = run_inprocess(s, {"v": 1})
    assert err.code == "bad_request"
    out = io.BytesIO()
    assert S.serve(io.BytesIO(b"not json\n"), out, s.adapter, "simulated") == 2
    assert S.serve(io.BytesIO(b""), io.BytesIO(), s.adapter, "simulated") == 2

    class Broken(SIM.SimulatedAdapter):
        def list_disks(self):
            raise RuntimeError("enumeration crashed")
    err, _ = run_inprocess(s, request(s), adapter=Broken(str(s.cards)))
    assert err.code == "internal" and "enumeration crashed" in err.message

    class NoOpen(SIM.SimulatedAdapter):
        def open(self, disk):
            raise PermissionError(13, "exclusive open refused")
    a = NoOpen(str(s.cards))
    err, _ = run_inprocess(s, request(s), adapter=a)
    assert err.code == "open_failed" and a.calls[-1][0] == "finish"

    class HelperErr(SIM.SimulatedAdapter):
        def prepare(self, disk):
            raise S.HelperError("unmount_failed", "custom")
    err, _ = run_inprocess(s, request(s), adapter=HelperErr(str(s.cards)))
    assert (err.code, err.message) == ("unmount_failed", "custom")


def test_reporter_throttles_and_survives_a_closed_pipe():
    t = [0.0]
    out = io.BytesIO()
    r = S.Reporter(out, interval=1.0, clock=lambda: t[0])
    r.progress("write", 1, 10); r.progress("write", 2, 10); t[0] = 2.0; r.progress("write", 3, 10); r.progress("write", 10, 10)
    lines = [json.loads(x)["done"] for x in out.getvalue().splitlines()]
    assert lines == [1, 3, 10]
    out.close()
    r.send({"event": "x"})                    # no exception: the GUI is gone


def test_watch_cancel_variants():
    for data in (b'{"op":"cancel"}\n', b"", b"garbage\n", b'{"op":"other"}\n'):
        ev = threading.Event()
        S._watch_cancel(io.BytesIO(data), ev)
        assert ev.is_set()


# ------------------------------------------------------------------ client failure modes
def scripted(lines: bytes):
    sent = io.BytesIO()
    closed = []
    return CL.Channel(io.BytesIO(lines), sent, lambda: closed.append(1)), sent, closed


def test_client_failure_modes():
    for lines, code in [(b"", "elevation_refused"), (b"junk\n", "protocol"), (b'{"event":"ready","v":9}\n', "protocol"),
                        (b'{"event":"ready","v":1}\n', "helper_died"), (b'{"event":"ready","v":1}\nzzz\n', "protocol"),
                        (b'{"event":"ready","v":1}\n{"event":"error","code":"disk_gone","message":"m"}\n', "disk_gone")]:
        ch, sent, closed = scripted(lines)
        with pytest.raises(CL.HelperFailed) as e:
            CL.run_session(ch, {"op": "write"})
        assert e.value.code == code and closed == [1]
    ch, sent, _ = scripted(b'{"event":"ready","v":1}\n{"event":"unknown"}\n{"event":"done","result":{"bytes":1}}\n')
    assert CL.run_session(ch, {"op": "write"}) == {"bytes": 1}
    assert json.loads(sent.getvalue().splitlines()[0]) == {"op": "write"}


def test_client_cancel_is_sent_even_if_the_pipe_broke():
    class Pipe(io.BytesIO):
        def write(self, b):
            if b"cancel" in b:
                raise BrokenPipeError()
            return super().write(b)
    gate = threading.Event()

    class Slow(io.BytesIO):
        def readline(self, n=-1):
            line = super().readline(n)
            if b"done" in line:
                gate.wait(5)
            return line
    cancel = threading.Event(); cancel.set()
    ch = CL.Channel(Slow(b'{"event":"ready","v":1}\n{"event":"done","result":{}}\n'), Pipe(), lambda: None)
    threading.Timer(0.3, gate.set).start()
    assert CL.run_session(ch, {}, cancel=cancel) == {}


# ------------------------------------------------------------------ helper entry point, stdio and connect-back
def test_helper_main_version_and_stdio(setup, capsys):
    s = setup
    assert helper_main(["--version"]) == 0 and "protocol 1" in capsys.readouterr().out
    out = io.BytesIO()
    r, w = os.pipe()                                   # stdin stays open during the write (EOF would mean "GUI gone" = cancel)
    os.write(w, PR.encode(request(s)))
    stdin = os.fdopen(r, "rb")
    rc = helper_main(["--stdio", "--simulate", str(s.cards)], stdin=stdin, stdout=out)
    os.close(w)            # EOF ends the helper's watcher thread; it owns the last reference to ``stdin`` (closing it here could deadlock)
    del stdin
    msgs = [json.loads(x) for x in out.getvalue().splitlines()]
    assert rc == 0 and msgs[0]["event"] == "ready" and msgs[-1]["event"] == "done"
    out = io.BytesIO()
    assert helper_main(["--stdio", "--simulate", str(s.cards)], stdin=io.BytesIO(PR.encode(request(s))), stdout=out) == 1
    assert json.loads(out.getvalue().splitlines()[-1])["code"] == "cancelled"
    with pytest.raises(SystemExit):
        helper_main(["--connect", "5"])
    with pytest.raises(SystemExit):
        helper_main(["--connect", "0", "--token-file", "x"])


def test_token_file_validation(tmp_path):
    f = tmp_path / "t"
    f.write_text("a" * 64 + "\n")
    assert read_token(str(f)) == "a" * 64
    for bad in ("short", "A" * 64, "g" * 64):
        f.write_text(bad)
        with pytest.raises(ValueError):
            read_token(str(f))


def test_connect_back_end_to_end_with_an_impostor_first(setup):
    s = setup

    def launch(port, token_file):
        assert oct(os.stat(token_file).st_mode)[-3:] == "600" and oct(os.stat(os.path.dirname(token_file)).st_mode)[-3:] == "700"
        impostor = socket.create_connection(("127.0.0.1", port))
        impostor.sendall(PR.encode({"hello": "0" * 64}))
        garbage = socket.create_connection(("127.0.0.1", port))
        garbage.sendall(b"\xff\xfe\n")

        def helper():
            helper_main(["--connect", str(port), "--token-file", token_file, "--simulate", str(s.cards)])
            impostor.close(); garbage.close()
        threading.Thread(target=helper, daemon=True).start()
        return None
    ch = CL.connect_back(launch, timeout=20)
    result = CL.run_session(ch, request(s))
    assert result["provisioned"] is True


def test_connect_back_refusals():
    with pytest.raises(CL.HelperFailed) as e:
        CL.connect_back(lambda port, tf: None, timeout=0.3)
    assert e.value.code == "elevation_refused"

    class Exited:
        def poll(self):
            return 1
    with pytest.raises(CL.HelperFailed, match="did not connect"):
        CL.connect_back(lambda port, tf: Exited(), timeout=30)

    def declined(port, tf):
        raise OSError(1223, "The operation was canceled by the user")
    with pytest.raises(CL.HelperFailed, match="refused"):
        CL.connect_back(declined)


def test_stdio_channel_with_an_injected_process(setup):
    s = setup
    seen = {}

    class FakeProc:
        def __init__(self, argv, **kw):
            seen["argv"] = argv
            a_r, a_w = os.pipe(); b_r, b_w = os.pipe()
            self.stdin, self.stdout = os.fdopen(a_w, "wb"), os.fdopen(b_r, "rb")
            hin, hout = os.fdopen(a_r, "rb"), os.fdopen(b_w, "wb")
            self.t = threading.Thread(target=lambda: (S.serve(hin, hout, s.adapter, "simulated"), hout.close(), hin.close()))
            self.t.start()

        def wait(self, timeout):
            self.t.join(timeout)
            if seen.get("hang"):
                import subprocess
                raise subprocess.TimeoutExpired("x", timeout)

        def kill(self):
            seen["killed"] = True
    ch = CL.stdio(["pkexec", "/opt/helper", "--stdio"], popen=FakeProc)
    assert CL.run_session(ch, request(s))["provisioned"] and seen["argv"][0] == "pkexec"
    seen["hang"] = True
    ch = CL.stdio(["x"], popen=FakeProc)
    CL.run_session(ch, request(s))
    assert seen["killed"]


def test_launch_commands_are_built_but_never_run(tmp_path):
    assert CL.helper_command(frozen=True, executable="/opt/hifi/hifi-bridge-flash", platform="linux") == ["/opt/hifi/hififlash-helper"]
    assert CL.helper_command(frozen=True, executable="C:/P/hifi.exe", platform="win32")[0].endswith("hififlash-helper.exe")
    assert CL.helper_command(frozen=False, executable="/usr/bin/python3") == ["/usr/bin/python3", "-m", "hififlash.helper"]
    assert CL.helper_command()[1:] == ["-m", "hififlash.helper"]
    assert CL.linux_command(["/opt/h"]) == ["pkexec", "/opt/h", "--stdio"]
    calls = []
    proc = CL.macos_launch(["/Applications/Hi-Fi Bridge.app/Contents/MacOS/hififlash-helper"],
                           popen=lambda argv, **kw: calls.append(argv) or "proc")(5555, '/tmp/x"y/token')
    assert proc == "proc" and calls[0][:2] == ["osascript", "-e"]
    script = calls[0][2]
    assert script.startswith('do shell script "') and script.endswith('" with administrator privileges')
    assert "--connect 5555" in script and "'/Applications/Hi-Fi Bridge.app/Contents/MacOS/hififlash-helper'" in script
    assert '\\"' in script                                          # the quote inside the path is escaped for AppleScript
    assert CL.applescript_string('a\\b"c') == '"a\\\\b\\"c"'
    got = []
    CL.windows_launch(["C:\\Program Files\\Hi-Fi Bridge\\hififlash-helper.exe"], runas=lambda exe, params: got.append((exe, params)))(7, "C:\\T\\tok")
    assert got == [("C:\\Program Files\\Hi-Fi Bridge\\hififlash-helper.exe", "--connect 7 --token-file C:\\T\\tok")]


def test_channel_factory_per_platform(setup, monkeypatch):
    s = setup
    f = CL.channel_factory("linux", simulate=str(s.cards))
    assert CL.run_session(f(), request(s))["provisioned"]
    started = []
    monkeypatch.setattr(CL, "stdio", lambda argv, popen: started.append(argv) or "ch")
    assert CL.channel_factory("linux")() == "ch" and started[0][0] == "pkexec"
    monkeypatch.setattr(CL, "connect_back", lambda launch: launch)
    assert callable(CL.channel_factory("darwin")()) and callable(CL.channel_factory("win32")())
    with pytest.raises(ValueError):
        CL.channel_factory("plan9")


def test_windows_launch_without_injection_uses_the_native_binding(monkeypatch):
    import sys
    import types
    fake = types.ModuleType("hififlash.platforms.win32api")
    fake.shell_execute_runas = lambda exe, params: ("native", exe, params)
    monkeypatch.setitem(sys.modules, "hififlash.platforms.win32api", fake)
    assert CL.windows_launch(["h.exe"])(1, "t") == ("native", "h.exe", "--connect 1 --token-file t")
