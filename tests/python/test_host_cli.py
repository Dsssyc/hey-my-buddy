"""ADR-018 items 12 and 13: parameter documents and generated per-method help.

The governed path runs through the real ``buddy.cli`` module, the real
``buddy.transport`` mapping and the real store/service validators; only the C-Two
socket is substituted with the in-process board, exactly as in ``test_cli.py``.
Every test uses a private state directory, starts no paid model and changes no user
setting. Help must not start a service or a model, so its tests fail loudly if any
transport or subprocess call happens.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import BoardTestCase
from test_workflow import WorkflowTestCase

import buddy.cli as cli
import buddy.cli_help as cli_help
import buddy.transport as transport

#: Methods whose request validators the help reader must cover.
PUBLIC_METHODS = tuple(cli.METHODS)


class PrivateCliTestCase(BoardTestCase):
    """Point the CLI helpers at this test's private state directory."""

    def setUp(self) -> None:
        super().setUp()
        self._set_private_environment()

    def _set_private_environment(self) -> None:
        names = ("BUDDY_STATE_DIR", "BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE")
        previous = {name: os.environ.get(name) for name in names}
        os.environ["BUDDY_STATE_DIR"] = str(self.directory)
        for name in ("BUDDY_AGENT_CREDENTIAL", "BUDDY_AGENT_CREDENTIAL_FILE"):
            os.environ.pop(name, None)

        def restore() -> None:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)

    def run_cli(self, *arguments: str, stdin: bytes | None = None) -> tuple[int, dict, str]:
        """Run the CLI with an optional binary standard input; return code, JSON and text."""
        output = io.StringIO()
        previous = sys.stdin
        if stdin is not None:
            sys.stdin = type("Stdin", (), {"buffer": io.BytesIO(stdin)})()
        try:
            with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                code = cli.main(list(arguments))
        finally:
            sys.stdin = previous
        text = output.getvalue().strip()
        return code, (json.loads(text) if text else {}), text

    def use_board_transport(self, board, capture: list | None = None) -> None:
        """Substitute only the C-Two socket; the whole service path stays real."""
        endpoint = {"address": "ipc://in-process-test", "token": "test-token"}
        self.enterContext(
            mock.patch.object(transport, "ensure_service", lambda state_dir=None, resource="control": endpoint)
        )

        def request(_endpoint, operation, params, resource="control"):
            if capture is not None:
                capture.append(json.loads(json.dumps(params)))
            return board.call(operation, params)

        self.enterContext(mock.patch.object(transport, "_request", request))

    def use_capture_transport(self, capture: list) -> None:
        """Capture the RPC parameters and answer with a canned admitted task."""
        endpoint = {"address": "ipc://in-process-test", "token": "test-token"}
        self.enterContext(
            mock.patch.object(transport, "ensure_service", lambda state_dir=None, resource="control": endpoint)
        )

        def request(_endpoint, operation, params, resource="control"):
            capture.append(json.loads(json.dumps(params)))
            return {"task": {"runId": "run-capture-1", "requestId": "host-cli", "state": "queued"}}

        self.enterContext(mock.patch.object(transport, "_request", request))

    def write_params(self, payload: dict, name: str = "params.json") -> Path:
        path = self.directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path


def command_payload(request_id: str, task: str = "noop") -> dict:
    return {"requestId": request_id, "task": task, "cwd": "", "adapter": "command", "argv": ["/bin/echo", "hi"]}


