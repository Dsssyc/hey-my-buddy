"""Read ACP profile row names without evaluating or retaining configuration."""
from __future__ import annotations

import threading
import time
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from ....protocol.process_identity import discard_confirmed_native_spawn, native_process_journal_active
from ..native_support import halt_owned_session
from .acp.connection import stop_evidence
from .acp.launch import LaunchOwnershipError, LaunchRejected, launch
from .protocol import NativeError

UNREADABLE = "dsh-tool-inventory-unreadable"
UNRECOGNIZED = "dsh-tool-inventory-format-unrecognized"
_MAX_BYTES = 4 * 1024 * 1024
_STR = "tag:yaml.org,2002:str"


def tool_rows(raw: bytes) -> tuple[str, ...] | None:
    """Recognize DSH CLI 0.2.2-rc.1's entry sequence, including nested groups.

    Compose nodes, never construct objects: custom tags such as !!js stay inert
    and plugin config is skipped. A new entry shape is unknown, never empty.
    """
    rows: list[str] = []
    active: set[int] = set()

    def visit(sequence):
        if (not isinstance(sequence, SequenceNode) or sequence.tag != "tag:yaml.org,2002:seq"
                or id(sequence) in active):
            raise ValueError()
        active.add(id(sequence))
        for node in sequence.value:
            if not isinstance(node, MappingNode) or node.tag != "tag:yaml.org,2002:map":
                raise ValueError()
            fields = {}
            for key, value in node.value:
                if not isinstance(key, ScalarNode) or key.tag != _STR or key.value in fields:
                    raise ValueError()
                fields[key.value] = value
            name = fields.get("id")
            if not isinstance(name, ScalarNode) or name.tag != _STR or not name.value:
                raise ValueError()
            if name.value.startswith("tool-"):
                rows.append(name.value)
            group = fields.get("group")
            if group is not None:
                if not isinstance(group, ScalarNode) or group.tag not in (
                        "tag:yaml.org,2002:bool", "tag:yaml.org,2002:null"):
                    raise ValueError()
                if group.tag.endswith(":bool") and group.value.lower() in ("true", "yes", "on"):
                    visit(fields.get("config"))
        active.remove(id(sequence))

    try:
        visit(yaml.compose(raw, Loader=yaml.SafeLoader))
    except (yaml.YAMLError, UnicodeError, ValueError, RecursionError):
        return None
    return tuple(sorted(set(rows))) if rows else None


def read_tool_rows(command: list[str], *, environment: dict, launch_environment: dict,
                   cwd: str, private_root: Path, deadline: float) -> tuple[tuple[str, ...] | None, str | None]:
    """Bound a boot-free dump in memory; discard stderr and all non-row data.

    Use the same owned launcher as ACP. No dump content, exception text or
    diagnostic output crosses this boundary, including failed launches.
    """
    try:
        process, handle = launch([*command, "--profile", "acp", "--dump-default-config"],
                                 private_root=private_root, cwd=Path(cwd),
                                 source_environment=environment, launch_environment=launch_environment)
    except LaunchOwnershipError as error:
        if error.evidence["shutdownConfirmed"]:
            return None, UNREADABLE
        failure = NativeError("native-shutdown-failed",
                              "the DSH tool inventory probe failed to start with an unconfirmed stop")
        failure.spawn_evidence = error.evidence
        raise failure from None
    except (LaunchRejected, OSError, ValueError):
        return None, UNREADABLE
    chunks: list[bytes] = []
    failed = threading.Event()

    def drain(stream, retain):
        count = 0
        try:
            while chunk := stream.read(65536):
                count += len(chunk)
                if count > _MAX_BYTES:
                    failed.set()
                elif retain:
                    chunks.append(chunk)
        except (OSError, ValueError):
            failed.set()

    readers = [threading.Thread(target=drain, args=(stream, retain), daemon=True)
               for stream, retain in ((process.stdout, True), (process.stderr, False))]
    try:
        process.stdin.close()
        for reader in readers:
            reader.start()
        stop_at = min(deadline, time.monotonic() + 10)
        while (process.poll() is None or any(reader.is_alive() for reader in readers)):
            if failed.is_set() or time.monotonic() >= stop_at:
                failed.set()
                break
            time.sleep(0.01)
    except (OSError, ValueError):
        failed.set()
    finally:
        halt_owned_session(handle, min(deadline, time.monotonic() + 2))
        evidence = stop_evidence(handle)
        for reader in readers:
            if reader.ident is not None:
                reader.join(timeout=1)
        if any(reader.is_alive() for reader in readers):
            evidence = {**evidence, "shutdownConfirmed": False, "groupObserved": "unknown"}
        if not evidence["shutdownConfirmed"]:
            failure = NativeError("native-shutdown-failed", "the DSH tool inventory probe stop is unconfirmed")
            failure.spawn_evidence = evidence
            raise failure
        if native_process_journal_active():
            discard_confirmed_native_spawn(process._buddy_native_intent)
        for stream in (process.stdout, process.stderr):
            stream.close()
    if failed.is_set() or process.returncode != 0:
        return None, UNREADABLE
    rows = tool_rows(b"".join(chunks))
    return (rows, None) if rows is not None else (None, UNRECOGNIZED)
