"""Explicit supervisors must survive replacement of their staged CLI package."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from support import stop_private_workers, wait_for
from test_packaging import ROOT, load_stage_plugin
from buddy import cli, runtime
from buddy.client import BoardClient
from buddy.transport import ServiceError


class WorkerLaunchEnvironmentTests(unittest.TestCase):
    def test_stable_target_replaces_inherited_cli_python_paths(self):
        with tempfile.TemporaryDirectory(prefix="buddy-worker-env-") as directory:
            target = {"python": "/stable/runtime/venv/bin/python", "pythonPath": None,
                      "identity": "runtime:fixed", "stable": True,
                      "runtime": {"runtimeDir": "/stable/runtime", "environment": "/stable/runtime/venv"}}
            inherited = {"PATH": "/plugin/venv/bin:/usr/bin", "PYTHONPATH": "/plugin/python",
                         "BUDDY_PYTHON": "/plugin/venv/bin/python", "VIRTUAL_ENV": "/plugin/venv",
                         "UV_PROJECT_ENVIRONMENT": "/plugin/venv", "BUDDY_RUNTIME": "/old/runtime"}
            with patch.dict(os.environ, inherited, clear=True), patch.object(runtime, "launch_target", return_value=target) as select, patch.object(cli.subprocess, "Popen") as spawn:
                spawn.return_value.pid = 123
                result = cli._worker_command("worker-start", {"workerId": "extra", "stateDir": directory})
            select.assert_called_once_with(log_path=Path(directory).resolve() / "runtime-install.log")
            args, options = spawn.call_args
            self.assertEqual(args[0][0], target["python"])
            self.assertNotIn("PYTHONPATH", options["env"])
            self.assertEqual(options["env"]["BUDDY_RUNTIME"], "/stable/runtime")
            self.assertEqual(options["env"]["BUDDY_PYTHON"], target["python"])
            for key in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
                self.assertNotIn(key, options["env"])
            self.assertEqual(options["env"]["PATH"].split(os.pathsep)[0], "/stable/runtime/venv/bin")
            self.assertEqual(options["env"]["BUDDY_RUNTIME_IDENTITY"], target["identity"])
            self.assertEqual(result["supervisorPid"], 123)

    def test_dev_source_with_private_empty_runtime_keeps_source_semantics(self):
        with tempfile.TemporaryDirectory(prefix="buddy-worker-source-") as directory:
            runtime_root = Path(directory) / "runtime"
            inherited = {"BUDDY_DEV_SOURCE": "1", "BUDDY_RUNTIME_ROOT": str(runtime_root),
                         "PYTHONPATH": "/dev/extra", "BUDDY_PYTHON": "/dev/python", "PATH": "/dev/bin:/usr/bin",
                         "VIRTUAL_ENV": "/dev/venv", "UV_PROJECT_ENVIRONMENT": "/dev/venv"}
            with patch.dict(os.environ, inherited, clear=True), patch.object(cli.subprocess, "Popen") as spawn:
                spawn.return_value.pid = 123
                cli._worker_command("worker-start", {"workerId": "dev-extra", "stateDir": directory})
            args, options = spawn.call_args
            self.assertEqual(args[0][0], sys.executable)
            self.assertEqual(options["env"]["PYTHONPATH"], str(runtime.project_root() / "src") + os.pathsep + "/dev/extra")
            self.assertTrue(options["env"]["BUDDY_RUNTIME_IDENTITY"].startswith("source:"))
            self.assertNotIn("BUDDY_RUNTIME", options["env"])
            for key in ("BUDDY_PYTHON", "PATH", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
                self.assertEqual(options["env"][key], inherited[key])
            self.assertFalse(runtime_root.exists(), "development start must not materialize a runtime")

    def test_worker_stop_does_not_select_runtime_or_spawn(self):
        with tempfile.TemporaryDirectory(prefix="buddy-worker-stop-") as directory:
            with patch.object(runtime, "launch_target") as select, patch.object(cli.subprocess, "Popen") as spawn:
                result = cli._worker_command("worker-stop", {"workerId": "extra", "stateDir": directory})
            select.assert_not_called()
            spawn.assert_not_called()
            self.assertTrue(result["stopRequested"])


class StagedWorkerRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-staged-worker-", dir="/tmp")
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"
        self.runtime_root = self.root / "runtime"
        self.work = self.root / "work"
        self.work.mkdir()
        self.local_work = self.work / "local"
        self.extra_work = self.work / "extra"
        self.local_work.mkdir()
        self.extra_work.mkdir()
        self.client = BoardClient(self.state, autostart=False)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY") and key not in {"VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT", "PYTHONPATH", "PLUGIN_DATA"}}
        self.environment.update(BUDDY_STATE_DIR=str(self.state), BUDDY_RUNTIME_ROOT=str(self.runtime_root),
                                BUDDY_MAX_CONCURRENT="2", PLUGIN_DATA=str(self.root / "plugin-data"))
        # Staging reads the current checkout and never rewrites it, so the real root
        # is staged directly; the one public launcher is bin/buddy.
        self.stage = self.root / "stage" / "hey-my-buddy"
        with contextlib.redirect_stdout(io.StringIO()):
            load_stage_plugin().stage(ROOT, self.stage)
        self.launcher = self.stage / "bin" / "buddy"

    def tearDown(self):
        (self.work / "release-local").touch()
        (self.work / "release-extra").touch()
        try:
            self.client.call("service_control", {"action": "stop", "drainSeconds": 5})
        except ServiceError as error:
            if error.code != "SERVICE_UNAVAILABLE":
                raise
        stop_private_workers(self.state)
        self.temp.cleanup()

    def staged_cli(self, command, params=None, *, extra_env=None):
        completed = subprocess.run(["/bin/sh", str(self.launcher), command, json.dumps(params or {})],
                                   env={**self.environment, **(extra_env or {})}, cwd=self.work,
                                   capture_output=True, text=True, timeout=300)
        self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
        return json.loads(completed.stdout)

    def wait_status(self, run_id, state):
        self.assertTrue(wait_for(lambda: self.client.get(runId=run_id)["status"] == state, 40),
                        self.client.get(runId=run_id))
        return self.client.get(runId=run_id)

    def test_staged_launcher_adds_runtime_worker_that_survives_stage_replacement(self):
        health = self.staged_cli("health")
        self.assertTrue(health["runtimeStable"], health)
        info = self.staged_cli("runtime")["identity"]
        runtime_dir = Path(info["runtimeDir"])
        self.assertEqual(set(info["actual"]["resources"]), set(runtime.declared_resources()))
        local_status = self.state / "workers/local/supervisor.json"
        self.assertTrue(wait_for(local_status.exists, 20))
        original_local_pid = json.loads(local_status.read_text())["supervisorPid"]

        waiting = "import pathlib,time; gate=pathlib.Path(" + repr(str(self.work / "release-local")) + "); end=time.monotonic()+110\nwhile not gate.exists() and time.monotonic()<end: time.sleep(0.05)"
        held = self.staged_cli("execution-submit", {"requestId": "hold-local", "task": "occupy the existing local worker", "cwd": str(self.local_work),
                                         "adapter": "command", "argv": [sys.executable, "-c", waiting], "timeoutSeconds": 120})
        self.assertEqual(self.wait_status(held["runId"], "running")["workerId"], "local")

        started = self.staged_cli("worker-start", {"workerId": "acceptance-extra"}, extra_env={
            "PYTHONPATH": str(self.stage / "src"),
            "BUDDY_PYTHON": str(self.stage / "replaceable-python"),
        })
        self.assertEqual(json.loads(local_status.read_text())["supervisorPid"], original_local_pid)
        probe = "import pathlib,time,json,os; gate=pathlib.Path(" + repr(str(self.work / "release-extra")) + "); end=time.monotonic()+35\nwhile not gate.exists() and time.monotonic()<end: time.sleep(0.05)\nfrom buddy import runtime\nr=runtime.resolve_runtime(); print(json.dumps({k:r[k] for k in ('identity','stable','actual','leaks','resourcesMissing')} | {'bridgePython':os.environ.get('BUDDY_PYTHON'),'virtualEnv':os.environ.get('VIRTUAL_ENV'),'uvEnvironment':os.environ.get('UV_PROJECT_ENVIRONMENT')}))"
        submitted = self.staged_cli("execution-submit", {"requestId": "probe-extra", "task": "report only local runtime paths", "cwd": str(self.extra_work),
                                              "adapter": "command", "argv": ["python", "-c", probe], "timeoutSeconds": 60})
        self.assertEqual(self.wait_status(submitted["runId"], "running")["workerId"], "acceptance-extra")
        extra_status = json.loads((self.state / "workers/acceptance-extra/supervisor.json").read_text())
        self.assertEqual(extra_status["supervisorPid"], started["supervisorPid"])
        # Both workers are already running. Replacing the disposable stage must
        # not affect the extra worker's interpreter, later imports or declared resources.
        self.stage.rename(self.stage.with_name("replaced-stage"))
        (self.work / "release-extra").touch()
        task = self.wait_status(submitted["runId"], "completed")
        self.assertEqual(task["selectedAttempt"]["runtimeIdentity"], health["runtimeIdentity"])
        observed = json.loads(Path(task["logPaths"]["stdout"]).read_text())
        self.assertTrue(observed["stable"], observed)
        self.assertEqual(observed["identity"], health["runtimeIdentity"])
        self.assertEqual(observed["leaks"], [])
        self.assertEqual(observed["resourcesMissing"], [])
        actual = observed["actual"]
        self.assertTrue(actual["executable"].startswith(str(runtime_dir / "venv")), actual)
        for key in ("prefix", "package"):
            self.assertTrue(Path(actual[key]).resolve().is_relative_to(runtime_dir.resolve()), (key, actual[key]))
        self.assertEqual(set(actual["resources"]), set(runtime.declared_resources()))
        for name, value in actual["resources"].items():
            self.assertTrue(Path(value).resolve().is_relative_to(runtime_dir.resolve()), (name, value))
        self.assertEqual(observed["bridgePython"], info["python"])
        self.assertIsNone(observed["virtualEnv"])
        self.assertIsNone(observed["uvEnvironment"])
        self.assertEqual(json.loads(local_status.read_text())["supervisorPid"], original_local_pid)
        (self.work / "release-local").touch()
        self.wait_status(held["runId"], "completed")


if __name__ == "__main__":
    unittest.main()
