"""HTTP layer of the owner WebUI (stdlib ``ThreadingHTTPServer``).

Security model (tests: tests/unit/test_webui.py):
  * every page except /login, /setup (first step) and /static/* requires a session; /api/* answers 401 instead of redirecting;
  * every state change is a POST with the session's CSRF token; an ``Origin`` header that does not match ``Host`` is rejected;
  * cookies ``HttpOnly; SameSite=Strict; Path=/`` (+ ``Secure`` with TLS); strict CSP without inline script; no-store caching;
  * request bodies are size-limited; errors never echo internals.
"""
from __future__ import annotations

import html
import http.cookies
import json
import ssl
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from . import views
from .auth import AuthError
from .i18n import LANGS, Translator, pick_language

MAX_BODY = 64 * 1024
CONN_TIMEOUT_S = 10.0          # per connection: TLS handshake, request, body and response (review M2: idle peers pinned a thread each)
MAX_CONNECTIONS = 64           # concurrent connections per listener; more are answered 503 and closed
BOOTSTRAP_PATHS = ("/trust", "/ca.crt", "/ca.der", "/healthz", "/static/app.css", "/static/logo.svg", "/static/app.js", "/manifest.webmanifest", "/trust/proof")
WIZARD_ACTIONS = ("/diagnostics/test",)     # actions offered inside the setup wizard (test sound on the last step)
COOKIE = "bab_session"
LANG_COOKIE = "bab_lang"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'")


class BodyError(ValueError):
    """A request body that is refused before it is read (status: 400, 411 or 413)."""
    def __init__(self, status: int, reason: str):
        super().__init__(reason)
        self.status = status


class Request:
    def __init__(self, h: BaseHTTPRequestHandler, server: "WebUI", tls: bool = False):
        self.h, self.ui = h, server
        u = urllib.parse.urlsplit(h.path)
        self.path, self.query, self.raw_query = u.path, urllib.parse.parse_qs(u.query), u.query
        self.method = h.command
        self.peer = h.client_address[0]
        self.client, self.scheme, self.host = self.peer, ("https" if tls else "http"), h.headers.get("Host", "")
        acc = server.svc.access
        if acc is not None and acc.is_trusted_proxy(self.peer):            # only an explicitly trusted reverse proxy may set these
            self.client = client_address(self.peer, h.headers.get("X-Forwarded-For", ""), acc.is_trusted_proxy)
            proto = h.headers.get("X-Forwarded-Proto", "").lower()
            if proto in ("http", "https"):
                self.scheme = proto
            self.host = h.headers.get("X-Forwarded-Host", self.host).split(",")[0].strip()
        self.secure = self.scheme == "https"
        c = http.cookies.SimpleCookie()
        try:
            c.load(h.headers.get("Cookie", ""))
        except http.cookies.CookieError:
            pass
        self.cookies = {k: v.value for k, v in c.items()}
        self.sid = self.cookies.get(COOKIE)
        self.session = server.svc.auth.sessions.get(self.sid)
        self.form: dict[str, str] = {}
        self.lang = pick_language(server.svc.store.get("language"), self.cookies.get(LANG_COOKIE), h.headers.get("Accept-Language", ""))
        self.t = Translator(self.lang)
        self.flash = self.cookies.get("bab_flash")

    def read_form(self) -> None:
        """Parse the request body. Before any session or CSRF check, so it is strict: only one plain decimal Content-Length of at
        most MAX_BODY bytes is accepted (a negative length used to read until EOF: review H1); chunked bodies are refused, and a
        short body ends with the connection's socket timeout instead of blocking a thread."""
        if self.h.headers.get("Transfer-Encoding") is not None:
            raise BodyError(411, "chunked request bodies are not supported")
        values = self.h.headers.get_all("Content-Length") or []
        if len(values) > 1:
            raise BodyError(400, "conflicting Content-Length")
        value = values[0].strip() if values else "0"
        if not value.isascii() or not value.isdigit() or len(value) > 12:
            raise BodyError(400, "malformed Content-Length")
        n = int(value)
        if n > MAX_BODY:
            raise BodyError(413, "body too large")
        raw = self.h.rfile.read(n) if n else b""
        if len(raw) != n:
            raise BodyError(400, "truncated body")
        raw = raw.decode("utf-8", "replace")
        ctype = self.h.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            data = json.loads(raw or "{}")
            self.form = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
        else:
            self.form = {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}


