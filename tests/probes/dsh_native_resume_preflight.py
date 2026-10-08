"""No-model DSH native-resume preflight probe (the R1 fact check).

With the user's installed ``dsh --profile acp``, this probe checks - through the
accepted public ACP client only - what a cross-process native resume can do
today. Two separately rooted owned launches share one micro-task sessions root
(``private_dirs.native_root(state, "dsh", <fake task>)/sessions``), which every
launch patch pins through DSH's own public ``session-persistence-jsonl`` row;
each attempt keeps its DSH home, profile, patch, launch log and frame metadata
log inside its own ``private_dirs.attempt_root``. Attempt A initializes,
creates one empty root session, reads back the declared configuration (and
switches one option and reads it back), lists sessions, closes the session and
stops with the owned group observed gone. Attempt B initializes in its own
root, resumes the recorded session id with its own new MCP service
description, lists sessions, classifies the native rejection for a
nonexistent session, closes what it resumed and stops the same way.

The probe never sends a prompt, never calls ``authenticate``, never calls a
model and never reads a credential file's content: the owning home's settings
and credentials stay at their original paths and are only named in the public
launch patch rows. Every client call is logged by method name before it is
sent, and the per-attempt frame metadata log plus the connection facts must
agree with that log, so the report can prove what crossed the wire. The
mounted MCP service is the minimal embedded stub in this same file (it answers
``initialize`` and ``tools/list`` and nothing else); its handshake evidence
file is the mount observation, never a tool-execution proof. Native
rejections, errors and unverifiable items are recorded as exactly that - the
probe never falls back to ``session/new``, never seeds a private format and
never sends content to reach a green result.

The launch rows, the patch order and the profile materialization reuse the
accepted production mechanisms (``source_binding_rows``, the
``session-persistence-jsonl`` pin, the two always-off rows last,
``materialize_acp_profile``, the ``AcpClient.start`` wrapper) without changing
any production module; the literal row ids are asserted against the production
constants at startup. Materials land under the registered task root only -
``<task-root>/m/<run-label>/`` for this run's state and evidence - and the run
label must be new, so an earlier failure is never overwritten and nothing is
ever deleted.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
import uuid
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[2]

#: The env keys this probe refuses to inherit: they could redirect the probe at
#: the daily board, a pinned runtime identity, a Worker or agent credential, or
#: a foreign virtualenv instead of this checkout.
FORBIDDEN_ENV_PREFIXES = ("BUDDY_WORKER", "BUDDY_AGENT_CREDENTIAL", "ANTHROPIC_")
FORBIDDEN_ENV_KEYS = ("BUDDY_RUNTIME_IDENTITY", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
#: Env keys a focused run may carry, but only when they name the task root.
ALLOWED_INSIDE_TASK_ROOT = ("TMPDIR", "BUDDY_CHECKS_TMPDIR", "BUDDY_STATE_DIR",
                            "BUDDY_RUNTIME_ROOT")

#: The public row ids this probe pins, kept literally beside the production
#: constants they mirror (asserted against them before any launch).
SESSION_ROOT_ROW = "session-persistence-jsonl"
ALWAYS_OFF_ROWS = ("session-title-llm", "session-telemetry-otel")

#: The embedded MCP stub's one catalog entry; a name only, never invoked.
STUB_TOOL_NAME = "r1_probe_catalog_tool"
STUB_HANDSHAKE_SECONDS = 30.0

#: The methods this probe must never send; the operation log is checked
#: against this closed set as part of the no-model proof.
FORBIDDEN_METHODS = ("session/prompt", "authenticate", "session/cancel")


class ProbeError(RuntimeError):
    """The probe's own integrity failed; the recorded facts stay on disk."""


def utc_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="milliseconds")


# -- the embedded minimal MCP stub ---------------------------------------------------


def mcp_stub_main(evidence_path: str) -> int:
    """Serve initialize and tools/list on stdio; record method metadata only.

    DSH spawns this mode as a mounted stdio MCP server. It appends one JSON
    line per protocol event to the evidence file - start, each request's
    method and id, EOF - and never records a parameter value. Answers mirror
    the accepted ``mcp_stub.py`` shape; any other method is refused with
    -32601. No model, no board, and no file beyond the evidence path.
    """
    import threading

    lock = threading.Lock()

    def record(event: dict) -> None:
        entry = {"ts": utc_now(), "epoch": time.time(), **event}
        line = json.dumps(entry, sort_keys=True) + "\n"
        with lock:
            with open(evidence_path, "a", encoding="utf-8") as handle:
                handle.write(line)

    record({"event": "stub-start", "pid": os.getpid(), "envKeys": sorted(os.environ)})
    for raw in sys.stdin:
        text = raw.strip()
        if not text:
            continue
        try:
            message = json.loads(text)
        except ValueError:
            continue
        if not isinstance(message, dict) or "method" not in message or "id" not in message:
            continue
        record({"event": "request", "method": message["method"], "id": message["id"]})
        if message["method"] == "initialize":
            answer = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                      "serverInfo": {"name": "r1-probe-stub", "version": "0.0.1"}}
        elif message["method"] == "tools/list":
            answer = {"tools": [{"name": STUB_TOOL_NAME, "description": "catalog entry only",
                                 "inputSchema": {"type": "object", "properties": {}}}]}
        else:
            print(json.dumps({"jsonrpc": "2.0", "id": message["id"],
                              "error": {"code": -32601, "message": "Method not found"}}), flush=True)
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": answer}), flush=True)
    record({"event": "stub-eof", "pid": os.getpid()})
    return 0


