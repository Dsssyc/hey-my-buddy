"""The attempt-local mount of the read-only frozen snapshot query service.

The role prepares one mount per routing or maintenance run: a private
configuration file under the invocation root binds a fresh per-attempt HMAC
key to one frozen snapshot reference, and the stdio MCP carrier
(:mod:`hey_my_buddy.buddy.roles.snapshot_mcp`) serves exactly that file under
the fixed server name. The native drivers consume only the two values of
:class:`~hey_my_buddy.buddy.harnesses.run_contract.SnapshotQueryMount` — the
configuration path and the bare tool names — and rebuild the live service from
the file. A snapshot mount travels separately from the Worker completion and
inquiry bindings (ADR-035 decision 3). The fixed server name keeps the tool
definitions stable across requests for prefix caching; per-attempt isolation
comes from the HMAC key, never from the name.
"""
from __future__ import annotations

import hashlib
import os
import secrets
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ...errors import BoardError
from ...json_codec import canonical_json
from ...private_files import read_bytes
from ...private_dirs import ensure_private_dir
from ..roles.snapshot_mcp import SNAPSHOT_MCP_SERVER_NAME, SnapshotService
from ..roles.turn_io import private_json
from .run_contract import EvidenceRef, SnapshotQueryMount
from .session_receipts import ReceiptError

SNAPSHOT_CONFIG_NAME = "snapshot-mcp.json"
_MCP_MODULE = "hey_my_buddy.buddy.roles.snapshot_mcp"
_SNAPSHOT_FIELDS = frozenset({"namespace", "owner", "reference", "round"})


def prepare_snapshot_mount(*, invocation_root: Path, state_dir: Path, identity: dict,
                           input_sha256: str, snapshot: dict) -> SnapshotQueryMount:
    """Write this attempt's snapshot configuration and describe its tools.

    ``snapshot`` is the frozen reference the service dispatched: the namespace
    (``routing`` or ``maintenance``), its owner, the content reference and the
    optional routing round. Reading the mounted snapshot here validates the
    reference before any native process starts, so a broken dispatch fails at
    the boundary instead of inside a harness run.
    """
    if (not isinstance(snapshot, dict) or not {"namespace", "owner", "reference"} <= set(snapshot)
            or set(snapshot) - _SNAPSHOT_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "a snapshot mount requires a namespace, owner and reference")
    configuration = {"identity": dict(identity), "inputSha256": input_sha256,
                     "key": secrets.token_hex(32),
                     "snapshot": {"stateDir": str(state_dir), **snapshot}}
    config_path = ensure_private_dir(Path(invocation_root)) / SNAPSHOT_CONFIG_NAME
    private_json(config_path, configuration, exclusive=True)
    service = SnapshotService.from_file(config_path)
    return SnapshotQueryMount(config_path=str(config_path),
                              tool_names=tuple(tool["name"] for tool in service.tools))


def mounted_snapshot(mount: SnapshotQueryMount) -> SnapshotService:
    """Rebuild the live service from the attempt's frozen configuration.

    The stdio MCP subprocess and this in-process instance share only the
    configuration file and its append-only ledger, so both attribute every
    signed reply to the same attempt key.
    """
    return SnapshotService.from_file(Path(mount.config_path))


def mounted_micro_task_coverage(mount: SnapshotQueryMount) -> list[dict]:
    """Sample the mounted invocation's issued pages for maintenance correction."""
    return SnapshotService.issued_micro_task_coverage(Path(mount.config_path).parent)


def stdio_tool_names(mount: SnapshotQueryMount) -> tuple[str, ...]:
    """The qualified names a stdio MCP harness sees for the mounted tools."""
    return tuple(f"mcp__{SNAPSHOT_MCP_SERVER_NAME}__{name}" for name in mount.tool_names)


def stdio_mcp_entry(mount: SnapshotQueryMount) -> dict:
    """One ACP-style ``mcpServers`` entry for the fixed-name stdio server."""
    return {"name": SNAPSHOT_MCP_SERVER_NAME, "command": sys.executable,
            "args": ["-m", _MCP_MODULE, str(mount.config_path)],
            "env": ([{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}]
                    if os.environ.get("PYTHONPATH") else [])}


