"""Two explicitly approved DSH Worker turns over a private real board.

Preparation uses native health/catalog discovery only. Execution deliberately
injects a capable DshAdapter at the workflow selection and the child execution
selection seams; the registered product declaration remains unchanged until
this proof passes. The private board serves real C-Two requests from each
isolated execution. Native sessions, inquiry forwarding and live endpoints are real.
No helper removes files, installs a runtime or reads user credential files.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator
from hey_my_buddy.buddy.harnesses.dsh.adapter import DshAdapter
import c_two as cc
from hey_my_buddy.buddy.harnesses.registry import run_seam
from hey_my_buddy.buddy.harnesses.run_contract import encode_run_request
from hey_my_buddy.buddy.harnesses import controller as launcher
from hey_my_buddy.buddy.roles import controller as role_seam
from hey_my_buddy.buddy.roles.run_execution import WorkerRunExecutor
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.private_dirs import ensure_private_dir
from hey_my_buddy.protocol import rpc_config
from hey_my_buddy.protocol.contracts import BuddyControl, BuddyWait, CONTROL_NAME, WAIT_NAME
from hey_my_buddy.blackboard.service.daemon import atomic_json
from support import InProcessBoard


TASK = """This is a two-turn native memory probe. Do not use file, command,
search, browser, or micro task tools. Use only the supplied buddy checkpoint,
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
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
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
        self.number = 2 if (root / "round-1.json").exists() else 1
        self.previous = (json.loads((root / "round-1.json").read_bytes())["report"]["result"]["sessionId"]
                         if self.number == 2 else None)
        self.starts = 0

    def start(self, context):
        require(self.starts == 0, "the probe permits one start per execution")
        if self.number == 2:
            require(self.previous is not None, "the first turn established no session")
            input_guard(context, self.key, self.previous)
        write_new(self.root / f"input-{self.number}.json",
                  {"spec": context.spec, "turn": context.turn_input})
        self.starts += 1
        return super().start(context)

    def collect(self, handle, context):
        outcome = super().collect(handle, context)
        number = self.number
        entry = {"report": outcome.to_report(),
                 "nativeStopped": handle.shutdown_confirmed()}
        try:
            request = encode_run_request(handle.request)
            write_new(self.root / f"run-request-{number}.json", json.loads(request))
            native = handle.result
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
                         inputReplaysKey=self.key.encode() in request.encode(),
                         requestSha256=hashlib.sha256(request.encode()).hexdigest())
        except Exception as error:
            # Recording must not replace the role's real report or prevent
            # its durable receipt; the Host checks this after Worker delivery.
            entry["captureError"] = f"{type(error).__name__}: {error}"
        write_new(self.root / f"round-{number}.json", entry)
        return outcome


def child_execution(startup: Path) -> int:
    """Inject the capable executor only in the execution that owns the native run."""
    from hey_my_buddy.buddy.runtime import execution as runtime
    material = json.loads(startup.read_bytes())
    root = Path(material["stateDir"]).parent
    context = json.loads((root / "probe-context.json").read_bytes())
    require(context["stateDir"] == material["stateDir"], "foreign probe context")
    candidate = DshAdapter()
    candidate.native_resume = True
    executor = CapturingExecutor(candidate, root, context["memoryKey"])
    sys.argv = [str(Path(__file__).resolve()), "--startup", str(startup)]
    original_executor = role_seam.worker_executor
    with patch.object(role_seam, "worker_executor",
                      lambda name: executor if name == "dsh" else original_executor(name)):
        return runtime.main()


