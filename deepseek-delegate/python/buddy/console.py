"""Private writable local console: a loopback browser surface over board operations.

The console is a *separate* entrypoint from the read-only dashboard. It binds
``127.0.0.1`` on an ephemeral port, prints one unguessable private URL, validates the
exact loopback ``Host`` and same-``Origin`` on every request, and requires a private
HttpOnly session cookie plus a same-origin anti-CSRF header for writes. There is no
wildcard CORS and no generic SQL surface: every command is dispatched to the same
named, validated Python operation that C-Two and the CLI use.

The page is the built React/Vite bundle served from ``python/buddy/console_assets``.
When that bundle is absent the console answers with an honest setup page and the JSON
API keeps working, so the backend can be verified without a frontend build.

Honesty boundary: this is a same-user local service. The token and session protect
against other browser origins and casual cross-site requests; they do not claim OS
isolation from a full-shell process running as the same user.
"""
from __future__ import annotations

import json
import mimetypes
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from .errors import BoardError
from .service import call_operation

MAX_BODY_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024
SESSION_COOKIE = "buddy_console_session"
CSRF_HEADER = "X-Buddy-CSRF"

#: The only operations a browser may invoke. Everything else — including any raw SQL
#: or an unwrapped store call — is not reachable through this surface.
CONSOLE_OPERATIONS = (
    "evaluation_write_begin",
    "evaluation_write_renew",
    "evaluation_write_publish",
    "evaluation_write_abort",
    "evaluation_reader_begin",
    "evaluation_reader_release",
    "evaluation_evidence_record",
    "evaluation_maintain",
    "selection_request",
    "selection_get",
    "model_catalog_refresh",
    "task_cancel",
    "task_retry",
    "task_acknowledge",
)

