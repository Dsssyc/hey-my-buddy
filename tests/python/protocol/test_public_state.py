"""Public directory selection against isolated real services, without model calls.

Each case has a separate interpreter and one frozen C-Two root. Evidence and
owned process receipts remain under BUDDY_CHECKS_TMPDIR for Host inspection.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

from support import (
    _child_environment,
    shutdown_private_rpc,
    stop_private_service,
    stop_private_workers,
    write_catalog_fixture,
)
from protocol.test_transport_attach import MutationRecorder, snapshot
from hey_my_buddy.protocol import transport
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.errors import BoardError

ROOT = Path(__file__).resolve().parents[3]
DAEMON_FIXTURE = ROOT / "tests/python/blackboard/service/fixtures/daemon_with_catalog.py"
DAEMON_COMMAND = [sys.executable, str(Path(__file__).resolve()), "--daemon-fixture"]


def prepare_daemon_spawn(command, kwargs, state: Path):
    """Keep the source-only production environment able to import this fixture."""
    environment = kwargs["env"]
    paths = [str(ROOT / "tests/python"), str(ROOT / "src")]
    if environment.get("PYTHONPATH"):
        paths.append(environment["PYTHONPATH"])
    environment.update(
        PYTHONPATH=os.pathsep.join(paths),
        PYTHONDONTWRITEBYTECODE="1",
        BUDDY_MODEL_CATALOG_FILE=str(state / "model-catalog.json"),
        BUDDY_MODEL_FACTS_FILE=str(state / "model-facts-fixture.json"),
        BUDDY_CHECKS_TMPDIR=os.environ["BUDDY_CHECKS_TMPDIR"],
    )
    return [command[0], *DAEMON_COMMAND[1:]]


def cleanup_owned_case(state, modes, handles, evidence):
    """Attempt every owned teardown step and retain secondary failures separately."""
    failures = []

    def attempt(stage, operation):
        try:
            operation()
        except Exception as error:
            failures.append({"stage": stage, "type": type(error).__name__, "message": str(error)})
            return False
        return True

    for path, mode in modes.items():
        def restore(path=path, mode=mode):
            info = path.lstat()
            assert stat.S_ISDIR(info.st_mode), "fixture directory was replaced"
            if os.name != "nt":
                assert info.st_uid == os.geteuid(), "fixture directory changed owner"
            if stat.S_IMODE(info.st_mode) != mode:
                path.chmod(mode)
        attempt("restore:" + str(path), restore)
    if handles:
        service = attempt("stop_service", lambda: stop_private_service(state))
        workers = attempt("stop_workers", lambda: stop_private_workers(state))
        evidence["serviceAndWorkerLocksReleased"] = service and workers
    for process, receipt in zip(handles, evidence["processes"]):
        def wait(process=process, receipt=receipt):
            receipt["waitExit"] = process.wait(timeout=20)
            if "failure" not in evidence:
                assert receipt["waitExit"] == 0, receipt
        if not attempt("wait:" + str(process.pid), wait):
            receipt["shutdownUnconfirmed"] = receipt["waitExit"] is None
    evidence["shutdownCompleted"] = attempt("shutdown_rpc", shutdown_private_rpc)
    return failures


def run_case(name: str, work: Path, *, substitutes_only: bool = False) -> None:
    """Own and collect every daemon handle; never switch native roots in process."""
    default = Path(os.environ["HOME"]) / ".local/share/hey-my-buddy/state"
    state = work / "selected" if name.startswith(("explicit", "environment", "late")) else default
    client = None
    if name.startswith("explicit"):
        os.environ["BUDDY_STATE_DIR"] = str(work / "unserved-environment")
    elif name.startswith("late"):
        os.environ["BUDDY_STATE_DIR"] = str(work / "construction-environment")
        client = BoardClient(autostart=name.endswith("auto"))
        assert client.state_dir is None
        os.environ["BUDDY_STATE_DIR"] = str(state)
    elif name.startswith("environment"):
        os.environ["BUDDY_STATE_DIR"] = str(state)
    else:
        assert "BUDDY_STATE_DIR" not in os.environ

    evidence = {"case": name, "state": str(state), "processes": [], "requests": [],
                "substitutesOnly": substitutes_only}
    handles = []
    restore_modes = {}
    log = None
    spawn = subprocess.Popen

    def collect_spawn(command, **kwargs):
        # Reuse the catalog daemon with its Worker pool disabled; actual service
        # operations and local RPC run unchanged. Keep explicit offline pins
        # after the production cold-start environment allowlist is applied.
        if "-m" in command and command[-1] == "hey_my_buddy.blackboard.service.daemon":
            command = prepare_daemon_spawn(command, kwargs, state)
        process = spawn(command, **kwargs)
        handles.append(process)
        evidence["processes"].append({"pid": process.pid, "command": command, "waitExit": None})
        return process

    request = transport._request

    def record_request(endpoint, operation, params, *args, **kwargs):
        evidence["requests"].append({"operation": operation, "state": str(kwargs.get("state_dir")),
                                     "pid": endpoint.get("pid"), "serviceId": endpoint.get("serviceId")})
        return request(endpoint, operation, params, *args, **kwargs)

    try:
        if name in {"missing-stop", "missing-client"}:
            with patch.object(transport, "_cold_start_preflight") as cold, \
                    patch.object(transport.subprocess, "Popen") as started:
                if name == "missing-stop":
                    for action in ("stop", "restart"):
                        reply = transport.call_service(action)
                        assert reply["alreadyStopped"] and reply["stopped"]
                else:
                    for resource in ("control", "wait"):
                        try:
                            BoardClient(autostart=False).call("ping", resource=resource)
                        except transport.ServiceError as error:
                            assert error.code == "SERVICE_UNAVAILABLE", error.payload()
                        else:
                            raise AssertionError("missing service attached")
                cold.assert_not_called()
                started.assert_not_called()
            assert not state.exists(), "read-only/no-service call created default state"
            return

        state.mkdir(mode=0o700, parents=True)
        restore_modes[state] = stat.S_IMODE(state.stat().st_mode)
        if name == "unsafe-path":
            (state / "ipc").mkdir(mode=0o700)
            link = state / "linked"
            link.symlink_to(state / "ipc", target_is_directory=True)
            endpoint = {"address": "ipc://unused", "token": "fixture"}
            with patch.object(transport.cc, "connect") as connect:
                for selected in (link, state / "unused" / ".."):
                    try:
                        transport.call_board("ping", state_dir=selected, endpoint=endpoint)
                    except BoardError as error:
                        assert error.code == "PRIVATE_PATH_UNSAFE", error.payload()
                    else:
                        raise AssertionError("public resolution hid an unsafe path")
                connect.assert_not_called()
            return

        if name.startswith("boundary-"):
            # Supplemental parameter regression only: native connection is
            # substituted, but the real _request/configure_client missing-root
            # guard runs. This is never evidence of a successful real RPC.
            (state / "ipc").mkdir(mode=0o700)
            endpoint = {"address": "ipc://public-state-parameter-fixture", "token": "fixture"}
            proxy = MagicMock()
            proxy.__enter__.return_value = proxy
            proxy.health.return_value = '{"status":"ok"}'
            with patch.object(transport, "_attach_read_only", return_value=endpoint) as attach, \
                    patch.object(transport.cc, "connect", return_value=proxy) as connect, \
                    patch.object(transport, "_request", side_effect=record_request):
                if name == "boundary-service":
                    reply = transport.call_service("health")
                elif name in {"boundary-board", "boundary-endpoint"}:
                    reply = transport.call_board("health", endpoint=endpoint if name == "boundary-endpoint" else None)
                else:
                    reply = BoardClient(autostart=False).health()
            assert reply == {"status": "ok"}
            connect.assert_called_once()
            if name == "boundary-endpoint":
                attach.assert_not_called()
            else:
                attach.assert_called_once_with(state)
            assert evidence["requests"][0]["state"] == str(state)
            evidence["connectionSubstitute"] = True
            return

        catalog = write_catalog_fixture(state)
        os.environ["BUDDY_MODEL_CATALOG_FILE"] = str(catalog)
        environment = dict(os.environ, BUDDY_STATE_DIR=str(state),
                           BUDDY_RUNTIME_ROOT=str(state / "runtime-root"))
        assert Path(environment["BUDDY_MODEL_CATALOG_FILE"]).is_relative_to(state)
        if name == "cold-import":
            from hey_my_buddy.install.launcher import service_environment
            with patch.dict(os.environ, environment, clear=True):
                source_only = service_environment()
            source_only["PYTHONPATH"] = str(ROOT / "src")
            kwargs = {"env": source_only}
            command = prepare_daemon_spawn([sys.executable, "-m", "hey_my_buddy.blackboard.service.daemon"], kwargs, state)
            command[-1] = "--import-check"
            process = collect_spawn(command, env=source_only, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = process.communicate(timeout=20)
            evidence["importCheck"] = {"exit": process.returncode, "stdout": stdout, "stderr": stderr}
            assert process.returncode == 0, evidence["importCheck"]
            return
        log = (work / "daemon.log").open("ab")

        with patch.object(transport, "_request", side_effect=record_request):
            if name.startswith("cold"):
                with patch.object(transport, "_attach_read_only", wraps=transport._attach_read_only) as attach, \
                        patch.object(transport, "_cold_start_preflight", wraps=transport._cold_start_preflight) as cold, \
                        patch.object(transport.subprocess, "Popen", side_effect=collect_spawn):
                    reply = transport.call_service("health") if name == "cold-service" else transport.call_board("health")
                cold.assert_called_once_with(state)
                assert all(item.args[0] == state for item in attach.call_args_list)
                assert handles and reply["serviceId"], "cold start must reach the actual service"
            else:
                process = collect_spawn(DAEMON_COMMAND, env=environment,
                                        stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                        start_new_session=True)
                deadline = time.monotonic() + 25
                while True:
                    endpoint = transport._read_endpoint(state)
                    if endpoint:
                        ready = request(endpoint, "health", {}, state_dir=state)
                        break
                    if process.poll() is not None:
                        raise AssertionError("real daemon exited before readiness: " + (work / "daemon.log").read_text())
                    if time.monotonic() >= deadline:
                        raise AssertionError("real daemon readiness timed out: " + (work / "daemon.log").read_text())
                    time.sleep(0.05)
                evidence["ready"] = {"pid": endpoint["pid"], "serviceId": ready["serviceId"]}

                if name in {"cli", "launcher"}:
                    command = ([sys.executable, "-m", "hey_my_buddy.cli.main"] if name == "cli" else
                               [sys.executable, str(ROOT / "src/hey_my_buddy/install/launcher.py")])
                    cli = collect_spawn([*command, "health"], env=dict(os.environ), text=True,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                    stdout, stderr = cli.communicate(timeout=35)
                    evidence["cli"] = {"command": command, "pid": cli.pid, "exit": cli.returncode,
                                       "stdout": stdout, "stderr": stderr}
                    assert cli.returncode == 0, evidence["cli"]
                    reply = json.loads(stdout)
                elif name in {"stop", "restart"}:
                    reply = transport.call_service(name, {"drainSeconds": 0})
                    assert reply.get("stopped") if name == "stop" else reply.get("restarting"), reply
                    assert process.wait(timeout=20) == 0
                    evidence["lifecycle"] = reply
                    if name == "restart":
                        resume = json.loads((state / "restart.resume.json").read_text())
                        assert resume["serviceId"] == ready["serviceId"]
                    return
                elif name == "board" or name.endswith("board"):
                    reply = transport.call_board("health", state_dir=state if name.startswith("explicit") else None)
                elif name in {"client-auto", "client-readonly", "readonly"} or name.endswith("client"):
                    if name == "readonly":
                        modes = {path: stat.S_IMODE(path.stat().st_mode) for path in (state, state / "ipc")}
                        restore_modes.update(modes)
                        before = snapshot(state)
                        with MutationRecorder() as recorder:
                            reply = BoardClient(autostart=False).ping()
                        recorder.assert_no_mutation()
                        assert snapshot(state) == before, "read-only client changed state entries"
                        assert {path: stat.S_IMODE(path.stat().st_mode) for path in modes} == modes
                        evidence["readonlySnapshotPreserved"] = True
                    else:
                        with patch.object(transport, "_cold_start_preflight") as cold, \
                                patch.object(transport.subprocess, "Popen") as started:
                            reply = BoardClient(state if name.startswith("explicit") else None,
                                                autostart=name == "client-auto").health()
                            cold.assert_not_called()
                            started.assert_not_called()
                elif client is not None:
                    reply = client.health()
                else:
                    reply = transport.call_service("health", state_dir=state if name.startswith("explicit") else None)
                assert reply["serviceId"] == ready["serviceId"], "public call reached another service"
                assert endpoint["pid"] == process.pid

            evidence["reply"] = reply
            service_state = ready["stateDir"] if name == "readonly" else reply["stateDir"]
            assert service_state == str(state), reply
            evidence["serviceStateDir"] = service_state
            # This is the native public context after real local I/O, not a mock
            # report inferred from the directory supplied to configure_client.
            evidence["nativeRoot"] = str(transport.cc.local_endpoint_context().root)
            assert evidence["nativeRoot"] == str(state / "ipc")
            assert all(item["state"] == str(state) for item in evidence["requests"]), evidence["requests"]
            assert not (work / "native-invoked").exists(), "native CLI sentinel was invoked"
    except BaseException as error:
        evidence["failure"] = {"type": type(error).__name__, "message": str(error)}
        if isinstance(error, BoardError):
            evidence["failure"]["code"] = error.code
        raise
    finally:
        failures = cleanup_owned_case(state, restore_modes, handles, evidence)
        try:
            if log is not None:
                log.close()
        except Exception as error:
            failures.append({"stage": "close_log", "type": type(error).__name__, "message": str(error)})
        evidence["cleanupFailures"] = failures
        try:
            (work / "evidence.json").write_text(json.dumps(evidence, indent=2))
            print(json.dumps(evidence), flush=True)
        except Exception:
            if "failure" not in evidence:
                raise
        if failures and "failure" not in evidence:
            raise AssertionError("Owned fixture cleanup failed: " + json.dumps(failures))


class PublicStateTests(unittest.TestCase):
    def case(self, name: str) -> None:
        work = Path(tempfile.mkdtemp(prefix="public-state-", dir=os.environ.get("BUDDY_CHECKS_TMPDIR"))).resolve()
        environment = _child_environment(work)
        sentinel = work / "native-forbidden"
        sentinel.write_text("#!/bin/sh\nprintf 'unexpected native invocation' >> '" + str(work / "native-invoked") + "'\nexit 99\n")
        sentinel.chmod(0o700)
        environment.update({"BUDDY_" + harness + "_CLI": str(sentinel) for harness in ("CLAUDE", "CODEX", "DSH", "ZCODE")})
        environment.pop("BUDDY_STATE_DIR")
        environment["BUDDY_MAX_CONCURRENT"] = "1"
        environment["BUDDY_CHECKS_TMPDIR"] = os.environ.get("BUDDY_CHECKS_TMPDIR", str(work))
        command = [sys.executable, str(Path(__file__).resolve()), "--case", name, str(work)]
        process = subprocess.Popen(command, env=environment, text=True, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=100)
        (work / "stdout.log").write_text(stdout)
        (work / "stderr.log").write_text(stderr)
        (work / "case-process.json").write_text(json.dumps({"pid": process.pid, "command": command,
                                                           "waitExit": process.returncode}, indent=2))
        self.assertEqual(process.returncode, 0, f"{name}: evidence {work}\n{stdout}\n{stderr}")

    def test_ps02_default_cli(self):
        self.case("cli")

    def test_ps02_source_launcher(self):
        self.case("launcher")

    def test_ps02_default_call_service(self):
        self.case("service")

    def test_ps02_default_call_board(self):
        self.case("board")

    def test_ps02_default_board_client_autostart(self):
        self.case("client-auto")

    def test_ps02_default_board_client_readonly(self):
        self.case("client-readonly")

    def test_ps03_default_stop_owned_service(self):
        self.case("stop")

    def test_ps03_default_restart_owned_service(self):
        self.case("restart")

    def test_ps03_no_service_stop_and_restart_do_not_start(self):
        self.case("missing-stop")

    def test_ps04_explicit_precedes_environment(self):
        for boundary in ("service", "board", "client"):
            with self.subTest(boundary=boundary):
                self.case("explicit-" + boundary)

    def test_ps04_environment_precedes_default(self):
        for boundary in ("service", "board", "client"):
            with self.subTest(boundary=boundary):
                self.case("environment-" + boundary)

    def test_ps04_environment_selected_after_construction(self):
        for mode in ("auto", "readonly"):
            with self.subTest(mode=mode):
                self.case("late-" + mode)

    def test_ps05_missing_readonly_client_never_creates_or_starts(self):
        self.case("missing-client")

    def test_ps05_readonly_attach_preserves_permissions(self):
        self.case("readonly")

    def test_ps06_cold_start_probe_and_request_share_default(self):
        for boundary in ("service", "board"):
            with self.subTest(boundary=boundary):
                self.case("cold-" + boundary)

    def test_ps07_internal_request_still_requires_private_root(self):
        environment = {key: value for key, value in os.environ.items() if key != "BUDDY_STATE_DIR"}
        with patch.dict(os.environ, environment, clear=True), patch.object(transport.cc, "connect") as connect:
            with self.assertRaises(BoardError) as refused:
                transport._request({"token": "fixture", "address": "ipc://unused"}, "ping", {})
            self.assertEqual(refused.exception.code, "PRIVATE_STATE_REQUIRED")
            connect.assert_not_called()

    def test_ps04_injected_call_does_not_resolve_state(self):
        with patch.object(transport, "get_state_dir") as resolve:
            client = BoardClient(call=lambda operation, params: {"operation": operation, **params})
            self.assertIsNone(client.state_dir)
            self.assertEqual(client.call("ping", {"fixture": True}), {"operation": "ping", "fixture": True})
            resolve.assert_not_called()

    def test_ps08_call_service_directory_parameter(self):
        self.case("boundary-service")

    def test_ps08_call_board_directory_parameter(self):
        self.case("boundary-board")

    def test_ps08_readonly_client_directory_parameter(self):
        self.case("boundary-client")

    def test_ps06_supplied_endpoint_keeps_default_directory(self):
        self.case("boundary-endpoint")

    def test_ps07_public_resolution_preserves_path_guards(self):
        self.case("unsafe-path")

    def test_ps06_cold_helper_imports_with_source_only_environment(self):
        self.case("cold-import")


class FixtureRepairTests(unittest.TestCase):
    def simulate_readonly(self, *, target_fails=True, stop_fails=False):
        """Failure injection only; no daemon or native RPC is started here."""
        work = Path(tempfile.mkdtemp(prefix="cleanup-unit-", dir=os.environ["BUDDY_CHECKS_TMPDIR"])).resolve()
        environment = _child_environment(work)
        environment.pop("BUDDY_STATE_DIR")
        state = Path(environment["HOME"]) / ".local/share/hey-my-buddy/state"
        endpoint = {"pid": "substitute-handle", "serviceId": "cleanup-unit"}
        ready = {"serviceId": "cleanup-unit", "stateDir": str(state)}
        process = MagicMock(pid="substitute-handle")
        process.wait.return_value = 0
        primary = BoardError("FIXTURE_TARGET_ERROR", "injected request failure")
        calls = []

        def spawn(*args, **kwargs):
            (state / "ipc").mkdir(mode=0o700)
            return process

        def request(*args, **kwargs):
            calls.append(args[1])
            if len(calls) > 1 and target_fails:
                os.chmod(state, 0o500)
                raise primary
            return ready

        def stop(selected):
            self.assertEqual(selected, state)
            self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
            if stop_fails:
                raise RuntimeError("injected stop failure")

        with patch.dict(os.environ, {**environment, "BUDDY_CHECKS_TMPDIR": os.environ["BUDDY_CHECKS_TMPDIR"]}, clear=True), \
                patch.object(subprocess, "Popen", new=spawn), \
                patch.object(transport, "_read_endpoint", return_value=endpoint), \
                patch.object(transport, "_attach_read_only", return_value=endpoint), \
                patch.object(transport, "_request", side_effect=request), \
                patch.object(transport.cc, "local_endpoint_context", return_value=MagicMock(root=str(state / "ipc"))), \
                patch(__name__ + ".stop_private_service", side_effect=stop), \
                patch(__name__ + ".stop_private_workers") as workers, \
                patch(__name__ + ".shutdown_private_rpc") as shutdown:
            with self.assertRaises(BoardError if target_fails else AssertionError) as caught:
                run_case("readonly", work, substitutes_only=True)
            if target_fails:
                self.assertIs(caught.exception, primary, "cleanup masked the target failure")
            else:
                self.assertIn("Owned fixture cleanup failed", str(caught.exception))
            process.wait.assert_called_once_with(timeout=20)
            workers.assert_called_once_with(state)
            shutdown.assert_called_once_with()
        evidence = json.loads((work / "evidence.json").read_text())
        self.assertEqual(evidence["processes"][0]["waitExit"], 0)
        self.assertEqual(stat.S_IMODE(state.stat().st_mode), 0o700)
        self.assertEqual([failure["stage"] for failure in evidence["cleanupFailures"]], ["stop_service"] if stop_fails else [])
        return evidence

    def test_ps05_request_failure_restores_permissions_before_stop_and_wait(self):
        self.simulate_readonly()

    def test_ps05_stop_failure_keeps_primary_error_and_still_waits(self):
        self.simulate_readonly(stop_fails=True)

    def test_ps05_cleanup_failure_without_target_is_reported(self):
        self.simulate_readonly(target_fails=False, stop_fails=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--import-check":
        assert str(ROOT / "tests/python") in sys.path and str(ROOT / "src") in sys.path
        state = Path(os.environ["BUDDY_STATE_DIR"])
        assert Path(os.environ["BUDDY_MODEL_CATALOG_FILE"]) == state / "model-catalog.json"
        assert Path(os.environ["BUDDY_MODEL_FACTS_FILE"]) == state / "model-facts-fixture.json"
        assert Path(os.environ["BUDDY_CHECKS_TMPDIR"]).is_dir()
        print(json.dumps({"supportFile": sys.modules["support"].__file__, "state": str(state)}))
    elif len(sys.argv) > 1 and sys.argv[1] == "--daemon-fixture":
        from hey_my_buddy.blackboard.service.daemon import Daemon
        # A transport-only fixture has no execution work. Reuse the real catalog
        # daemon without starting or reconciling any Worker process.
        with patch.object(Daemon, "_start_pool"), patch.object(Daemon, "_reconcile_pool"):
            runpy.run_path(str(DAEMON_FIXTURE), run_name="__main__")
    elif len(sys.argv) > 1 and sys.argv[1] == "--case":
        run_case(sys.argv[2], Path(sys.argv[3]))
    else:
        unittest.main()
