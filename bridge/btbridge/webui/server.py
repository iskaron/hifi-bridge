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
from .i18n import Translator, pick_language

MAX_BODY = 64 * 1024
WIZARD_ACTIONS = ("/diagnostics/test",)     # actions offered inside the setup wizard (test sound on the last step)
COOKIE = "bab_session"
LANG_COOKIE = "bab_lang"
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; "
       "frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'")


class Request:
    def __init__(self, h: BaseHTTPRequestHandler, server: "WebUI"):
        self.h, self.ui = h, server
        u = urllib.parse.urlsplit(h.path)
        self.path, self.query = u.path, urllib.parse.parse_qs(u.query)
        self.method = h.command
        self.client = h.client_address[0]
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
        n = int(self.h.headers.get("Content-Length") or 0)
        if n > MAX_BODY:
            raise ValueError("body too large")
        raw = self.h.rfile.read(n).decode("utf-8", "replace") if n else ""
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


def redirect(to: str, flash: str | None = None, secure: bool = False) -> Response:
    r = Response(303)
    r.headers.append(("Location", to))
    if flash:
        r.cookie("bab_flash", urllib.parse.quote(flash), secure, max_age=30)
    return r


def json_response(data, status: int = 200) -> Response:
    return Response(status, json.dumps(data), "application/json")


class WebUI:
    def __init__(self, service, host: str = "0.0.0.0", port: int = 8080, tls: tuple[str, str] | None = None, static_dir: str | None = None):
        self.svc, self.host, self.port, self.tls = service, host, port, tls
        self.secure = tls is not None
        self.static_dir = static_dir or views.STATIC_DIR
        self.routes: dict[tuple[str, str], Callable[[Request], Response]] = views.routes(self)
        self._httpd: ThreadingHTTPServer | None = None

    # ---- lifecycle ----
    def start(self) -> int:
        ui = self

        class H(BaseHTTPRequestHandler):
            server_version = "btbridge"
            sys_version = ""

            def log_message(self, fmt, *args):            # access logs would leak paths of the stream; keep quiet
                pass

            def do_GET(self):
                ui._dispatch(self)

            def do_POST(self):
                ui._dispatch(self)

            def do_HEAD(self):
                ui._dispatch(self)

        self._httpd = ThreadingHTTPServer((self.host, self.port), H)
        self._httpd.daemon_threads = True
        if self.tls:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(*self.tls)
            self._httpd.socket = ctx.wrap_socket(self._httpd.socket, server_side=True)
        self.port = self._httpd.server_address[1]
        threading.Thread(target=self._httpd.serve_forever, daemon=True, name="webui").start()
        return self.port

    def stop(self) -> None:
        if self._httpd:
            self._httpd.shutdown()
            self._httpd.server_close()

    # ---- dispatch ----
    def _dispatch(self, h: BaseHTTPRequestHandler) -> None:
        try:
            req = Request(h, self)
            resp = self._route(req)
        except Exception as e:                                # never echo internals to the browser
            self.svc.logs(f"webui error: {type(e).__name__}: {e}")
            resp = error_page(500, "Internal error")
        self._send(h, resp)

    def _route(self, req: Request) -> Response:
        if req.path.startswith("/static/"):
            return views.static(self, req)
        method = "GET" if req.method == "HEAD" else req.method
        handler = self.routes.get((method, req.path))
        if handler is None:
            if any(p == req.path for (_, p) in self.routes):
                return error_page(405, "Method not allowed")
            return Response(404, views.not_found(self, req))
        public = getattr(handler, "public", False)
        if method == "POST":
            if not same_origin(req.h.headers):
                return error_page(403, "Cross-origin request refused")
            try:
                req.read_form()
            except (ValueError, json.JSONDecodeError):
                return error_page(400, "Bad request")
            if not public:
                token = req.form.get("csrf") or req.h.headers.get("X-CSRF-Token")
                if not self.svc.auth.sessions.check_csrf(req.sid, token):
                    if req.path.startswith("/api/"):
                        return json_response({"error": "csrf"}, 403)
                    return redirect("/login", req.t("flash.session_expired"), self.secure)
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

    def _send(self, h: BaseHTTPRequestHandler, r: Response) -> None:
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
            if self.secure:                                   # browsers ignore (and warn about) COOP on plain-HTTP origins
                h.send_header("Cross-Origin-Opener-Policy", "same-origin")
            if not any(k == "Cache-Control" for k, _ in r.headers):
                h.send_header("Cache-Control", "no-store")
            for k, v in r.headers:
                h.send_header(k, v)
            h.end_headers()
            if h.command != "HEAD":
                h.wfile.write(r.body)
        except (BrokenPipeError, ConnectionResetError):
            pass


def same_origin(headers) -> bool:
    """CSRF defence in depth for POSTs (the CSRF token is the primary control for authenticated actions):
    a real Origin must match Host; "Origin: null" is accepted only if the browser's Sec-Fetch-Site says same-origin;
    clients sending neither header (non-browsers) are left to the token check."""
    origin, host, site = headers.get("Origin"), headers.get("Host", ""), headers.get("Sec-Fetch-Site")
    if origin and origin != "null":
        return urllib.parse.urlsplit(origin).netloc == host
    if site is not None:
        return site in ("same-origin", "none")
    return origin is None


def error_page(status: int, text: str) -> Response:
    return Response(status, f'<!doctype html><html lang="en"><meta charset="utf-8"><title>{status}</title><p>{html.escape(text)}</p></html>')


def esc(s) -> str:
    return html.escape("" if s is None else str(s), quote=True)


__all__ = ["WebUI", "Request", "Response", "redirect", "json_response", "esc", "AuthError", "COOKIE", "LANG_COOKIE"]
