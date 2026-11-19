"""UNIT (local HTTP server imitating the GitHub Releases API; GitHub itself is never called): update check, resumable download,
streamed multipart upload."""
import hashlib
import io
import json
import os
import threading
import urllib.error
import urllib.request

import pytest
from fakes_ota import ReleaseServer

from btbridge.ota import source, transfer
from btbridge.ota.source import UpdateSource, is_newer, parse_version
from btbridge.ota.transfer import Download, TransferError, boundary_of, parse_multipart

C = "hifi-bridge-rpi3"


@pytest.fixture
def srv():
    s = ReleaseServer()
    yield s
    s.close()


# ---------------------------------------------------------------- versions
@pytest.mark.parametrize("a,b", [("0.4.0", "0.3.0"), ("0.3.0", "0.3.0-dev"), ("1.0.0", "0.99.9"), ("v0.3.1", "0.3.0"),
                                 ("1.0.0-rc2", "1.0.0-rc1"), ("1.0.0-rc.10", "1.0.0-rc.9"), ("1.0.0-rc1", "1.0.0-beta"), ("0.4", "0.3.9")])
def test_version_order(a, b):
    assert is_newer(a, b) and not is_newer(b, a)


def test_unparsable_versions_are_never_newer():
    assert parse_version("banana") is None and not is_newer("banana", "0.1") and not is_newer("0.2", "") and not is_newer("1.0", "1.0")


def test_load_trust(tmp_path):
    p = tmp_path / "t.json"
    assert source.load_trust(str(p)) is None
    p.write_text("[1]")
    assert source.load_trust(str(p)) is None
    p.write_text('{"mode": "own", "official_source": null}')
    assert source.load_trust(str(p))["mode"] == "own"


# ---------------------------------------------------------------- update check
def test_check_finds_the_newest_published_bundle_for_this_board(srv):
    for n in ("hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", "hifi-bridge-hifi-bridge-rpi3-0.5.0.raucb", "hifi-bridge-hifi-bridge-rpi3-0.6.0.raucb",
              "hifi-bridge-hifi-bridge-rpi4-0.7.0.raucb"):
        srv.assets[n] = b"x" * 100
    digest = "sha256:" + "AB" * 32
    srv.releases = [srv.release("v0.7.0", [("hifi-bridge-hifi-bridge-rpi4-0.7.0.raucb", {})]),            # other board only
                    srv.release("v0.6.0", [("hifi-bridge-hifi-bridge-rpi3-0.6.0.raucb", {})], prerelease=True),
                    srv.release("v0.5.0", [("notes.txt", {}), ("hifi-bridge-hifi-bridge-rpi3-0.5.0.raucb", {"digest": digest})]),
                    srv.release("v0.4.0", [("hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", {})]),
                    srv.release("v9.9.9", [("hifi-bridge-hifi-bridge-rpi3-9.9.9.raucb", {})], draft=True),
                    srv.release("nightly", [("hifi-bridge-hifi-bridge-rpi3-x.raucb", {})]), "junk",
                    {"tag_name": "v0.4.5", "assets": ["junk", {"name": "hifi-bridge-hifi-bridge-rpi3-a.raucb", "browser_download_url": "ftp://x"},
                                                       {"name": "hifi-bridge-hifi-bridge-rpi3-b.raucb", "size": "n/a",
                                                        "browser_download_url": f"{srv.base}/iskaron/hifi-bridge/releases/download/v0.4.5/b"}]}]
    r = UpdateSource(srv.source(), allow_http=True, clock=lambda: 42).check("0.3.0-dev", C)
    assert r["status"] == "update_available" and r["checked_at"] == 42
    lt = r["latest"]
    assert lt["version"] == "0.5.0" and lt["asset"]["size"] == 100 and lt["asset"]["sha256"] == "ab" * 32
    assert lt["asset"]["url"] == f"{srv.base}/iskaron/hifi-bridge/releases/download/v0.5.0/hifi-bridge-hifi-bridge-rpi3-0.5.0.raucb"
    assert lt["page"] == f"{srv.base}/iskaron/hifi-bridge/releases/tag/v0.5.0"
    path, headers = srv.requests[-1]
    assert path.endswith("/releases?per_page=20") and headers["User-Agent"].startswith("hifi-bridge/")
    assert "Authorization" not in headers                                     # never any credentials
    assert UpdateSource(srv.source(), allow_http=True).check("0.5.0", C)["status"] == "up_to_date"
    assert UpdateSource(srv.source(), allow_http=True).check("0.5.0", "hifi-bridge-rpizero2w")["status"] == "no_bundle"