def publish_board(board: InProcessBoard) -> None:
    """Give the isolated execution a real C-Two route to this same private board."""
    rpc_config.configure_server(board.directory)
    cc.register(BuddyControl, board.service, name=CONTROL_NAME,
                concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
    cc.register(BuddyWait, board.wait_service, name=WAIT_NAME,
                concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
    atomic_json(board.directory / "control.json", {"address": cc.server_address(), "token": "test-token"})


def open_board(root: Path, *, discover: bool = False) -> InProcessBoard:
    board = InProcessBoard(ensure_private_dir(root / "state"), lease_seconds=120)
    if discover:
        # Preparation alone performs actual native health/catalog discovery.
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
    board = open_board(root, discover=True)
    try:
        health = board.service.harnesses.refresh("dsh", force=True)
        require(health.get("available") is True, "installed DSH is unavailable")
        buddy = {"harness": "dsh", "provider": "deepseek-official",
                         "model": "deepseek-v4-flash", "effort": "off"}
        from hey_my_buddy.blackboard.catalog.catalog import validate_buddy
        validate_buddy(buddy, directory=board.directory)
        facts = {"buddy": buddy, "health": health,
                 "modelCalls": 0, "productCapability": DshAdapter.native_resume}
        write_new(root / "prepared.json", facts)
        return facts
    finally:
        board.service.harnesses.close()


def run_round(board, run_id: str, question: str, number: int, root: Path):
    publish_board(board)
    errors: list[str] = []
    worker = Worker(f"native-smoke-{number}", board.directory, client=board.client(),
                    adapters=("dsh",))
    descriptor = worker.live.start()
    context = cc.local_endpoint_context(root=str(board.directory / "ipc"))
    require(cc.inspect_endpoint(descriptor.address, context=context)["status"] == "present",
            "Worker endpoint was not created")

    def run():
        try:
            worker.run(max_iterations=1)
        except Exception as error:
            errors.append(f"{type(error).__name__}: {error}")

    handles = []
    original_launch = launcher.launch_controller

    def launch_probe(*, prepare, **options):
        def prepared():
            argv, cwd, environment = prepare()
            startup = argv[argv.index("--startup") + 1]
            return [sys.executable, str(Path(__file__).resolve()), "execution",
                    "--startup", startup], cwd, environment
        handle = original_launch(prepare=prepared, **options)
        handles.append(handle)
        return handle

    thread = threading.Thread(target=run, name=f"native-smoke-{number}")
    inquiry_id = f"memory-question-{number}"
    deadline = time.monotonic() + 240
    execution_endpoints: dict[str, bool] = {}
    question_posted = False
    launch_patch = patch.object(launcher, "launch_controller", launch_probe)
    launch_patch.start()
    try:
        thread.start()
        while thread.is_alive():
            require(time.monotonic() < deadline, "probe deadline expired")
            for handle in handles:
                ready = getattr(handle, "role_live_descriptor", None)
                if ready is not None and ready.endpoint_credential is not None:
                    address = ready.address
                    execution_endpoints[address] = execution_endpoints.get(address, False) or (
                        cc.inspect_endpoint(address, context=context)["status"] == "present")
            # Posting while the task is queued makes the inquiry unavailable.
            observed = board.call("inquiry_observe", {"runId": run_id})
            live = observed.get("live") or {}
            if not question_posted:
                question_posted = live.get("available") is True and bool(live.get("sessionId"))
            if question_posted:
                board.call("inquiry_observe", {"runId": run_id,
                           "inquiryId": inquiry_id, "question": question})
            thread.join(0.25)
    finally:
        if thread.is_alive():
            worker.stop.set()
            thread.join(25)
        try:
            require(not thread.is_alive(), "owning Worker has not stopped; preserve its directory")
            worker.live.stop()
            write_new(root / f"worker-{number}.json",
                      {"errors": errors, "address": descriptor.address,
                       "endpointStatusBefore": "present",
                       "endpointStatusAfter": cc.inspect_endpoint(descriptor.address, context=context)["status"],
                       "executionStopped": handles[0].shutdown_confirmed() if len(handles) == 1 else None,
                       "executionEndpoints": [{"address": address, "existedBefore": existed,
                           "statusAfter": cc.inspect_endpoint(address, context=context)["status"]}
                           for address, existed in execution_endpoints.items()]})
        finally:
            launch_patch.stop()
    require(not errors, "the Worker raised an error")
    require(cc.inspect_endpoint(descriptor.address, context=context)["status"] == "absent", "Worker endpoint file survived stop")
    require(execution_endpoints and all(execution_endpoints.values()), "the live execution socket was not observed")
    require(all(cc.inspect_endpoint(address, context=context)["status"] == "absent"
                for address in execution_endpoints), "execution endpoint file survived stop")
    require(len(handles) == 1, "the Worker did not own exactly one execution")
    require((root / f"round-{number}.json").exists(), "the role did not collect a complete result")
    entry = json.loads((root / f"round-{number}.json").read_bytes())
    report = entry["report"]
    require(report["status"] == "ok", "native Worker round failed")
    require("captureError" not in entry, "the probe could not capture the actual request and facts")
    require(report["shutdownConfirmed"] is True and entry["nativeStopped"] is True,
            "native run stop is unconfirmed")
    require(handles[0].shutdown_confirmed() is True,
            "outer execution process stop is unconfirmed")
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
    write_new(root / "probe-context.json", {"stateDir": str(board.directory), "memoryKey": key})
    try:
        original_selection = WorkflowCoordinator._execution_adapter
        with patch.object(WorkflowCoordinator, "_execution_adapter",
                          staticmethod(lambda name: candidate if name == "dsh" else original_selection(name))):
            submitted = board.call("workflow_submit", {**prepared["buddy"],
                "requestId": "native-memory-probe", "hostId": "codex-adr025",
                "task": TASK, "cwd": str(root / "repo"), "expectedSeconds": 180,
                "executionWorkspace": {"kind": "existing", "access": "write"}})
            run_id = submitted["runId"]
            first, view = run_round(board, run_id,
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
            second, _ = run_round(board, run_id,
                "Confirm this restored turn is active. Answer active, then finish with the remembered key.", 2, root)
            first_result, second_result = first["report"]["result"], second["report"]["result"]
            require(first_result["sessionId"] == second_result["sessionId"], "session identity changed")
            require(second_result["turn"]["outcome"]["summary"].strip() == key, "native history did not retain the key")
            require(DshAdapter.native_resume == original_capability, "probe changed the product capability")
            result = {"confirmed": True, "runId": run_id, "sessionId": first_result["sessionId"],
                      "completedWorkerTurns": 2, "memoryKey": key,
                      "tokenUsage": [first_result.get("tokenUsage"), second_result.get("tokenUsage")],
                      "productCapabilityUnchanged": True}
            write_new(root / "confirmed.json", result)
            return result
    finally:
        board.service.harnesses.close()
        cc.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "execute", "execution"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--startup", type=Path)
    args = parser.parse_args()
    if args.mode == "execution":
        require(args.startup is not None, "execution startup path required")
        return child_execution(args.startup)
    require(args.root is not None, "private probe root required")
    root = args.root.absolute()
    require(root.is_dir(), "Host must create and register the exact root first")
    require(os.environ.get("HMB_STATE_DIR") == str(root / "state"), "private state root required")
    require(os.environ.get("HMB_RUNTIME_ROOT") == str(root / "runtime"), "private runtime root required")
    require(os.environ.get("HMB_DEV_SOURCE") == "1", "source runtime required")
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
