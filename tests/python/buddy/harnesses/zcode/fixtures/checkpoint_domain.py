"""Private process owners for the two concurrent checkpoint scenarios.

Only JSON fixture commands cross the parent pipe. Each child reuses the real
ZcodeFixtureCase, registered Worker executor and native mock in its own domain.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import select
import subprocess
import sys
import tempfile
import time
import traceback


class CheckpointDomain:
    """The parent owns this Popen; the child owns its controller/native group."""

    def __init__(self, test, case):
        from support import _child_environment

        temporary = tempfile.TemporaryDirectory(prefix="zh-", dir=Path(tempfile.gettempdir()).resolve())
        test.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.log = (self.root / "owner.log").open("wb")
        test.addCleanup(self.log.close)
        self.process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve())],
            env=_child_environment(self.root), stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=self.log,
        )
        test.addCleanup(self.close)
        self.live = self.command({"op": "start", "case": case})

    def command(self, value, timeout=50):
        from protocol.fixtures.ctwo_controller import exchange

        try:
            reply = exchange(self.process, value, timeout=timeout)
        except Exception as error:
            raise AssertionError(f"checkpoint owner pipe failed: {error}; "
                                 f"{(self.root / 'owner.log').read_text()}") from error
        if not reply.get("ok"):
            raise AssertionError(reply)
        return reply

    def close(self):
        evidence = {"ownerPid": self.process.pid, "nativeStopped": None}
        try:
            if self.process.poll() is None:
                # Cooperative stop first: only the child can stop the controller
                # and native processes that it created and retained handles for.
                reply = self.command({"op": "stop"}, timeout=30)
                evidence.update(reply)
                if not reply.get("cleanupConfirmed"):
                    raise AssertionError(f"checkpoint child shutdown unconfirmed: {reply}")
            code = self.process.wait(timeout=10)
            evidence["ownerExitCode"] = code
            if code != 0:
                raise AssertionError(f"checkpoint owner exit {code}: "
                                     f"{(self.root / 'owner.log').read_text()}")
        except Exception:
            if self.process.poll() is None:
                # This kills only our saved child, never its descendants by PID.
                # Its descendants' stop state remains unknown on this path.
                self.process.kill()
                self.process.wait(timeout=10)
            raise
        finally:
            # Keep stop facts outside the normal TemporaryDirectory cleanup.
            # A missing native receipt stays unknown even when the owner exits.
            audit = Path(os.environ["TMPDIR"]) / "checkpoint-domain-processes.jsonl"
            with audit.open("a") as stream:
                stream.write(json.dumps(evidence) + "\n")
            self.process.stdin.close()
            self.process.stdout.close()
            self.log.close()


def read_command(timeout=60):
    """Bound the child side too, including partial input and a vanished parent."""
    deadline = time.monotonic() + timeout
    raw = bytearray()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([sys.stdin], [], [], remaining)[0]:
            raise TimeoutError("checkpoint owner command deadline expired")
        chunk = os.read(sys.stdin.fileno(), 4096)
        if not chunk:
            return None
        raw.extend(chunk)
        if len(raw) > 64 * 1024:
            raise ValueError("checkpoint command exceeds its bound")
        if b"\n" in raw:
            line, _, extra = raw.partition(b"\n")
            if extra.strip():
                raise ValueError("checkpoint commands must be exchanged one at a time")
            return json.loads(line)


def serve():
    # Determine every private root before importing the SDK or role modules.
    temporary = tempfile.TemporaryDirectory(prefix="zd-", dir=Path(tempfile.gettempdir()).resolve())
    root = Path(temporary.name).resolve()
    home, state, runtime = root / "home", root / "state", root / "runtime"
    for directory in (home, state, runtime, state / "ipc"):
        directory.mkdir(mode=0o700)
    environment = {key: value for key, value in os.environ.items()
                   if not key.startswith(("BUDDY_", "ANTHROPIC_", "C2_"))
                   and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       BUDDY_STATE_DIR=str(state), BUDDY_RUNTIME_ROOT=str(runtime),
                       BUDDY_DEV_SOURCE="1", C2_IPC_ROOT=str(state / "ipc"),
                       BUDDY_CHECKS_TMPDIR=os.environ["TMPDIR"])
    # Native config overrides from the parent also stay within this child.
    for key, suffix in (("CODEX_HOME", ".codex"), ("CLAUDE_CONFIG_DIR", ".claude"),
                        ("ZCODE_DATA_BASE_DIR", ".zcode"), ("DSH_HOME", ".dsh"),
                        ("XDG_CONFIG_HOME", ".config"), ("XDG_DATA_HOME", ".local/share"),
                        ("XDG_CACHE_HOME", ".cache"), ("XDG_STATE_HOME", ".local/state")):
        environment[key] = str(home / suffix)
    environment.update(XDG_RUNTIME_DIR=str(runtime),
                       ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(home / "builtin.json"),
                       ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(home / "personal.json"),
                       BUDDY_MODEL_FACTS_FILE=str(root / "offline-facts.json"))
    os.environ.clear()
    os.environ.update(environment)

    from unittest.mock import patch
    import c_two as cc
    from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
    from hey_my_buddy.private_dirs import context_root
    from support import shutdown_private_rpc

    fixture = ZcodeFixtureCase()
    # Reuse its complete setup, selecting our already-created private root.
    with patch("buddy.harnesses.zcode.test_zcode.tempfile.TemporaryDirectory",
               return_value=temporary):
        fixture.setUp()
    fixture.addCleanup(shutdown_private_rpc)
    context = handle = channel = asked = None
    cleaned = False
    native_stopped = None

    def cleanup():
        nonlocal cleaned
        if not cleaned:
            if handle is not None:
                if handle.group_alive():
                    fixture.adapter.cancel(handle)
                fixture.assertTrue(handle.shutdown_confirmed(),
                                   "owned controller stop unconfirmed; native state unknown")
            fixture.assertTrue(fixture.doCleanups(), "owned fixture cleanup unconfirmed")
            cleaned = True
        return {"cleanupConfirmed": True, "controllerStopped": handle is None or handle.shutdown_confirmed(),
                "nativeStopped": native_stopped, "rpcShutdownConfirmed": True, "root": str(root)}

    try:
        while command := read_command():
            try:
                operation = command["op"]
                facts = {}
                if operation == "start":
                    fixture.assertIsNone(handle, "one domain owns exactly one attempt")
                    context = fixture.context(command["case"], timeout=40)
                    handle = fixture.adapter.start(context)
                    fixture.own_handle(handle)
                    channel = fixture.ready_channel(handle)
                    fixture.assertEqual(Path(cc.local_endpoint_context().root), state / "ipc",
                                        "holder must use its own actual process-level IPC domain")
                    material = handle.role_run_control["live"]
                    descriptor = json.loads(Path(material["readyFile"]).read_text())
                    facts = {"attemptId": context.attempt_id, "ownerPid": os.getpid(),
                             "controllerPid": handle.pid, "instanceId": material["instanceId"],
                             "token": material["token"], "address": descriptor["address"],
                             "state": str(state), "runtime": str(runtime), "home": str(home),
                             "ipcRoot": str(cc.local_endpoint_context().root),
                             "live": handle.process.poll() is None}
                elif operation == "live":
                    channel = fixture.ready_channel(handle)
                    facts = {"live": handle.process.poll() is None}
                elif operation == "ask":
                    _, asked = fixture.ask(handle, command["id"], command["question"], channel)
                    facts = {"correlation": asked.native_correlation.value}
                elif operation == "release":
                    fixture.release(context)
                elif operation == "finish-accepted":
                    fixture.assertIsNotNone(fixture.wait_file(context, "finish-accepted"))
                elif operation == "late-asked":
                    (context_root(context, "zcode") / "native-logs" / "late-asked").touch()
                elif operation == "collect":
                    fixture.assertIsNotNone(handle.wait(40), "controller did not exit")
                    outcome = fixture.adapter.collect(handle, context)
                    native_stopped = outcome.result.get("processState", {}).get("shutdownConfirmed")
                    facts = {"status": outcome.status, "report": outcome.to_report(),
                             "result": outcome.result, "shutdown": outcome.shutdown_confirmed,
                             "controllerStopped": handle.shutdown_confirmed(),
                             "nativeStopped": native_stopped,
                             "records": {key: fixture.inquiry_records(context, key)
                                         for key in command["ids"]},
                             "methods": fixture.native_log(context, "methods.jsonl"),
                             "journal": fixture.inquiry_results_path(context).read_text()}
                elif operation == "stop":
                    stop = cleanup()
                    print(json.dumps({"ok": True, **stop}), flush=True)
                    return
                else:
                    raise ValueError(f"unknown fixture command: {operation}")
                print(json.dumps({"ok": True, **facts}), flush=True)
            except Exception:
                logs = {name: Path(path).read_text()[-12000:]
                        for name, path in context.log_paths().items() if Path(path).is_file()} if context else {}
                print(json.dumps({"ok": False, "error": traceback.format_exc(),
                                  "controllerLogs": logs}), flush=True)
    finally:
        cleanup()


if __name__ == "__main__":
    serve()
