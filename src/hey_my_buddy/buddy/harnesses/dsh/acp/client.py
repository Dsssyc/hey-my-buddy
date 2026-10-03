"""The native ACP session path: one client over one launched process.

``AcpClient`` wraps a launched process and its connection with the session
operations the protocol offers: initialize, session/new (the ``mcpServers`` key
is always sent, empty or not - the installed agent rejects its absence),
session/set_config_option, session/list, session/resume, session/close, and the
prompt and cancel native operations for a later driver to reuse. Stdio MCP
entries must carry their ``env`` as an array of ``{name, value}`` objects; a
mapping is refused client-side because the installed agent silently fails to
mount it. Every response is returned raw beside redacted facts; judging
configurations, tool events and outcomes is not this client's job.

The optional metadata log path is bound to the run's private root like every
other launch path, and a failure after the child exists raises an ownership
error that carries the process, handle and stop evidence instead of dropping
them.
"""
from __future__ import annotations

import os
from pathlib import Path

from .....errors import BoardError
from .connection import AcpConnection, FrameMetaLog, stop_evidence
from .launch import (LaunchOwnershipError, ensure_private_log_path, finalize_after_spawn_failure,
                     launch)

PROTOCOL_VERSION = 1

INITIALIZE_TIMEOUT = 30.0
SESSION_TIMEOUT = 60.0
CONFIG_TIMEOUT = 30.0
CLOSE_TIMEOUT = 30.0
PROMPT_TIMEOUT = 3600.0