def test_assets_outside_the_official_repository_are_ignored(srv):
    srv.assets["hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"] = b"x"
    rel = srv.release("v0.4.0", [("hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", {})])
    rel["assets"][0]["browser_download_url"] = "https://192.168.1.1/admin/hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"
    srv.releases = [rel]
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["status"] == "no_bundle"


def test_release_page_link_never_leaves_the_official_releases(srv):
    srv.assets["hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb"] = b"x"
    rel = srv.release("v0.4.0", [("hifi-bridge-hifi-bridge-rpi3-0.4.0.raucb", {})])
    rel["html_url"] = "https://evil.example/phish"
    srv.releases = [rel]
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["latest"]["page"] == srv.source()["releases"]


@pytest.mark.parametrize("status,headers,expected", [(404, {}, "not_public"), (401, {}, "not_public"), (403, {}, "not_public"),
                                                     (403, {"X-RateLimit-Remaining": "0"}, "rate_limited"), (429, {}, "rate_limited"),
                                                     (500, {}, "unreachable")])
def test_check_maps_http_errors(srv, status, headers, expected):
    srv.api_status, srv.api_headers, srv.releases = status, headers, {"message": "Not Found"}
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["status"] == expected


def test_check_handles_bad_or_missing_sources(srv):
    assert UpdateSource(None).check("0.3.0", C)["status"] == "no_source"
    assert UpdateSource({"type": "other", "api": "x"}).check("0.3.0", C)["status"] == "no_source"
    assert UpdateSource(srv.source()).check("0.3.0", C)["status"] == "insecure_source"          # http only with allow_http (tests)
    srv.releases = b"not json"
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["status"] == "invalid_response"
    srv.releases = {"not": "a list"}
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["status"] == "invalid_response"
    srv.releases = b"[" + b" " * source.MAX_JSON + b"]"
    assert UpdateSource(srv.source(), allow_http=True).check("0.3.0", C)["status"] == "invalid_response"
    dead = dict(srv.source(), api="http://127.0.0.1:9/repos/x/releases")
    assert UpdateSource(dead, allow_http=True, timeout=2).check("0.3.0", C)["status"] == "unreachable"


def test_redirects_away_from_https_are_refused():
    h = source.HttpsOnlyRedirect(allow_http=False)
    req = urllib.request.Request("https://api.github.com/x")
    with pytest.raises(urllib.error.URLError):
        h.redirect_request(req, None, 302, "Found", {}, "http://evil.example/x")
    assert h.redirect_request(req, None, 302, "Found", {}, "https://objects.githubusercontent.com/x").full_url.startswith("https://")


# ---------------------------------------------------------------- download
def blob(n=300_000):
    return bytes((i * 7) % 251 for i in range(n))


def test_download_follows_the_redirect_and_checks_size_and_digest(srv, tmp_path):
    data = blob()
    srv.assets["b.raucb"] = data
    seen = []
    d = Download(f"{srv.base}/dl/b.raucb", str(tmp_path / "b.raucb"), len(data), hashlib.sha256(data).hexdigest(), allow_http=True,
                 progress=lambda done, total: seen.append(done))
    assert d.run() == str(tmp_path / "b.raucb") and (tmp_path / "b.raucb").read_bytes() == data
    assert seen[-1] == len(data) and not os.path.exists(d.part) and not os.path.exists(d.meta)


def test_download_resumes_with_a_range_request_after_a_dropped_connection(srv, tmp_path):
    data = blob()
    srv.assets["b.raucb"] = data
    srv.cut_after = 100_000
    dest = str(tmp_path / "b.raucb")
    with pytest.raises(TransferError) as e:
        Download(f"{srv.base}/assets/b.raucb", dest, len(data), allow_http=True).run()
    assert e.value.code == "interrupted" and 0 < os.path.getsize(dest + ".part") < len(data)
    Download(f"{srv.base}/assets/b.raucb", dest, len(data), hashlib.sha256(data).hexdigest(), allow_http=True).run()
    assert open(dest, "rb").read() == data
    assert srv.requests[-1][1].get("Range", "").startswith("bytes=")


