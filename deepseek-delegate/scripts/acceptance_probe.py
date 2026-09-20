#!/usr/bin/env python3
"""One resumable, private real-dsh acceptance of the new Buddy blackboard.

This driver uses the public CLI, never SQLite or service implementation methods.
Local attempt files and process identities are read only as independent evidence.
It never retries a failed task, changes a task's verdict, or signals a stored PID.
Run via uv; see docs/acceptance/python-blackboard-0.4.0.md for usage and limits.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any


FORMAT = 1
OWNER_FILE = ".buddy-acceptance-owner.json"
FINAL_STATES = {"completed", "failed", "cancelled"}


class AcceptanceError(RuntimeError):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


def utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        parent_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        temporary.unlink(missing_ok=True)


def within(path: Path, root: Path, *, resolve: bool = True) -> bool:
    if resolve:
        path, root = path.resolve(), root.resolve()
    else:
        path, root = Path(os.path.abspath(path)), Path(os.path.abspath(root))
    return path == root or root in path.parents


def process_identity(pid: int) -> dict | None:
    """Read identity only. No signal, no command arguments or credentials."""
    completed = subprocess.run(
        ["ps", "-p", str(pid), "-o", "pid=,ppid=,lstart=,comm="],
        capture_output=True, text=True, timeout=10, check=False,
    )
    fields = completed.stdout.strip().split(None, 7)
    if completed.returncode != 0 or len(fields) != 8:
        return None
    return {"pid": int(fields[0]), "ppid": int(fields[1]), "started": " ".join(fields[2:7]), "command": fields[7]}


def same_process(left: dict | None, right: dict | None) -> bool:
    return bool(left and right and all(left[key] == right[key] for key in ("pid", "started", "command")))


def jsonl(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    require(all(isinstance(row, dict) for row in rows), f"Expected JSON objects in {path}")
    return rows


class Driver:
    def __init__(self, args: argparse.Namespace):
        self.args = args
        self.launcher = args.launcher.expanduser().absolute()
        self.paths = {key: getattr(args, key).expanduser().resolve() for key in ("state_dir", "runtime_root", "work_dir", "evidence_dir")}
        self.state_dir = self.paths["state_dir"]
        self.evidence = self.paths["evidence_dir"]
        self.work = self.paths["work_dir"]
        self.manifest_path = self.evidence / "driver-state.json"
        self.env = dict(os.environ)
        for key in ("BUDDY_DEV_SOURCE", "BUDDY_RUNTIME", "BUDDY_RUNNER_PATH", "BUDDY_PYTHON", "PYTHONPATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PLUGIN_DATA"):
            self.env.pop(key, None)
        self.env.update(BUDDY_STATE_DIR=str(self.state_dir), BUDDY_RUNTIME_ROOT=str(self.paths["runtime_root"]), PYTHONUNBUFFERED="1")
        self.command = ["/bin/sh", str(self.launcher)]
        self.lock_fd: int | None = None
        self.state: dict = {}
        self.calls = 0

    def log(self, message: str) -> None:
        print(f"[{utc()}] {message}", flush=True)

    def save(self) -> None:
        self.state["updatedAt"] = utc()
        atomic_json(self.manifest_path, self.state)

    def setup(self) -> bool:
        default = Path.home() / ".local/share/hey-my-buddy"
        roots = list(self.paths.values())
        for path in roots:
            require(path not in (Path("/"), Path.home(), Path.cwd().resolve()), f"Refusing broad directory: {path}")
            require(not within(path, default) and not within(default, path), f"Refusing default Buddy service tree: {path}")
        for index, left in enumerate(roots):
            for right in roots[index + 1:]:
                require(not within(left, right) and not within(right, left), "State, runtime, work and evidence must be distinct sibling directories")
        self.evidence.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.lock_fd = os.open(self.evidence / "driver.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise AcceptanceError("Another acceptance driver already owns these directories") from error
        identity = {key: str(value) for key, value in self.paths.items()}
        if self.manifest_path.exists():
            self.state = read_json(self.manifest_path)
            require(self.state.get("format") == FORMAT and self.state.get("paths") == identity, "Existing acceptance paths or format differ")
            if self.state.get("status") in ("passed", "failed"):
                self.log(f"Existing acceptance is {self.state['status']}; no task or service will be started again. Evidence: {self.evidence}")
                return False
            require(not self.args.request_id or self.args.request_id == self.state["requestId"], "Cannot change requestId when resuming")
        else:
            for key, path in self.paths.items():
                if path.exists():
                    leftovers = [entry for entry in path.iterdir() if not (key == "evidence_dir" and entry.name == "driver.lock")]
                    require(not leftovers, f"A first run requires an empty dedicated {key}: {path}")
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            case_key = digest(json.dumps(identity, sort_keys=True).encode())[:20]
            self.state = {"format": FORMAT, "paths": identity, "owner": f"selfhost-{case_key}", "requestId": self.args.request_id or f"blackboard-selfhost-{case_key}", "createdAt": utc(), "status": "running", "launcher": str(self.launcher)}
            self.save()
        for path in roots:
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
            marker = path / OWNER_FILE
            if marker.exists():
                require(read_json(marker) == {"owner": self.state["owner"]}, f"Directory owner marker changed: {path}")
            else:
                atomic_json(marker, {"owner": self.state["owner"]})
            require(path.stat().st_uid == os.geteuid() and not path.stat().st_mode & 0o077, f"Acceptance directory must be private (0700): {path}")
        # On an interrupted run, lost state cannot justify another execution.
        if self.state.get("startIssued"):
            require((self.state_dir / "board.sqlite3").is_file(), "The original blackboard database is missing; refusing to recreate the task")
        if self.state.get("runtime"):
            self.use_runtime(self.state["runtime"])
        else:
            require(self.launcher.is_file(), f"Staged launcher is missing: {self.launcher}")
        return True

    def use_runtime(self, identity: dict) -> None:
        interpreter = Path(identity["python"])
        require(interpreter.is_file(), "Recorded runtime interpreter is missing")
        self.command = [str(interpreter), "-m", "buddy.cli"]
        self.env["BUDDY_RUNTIME"] = identity["runtimeDir"]

    def invoke(self, command: list[str], label: str, *, timeout: float = 90, tick=None, allow_error: bool = False) -> dict:
        self.calls += 1
        serial = f"{time.time_ns()}-{self.calls:03d}-{label}"
        directory = self.evidence / "calls"
        directory.mkdir(mode=0o700, exist_ok=True)
        stdout, stderr = directory / f"{serial}.stdout.json", directory / f"{serial}.stderr.log"
        metadata = {"startedAt": utc(), "argv": command, "cwd": str(self.work), "stdout": str(stdout), "stderr": str(stderr)}
        atomic_json(directory / f"{serial}.request.json", metadata)
        with stdout.open("wb") as out, stderr.open("wb") as err:
            process = subprocess.Popen(command, cwd=self.work, env=self.env, stdin=subprocess.DEVNULL, stdout=out, stderr=err, start_new_session=True)
            deadline, next_tick = time.monotonic() + timeout, time.monotonic() + 10
            try:
                while process.poll() is None:
                    require(time.monotonic() < deadline, f"CLI {label} exceeded its {timeout}s driver limit; request outcome may be uncertain")
                    if tick and time.monotonic() >= next_tick:
                        tick()
                        next_tick = time.monotonic() + 10
                    time.sleep(0.2)
            finally:
                if process.poll() is None:
                    # This is the freshly created CLI process group, never a PID
                    # from a stored task record. Daemon/workers are detached.
                    os.killpg(process.pid, signal.SIGTERM)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL)
                        process.wait(timeout=5)
        metadata.update(finishedAt=utc(), returncode=process.returncode)
        atomic_json(directory / f"{serial}.request.json", metadata)
        try:
            reply = read_json(stdout)
        except (ValueError, OSError) as error:
            raise AcceptanceError(f"CLI {label} did not emit one JSON result; inspect {stdout} and {stderr}") from error
        require(isinstance(reply, dict), f"CLI {label} returned a non-object")
        if not allow_error:
            require(process.returncode == 0 and not reply.get("error"), f"CLI {label} failed: {reply.get('error', reply)}")
        return reply

    def call(self, method: str, params: dict | None = None, **options) -> dict:
        return self.invoke([*self.command, method, json.dumps(params or {}, ensure_ascii=False)], method, **options)

    def endpoint(self) -> dict | None:
        path = self.state_dir / "control.json"
        if not path.exists():
            return None
        try:
            value = read_json(path)
        except (OSError, ValueError):
            return None
        # Never persist or print the endpoint token.
        return {key: value.get(key) for key in ("pid", "serviceId", "startedAt", "protocol", "runtimeIdentity")}

    def await_departure(self, old: dict, *, timeout: float = 45) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            endpoint = self.endpoint()
            departed = endpoint is None or endpoint.get("serviceId") != old["serviceId"]
            old_process = process_identity(old["pid"])
            if departed and not same_process(old.get("process"), old_process):
                return
            time.sleep(0.2)
        raise AcceptanceError("The old private daemon did not fully exit after its lifecycle request")

    def verify_runtime(self) -> dict:
        health = self.call("health", timeout=900)
        require(Path(health["stateDir"]).resolve() == self.state_dir, "Health points to a different service state directory")
        require(health.get("runtimeStable") is True, "Daemon health does not claim actual stable runtime execution")
        info = self.call("runtime")
        identity = info["identity"]
        directory = Path(identity["runtimeDir"])
        require(within(directory, self.paths["runtime_root"]), "Daemon runtime is outside this acceptance's private runtime root")
        require(identity.get("stable") is True and identity.get("inUse") is True and not info.get("leaks"), "Runtime is declared but not actually in use, or leaks disposable source")
        require(identity["identity"] == health["runtimeIdentity"], "Runtime and health identities disagree")
        require(within(Path(identity["python"]), directory, resolve=False), "Runtime interpreter lexical path is outside the runtime")
        if self.state.get("runtime"):
            require(identity["identity"] == self.state["runtime"]["identity"], "Daemon reattached to a different runtime release")
        self.state["runtime"] = identity
        self.save()
        self.use_runtime(identity)
        probe_code = "import json, pathlib, sys, buddy; from buddy import runtime; print(json.dumps({'executable':sys.executable,'resolvedExecutable':str(pathlib.Path(sys.executable).resolve()),'prefix':sys.prefix,'package':str(pathlib.Path(buddy.__file__).resolve()),'identity':runtime.resolve_runtime()}))"
        probe = self.invoke([identity["python"], "-c", probe_code], "runtime-import")
        require(within(Path(probe["executable"]), directory, resolve=False), "Actual interpreter lexical path is outside the runtime")
        require(within(Path(probe["prefix"]), directory), "Actual sys.prefix is outside the runtime")
        require(within(Path(probe["package"]), directory), "Actual buddy import comes from disposable source")
        require("/plugins/cache/" not in probe["resolvedExecutable"], "Resolved base Python is in a disposable plugin cache")
        actual = probe["identity"]["actual"]
        for key in ("adapterScript", "yamlBridge"):
            require(within(Path(actual[key]), directory) and Path(actual[key]).is_file(), f"{key} does not exist in the stable runtime")
        atomic_json(self.evidence / "runtime-verification.json", {"health": health, "runtime": info, "importProbe": probe})
        return health

    def prepare_task(self) -> None:
        if "startParams" in self.state:
            require(digest((self.work / "input.json").read_bytes()) == self.state["expected"]["inputSha256"], "Original acceptance input changed")
            return
        values = [((index * 7919 + 104729) % 200003) - 100001 for index in range(513)] + [0, 0, -1, 1, 2**31, -(2**31)]
        input_value = {"caseId": self.state["requestId"], "values": values}
        atomic_json(self.work / "input.json", input_value)
        expected = {"caseId": input_value["caseId"], "inputSha256": digest((self.work / "input.json").read_bytes()), "count": len(values), "sum": sum(values), "sumOfSquares": sum(value * value for value in values), "sortedValues": sorted(values)}
        task = self.args.task_file.read_text(encoding="utf-8")
        task += "\n\nExact acceptance file contract: use execution.jsonl entries with keys event, pid, at. event must be started then finished, pid is the script process PID, and at is a UTC ISO-8601 timestamp. live.json must contain event=started, the same pid and at as the started journal entry. Copy caseId from input.json into result.json. Do not run the computation script twice. Use uv run --python 3.12 for this foreground script. The coordinator's correlated inquiry contains an acceptance challenge; echo that challenge in your buddy_inquiry_reply answer after the foreground tool finishes.\n"
        (self.evidence / "submitted-task.md").write_text(task, encoding="utf-8")
        self.state.update(expected=expected, startParams={"requestId": self.state["requestId"], "task": task, "cwd": str(self.work), "adapter": "dsh", "workspace": False, "timeoutSeconds": self.args.timeout_seconds})
        self.save()

    def start_once(self) -> None:
        if self.state.get("runId"):
            view = self.call("status", {"runId": self.state["runId"]})
        elif self.state.get("startIssued"):
            # A lost submission reply is recovered by requestId. Do not issue a
            # new start against a missing record and risk a second execution.
            view = self.call("status", {"requestId": self.state["requestId"]})
        else:
            self.state["startIssued"] = True
            self.state["startIssuedAt"] = utc()
            self.save()
            view = self.call("start", self.state["startParams"])
        require(isinstance(view.get("runId"), str) and view.get("requestId") == self.state["requestId"], "Submission recovery returned a different task")
        if self.state.get("runId"):
            require(view["runId"] == self.state["runId"], "Submission changed the original runId")
        self.state["runId"] = view["runId"]
        self.save()
        atomic_json(self.evidence / "start.json", view)
        self.log(f"One task recorded: runId={view['runId']}")

    def snapshot(self) -> dict:
        view = self.call("status", {"runId": self.state["runId"]})
        attempt = view.get("selectedAttempt")
        require(isinstance(attempt, dict), "Task has no selected attempt")
        worker = next((value for value in self.call("workers")["workers"] if value["workerId"] == attempt["workerId"]), None)
        require(worker is not None, "Attempt's public worker record is absent")
        directory = self.state_dir / "attempts" / view["taskId"] / attempt["attemptId"]
        marker = read_json(directory / "spawn.marker")
        require(marker["attemptId"] == attempt["attemptId"] and marker["generation"] == attempt["generation"], "Spawn marker does not identify the selected attempt")
        live = read_json(self.work / "live.json")
        require(live.get("event") == "started" and type(live.get("pid")) is int, "live.json does not follow the submitted probe contract")
        ancestry, pid = [], live["pid"]
        for _ in range(16):
            observed = process_identity(pid)
            if not observed:
                break
            ancestry.append(observed)
            if observed["pid"] == worker["pid"] or observed["ppid"] <= 1:
                break
            pid = observed["ppid"]
        identities = {"worker": process_identity(worker["pid"]), "runner": process_identity(marker["pid"]), "probe": process_identity(live["pid"])}
        require(all(identities.values()), "A required worker/runner/probe process is no longer alive")
        return {"observedAt": utc(), "task": view, "attempt": attempt, "worker": worker, "spawnMarker": marker, "live": live, "identities": identities, "probeAncestry": ancestry}

    def wait_live(self) -> None:
        if self.state.get("beforeRestart"):
            return
        deadline = time.monotonic() + self.args.live_timeout_seconds
        next_report = 0.0
        while not (self.work / "live.json").is_file():
            require(time.monotonic() < deadline, "The real dsh probe did not produce live.json in the allowed startup window")
            view = self.call("status", {"runId": self.state["runId"]})
            require(view["status"] not in FINAL_STATES, f"Task ended before its foreground probe: {view['status']}")
            if time.monotonic() >= next_report:
                self.log(f"Waiting for the real dsh foreground probe; task status={view['status']}")
                next_report = time.monotonic() + 20
            time.sleep(3)
        before = self.snapshot()
        require(before["task"]["status"] not in FINAL_STATES, "Missed the probe's live restart window")
        self.state["beforeRestart"] = before
        self.save()
        atomic_json(self.evidence / "before-restart.json", before)
        self.log(f"Live attempt={before['attempt']['attemptId']}, worker={before['worker']['pid']}, runner={before['spawnMarker']['pid']}, probe={before['live']['pid']}")

    def inquire(self) -> dict:
        return self.call("inquire", self.state["inquiryParams"])

    def post_inquiry(self) -> None:
        if "inquiryParams" not in self.state:
            challenge = "acceptance-" + digest(self.state["runId"].encode())[:16]
            self.state["challenge"] = challenge
            self.state["inquiryParams"] = {"runId": self.state["runId"], "inquiryId": "selfhost-progress-v1", "question": f"Report your current stage through buddy_inquiry_reply after the foreground tool finishes. Include the exact challenge {challenge}. Continue the one existing task; do not re-run its script or modify the acceptance plan.", "waitMs": 0}
            self.save()
        if not self.state.get("inquirySubmitted"):
            reply = self.inquire()
            require((reply.get("inquiry") or {}).get("recorded") is True, "Correlated inquiry was not recorded")
            require(reply.get("bridge", {}).get("observed") is True, "The live dsh bridge did not accept the correlated inquiry")
            self.state["inquirySubmitted"] = True
            self.save()
            atomic_json(self.evidence / "inquiry-submitted.json", reply)
            self.log("One correlated inquiry submitted to the running agent")

    def events(self, after: int = 0) -> list[dict]:
        cursor, rows = after, []
        for _ in range(100):
            page = self.call("events", {"runId": self.state["runId"], "after": cursor, "limit": 100})
            batch = page["events"]
            require(all(type(item.get("seq")) is int and item["seq"] > cursor for item in batch), "Event page does not advance its cursor")
            rows.extend(batch)
            if not page["truncated"]:
                return rows
            require(page["cursor"] > cursor, "Truncated event page has no cursor progress")
            cursor = page["cursor"]
        raise AcceptanceError("Event history exceeded the bounded acceptance page limit")

    def restart(self) -> None:
        if self.state.get("restartVerified"):
            return
        if not self.state.get("restartRequested"):
            health = self.call("health")
            old = {"pid": health["pid"], "serviceId": health["serviceId"], "process": process_identity(health["pid"])}
            require(old["process"] is not None, "Cannot identify the old daemon process")
            prior_events = self.events()
            self.state["beforeCursor"] = prior_events[-1]["seq"] if prior_events else 0
            self.state["oldDaemon"] = old
            self.state["restartRequested"] = True
            self.save()
            atomic_json(self.evidence / "events-before-restart.json", prior_events)
            reply = self.call("restart", {"reason": "private selfhost acceptance: preserve the active attempt", "drainSeconds": 0})
            require(reply.get("workersPreserved") is True and reply.get("restarting") is True, "Restart did not report preservation of independent workers")
            atomic_json(self.evidence / "restart-reply.json", reply)
        self.await_departure(self.state["oldDaemon"])
        health = self.verify_runtime()
        require(health["serviceId"] != self.state["oldDaemon"]["serviceId"], "Service identity did not change across restart")
        after = self.snapshot()
        before = self.state["beforeRestart"]
        for field in ("attemptId", "generation", "workerId", "workerInstance"):
            require(after["attempt"][field] == before["attempt"][field], f"Restart changed attempt identity field {field}")
        require(after["spawnMarker"] == before["spawnMarker"] and after["live"] == before["live"], "Restart changed the original execution markers")
        for role in ("worker", "runner", "probe"):
            require(same_process(before["identities"][role], after["identities"][role]), f"Restart replaced the live {role} process")
        replay = self.events()
        original = read_json(self.evidence / "events-before-restart.json")
        require(replay[:len(original)] == original, "Previously committed task events changed or disappeared after restart")
        duplicate = self.call("start", self.state["startParams"])
        require(duplicate.get("duplicate") is True and duplicate.get("runId") == self.state["runId"], "Repeated identical start did not recover the original task")
        require(duplicate.get("selectedAttempt", {}).get("attemptId") == before["attempt"]["attemptId"], "Idempotent start selected a different attempt")
        self.state["restartVerified"] = True
        self.save()
        atomic_json(self.evidence / "after-restart.json", {"health": health, "snapshot": after, "duplicateStart": duplicate, "events": replay})
        self.log("Daemon restart preserved the same worker, child processes and attempt; identical start recovered that run")

    def await_result(self) -> None:
        if self.state.get("awaitEnvelope"):
            return
        def tick() -> None:
            # Observation has no inquiryId: do not import a live answer into the
            # board and accidentally mask the terminal journal-import path.
            reply = self.call("inquire", {"runId": self.state["runId"]})
            inquiry = self.call("message-get", {"runId": self.state["runId"], "inquiryId": self.state["inquiryParams"]["inquiryId"]})["message"]
            if inquiry.get("answer"):
                self.state["liveAnswer"] = inquiry["answer"]
                self.save()
                atomic_json(self.evidence / "inquiry-live-answer.json", inquiry)
            self.log(f"Waiting on the same run; task={reply.get('status')}, inquiry={inquiry.get('state')}")
        window = min(86400, self.state["startParams"]["timeoutSeconds"] + 60)
        envelope = self.call("await", {"runId": self.state["runId"], "waitSeconds": window}, timeout=window + 60, tick=tick)
        self.state["awaitEnvelope"] = envelope
        self.save()
        atomic_json(self.evidence / "await.json", envelope)
        require(envelope.get("ok") is True and envelope.get("shutdownConfirmed") is True and envelope.get("outcome") == "completed", "Await did not deliver a completed, confirmed-shutdown result")

    def verify(self) -> dict:
        result = self.call("result", {"runId": self.state["runId"]})
        atomic_json(self.evidence / "result-envelope.json", result)
        require(result.get("status") == "completed" and result.get("resultDelivered") is True and result.get("shutdownConfirmed") is True, "Public result is not an executed, delivered and shutdown-confirmed success")
        attempt = result["selectedAttempt"]
        before = self.state["beforeRestart"]
        for field in ("attemptId", "generation", "workerId", "workerInstance"):
            require(attempt[field] == before["attempt"][field], f"Final result changed {field}")
        require(attempt.get("exitCode") == 0 and result.get("resultMeta", {}).get("status") == "ok", "Worker adapter did not report an ordinary successful exit")
        require(attempt.get("runtimeIdentity") == self.state["runtime"]["identity"], "Worker executed using a different runtime identity")
        payload = result["result"]
        require(payload.get("status") == "ok" and payload.get("processState", {}).get("shutdownConfirmed") is True, "dsh runner did not independently confirm shutdown")
        dsh_pid = payload["processState"]["pid"]
        require(dsh_pid in {item["pid"] for item in before["probeAncestry"]}, "The completed dsh PID was not an ancestor of the observed probe")
        expected = self.state["expected"]
        require(digest((self.work / "input.json").read_bytes()) == expected["inputSha256"], "The task modified its input")
        actual = read_json(self.work / "result.json")
        require(actual == expected, "Independent input hash/count/sum/squares/sort verification failed")
        execution = jsonl(self.work / "execution.jsonl")
        require(len(execution) == 2 and [item.get("event") for item in execution] == ["started", "finished"], "The foreground script did not execute exactly once")
        require(all(item.get("pid") == before["live"]["pid"] for item in execution), "Execution journal belongs to another script process")
        require(execution[0] == before["live"], "live.json did not contain the same started journal identity")
        timestamps = [datetime.fromisoformat(item["at"].replace("Z", "+00:00")) for item in execution]
        require(all(value.tzinfo is not None for value in timestamps) and (timestamps[1] - timestamps[0]).total_seconds() >= 89, "Foreground sleep evidence does not cover the planned 90-second interval")
        # Always use the terminal public inquiry path; a cached live answer is not
        # sufficient to pass persisted-answer acceptance.
        terminal = self.inquire()
        atomic_json(self.evidence / "inquiry-terminal.json", terminal)
        require(terminal.get("phase") == "terminal", "Inquiry was not read after terminal task state")
        inquiry = terminal.get("inquiry") or {}
        answer = inquiry.get("answer") or {}
        require(inquiry.get("inquiryId") == self.state["inquiryParams"]["inquiryId"] and inquiry.get("state") == "answered", "Terminal inquiry did not recover the correlated answer")
        require(answer.get("available") is True and answer.get("via") == "tool:buddy_inquiry_reply" and bool(answer.get("toolCallId")), "Terminal answer lacks actual scoped reply-tool evidence")
        require(self.state["challenge"] in answer.get("text", ""), "Reply-tool answer did not echo this run's challenge")
        if self.state.get("liveAnswer"):
            require(all(answer.get(key) == self.state["liveAnswer"].get(key) for key in ("text", "via", "toolCallId")), "Persisted terminal answer differs from the live correlated answer")
        message = self.call("message-get", {"runId": self.state["runId"], "inquiryId": inquiry["inquiryId"]})["message"]
        atomic_json(self.evidence / "inquiry-board-message.json", message)
        require(message.get("state") == "answered" and all(message.get("answer", {}).get(key) == answer.get(key) for key in ("text", "via", "toolCallId")), "Board message did not persist the same answer")
        journal_path = Path(payload["inquiryBridge"]["resultsPath"])
        journal = jsonl(journal_path)
        delivered = [item for item in journal if item.get("inquiryId") == inquiry["inquiryId"] and item.get("state") == "delivered"]
        require(len(delivered) == 1 and bool(delivered[0].get("messageId")), "Question lacks exactly one durable model-visible delivery event")
        answered = [item for item in journal if item.get("inquiryId") == inquiry["inquiryId"] and item.get("state") == "answered"]
        require(len(answered) == 1, "Inquiry journal does not contain exactly one correlated answer")
        raw = answered[0]
        require(raw.get("messageId") == delivered[0]["messageId"], "Reply journal does not identify the delivered question")
        require(raw.get("answer") == answer["text"] and raw.get("via") == answer["via"] and raw.get("toolCallId") == answer["toolCallId"], "Persisted answer does not match the real bridge journal")
        shutil.copy2(journal_path, self.evidence / "inquiry.results.jsonl")
        events = self.events()
        require(len({item["seq"] for item in events}) == len(events) and [item["seq"] for item in events] == sorted(item["seq"] for item in events), "Event history is duplicated or unordered")
        complete = [item for item in events if item["kind"] == "task.completed"]
        require(len(complete) == 1 and complete[0]["attemptId"] == attempt["attemptId"], "Completion event was duplicated or identifies another attempt")
        require(sum(item["kind"] == "task.submitted" for item in events) == 1, "Task submission was duplicated")
        require({item["attemptId"] for item in events if item.get("attemptId")} == {attempt["attemptId"]}, "More than one attempt appears in this run's events")
        replay = self.events(self.state["beforeCursor"])
        require(replay == [item for item in events if item["seq"] > self.state["beforeCursor"]], "Event replay from the saved pre-restart cursor is inconsistent")
        atomic_json(self.evidence / "events-final.json", events)
        checks = {"runId": self.state["runId"], "requestId": self.state["requestId"], "attemptId": attempt["attemptId"], "generation": attempt["generation"], "workerId": attempt["workerId"], "workerInstance": attempt["workerInstance"], "runtimeIdentity": attempt["runtimeIdentity"], "inputSha256": expected["inputSha256"], "resultSha256": digest((self.work / "result.json").read_bytes()), "inquiryId": inquiry["inquiryId"], "toolCallId": answer["toolCallId"], "completionEventSeq": complete[0]["seq"], "scriptExecutions": 1, "passed": True, "verifiedAt": utc(), "nativeAppWakeupValidated": False}
        atomic_json(self.evidence / "independent-checks.json", checks)
        self.log("Independent artifact, one execution, same attempt, event replay and terminal reply-tool checks passed")
        return checks

    def acknowledge(self, checks: dict) -> None:
        note = "Independent private selfhost acceptance verified exact input hash/count/sum/squares/sort, one foreground script execution, daemon restart preserving worker/runner/probe/attempt, idempotent start, durable event replay with one completion, confirmed shutdown, and matching terminal board/journal reply-tool answer. See independent-checks.json."
        reply = self.call("acknowledge", {"runId": self.state["runId"], "note": note, "verdict": "accepted", "evidence": [str(self.evidence / "independent-checks.json"), str(self.evidence / "events-final.json"), str(self.work / "result.json")], "acknowledgedBy": "independent-selfhost-driver"})
        require(bool(reply.get("acceptedAt")) and reply.get("acceptanceVerdict") == "accepted", "Acceptance was not persisted")
        atomic_json(self.evidence / "acknowledgement.json", reply)
        self.state["checks"] = checks
        self.state["acknowledged"] = True
        self.save()

    def copy_logs(self) -> None:
        destination = self.evidence / "private-logs"
        destination.mkdir(mode=0o700, exist_ok=True)
        for name in ("control.log", "worker.log", "runtime-install.log"):
            source = self.state_dir / name
            if source.is_file():
                shutil.copy2(source, destination / name)
        attempt = self.state.get("beforeRestart", {}).get("attempt", {})
        if attempt.get("attemptId") and self.state.get("runId"):
            source = self.state_dir / "attempts" / self.state["runId"] / attempt["attemptId"]
            for item in source.glob("*.log"):
                if item.is_file():
                    shutil.copy2(item, destination / item.name)

    def stop_private_service(self) -> dict:
        require(read_json(self.state_dir / OWNER_FILE) == {"owner": self.state["owner"]}, "Refusing cleanup: private state ownership marker changed")
        endpoint = self.endpoint()
        old = {**endpoint, "process": process_identity(endpoint["pid"])} if endpoint else None
        reply = self.call("stop", {"drainSeconds": 30, "reason": "private selfhost acceptance finished"}, timeout=70)
        atomic_json(self.evidence / "stop.json", reply)
        if old:
            self.await_departure(old)
        require(not reply.get("unresolvedAttempts"), "Private service stop reported unresolved attempts; inspect evidence without signalling stored PIDs")
        # A stopped daemon alone is not evidence that its detached worker exited.
        worker_identity = self.state.get("beforeRestart", {}).get("identities", {}).get("worker")
        if worker_identity:
            deadline = time.monotonic() + 35
            while same_process(worker_identity, process_identity(worker_identity["pid"])):
                require(time.monotonic() < deadline, "Private worker has not exited after cooperative stop")
                time.sleep(0.5)
        return reply

    def run(self) -> int:
        if not self.setup():
            return 0 if self.state.get("status") == "passed" else 1
        outcome, failure = "failed", None
        try:
            self.log("Cold-starting or reattaching only the private staged blackboard")
            if self.state.get("restartRequested") and not self.state.get("restartVerified"):
                self.await_departure(self.state["oldDaemon"])
            self.verify_runtime()
            self.prepare_task()
            self.start_once()
            self.wait_live()
            self.post_inquiry()
            self.restart()
            self.await_result()
            checks = self.verify()
            self.acknowledge(checks)
            outcome = "passed"
        except BaseException as error:
            failure = {"type": type(error).__name__, "message": str(error), "at": utc(), "runId": self.state.get("runId"), "requestId": self.state.get("requestId"), "traceback": traceback.format_exc()}
            atomic_json(self.evidence / "failure.json", failure)
            self.log(f"Acceptance failed; the same run will not be re-executed: {error}")
        finally:
            try:
                self.stop_private_service()
            except BaseException as error:
                atomic_json(self.evidence / "cleanup-error.json", {"type": type(error).__name__, "message": str(error), "at": utc()})
                self.log(f"Private cleanup is unresolved: {error}")
                outcome = "failed"
            try:
                self.copy_logs()
            except OSError as error:
                self.log(f"Some private logs could not be copied: {error}")
            self.state.update(status=outcome, finishedAt=utc(), failure=failure)
            self.save()
            atomic_json(self.evidence / "summary.json", {"status": outcome, "runId": self.state.get("runId"), "requestId": self.state.get("requestId"), "checks": self.state.get("checks"), "failure": failure, "evidenceDir": str(self.evidence)})
            self.log(f"Acceptance {outcome}; evidence={self.evidence}")
        return 0 if outcome == "passed" else 1


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--launcher", required=True, type=Path, help="Staged new scripts/launch-buddy.sh")
    for option in ("state-dir", "runtime-root", "work-dir", "evidence-dir"):
        parser.add_argument(f"--{option}", required=True, type=Path)
    parser.add_argument("--task-file", type=Path, default=Path(__file__).with_name("acceptance-task.md"))
    parser.add_argument("--request-id")
    parser.add_argument("--timeout-seconds", type=int, default=900)
    parser.add_argument("--live-timeout-seconds", type=int, default=300)
    value = parser.parse_args()
    parser.error("timeout-seconds must be between 600 and 86400") if not 600 <= value.timeout_seconds <= 86400 else None
    return value


if __name__ == "__main__":
    os.umask(0o077)
    args = arguments()
    try:
        raise SystemExit(Driver(args).run())
    except (AcceptanceError, OSError, ValueError) as error:
        print(f"Acceptance setup refused: {error}", file=sys.stderr, flush=True)
        raise SystemExit(2)