class PermissionPolicy:
    """Answers only the agent's upgrade-permission requests; nothing else.

    The conservative default prefers ``reject_once`` then ``reject_always`` then
    ``cancelled``; an option of unknown or missing kind is never selected, and
    ``allow`` kinds apply only to an exact, explicitly listed tool-call title -
    a longer title that merely starts with a listed one is not on the list.
    Malformed request shapes are answered ``cancelled`` and named in the
    decision basis instead of raising into the reader. This answers the
    protocol's permission prompt; it never pretends a tool scope is enforced -
    enforcement facts stay with the evidence.
    """

    def __init__(self, allowed_titles: tuple[str, ...] = ()):
        self.allowed_titles = tuple(allowed_titles)

    def decide(self, params) -> tuple[dict, str]:
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
        if self.allowed_titles:
            if title is not None and title in self.allowed_titles:
                for kind in ("allow_once", "allow_always"):
                    for option in options:
                        if option.get("kind") == kind and isinstance(option.get("optionId"), str):
                            return ({"outcome": "selected", "optionId": option["optionId"]},
                                    f"{kind} for an explicitly allowed tool call")
                return {"outcome": "cancelled"}, "allowed tool call but no allow-kind option"
            return {"outcome": "cancelled"}, "tool call title is not on the explicit allow list"
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
    def start(cls, argv: list[str], *, private_root: Path, dsh_home: Path | None = None,
              home: Path | None = None, extra_env: dict | None = None,
              cwd: Path | None = None, launch_log: Path | None = None,
              frame_log: FrameMetaLog | None = None,
              permission_policy: PermissionPolicy | None = None) -> "AcpClient":
        """Launch the process and start its connection; a fake agent under test
        launches through the same wrapper and the same private-home contract."""
        if frame_log is not None:
            ensure_private_log_path(Path(frame_log.path), private_root)
        process, handle = launch(argv, private_root=private_root, dsh_home=dsh_home,
                                 home=home, extra_env=extra_env, cwd=cwd,
                                 launch_log=launch_log)
        policy = permission_policy if permission_policy is not None else PermissionPolicy()
        connection = AcpConnection(process, handle, frame_log=frame_log,
                                   permission_handler=policy.decide)
        try:
            connection.start()
        except BaseException as error:
            evidence = finalize_after_spawn_failure(process, handle)
            raise LaunchOwnershipError(f"connection start failed after spawn: {error}",
                                       process=process, handle=handle,
                                       evidence=evidence) from error
        return cls(process, handle, connection)

    # -- session path ----------------------------------------------------------

    def initialize(self, timeout: float = INITIALIZE_TIMEOUT) -> dict:
        """Negotiate the protocol; returns the agent's raw initialize result."""
        result = self.connection.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False},
                                   "terminal": False},
        }, timeout)
        if not isinstance(result, dict):
            raise BoardError("ACP_PROTOCOL_FAULT", "initialize returned a non-object result")
        return result

    def new_session(self, cwd: str, mcp_servers: list | None = None,
                    timeout: float = SESSION_TIMEOUT) -> dict:
        """Create a session; ``mcpServers`` is always present on the wire."""
        servers = self._checked_mcp_servers(mcp_servers)
        if not os.path.isabs(str(cwd)):
            raise BoardError("ACP_SESSION_CWD", "session cwd must be an absolute path", cwd=str(cwd))
        return self.connection.request("session/new", {"cwd": str(cwd), "mcpServers": servers}, timeout)

    def resume_session(self, session_id: str, cwd: str, mcp_servers: list | None = None,
                       timeout: float = SESSION_TIMEOUT) -> dict:
        """Resume a persisted session; the response carries no session id."""
        servers = self._checked_mcp_servers(mcp_servers)
        if not os.path.isabs(str(cwd)):
            raise BoardError("ACP_SESSION_CWD", "session cwd must be an absolute path", cwd=str(cwd))
        return self.connection.request("session/resume", {"sessionId": str(session_id),
                                                          "cwd": str(cwd),
                                                          "mcpServers": servers}, timeout)

    def list_sessions(self, cwd: str | None = None, cursor: str | None = None,
                      timeout: float = SESSION_TIMEOUT) -> dict:
        """List persisted sessions; both filters are optional."""
        params: dict = {}
        if cwd is not None:
            params["cwd"] = str(cwd)
        if cursor is not None:
            params["cursor"] = str(cursor)
        return self.connection.request("session/list", params, timeout)

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

    def request_update(self, method: str, params: dict, timeout: float) -> dict:
        """One explicit non-session request for a later driver; session methods
        above remain the reviewed surface."""
        return self.connection.request(method, params, timeout)

    # -- shutdown ---------------------------------------------------------------

    def shutdown(self, *, drain_seconds: float = 20.0, settle_seconds: float = 5.0) -> dict:
        """Close stdin, drain to EOF, wait for the leader, then observe leader and
        group separately. Returns the stop evidence; unknown stays conservative."""
        write_state = self.connection.shutdown(drain_seconds=drain_seconds)
        if self.handle.process.poll() is None:
            self.handle.wait(settle_seconds)
        evidence = stop_evidence(self.handle)
        evidence.update(write_state)
        return evidence

    def facts(self) -> dict:
        return self.connection.facts()

    def stderr_tail(self) -> str:
        return self.connection.stderr_text()

    # -- validation ---------------------------------------------------------

    @staticmethod
    def _checked_mcp_servers(mcp_servers: list | None) -> list:
        """Validate the declared server list; the stdio ``env`` must be an array.

        The installed agent accepts a mapping ``env`` without an error and then
        silently fails to mount the server, so the shape is enforced here where
        the mistake is visible.
        """
        servers = list(mcp_servers or [])
        for server in servers:
            if not isinstance(server, dict):
                raise BoardError("ACP_MCP_SERVER", "an MCP server entry must be an object")
            name = server.get("name")
            if not isinstance(name, str) or not name:
                raise BoardError("ACP_MCP_SERVER", "an MCP server entry needs a name")
            if server.get("type") in ("http", "sse", "acp"):
                continue
            command = server.get("command")
            if not isinstance(command, str) or not command:
                raise BoardError("ACP_MCP_SERVER", "a stdio MCP server entry needs a command")
            env = server.get("env")
            if env is None:
                continue
            if not isinstance(env, list) or not all(
                    isinstance(item, dict) and isinstance(item.get("name"), str)
                    and isinstance(item.get("value"), str) for item in env):
                raise BoardError("ACP_MCP_SERVER",
                                 "a stdio MCP server env must be an array of {name, value} objects",
                                 server=name)
        return servers
