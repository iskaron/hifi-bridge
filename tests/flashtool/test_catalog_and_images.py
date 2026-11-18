"""UNIT/INTEGRATION (real HTTP on loopback, never the internet): release catalogue in the GitHub Releases format, streamed and resumable
downloads with a cache, cancel, clear source errors (private repository = 404), and the cryptographic image check (signature by a trusted
publisher, board, size and SHA-256 of raw and compressed images)."""
import gzip
import os
import threading
import urllib.error

import pytest

import flashfixtures as F
from hififlash import boards as B
from hififlash import catalog as C
from hififlash import images as I
from hififlash.keystore import KeyStore

pytestmark = pytest.mark.usefixtures("no_subprocess")
RPI3 = B.by_id("rpi3")


@pytest.fixture
def server():
    s = F.ReleaseServer()
    yield s
    s.close()


@pytest.fixture
def keys(tmp_path):
    official, pub = F.keypair()
    d = tmp_path / "official"; d.mkdir()
    (d / "hifi-bridge.pem").write_bytes(pub)
    return official, KeyStore(str(tmp_path / "custom"), official_dir=str(d))


def test_catalogue_lists_signed_images_and_skips_incomplete_assets(server, keys, tmp_path):
    key, store = keys
    a = F.image(tmp_path, "hifi-bridge-rpi3", "0.3.0", compress=True, key=key)
    b = F.image(tmp_path, "hifi-bridge-rpi4", "0.3.0", key=key)
    c = F.image(tmp_path, "hifi-bridge-rpi3", "0.4.0-rc1", key=None)                 # unsigned pre-release
    server.publish("v0.3.0", [a, b], extra_assets=[
        {"name": "../evil.manifest.json", "size": 1, "browser_download_url": server.url("x")},     # unsafe name: ignored
        {"name": "orphan.img.manifest.json", "size": 1, "browser_download_url": server.url("x")},  # no image: ignored
        {"name": "notes.txt", "size": 1, "browser_download_url": server.url("x")}])
    server.publish("v0.4.0-rc1", [c], prerelease=True)
    server.releases.append({"tag_name": "draft", "draft": True, "assets": []})
    entries = C.list_entries(server.base + "/releases")
    assert [(e.release, e.image.name, e.signature is not None, e.prerelease) for e in entries] == [
        ("v0.3.0", a["name"] + ".xz", True, False), ("v0.3.0", b["name"], True, False), ("v0.4.0-rc1", c["name"], False, True)]
    raw, sig = C.fetch_manifest(entries[0])
    m, who = I.check_manifest(raw, sig, store, RPI3)
    assert m["version"] == "0.3.0" and who.kind == "official"
    assert C.fetch_manifest(entries[2])[1] is None


@pytest.mark.parametrize("status,code", [(404, "not_found_or_private"), (403, "forbidden"), (401, "forbidden"), (500, "http_error")])
def test_source_errors_are_classified(server, status, code):
    server.status = status
    with pytest.raises(C.SourceError) as e:
        C.list_entries(server.base + "/releases")
    assert e.value.code == code


def test_unreachable_bad_url_bad_json_and_oversized_answers(server, monkeypatch):
    port = server.httpd.server_port
    server.close()
    with pytest.raises(C.SourceError) as e:
        C.list_entries(f"http://127.0.0.1:{port}/releases", timeout=2)
    assert e.value.code == "unreachable"
    for url in ("file:///etc/passwd", "ftp://x/y", "", "https://"):
        with pytest.raises(C.SourceError) as e:
            C.list_entries(url)
        assert e.value.code == "bad_url"

    class Resp:
        def __init__(self, body):
            self.body = body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            return self.body[:n]
    for body in (b"{}", b"not json", b'[{"assets": [{"name": "a"}]}]', b"[1]"):
        with pytest.raises(C.SourceError) as e:
            C.list_entries("https://example.invalid/r", opener=lambda req, timeout: Resp(body))
        assert e.value.code == "bad_response"
    monkeypatch.setattr(C, "MAX_JSON", 10)
    with pytest.raises(C.SourceError, match="too large"):
        C.list_entries("https://example.invalid/r", opener=lambda req, timeout: Resp(b"[" + b" " * 20 + b"]"))
    with pytest.raises(C.SourceError) as e:
        C.list_entries("https://example.invalid/r", opener=lambda req, timeout: (_ for _ in ()).throw(OSError("boom")))
    assert e.value.code == "unreachable"


def test_the_default_source_is_the_official_github_repository():
    assert C.DEFAULT_SOURCE == "https://api.github.com/repos/iskaron/hifi-bridge/releases"


