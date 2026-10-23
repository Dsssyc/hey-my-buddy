"""SC-01..03/07: select at public boundaries, trust explicit internal Path values."""
from __future__ import annotations

import ast
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import client, rpc_config, transport
from hey_my_buddy.protocol.client import BoardClient


class StateBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="state-boundary-")).resolve()
        self.state = self.root / "state"
        self.state.mkdir(mode=0o700)
        (self.state / "ipc").mkdir(mode=0o700)
        self.endpoint = {"token": "fixture", "address": "ipc://fixture"}

    def test_internal_parameters_are_required_path_without_another_resolver(self):
        import inspect
        from hey_my_buddy.buddy.roles.run_execution import _native_operation, discover_models, delete_sessions
        from hey_my_buddy.buddy.harnesses.discovery import discover as discover_harness, _probe_dsh_version
        from hey_my_buddy.buddy.harnesses.claude.adapter import _probe_native_metadata, _cached_native_metadata, _load_native_metadata
        from hey_my_buddy.blackboard.service.harness_health import _discover
        from hey_my_buddy.buddy.runtime.execution import Execution
        from hey_my_buddy.cli.blocking import await_run
        from hey_my_buddy.cli.console_cli import run
        from hey_my_buddy.blackboard.catalog.catalog import discover
        for function, argument in ((transport._request, "state_dir"), (transport._healthy, "directory"),
                (transport._call_board_read_only, "directory"),
                (transport._attach_read_only, "directory"), (transport._cold_start_preflight, "directory"),
                (rpc_config.configure_local_endpoint, "state_dir"), (rpc_config.configure_client, "state_dir"),
                (rpc_config.configure_server, "state_dir"), (rpc_config._apply, "state_dir"),
                (Execution, "state_dir"),
                (_native_operation, "state_dir"), (discover_models, "state_dir"), (delete_sessions, "state_dir"),
                (discover_harness, "state_dir"), (_probe_dsh_version, "state_dir"), (_discover, "state_dir"),
                (_probe_native_metadata, "state_dir"), (_cached_native_metadata, "state_dir"), (_load_native_metadata, "state_dir"),
                (await_run, "state_dir"), (run, "state_dir"), (discover, "directory")):
            with self.subTest(function=function):
                parameter = inspect.signature(function, eval_str=True).parameters[argument]
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertIs(parameter.annotation, Path)
        self.assertFalse(hasattr(rpc_config, "resolve_state_dir"))
        source = Path(transport.__file__).parents[1]
        for name in ("protocol/rpc_config.py", "buddy/runtime/execution.py",
                     "buddy/harnesses/c_two_live.py"):
            tree = ast.parse((source / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, ("resolve", "absolute", "resolve_state_dir"), name)
            self.assertNotIn("PRIVATE_STATE_REQUIRED", (source / name).read_text(encoding="utf-8"))

    def test_native_temporary_directories_use_explicit_root_without_ambient_state(self):
        from hey_my_buddy import home
        from hey_my_buddy.buddy.roles import run_execution
        from hey_my_buddy.buddy.harnesses import discovery
        from hey_my_buddy.buddy.harnesses.dsh.acp.launch import LaunchRejected

        def controller(*, prepare, log_paths):
            command, cwd, environment = prepare()
            control = Path(command[-1])
            self.assertEqual(control.parent.parent, self.state / "tmp")
            self.assertEqual(environment["HMB_STATE_DIR"], str(self.state))
            return SimpleNamespace(wait=lambda timeout: 0)

        def rejected(command, *, private_root, **kwargs):
            self.assertEqual(private_root.parent, self.state / "tmp")
            raise LaunchRejected("fixture launch refusal")

        receipt = {"source": "fixture", "providers": []}
        collection = SimpleNamespace(payload={"status": "ok", "catalog": receipt}, exit_code=0, stop_confirmed=True)
        for ambient in (None, str(self.root / "foreign")):
            with self.subTest(ambient=ambient), patch.dict(os.environ), \
                    patch.object(home, "default_state_dir", side_effect=AssertionError("internal default state")), \
                    patch.object(run_execution, "launch_controller", side_effect=controller), \
                    patch.object(run_execution, "collect_controller", return_value=collection), \
                    patch("hey_my_buddy.buddy.harnesses.dsh.acp.launch.launch", side_effect=rejected):
                if ambient is None:
                    os.environ.pop("HMB_STATE_DIR", None)
                else:
                    os.environ["HMB_STATE_DIR"] = ambient
                self.assertIs(run_execution.discover_models("codex", state_dir=self.state), receipt)
                code, output = discovery._probe_dsh_version(
                    [sys.executable], {}, time.monotonic() + 1, state_dir=self.state)
                self.assertEqual((code, output), ("launch-failed", b""))
                self.assertEqual(list((self.state / "tmp").iterdir()), [])
        self.assertFalse((self.root / "foreign").exists())

    def test_only_public_boundaries_can_select_an_implicit_state_root(self):
        source = Path(transport.__file__).parents[1]
        boundaries = {"cli/main.py", "install/skill_install.py", "install/upgrade.py"}
        for path in source.rglob("*.py"):
            name = path.relative_to(source).as_posix()
            if name in boundaries:
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "get_state_dir":
                    self.assertTrue(node.args or node.keywords, (name, node.lineno))

    def test_internal_cli_defaults_share_selected_path_without_public_resolution(self):
        from hey_my_buddy.cli import blocking, console_cli
        lifecycle = {"running": True, "consoleId": "0123456789abcdef01234567",
                     "url": "http://127.0.0.1:8123/"}
        run = {"runId": "fixture", "status": "failed", "revision": 1}

        def request(endpoint, operation, params, resource="control", *, state_dir):
            self.assertIs(state_dir, self.state)
            return (lifecycle if params.get("action") == "open" else {"running": False}) if operation == "console" else {"task": run}

        with patch.object(transport, "get_state_dir", side_effect=AssertionError("internal CLI reselected state")), \
                patch.object(transport, "_ensure_service", return_value=self.endpoint) as ensure, \
                patch.object(transport, "_attach_read_only", return_value=self.endpoint) as attach, \
                patch.object(transport, "_request", side_effect=request):
            console = console_cli.run({"browser": False, "wait": True}, state_dir=self.state)
            self.assertEqual(console["wait"], {"status": "closed"})
            blocking.await_run({"runId": "fixture"}, state_dir=self.state)
        for call in ensure.call_args_list + attach.call_args_list:
            self.assertIs(call.args[0], self.state)

    def test_public_calls_select_exactly_once_and_share_the_selected_object(self):
        callers = (
            (transport, lambda: transport.call_board("ping")),
            (transport, lambda: transport.call_service("health")),
            (transport, lambda: transport.ensure_service()),
            (transport, lambda: transport.request_stop()),
            (client, lambda: BoardClient().ping()),
            (client, lambda: BoardClient(autostart=False).ping()),
        )
        for module, call in callers:
            with self.subTest(call=call), patch.object(module, "get_state_dir", return_value=self.state) as resolve, \
                    patch.object(transport, "_attach_read_only", return_value=self.endpoint) as attach, \
                    patch.object(transport, "_request", return_value={"status": "ok"}) as request:
                call()
                resolve.assert_called_once()
                for observed in attach.call_args_list:
                    self.assertIs(observed.args[0], self.state)
                for observed in request.call_args_list:
                    self.assertIs(observed.kwargs["state_dir"], self.state)

    def test_configuration_uses_supplied_root_without_environment_or_client_fallback(self):
        with patch.dict(os.environ, {"HMB_STATE_DIR": str(self.root / "foreign")}), \
                patch.object(rpc_config.cc, "set_local_endpoint") as select:
            self.assertEqual(rpc_config.configure_local_endpoint(self.state, create=False), self.state / "ipc")
            select.assert_called_once_with(root=str(self.state / "ipc"))

    def test_unavailable_ping_is_unhealthy_but_other_errors_escape_unchanged(self):
        with patch.object(transport, "_read_endpoint", return_value=self.endpoint):
            for code in ("SERVICE_UNAVAILABLE", "INVALID_RESPONSE", "PRIVATE_PATH_UNSAFE",
                         "RPC_CONFIG_TOO_LATE", "INVALID_ARGUMENT"):
                error = transport.ServiceError(code, "specific failure", path="<PRIVATE_STATE>/ipc")
                with self.subTest(code=code), patch.object(transport, "_request", side_effect=error):
                    if code == "SERVICE_UNAVAILABLE":
                        self.assertIsNone(transport._healthy(self.state))
                    else:
                        with self.assertRaises(BoardError) as raised:
                            transport._healthy(self.state)
                        self.assertIs(raised.exception, error)
            error = RuntimeError("configuration defect")
            with patch.object(transport, "_request", side_effect=error):
                with self.assertRaises(RuntimeError) as raised:
                    transport._healthy(self.state)
                self.assertIs(raised.exception, error)

    def test_missing_ipc_is_a_direct_read_only_condition(self):
        with patch.object(transport, "_read_endpoint", return_value=self.endpoint), patch.object(transport, "_request") as request:
            self.assertIsNone(transport._attach_read_only(self.root / "absent"))
            request.assert_not_called()

    def test_windows_attach_does_not_require_a_posix_ipc_directory(self):
        from types import SimpleNamespace
        with patch.object(transport, "os", SimpleNamespace(name="nt")),                 patch.object(transport, "_read_endpoint", return_value=self.endpoint),                 patch.object(transport, "_request", return_value={"status": "ok"}):
            self.assertIs(transport._healthy(self.root / "without-ipc"), self.endpoint)

    def test_dangling_ipc_link_still_reaches_the_structural_guard(self):
        state = self.root / "dangling-state"
        state.mkdir(mode=0o700)
        (state / "ipc").symlink_to(self.root / "missing-target", target_is_directory=True)
        with patch.object(transport, "_read_endpoint", return_value=self.endpoint),                 patch.object(transport.cc, "connect") as connect:
            with self.assertRaises(BoardError) as raised:
                transport._healthy(state)
            self.assertEqual(raised.exception.code, "PRIVATE_PATH_UNSAFE")
            self.assertEqual(raised.exception.details["path"], str(state / "ipc"))
            connect.assert_not_called()

    def test_process_entries_fail_without_parent_state_and_do_not_select_default(self):
        environment = {k: v for k, v in os.environ.items() if not k.startswith(("HMB_", "ANTHROPIC_", "C2_"))
                       and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        home = self.root / "home"
        home.mkdir(mode=0o700)
        environment.update(HOME=str(home), USERPROFILE=str(home))
        for module, arguments in (("blackboard.service.daemon", []),
                                  ("buddy.runtime.execution", ["--startup", str(self.root / "absent-startup")]),
                                  ("buddy.roles.run_controller", ["--control", str(self.root / "absent-control")])):
            with self.subTest(module=module):
                child = subprocess.Popen([sys.executable, "-m", "hey_my_buddy." + module, *arguments],
                                         env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
                stdout, stderr = child.communicate(timeout=15)
                self.assertEqual(child.returncode, 2, (stdout, stderr))
                self.assertIn("HMB_STATE_DIR is required", stderr)
                self.assertEqual(list(home.iterdir()), [], "internal process selected default state")