# -- the no-model operation log ------------------------------------------------------


class OperationLog:
    """Every client call by method name, recorded before the frame is sent."""

    def __init__(self, label: str):
        self.label = label
        self.entries: list[dict] = []

    def call(self, method: str, fn, *, note: str | None = None):
        self.entries.append({"ts": utc_now(), "method": method, "note": note})
        return fn()

    def methods(self) -> list[str]:
        return [entry["method"] for entry in self.entries]

    def assert_no_forbidden(self) -> None:
        sent = [method for method in self.methods() if method in FORBIDDEN_METHODS]
        if sent:
            raise ProbeError(f"{self.label}: a forbidden method was called: {sorted(set(sent))}")


def frame_log_counts(path: Path) -> dict:
    """Direction counts and byte size of one frame metadata log (metadata only)."""
    counts = {"in": 0, "out": 0, "lines": 0, "bytes": path.stat().st_size if path.is_file() else 0}
    if not path.is_file():
        return counts
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        counts["lines"] += 1
        if entry.get("dir") in ("in", "out"):
            counts[entry["dir"]] += 1
    return counts


def stub_evidence_lines(path: Path, since_epoch: float | None = None) -> list[dict]:
    """One stub evidence file's events, optionally only those after a stamp."""
    events: list[dict] = []
    if not path.is_file():
        return events
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        stamp = entry.get("epoch")
        if since_epoch is not None and isinstance(stamp, (int, float)) and stamp < since_epoch:
            continue
        if entry.get("event") == "request":
            events.append({"event": "request", "method": entry.get("method"),
                           "ts": entry.get("ts"), "epoch": stamp})
        elif entry.get("event") in ("stub-start", "stub-eof"):
            events.append({"event": entry.get("event"), "pid": entry.get("pid"),
                           "ts": entry.get("ts"), "epoch": stamp})
    return events


def stub_handshake(evidence_path: Path, timeout: float) -> dict:
    """Poll one stub evidence file for the initialize/tools/list handshake."""
    deadline = time.monotonic() + timeout
    while True:
        events = stub_evidence_lines(evidence_path)
        methods = {event.get("method") for event in events}
        if {"initialize", "tools/list"} <= methods:
            return {"observed": True, "events": events}
        if time.monotonic() >= deadline:
            return {"observed": False, "events": events}
        time.sleep(0.1)


def sessions_root_listing(root: Path) -> list[dict]:
    """Name, size and mtime of everything under the shared sessions root.

    Existence and metadata only: no record content is read. A missing root is
    an empty listing beside the start listing, never a fabricated one.
    """
    if not root.is_dir():
        return []
    found: list[dict] = []
    for path in sorted(root.rglob("*")):
        try:
            info = path.lstat()
        except OSError:
            continue
        found.append({"path": str(path.relative_to(root)),
                      "type": "dir" if path.is_dir() else "file",
                      "bytes": info.st_size, "mtimeEpoch": info.st_mtime})
    return found


def config_summary(snapshot) -> dict:
    """The declared option ids plus model/effort current values, from one
    session snapshot or response; nothing else is projected."""
    options = snapshot.get("configOptions") if isinstance(snapshot, dict) else None
    summary = {"optionIds": [], "model": None, "reasoning_effort": None,
               "optionsShape": "missing" if options is None else type(options).__name__}
    if not isinstance(options, list):
        return summary
    for option in options:
        if not isinstance(option, dict) or not isinstance(option.get("id"), str):
            continue
        summary["optionIds"].append(option["id"])
        if option["id"] in ("model", "reasoning_effort"):
            summary[option["id"]] = option.get("currentValue")
    return summary


# -- the probe ------------------------------------------------------------------------


