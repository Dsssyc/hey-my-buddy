"""Two explicitly approved DSH Worker turns over a private real board.

Preparation uses native health/catalog discovery only. Execution deliberately
injects a capable DshAdapter instance at the two existing selection seams;
the registered product declaration remains unchanged until this proof passes.
Board transport is local, while both Worker/controller/native processes,
session tools, inquiry forwarding and C-Two live endpoints are real.
No helper removes files, installs a runtime or reads user credential files.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
from hey_my_buddy.buddy.harnesses.c_two_live import C_TWO_IPC_DIRECTORY
from hey_my_buddy.buddy.harnesses.registry import run_seam
from hey_my_buddy.buddy.harnesses.run_contract import decode_run_result
from hey_my_buddy.buddy.roles import controller as role_seam
from hey_my_buddy.buddy.roles.run_execution import WorkerRunExecutor
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.private_dirs import ensure_private_dir
from support import InProcessBoard


TASK = """This is a two-turn native memory probe. Do not use file, command,
search, browser, or delegation tools. Use only the supplied buddy checkpoint,
inquiry answer and finish tools. Poll a checkpoint for the Host's question,
answer it through the inquiry tool, and finish immediately afterwards.
On the initial turn, remember the memory key from that question without
echoing the key in any assistant message, checkpoint, answer or final summary.
Answer and finish with 'memorized'. On the native-session continuation, answer
the new question with 'active', then finish with the previous turn's exact
memory key as the summary. Retrieve it only from the native conversation.
If no question is returned, poll at most four checkpoints; then report
attention rather than claiming success. Keep every response very short."""


class ProbeFailure(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise ProbeFailure(message)


def write_new(path: Path, value) -> None:
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def input_guard(context, key: str, previous: str) -> None:
    require(context.turn_input["resumeMode"] == "native-session", "common selection did not resume")
    require(context.turn_input["previousSessionId"] == previous, "wrong previous native session")
    document = json.dumps({"spec": context.spec, "turn": context.turn_input}, ensure_ascii=False)
    require(key not in document, "the second input replays the first turn's memory key")


def usage_guard(usage) -> None:
    require(isinstance(usage, dict) and usage.get("scope") == "attempt"
            and usage.get("completeness") in ("complete", "partial")
            and any(type(usage.get(name)) is int and usage[name] >= 0
            for name in ("inputTokens", "outputTokens", "totalTokens")),
            "this round has no observed token counters")


class CapturingExecutor(WorkerRunExecutor):
    def __init__(self, candidate, root: Path, key: str):
        super().__init__(candidate, run_seam("dsh"))
        self.root, self.key = root, key
        self.previous: str | None = None
        self.completed: list[dict] = []
        self.starts = 0
        self.handles = []

    def start(self, context):
        require(self.starts < 2, "the probe permits at most two Worker starts")
        if self.completed:
            require(self.previous is not None, "the first turn established no session")
            input_guard(context, self.key, self.previous)
        write_new(self.root / f"input-{len(self.completed) + 1}.json",
                  {"spec": context.spec, "turn": context.turn_input})
        self.starts += 1
        handle = super().start(context)
        self.handles.append(handle)
        return handle

    def collect(self, handle, context):
        outcome = super().collect(handle, context)
        number = len(self.completed) + 1
        entry = {"report": outcome.to_report(),
                 "controllerStopped": handle.shutdown_confirmed()}
        try:
            request = Path(handle.role_run_control["requestFile"]).read_bytes()
            write_new(self.root / f"run-request-{number}.json", json.loads(request))
            native = decode_run_result(Path(handle.log_paths["stdout"]).read_bytes())
            record_ref = next(ref for ref in native.evidence_refs if ref.kind == "dsh-session-record")
            record_path = Path(record_ref.location)
            require(record_path.is_relative_to(self.root), "session facts are outside the private probe root")
            record = json.loads(record_path.read_bytes())
            write_new(self.root / f"session-facts-{number}.json", record)
            write_new(self.root / f"run-result-{number}.json", native.to_payload())
            entry.update(nativeStop=native.stop_evidence.to_payload(),
                         activity=native.activity.value if native.activity else None,
                         toolEvidence=native.tool_evidence.value if native.tool_evidence else None,
                         recordFacts=record,
                         inputReplaysKey=self.key.encode() in request,
                         requestSha256=hashlib.sha256(request).hexdigest())
        except Exception as error:
            # Recording must not replace the role's real report or prevent
            # its durable receipt; the Host checks this after Worker delivery.
            entry["captureError"] = f"{type(error).__name__}: {error}"
        write_new(self.root / f"round-{number}.json", entry)
        self.completed.append(entry)
        if number == 1:
            self.previous = outcome.result.get("sessionId")
        return outcome


def open_board(root: Path) -> InProcessBoard:
    board = InProcessBoard(ensure_private_dir(root / "state"), lease_seconds=120)
    # Undo this fixture's synthetic refresh stub: use actual source discovery.
    board.service.harnesses.__dict__.pop("refresh", None)
    return board


def prepare(root: Path) -> dict:
    require(not (root / "prepared.json").exists(), "use a newly created probe directory")
    repo = ensure_private_dir(root / "repo")
    git_env = {**os.environ, "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_CONFIG_SYSTEM": "/dev/null"}
    for arguments in (("init", "-q", "-b", "main"),
                      ("-c", "user.name=Probe", "-c", "user.email=probe@example.invalid",
                       "commit", "--allow-empty", "-qm", "private native memory probe")):
        subprocess.run(["git", *arguments], cwd=repo, env=git_env, check=True, capture_output=True)
    board = open_board(root)
    try:
        health = board.service.harnesses.refresh("dsh", force=True)
        require(health.get("available") is True, "installed DSH is unavailable")
        configuration = {"adapter": "dsh", "provider": "deepseek-official",
                         "model": "deepseek-v4-flash", "effort": "off"}
        from hey_my_buddy.blackboard.catalog.catalog import validate_configuration
        validate_configuration(configuration, directory=board.directory)
        facts = {"configuration": configuration, "health": health,
                 "modelCalls": 0, "productCapability": DshAdapter.native_resume}
        write_new(root / "prepared.json", facts)
        return facts
    finally:
        board.service.harnesses.close()


def run_round(board, executor, run_id: str, question: str, number: int, root: Path):
    errors: list[str] = []
    worker = Worker(f"native-smoke-{number}", board.directory, client=board.client(),
                    adapters=("dsh",), retry_seconds=1)
    descriptor = worker.live.start()
    socket = Path(C_TWO_IPC_DIRECTORY) / (descriptor.address.removeprefix("ipc://") + ".sock")
    require(socket.is_socket(), "Worker endpoint was not created")

    def run():
        try:
            worker.run(max_iterations=1)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

    thread = threading.Thread(target=run, name=f"native-smoke-{number}")
    thread.start()
    inquiry_id = f"memory-question-{number}"
    deadline = time.monotonic() + 240
    controller_sockets: dict[str, bool] = {}
    question_posted = False
    try:
        while thread.is_alive():
            require(time.monotonic() < deadline, "probe deadline expired")
            for handle in executor.handles[number - 1:]:
                ready = getattr(handle, "role_live_descriptor", None)
                if ready is not None and ready.socket is not None:
                    path = ready.socket.path
                    controller_sockets[path] = controller_sockets.get(path, False) or Path(path).is_socket()
            # Posting while the task is still queued permanently marks this
            # inquiry unavailable. Observe without posting until the actual
            # holder is attached and reports its native session ready.
            if not question_posted:
                observed = board.call("inquiry_observe", {"runId": run_id})
                live = observed.get("live") or {}
                question_posted = live.get("available") is True and bool(live.get("sessionId"))
            if question_posted:
                board.call("inquiry_observe", {"runId": run_id,
                           "inquiryId": inquiry_id, "question": question})
            thread.join(0.25)
    finally:
        if thread.is_alive():
            worker.stop.set()
            thread.join(25)
        require(not thread.is_alive(), "owning Worker has not stopped; preserve its directory")
        worker.live.stop()
        write_new(root / f"worker-{number}.json",
                  {"errors": errors, "address": descriptor.address,
                   "socketExistedBefore": True, "socketExistsAfter": socket.exists(),
                   "controllerSockets": [{"path": path, "existedBefore": existed,
                                          "existsAfter": Path(path).exists()}
                                         for path, existed in controller_sockets.items()]})
    require(not errors, "the Worker raised an error")
    require(not socket.exists(), "Worker endpoint file survived stop")
    require(controller_sockets and all(controller_sockets.values()), "the live controller socket was not observed")
    require(all(not Path(path).exists() for path in controller_sockets), "controller endpoint file survived stop")
    require(len(executor.completed) == number, "the role did not collect a complete result")
    entry = executor.completed[-1]
    report = entry["report"]
    require(report["status"] == "ok", "native Worker round failed")
    require("captureError" not in entry, "the probe could not capture the actual request and facts")
    require(report["shutdownConfirmed"] is True and entry["controllerStopped"] is True,
            "outer controller stop is unconfirmed")
    require(report["result"]["processState"]["shutdownConfirmed"] is True,
            "native group stop is unconfirmed")
    usage_guard(report["result"].get("tokenUsage"))
    tools = entry["toolEvidence"] or {}
    require(tools.get("streamComplete") is True and tools.get("truncated") is False,
            "the native tool stream is incomplete")
    # The existing collector excludes only session calls with verified signed
    # receipts. Every remaining call is retained, including unknown tools.
    require(tools.get("toolCalls") == 0, "a non-session tool ran; it could retrieve the key from a file")
    if number == 2:
        require(entry["inputReplaysKey"] is False, "the actual second RunRequest replays the key")
        require(entry["recordFacts"].get("resumeBoundary", {}).get("proven") is True,
                "the resumed turn's usage boundary is unproven")
    # Import the final durable answer projection after the holder stops. A
    # short turn can settle between the last live poll and Worker delivery.
    final_observation = board.call("inquiry_observe", {"runId": run_id,
                                   "inquiryId": inquiry_id, "question": question})
    write_new(root / f"inquiry-observation-{number}.json", final_observation)
    message = board.client().get_message(inquiry_id, runId=run_id)
    write_new(root / f"inquiry-{number}.json", message)
    require(message["state"] == "answered", "the real inquiry was not answered")
    expected_answer = "memorized" if number == 1 else "active"
    require(message["answer"]["text"].strip() == expected_answer, "unexpected inquiry answer")
    view = board.call("workflow_get", {"runId": run_id, "includeAudit": True})
    write_new(root / f"view-{number}.json", view)
    require(view["state"] == "delivered", "the board did not import and seal the round")
    return entry, view


def execute(root: Path) -> dict:
    prepared = json.loads((root / "prepared.json").read_bytes())
    require(not (root / "paid-started.json").exists(), "this probe cannot be rerun")
    write_new(root / "paid-started.json", {"requestedWorkerTurns": 2})
    board = open_board(root)
    candidate = DshAdapter()
    original_capability = DshAdapter.native_resume
    candidate.native_resume = True
    key = "memory-" + secrets.token_hex(12)
    executor = CapturingExecutor(candidate, root, key)
    try:
        with ExitStack() as stack:
            original_selection = WorkflowCoordinator._execution_adapter
            stack.enter_context(patch.object(WorkflowCoordinator, "_execution_adapter",
                staticmethod(lambda name: candidate if name == "dsh" else original_selection(name))))
            original_executor = role_seam.worker_executor
            stack.enter_context(patch.object(role_seam, "worker_executor",
                lambda name: executor if name == "dsh" else original_executor(name)))
            submitted = board.call("workflow_submit", {**prepared["configuration"],
                "requestId": "native-memory-probe", "hostId": "codex-adr025",
                "task": TASK, "cwd": str(root / "repo"), "timeoutSeconds": 180,
                "executionWorkspace": {"kind": "existing", "access": "write"}})
            run_id = submitted["runId"]
            first, view = run_round(board, executor, run_id,
                f"Remember the memory key {key}. Never echo it this turn. Answer memorized.", 1, root)
            first_text = json.dumps(first["report"]["result"].get("turn"), ensure_ascii=False)
            require(key not in first_text, "the first outcome echoed the memory key")
            require(first["report"]["result"]["nativeSession"]["resumable"] is True,
                    "the complete first turn did not qualify for native resume")
            continued = board.call("workflow_continue", {"runId": run_id,
                "commandId": "native-memory-continue", "expectedRevision": view["revision"],
                "input": "Continue the memory probe using the native conversation; await the new Host question.",
                "helperPolicy": "keep", **submitted["control"]})
            write_new(root / "continued.json", continued)
            second, _ = run_round(board, executor, run_id,
                "Confirm this restored turn is active. Answer active, then finish with the remembered key.", 2, root)
            first_result, second_result = first["report"]["result"], second["report"]["result"]
            require(first_result["sessionId"] == second_result["sessionId"], "session identity changed")
            require(second_result["turn"]["outcome"]["summary"].strip() == key, "native history did not retain the key")
            require(DshAdapter.native_resume == original_capability, "probe changed the product capability")
            result = {"confirmed": True, "runId": run_id, "sessionId": executor.previous,
                      "completedWorkerTurns": len(executor.completed), "memoryKey": key,
                      "tokenUsage": [first_result.get("tokenUsage"), second_result.get("tokenUsage")],
                      "productCapabilityUnchanged": True}
            write_new(root / "confirmed.json", result)
            return result
    finally:
        board.service.harnesses.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "execute"))
    parser.add_argument("--root", required=True, type=Path)
    args = parser.parse_args()
    root = args.root.absolute()
    require(root.is_dir(), "Host must create and register the exact root first")
    require(os.environ.get("BUDDY_STATE_DIR") == str(root / "state"), "private state root required")
    require(os.environ.get("BUDDY_RUNTIME_ROOT") == str(root / "runtime"), "private runtime root required")
    require(os.environ.get("BUDDY_DEV_SOURCE") == "1", "source runtime required")
    require(not any(k.startswith("ANTHROPIC_") or k in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")
                    for k in os.environ), "clear inherited environment before the probe")
    try:
        facts = prepare(root) if args.mode == "prepare" else execute(root)
        print(json.dumps(facts, ensure_ascii=False))
        return 0
    except Exception as error:
        write_new(root / f"{args.mode}-failed.json", {"type": type(error).__name__, "message": str(error),
                  "automaticPaidRetry": False})
        print(f"{args.mode} failed: {type(error).__name__}: {error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