class ParameterDocumentTests(PrivateCliTestCase):
    """Item 12: the same request from an argument, a file or standard input."""

    def test_a_params_file_is_equivalent_to_the_positional_argument(self):
        board = self.board()
        captured: list = []
        self.use_board_transport(board, captured)
        payload = {**command_payload("host-cli-1"), "cwd": str(self.workdir())}
        path = self.write_params(payload)
        code, from_file, _ = self.run_cli("execution-submit", "--params-file", str(path))
        self.assertEqual(code, 0, from_file)
        code, from_argument, _ = self.run_cli("execution-submit", json.dumps(payload))
        self.assertEqual(code, 0, from_argument)
        # The identical request is recovered, not duplicated: the durable identity and
        # the request the service received are what "same document" must mean.
        self.assertEqual(from_file["runId"], from_argument["runId"])
        self.assertEqual(from_file["state"], from_argument["state"])
        self.assertEqual(captured[0], captured[1])

    def test_standard_input_and_the_dash_file_are_the_same_document(self):
        board = self.board()
        self.use_board_transport(board)
        payload = {**command_payload("host-cli-2"), "cwd": str(self.workdir())}
        document = json.dumps(payload).encode("utf-8")
        code, from_stdin, _ = self.run_cli("execution-submit", "-", stdin=document)
        self.assertEqual(code, 0, from_stdin)
        code, from_dash_file, _ = self.run_cli("execution-submit", "--params-file", "-", stdin=document)
        self.assertEqual(code, 0, from_dash_file)
        self.assertEqual(from_stdin["runId"], from_dash_file["runId"])

    def test_the_output_view_is_identical_whichever_input_carried_it(self):
        board = self.board()
        self.use_board_transport(board)
        payload = {"limit": 1, "output": "full"}
        path = self.write_params(payload)
        code, from_file, _ = self.run_cli("list", "--params-file", str(path))
        self.assertEqual(code, 0, from_file)
        code, from_argument, _ = self.run_cli("list", json.dumps(payload))
        self.assertEqual(code, 0, from_argument)
        self.assertEqual(from_file, from_argument)
        self.assertIn("runs", from_file)

    def test_the_attempt_scoped_credential_is_injected_identically_from_a_file(self):
        captured: list = []
        self.use_capture_transport(captured)
        payload = {**command_payload("host-cli-4"), "cwd": str(self.workdir())}
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL": "scoped-token"}):
            code, _, _ = self.run_cli("execution-submit", "--params-file", str(self.write_params(payload)))
            self.assertEqual(code, 0)
            code, _, _ = self.run_cli("execution-submit", json.dumps(payload))
            self.assertEqual(code, 0)
        self.assertEqual(captured[0], captured[1])
        self.assertEqual(captured[0].get("credential"), "scoped-token")
        self.assertNotIn("submissionToken", captured[0])

    def test_file_content_is_never_reinterpreted_by_a_shell(self):
        board = self.board()
        captured: list = []
        self.use_board_transport(board, captured)
        marker = self.directory / "shell-would-have-created-this"
        task = f'keep $(touch {marker}) `touch {marker}` ; rm -rf {marker} "quotes" \'single\' \\backslash\\'
        payload = {**command_payload("host-cli-5", task), "cwd": str(self.workdir())}
        code, submitted, _ = self.run_cli("execution-submit", "--params-file", str(self.write_params(payload)))
        self.assertEqual(code, 0, submitted)
        self.assertEqual(captured[0]["task"], task)
        self.assertFalse(marker.exists())

    def test_utf8_documents_are_accepted_and_invalid_utf8_is_refused(self):
        board = self.board()
        self.use_board_transport(board)
        payload = {**command_payload("host-cli-6", "用中文写下结果"), "cwd": str(self.workdir())}
        path = self.directory / "utf8.json"
        path.write_bytes(b"\xef\xbb\xbf" + json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        code, submitted, _ = self.run_cli("execution-submit", "--params-file", str(path))
        self.assertEqual(code, 0, submitted)

        broken = self.directory / "broken.json"
        broken.write_bytes(b'{"task": "\xff\xfe"}')
        code, refused, _ = self.run_cli("status", "--params-file", str(broken))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "INVALID_ARGUMENT")
        self.assertIn("UTF-8", refused["error"]["message"])

    def test_an_oversized_document_is_refused_before_any_call(self):
        self.use_board_transport(self.board())
        path = self.directory / "large.json"
        path.write_bytes(b'{"runId": "' + b"x" * 4096 + b'"}')
        with mock.patch.object(cli, "MAX_PARAMS_BYTES", 1024):
            code, refused, _ = self.run_cli("status", "--params-file", str(path))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "INVALID_ARGUMENT")
        self.assertIn("exceeds 1024 bytes", refused["error"]["message"])

    def test_malformed_json_and_a_non_object_document_are_structured_errors(self):
        self.use_board_transport(self.board())
        path = self.directory / "malformed.json"
        path.write_text("{not json", encoding="utf-8")
        code, refused, _ = self.run_cli("status", "--params-file", str(path))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "INVALID_ARGUMENT")
        code, refused, _ = self.run_cli("status", "[1,2,3]")
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "INVALID_ARGUMENT")

    def test_input_sources_are_mutually_exclusive_and_no_call_happens(self):
        def forbidden(*_args, **_kwargs):
            raise AssertionError("the service must not be called for a usage error")

        self.enterContext(mock.patch.object(transport, "ensure_service", forbidden))
        self.enterContext(mock.patch.object(transport, "_request", forbidden))
        path = self.write_params({"runId": "run-1"})
        with self.assertRaises(SystemExit) as caught:
            self.run_cli("status", '{"runId":"run-1"}', "--params-file", str(path))
        self.assertEqual(caught.exception.code, 2)

    def test_the_storage_and_harness_spelling_aliases_still_reach_the_service(self):
        board = self.board()
        captured: list = []
        self.use_board_transport(board, captured)
        code, planned, _ = self.run_cli("storage", "plan")
        self.assertEqual(code, 0, planned)
        self.assertEqual(captured[-1], {})
        code, _, _ = self.run_cli("harness", "set", "codex", "--auto")
        self.assertEqual(code, 0)
        self.assertEqual(captured[-1], {"adapter": "codex", "path": None})

    def test_a_missing_file_names_the_exact_path(self):
        self.use_board_transport(self.board())
        missing = self.directory / "absent.json"
        code, refused, _ = self.run_cli("status", "--params-file", str(missing))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "INVALID_ARGUMENT")
        self.assertIn(str(missing), refused["error"]["message"])