class Probe:
    """One labeled run inside the registered task root; it only ever creates."""

    def __init__(self, task_root: Path, run_label: str):
        self.task_root = task_root.resolve()
        self.run_dir = self.task_root / "m" / run_label
        if self.run_dir.exists():
            raise ProbeError(f"run label already exists; use a new name: {self.run_dir}")
        self.run_dir.mkdir(mode=0o700, parents=True)
        self.evidence_path = self.run_dir / "probe-evidence.json"
        self.evidence: dict = {
            "version": 1, "probe": "dsh-native-resume-preflight", "label": run_label,
            "startedAt": utc_now(), "taskRoot": str(self.task_root), "runDir": str(self.run_dir),
            "attempts": {}, "findings": [], "integrityFailures": [],
        }
        self.findings_list: list[dict] = []
        self.state_root: Path | None = None
        self.fake_task_id: str | None = None

    def write_evidence(self) -> None:
        """Publish the evidence so far; a crash leaves the recorded facts."""
        from hey_my_buddy.buddy.roles.turn_io import private_json

        self.evidence["findings"] = self.findings_list
        self.evidence["updatedAt"] = utc_now()
        private_json(self.evidence_path, self.evidence)

    def finding(self, name: str, status: str, detail) -> None:
        if status not in ("confirmed", "refused", "error", "unknown"):
            raise ProbeError(f"unknown finding status {status!r} for {name}")
        self.findings_list.append({"name": name, "status": status, "detail": detail})
        self.write_evidence()

    def integrity(self, message: str) -> None:
        self.evidence["integrityFailures"].append({"ts": utc_now(), "message": message})
        self.write_evidence()

    # -- environment ------------------------------------------------------

    def hygiene_gate(self) -> dict:
        """Refuse to run outside the registered root or with redirected state."""
        problems: list[str] = []
        for key in os.environ:
            if key in FORBIDDEN_ENV_KEYS or any(key.startswith(prefix)
                                                for prefix in FORBIDDEN_ENV_PREFIXES):
                problems.append(f"{key} is inherited and forbidden")
        for key in ALLOWED_INSIDE_TASK_ROOT:
            value = os.environ.get(key)
            if value and not Path(value).resolve().is_relative_to(self.task_root):
                problems.append(f"{key} points outside the task root: {value}")
        pythonpath = os.environ.get("PYTHONPATH", "")
        if not any(Path(part).resolve() == (CHECKOUT / "src").resolve()
                   for part in pythonpath.split(os.pathsep) if part):
            problems.append("PYTHONPATH does not name this checkout's src")
        if problems:
            raise ProbeError("environment hygiene failed: " + "; ".join(problems))
        import hey_my_buddy

        package = Path(hey_my_buddy.__file__).resolve()
        if not package.is_relative_to(CHECKOUT):
            raise ProbeError(f"hey_my_buddy resolves to {package}, not this checkout")
        return {"python": sys.executable, "heyMyBuddy": str(package),
                "checkout": str(CHECKOUT), "packageInsideCheckout": True,
                "pythonpath": pythonpath, "productionAnchors": production_anchor()}

    # -- one owned attempt -------------------------------------------------

    def attempt(self, attempt_id: str, sessions_root: Path, cwd: Path) -> dict:
        """One separately rooted owned launch with its own patch and stub."""
        from hey_my_buddy.buddy.harnesses.dsh.acp import AcpClient
        from hey_my_buddy.buddy.harnesses.dsh.acp.client import PermissionPolicy
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import FrameMetaLog
        from hey_my_buddy.buddy.harnesses.dsh.native_run import (
            materialize_acp_profile, source_binding_rows, source_home)
        from hey_my_buddy.buddy.harnesses.discovery import discover
        from hey_my_buddy.buddy.harnesses.runtime_selection import selected
        from hey_my_buddy.buddy.roles.turn_io import private_json
        from hey_my_buddy.private_dirs import attempt_root, ensure_private_dir

        assert self.state_root is not None and self.fake_task_id is not None
        root = ensure_private_dir(attempt_root(self.state_root, "dsh", self.fake_task_id,
                                               attempt_id))
        dsh_home = ensure_private_dir(root / "dsh-home")
        logs = ensure_private_dir(root / "logs")
        patch_path = root / "dsh-launch-patch.json"
        stub_evidence = root / "mcp-stub-evidence.jsonl"
        materialize_acp_profile(dsh_home)
        binding = source_binding_rows(source_home(os.environ))
        rows = [*binding,
                {"id": SESSION_ROOT_ROW, "config": {"root": str(sessions_root)}},
                *[{"id": name, "disabled": True} for name in ALWAYS_OFF_ROWS]]
        private_json(patch_path, rows, exclusive=True)
        # The same selection check_preparation runs, kept whole so the
        # selected version travels with the launch evidence.
        selection = selected("dsh", dict(os.environ))
        if selection is None:
            selection = discover("dsh", environment=dict(os.environ))
        if not isinstance(selection, dict) or selection.get("status") != "ready" \
                or not selection.get("command"):
            raise ProbeError(f"the installed dsh command is not ready: {selection}")
        command = list(selection["command"])
        argv = [*command, "--profile", "acp", "--patch", str(patch_path)]
        entry: dict = {
            "attemptId": attempt_id, "attemptRoot": str(root), "dshHome": str(dsh_home),
            "patchPath": str(patch_path), "patchRowIds": [row["id"] for row in rows],
            "patchSessionRoot": str(sessions_root),
            "sourceBindingRowIds": [row["id"] for row in binding],
            "sourceHome": str(source_home(os.environ)),
            "launchLog": str(logs / "launches.jsonl"), "frameLog": str(logs / "frames.jsonl"),
            "stubEvidence": str(stub_evidence), "stubServerName": f"r1_{attempt_id}",
            "mcpServer": {
                "name": f"r1_{attempt_id}", "command": sys.executable,
                "args": [str(Path(__file__).resolve()), "--mcp-stub", str(stub_evidence)],
                "env": ([{"name": "PYTHONPATH", "value": os.environ["PYTHONPATH"]}]
                        if os.environ.get("PYTHONPATH") else [])
                        + [{"name": "BUDDY_R1_PROBE_STUB", "value": f"r1_{attempt_id}"}],
            },
            "dshSelection": {"version": selection.get("version"), "command": command},
            "argv": argv,
        }
        self.evidence["attempts"][attempt_id] = entry
        self.write_evidence()
        ops = OperationLog(f"attempt {attempt_id}")
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import stop_evidence
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import LaunchOwnershipError

        try:
            client = AcpClient.start(argv, private_root=root, dsh_home=dsh_home, cwd=cwd,
                                     launch_log=Path(entry["launchLog"]),
                                     frame_log=FrameMetaLog(Path(entry["frameLog"])),
                                     permission_policy=PermissionPolicy())
        except LaunchOwnershipError as error:
            # The child existed and the accepted wrapper kept its ownership:
            # register the real handle identity and the wrapper's stop evidence,
            # stop the owned group if the finalize left that unconfirmed, and
            # never start another attempt from this failure - an unconfirmed
            # stop stays an explicit unknown fact beside the kept handle.
            process, handle = error.process, error.handle
            spawn = {"message": str(error)[:300],
                     "pid": getattr(process, "pid", None),
                     "pgid": getattr(handle, "pgid", None),
                     "wrapperEvidence": error.evidence if isinstance(error.evidence, dict) else None}
            entry["spawnFailure"] = spawn
            self.finding(f"launch-ownership-{attempt_id}", "error",
                         {"message": spawn["message"], "pid": spawn["pid"], "pgid": spawn["pgid"]})
            evidence = spawn["wrapperEvidence"] or {}
            if not evidence.get("shutdownConfirmed"):
                try:
                    handle.terminate(grace_seconds=3.0)
                    spawn["terminatedByProbe"] = True
                except Exception as terminate_error:  # noqa: BLE001 - the observation below is the fact
                    spawn["terminateError"] = type(terminate_error).__name__
                evidence = stop_evidence(handle)
            spawn["stopEvidence"] = evidence
            spawn["stopConfirmed"] = bool(evidence.get("shutdownConfirmed"))
            self.write_evidence()
            raise ProbeError(
                f"attempt {attempt_id}: the ACP launch failed after spawn; the handle identity, "
                f"the wrapper's stop evidence and the owned group's actual stop are retained, and "
                f"no further attempt is started") from error
        entry["pid"] = client.process.pid
        entry["startedEpoch"] = time.time()
        self.write_evidence()
        return {"client": client, "entry": entry, "ops": ops}

    def stop(self, attempt_id: str, client) -> dict:
        """The native leader/process-group stop: the wrapper's bounded drain,
        one escalation on an unconfirmed group, then a direct group observation.

        The wrapper evidence and the group observation are the same native
        process layer; this free probe proves that layer only. The roles or
        controller outer stop layer is a governed-run fact and stays with the
        later Worker smoke verification, not this probe.
        """
        from hey_my_buddy.buddy.harnesses.dsh.acp.connection import group_observation, stop_evidence

        facts: dict = {"wallClockEpoch": time.time()}
        try:
            shutdown = client.shutdown(drain_seconds=10.0, settle_seconds=5.0)
        except BaseException as error:  # noqa: BLE001 - the handle stays owned either way
            shutdown = {"shutdownError": f"{type(error).__name__}: {error}"}
        facts["shutdown"] = shutdown
        confirmed = bool(shutdown.get("shutdownConfirmed"))
        if not confirmed:
            client.handle.terminate(grace_seconds=3.0)
            facts["escalatedTerminate"] = True
            try:
                client.handle.wait(10)
            except Exception:  # noqa: BLE001 - the observation below is the fact
                pass
            shutdown = stop_evidence(client.handle)
            facts["shutdownAfterEscalation"] = shutdown
            confirmed = bool(shutdown.get("shutdownConfirmed"))
        facts["finalGroupObservation"] = group_observation(client.handle)
        facts["leaderExitCode"] = client.process.poll()
        facts["stopConfirmed"] = bool(facts["leaderExitCode"] is not None
                                      and facts["finalGroupObservation"] == "gone")
        self.evidence["attempts"][attempt_id]["stop"] = facts
        self.write_evidence()
        return facts

    @staticmethod
    def connection_facts(client) -> dict:
        facts = client.facts()
        return {key: facts[key] for key in (
            "protocolFaultCount", "unmatchedResponseCount", "permissionDecisions",
            "deniedInteractions", "observationFailureCount", "notificationCounts",
            "writeFailureCount", "writeUncertain", "eof") if key in facts}