STATUS_BY_CODE = {
    "INVALID_ARGUMENT": 400,
    "INVALID_RESPONSE": 502,
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
    "MESSAGE_TOO_LARGE": 413,
    "UNSUPPORTED": 501,
    "UNSUPPORTED_ADAPTER": 501,
    "CATALOG_UNAVAILABLE": 503,
    "CATALOG_INVALID": 502,
    "ADAPTER_UNAVAILABLE": 503,
    "SERVICE_UNAVAILABLE": 503,
    "INTERNAL_ERROR": 500,
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
<p>Build the frontend into <code>deepseek-delegate/python/buddy/console_assets/</code> (the frontend workstream owns
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

    def __init__(self, store, service, *, assets_dir: Path | str | None = None, host: str = "127.0.0.1"):
        self.store = store
        self.service = service
        self.assets_dir = Path(assets_dir) if assets_dir is not None else default_assets_dir()
        self.host = host
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.token = ""
        self.session = ""
        self.csrf = ""
        self.url: str | None = None
        self.origin: str | None = None

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> dict:
        with self._lock:
            if self._server is not None:
                return {
                    "url": self.url,
                    "readOnly": False,
                    "alreadyRunning": True,
                    "assetsBuilt": assets_ready(self.assets_dir),
                }
            # A closed-and-reopened console gets fresh credentials; a stale URL from a
            # previous session can never write to the new one.
            self.token = secrets.token_urlsafe(32)
            self.session = secrets.token_urlsafe(32)
            self.csrf = secrets.token_urlsafe(32)
            server = _ConsoleHTTPServer((self.host, 0), self._handler())
            self._server = server
            self.origin = f"http://{self.host}:{server.server_address[1]}"
            self.url = f"{self.origin}/{self.token}/"
            self._thread = threading.Thread(target=server.serve_forever, name="buddy-console", daemon=True)
            self._thread.start()
            return {
                "url": self.url,
                "readOnly": False,
                "alreadyRunning": False,
                "assetsBuilt": assets_ready(self.assets_dir),
                "note": (
                    "Private loopback console. It is a second browser surface over the same validated Python "
                    "operations, not a second state authority; closing it never stops a task. The token and session "
                    "separate browser origins - they are not OS isolation from a same-user process with shell access."
                ),
            }

    def close(self) -> dict:
        with self._lock:
            if self._server is not None:
                self._server.shutdown()
                self._server.server_close()
                self._server = None
            self.token = self.session = self.csrf = ""
            self.url = None
            return {"closed": True, "readOnly": False}

    def status(self) -> dict:
        return {
            "url": self.url,
            "readOnly": False,
            "running": self._server is not None,
            "assetsBuilt": assets_ready(self.assets_dir),
            "assetsDir": str(self.assets_dir),
        }

    # -- dispatch ------------------------------------------------------------
    def command(self, operation: str, params: dict) -> dict:
        if operation not in CONSOLE_OPERATIONS:
            raise BoardError(
                "METHOD_NOT_FOUND",
                f"Operation {operation!r} is not available through the console; only validated board operations are",
            )
        if not isinstance(params, dict):
            raise BoardError("INVALID_ARGUMENT", "params must be an object")
        return call_operation(self.service, operation, params)

    def snapshot(self) -> dict:
        snapshot = call_operation(self.service, "console_snapshot", {})
        # The bootstrap object is the snapshot itself; the CSRF value lives only in
        # this browser session's memory and is never part of a shared view.
        snapshot["csrfToken"] = self.csrf
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

            def _send_with_session(
                self, status: int, value: Any, content_type: str, *, cache: str = "no-store"
            ) -> None:
                """One JSON/HTML reply that also establishes the private session cookie."""
                body = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self._security_headers()
                self._session_cookie()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _send(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store") -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", cache)
                self._security_headers()
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(body)

            def _json(self, status: int, value: Any) -> None:
                body = json.dumps(value, ensure_ascii=False).encode()
                self._send(status, body, "application/json; charset=utf-8")

            def _error(self, status: int, code: str, message: str, details: dict | None = None) -> None:
                error: dict[str, Any] = {"code": code, "message": message}
                if details:
                    error["details"] = details
                self._json(status, {"ok": False, "error": error})

            def _session_cookie(self) -> None:
                if self._cookie() != console.session:
                    self.send_header(
                        "Set-Cookie",
                        f"{SESSION_COOKIE}={console.session}; Path=/{console.token}/; HttpOnly; SameSite=Strict",
                    )

            def _cookie(self) -> str:
                raw = self.headers.get("Cookie") or ""
                for part in raw.split(";"):
                    name, _, value = part.strip().partition("=")
                    if name == SESSION_COOKIE:
                        return value
                return ""

            # -- trust checks ----------------------------------------------
            def _trusted_request(self) -> bool:
                expected_host = f"{console.host}:{console._server.server_address[1]}" if console._server else ""
                if self.headers.get("Host") != expected_host:
                    self._error(403, "FORBIDDEN", "Host is not the loopback console origin")
                    return False
                origin = self.headers.get("Origin")
                if origin is not None and origin != console.origin:
                    self._error(403, "FORBIDDEN", "Origin is not the console origin")
                    return False
                site = self.headers.get("Sec-Fetch-Site")
                if site is not None and site not in ("same-origin", "none"):
                    self._error(403, "FORBIDDEN", "Cross-site request refused")
                    return False
                return True

            def _write_trusted(self) -> bool:
                """Writes require the console session and a same-origin CSRF header."""
                if self.headers.get("Origin") != console.origin:
                    self._error(403, "FORBIDDEN", "A write requires the exact console Origin")
                    return False
                if self.headers.get(CSRF_HEADER) != console.csrf or self._cookie() != console.session:
                    self._error(
                        403,
                        "FORBIDDEN",
                        "A write requires the private console session cookie and the bootstrap CSRF header; "
                        "reload the console to obtain both",
                    )
                    return False
                return True

            def _path(self) -> str:
                return unquote(urlsplit(self.path).path)

            def _under_prefix(self, path: str) -> str | None:
                prefix = f"/{console.token}"
                if console.token and (path == prefix or path.startswith(prefix + "/")):
                    return path[len(prefix):]
                return None

            # -- routes ----------------------------------------------------
            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                if not self._trusted_request():
                    return
                path = self._path()
                relative = self._under_prefix(path)
                if relative is None:
                    # The read-only dashboard token (or any other path) is simply not
                    # this surface; a write can never be smuggled through it.
                    return self._error(404, "NOT_FOUND", "Not found")
                if relative in ("", "/"):
                    return self._page()
                if relative == "/api/console":
                    try:
                        snapshot = console.snapshot()
                    except BoardError as error:
                        return self._board_error(error)
                    except Exception:  # noqa: BLE001 - no traceback crosses the boundary
                        return self._error(500, "INTERNAL_ERROR", "The console could not read the snapshot")
                    return self._send_with_session(200, snapshot, "application/json; charset=utf-8")
                if relative.startswith("/api/tasks/"):
                    return self._task(relative[len("/api/tasks/"):])
                if relative.startswith("/api/"):
                    return self._error(404, "NOT_FOUND", "Not found")
                return self._asset(relative)

            def _send_json_with_cookie(self, status: int, value: Any) -> None:
                body = json.dumps(value, ensure_ascii=False).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header("Cross-Origin-Opener-Policy", "same-origin")
                self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
                self._session_cookie()
                self.end_headers()
                self.wfile.write(body)

            def _page(self) -> None:
                index = console.assets_dir / "index.html"
                if not index.is_file():
                    body = SETUP_PAGE.format(
                        api=f"{console.origin}/{console.token}/api/console",
                        detail=f"Asset directory: {console.assets_dir}",
                    ).encode()
                    return self._send_with_session(503, body, "text/html; charset=utf-8")
                try:
                    body = index.read_bytes()
                except OSError:
                    return self._error(500, "INTERNAL_ERROR", "The console page could not be read")
                self._send_with_session(200, body, "text/html; charset=utf-8", cache="no-cache")

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
                cache = "no-cache" if target.suffix.lower() == ".html" else "public, max-age=31536000, immutable"
                self._send(200, body, content_type, cache=cache)

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
                relative = self._under_prefix(path)
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
                    result = console.command(operation, value.get("params") or {})
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
