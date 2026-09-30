"""Private writable local console: a loopback browser surface over board operations.

The console binds ``127.0.0.1`` on a fixed configurable port. Local access is
login-free by default; optional login uses a single-use ten-minute entry and
explicitly revocable hashed sessions. Every request passes the exact loopback
origin boundary and writes also need a bootstrap CSRF value. Each admitted client
uses the existing named, validated operations and revision fences. There is no
CORS, generic SQL surface or browser authority over Host workflow commands.

The page is the built React/Vite bundle served from ``src/buddy/console_assets``.
When that bundle is absent the console answers with an honest setup page and the JSON
API keeps working, so the backend can be verified without a frontend build.

Honesty boundary: this is a same-user local service. The HTTP origin and CSRF checks protect
against other browser origins and casual cross-site requests; they do not claim OS
isolation from a full-shell process running as the same user.
"""
from __future__ import annotations

import json
import hmac
import mimetypes
import os
import re
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import BoardError
from .console_sessions import BrowserSession, ConsoleSessions, ENTRY_SECONDS, COOKIE_SECONDS, READ_OPERATIONS
from .service import call_operation

MAX_BODY_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
SESSION_COOKIE = "buddy_console_session"
CSRF_HEADER = "X-Buddy-CSRF"
ACCESS_OPERATIONS = frozenset({"console_access_set", "console_logout", "console_session_revoke"})

#: Exact parameter whitelist of the read-only task history route. It is forwarded to
#: the same named ``task_list`` operation the CLI and C-Two use; an unknown or
#: repeated parameter is refused instead of being ignored or silently collapsed.
TASK_HISTORY_PARAMETERS = frozenset(
    {"limit", "offset", "state", "adapter", "before", "rootsOnly", "query", "projectId", "hostId", "filter"}
)
TASK_HISTORY_INTEGERS = frozenset({"limit", "offset"})
TASK_HISTORY_BOOLEANS = frozenset({"rootsOnly"})
_QUERY_INTEGER = re.compile(r"^[0-9]{1,7}$")
_TRUE_VALUES = frozenset({"true", "1"})
_FALSE_VALUES = frozenset({"false", "0"})


def parse_task_history_query(query: str) -> dict:
    """Parse one task-history query string into typed ``task_list`` parameters.

    Only the exact whitelist above is accepted; a repeated parameter is an error so
    a caller can never smuggle two values past a checker that validated one, and
    booleans/integers are converted here so the store receives real JSON types.
    """
    entries = parse_qs(query, keep_blank_values=True)
    params: dict[str, Any] = {}
    for name in sorted(entries):
        if name not in TASK_HISTORY_PARAMETERS:
            raise BoardError("INVALID_ARGUMENT", f"Unknown task history parameter: {name}", field=name)
        values = entries[name]
        if len(values) != 1:
            raise BoardError("INVALID_ARGUMENT", f"Duplicate task history parameter: {name}", field=name)
        value = values[0]
        if name in TASK_HISTORY_BOOLEANS:
            lowered = value.lower()
            if lowered not in _TRUE_VALUES | _FALSE_VALUES:
                raise BoardError("INVALID_ARGUMENT", f"{name} must be true or false", field=name)
            params[name] = lowered in _TRUE_VALUES
        elif name in TASK_HISTORY_INTEGERS:
            if not _QUERY_INTEGER.match(value):
                raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonnegative integer", field=name)
            params[name] = int(value)
        else:
            if not value:
                raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonempty value", field=name)
            params[name] = value
    return params


#: Exact parameter whitelists of the read-only objective routes; they reach the
#: same named ``objective_list``/``objective_timeline`` operations as the CLI.
OBJECTIVE_LIST_PARAMETERS = frozenset({"limit", "before", "projectId", "hostId", "query", "filter"})
OBJECTIVE_TIMELINE_PARAMETERS = frozenset({"limit", "query", "filter"})
_OBJECTIVE_ID = re.compile(r"^(obj-[A-Za-z0-9-]{1,120}|run:[A-Za-z0-9._:-]{1,128})$")


