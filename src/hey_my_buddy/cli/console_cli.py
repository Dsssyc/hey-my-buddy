"""CLI-only console entry: local booleans, browser launch and fenced waits.

``buddy console`` keeps the canonical single-JSON-argument form. This module owns only
the parts of that command that live in the CLI process: validating and stripping the
``browser``/``wait`` booleans, launching the default browser through Python's
:mod:`webbrowser` API, and waiting for the exact console instance that was opened.
Lifecycle, launch tickets and browser sessions stay with the daemon: nothing here mints
a credential, launches a browser on the daemon's behalf, or cold-starts a service while
observing or closing a console. Only the first ``open`` may attach to (or start) one.
"""
from __future__ import annotations

import re
import sys
import time
import webbrowser
from typing import Any, Callable, NamedTuple

from ..protocol import transport
from ..errors import BoardError

#: The three actions of the one console lifecycle operation the service exposes.
CONSOLE_ACTIONS = ("open", "status", "close")
#: Booleans that belong to this CLI process and never reach the daemon.
LOCAL_OPTIONS = ("browser", "wait")
#: Exact per-action field whitelist. An unknown field is refused before any RPC.
ACTION_FIELDS = {
    "open": frozenset({"action", "browser", "wait"}),
    "status": frozenset({"action"}),
    "close": frozenset({"action", "expectedConsoleId"}),
}
#: Bounded observation interval while waiting for a console instance.
POLL_SECONDS = 2.0
#: stderr notice carrying the fallback entry URL while a wait blocks stdout.
FALLBACK_NOTICE = "buddy console: the browser was not opened; entry URL: "

#: A public console instance id is exactly 24 lowercase hex characters. An alternate
#: length, case or encoding is never normalized into one: the daemon contract is exact,
#: so this CLI refuses anything else instead of forwarding an unfenced guess.
CONSOLE_ID_RE = re.compile(r"[0-9a-f]{24}")
#: The one permitted entry URL: numeric loopback, an explicit valid port and exactly
#: an optional 43-character URL-safe launch ticket, with no userinfo, query, fragment, alternate
#: host or alternate scheme. ``webbrowser`` must never be pointed at anything else.
LAUNCH_URL_RE = re.compile(r"http://127\.0\.0\.1:([0-9]{1,5})/(?:launch/([A-Za-z0-9_-]{43}))?")


def is_console_id(value: Any) -> bool:
    """True only for the exact public console instance id shape."""
    return isinstance(value, str) and CONSOLE_ID_RE.fullmatch(value) is not None


def is_launch_url(value: Any) -> bool:
    """True only for the single permitted loopback launch URL shape."""
    if not isinstance(value, str):
        return False
    match = LAUNCH_URL_RE.fullmatch(value)
    if match is None:
        return False
    return 1 <= int(match.group(1)) <= 65535


class Request(NamedTuple):
    """One validated console command: the RPC parameters plus CLI-local choices."""

    action: str
    rpc_params: dict
    browser: bool
    wait: bool


def _local_boolean(params: dict, name: str, default: bool) -> bool:
    value = params.get(name, default)
    if not isinstance(value, bool):
        raise BoardError("INVALID_ARGUMENT", f"{name} must be a boolean")
    return value


def parse_request(params: Any) -> Request:
    """Validate one canonical ``console`` JSON argument and strip CLI-local fields.

    Strictness is deliberate: a wrong type, an unknown field or either local option on
    ``status``/``close`` is refused here, before any browser launch or RPC. A close
    fence is accepted only as the exact daemon identifier: no trimming, lowercasing or
    decoding, and an explicit ``null`` is refused rather than dropped.
    """
    if not isinstance(params, dict):
        raise BoardError("INVALID_ARGUMENT", "params must be a JSON object")
    action = params.get("action", "open")
    if not isinstance(action, str) or action not in CONSOLE_ACTIONS:
        raise BoardError("INVALID_ARGUMENT", "action must be 'open', 'status' or 'close'")
    for option in LOCAL_OPTIONS:
        if option in params and option not in ACTION_FIELDS[action]:
            raise BoardError(
                "INVALID_ARGUMENT",
                f"{option} is a local console option and is only valid for console open",
            )
    unknown = sorted(set(params) - ACTION_FIELDS[action])
    if unknown:
        raise BoardError("INVALID_ARGUMENT", f"Unknown console field: {unknown[0]}")
    rpc_params = {"action": action}
    browser = True
    wait = False
    if action == "open":
        browser = _local_boolean(params, "browser", True)
        wait = _local_boolean(params, "wait", False)
    if action == "close" and "expectedConsoleId" in params:
        expected = params["expectedConsoleId"]
        if not is_console_id(expected):
            raise BoardError(
                "INVALID_ARGUMENT",
                "expectedConsoleId must be exactly 24 lowercase hex characters",
            )
        rpc_params["expectedConsoleId"] = expected
    return Request(action=action, rpc_params=rpc_params, browser=browser, wait=wait)


def _launch_browser(url: str, enabled: bool, opener: Callable[..., Any]) -> bool:
    """Launch the default browser once and report honestly; a failure is never raised."""
    if not enabled:
        return False
    try:
        return bool(opener(url, new=2))
    except Exception:  # noqa: BLE001 - the raw launch failure must not cross this boundary
        return False


def _non_autostart_client():
    """One observation-only board client: observing must never cold-start a daemon."""
    from ..protocol.client import BoardClient

    return BoardClient(autostart=False)


def _observe_client(factory: Callable[[], Any] | None):
    """Build the read-only client used by every non-opening console action."""
    return (factory or _non_autostart_client)()


