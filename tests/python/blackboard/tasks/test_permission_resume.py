"""Service-owned resume selection uses each turn's frozen permission level."""
from types import SimpleNamespace

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.buddy.harnesses.base import Adapter
from hey_my_buddy.buddy.harnesses.registry import adapter


class PermissionResumeTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        for name in ("codex", "zcode", "dsh", "claude"):
            declared = adapter(name)
            self.executors[name] = SimpleNamespace(
                native_resume=declared.native_resume,
                native_resume_permission_change=declared.native_resume_permission_change,
                validate_turn_provenance=lambda record: None,
            )

    def test_native_permission_change_declarations(self):
        self.assertFalse(Adapter.native_resume_permission_change)
        for name, expected in (("codex", True), ("zcode", True), ("dsh", False), ("claude", False)):
            with self.subTest(harness=name):
                self.assertEqual(adapter(name).native_resume_permission_change, expected)

    def assert_permission_sequence(self, harness, levels):
        board = self.board()
        board.call("worker_register", {"workerId": "w1", "capabilities": [harness]})
        submitted = self.submit(
            board, harness=harness, provider="fixture", model="fixture", effort="high", access=levels[0])
        first = self.claim(board)
        self.assertEqual(first["claim"]["turn"]["resumeMode"], "initial")
        session = "original-session"
        owner = {"codex": "harness-user-store", "dsh": "hey-my-buddy-micro-task"}.get(harness)
        for index, access in enumerate(levels[1:], 1):
            self.finish_turn(board, first, disposition="attention", session_id=session,
                             result_mutator=lambda result: result.update(nativeSession={"storageOwner": owner}))
            view = board.call("workflow_get", {"runId": submitted["runId"]})
            kwargs = {"access": access}
            if access == "full":
                kwargs["accessReason"] = "Need unrestricted fixture operations"
            self.continue_run(board, view, command_id=f"continue-{index}", **kwargs)
            # The current task already has the new level; the old turn must keep
            # the old one so this comparison cannot use the task's current access.
            with board.store.db.read() as db:
                previous = db.execute("SELECT access FROM workflow_turns WHERE turn_id=?",
                                      (first["claim"]["turn"]["turnId"],)).fetchone()
            self.assertEqual(previous["access"], levels[index - 1])
            first = self.claim(board, claim_request_id=f"claim-{index}")
            turn = first["claim"]["turn"]
            changed = levels[index - 1] != access
            reconstructed = harness == "claude" or (harness == "dsh" and changed)
            self.assertEqual(turn["resumeMode"],
                             "reconstructed-new-session" if reconstructed else "native-session")
            self.assertEqual(turn["input"]["previousSessionId"], session)
            self.assertEqual(turn["input"]["executionWorkspace"]["access"], access)
            if harness == "dsh" and changed:
                self.assertEqual(turn["input"]["context"]["resumeReason"], "permission-level-changed")
            else:
                self.assertNotIn("resumeReason", turn["input"]["context"])
            if reconstructed:
                session = f"new-session-{index}"

    def test_codex_changes_permission_on_the_native_session(self):
        self.assert_permission_sequence("codex", ("read", "write", "write", "read", "read", "full", "full", "write"))

    def test_zcode_changes_permission_on_the_native_session(self):
        self.assert_permission_sequence("zcode", ("read", "write", "write", "read", "read", "full", "full", "write"))

    def test_dsh_reconstructs_for_every_permission_change_and_resumes_same_level(self):
        self.assert_permission_sequence("dsh", ("read", "write", "write", "read", "read", "full", "full", "write"))

    def test_claude_reconstructs_even_when_permission_stays_the_same(self):
        self.assert_permission_sequence("claude", ("read", "write", "write", "read", "read", "full", "full", "write"))