def claude_mcp_config(mount: SnapshotQueryMount) -> dict:
    """The ``--mcp-config`` document Claude Code mounts for the snapshot service."""
    return {"mcpServers": {SNAPSHOT_MCP_SERVER_NAME: {
        "command": sys.executable, "args": ["-m", _MCP_MODULE, str(mount.config_path)],
        "env": {"PYTHONPATH": os.environ["PYTHONPATH"]} if os.environ.get("PYTHONPATH") else {}}}}


@dataclass
class ObservedSnapshotCalls:
    """Driver-side pairing of native root calls with the mounted service.

    Each driver feeds the facts its own native events expose — identity, call
    id, tool name, arguments, result and the observed phases and status — and
    this collector keeps one row per call. Rows a native stream never completed
    stay unfinished and fail verification, exactly like replays and foreign
    roots; the first observed body wins and nothing is ever invented here.
    """

    names: frozenset[str] = frozenset()
    _rows: dict[tuple[str, str], dict] = field(default_factory=dict)
    _conflicts: set[tuple[str, str]] = field(default_factory=set)

    def observe(self, identity: dict, call_id, tool_name, *, arguments=None, result=None,
                started=None, completed=None, ok=None) -> None:
        """Fold one native fact into its call row.

        A row is created only by a fact that names a mounted tool; later facts
        of the same native call may arrive without the name again (Codex pairs
        a typed ``dynamicToolCall`` item with the raw item that names the
        tool) and fold into the existing row by identity and call id.
        """
        if not isinstance(call_id, str) or not call_id:
            return
        key = (canonical_json(identity), call_id)
        row = self._rows.get(key)
        if row is None:
            if tool_name not in self.names:
                return
            row = self._rows[key] = {"nativeIdentity": dict(identity), "callId": call_id,
                                     "toolName": tool_name, "arguments": None, "result": None,
                                     "started": False, "completed": False, "ok": None}
        for name, value in (("toolName", tool_name), ("arguments", arguments),
                            ("result", result), ("ok", ok)):
            if value is None:
                continue
            if row[name] is None:
                row[name] = value
            elif row[name] != value:
                self._conflicts.add(key)
        if started is True:
            row["started"] = True
        if completed is True:
            row["completed"] = True

    def calls(self) -> list[dict]:
        return [{**row, "completed": False} if key in self._conflicts else row
                for key, row in self._rows.items()]


def verify_mounted_calls(mount: SnapshotQueryMount, observed: ObservedSnapshotCalls,
                         native_roots) -> tuple[frozenset[tuple[str, str]], list[dict] | None]:
    """Verify the ledger against the native root calls, or verify nothing.

    A receipt mismatch is a publication fact, never a driver crash: an
    unverifiable ledger leaves every mounted-tool call unverified, so the final
    ``judge_tool_evidence`` forbids the run instead of the driver guessing.
    """
    try:
        return mounted_snapshot(mount).verify_calls(observed.calls(), list(native_roots))
    except ReceiptError:
        return frozenset(), None


def snapshot_coverage_refs(invocation_root: Path, coverage: list[dict] | None) -> tuple[EvidenceRef, ...]:
    if not coverage:
        return ()
    target = invocation_root / "snapshot-coverage.json"
    private_json(target, coverage, exclusive=True)
    raw = read_bytes(target)
    return (EvidenceRef(kind="snapshot-coverage", location=str(target), size_bytes=len(raw),
                        sha256=hashlib.sha256(raw).hexdigest()),)


__all__ = [
    "SNAPSHOT_CONFIG_NAME", "ObservedSnapshotCalls", "SnapshotQueryMount",
    "claude_mcp_config", "mounted_micro_task_coverage", "mounted_snapshot", "prepare_snapshot_mount",
    "stdio_mcp_entry", "stdio_tool_names", "verify_mounted_calls",
]