class GovernedParameterDocumentTests(PrivateCliTestCase, WorkflowTestCase):
    """Item 12: a governed mutation keeps its private control capability by any input."""

    def test_a_governed_mutation_from_a_file_uses_the_same_private_control_file(self):
        self.catalog_fixture()
        board = self.board()
        self.use_board_transport(board)
        payload = {
            "requestId": "host-cli-3",
            "hostId": "host-1",
            "task": "do the thing",
            "cwd": str(self.workdir()),
            "adapter": "dsh",
            "provider": "deepseek-official",
            "model": "deepseek-flash",
            "effort": "off",
            "executionWorkspace": {"kind": "existing", "access": "write"},
        }
        code, submitted, _ = self.run_cli("submit", "--params-file", str(self.write_params(payload)))
        self.assertEqual(code, 0, submitted)
        control_file = submitted["controlFile"]
        self.assertTrue(Path(control_file).is_file())
        self.assertNotIn("controlToken", json.dumps(submitted))

        cancel = {"runId": submitted["runId"], "commandId": "host-cli-3-cancel", "controlFile": control_file}
        code, cancelled, _ = self.run_cli("cancel", "-", stdin=json.dumps(cancel).encode("utf-8"))
        self.assertEqual(code, 0, cancelled)
        self.assertTrue(cancelled["cancelled"])