class Response:
    def __init__(self, status: int = 200, body: str | bytes = b"", ctype: str = "text/html; charset=utf-8"):
        self.status, self.ctype = status, ctype
        self.body = body.encode("utf-8") if isinstance(body, str) else body
        self.headers: list[tuple[str, str]] = []
        self.close = False

    def cookie(self, name: str, value: str, secure: bool, max_age: int | None = None, http_only: bool = True) -> "Response":
        parts = [f"{name}={value}", "Path=/", "SameSite=Strict"]
        if http_only:
            parts.append("HttpOnly")
        if secure:
            parts.append("Secure")
        if max_age is not None:
            parts.append(f"Max-Age={max_age}")
        self.headers.append(("Set-Cookie", "; ".join(parts)))
        return self


def redirect(to: str, flash: str | None = None, secure: bool = False, error: bool = False) -> Response:
    """PRG redirect with a one-shot message. Errors are marked so the next page announces them (role=alert) and moves focus there."""
    r = Response(303)
    r.headers.append(("Location", to))
    if flash:
        r.cookie("bab_flash", urllib.parse.quote(("e:" if error else "i:") + flash), secure, max_age=30)
    return r


def json_response(data, status: int = 200) -> Response:
    return Response(status, json.dumps(data), "application/json")


class _Server(ThreadingHTTPServer):
    """Threaded listener with a cap on concurrent connections. With TLS the handshake runs in the connection's own thread with
    a timeout: wrapping the listening socket ran it inside accept(), so one idle TCP peer blocked every HTTPS client (review M1)."""
    daemon_threads = True

    def __init__(self, addr, handler, max_connections: int):
        super().__init__(addr, handler)
        self._slots = threading.BoundedSemaphore(max_connections)

    def process_request(self, request, client_address):
        if not self._slots.acquire(blocking=False):
            try:
                request.settimeout(1.0)
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()

    def handle_error(self, request, client_address):
        pass                                             # failed handshakes, timeouts, resets: nothing to report per peer