def test_download_streams_caches_and_reports_progress(server, tmp_path):
    im = F.image(tmp_path, size=3 * F.MB + 123)
    server.publish("v1", [im])
    entry = C.list_entries(server.base + "/releases")[0]
    dl = C.Downloader(str(tmp_path / "cache"))
    seen = []
    path = dl.fetch(entry.image, progress=lambda d, t: seen.append((d, t)))
    assert open(path, "rb").read() == im["data"] and seen[-1] == (entry.image.size, entry.image.size) and len(seen) >= 4
    assert not os.path.exists(path + ".part") and not os.path.exists(path + ".part.json")
    n = len(server.requests)
    assert dl.fetch(entry.image) == path and len(server.requests) == n                 # cached: no request
    assert dl.cached(entry.image) == path


def test_interrupted_download_resumes_with_range_and_if_range(server, tmp_path):
    im = F.image(tmp_path, size=3 * F.MB)
    server.publish("v1", [im])
    entry = C.list_entries(server.base + "/releases")[0]
    dl = C.Downloader(str(tmp_path / "cache"), timeout=5)
    server.drop_after = F.MB + 7
    with pytest.raises(C.DownloadError, match="resumes next time"):
        dl.fetch(entry.image)
    part = dl.path_for(entry.image.name) + ".part"
    assert os.path.getsize(part) == F.MB + 7
    path = dl.fetch(entry.image)
    assert server.requests[-1]["range"] == f"bytes={F.MB + 7}-" and server.requests[-1]["if_range"] == '"v1"'
    assert open(path, "rb").read() == im["data"]


def test_changed_file_or_range_ignored_restarts_from_zero(server, tmp_path):
    im = F.image(tmp_path, size=2 * F.MB)
    server.publish("v1", [im])
    entry = C.list_entries(server.base + "/releases")[0]
    dl = C.Downloader(str(tmp_path / "cache"), timeout=5)
    for knob in ("etag", "ignore_range"):
        server.drop_after = 500_000
        with pytest.raises(C.DownloadError):
            dl.fetch(entry.image)
        if knob == "etag":
            server.etag = '"v2"'                     # the file changed on the server: If-Range fails, the server sends 200 + all
        else:
            server.ignore_range = True
        assert open(dl.fetch(entry.image), "rb").read() == im["data"]
        os.remove(dl.path_for(entry.image.name))


def test_cancel_keeps_the_partial_file_and_416_handling(server, tmp_path):
    im = F.image(tmp_path, size=3 * F.MB)
    server.publish("v1", [im])
    entry = C.list_entries(server.base + "/releases")[0]
    dl = C.Downloader(str(tmp_path / "cache"))
    cancel = threading.Event()
    with pytest.raises(C.DownloadCancelled):
        dl.fetch(entry.image, cancel=cancel, progress=lambda d, t: cancel.set())
    part = dl.path_for(entry.image.name) + ".part"
    assert os.path.getsize(part) == C.CHUNK
    # the partial file is already complete (power cut before the rename): the server answers 416, we finish without data
    with open(part, "wb") as fh:
        fh.write(im["data"])
    assert open(dl.fetch(entry.image), "rb").read() == im["data"]
    # a partial file that does not fit the server's file (416 below the size): removed, error, next attempt starts over
    big = C.Asset(entry.image.name, entry.image.url, entry.image.size + 10)
    with open(part, "wb") as fh:
        fh.write(im["data"] + b"0123456789"[:0])
    os.remove(dl.path_for(entry.image.name))
    with open(dl.path_for(entry.image.name) + ".part.json", "w") as fh:
        fh.write('{"url": "%s", "etag": "\\"v1\\""}' % entry.image.url)
    with pytest.raises(C.DownloadError, match="HTTP 416"):
        dl.fetch(big)
    assert not os.path.exists(part)


def test_download_refuses_bad_names_oversize_and_reports_http_and_network_errors(server, tmp_path):
    dl = C.Downloader(str(tmp_path / "cache"), timeout=3)
    for bad in ("../x", ".hidden", "a/b", "", "x" * 300):
        with pytest.raises(C.DownloadError, match="unsafe"):
            dl.path_for(bad)
    server.files["f.img"] = b"x" * 100
    with pytest.raises(C.DownloadError, match="more data than announced"):
        dl.fetch(C.Asset("f.img", server.url("f.img"), 50))
    with pytest.raises(C.DownloadError, match="incomplete"):
        dl.fetch(C.Asset("f.img", server.url("f.img"), 200))
    with pytest.raises(C.DownloadError, match="HTTP 404"):
        dl.fetch(C.Asset("g.img", server.url("missing"), 10))
    # a stale meta file from another URL and an over-long partial file are ignored
    with open(dl.path_for("f.img") + ".part", "wb") as fh:
        fh.write(b"y" * 500)
    with open(dl.path_for("f.img") + ".part.json", "w") as fh:
        fh.write("[]")
    assert open(dl.fetch(C.Asset("f.img", server.url("f.img"), 100)), "rb").read() == b"x" * 100
    with open(dl.path_for("h.img") + ".part", "wb") as fh:
        fh.write(b"y" * 500)
    with open(dl.path_for("h.img") + ".part.json", "w") as fh:
        fh.write('{"url": "%s"}' % server.url("f.img"))
    assert open(dl.fetch(C.Asset("h.img", server.url("f.img"), 100)), "rb").read() == b"x" * 100
    port = server.httpd.server_port
    server.close()
    with pytest.raises(C.DownloadError, match="download failed"):
        dl.fetch(C.Asset("z.img", f"http://127.0.0.1:{port}/f/z", 10))


