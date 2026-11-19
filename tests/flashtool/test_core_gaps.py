"""UNIT: remaining edge cases of the core - assets with a non-http URL, resuming without an ETag, a provisioning slot that does not read
back, Windows cards without volumes, the plain-text owner sheet without a hotspot, and the module entry points."""
import json
import runpy
import sys

import pytest

import flashfixtures as F
from btbridge import provisioning as P
from hififlash import catalog as C
from hififlash import provision as FP
from hififlash import writer as W
from hififlash.platforms import windows as WIN

from test_platforms import FakeKernel32, card_file, windows_listing

pytestmark = pytest.mark.usefixtures("no_subprocess")


def test_assets_with_other_url_schemes_are_ignored_not_fatal(tmp_path):
    im = F.image(tmp_path)
    s = F.ReleaseServer()
    try:
        s.publish("v1", [im], extra_assets=[{"name": "x.img.manifest.json", "size": 1, "browser_download_url": "file:///etc/passwd"},
                                            {"name": "x.img", "size": 1, "browser_download_url": "ftp://x/y"}])
        assert [e.image.name for e in C.list_entries(s.base + "/releases")] == [im["name"]]
    finally:
        s.close()


def test_resume_without_an_etag_sends_no_if_range(tmp_path):
    im = F.image(tmp_path, size=2 * F.MB)
    s = F.ReleaseServer()
    try:
        s.publish("v1", [im])
        entry = C.list_entries(s.base + "/releases")[0]
        dl = C.Downloader(str(tmp_path / "cache"), timeout=5)
        part = dl.path_for(entry.image.name) + ".part"
        (tmp_path / "cache").mkdir()
        with open(part, "wb") as fh:
            fh.write(im["data"][:1000])
        with open(part + ".json", "w") as fh:
            json.dump({"url": entry.image.url}, fh)
        assert open(dl.fetch(entry.image), "rb").read() == im["data"]
        assert s.requests[-1]["range"] == "bytes=1000-" and s.requests[-1]["if_range"] is None
    finally:
        s.close()


def test_provisioning_that_does_not_read_back_invalidates_the_card(tmp_path):
    im = F.image(tmp_path)
    p = tmp_path / "card.bin"
    p.write_bytes(bytes(16 * F.MB))

    class Forgetful(W.FileTarget):
        def read_at(self, offset, n):
            data = super().read_at(offset, n)
            return bytes(n) if offset == im["manifest"]["slot"]["offset"] else data
    t = Forgetful(str(p))
    with pytest.raises(W.WriteError, match="provisioning data could not be verified.*unbootable"):
        W.write_card(open(im["path"], "rb"), im["manifest"], t, t.identity(), P.render(FP.new_document("DE")[0]))
    assert p.read_bytes()[:W.INVALIDATE_BYTES] == bytes(W.INVALIDATE_BYTES)


def test_windows_card_without_volumes_and_idempotent_close(tmp_path):
    dev = card_file(tmp_path)
    k = FakeKernel32(dev)
    a = WIN.WindowsAdapter(runner=lambda argv: windows_listing(), k32=k, sleep=lambda s: None)
    disk = [d for d in a.list_disks() if d.name == "PhysicalDrive3"][0]
    bare = type(disk)(**{**disk.__dict__, "mounts": ()})
    a.prepare(bare)
    assert not [e for e in k.log if e[0] == "CreateFile"]                          # nothing to lock
    io_ = WIN.WindowsDeviceIO(k, "\\\\.\\PhysicalDrive3")
    io_.close()
    io_.close()
    a.finish(bare)


def test_plain_owner_sheet_without_hotspot():
    doc, _ = FP.new_document("DE", hotspot=False)
    for lang in ("en", "de"):
        sheet = FP.owner_sheet(doc, lang)
        assert doc["setup_code"] in sheet and "Wi-Fi" not in sheet and "WLAN" not in sheet


@pytest.mark.parametrize("module,argv,code", [("hififlash.helper", ["--version"], 0), ("hififlash.manifest", [], 2), ("hififlash", [], 2)])
def test_module_entry_points(monkeypatch, module, argv, code):
    import os

    def fake_exit(rc):                                         # the helper ends with os._exit (no finalisation), see run_and_exit
        raise SystemExit(rc)
    monkeypatch.setattr(os, "_exit", fake_exit)
    monkeypatch.setattr(sys, "argv", [module] + argv)
    with pytest.raises(SystemExit) as e:
        runpy.run_module(module, run_name="__main__", alter_sys=True)
    assert e.value.code == code


def test_trust_calls_accept_only_http_and_https():
    from hififlash import trust as FT
    for bad in ("file:///etc/passwd", "ftp://x", "hifi-bridge.local", None):
        with pytest.raises(ValueError, match="must start with"):
            FT.fetch_verified_ca(bad, b"k" * 32, opener=lambda *a, **k: (_ for _ in ()).throw(AssertionError("opened")))
    with pytest.raises(ValueError, match="https://"):
        FT.check_https("http://hifi-bridge.local", b"")