def parse_objective_query(query: str, allowed: frozenset[str]) -> dict:
    """Parse one objective route query string; unknown or repeated names are refused."""
    entries = parse_qs(query, keep_blank_values=True)
    params: dict[str, Any] = {}
    for name in sorted(entries):
        if name not in allowed:
            raise BoardError("INVALID_ARGUMENT", f"Unknown objective parameter: {name}", field=name)
        values = entries[name]
        if len(values) != 1:
            raise BoardError("INVALID_ARGUMENT", f"Duplicate objective parameter: {name}", field=name)
        value = values[0]
        if name == "limit":
            if not _QUERY_INTEGER.match(value):
                raise BoardError("INVALID_ARGUMENT", "limit must be a nonnegative integer", field=name)
            params[name] = int(value)
        else:
            if not value:
                raise BoardError("INVALID_ARGUMENT", f"{name} must be a nonempty value", field=name)
            params[name] = value
    return params


#: The only operations a browser may invoke. Everything else — including any raw SQL
#: or an unwrapped store call — is not reachable through this surface.
#:
#: Evaluation maintenance is Harness-owned: the console may read the publication log
#: (``evaluation_history``) but never ``evaluation_prepare``, never a maintenance or
#: selection model call, and no direct evidence entry. The ordinary writer gate
#: permits authenticated user policy patches; assessment cards stay Harness-owned.
CONSOLE_OPERATIONS = (
    "capabilities",
    "harness_verify",
    "harness_set",
    "quota_redetect",
    "evaluation_write_begin",
    "evaluation_write_renew",
    "user_policy_publish",
    "evaluation_write_abort",
    "evaluation_history",
    "selection_get",
    "selection_list",
    "model_catalog_refresh",
    "model_profiles",
    "workflow_get",
    "objective_stop",
    "storage_plan",
    "storage_apply",
)

#: Fields the console server owns. A browser that supplies one is refused: console
#: authority is the session this server registered with the service, never a JSON
#: value a caller can choose.
BROWSER_REFUSED_FIELDS = frozenset({"consoleAuthority", "userOverride", "consoleUser", "adminOverride"})

STATUS_BY_CODE = {
    "INVALID_ARGUMENT": 400,
    "INVALID_RESPONSE": 502,
    "STATE_INTEGRITY": 500,
    "METHOD_NOT_FOUND": 404,
    "NOT_FOUND": 404,
    "UNAUTHORIZED": 401,
    "FORBIDDEN": 403,
    "CONFLICT": 409,
    "REVISION_CONFLICT": 409,
    "STALE_GENERATION": 409,
    "WRITER_NOT_ACTIVE": 409,
    "ALREADY_PUBLISHED": 409,
    "TABLE_BUSY": 409,
    "GATE_CLOSED": 409,
    "NOT_READY": 409,
    "SHUTDOWN_UNCONFIRMED": 409,
    "PREPARATION_CONFLICT": 409,
    "SNAPSHOT_CHANGED": 409,
    "TURN_INPUT_MISMATCH": 409,
    "WORKSPACE_INVALID": 409,
    "WORKSPACE_CONFLICT": 409,
    "MESSAGE_TOO_LARGE": 413,
    "UNSUPPORTED": 501,
    "UNSUPPORTED_ADAPTER": 501,
    "CATALOG_UNAVAILABLE": 503,
    "CATALOG_INVALID": 502,
    "ADAPTER_UNAVAILABLE": 503,
    "SERVICE_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
    "CONSOLE_ENTRY_EXPIRED": 410,
    "CONSOLE_SESSION_EXPIRED": 401,
    "CONSOLE_LIMIT": 429,
}

#: Static assets the console is willing to serve. Anything else is a 404, so an
#: unrelated file dropped into the asset directory is not reachable by URL.
ASSET_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
    ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
}

SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")

CONTENT_SECURITY_POLICY = (
    "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
    "font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
)

SETUP_PAGE = """<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Buddy console — assets not built</title>
<style>body{{font:16px system-ui,sans-serif;max-width:720px;margin:10vh auto;padding:0 24px;color:#20332e;background:#f4f7f5}}
code{{background:#e5ece8;padding:2px 6px;border-radius:4px}}pre{{background:#fff;padding:16px;border-radius:10px;overflow-wrap:anywhere;white-space:pre-wrap}}</style>
<h1>Console assets are not built</h1>
<p>This Buddy build has no <code>console_assets/index.html</code>, so there is no React bundle to serve.
Nothing was mocked or invented in its place; the JSON API below is live and can be read directly:</p>
<pre>{api}</pre>
<p>Build the frontend into <code>src/buddy/console_assets/</code> (the frontend workstream owns
that directory), then reload this page. Running the backend never requires npm.</p>
<p>{detail}</p></html>"""