def test_server_without_range_support_restarts_from_zero(srv, tmp_path):
    data = blob()
    srv.assets["b.raucb"] = data
    dest = str(tmp_path / "b.raucb")
    open(dest + ".part", "wb").write(b"garbage")
    json.dump({"url": f"{srv.base}/assets/b.raucb", "size": len(data)}, open(dest + ".part.json", "w"))
    srv.ignore_range = True
    Download(f"{srv.base}/assets/b.raucb", dest, len(data), hashlib.sha256(data).hexdigest(), allow_http=True).run()
    assert open(dest, "rb").read() == data


def test_partial_file_of_another_url_is_discarded(srv, tmp_path):
    data = blob(1000)
    srv.assets["b.raucb"] = data
    dest = str(tmp_path / "b.raucb")
    open(dest + ".part", "wb").write(b"old")
    json.dump({"url": "http://elsewhere/x", "size": 1000}, open(dest + ".part.json", "w"))
    Download(f"{srv.base}/assets/b.raucb", dest, 1000, allow_http=True).run()
    assert open(dest, "rb").read() == data


def test_already_complete_partial_file_finishes_on_416(srv, tmp_path):
    data = blob(1000)
    srv.assets["b.raucb"] = data
    dest = str(tmp_path / "b.raucb")
    open(dest + ".part", "wb").write(data)
    json.dump({"url": f"{srv.base}/assets/b.raucb", "size": 1000}, open(dest + ".part.json", "w"))
    assert Download(f"{srv.base}/assets/b.raucb", dest, 1000, allow_http=True).run() == dest


def test_download_errors(srv, tmp_path):
    data = blob(5000)
    srv.assets["b.raucb"] = data
    dest = str(tmp_path / "b.raucb")
    url = f"{srv.base}/assets/b.raucb"
    with pytest.raises(TransferError, match="digest_mismatch"):
        Download(url, dest, len(data), "00" * 32, allow_http=True).run()
    assert not os.path.exists(dest) and not os.path.exists(dest + ".part")
    with pytest.raises(TransferError, match="size_mismatch"):
        Download(url, dest, len(data) + 1, allow_http=True).run()
    with pytest.raises(TransferError, match="too_large"):
        Download(url, dest, len(data), max_size=100, allow_http=True).run()
    with pytest.raises(TransferError, match="too_large"):
        Download(url, dest, 0, allow_http=True).run()
    with pytest.raises(TransferError, match="no_space"):
        Download(url, dest, len(data), allow_http=True, free=lambda p: 1000).run()
    with pytest.raises(TransferError, match="http_error"):
        Download(f"{srv.base}/assets/missing", dest, 10, allow_http=True).run()
    with pytest.raises(TransferError, match="unreachable"):
        Download("http://127.0.0.1:9/x", dest, 10, allow_http=True, timeout=2).run()
    with pytest.raises(ValueError):
        Download(url, dest, 10)                                            # plain http refused outside tests
    cancel = threading.Event(); cancel.set()
    with pytest.raises(TransferError, match="cancelled"):
        Download(url, dest, len(data), allow_http=True, cancel=cancel).run()
    assert os.path.exists(dest + ".part")                                   # kept for a later resume


def test_download_rejects_a_wrong_content_range(tmp_path):
    class Resp(io.BytesIO):
        status = 206
        headers = {"Content-Range": "bytes 0-9/10", "Content-Length": "10"}
    dest = str(tmp_path / "b")
    open(dest + ".part", "wb").write(b"12345")
    json.dump({"url": "https://x/b", "size": 10}, open(dest + ".part.json", "w"))
    with pytest.raises(TransferError, match="Content-Range"):
        Download("https://x/b", dest, 10, open_url=lambda req, timeout: Resp(b"0123456789")).run()

    class Weird(io.BytesIO):
        status = 204
        headers = {}
    with pytest.raises(TransferError, match="http_error"):
        Download("https://x/b", str(tmp_path / "c"), 10, open_url=lambda req, timeout: Weird(b"")).run()

    class Short(io.BytesIO):
        status = 200
        headers = {}
    with pytest.raises(TransferError, match="interrupted"):
        Download("https://x/b", str(tmp_path / "d"), 10, open_url=lambda req, timeout: Short(b"123")).run()
    with pytest.raises(TransferError, match="more data than announced"):          # no Content-Length, then too much data
        Download("https://x/b", str(tmp_path / "e"), 10, open_url=lambda req, timeout: Short(b"x" * 11)).run()

    class Dropping(Short):
        def read(self, n=-1):
            raise ConnectionResetError("peer reset")
    with pytest.raises(TransferError, match="interrupted"):
        Download("https://x/b", str(tmp_path / "f"), 10, open_url=lambda req, timeout: Dropping(b"")).run()