def _validated_open_reply(reply: Any) -> dict:
    """Validate the lifecycle open reply before anything local happens.

    Only the daemon's exact lifecycle answer may reach the browser or the wait: a
    malformed reply is ``INVALID_RESPONSE`` and launches nothing.
    """
    if not isinstance(reply, dict):
        raise BoardError("INVALID_RESPONSE", "The console did not return a lifecycle object")
    if not is_launch_url(reply.get("url")):
        raise BoardError("INVALID_RESPONSE", "The console did not return a valid loopback launch URL")
    if not is_console_id(reply.get("consoleId")):
        raise BoardError("INVALID_RESPONSE", "The console did not return a valid consoleId")
    if reply.get("running") is not True:
        raise BoardError("INVALID_RESPONSE", "The console did not report that it is running")
    return dict(reply)


def _unavailable(code: str, message: str) -> dict:
    return {"status": "unavailable", "error": {"code": code, "message": message}}


def wait_for_console(
    console_id: str, *, client, sleep: Callable[[float], None], poll_seconds: float = POLL_SECONDS
) -> dict:
    """Observe this exact console instance through a non-autostart client.

    The wait ends when the instance closes, when a different instance is current, or
    when it can no longer be observed: losing the service is never reported as a
    confirmed close. ``KeyboardInterrupt`` is left to the caller so it can fence the
    interruption with one console close.
    """
    while True:
        try:
            status = client.call("console", {"action": "status"})
        except BoardError as error:
            return _unavailable(error.code, error.message)
        if not isinstance(status, dict):
            return _unavailable("INVALID_RESPONSE", "The console status was not a JSON object")
        running = status.get("running")
        if not isinstance(running, bool):
            return _unavailable("INVALID_RESPONSE", "The console status did not report whether it is running")
        if not running:
            return {"status": "closed"}
        current = status.get("consoleId")
        if not is_console_id(current):
            return _unavailable("INVALID_RESPONSE", "The console status did not identify the running console")
        if current != console_id:
            return {"status": "replaced", "consoleId": current}
        sleep(poll_seconds)


def _close_rejection(reply: Any) -> str | None:
    """Why an interruption close reply cannot be reported honestly, or ``None``.

    ``closed`` must be a real boolean and a console that stayed open must say why;
    otherwise the reply is malformed and must not be presented as success. A replied
    reason (for example ``replaced``) is the honest replacement information.
    """
    if not isinstance(reply, dict):
        return "The console close was not a lifecycle object"
    if not isinstance(reply.get("closed"), bool):
        return "The console close did not report whether the console closed"
    if reply["closed"] is False:
        reason = reply.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return "The console close did not report why the console stayed open"
    return None


def _close_after_interrupt(console_id: str, *, client) -> dict:
    """Ctrl-C requests only this console's fenced close: never a service stop or cancel."""
    try:
        closed = client.call("console", {"action": "close", "expectedConsoleId": console_id})
    except BoardError as error:
        return {
            "status": "interrupted",
            "close": {"closed": False, "error": {"code": error.code, "message": error.message}},
        }
    rejection = _close_rejection(closed)
    if rejection is not None:
        return {
            "status": "interrupted",
            "close": {"closed": False, "error": {"code": "INVALID_RESPONSE", "message": rejection}},
        }
    return {"status": "interrupted", "close": dict(closed)}


def run(
    params: Any,
    *,
    credential: str | None = None,
    call_service: Callable[[str, dict], dict] | None = None,
    client_factory: Callable[[], Any] | None = None,
    browser_open: Callable[..., Any] | None = None,
    sleep: Callable[[float], None] | None = None,
    stderr=None,
) -> dict:
    """Run one canonical console command and return its single JSON result object.

    ``call_service`` drives only the first ``open``: that RPC is the one place where a
    cold start is allowed. Every status, wait poll and close goes through
    ``client_factory`` (a non-autostart :class:`~hey_my_buddy.protocol.client.BoardClient` by default),
    so observing or closing a console never starts an absent service.
    """
    if credential is not None:
        raise BoardError(
            "FORBIDDEN",
            "An attempt-scoped credential cannot open, observe or close the console; "
            "console authority belongs to the authenticated local CLI session",
        )
    request = parse_request(params)
    if request.action != "open":
        reply = _observe_client(client_factory).call("console", request.rpc_params)
        if not isinstance(reply, dict):
            raise BoardError("INVALID_RESPONSE", "The console did not return a lifecycle object")
        return dict(reply)
    call = call_service if call_service is not None else transport.call_service
    reply = call("console", request.rpc_params)
    result = _validated_open_reply(reply)
    opener = browser_open if browser_open is not None else webbrowser.open
    opened = _launch_browser(result["url"], request.browser, opener)
    result["browserOpened"] = opened
    if not request.wait:
        return result
    if not opened:
        print(f"{FALLBACK_NOTICE}{result['url']}", file=stderr if stderr is not None else sys.stderr)
    client = _observe_client(client_factory)
    try:
        result["wait"] = wait_for_console(
            result["consoleId"], client=client, sleep=sleep if sleep is not None else time.sleep
        )
    except KeyboardInterrupt:
        result["wait"] = _close_after_interrupt(result["consoleId"], client=client)
    return result


__all__ = [
    "ACTION_FIELDS",
    "CONSOLE_ACTIONS",
    "CONSOLE_ID_RE",
    "FALLBACK_NOTICE",
    "LAUNCH_URL_RE",
    "LOCAL_OPTIONS",
    "POLL_SECONDS",
    "Request",
    "is_console_id",
    "is_launch_url",
    "parse_request",
    "run",
    "wait_for_console",
]