class HelpTests(unittest.TestCase):
    """Item 13: help is generated from the validators and starts nothing."""

    def help_text(self, *arguments: str) -> str:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(
            transport, "call_service", side_effect=AssertionError("help must not call the service")
        ), mock.patch.object(transport, "ensure_service", side_effect=AssertionError("help must not start the service")):
            code = cli.main(["help", *arguments])
        self.assertEqual(code, 0)
        return output.getvalue()

    def test_index_lists_every_public_method_with_one_sentence(self):
        text = self.help_text()
        for method in PUBLIC_METHODS:
            with self.subTest(method=method):
                self.assertRegex(text, rf"(?m)^  {method} +[A-Z]")
        self.assertRegex(text, r"(?m)^  help +[A-Z]")
        self.assertNotIn(cli_help.MISSING_SUMMARY, text, "every method needs its one-sentence summary")
        self.assertIn("--params-file", text)
        self.assertIn("buddy help METHOD", text)

    def test_help_starts_no_service_and_no_model(self):
        # ``help_text`` already fails on any transport call; this also refuses a spawn.
        with mock.patch("subprocess.Popen", side_effect=AssertionError("help must not spawn a process")):
            self.assertIn("buddy submit", self.help_text("submit"))
            self.assertIn("methods", self.help_text())

    def test_every_method_has_parameter_help_and_never_a_silent_empty_list(self):
        for method in (*PUBLIC_METHODS, "help"):
            with self.subTest(method=method):
                facts = cli_help.method_help(method)
                self.assertIsNotNone(facts, method)
                rendered = cli_help.render_method(facts)
                self.assertIn(f"buddy {method}", rendered)
                self.assertTrue(
                    facts.parameters or facts.inline or "takes no parameters" in " ".join(facts.notes),
                    f"{method} must list parameters or say it takes none",
                )

    def test_known_defaults_and_bounds_come_from_the_validators(self):
        text = self.help_text("submit")
        self.assertIn("timeoutSeconds", text)
        self.assertIn("default 1800", text)
        self.assertIn("10–86400", text)
        self.assertIn("executionWorkspace", text)
        self.assertIn("requestId", text)
        self.assertIn("cli-local", text)
        # A governed mutation names the private Host control file the CLI accepts locally.
        self.assertIn("controlFile", self.help_text("cancel"))
        wait = self.help_text("wait")
        self.assertIn("timeoutMs", wait)
        self.assertIn("default 30000", wait)
        self.assertIn("0–30000", wait)
        listing = self.help_text("list")
        self.assertIn("limit", listing)
        self.assertIn("1–100", listing)
        acknowledge = self.help_text("acknowledge")
        self.assertIn("note", acknowledge)
        self.assertIn("10000", acknowledge)

    def test_conditional_parameters_are_marked(self):
        self.assertIn("conditional", self.help_text("continue"))
        self.assertIn("conditional", self.help_text("submit"))

    def test_a_mistyped_method_prints_the_closest_candidates(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            cli.main(["submt", "{}"])
        self.assertEqual(caught.exception.code, 2)
        payload = json.loads(output.getvalue())["error"]
        self.assertEqual(payload["code"], "UNKNOWN_METHOD")
        self.assertIn("submit", payload["didYouMean"])

        output = io.StringIO()
        with contextlib.redirect_stdout(output), self.assertRaises(SystemExit) as caught:
            cli.main(["help", "submt"])
        self.assertEqual(caught.exception.code, 2)
        self.assertIn("submit", json.loads(output.getvalue())["error"]["didYouMean"])

    def test_help_never_claims_a_version_or_an_installation_state(self):
        text = self.help_text("submit")
        self.assertNotIn("contractVersion", text)
        self.assertNotIn("schema 1", text)
        for method in PUBLIC_METHODS:
            with self.subTest(method=method):
                self.assertNotIn("尚未安装", cli_help.render_method(cli_help.method_help(method)))


class HelpBoundaryTests(PrivateCliTestCase):
    """Help facts and the real rejection messages describe the same bounds."""

    def _rejection(self, operation: str, params: dict) -> str:
        board = self.board()
        try:
            board.call(operation, params)
        except Exception as error:  # noqa: BLE001 - the rejection message is the assertion target
            return getattr(error, "message", str(error))
        raise AssertionError(f"{operation} accepted {params}")

    def test_declared_bounds_match_the_actual_service_rejection(self):
        cases = (
            ("task_list", {"limit": 0}, "limit must be an integer between 1 and 100", "list", "1–100"),
            ("task_wait", {"runId": "run-1", "timeoutMs": 30001}, "timeoutMs must be an integer between 0 and 30000", "wait", "0–30000"),
            ("inquiry_observe", {"runId": "run-1", "timeoutMs": 50}, "timeoutMs must be an integer between 100 and 5000", "inquire", "100–5000"),
            ("service_control", {"action": "stop", "drainSeconds": 121}, "drainSeconds must be an integer between 0 and 120", "stop", "0–120"),
        )
        for operation, params, message, method, declared in cases:
            with self.subTest(operation=operation):
                self.assertEqual(self._rejection(operation, params), message)
                self.assertIn(declared, cli_help.render_method(cli_help.method_help(method)))

    def test_every_declared_parameter_name_is_accepted_or_named_by_the_validator(self):
        """A listed field is never invented: the validator names it when only it is invalid."""
        for method, operation, field, value in (
            ("list", "task_list", "limit", "not-an-integer"),
            ("wait", "task_wait", "timeoutMs", "not-an-integer"),
            ("inquire", "inquiry_observe", "timeoutMs", "not-an-integer"),
            ("model-profiles", "model_profiles", "limit", "not-an-integer"),
            ("stop", "service_control", "drainSeconds", "not-an-integer"),
        ):
            with self.subTest(method=method):
                names = {parameter.name for parameter in cli_help.method_help(method).parameters}
                self.assertIn(field, names)
                payload = {"action": "stop", field: value} if operation == "service_control" else {field: value}
                self.assertIn(field, self._rejection(operation, payload))


class HelpExtractionRegressionTests(unittest.TestCase):
    """Item 13, second pass: the rejected extraction lost facts; these keep them.

    Each assertion below names the exact fact the first attempt omitted (an input
    bound, a configuration object's required strings, the task byte cap, a field-name
    loop, or the two-part cwd rule). A silently skipped field would fail here rather
    than reappear as an "accepted; bound is validated inline" placeholder.
    """

    def parameter(self, method: str, path: str):
        parameters = cli_help.method_help(method).parameters
        found = None
        for part in path.split("."):
            found = next((item for item in parameters if item.name == part), None)
            self.assertIsNotNone(found, f"{method} does not list {part!r} (path {path})")
            parameters = found.children
        return found

    def facts(self, method: str, path: str) -> str:
        parameter = self.parameter(method, path)
        return " ".join(
            str(part)
            for part in (parameter.kind, parameter.default, parameter.bounds, parameter.expression, parameter.required)
            if part
        )

    def test_continue_reports_the_whole_input_document_rule(self):
        parameter = self.parameter("continue", "input")
        self.assertTrue(parameter.required, "input is required")
        self.assertIn("string", parameter.kind)
        self.assertIn("object", parameter.kind)
        self.assertIn("nonempty", parameter.bounds or "")
        self.assertIn("65536 UTF-8 bytes", parameter.bounds or "")

    def test_continue_reports_every_required_configuration_string(self):
        configuration = self.parameter("continue", "configuration")
        self.assertEqual(configuration.kind, "object")
        for field in ("adapter", "provider", "model", "effort"):
            with self.subTest(field=field):
                parameter = self.parameter("continue", f"configuration.{field}")
                self.assertEqual(parameter.kind, "string")
                self.assertTrue(parameter.required)
                self.assertIn("256 characters", parameter.bounds or "")

    def test_submit_reports_the_task_cap_and_the_named_loop_fields(self):
        task = self.parameter("submit", "task")
        self.assertEqual(task.kind, "string")
        self.assertTrue(task.required)
        self.assertIn("1048576 UTF-8 bytes", task.bounds or "")
        for field in ("model", "provider", "effort"):
            with self.subTest(field=field):
                parameter = self.parameter("submit", field)
                self.assertEqual(parameter.kind, "string")
                self.assertIn("256 characters", parameter.bounds or "")
                self.assertIn("256 characters", self.facts("submit", f"spec.{field}"))

    def test_submit_reports_both_halves_of_the_cwd_rule(self):
        cwd = self.parameter("submit", "cwd")
        self.assertTrue(cwd.required)
        self.assertIn('starts with "/"', cwd.bounds or "")
        self.assertIn("existing directory", cwd.bounds or "")

    def test_an_undecodable_condition_is_a_readable_expression_with_its_source(self):
        plan = self.parameter("storage-apply", "planId")
        self.assertTrue(plan.expression, "a complex condition must not be dropped")
        self.assertTrue(plan.source, "the expression names the validator it came from")

    def test_no_method_falls_back_to_the_retired_placeholder(self):
        for method in (*PUBLIC_METHODS, "help"):
            with self.subTest(method=method):
                rendered = cli_help.render_method(cli_help.method_help(method))
                self.assertNotIn("bound is validated inline", rendered)
                self.assertNotIn("read directly", rendered)
                self.assertNotRegex(rendered, r"\n\s*\w+\s+value\s*$")

    def test_helper_spec_fields_do_not_leak_to_the_decide_top_level(self):
        names = {parameter.name for parameter in cli_help.method_help("decide").parameters}
        self.assertNotIn("adapter", names)
        self.assertNotIn("task", names)
        self.assertEqual(self.parameter("decide", "helpers.spec.adapter").kind, "string")

    def test_an_unknown_method_is_always_a_structured_error_with_candidates(self):
        for name, nearest in (("submt", "submit"), ("zzzzzz", None)):
            with self.subTest(name=name):
                output = io.StringIO()
                with contextlib.redirect_stdout(output), mock.patch.object(
                    transport, "call_service", side_effect=AssertionError("unknown methods are never dispatched")
                ), mock.patch.object(
                    transport, "ensure_service", side_effect=AssertionError("unknown methods never start a service")
                ), self.assertRaises(SystemExit) as caught:
                    cli.main([name, "{}"])
                self.assertEqual(caught.exception.code, 2)
                payload = json.loads(output.getvalue())["error"]
                self.assertEqual(payload["code"], "UNKNOWN_METHOD")
                if nearest is None:
                    self.assertEqual(payload["didYouMean"], [])
                else:
                    self.assertIn(nearest, payload["didYouMean"])


class HelpSourceFreshnessTests(unittest.TestCase):
    """The answer comes from the validators on disk, not from a frozen table."""

    def test_a_changed_validator_constant_changes_the_help(self):
        with tempfile.TemporaryDirectory(prefix="buddy-help-source-") as directory:
            copy = Path(directory) / "buddy"
            for path in sorted(Path(cli_help.PACKAGE_ROOT).rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                target = copy / path.relative_to(cli_help.PACKAGE_ROOT)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
            schemas = copy / "schemas.py"
            text = schemas.read_text(encoding="utf-8")
            text = text.replace("MAX_WORKFLOW_INPUT_BYTES = 64 * 1024", "MAX_WORKFLOW_INPUT_BYTES = 48 * 1024")
            text = text.replace("MAX_TASK_BYTES = 1024 * 1024", "MAX_TASK_BYTES = 3 * 512 * 1024")
            schemas.write_text(text, encoding="utf-8")

            previous = cli_help.PACKAGE_ROOT
            cli_help.PACKAGE_ROOT = copy
            cli_help.reset_index()
            try:
                continuation = cli_help.render_method(cli_help.method_help("continue"))
                submission = cli_help.render_method(cli_help.method_help("submit"))
            finally:
                cli_help.PACKAGE_ROOT = previous
                cli_help.reset_index()

            self.assertIn("49152 UTF-8 bytes", continuation)
            self.assertNotIn("65536 UTF-8 bytes", continuation)
            self.assertIn("1572864 UTF-8 bytes", submission)
            self.assertNotIn("1048576 UTF-8 bytes", submission)


if __name__ == "__main__":
    unittest.main()