class WebUI:
    def __init__(self, service, host: str = "0.0.0.0", port: int = 8080, tls_port: int | None = None,  # nosec B104 - see appliance.py
                 static_dir: str | None = None):
        self.svc, self.host, self.port, self.tls_port = service, host, port, tls_port
        self.static_dir = static_dir or views.STATIC_DIR
        self.routes: dict[tuple[str, str], Callable[[Request], Response]] = views.routes(self)
        self._httpd: ThreadingHTTPServer | None = None
        self._httpsd: ThreadingHTTPServer | None = None
        from ..net.hostcheck import HostPolicy
        self._fallback_policy = HostPolicy(lambda: [])          # without LocalAccess: only IP literals and localhost

    # ---- lifecycle ----
    def _server(self, port: int, tls: bool) -> ThreadingHTTPServer:
        ui = self

        class H(BaseHTTPRequestHandler):
            server_version = "hifi-bridge"
            sys_version = ""
            timeout = CONN_TIMEOUT_S

            def setup(self):
                if tls:
                    self.request.settimeout(CONN_TIMEOUT_S)
                    ctx = ui.svc.access.ctx if ui.svc.access is not None else None    # read per connection: renewal/swap applies
                    if ctx is None:
                        raise ConnectionAbortedError("TLS not available")
                    self.request = ctx.wrap_socket(self.request, server_side=True, do_handshake_on_connect=False)
                    self.request.do_handshake()
                super().setup()

            def finish(self):
                try:
                    super().finish()
                finally:
                    if tls:
                        try:
                            self.request.close()
                        except OSError:
                            pass

            def log_message(self, fmt, *args):            # access logs would leak paths of the stream; keep quiet
                pass

            def do_GET(self):
                ui._dispatch(self, tls)

            def do_POST(self):
                ui._dispatch(self, tls)

            def do_HEAD(self):
                ui._dispatch(self, tls)

        httpd = _Server((self.host, port), H, MAX_CONNECTIONS)
        threading.Thread(target=httpd.serve_forever, daemon=True, name="webui-tls" if tls else "webui").start()
        return httpd

    def start(self) -> int:
        self._httpd = self._server(self.port, False)
        self.port = self._httpd.server_address[1]
        self.ensure_https()
        return self.port

    def ensure_https(self) -> int | None:
        """Start the HTTPS listener once TLS is available (also after the owner switches the TLS mode at runtime)."""
        acc = self.svc.access
        if self._httpsd is None and self.tls_port is not None and acc is not None and acc.tls_available():
            self._httpsd = self._server(self.tls_port, True)
            self.tls_port = self._httpsd.server_address[1]
            if acc.https_port != self.tls_port:
                acc.https_port = self.tls_port
        return self.tls_port if self._httpsd else None

    def stop(self) -> None:
        for d in (self._httpd, self._httpsd):
            if d:
                d.shutdown()
                d.server_close()

    # ---- dispatch ----
    def _dispatch(self, h: BaseHTTPRequestHandler, tls: bool = False) -> None:
        req = None
        try:
            req = Request(h, self, tls)
            policy = self.svc.access.policy if self.svc.access is not None else self._fallback_policy
            if not policy.allowed(req.host):              # DNS-rebinding protection: refuse foreign host names before anything else
                resp = self._misdirected(req, tls)
            elif self._needs_https(req):
                resp = Response(308)
                host = req.host.split("]")[0] + "]" if req.host.startswith("[") else req.host.split(":")[0]
                resp.headers.append(("Location", f"https://{host}:{self.tls_port}{req.path}" + (f"?{req.raw_query}" if req.raw_query else "")))
            else:
                resp = self._route(req)
        except Exception as e:                                # never echo internals to the browser
            self.svc.logs(f"webui error: {type(e).__name__}: {e}")
            resp = error_page(500, req.lang if req is not None else "en")
        self._send(h, resp, req)

    def _misdirected(self, req: Request, tls: bool) -> Response:
        """421 for a foreign host name (DNS-rebinding protection), with links that do work: the device's names and the address
        the browser actually connected to (review M9: a bare English text left owners stuck)."""
        from ..net.hostcheck import split_host
        acc = self.svc.access
        try:
            local_ip, port = req.h.connection.getsockname()[:2]
        except (OSError, ValueError):
            local_ip, port = None, (self.tls_port if tls else self.port)
        scheme = "https" if tls else "http"
        names = [acc.main_name(), *acc.extra_names()] if acc is not None else []
        hosts = list(dict.fromkeys(n for n in [*names, local_ip, *(acc.addresses() if acc is not None else [])] if n))
        suffix = "" if (scheme, port) in (("http", 80), ("https", 443)) else f":{port}"
        links = [f"{scheme}://{'[' + h + ']' if ':' in h else h}{suffix}/" for h in hosts]
        return error_page(421, req.lang, host=split_host(req.host)[:100], links=links)

    def _needs_https(self, req: Request) -> bool:
        acc = self.svc.access
        return (not req.secure and acc is not None and self.svc.store.get("https_only") and acc.tls_available()
                and self._httpsd is not None and req.path not in BOOTSTRAP_PATHS)

    def _route(self, req: Request) -> Response:
        if req.path.startswith("/static/"):
            return views.static(self, req)
        method = "GET" if req.method == "HEAD" else req.method
        handler = self.routes.get((method, req.path))
        if handler is None:
            if any(p == req.path for (_, p) in self.routes):
                return error_page(405, req.lang)
            return Response(404, views.not_found(self, req))
        public = getattr(handler, "public", False)
        if method == "POST":
            if not same_origin(req.h.headers, req.host):
                return error_page(403, req.lang)
            try:
                req.read_form()
            except BodyError as e:
                resp = error_page(e.status, req.lang)
                resp.close = True                               # the unread body must not be parsed as a next request
                return resp
            except (ValueError, json.JSONDecodeError):
                return error_page(400, req.lang)
            if not public:
                token = req.form.get("csrf") or req.h.headers.get("X-CSRF-Token")
                if not self.svc.auth.sessions.check_csrf(req.sid, token):
                    if req.path.startswith("/api/"):
                        return json_response({"error": "csrf"}, 403)
                    return redirect("/login", req.t("flash.session_expired"), req.secure)
        if not public and req.session is None:
            if req.path.startswith("/api/"):
                return json_response({"error": "unauthenticated"}, 401)
            if not self.svc.auth.has_password():
                return redirect("/setup")
            return redirect("/login")
        if req.session is not None and not public and not self.svc.store.get("setup_complete") \
                and not req.path.startswith(("/setup", "/api/", "/logout")) and req.path not in WIZARD_ACTIONS:
            return redirect("/setup/bluetooth")                 # finish the wizard first
        return handler(req)

    def _send(self, h: BaseHTTPRequestHandler, r: Response, req: Request | None = None) -> None:
        try:
            h.send_response(r.status)
            h.send_header("Content-Type", r.ctype)
            h.send_header("Content-Length", str(len(r.body)))
            h.send_header("Content-Security-Policy", CSP)
            h.send_header("X-Content-Type-Options", "nosniff")
            h.send_header("X-Frame-Options", "DENY")
            # same-origin (not no-referrer): with no-referrer, browsers send "Origin: null" on form POSTs (found by the browser test)
            h.send_header("Referrer-Policy", "same-origin")
            h.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
            if req is not None and req.secure:                # browsers ignore (and warn about) COOP on plain-HTTP origins
                h.send_header("Cross-Origin-Opener-Policy", "same-origin")
            if not any(k == "Cache-Control" for k, _ in r.headers):
                h.send_header("Cache-Control", "no-store")
            for k, v in r.headers:
                h.send_header(k, v)
            if r.close:
                h.send_header("Connection", "close")
                h.close_connection = True
            h.end_headers()
            if h.command != "HEAD":
                h.wfile.write(r.body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def client_address(peer: str, forwarded_for: str, trusted: Callable[[str], bool]) -> str:
    """The client behind trusted proxies: the RIGHTMOST X-Forwarded-For entry that is not itself a trusted proxy. The leftmost
    entry is whatever the client wrote and was used to reset the login back-off at will (review M4)."""
    import ipaddress
    hops = [x.strip() for x in forwarded_for.split(",") if x.strip()]
    for hop in reversed(hops):
        try:
            ipaddress.ip_address(hop)
        except ValueError:
            return peer                                  # malformed chain: fall back to the proxy's own address
        if not trusted(hop):
            return hop
    return peer


def same_origin(headers, host: str | None = None) -> bool:
    """CSRF defence in depth for POSTs (the CSRF token is the primary control for authenticated actions):
    a real Origin must match the request's host - behind a trusted reverse proxy the forwarded host the browser used, not the
    proxy's upstream Host (review M3); "Origin: null" is accepted only if the browser's Sec-Fetch-Site says same-origin;
    clients sending neither header (non-browsers) are left to the token check."""
    origin, site = headers.get("Origin"), headers.get("Sec-Fetch-Site")
    host = headers.get("Host", "") if host is None else host
    if origin and origin != "null":
        u = urllib.parse.urlsplit(origin)
        default = {"http": ":80", "https": ":443"}.get(u.scheme, "")
        strip = lambda n: n[:-len(default)] if default and n.lower().endswith(default) else n   # noqa: E731
        return strip(u.netloc.lower()) == strip(host.lower())
    if site is not None:
        return site in ("same-origin", "none")
    return origin is None


def error_page(status: int, lang: str = "en", host: str = "", links: list[str] | tuple = ()) -> Response:
    """Error page in both UI languages (the preferred one first, each marked with its lang attribute); never echoes internals."""
    first = lang if lang in LANGS else "en"
    parts = []
    for code in (first, *[x for x in LANGS if x != first]):
        t = Translator(code)
        key = f"errorpage.{status}" if f"errorpage.{status}" in t.s else "errorpage.400"
        parts.append(f'<section lang="{code}"><h1>{esc(t("errorpage.title", status=status))}</h1><p>{esc(t(key, host=host))}</p></section>')
    items = "".join(f'<li><a href="{esc(u)}">{esc(u)}</a></li>' for u in links)
    return Response(status, f'<!doctype html><html lang="{first}"><meta charset="utf-8">'
                            f'<meta name="viewport" content="width=device-width, initial-scale=1"><title>{status}</title>'
                            f'<main>{"".join(parts)}{f"<ul>{items}</ul>" if items else ""}</main></html>')


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


__all__ = ["WebUI", "Request", "Response", "redirect", "json_response", "esc", "AuthError", "COOKIE", "LANG_COOKIE"]
