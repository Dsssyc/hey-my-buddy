"""CLI surface tests: the current single-contract names, help and Host control privacy.

The micro-task command path is driven through the real ``hey_my_buddy.cli.main`` module, the real
``transport.call_service`` mapping and the real store/service validators; only the
C-Two socket is substituted with the in-process board and only the Git-backed
workspace module is replaced by the deterministic double.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import unittest
from functools import partial
from pathlib import Path
from unittest import mock

from support import BoardTestCase, InProcessBoard
from private_acl import assert_private_path
from blackboard.tasks.test_workflow import WorkflowTestCase

import hey_my_buddy.cli.main as cli
import hey_my_buddy.cli.cli_help as cli_help
import hey_my_buddy.protocol.transport as transport
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.service.service import BoardService, CONTROL_OPERATIONS
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.contracts import BuddyControl
from hey_my_buddy.paths import native_path

#: Every CLI method that was removed by ADR-007, plus the ADR-008 removal of the
#: internal maintenance call and the ADR-021 retirement of the paid review
#: verifier, and the retired Host execution submission. None may remain as an alias.
RETIRED_METHODS = (
    "run",
    "start",
    "retry",
    "workflow-submit",
    "workflow-get",
    "workflow-decide",
    "workflow-continue",
    "workflow-takeover",
    "workflow-cancel",
    "workflow-acknowledge",
    "workflow-suggest",
    "acknowledge",
    "integration-record",
    "workspace-cleanup-plan",
    "workspace-cleanup-apply",
    "evaluation-maintain",
    "evaluation-write-publish",
    "evaluation-write-begin",
    "evaluation-write-renew",
    "evaluation-write-abort",
    "evaluation-reader-begin",
    "evaluation-reader-release",
    "evaluation-evidence-record",
    "evaluation-prepare",
    "evaluation-history",
    "assessment-publish",
    "harness-verify",
    "execution-submit",
)


class MethodSurfaceTests(unittest.TestCase):
    def test_cli_names_map_onto_the_unchanged_c_two_operations(self):
        expected = {
            "harnesses": ("control", "capabilities"),
            "submit": ("control", "workflow_submit"),
            "get": ("control", "workflow_get"),
            "decide": ("control", "workflow_decide"),
            "continue": ("control", "workflow_continue"),
            "takeover": ("control", "workflow_takeover"),
            "cancel": ("control", "workflow_cancel"),
            "accept": ("control", "workflow_accept"),
            "conclude": ("control", "workflow_conclude"),
            "reclaim": ("control", "workflow_reclaim"),
            "suggest": ("control", "workflow_suggest"),
            "execution-cancel": ("control", "task_cancel"),
            "execution-retry": ("control", "task_retry"),
            "execution-acknowledge": ("control", "task_acknowledge"),
            "status": ("control", "task_get"),
            "list": ("control", "task_list"),
            "result": ("control", "task_result"),
            "wait": ("wait", "task_wait"),
            "watch": ("wait", "events_wait"),
            "wait-capacity": ("wait", "wait_capacity"),
            "inquire": ("control", "inquiry_observe"),
            "artifacts": ("control", "artifact_list"),
            "events": ("control", "events_read"),
        }
        for method, target in expected.items():
            with self.subTest(method=method):
                self.assertEqual(transport.METHOD_MAP[method], target)
        # Await and installation are CLI-level helpers.
        self.assertEqual(set(cli.METHODS), set(transport.METHOD_MAP) | {"await", "install", "paths"})

    def test_capabilities_is_only_a_c_two_operation_not_a_cli_command(self):
        self.assertNotIn("capabilities", cli.METHODS)
        self.assertNotIn("capabilities", transport.METHOD_MAP)
        self.assertNotIn("capabilities", cli_help.SUMMARIES)
        self.assertIn("capabilities", CONTROL_OPERATIONS)
        self.assertTrue(hasattr(BuddyControl, "capabilities"))
        self.assertTrue(hasattr(BoardService, "capabilities"))
        self.assertTrue(hasattr(BoardClient, "capabilities"))
        output = io.StringIO()
        with contextlib.redirect_stdout(output), mock.patch.object(
            cli, "_call_service", side_effect=AssertionError("unknown commands must not call the service")
        ), self.assertRaises(SystemExit) as caught:
            cli.main(["capabilities", "{}"])
        self.assertEqual(caught.exception.code, 2)
        error = json.loads(output.getvalue())["error"]
        self.assertEqual(error["code"], "UNKNOWN_METHOD")
        self.assertEqual(error["didYouMean"], cli_help.nearest_methods("capabilities", cli.METHODS))

    def test_execution_submission_is_internal_only(self):
        self.assertNotIn("execution-submit", cli.METHODS)
        self.assertNotIn("execution-submit", transport.METHOD_MAP)
        self.assertNotIn("execution-submit", cli_help.SUMMARIES)
        self.assertNotIn("task_submit", CONTROL_OPERATIONS)
        for owner in (BoardService, BuddyControl, BoardClient):
            self.assertFalse(hasattr(owner, "task_submit"))
        self.assertFalse(hasattr(BoardClient, "submit"))

    def test_execution_results_unwrap_the_task_view_and_micro_task_results_do_not(self):
        self.assertEqual(
            transport.UNWRAP_TASK,
            frozenset({"execution-cancel", "execution-retry", "execution-acknowledge", "status"}),
        )
        self.assertEqual(transport.DIRECT_TASK_VIEW, frozenset({"wait"}))
        for micro_task in ("submit", "get", "decide", "continue", "takeover", "cancel", "accept", "conclude", "reclaim", "suggest"):
            self.assertNotIn(micro_task, transport.UNWRAP_TASK)
            self.assertNotIn(micro_task, transport.DIRECT_TASK_VIEW)

    def test_retired_aliases_are_rejected_by_argparse(self):
        for method in (*RETIRED_METHODS, "upgrade", "storage", "harness"):
            with self.subTest(method=method):
                self.assertNotIn(method, cli.METHODS)
                self.assertNotIn(method, transport.METHOD_MAP)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main([method, "{}"])
                self.assertEqual(caught.exception.code, 2)

    def test_retired_evaluation_operations_are_absent_from_every_public_surface(self):
        for method in (
            "evaluation_write_begin", "evaluation_write_renew", "evaluation_write_abort",
            "evaluation_reader_begin", "evaluation_reader_release", "evaluation_evidence_record",
            "evaluation_prepare", "evaluation_history", "assessment_publish",
        ):
            with self.subTest(method=method):
                self.assertNotIn(method, CONTROL_OPERATIONS)
                self.assertFalse(hasattr(BoardService, method))
                self.assertFalse(hasattr(BuddyControl, method))
                self.assertFalse(hasattr(BoardClient, method))
        self.assertFalse(hasattr(BoardClient, "evaluation_write_publish"))
        self.assertIn("user_policy_publish", CONTROL_OPERATIONS)
        self.assertTrue(hasattr(BoardService, "user_policy_publish"))
        self.assertTrue(hasattr(BuddyControl, "user_policy_publish"))

    def test_control_methods_are_the_current_micro_task_mutations(self):
        self.assertEqual(cli.cli_help._CONTROL_METHODS, frozenset({"decide", "continue", "takeover", "cancel", "accept", "conclude", "reclaim", "scope-amend", "workspace-resolve"}))

    def test_console_keeps_the_canonical_name_and_no_alternative_spellings(self):
        self.assertEqual(transport.METHOD_MAP["console"], ("control", "console"))
        self.assertIn("console", cli.METHODS)
        for alias in ("console-open", "console-status", "console-close", "open-console"):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, cli.METHODS)
                self.assertNotIn(alias, transport.METHOD_MAP)
        # The CLI-local browser/wait booleans never turn console into a local method:
        # it still maps onto exactly one named C-Two lifecycle operation.
        self.assertIn("console", transport.METHOD_MAP)

    def test_execution_reporting_configuration_and_selection_operations_stay_distinct(self):
        # The micro task lifecycle never absorbs execution reporting,
        # configuration or selection surfaces.
        for method, operation in (
            ("scope-amend", "workflow_scope_amend"),
            ("workspace-resolve", "workflow_workspace_resolve"),
            ("accept", "workflow_accept"),
            ("conclude", "workflow_conclude"),
            ("reclaim", "workflow_reclaim"),
            ("user-policy-publish", "user_policy_publish"),
            ("buddy-list", "buddy_list"),
            ("selection-request", "selection_request"),
            ("selection-get", "selection_get"),
        ):
            with self.subTest(method=method):
                self.assertIn(method, cli.METHODS)
                self.assertEqual(transport.METHOD_MAP[method], ("control", operation))
        for retired in ("workers", "worker-register", "worker-claim", "worker-reconcile", "worker-renew", "worker-release", "worker-start", "worker-stop", "worker-progress", "worker-result", "execution-progress", "execution-result"):
            self.assertNotIn(retired, cli.METHODS)
            self.assertNotIn(retired, transport.METHOD_MAP)


class HelpTests(unittest.TestCase):
    def help_text(self) -> str:
        with contextlib.redirect_stdout(io.StringIO()) as captured, self.assertRaises(SystemExit):
            cli.main(["--help"])
        return captured.getvalue()

    def test_help_is_current_and_concise(self):
        text = self.help_text()
        for required in (
            "executionWorkspace",
            "controlFile",
            "without a Router call",
            "partial quadruple is rejected",
            "let the Router choose",
            "wait-timeout",
            "Accept one delivered artifact",
            'adjusted":true and a note',
            "conclude ends a failed, cancelled or delivered-but-unaccepted micro task",
            "conclude ends a failed, cancelled or delivered-but-unaccepted micro task",
            "hey-my-buddy get",
            "hey-my-buddy await",
            "no implicit",
            "latest-generation lookup",
        ):
            self.assertIn(required, text)
        # accept/conclude/reclaim replaced the five-step acknowledge protocol, so
        # the lifecycle section carries three one-line examples where one used to be.
        self.assertLess(len(text.splitlines()), 124, "help must stay concise")

    def test_help_never_names_a_retired_alias_or_implicit_control_lookup(self):
        text = self.help_text()
        for removed in ("workflow-submit", "workflow-get", "workflow-decide", "workflow-continue", "buddy run ", "buddy start ",
                        "integration-record", "workspace-cleanup-plan", "workspace-cleanup-apply",
                        "hey-my-buddy acknowledge", "execution-submit"):
            self.assertNotIn(removed, text)
        # The old misleading sentence used to promise a cached generation was reused.
        self.assertNotIn("without it the latest saved generation", text)


class PrivateStateTestCase(BoardTestCase):
    """Point the CLI helpers at this test's private state directory."""

    def setUp(self) -> None:
        super().setUp()
        self._set_private_environment()

    def _set_private_environment(self) -> None:
        names = ("HMB_STATE_DIR", "HMB_AGENT_CREDENTIAL_FILE")
        previous = {name: os.environ.get(name) for name in names}
        os.environ["HMB_STATE_DIR"] = str(self.directory)
        for name in ("HMB_AGENT_CREDENTIAL_FILE",):
            os.environ.pop(name, None)

        def restore() -> None:
            for name, value in previous.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

        self.addCleanup(restore)

    def run_cli(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(arguments))
        text = output.getvalue().strip()
        return code, (json.loads(text) if text else {})

    def use_board_transport(self, board) -> None:
        """Substitute only the C-Two socket; the whole service path stays real."""
        self.enterContext(mock.patch.object(cli, "_call_service", transport._call_service))
        endpoint = {"address": "ipc://in-process-test", "token": "test-token"}
        def ensure_service(state_dir, resource="control"):
            self.assert_rpc_state_dir(state_dir)
            return endpoint

        def request(_endpoint, operation, params, resource="control", *, state_dir):
            self.assert_rpc_state_dir(state_dir)
            return board.call(operation, params)

        self.enterContext(
            mock.patch.object(transport, "_ensure_service", ensure_service)
        )
        self.enterContext(
            mock.patch.object(
                transport, "_request", request
            )
        )