def test_read_error_mid_stream_is_reported(tmp_path):
    class Resp:
        status, headers = 200, {"ETag": ""}
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n):
            raise OSError("connection reset")
    dl = C.Downloader(str(tmp_path / "c"), opener=lambda req, timeout: Resp())
    with pytest.raises(C.DownloadError, match="connection reset"):
        dl.fetch(C.Asset("a.img", "https://example.invalid/a", 10))


def test_image_verification_states(keys, tmp_path):
    key, store = keys
    good = F.image(tmp_path, key=key)
    v = I.verify_image(good["path"], good["raw"], good["sig"], store, RPI3)
    assert v.publisher.kind == "official" and v.board == RPI3 and v.manifest["sha256"] == good["manifest"]["sha256"]
    xz = F.image(tmp_path / "..", version="0.3.1", compress=True, key=key)
    seen = []
    I.verify_image(xz["path"], xz["raw"], xz["sig"], store, RPI3, progress=lambda d, t: seen.append(d))
    assert seen[-1] == xz["manifest"]["size"]
    gz = tmp_path / "x.img.gz"; gz.write_bytes(gzip.compress(good["data"]))
    I.verify_image(str(gz), good["raw"], good["sig"], store, RPI3)

    def code(fn):
        with pytest.raises(I.ImageError) as e:
            fn()
        return e.value.code
    assert code(lambda: I.verify_image(good["path"], good["raw"], None, store, RPI3)) == "unsigned"
    other, _ = F.keypair()
    assert code(lambda: I.verify_image(good["path"], good["raw"], F.sign(other, good["raw"]), store, RPI3)) == "untrusted_signature"
    assert code(lambda: I.verify_image(good["path"], b"{}", good["sig"], store, RPI3)) == "bad_manifest"
    assert code(lambda: I.verify_image(good["path"], good["raw"], good["sig"], store, B.by_id("rpi4"))) == "wrong_board"
    tampered = tmp_path / "t.img"; tampered.write_bytes(b"\x01" + good["data"][1:])
    assert code(lambda: I.verify_image(str(tampered), good["raw"], good["sig"], store, RPI3)) == "hash_mismatch"
    short = tmp_path / "s.img"; short.write_bytes(good["data"][:-1])
    assert code(lambda: I.verify_image(str(short), good["raw"], good["sig"], store, RPI3)) == "size_mismatch"
    longer = tmp_path / "l.img"; longer.write_bytes(good["data"] + b"x")
    assert code(lambda: I.verify_image(str(longer), good["raw"], good["sig"], store, RPI3)) == "size_mismatch"
    broken = tmp_path / "b.img.xz"; broken.write_bytes(open(xz["path"], "rb").read()[:5000])
    assert code(lambda: I.verify_image(str(broken), xz["raw"], xz["sig"], store, RPI3)) == "unreadable"
    assert code(lambda: I.verify_image(str(tmp_path / "missing"), good["raw"], good["sig"], store, RPI3)) == "unreadable"
    cancel = threading.Event(); cancel.set()
    assert code(lambda: I.verify_image(good["path"], good["raw"], good["sig"], store, RPI3, cancel=cancel)) == "cancelled"
    # a custom publisher is accepted only after explicit addition and is reported as such
    mine, pub = F.keypair()
    custom = F.image(tmp_path / "..", version="9.9.9", key=mine)
    assert code(lambda: I.verify_image(custom["path"], custom["raw"], custom["sig"], store, RPI3)) == "untrusted_signature"
    store.add_custom("Workshop", pub)
    assert I.verify_image(custom["path"], custom["raw"], custom["sig"], store, RPI3).publisher.kind == "custom"
    assert str(I.ImageError("x", "detail")) == "x: detail"


def test_http_error_from_an_injected_opener_is_classified():
    def opener(req, timeout):
        raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
    with pytest.raises(C.SourceError) as e:
        C.list_entries("https://example.invalid/x", opener=opener)
    assert e.value.code == "not_found_or_private" and "HTTP 404" in str(e.value)