def production_anchor() -> dict:
    """The literal rows this probe pins, checked against production constants."""
    from hey_my_buddy.buddy.harnesses.dsh import native_run

    drift = []
    if SESSION_ROOT_ROW != getattr(native_run, "_SESSION_ROOT_ROW", None):
        drift.append("sessionRootRow")
    if list(ALWAYS_OFF_ROWS) != list(getattr(native_run, "_ALWAYS_DISABLED_ROWS", ())):
        drift.append("alwaysOffRows")
    if drift:
        raise ProbeError(f"probe row literals drifted from production constants: {drift}")
    return {"sessionRootRowMatchesProduction": True, "alwaysOffRowsMatchProduction": True}


# -- the two processes -----------------------------------------------------------------


def run_attempt_a(probe: Probe, sessions_root: Path, cwd: Path) -> dict:
    """initialize, one empty root session, config readback/switch, list,
    close, stop with the group observed gone."""
    from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpRequestError

    result: dict = {"phase": "A"}
    handle = probe.attempt("a", sessions_root, cwd)
    client, entry, ops = handle["client"], handle["entry"], handle["ops"]
    try:
        initialize = ops.call("initialize", lambda: client.initialize())
        entry["initializeResult"] = initialize
        entry["initialize"] = {
            "protocolVersion": initialize.get("protocolVersion"),
            "agentInfo": initialize.get("agentInfo"),
            "capabilities": initialize.get("capabilities"),
            "sessionCapabilities": initialize.get("sessionCapabilities"),
            "authMethods": initialize.get("authMethods"),
        }
        probe.finding("agent-capabilities-a", "confirmed", entry["initialize"])

        snapshot = ops.call("session/new", lambda: client.new_session(
            str(cwd), mcp_servers=[entry["mcpServer"]]))
        session_id = snapshot.get("sessionId") if isinstance(snapshot, dict) else None
        if not isinstance(session_id, str) or not session_id:
            raise ProbeError("attempt A: session/new returned no sessionId")
        entry["sessionId"] = session_id
        entry["sessionIdSource"] = "session/new response"
        entry["sessionNewOtherKeys"] = sorted(
            key for key in snapshot if key not in ("sessionId", "configOptions"))
        entry["configOptions"] = config_summary(snapshot)
        probe.finding("session-new-empty-root-a", "confirmed", {
            "sessionIdSource": entry["sessionIdSource"], "sessionIdShape": "non-empty string",
            "config": entry["configOptions"], "mcpServerSent": entry["stubServerName"]})
        entry["sessionsRootAfterNew"] = sessions_root_listing(sessions_root)

        listed = ops.call("session/list", lambda: client.list_sessions())
        entry["listBeforeClose"] = _list_ids(listed)
        probe.finding("session-list-before-close-a", "confirmed", {
            "listedSessionIds": entry["listBeforeClose"],
            "ourSessionListed": session_id in entry["listBeforeClose"]})

        entry["configSwitch"] = _switch_effort(probe, client, ops, session_id, snapshot, "a")

        listed = ops.call("session/list", lambda: client.list_sessions())
        entry["listAfterSwitch"] = _list_ids(listed)

        ops.call("session/close", lambda: client.close_session(session_id))
        entry["closeAcknowledged"] = True
        probe.finding("session-close-a", "confirmed", {"sessionId": "<attempt-a-session>"})
        listed = ops.call("session/list", lambda: client.list_sessions())
        entry["listAfterClose"] = _list_ids(listed)
        probe.finding("session-list-after-close-a", "confirmed", {
            "listedSessionIds": entry["listAfterClose"],
            "ourSessionStillListed": session_id in entry["listAfterClose"]})
        entry["sessionsRootAfterClose"] = sessions_root_listing(sessions_root)
    except AcpRequestError as error:
        entry["nativeRefusal"] = {"method": error.method, "code": error.error.get("code"),
                                  "message": str(error.error.get("message", ""))[:300]}
        probe.finding("attempt-a-native-refusal", "refused", entry["nativeRefusal"])
        raise ProbeError(f"attempt A refused at {error.method}: {entry['nativeRefusal']}") from None
    finally:
        result["stop"] = probe.stop("a", client)
        entry["connectionFacts"] = probe.connection_facts(client)
        entry["frameLogCounts"] = frame_log_counts(Path(entry["frameLog"]))
        entry["operations"] = ops.entries
        entry["stubHandshake"] = stub_handshake(Path(entry["stubEvidence"]),
                                                STUB_HANDSHAKE_SECONDS)
        entry["sessionsRootAfterStop"] = sessions_root_listing(sessions_root)
        _finish_attempt(probe, "a", client, ops, result)
    return result