class ModelBuddiesCliTests(PrivateStateTestCase):
    def test_model_buddies_uses_the_named_schema_and_cursor(self):
        self.catalog_fixture()
        board = self.board()
        self.use_board_transport(board)
        board.call("model_catalog_refresh", {"requestId": "cli-buddies-catalog"})
        code, first = self.run_cli("buddy-list", json.dumps({"limit": 1, "includeUnavailable": True}))
        self.assertEqual(code, 0, first)
        self.assertEqual(len(first["buddies"]), 1)
        self.assertIsNotNone(first["nextCursor"])
        code, second = self.run_cli("buddy-list", json.dumps({"limit": 1, "after": first["nextCursor"], "includeUnavailable": True}))
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["buddies"][0]["buddyId"], second["buddies"][0]["buddyId"])
        code, invalid = self.run_cli("buddy-list", json.dumps({"limit": 0}))
        self.assertNotEqual(code, 0)
        self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENT")


class ControlFileTests(PrivateStateTestCase):
    def test_exec_cli_consumes_transferred_document_without_rereading_source_file(self):
        import tempfile
        from hey_my_buddy.install import launcher
        document = {"token": "scoped-token", "stateDir": str(self.directory)}
        with tempfile.TemporaryFile(mode="w+b") as transfer:
            transfer.write(json.dumps(document).encode())
            transfer.seek(0)
            descriptor = os.dup(transfer.fileno())
            with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": str(self.directory / "absent.json"),
                                           "HMB_AGENT_CREDENTIAL_FD": str(descriptor)}), \
                    mock.patch.object(launcher, "read_private", side_effect=AssertionError("source file reread")), \
                    mock.patch.object(cli, "_call_service", return_value={"ok": True}) as service:
                code, result = self.run_cli("ping", "{}")
                self.assertNotIn("HMB_AGENT_CREDENTIAL_FD", os.environ)
        self.assertEqual(code, 0, result)
        self.assertEqual(service.call_args.args[1]["credential"], "scoped-token")

    def test_direct_cli_reads_credential_file_once(self):
        from hey_my_buddy.install import launcher
        credential_file = self.directory / "direct-credential.json"
        credential_file.write_text(json.dumps({"token": "scoped-token", "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
        credential_file.chmod(0o600)
        original_read = launcher.read_private
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": str(credential_file)}), \
                mock.patch.object(launcher, "read_private", wraps=original_read) as read, \
                mock.patch.object(cli, "_call_service", return_value={"ok": True}) as service:
            code, result = self.run_cli("ping", "{}")
        self.assertEqual(code, 0, result)
        read.assert_called_once_with(credential_file)
        self.assertEqual(service.call_args.args[1]["credential"], "scoped-token")

    def test_saved_control_is_private_and_generation_scoped(self):
        triple = {"hostId": "host-1", "ownerGeneration": 2, "controlToken": "token-2"}
        path = cli._save_control("run-1", triple, state_dir=self.directory)
        self.assertEqual(Path(path).name, "run-1.g2.json")
        assert_private_path(self, Path(path), 0o600)
        assert_private_path(self, Path(path).parent, 0o700)
        self.assertEqual(cli._read_control(path), triple)
        # A different generation never overwrites another generation's capability.
        other = cli._save_control("run-1", {"hostId": "host-2", "ownerGeneration": 3, "controlToken": "token-3"}, state_dir=self.directory)
        self.assertNotEqual(other, path)
        self.assertEqual(cli._read_control(path), triple)

    def test_control_file_input_errors_name_the_file_boundary_before_rpc(self):
        for name, contents in (
            ("missing", None),
            ("directory", None),
            ("invalid-json", b"{"),
            ("invalid-utf8", b"\xff"),
            ("null", b"null"),
            ("array", b"[]"),
            ("empty-object", b"{}"),
            ("missing-token", b'{"hostId":"host-1","ownerGeneration":1}'),
        ):
            with self.subTest(name=name):
                path = self.directory / name
                if name == "directory":
                    path.mkdir()
                elif contents is not None:
                    path.write_bytes(contents)
                with mock.patch.object(cli, "_call_service") as service:
                    code, result = self.run_cli("decide", json.dumps({"runId": "run-1", "controlFile": str(path)}))
                self.assertEqual(code, 1)
                self.assertEqual(result["error"]["code"], "INVALID_ARGUMENT")
                self.assertIn("control file", result["error"]["message"].lower())
                service.assert_not_called()

    def test_apply_control_injects_only_the_named_file_and_never_a_latest_generation(self):
        first = cli._save_control("run-1", {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"}, state_dir=self.directory)
        second = cli._save_control("run-1", {"hostId": "host-2", "ownerGeneration": 2, "controlToken": "token-2"}, state_dir=self.directory)

        # Passing no controlFile injects nothing at all, even though files exist.
        self.assertEqual(cli._apply_control({"runId": "run-1"}), {"runId": "run-1"})
        # An explicit file injects exactly that file's capability.
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": first}),
            {"runId": "run-1", "hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"},
        )
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": second}),
            {"runId": "run-1", "hostId": "host-2", "ownerGeneration": 2, "controlToken": "token-2"},
        )
        # An explicit field always wins over the file (the caller named it).
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": first, "hostId": "explicit"}),
            {"runId": "run-1", "hostId": "explicit", "ownerGeneration": 1, "controlToken": "token-1"},
        )

    def test_agent_credential_passes_explicit_control_file_for_service_authorization(self):
        path = cli._save_control("run-1", {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"}, state_dir=self.directory)
        credential_file = self.directory / "control-credential.json"
        credential_file.write_text(json.dumps({"token": "scoped-token", "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
        credential_file.chmod(0o600)
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": str(credential_file)}):
            self.assertEqual(cli._apply_control({"runId": "run-1", "controlFile": path}),
                             {"runId": "run-1", "hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"})
            # A scoped caller may still pass the explicit triple through.
            self.assertEqual(cli._apply_control({"runId": "run-1"}), {"runId": "run-1"})
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": ""}):
            with self.assertRaises(BoardError) as empty:
                cli._agent_credential()
            self.assertEqual(empty.exception.code, "UNAUTHORIZED")

    def test_scrub_and_save_replaces_the_token_with_the_control_file(self):
        result = {
            "runId": "run-1",
            "control": {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "super-secret"},
        }
        cli._scrub_and_save(result, state_dir=self.directory)
        encoded = json.dumps(result)
        self.assertNotIn("super-secret", encoded)
        self.assertNotIn("controlToken", encoded)
        path = result["controlFile"]
        self.assertEqual(result["control"]["controlFile"], path)
        assert_private_path(self, Path(path), 0o600)

        untouched = {"runId": "run-2", "status": "queued"}
        cli._scrub_and_save(untouched, state_dir=self.directory)
        self.assertEqual(untouched, {"runId": "run-2", "status": "queued"})

    def test_submission_capability_is_private_and_stable_per_request(self):
        token = cli._submission_token("req-1", {}, state_dir=self.directory)
        self.assertEqual(cli._submission_token("req-1", {}, state_dir=self.directory), token)
        self.assertEqual(cli._submission_token("req-1", {"cwd": "/elsewhere"}, state_dir=self.directory), token)
        self.assertNotEqual(cli._submission_token("req-2", {}, state_dir=self.directory), token)
        files = list((self.directory / "submissions").glob("*.json"))
        self.assertEqual(len(files), 2)
        for path in files:
            assert_private_path(self, path, 0o600)
        assert_private_path(self, self.directory / "submissions", 0o700)
        with self.assertRaises(BoardError) as missing:
            cli._submission_token("", {}, state_dir=self.directory)
        self.assertEqual(missing.exception.code, "INVALID_ARGUMENT")


class MicroTaskCliTests(PrivateStateTestCase, WorkflowTestCase):
    """End-to-end micro-task CLI commands through the real service path."""

    def submit_payload(self, request_id: str = "cli-req-1", **extra) -> str:
        return json.dumps(
            {
                "requestId": request_id,
                "hostId": "host-1",
                "task": "do the thing",
                "harness": "dsh",
                "provider": "deepseek-official",
                "model": "deepseek-flash",
                "effort": "off",
                "cwd": str(self.workdir()),
                "executionWorkspace": {"kind": "existing", "access": "write"},
                **extra,
            }
        )

    def test_file_credential_is_valid_only_on_its_issuing_board(self):
        board = self.board()
        self.register(board)
        submitted = self.submit(board)
        claim = self.claim(board)
        token = claim["claim"]["agentCredential"]
        credential_file = self.directory / "attempt-credential.json"
        credential_file.write_text(json.dumps({"token": token, "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
        credential_file.chmod(0o600)

        other_state = self.directory / "other-board"
        other_state.mkdir(mode=0o700)
        other_board = InProcessBoard(other_state)
        self._stack.append(other_board)
        with mock.patch.dict(os.environ, {"HMB_STATE_DIR": str(self.directory),
                                       "HMB_AGENT_CREDENTIAL_FILE": str(credential_file)}):
            loaded = cli._agent_credential()
            self.assertEqual(loaded, token)
            self.assertEqual(board.call("workflow_get", {"runId": submitted["runId"],
                                                         "credential": loaded})["runId"], submitted["runId"])
            with self.assertRaises(BoardError) as denied:
                other_board.call("workflow_get", {"runId": submitted["runId"],
                                                   "credential": loaded})
            self.assertEqual(denied.exception.code, "UNAUTHORIZED")

    def test_lead_cli_saves_control_and_await_is_denied_with_its_credential(self):
        from blackboard.tasks.test_workflow import BUDDY
        from support import enable_fixture_buddy
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        lead = self.submit(board, request_id="lead", access="full", delegationAuthority=True,
                           executionWorkspace={"kind": "existing", "access": "full", "accessNote": "Lead checks"})
        self.register(board)
        credential = self.claim(board)["claim"]["agentCredential"]
        self.use_board_transport(board)
        payload = json.loads(self.submit_payload("owned", cwd=str(self.workdir("owned"))))
        payload.pop("hostId")
        credential_file = self.directory / "lead-credential.json"
        credential_file.write_text(json.dumps({"token": credential, "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
        credential_file.chmod(0o600)
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": str(credential_file)}):
            code, child = self.run_cli("submit", json.dumps({**payload, "output": "full"}))
            self.assertEqual(code, 0, child)
            self.assertNotIn("controlFile", child)
            self.assertNotIn("controlToken", json.dumps(child))
            self.register(board, "w2")
            child_claim = self.claim(board, "w2", claim_request_id="owned-claim", run_id=child["runId"])
            original = transport._call_service
            calls = []
            def record(method, params, directory):
                calls.append((method, dict(params)))
                return original(method, params, directory)
            with mock.patch.object(cli, "_call_service", side_effect=record):
                code, result = self.run_cli("await", json.dumps({"requestId": "owned", "waitSeconds": 1}))
            self.assertEqual(code, 1, result)
            self.assertEqual(result["error"]["code"], "FORBIDDEN")
            self.assertEqual(result["error"]["details"]["operation"], "events_wait")
            self.assertTrue(calls)
            self.assertTrue(all(params["credential"] == credential for _, params in calls))
            self.assertNotIn("list", [method for method, _ in calls])
            self.assertTrue(set(method for method, _ in calls) & {"wait", "watch"})
            code, stopped = self.run_cli("cancel", json.dumps({"runId": child["runId"], "output": "full"}))
            self.assertEqual(code, 0, stopped)
            foreign = self.submit(board, request_id="foreign", cwd=str(self.workdir("foreign")))
            for selector in ({"runId": foreign["runId"]}, {"requestId": "foreign"}):
                code, denied = self.run_cli("await", json.dumps({**selector, "waitSeconds": 1}))
                self.assertEqual(code, 1, denied)
                self.assertEqual(denied["error"]["code"], "FORBIDDEN")

    def test_submit_persists_private_control_and_decide_injects_the_named_file(self):
        board = self.board()
        self.use_board_transport(board)
        code, submitted = self.run_cli("submit", self.submit_payload())
        self.assertEqual(code, 0, submitted)
        run_id = submitted["runId"]
        self.assertNotIn("controlToken", json.dumps(submitted))
        control_path = Path(submitted["controlFile"])
        assert_private_path(self, control_path, 0o600)
        assert_private_path(self, control_path.parent, 0o700)

        # The private submission capability was created before the RPC, and a replay
        # recovers the same owner generation without printing a token.
        files = list((self.directory / "submissions").glob("*.json"))
        self.assertEqual(len(files), 1)
        assert_private_path(self, files[0], 0o600)
        code, replay = self.run_cli("submit", self.submit_payload())
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay["runId"], run_id)
        self.assertEqual(replay["controlFile"], submitted["controlFile"])
        self.assertNotIn("controlToken", json.dumps(replay))

        code, view = self.run_cli("get", json.dumps({"runId": run_id}))
        self.assertEqual(code, 0, view)
        self.assertEqual(view["state"], "executing")
        self.assertNotIn("controlToken", json.dumps(view))

        # The named control file authorizes the micro-task mutation...
        code, cancelled = self.run_cli(
            "cancel",
            json.dumps({"runId": run_id, "commandId": "cli-cancel-1", "note": "host stop", "controlFile": submitted["controlFile"]}),
        )
        self.assertEqual(code, 0, cancelled)
        self.assertTrue(cancelled["cancelled"])

    def test_micro_task_mutation_without_the_control_file_fails_closed(self):
        board = self.board()
        self.use_board_transport(board)
        code, submitted = self.run_cli("submit", self.submit_payload("cli-req-2"))
        self.assertEqual(code, 0, submitted)
        # A control file exists on disk for this run, but the CLI must not adopt it:
        # no implicit latest-generation lookup is ever authority.
        self.assertTrue(native_path(Path(submitted["controlFile"])).is_file())
        code, refused = self.run_cli("cancel", json.dumps({"runId": submitted["runId"], "commandId": "cli-cancel-2"}))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
        view = self.run_cli("get", json.dumps({"runId": submitted["runId"]}))[1]
        self.assertEqual(view["state"], "executing")

    def test_takeover_rotates_generation_and_saves_the_new_control_file(self):
        board = self.board()
        self.use_board_transport(board)
        code, submitted = self.run_cli("submit", self.submit_payload("cli-req-3"))
        self.assertEqual(code, 0, submitted)
        old_control = submitted["controlFile"]
        code, taken = self.run_cli(
            "takeover",
            json.dumps(
                {
                    "runId": submitted["runId"],
                    "commandId": "cli-takeover-1",
                    "expectedOwnerGeneration": 1,
                    "newHostId": "host-2",
                    "controlFile": old_control,
                }
            ),
        )
        self.assertEqual(code, 0, taken)
        self.assertNotIn("controlToken", json.dumps(taken))
        self.assertEqual(taken["ownerGeneration"], 2)
        new_control = Path(taken["controlFile"])
        self.assertEqual(new_control.name, f"{submitted['runId']}.g2.json")
        assert_private_path(self, new_control, 0o600)

        # The superseded generation is fenced, not adopted.
        code, stale = self.run_cli(
            "cancel",
            json.dumps({"runId": submitted["runId"], "commandId": "cli-cancel-3", "controlFile": old_control}),
        )
        self.assertEqual(code, 1)
        self.assertEqual(stale["error"]["code"], "STALE_GENERATION")
        # The current generation's file authorizes the mutation.
        code, cancelled = self.run_cli(
            "cancel",
            json.dumps({"runId": submitted["runId"], "commandId": "cli-cancel-4", "controlFile": str(new_control)}),
        )
        self.assertEqual(code, 0, cancelled)
        self.assertTrue(cancelled["cancelled"])

    def test_invalid_scoped_submission_cannot_admit_work_despite_local_submission_capability(self):
        board = self.board()
        self.use_board_transport(board)
        credential_file = self.directory / "invalid-credential.json"
        credential_file.write_text(json.dumps({"token": "not-a-real-credential", "stateDir": str(self.directory)}), encoding="utf-8", newline="\n")
        credential_file.chmod(0o600)
        with mock.patch.dict(os.environ, {"HMB_AGENT_CREDENTIAL_FILE": str(credential_file)}):
            code, refused = self.run_cli("submit", self.submit_payload("cli-req-4"))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(len(list((self.directory / "submissions").glob("*.json"))), 1)
        self.assertEqual(board.store.count_tasks(), 0)


if __name__ == "__main__":
    unittest.main()
