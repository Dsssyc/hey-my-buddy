"""The native ACP session path: one client over one launched process."""
from __future__ import annotations

from pathlib import Path
import threading

from .connection import AcpConnection, stop_evidence
from .launch import launch, stop_after_spawn_failure

PROTOCOL_VERSION = 1

INITIALIZE_TIMEOUT = 30.0
SESSION_TIMEOUT = 60.0
CONFIG_TIMEOUT = 30.0
CLOSE_TIMEOUT = 30.0
PROMPT_TIMEOUT = 3600.0


class PermissionPolicy:
    """Answers only the agent's upgrade-permission requests; nothing else.

    The conservative default prefers ``reject_once`` then ``reject_always`` then
    ``cancelled``; an option of unknown or missing kind is never selected.
    Malformed request shapes are answered ``cancelled`` and named in the
    decision basis instead of raising into the reader. This answers the
    protocol's permission prompt; it never pretends a tool scope is enforced -
    enforcement facts stay with the evidence.
    """

    def decide(self, params, *, operation: str) -> tuple[dict, str]:
        if not isinstance(params, dict):
            return {"outcome": "cancelled"}, "malformed request: params is not an object"
        raw_options = params.get("options")
        if raw_options is not None and not isinstance(raw_options, list):
            return {"outcome": "cancelled"}, "malformed request: options is not an array"
        options = [option for option in (raw_options or []) if isinstance(option, dict)]
        tool_call = params.get("toolCall")
        if tool_call is not None and not isinstance(tool_call, dict):
            return {"outcome": "cancelled"}, "malformed request: toolCall is not an object"
        title = tool_call.get("title") if isinstance(tool_call, dict) else None
        if title is not None and not isinstance(title, str):
            return {"outcome": "cancelled"}, "malformed request: toolCall.title is not a string"
        for kind in ("reject_once", "reject_always"):
            for option in options:
                if option.get("kind") == kind and isinstance(option.get("optionId"), str):
                    return {"outcome": "selected", "optionId": option["optionId"]}, kind
        return {"outcome": "cancelled"}, "no reject-kind option; unknown kinds are never selected"


class AcpClient:
    """One native ACP process and the sessions it hosts."""

    def __init__(self, process, handle, connection: AcpConnection):
        self.process = process
        self.handle = handle
        self.connection = connection

    @classmethod
    def start(cls, argv: list[str], *, private_root: Path, extra_env: dict | None = None,
              cwd: Path | None = None, launch_environment: dict | None = None,
              permission_policy: PermissionPolicy | None = None) -> "AcpClient":
        """Launch the process and start its connection; a fake agent under test
        launches through the same wrapper with inherited native configuration."""
        stdin_lock = threading.Lock()
        process, handle = launch(argv, private_root=private_root, extra_env=extra_env, cwd=cwd,
                                 launch_environment=launch_environment, stdin_lock=stdin_lock)
        try:
            policy = permission_policy if permission_policy is not None else PermissionPolicy()
            connection = AcpConnection(process, stdin_lock=stdin_lock,
                                       facts_path=Path(private_root) / "logs" / "acp-facts.jsonl",
                                       permission_handler=policy.decide)
            connection.start()
            return cls(process, handle, connection)
        except BaseException as error:
            raise stop_after_spawn_failure(process, handle, error) from error

    # -- session path ----------------------------------------------------------

    def initialize(self, timeout: float = INITIALIZE_TIMEOUT) -> object:
        """Negotiate the protocol; returns the agent's raw initialize result."""
        result = self.connection.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }, timeout)
        return result

    def new_session(self, cwd: str, mcp_servers: list | None = None,
                    timeout: float = SESSION_TIMEOUT) -> dict:
        """Create a session; ``mcpServers`` is always present on the wire."""
        servers = list(mcp_servers or [])
        return self.connection.request("session/new", {"cwd": str(cwd), "mcpServers": servers}, timeout)

    def resume_session(self, session_id: str, cwd: str, mcp_servers: list | None = None,
                       timeout: float = SESSION_TIMEOUT) -> dict:
        """Resume a persisted session; the response carries no session id."""
        servers = list(mcp_servers or [])
        return self.connection.request("session/resume", {"sessionId": str(session_id),
                                                          "cwd": str(cwd),
                                                          "mcpServers": servers}, timeout)

    def set_config_option(self, session_id: str, config_id: str, value, timeout: float = CONFIG_TIMEOUT) -> dict:
        """Set one session configuration option; the response carries the whole
        option group and never a notification."""
        return self.connection.request("session/set_config_option", {
            "sessionId": str(session_id), "configId": str(config_id), "value": value}, timeout)

    def close_session(self, session_id: str, timeout: float = CLOSE_TIMEOUT) -> dict:
        return self.connection.request("session/close", {"sessionId": str(session_id)}, timeout)

    # -- native prompt operations, for a later driver ---------------------------

    def prompt(self, session_id: str, text: str, timeout: float = PROMPT_TIMEOUT) -> dict:
        """Submit one text prompt; the response carries the stop reason. Running
        a prompt calls the model - callers own that authorization."""
        blocks = [{"type": "text", "text": str(text)}]
        return self.connection.request("session/prompt", {"sessionId": str(session_id),
                                                          "prompt": blocks}, timeout)

    def cancel(self, session_id: str, timeout: float = 10.0) -> None:
        """Send the cancel notification through the bounded writer within its
        budget. There is no acknowledgement to wait for; this is a transport
        fact and never a stop or termination proof."""
        self.connection.notify("session/cancel", {"sessionId": str(session_id)}, timeout=timeout)

    # -- shutdown ---------------------------------------------------------------

    def shutdown(self, *, drain_seconds: float = 20.0, settle_seconds: float = 5.0) -> dict:
        """Stop through the owned input channel, drain and retain actual evidence."""
        self.connection.shutdown(drain_seconds=drain_seconds,
            # Both existing waits followed EOF. The reader keeps draining in
            # parallel; keep its budget before force as well as the leader's.
            stop=lambda: self.handle.terminate(grace_seconds=drain_seconds + settle_seconds))
        evidence = stop_evidence(self.handle)
        return evidence

    def facts(self) -> dict:
        return self.connection.facts()

    def stderr_tail(self) -> str:
        return self.connection.stderr_text()