def run_attempt_b(probe: Probe, sessions_root: Path, cwd: Path, session_id: str) -> dict:
    """initialize, resume the recorded id with this attempt's own service,
    list, classify a nonexistent-session refusal, close, stop."""
    from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpRequestError

    result: dict = {"phase": "B", "requestedSessionIdSource": "attempt A's session/new response"}
    handle = probe.attempt("b", sessions_root, cwd)
    client, entry, ops = handle["client"], handle["entry"], handle["ops"]
    entry["resumeOutcome"] = "not-attempted"
    entry["resumeResponseHasSessionId"] = None
    try:
        initialize = ops.call("initialize", lambda: client.initialize())
        entry["initializeResult"] = initialize
        entry["initialize"] = {
            "protocolVersion": initialize.get("protocolVersion"),
            "agentInfo": initialize.get("agentInfo"),
            "sessionCapabilities": initialize.get("sessionCapabilities"),
            "authMethods": initialize.get("authMethods"),
        }
        probe.finding("agent-capabilities-b", "confirmed", entry["initialize"])

        listed = ops.call("session/list", lambda: client.list_sessions())
        entry["listBeforeResume"] = _list_ids(listed)
        probe.finding("session-list-before-resume-b", "confirmed", {
            "listedSessionIds": entry["listBeforeResume"],
            "targetSessionListed": session_id in entry["listBeforeResume"]})

        request_summary = {"sessionId": "<attempt-a-session-id>", "cwd": "<shared-cwd>",
                           "mcpServers": [entry["stubServerName"]]}
        try:
            resumed = ops.call("session/resume", lambda: client.resume_session(
                session_id, str(cwd), mcp_servers=[entry["mcpServer"]]))
        except AcpRequestError as error:
            entry["resumeOutcome"] = "refused"
            entry["resumeRefusal"] = {"method": error.method, "code": error.error.get("code"),
                                      "message": str(error.error.get("message", ""))[:300]}
            probe.finding("cross-process-resume", "refused",
                          {"request": request_summary, "nativeError": entry["resumeRefusal"]})
        else:
            entry["resumeOutcome"] = "answered"
            entry["resumeResponseKeys"] = sorted(resumed) if isinstance(resumed, dict) else None
            entry["resumeResponseHasSessionId"] = bool(
                isinstance(resumed, dict) and "sessionId" in resumed)
            entry["resumeConfigOptions"] = config_summary(resumed) if resumed else None
            probe.finding("cross-process-resume", "confirmed",
                          {"request": request_summary, "responseKeys": entry["resumeResponseKeys"],
                           "configOptions": entry["resumeConfigOptions"]})
            if entry["resumeResponseHasSessionId"]:
                raise ProbeError("attempt B: the resume response carried a sessionId; "
                                 "this contradicts the public schema fact")
            probe.finding("resume-response-identity", "confirmed", {
                "responseCarriesNoSessionId": True,
                "identityAvailableOnlyThrough": "session/list (the resume response carries no id)"})

        listed = ops.call("session/list", lambda: client.list_sessions())
        entry["listAfterResume"] = _list_ids(listed)
        probe.finding("session-list-after-resume-b", "confirmed", {
            "listedSessionIds": entry["listAfterResume"],
            "targetSessionListed": session_id in entry["listAfterResume"]})

        try:
            ops.call("session/resume", lambda: client.resume_session(
                "r1-probe-no-such-session", str(cwd), mcp_servers=[]),
                note="negative probe: nonexistent session id")
            entry["negativeResume"] = {"outcome": "answered-unexpectedly"}
            probe.finding("negative-resume-error-classification", "unknown",
                          entry["negativeResume"])
        except AcpRequestError as error:
            entry["negativeResume"] = {"outcome": "refused", "code": error.error.get("code"),
                                       "message": str(error.error.get("message", ""))[:300]}
            probe.finding("negative-resume-error-classification", "confirmed",
                          entry["negativeResume"])

        if entry["resumeOutcome"] == "answered":
            try:
                ops.call("session/close", lambda: client.close_session(session_id))
                entry["closeAcknowledged"] = True
                probe.finding("session-close-b", "confirmed",
                              {"sessionId": "<attempt-a-session-id>"})
            except AcpRequestError as error:
                entry["closeRefusal"] = {"code": error.error.get("code"),
                                         "message": str(error.error.get("message", ""))[:300]}
                probe.finding("session-close-b", "refused", entry["closeRefusal"])
            listed = ops.call("session/list", lambda: client.list_sessions())
            entry["listAfterCloseB"] = _list_ids(listed)
            probe.finding("session-list-after-close-b", "confirmed", {
                "listedSessionIds": entry["listAfterCloseB"],
                "sessionListedAgain": session_id in entry["listAfterCloseB"]})
        entry["sessionsRootAfterResume"] = sessions_root_listing(sessions_root)
    finally:
        result["stop"] = probe.stop("b", client)
        entry["connectionFacts"] = probe.connection_facts(client)
        entry["frameLogCounts"] = frame_log_counts(Path(entry["frameLog"]))
        entry["operations"] = ops.entries
        entry["stubHandshake"] = stub_handshake(Path(entry["stubEvidence"]),
                                                STUB_HANDSHAKE_SECONDS)
        entry["sessionsRootAfterStop"] = sessions_root_listing(sessions_root)
        _finish_attempt(probe, "b", client, ops, result)
    return result


