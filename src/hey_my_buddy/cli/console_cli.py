"""CLI-only console entry: local booleans, browser launch and fenced waits.

``hey-my-buddy console`` keeps the canonical single-JSON-argument form. This module owns only
the parts of that command that live in the CLI process: validating and stripping the
``browser``/``wait`` booleans, launching the default browser through Python's
:mod:`webbrowser` API, and waiting for the exact console instance that was opened.
Lifecycle, launch tickets and browser sessions stay with the daemon: nothing here mints
a credential, launches a browser on the daemon's behalf, or cold-starts a service while
observing or closing a console. Only the first ``open`` may attach to (or start) one.
"""
from __future__ import annotations

from functools import partial
from pathlib import Path
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
FALLBACK_NOTICE = "hey-my-buddy console: the browser was not opened; entry URL: "

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
    ``status``/``close`` is refused here, before any browser launch or RPC.
    The service validates the close fence.
    """
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
        rpc_params["expectedConsoleId"] = params["expectedConsoleId"]
    return Request(action=action, rpc_params=rpc_params, browser=browser, wait=wait)


def _launch_browser(url: str, enabled: bool, opener: Callable[..., Any]) -> bool:
    """Launch the default browser once and report honestly; a failure is never raised."""
    if not enabled:
        return False
    try:
        return bool(opener(url, new=2))
    except Exception:  # noqa: BLE001 - the raw launch failure must not cross this boundary
        return False


def _non_autostart_client(state_dir: Path):
    """One observation-only board client: observing must never cold-start a daemon."""
    from ..protocol.client import BoardClient

    return BoardClient(state_dir, call=partial(transport._call_board_read_only, directory=state_dir), autostart=False)


def _observe_client(factory: Callable[[], Any] | None, state_dir: Path):
    """Build the read-only client used by every non-opening console action."""
    return factory() if factory is not None else _non_autostart_client(state_dir)


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
        running = status["running"]
        if not running:
            return {"status": "closed"}
        current = status["consoleId"]
        if current != console_id:
            return {"status": "replaced", "consoleId": current}
        sleep(poll_seconds)


def _close_after_interrupt(console_id: str, *, client) -> dict:
    """Ctrl-C requests only this console's fenced close: never a service stop or cancel."""
    try:
        closed = client.call("console", {"action": "close", "expectedConsoleId": console_id})
    except BoardError as error:
        return {
            "status": "interrupted",
            "close": {"closed": False, "error": {"code": error.code, "message": error.message}},
        }
    return {"status": "interrupted", "close": dict(closed)}


def run(
    params: Any,
    *,
    state_dir: Path,
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
        reply = _observe_client(client_factory, state_dir).call("console", request.rpc_params)
        return dict(reply)
    reply = (transport._call_service("console", request.rpc_params, state_dir)
             if call_service is None else call_service("console", request.rpc_params))
    result = dict(reply)
    opener = browser_open if browser_open is not None else webbrowser.open
    opened = _launch_browser(result["url"], request.browser, opener)
    result["browserOpened"] = opened
    if not request.wait:
        return result
    if not opened:
        print(f"{FALLBACK_NOTICE}{result['url']}", file=stderr if stderr is not None else sys.stderr)
    client = _observe_client(client_factory, state_dir)
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
    "FALLBACK_NOTICE",
    "LOCAL_OPTIONS",
    "POLL_SECONDS",
    "Request",
    "parse_request",
    "run",
    "wait_for_console",
]