def default_assets_dir() -> Path:
    return Path(__file__).resolve().parent / "console_assets"


def assets_ready(assets_dir: Path | None = None) -> bool:
    root = Path(assets_dir) if assets_dir is not None else default_assets_dir()
    return (root / "index.html").is_file()


class _ConsoleHTTPServer(ThreadingHTTPServer):
    """A loopback server that never dumps a client-abort traceback into the log."""

    daemon_threads = True

    def handle_error(self, request, client_address):  # pragma: no cover - connection hygiene
        import sys

        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError, OSError)):
            return
        sys.stderr.write(f"buddy-console: {type(error).__name__}\n")


class Console:
    """One loopback console instance owned by the service."""

    def __init__(self, store, service, *, assets_dir: Path | str | None = None, host: str = "127.0.0.1", port: int | None = None, clock=time.time):
        self.store = store
        self.service = service
        self.assets_dir = Path(assets_dir) if assets_dir is not None else default_assets_dir()
        if host != "127.0.0.1":
            raise BoardError("INVALID_ARGUMENT", "Console must bind numeric loopback 127.0.0.1")
        self.host = host
        self._persist_port = port is None
        from .launcher import read_private
        self._settings = read_private(self.store.directory / "console-settings.json") or {}
        self.require_login = self._settings.get("requireLogin", False)
        self.access_revision = self._settings.get("revision", 0)
        if type(self.require_login) is not bool or type(self.access_revision) is not int or self.access_revision < 0:
            raise BoardError("CONSOLE_SESSION_STORE", "Invalid console access settings")
        try:
            if port is None:
                value = os.environ.get("BUDDY_CONSOLE_PORT", self._settings.get("port", 49637))
                self.port = int(value)
                if isinstance(value, bool):
                    raise ValueError()
            else:
                self.port = port
            if isinstance(self.port, bool) or not isinstance(self.port, int) or not 0 <= self.port <= 65535:
                raise ValueError()
        except (ValueError, TypeError, KeyError) as error:
            raise BoardError("INVALID_ARGUMENT", "BUDDY_CONSOLE_PORT must be an integer port") from error
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()
        self._clock = clock
        self._sessions: ConsoleSessions | None = None
        self._local_session: BrowserSession | None = None
        self._expiry_stop: threading.Event | None = None
        self.origin: str | None = None

    # -- lifecycle -----------------------------------------------------------
    def start(self, *, issue_ticket: bool = True) -> dict:
        with self._lock:
            already_running = self._server is not None
            if not already_running:
                self._sessions = ConsoleSessions(clock=self._clock, path=self.store.directory / "console-sessions.json")
                self._local_session = self._new_local_session()
                try:
                    server = _ConsoleHTTPServer((self.host, self.port), self._handler())
                except OSError as error:
                    self._sessions = None
                    raise BoardError("CONSOLE_PORT_IN_USE", f"Cannot bind 127.0.0.1:{self.port}; free the port or configure BUDDY_CONSOLE_PORT") from error
                if self._persist_port:
                    try:
                        from .launcher import write_private
                        write_private(self.store.directory / "console-settings.json", {**self._settings, "port": self.port})
                    except Exception:
                        server.server_close()
                        self._sessions = None
                        raise
                self._server = server
                self.origin = f"http://{self.host}:{server.server_address[1]}"
                self._thread = threading.Thread(target=server.serve_forever, name="buddy-console", daemon=True)
                self._thread.start()
                stop = threading.Event()
                self._expiry_stop = stop
                threading.Thread(target=self._expire_loop, args=(self._sessions.console_id, stop),
                                 name="buddy-console-expiry", daemon=True).start()
            self._expire_sessions()
            if not issue_ticket:
                return self.status()
            ticket = self._sessions.issue() if self.require_login else None
            return {
                "url": f"{self.origin}/launch/{ticket}" if ticket else f"{self.origin}/",
                "consoleId": self._sessions.console_id,
                "expiresAt": (datetime.fromtimestamp(self._clock(), timezone.utc) + timedelta(seconds=ENTRY_SECONDS)).isoformat() if ticket else None,
                "requireLogin": self.require_login,
                "readOnly": False,
                "running": True,
                "alreadyRunning": already_running,
                "assetsBuilt": assets_ready(self.assets_dir),
            }

    def close(self, expected_console_id: str | None = None) -> dict:
        with self._lock:
            console_id = self._sessions.console_id if self._sessions else None
            if expected_console_id is not None and console_id is not None and expected_console_id != console_id:
                return {"closed": False, "reason": "replaced", "consoleId": console_id}
            server, self._server = self._server, None
            if self._expiry_stop is not None:
                self._expiry_stop.set()
                self._expiry_stop = None
            if self._sessions is not None:
                for session in self._sessions.sessions.values():
                    self.service.revoke_console_authority(session.cookie)
            if self._local_session is not None:
                self.service.revoke_console_authority(self._local_session.cookie)
            self._local_session = None
            self._sessions = None
            self.origin = None
        # A handler waiting for the ownership lock must not deadlock shutdown.
        if server is not None:
            server.shutdown()
            server.server_close()
        return {"closed": True, "consoleId": console_id}

    def _expire_sessions(self) -> None:
        if self._sessions is not None:
            for session in self._sessions.expire():
                self.service.revoke_console_authority(session.cookie)

    def expire(self, console_id: str) -> None:
        """Expire from a private timer; status and rejected HTTP never renew activity."""
        with self._lock:
            if self._sessions is None or self._sessions.console_id != console_id:
                return
            self._expire_sessions()

    def _expire_loop(self, console_id: str, stop: threading.Event) -> None:
        while not stop.wait(5):
            self.expire(console_id)

    def status(self) -> dict:
        with self._lock:
            return {
                "consoleId": self._sessions.console_id if self._sessions else None,
                "running": self._server is not None,
                "sessionCount": len(self._sessions.sessions) if self._sessions else 0,
                "idleTimeoutSeconds": None,
                "sessionLifetimeSeconds": None,
                "requireLogin": self.require_login,
                "baseUrl": self.origin + "/" if self.origin else None,
                "assetsBuilt": assets_ready(self.assets_dir),
                "assetsDir": str(self.assets_dir),
            }

    # -- local access settings ----------------------------------------------
    def _new_local_session(self) -> BrowserSession:
        return BrowserSession(secrets.token_hex(12), secrets.token_urlsafe(32), secrets.token_urlsafe(32), self._clock())

    def _authenticate(self, session: BrowserSession) -> None:
        if self._sessions is None:
            raise BoardError("CONSOLE_SESSION_EXPIRED", "The console is closed")
        if not self.require_login and session is self._local_session:
            return
        if self.require_login and session is not self._local_session:
            self._sessions.authenticate(session.id, session.cookie)
            return
        raise BoardError("CONSOLE_SESSION_EXPIRED", "Console access changed; reload or run buddy console")

    def access_view(self, session: BrowserSession | None = None) -> dict:
        return {"requireLogin": self.require_login, "revision": self.access_revision,
                "sessions": [{"id": item.id, "lastSeen": item.last_seen, "current": item is session}
                             for item in self._sessions.sessions.values()] if self.require_login and self._sessions else []}

    def access_command(self, operation: str, params: dict, session: BrowserSession) -> tuple[dict, BrowserSession | None]:
        """Console-local authority only; no new service operation or database schema."""
        with self._lock:
            self._authenticate(session)
            if not isinstance(params, dict):
                raise BoardError("INVALID_ARGUMENT", "params must be an object")
            allowed = {"requireLogin", "expectedRevision"} if operation == "console_access_set" else {"sessionId"} if operation == "console_session_revoke" else set()
            if operation not in ACCESS_OPERATIONS or set(params) != allowed:
                raise BoardError("INVALID_ARGUMENT", "Invalid console access fields")
            sessions = self._sessions
            if operation == "console_access_set":
                required, revision = params["requireLogin"], params["expectedRevision"]
                if type(required) is not bool or type(revision) is not int or revision < 0:
                    raise BoardError("INVALID_ARGUMENT", "requireLogin must be boolean and expectedRevision a nonnegative integer")
                if revision != self.access_revision:
                    raise BoardError("REVISION_CONFLICT", "Console access settings changed; refresh before saving")
                if required == self.require_login:
                    return self.access_view(session), session
                from .launcher import write_private
                previous = dict(sessions.sessions)
                settings = {**self._settings, "port": self.port, "requireLogin": required, "revision": revision + 1}
                try:
                    revoked = sessions.revoke()
                    replacement = sessions.create() if required else self._new_local_session()
                    write_private(self.store.directory / "console-settings.json", settings)
                except Exception:
                    sessions.sessions = previous
                    sessions._save()
                    raise
                for item in revoked:
                    self.service.revoke_console_authority(item.cookie)
                if self._local_session is not None:
                    self.service.revoke_console_authority(self._local_session.cookie)
                sessions.entries.clear()
                self._settings = settings
                self.require_login, self.access_revision = required, revision + 1
                self._local_session = None if required else replacement
                self.service.register_console_authority(replacement.cookie, replacement.id)
                return self.access_view(replacement), replacement
            if not self.require_login:
                raise BoardError("CONFLICT", "Login is disabled; there is no browser login to revoke")
            target = session.id if operation == "console_logout" else params["sessionId"]
            if not isinstance(target, str) or re.fullmatch(r"[0-9a-f]{24}", target) is None:
                raise BoardError("INVALID_ARGUMENT", "sessionId must identify a console login")
            for item in sessions.revoke(target):
                self.service.revoke_console_authority(item.cookie)
            current = None if target == session.id else session
            return self.access_view(current), current

    # -- dispatch ------------------------------------------------------------
    def command(self, operation: str, params: dict, *, session: BrowserSession | None = None) -> dict:
        # Serialize authentication and mutation with console close/session expiry.
        # Settings concurrency is governed by their existing revision checks.
        with self._lock:
            if session is not None:
                self._authenticate(session)
            if operation in CONSOLE_OPERATIONS and operation not in READ_OPERATIONS:
                if session is None:
                    raise BoardError("CONSOLE_SESSION_EXPIRED", "An authenticated console session is required")
            return self._command(operation, params, session=session)

    def _command(self, operation: str, params: dict, *, session: BrowserSession | None) -> dict:
        if operation not in CONSOLE_OPERATIONS:
            raise BoardError(
                "METHOD_NOT_FOUND",
                f"Operation {operation!r} is not available through the console; only validated board operations are",
            )
        if not isinstance(params, dict):
            raise BoardError("INVALID_ARGUMENT", "params must be an object")
        params = dict(params)
        refused = sorted(set(params) & BROWSER_REFUSED_FIELDS)
        if refused:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"{refused[0]} is not accepted from the browser; console authority is attached by the console server",
            )
        if session is not None:
            params["consoleAuthority"] = {"sessionId": session.cookie}
        return call_operation(self.service, operation, params)

    def snapshot(self, session: BrowserSession) -> dict:
        with self._lock:
            self._authenticate(session)
            snapshot = call_operation(self.service, "console_snapshot", {})
            snapshot["csrfToken"] = session.csrf
            snapshot["consoleSession"] = session.view()
            snapshot["consoleAccess"] = self.access_view(session)
            snapshot["capabilities"]["consoleAssets"] = assets_ready(self.assets_dir)
            return snapshot

    # -- HTTP ----------------------------------------------------------------
    def _handler(self):
        console = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "buddy-console"
            #: A stalled local client must not pin a console thread forever.
            timeout = 30

            def log_message(self, *_args):  # noqa: D102 - silence access logging
                return

            # -- response helpers ------------------------------------------
            def _security_headers(self) -> None:
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)

            def _send(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                if self.close_connection:
                    self.send_header("Connection", "close")
                if getattr(self, "clear_cookie", False):
                    self.send_header("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict")
                elif console.require_login and getattr(self, "browser_session", None) is not None:
                    self.send_header("Set-Cookie", f"{SESSION_COOKIE}={self.browser_session.cookie}; Path=/; Max-Age={COOKIE_SECONDS}; HttpOnly; SameSite=Strict")
                self._security_headers()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, status: int, value: Any) -> None:
                body = json.dumps(value, ensure_ascii=False).encode()
                self._send(status, body, "application/json; charset=utf-8")

            def _error(self, status: int, code: str, message: str, details: dict | None = None) -> None:
                # Refusals may precede body consumption. Never reuse a connection
                # whose unread body could be parsed as a subsequent request.
                self.close_connection = True
                error: dict[str, Any] = {"code": code, "message": message}
                if details:
                    error["details"] = details
                self._json(status, {"ok": False, "error": error})

            def _cookie(self) -> str:
                raw = self.headers.get("Cookie") or ""
                values = []
                for part in raw.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == SESSION_COOKIE:
                        values.append(value)
                # Do not guess which of two same-named cookies is authoritative.
                return values[0] if len(values) == 1 else ""

            # -- trust checks ----------------------------------------------
            def _trusted_request(self) -> bool:
                self.browser_session = None
                self.clear_cookie = False
                if (console.store.directory / "upgrade.json").exists():
                    self._error(503, "UPGRADE_IN_PROGRESS", "Upgrade verification is in progress; retry shortly")
                    return False
                expected_host = f"{console.host}:{console._server.server_address[1]}" if console._server else ""
                if self.headers.get_all("Host", []) != [expected_host]:
                    self._error(403, "FORBIDDEN", "Host is not the loopback console origin")
                    return False
                origin = self.headers.get("Origin")
                if origin is not None and (origin != console.origin or len(self.headers.get_all("Origin")) != 1):
                    self._error(403, "FORBIDDEN", "Origin is not the console origin")
                    return False
                site = self.headers.get("Sec-Fetch-Site")
                if site is not None and (site not in ("same-origin", "none") or len(self.headers.get_all("Sec-Fetch-Site")) != 1):
                    self._error(403, "FORBIDDEN", "Cross-site request refused")
                    return False
                return True

            def _write_trusted(self) -> bool:
                """Writes require exact Origin and the admitted access mode’s CSRF value."""
                if self.headers.get("Origin") != console.origin:
                    self._error(403, "FORBIDDEN", "A write requires the exact console Origin")
                    return False
                if not hmac.compare_digest((self.headers.get(CSRF_HEADER) or "").encode(), self.browser_session.csrf.encode()):
                    self._error(
                        403,
                        "FORBIDDEN",
                        "A write requires the current bootstrap CSRF header; "
                        "open a fresh entry if this browser session is unavailable",
                    )
                    return False
                return True

            def _path(self) -> str:
                return unquote(urlsplit(self.path).path)

            def _authenticated_path(self, path: str) -> str | None:
                with console._lock:
                    sessions = console._sessions
                    if sessions is None:
                        self._error(401, "CONSOLE_SESSION_EXPIRED", "The console is closed; run buddy console again")
                        return None
                    try:
                        console._expire_sessions()
                        self.browser_session = sessions.authenticate(None, self._cookie()) if console.require_login else console._local_session
                        console.service.register_console_authority(self.browser_session.cookie, self.browser_session.id)
                    except BoardError as error:
                        self._board_error(error)
                        return None
                    self.session_path = sessions.path(self.browser_session)
                    return path

            def _launch(self, path: str) -> None:
                ticket = path.removeprefix("/launch/")
                try:
                    with console._lock:
                        sessions = console._sessions
                        if sessions is None or re.fullmatch(r"[A-Za-z0-9_-]{43}", ticket) is None:
                            raise BoardError("CONSOLE_ENTRY_EXPIRED", "This entry link is unavailable; run buddy console again")
                        console._expire_sessions()
                        session = sessions.redeem(ticket, self._cookie())
                        console.service.register_console_authority(session.cookie, session.id)
                        target = sessions.path(session)
                except BoardError as error:
                    # A human following a stale entry needs an actionable page, not
                    # another reusable credential or a raw JSON error.
                    body = ("<!doctype html><html lang=zh-CN><meta charset=utf-8>"
                            "<meta name=viewport content='width=device-width,initial-scale=1'>"
                            "<title>Buddy 控制台入口不可用</title><h1>控制台入口已失效或暂不可用</h1>"
                            "<p>请重新运行 <code>buddy console</code> 打开控制台。后台任务不受影响。</p></html>").encode()
                    return self._send(STATUS_BY_CODE.get(error.code, 400), body, "text/html; charset=utf-8")
                self.send_response(303)
                self.send_header("Location", target)
                self.send_header("Set-Cookie", f"{SESSION_COOKIE}={session.cookie}; Path=/; Max-Age={COOKIE_SECONDS}; HttpOnly; SameSite=Strict")
                self.send_header("Content-Length", "0")
                self.send_header("Cache-Control", "no-store")
                self._security_headers()
                self.end_headers()

            # -- routes ----------------------------------------------------
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                if not self._trusted_request():
                    return
                path = self._path()
                if path.startswith("/launch/"):
                    return self._launch(path)
                relative = self._authenticated_path(path)
                if relative is None:
                    return
                if relative in ("", "/"):
                    return self._page()
                if relative == "/api/console":
                    try:
                        snapshot = console.snapshot(self.browser_session)
                    except BoardError as error:
                        return self._board_error(error)
                    except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                        return self._error(500, "INTERNAL_ERROR", "The console could not read the snapshot")
                    return self._json(200, snapshot)
                if relative == "/api/tasks":
                    return self._task_history()
                if relative == "/api/objectives":
                    return self._objective_read("objective_list", {}, OBJECTIVE_LIST_PARAMETERS)
                if relative.startswith("/api/objectives/") and relative.endswith("/timeline"):
                    # The request path is already percent-decoded once by _path().
                    identifier = relative[len("/api/objectives/"):-len("/timeline")]
                    if not _OBJECTIVE_ID.match(identifier):
                        return self._error(404, "NOT_FOUND", "Not found")
                    return self._objective_read(
                        "objective_timeline", {"objectiveId": identifier}, OBJECTIVE_TIMELINE_PARAMETERS
                    )
                if relative.startswith("/api/tasks/"):
                    return self._task(relative[len("/api/tasks/"):])
                if relative.startswith("/api/"):
                    return self._error(404, "NOT_FOUND", "Not found")
                return self._asset(relative)

            def _page(self) -> None:
                index = console.assets_dir / "index.html"
                if not index.is_file():
                    body = SETUP_PAGE.format(
                        api=f"{console.origin}{self.session_path}api/console",
                        detail=f"Asset directory: {console.assets_dir}",
                    ).encode()
                    return self._send(503, body, "text/html; charset=utf-8")
                try:
                    body = index.read_bytes()
                except OSError:
                    return self._error(500, "INTERNAL_ERROR", "The console page could not be read")
                self._send(200, body, "text/html; charset=utf-8", cache="no-cache")

            def _asset(self, relative: str) -> None:
                target = console.resolve_asset(relative)
                if target is None:
                    # Standard single-page-app fallback: an extension-less navigation
                    # path outside the asset and API prefixes serves the same index
                    # document. Directories, /assets and /api are never guessed.
                    first = Path(relative.lstrip("/")).parts[0] if relative.strip("/") else ""
                    if (
                        not relative.endswith("/")
                        and "." not in Path(relative).name
                        and first not in ("assets", "api")
                    ):
                        return self._page()
                    return self._error(404, "NOT_FOUND", "Not found")
                try:
                    body = target.read_bytes()
                except OSError:
                    return self._error(500, "INTERNAL_ERROR", "The console asset could not be read")
                content_type = ASSET_TYPES.get(target.suffix.lower())
                if content_type is None:
                    content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                cache = "no-cache" if target.suffix.lower() == ".html" else "private, max-age=31536000, immutable"
                self._send(200, body, content_type, cache=cache)

            def _task_history(self) -> None:
                """Read-only task history over the same named ``task_list`` operation.

                Reads authenticate the session cookie and exact-origin trust boundary;
                they require no write authority or publication lease.
                """
                try:
                    params = parse_task_history_query(urlsplit(self.path).query)
                except BoardError as error:
                    return self._board_error(error)
                try:
                    result = call_operation(console.service, "task_list", params)
                except BoardError as error:
                    return self._board_error(error)
                except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                    return self._error(500, "INTERNAL_ERROR", "The task history could not be read")
                self._json(200, result)

            def _objective_read(self, operation: str, fixed: dict, allowed: frozenset[str]) -> None:
                """Authenticated read-only objective browsing; no lease, write authority or model call."""
                try:
                    params = {**parse_objective_query(urlsplit(self.path).query, allowed), **fixed}
                    result = call_operation(console.service, operation, params)
                except BoardError as error:
                    return self._board_error(error)
                except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                    return self._error(500, "INTERNAL_ERROR", "The work objectives could not be read")
                self._json(200, result)

            def _task(self, run_id: str) -> None:
                if not re.match(r"^[A-Za-z0-9._:-]{1,128}$", run_id):
                    return self._error(404, "NOT_FOUND", "Not found")
                try:
                    view = call_operation(console.service, "task_get", {"runId": run_id})["task"]
                    if view.get("resultAvailable"):
                        view = {**view, **call_operation(console.service, "task_result", {"runId": run_id})}
                except BoardError as error:
                    return self._board_error(error)
                except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                    return self._error(500, "INTERNAL_ERROR", "The task could not be read")
                self._json(200, view)

            def _board_error(self, error: BoardError) -> None:
                status = STATUS_BY_CODE.get(error.code, 400)
                self._error(status, error.code, error.message, error.details or None)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                if not self._trusted_request():
                    return
                path = self._path()
                relative = self._authenticated_path(path)
                if relative is None:
                    return
                if relative != "/api/command":
                    return self._error(404, "NOT_FOUND", "Not found")
                if not self._write_trusted():
                    return
                body = self._read_body()
                if body is None:
                    return
                try:
                    value = json.loads(body)
                except ValueError:
                    return self._error(400, "INVALID_ARGUMENT", "The request body is not valid JSON")
                if not isinstance(value, dict):
                    return self._error(400, "INVALID_ARGUMENT", "The request body must be a JSON object")
                unknown = sorted(set(value) - {"operation", "params"})
                if unknown:
                    return self._error(400, "INVALID_ARGUMENT", f"Unknown command field: {unknown[0]}")
                operation = value.get("operation")
                if not isinstance(operation, str) or not operation:
                    return self._error(400, "INVALID_ARGUMENT", "operation is required")
                try:
                    if operation in ACCESS_OPERATIONS:
                        result, self.browser_session = console.access_command(operation, value.get("params", {}), self.browser_session)
                        self.clear_cookie = self.browser_session is None or not console.require_login
                    else:
                        result = console.command(operation, value.get("params", {}), session=self.browser_session)
                except BoardError as error:
                    return self._board_error(error)
                except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                    return self._error(500, "INTERNAL_ERROR", "The command failed inside the service")
                self._json(200, {"ok": True, "result": result})

            def _read_body(self) -> bytes | None:
                if (self.headers.get("Transfer-Encoding") or "").strip():
                    self._error(411, "INVALID_ARGUMENT", "A bounded Content-Length body is required")
                    return None
                raw = self.headers.get("Content-Length")
                try:
                    length = int(raw) if raw is not None else -1
                except ValueError:
                    length = -1
                if length < 0:
                    self._error(411, "INVALID_ARGUMENT", "A bounded Content-Length body is required")
                    return None
                if length == 0 or length > MAX_BODY_BYTES:
                    self._error(413, "MESSAGE_TOO_LARGE", f"The request body must be 1..{MAX_BODY_BYTES} bytes")
                    return None
                return self.rfile.read(length)

            def do_OPTIONS(self):  # noqa: N802 - BaseHTTPRequestHandler API
                self._error(405, "METHOD_NOT_FOUND", "Method not allowed")

            def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
                self._error(405, "METHOD_NOT_FOUND", "Method not allowed")

            def do_DELETE(self):  # noqa: N802 - BaseHTTPRequestHandler API
                self._error(405, "METHOD_NOT_FOUND", "Method not allowed")

        return Handler

    # -- assets --------------------------------------------------------------
    def resolve_asset(self, relative: str) -> Path | None:
        """Resolve one static asset, refusing traversal, symlinks and directories."""
        value = relative.lstrip("/")
        if not value or "\0" in value or "\\" in value:
            return None
        parts = value.split("/")
        if any(part in ("", ".", "..") or not SAFE_SEGMENT.match(part) for part in parts):
            return None
        suffix = Path(parts[-1]).suffix.lower()
        if suffix not in ASSET_TYPES:
            return None
        root = self.assets_dir.resolve()
        candidate = root.joinpath(*parts)
        try:
            resolved = candidate.resolve()
            resolved.relative_to(root)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        try:
            if resolved.stat().st_size > MAX_ASSET_BYTES:
                return None
        except OSError:
            return None
        return resolved


__all__ = ["Console", "CONSOLE_OPERATIONS", "assets_ready", "default_assets_dir"]