def _switch_effort(probe: Probe, client, ops, session_id: str, snapshot, label: str) -> dict:
    """Switch one declared configuration option and read the echo back."""
    from hey_my_buddy.buddy.harnesses.dsh.acp.connection import AcpRequestError

    options = snapshot.get("configOptions") if isinstance(snapshot, dict) else None
    declared: list[str] = []
    current = None
    for option in options or []:
        if isinstance(option, dict) and option.get("id") == "reasoning_effort":
            current = option.get("currentValue")
            declared = [item.get("value") for item in (option.get("options") or [])
                        if isinstance(item, dict) and isinstance(item.get("value"), str)]
    if not declared:
        probe.finding(f"config-readback-{label}", "unknown",
                      {"reason": "no declared reasoning_effort options exposed"})
        return {"switched": False}
    target = next((value for value in declared if value != current), None)
    if target is None:
        probe.finding(f"config-readback-{label}", "unknown",
                      {"reason": "one declared effort only; nothing to switch"})
        return {"switched": False}
    try:
        echoed = ops.call("session/set_config_option", lambda: client.set_config_option(
            session_id, "reasoning_effort", target))
    except AcpRequestError as error:
        probe.finding(f"config-readback-{label}", "refused",
                      {"code": error.error.get("code"),
                       "message": str(error.error.get("message", ""))[:300]})
        return {"switched": False}
    readback = config_summary(echoed) if isinstance(echoed, dict) else {}
    probe.finding(f"config-readback-{label}", "confirmed", {
        "declaredEfforts": declared, "before": current, "requested": target,
        "readbackEffort": readback.get("reasoning_effort"),
        "retained": readback.get("reasoning_effort") == target})
    return {"switched": True, "requested": target,
            "readback": readback.get("reasoning_effort")}


