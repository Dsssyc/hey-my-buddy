"""CLI surface tests: the current single-contract names, help and Host control privacy.

The governed command path is driven through the real ``buddy.cli`` module, the real
``transport.call_service`` mapping and the real store/service validators; only the
C-Two socket is substituted with the in-process board and only the Git-backed
workspace module is replaced by the deterministic double.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import unittest
from pathlib import Path
from unittest import mock

from support import BoardTestCase
from test_workflow import WorkflowTestCase

import buddy.cli as cli
import buddy.transport as transport
from buddy.errors import BoardError

#: Every CLI method that was removed by ADR-007, plus the ADR-008 removal of the
#: internal maintenance call. None may remain as an alias.
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
    "evaluation-maintain",
    "evaluation-write-publish",
)


class MethodSurfaceTests(unittest.TestCase):
    def test_cli_names_map_onto_the_unchanged_c_two_operations(self):
        expected = {
            "submit": ("control", "workflow_submit"),
            "get": ("control", "workflow_get"),
            "decide": ("control", "workflow_decide"),
            "continue": ("control", "workflow_continue"),
            "takeover": ("control", "workflow_takeover"),
            "cancel": ("control", "workflow_cancel"),
            "acknowledge": ("control", "workflow_acknowledge"),
            "suggest": ("control", "workflow_suggest"),
            "execution-submit": ("control", "task_submit"),
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
        # Await, the new-package upgrade coordinator and skill install are CLI-level helpers.
        self.assertEqual(set(cli.METHODS), set(transport.METHOD_MAP) | set(cli.LOCAL_METHODS) | {"await", "upgrade", "install", "paths", "backup-preflight"})
        self.assertEqual(cli.LOCAL_METHODS, ("worker-start", "worker-stop"))

    def test_execution_results_unwrap_the_task_view_and_goal_results_do_not(self):
        self.assertEqual(
            transport.UNWRAP_TASK,
            frozenset({"execution-submit", "execution-cancel", "execution-retry", "execution-acknowledge", "status"}),
        )
        self.assertEqual(transport.DIRECT_TASK_VIEW, frozenset({"wait"}))
        for governed in ("submit", "get", "decide", "continue", "takeover", "cancel", "acknowledge", "suggest"):
            self.assertNotIn(governed, transport.UNWRAP_TASK)
            self.assertNotIn(governed, transport.DIRECT_TASK_VIEW)

    def test_retired_aliases_are_rejected_by_argparse(self):
        for method in RETIRED_METHODS:
            with self.subTest(method=method):
                self.assertNotIn(method, cli.METHODS)
                self.assertNotIn(method, transport.METHOD_MAP)
                with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
                    cli.main([method, "{}"])
                self.assertEqual(caught.exception.code, 2)

    def test_control_methods_are_the_current_governed_mutations(self):
        self.assertEqual(cli.CONTROL_METHODS, frozenset({"decide", "continue", "takeover", "cancel", "acknowledge", "scope-amend", "workspace-resolve", "integration-record", "workspace-cleanup-plan", "workspace-cleanup-apply"}))

    def test_console_keeps_the_canonical_name_and_no_alternative_spellings(self):
        self.assertEqual(transport.METHOD_MAP["console"], ("control", "console"))
        self.assertIn("console", cli.METHODS)
        for alias in ("console-open", "console-status", "console-close", "open-console"):
            with self.subTest(alias=alias):
                self.assertNotIn(alias, cli.METHODS)
                self.assertNotIn(alias, transport.METHOD_MAP)
        # The CLI-local browser/wait booleans never turn console into a local method:
        # it still maps onto exactly one named C-Two lifecycle operation.
        self.assertNotIn("console", cli.LOCAL_METHODS)

    def test_worker_evaluation_and_selection_operations_stay_distinct(self):
        # The governed goal lifecycle never absorbs the separate worker, evaluation
        # or selection surfaces; they keep their names and their own operations.
        for method, operation in (
            ("workers", "worker_list"),
            ("worker-register", "worker_register"),
            ("worker-claim", "worker_claim"),
            ("worker-reconcile", "worker_reconcile"),
            ("worker-renew", "worker_renew"),
            ("worker-progress", "worker_progress"),
            ("worker-result", "worker_result"),
            ("worker-release", "worker_release"),
            ("evaluation-write-begin", "evaluation_write_begin"),
            ("scope-amend", "workflow_scope_amend"),
            ("workspace-resolve", "workflow_workspace_resolve"),
            ("integration-record", "workflow_integration_record"),
            ("workspace-cleanup-plan", "workspace_cleanup_plan"),
            ("workspace-cleanup-apply", "workspace_cleanup_apply"),
            ("user-policy-publish", "user_policy_publish"),
            ("assessment-publish", "assessment_publish"),
            ("model-profiles", "model_profiles"),
            ("evaluation-reader-begin", "evaluation_reader_begin"),
            ("evaluation-evidence-record", "evaluation_evidence_record"),
            ("evaluation-prepare", "evaluation_prepare"),
            ("evaluation-history", "evaluation_history"),
            ("selection-request", "selection_request"),
            ("selection-get", "selection_get"),
        ):
            with self.subTest(method=method):
                self.assertIn(method, cli.METHODS)
                self.assertEqual(transport.METHOD_MAP[method], ("control", operation))
        for local in ("worker-start", "worker-stop"):
            self.assertIn(local, cli.LOCAL_METHODS)
            self.assertNotIn(local, transport.METHOD_MAP)


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
            "routing-model call",
            "hard filter",
            "evaluation table",
            "wait-timeout",
            "Inspect the selected final artifact first",
            "execution-submit",
            "buddy get",
            "buddy await",
            "no implicit",
            "latest-generation lookup",
        ):
            self.assertIn(required, text)
        self.assertLess(len(text.splitlines()), 120, "help must stay concise")

    def test_help_never_names_a_retired_alias_or_implicit_control_lookup(self):
        text = self.help_text()
        for removed in ("workflow-submit", "workflow-get", "workflow-decide", "workflow-continue", "buddy run ", "buddy start "):
            self.assertNotIn(removed, text)
        # The old misleading sentence used to promise a cached generation was reused.
        self.assertNotIn("without it the latest saved generation", text)


class PrivateStateTestCase(BoardTestCase):
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

    def run_cli(self, *arguments: str) -> tuple[int, dict]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
            code = cli.main(list(arguments))
        text = output.getvalue().strip()
        return code, (json.loads(text) if text else {})

    def use_board_transport(self, board) -> None:
        """Substitute only the C-Two socket; the whole service path stays real."""
        endpoint = {"address": "ipc://in-process-test", "token": "test-token"}
        self.enterContext(
            mock.patch.object(transport, "ensure_service", lambda state_dir=None, resource="control": endpoint)
        )
        self.enterContext(
            mock.patch.object(
                transport, "_request", lambda _endpoint, operation, params, resource="control": board.call(operation, params)
            )
        )


class ModelProfilesCliTests(PrivateStateTestCase):
    def test_model_profiles_uses_the_named_schema_and_cursor(self):
        self.catalog_fixture()
        board = self.board()
        self.use_board_transport(board)
        board.call("model_catalog_refresh", {"requestId": "cli-profiles-catalog"})
        code, first = self.run_cli("model-profiles", json.dumps({"limit": 1, "includeUnavailable": True}))
        self.assertEqual(code, 0, first)
        self.assertEqual(len(first["profiles"]), 1)
        self.assertIsNotNone(first["nextCursor"])
        code, second = self.run_cli("model-profiles", json.dumps({"limit": 1, "after": first["nextCursor"], "includeUnavailable": True}))
        self.assertEqual(code, 0, second)
        self.assertNotEqual(first["profiles"][0]["profileId"], second["profiles"][0]["profileId"])
        code, invalid = self.run_cli("model-profiles", json.dumps({"limit": 0}))
        self.assertNotEqual(code, 0)
        self.assertEqual(invalid["error"]["code"], "INVALID_ARGUMENT")


class ControlFileTests(PrivateStateTestCase):
    def test_saved_control_is_private_and_generation_scoped(self):
        triple = {"hostId": "host-1", "ownerGeneration": 2, "controlToken": "token-2"}
        path = cli._save_control("run-1", triple)
        self.assertEqual(Path(path).name, "run-1.g2.json")
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(path).parent.stat().st_mode), 0o700)
        self.assertEqual(cli._read_control(path), triple)
        # A different generation never overwrites another generation's capability.
        other = cli._save_control("run-1", {"hostId": "host-2", "ownerGeneration": 3, "controlToken": "token-3"})
        self.assertNotEqual(other, path)
        self.assertEqual(cli._read_control(path), triple)

    def test_read_control_refuses_loose_modes_links_and_unknown_fields(self):
        triple = {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"}
        path = Path(cli._save_control("run-1", triple))
        path.chmod(0o644)
        with self.assertRaises(BoardError) as loose:
            cli._read_control(str(path))
        self.assertEqual(loose.exception.code, "INSECURE_CONTROL_FILE")
        path.chmod(0o600)

        link = path.parent / "run-link.json"
        link.symlink_to(path)
        with self.assertRaises(BoardError) as linked:
            cli._read_control(str(link))
        self.assertEqual(linked.exception.code, "INSECURE_CONTROL_FILE")

        path.write_text(json.dumps({**triple, "unexpected": True}))
        path.chmod(0o600)
        with self.assertRaises(BoardError) as unexpected:
            cli._read_control(str(path))
        self.assertEqual(unexpected.exception.code, "INVALID_ARGUMENT")

        with self.assertRaises(BoardError) as missing:
            cli._read_control(str(path.parent / "absent.json"))
        self.assertEqual(missing.exception.code, "INVALID_ARGUMENT")

    def test_apply_control_injects_only_the_named_file_and_never_a_latest_generation(self):
        first = cli._save_control("run-1", {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"})
        second = cli._save_control("run-1", {"hostId": "host-2", "ownerGeneration": 2, "controlToken": "token-2"})

        # Passing no controlFile injects nothing at all, even though files exist.
        self.assertEqual(cli._apply_control({"runId": "run-1"}, "decide"), {"runId": "run-1"})
        # An explicit file injects exactly that file's capability.
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": first}, "decide"),
            {"runId": "run-1", "hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"},
        )
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": second}, "cancel"),
            {"runId": "run-1", "hostId": "host-2", "ownerGeneration": 2, "controlToken": "token-2"},
        )
        # An explicit field always wins over the file (the caller named it).
        self.assertEqual(
            cli._apply_control({"runId": "run-1", "controlFile": first, "hostId": "explicit"}, "decide"),
            {"runId": "run-1", "hostId": "explicit", "ownerGeneration": 1, "controlToken": "token-1"},
        )

    def test_agent_credential_cannot_use_a_host_control_file(self):
        path = cli._save_control("run-1", {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "token-1"})
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL": "scoped-token"}):
            with self.assertRaises(BoardError) as refused:
                cli._apply_control({"runId": "run-1", "controlFile": path}, "decide")
            self.assertEqual(refused.exception.code, "FORBIDDEN")
            # A scoped caller may still pass the explicit triple through.
            self.assertEqual(cli._apply_control({"runId": "run-1"}, "get"), {"runId": "run-1"})
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL": ""}):
            with self.assertRaises(BoardError) as empty:
                cli._agent_credential()
            self.assertEqual(empty.exception.code, "UNAUTHORIZED")

    def test_scrub_and_save_replaces_the_token_with_the_control_file(self):
        result = {
            "runId": "run-1",
            "control": {"hostId": "host-1", "ownerGeneration": 1, "controlToken": "super-secret"},
        }
        cli._scrub_and_save(result)
        encoded = json.dumps(result)
        self.assertNotIn("super-secret", encoded)
        self.assertNotIn("controlToken", encoded)
        path = result["controlFile"]
        self.assertEqual(result["control"]["controlFile"], path)
        self.assertEqual(stat.S_IMODE(Path(path).stat().st_mode), 0o600)

        untouched = {"runId": "run-2", "status": "queued"}
        cli._scrub_and_save(untouched)
        self.assertEqual(untouched, {"runId": "run-2", "status": "queued"})

    def test_submission_capability_is_private_and_stable_per_request(self):
        token = cli._submission_token("req-1", {})
        self.assertEqual(cli._submission_token("req-1", {}), token)
        self.assertEqual(cli._submission_token("req-1", {"cwd": "/elsewhere"}), token)
        self.assertNotEqual(cli._submission_token("req-2", {}), token)
        files = list((self.directory / "submissions").glob("*.json"))
        self.assertEqual(len(files), 2)
        for path in files:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE((self.directory / "submissions").stat().st_mode), 0o700)
        with self.assertRaises(BoardError) as missing:
            cli._submission_token("", {})
        self.assertEqual(missing.exception.code, "INVALID_ARGUMENT")


class GovernedCliTests(PrivateStateTestCase, WorkflowTestCase):
    """End-to-end governed CLI commands through the real service path."""

    def submit_payload(self, request_id: str = "cli-req-1", **extra) -> str:
        return json.dumps(
            {
                "requestId": request_id,
                "hostId": "host-1",
                "task": "do the thing",
                "adapter": "dsh",
                "provider": "deepseek-official",
                "model": "deepseek-flash",
                "effort": "off",
                "cwd": str(self.workdir()),
                "executionWorkspace": {"kind": "existing", "access": "write"},
                **extra,
            }
        )

    def test_submit_persists_private_control_and_decide_injects_the_named_file(self):
        board = self.board()
        self.use_board_transport(board)
        code, submitted = self.run_cli("submit", self.submit_payload())
        self.assertEqual(code, 0, submitted)
        run_id = submitted["runId"]
        self.assertNotIn("controlToken", json.dumps(submitted))
        control_path = Path(submitted["controlFile"])
        self.assertEqual(stat.S_IMODE(control_path.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(control_path.parent.stat().st_mode), 0o700)

        # The private submission capability was created before the RPC, and a replay
        # recovers the same owner generation without printing a token.
        files = list((self.directory / "submissions").glob("*.json"))
        self.assertEqual(len(files), 1)
        self.assertEqual(stat.S_IMODE(files[0].stat().st_mode), 0o600)
        code, replay = self.run_cli("submit", self.submit_payload())
        self.assertEqual(code, 0, replay)
        self.assertEqual(replay["runId"], run_id)
        self.assertEqual(replay["controlFile"], submitted["controlFile"])
        self.assertNotIn("controlToken", json.dumps(replay))

        code, view = self.run_cli("get", json.dumps({"runId": run_id}))
        self.assertEqual(code, 0, view)
        self.assertEqual(view["state"], "executing")
        self.assertNotIn("controlToken", json.dumps(view))

        # The named control file authorizes the governed mutation...
        code, cancelled = self.run_cli(
            "cancel",
            json.dumps({"runId": run_id, "commandId": "cli-cancel-1", "reason": "host stop", "controlFile": submitted["controlFile"]}),
        )
        self.assertEqual(code, 0, cancelled)
        self.assertTrue(cancelled["cancelled"])

    def test_governed_mutation_without_the_control_file_fails_closed(self):
        board = self.board()
        self.use_board_transport(board)
        code, submitted = self.run_cli("submit", self.submit_payload("cli-req-2"))
        self.assertEqual(code, 0, submitted)
        # A control file exists on disk for this run, but the CLI must not adopt it:
        # no implicit latest-generation lookup is ever authority.
        self.assertTrue(Path(submitted["controlFile"]).is_file())
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
        self.assertEqual(stat.S_IMODE(new_control.stat().st_mode), 0o600)

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

    def test_scoped_caller_never_mints_a_submission_capability(self):
        board = self.board()
        self.use_board_transport(board)
        with mock.patch.dict(os.environ, {"BUDDY_AGENT_CREDENTIAL": "not-a-real-credential"}):
            code, refused = self.run_cli("submit", self.submit_payload("cli-req-4"))
        self.assertEqual(code, 1)
        self.assertEqual(refused["error"]["code"], "UNAUTHORIZED")
        self.assertEqual(list((self.directory / "submissions").glob("*.json")), [])


if __name__ == "__main__":
    unittest.main()