# ---------------------------------------------------------------- multipart
def mp(parts, boundary="----xyz"):
    out = b""
    for name, filename, data in parts:
        fn = f'; filename="{filename}"' if filename is not None else ""
        out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"{fn}\r\n"
                + ("Content-Type: application/octet-stream\r\n" if filename else "") + "\r\n").encode() + data + b"\r\n"
    return out + f"--{boundary}--\r\n".encode()


def reader(body, step=7000):
    f = io.BytesIO(body)
    return lambda n: f.read(min(n, step))


@pytest.mark.parametrize("step", [1, 13, 7000, 1 << 20])
def test_multipart_streams_files_and_returns_fields(step):
    payload = blob(200_000) + b"\r\n------xy" + b"\r\n--"          # contains look-alikes of the delimiter
    body = mp([("csrf", None, b"tok"), ("bundle", "u.raucb", payload), ("note", None, "ä".encode())])
    sinks = {}

    def open_file(name, filename):
        sinks[name] = (filename, io.BytesIO())
        return sinks[name][1]
    seen = []
    fields = parse_multipart(reader(body, step), b"----xyz", open_file, progress=seen.append)
    assert fields == {"csrf": "tok", "note": "ä"} and sinks["bundle"][0] == "u.raucb" and sinks["bundle"][1].getvalue() == payload
    assert seen[-1] == len(body)


def test_multipart_lets_the_caller_refuse_before_the_file_is_stored():
    body = mp([("bundle", "u.raucb", b"data"), ("csrf", None, b"tok")])

    def open_file(name, filename):
        raise PermissionError("csrf first")
    with pytest.raises(PermissionError):
        parse_multipart(reader(body), b"----xyz", open_file)


@pytest.mark.parametrize("body,code", [
    (b"no boundary here", "bad_request"),
    (b"x" * 10000, "bad_request"),
    (b"------xyz", "truncated"),
    (b"------xyzXX", "bad_request"),
    (b"------xyz\r\nContent-Disposition: form-data; name=\"a\"\r\n", "truncated"),
    (b"------xyz\r\n" + b"X: y\r\n" * 2000, "bad_request"),
    (b"------xyz\r\nX-Other: 1\r\n\r\nv\r\n------xyz--", "bad_request"),
    (b"------xyz\r\nContent-Disposition: form-data; filename=\"a\"\r\n\r\nv\r\n------xyz--", "bad_request"),
    (b"------xyz\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nvalue without end", "truncated"),
    (b"------xyz\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\n" + b"v" * 5000 + b"\r\n------xyz--", "field_too_large"),
    (b"".join(b"------xyz\r\nContent-Disposition: form-data; name=\"a\"\r\n\r\nv\r\n" for _ in range(9)) + b"------xyz--", "too_many_parts"),
])
def test_multipart_errors(body, code):
    with pytest.raises(TransferError) as e:
        parse_multipart(reader(body), b"----xyz", lambda n, f: io.BytesIO())
    assert e.value.code == code


def test_boundary_parsing():
    assert boundary_of("multipart/form-data; boundary=----WebKitFormBoundaryAbc") == b"----WebKitFormBoundaryAbc"
    assert boundary_of('multipart/form-data; charset=utf-8; boundary="a b"') == b"a b"
    for bad in ("application/json", "", "multipart/form-data", "multipart/form-data; boundary=" + "x" * 71 + ";"):
        with pytest.raises(TransferError):
            boundary_of(bad)


def test_space_check():
    transfer.ensure_space("/", 10, free=lambda p: transfer.SPACE_MARGIN + 10)
    with pytest.raises(TransferError, match="no_space"):
        transfer.ensure_space("/", 11, free=lambda p: transfer.SPACE_MARGIN + 10)
    assert transfer.free_bytes("/") > 0