def _list_ids(listed) -> list[str]:
    """The session ids one public list response carried, in its own order."""
    sessions = listed.get("sessions") if isinstance(listed, dict) else None
    if not isinstance(sessions, list):
        return []
    return [entry.get("sessionId") for entry in sessions
            if isinstance(entry, dict) and isinstance(entry.get("sessionId"), str)]


def _finish_attempt(probe: Probe, attempt_id: str, client, ops: OperationLog,
                    result: dict) -> None:
    """The per-attempt integrity ledger: no forbidden call, counts agree."""
    entry = probe.evidence["attempts"][attempt_id]
    ops.assert_no_forbidden()
    sent = len(ops.entries)
    outbound = entry["frameLogCounts"].get("out", 0)
    entry["operationCount"] = sent
    entry["outboundFrameCount"] = outbound
    if sent != outbound:
        probe.integrity(f"attempt {attempt_id}: {sent} operations logged but "
                        f"{outbound} outbound frames recorded")
    facts = entry["connectionFacts"]
    stop = result["stop"]
    stop_confirmed = bool(stop.get("stopConfirmed"))
    probe.finding(f"native-leader-group-stop-{attempt_id}",
                  "confirmed" if stop_confirmed else "unknown", {
                      "shutdownConfirmed": bool(
                          stop.get("shutdown", {}).get("shutdownConfirmed")
                          or stop.get("shutdownAfterEscalation", {}).get("shutdownConfirmed")),
                      "finalGroupObservation": stop.get("finalGroupObservation"),
                      "leaderExitCode": stop.get("leaderExitCode"),
                      "scope": ("native process layer only: both observations watch the same "
                                "owned process; the roles/controller outer stop is not checked "
                                "by this free probe")})
    probe.finding(f"no-model-calls-{attempt_id}", "confirmed", {
        "operations": ops.methods(), "forbiddenMethodsSent": [],
        "outboundFrames": outbound, "operationCount": sent,
        "permissionDecisions": len(facts.get("permissionDecisions") or []),
        "notificationCounts": facts.get("notificationCounts")})
    if facts.get("permissionDecisions") or facts.get("deniedInteractions"):
        probe.finding(f"unexpected-agent-requests-{attempt_id}", "unknown", {
            "permissionDecisions": len(facts.get("permissionDecisions") or []),
            "deniedInteractions": len(facts.get("deniedInteractions") or [])})


# -- entry -------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-root", type=Path,
                        help="the registered short task root; materials land under <root>/m/<label>")
    parser.add_argument("--run-label", help="a new label; an existing one is refused")
    parser.add_argument("--mcp-stub", type=Path,
                        help="internal: serve the mounted MCP stub, writing evidence to this path")
    args = parser.parse_args(argv)
    if args.mcp_stub is not None:
        return mcp_stub_main(str(args.mcp_stub))
    if args.task_root is None or not args.run_label:
        parser.error("--task-root and --run-label are required without --mcp-stub")

    probe = Probe(args.task_root.resolve(), args.run_label)
    try:
        probe.evidence["environment"] = probe.hygiene_gate()
        from hey_my_buddy.private_dirs import ensure_private_dir, native_root

        probe.state_root = ensure_private_dir(probe.run_dir / "state")
        probe.fake_task_id = "dnrr1-" + uuid.uuid4().hex[:12]
        native = ensure_private_dir(native_root(probe.state_root, "dsh", probe.fake_task_id))
        sessions_root = ensure_private_dir(native / "sessions")
        cwd = ensure_private_dir(probe.run_dir / "cwd")
        probe.evidence["microTask"] = {"fakeTaskId": probe.fake_task_id,
                                       "stateRoot": str(probe.state_root),
                                       "nativeRoot": str(native),
                                       "sessionsRoot": str(sessions_root),
                                       "sharedCwd": str(cwd)}
        probe.evidence["sessionsRootAtStart"] = sessions_root_listing(sessions_root)
        probe.write_evidence()

        run_attempt_a(probe, sessions_root, cwd)
        entry_a = probe.evidence["attempts"]["a"]
        session_id = entry_a.get("sessionId")
        if not isinstance(session_id, str) or not session_id:
            raise ProbeError("attempt A produced no session id; attempt B cannot run")
        if not entry_a.get("stop", {}).get("stopConfirmed"):
            # The gate is the resume conclusion's precondition: with attempt
            # A's native leader/process-group stop unconfirmed, attempt B is
            # never started, the unknown stop facts and the kept ownership
            # stand as recorded, and no cross-process resume conclusion is
            # claimed from this run.
            probe.finding("attempt-b-not-started", "error", {
                "reason": ("attempt A's native leader/process-group stop was not confirmed; "
                           "attempt B is never started and this run claims no cross-process "
                           "resume conclusion"),
                "attemptAStop": entry_a.get("stop")})
            raise ProbeError("attempt A's native stop is unconfirmed; attempt B blocked")
        run_attempt_b(probe, sessions_root, cwd, session_id)

        entry_a = probe.evidence["attempts"]["a"]
        stop_a = entry_a["stop"]
        stub_a = entry_a.get("stubHandshake", {})
        stub_b = probe.evidence["attempts"]["b"].get("stubHandshake", {})
        probe.finding("mcp-mount-at-session-new",
                      "confirmed" if stub_a.get("observed") else "unknown",
                      {"server": entry_a.get("stubServerName"), "observed": stub_a.get("observed"),
                       "events": stub_a.get("events")})
        probe.finding("mcp-mount-at-resume",
                      "confirmed" if stub_b.get("observed") else "unknown",
                      {"server": probe.evidence["attempts"]["b"].get("stubServerName"),
                       "observed": stub_b.get("observed"), "events": stub_b.get("events")})
        post_stop = stub_evidence_lines(Path(entry_a["stubEvidence"]),
                                        since_epoch=stop_a.get("wallClockEpoch"))
        probe.finding("old-service-remount", "unknown", {
            "note": ("attempt A's stub evidence gained no new events after A's stop (checked "
                     "below); whether DSH would attempt to remount a prior attempt's service "
                     "from persisted session state is not observable in this probe"),
            "eventsAfterAStop": post_stop})

        resume = next((item for item in probe.findings_list
                       if item["name"] == "cross-process-resume"), None)
        probe.evidence["conclusion"] = {
            "factCheck": "complete",
            "resumeOutcome": resume["status"] if resume else "unknown",
            "note": ("this is the R1 no-model fact check only: it reports what the public ACP "
                     "surface did; it neither passes nor fails a product capability, and no "
                     "native-session capability claim follows from it"),
        }
        probe.evidence["finishedAt"] = utc_now()
        probe.write_evidence()
    except BaseException as error:  # noqa: BLE001 - every failure still writes its evidence
        probe.integrity(f"{type(error).__name__}: {error}")
        probe.evidence["finishedAt"] = utc_now()
        probe.write_evidence()
        print(f"probe failed; evidence at {probe.evidence_path}: "
              f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    failures = probe.evidence.get("integrityFailures") or []
    print(f"probe finished; evidence at {probe.evidence_path}; "
          f"{len(probe.findings_list)} findings, {len(failures)} integrity failures")
    for item in probe.findings_list:
        print(f"  [{item['status']}] {item['name']}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
