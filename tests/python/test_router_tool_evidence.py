"""The blackboard side of unified tool evidence: claim-frozen toolPolicy and the bound wrapper.

Every test uses the production store and the real claim transaction against a
private state directory (no model call): the claim path freezes the local
system-sandbox program fact into this attempt's durable row, and
``DecisionCoordinator._tool_evidence_problem`` binds a receipt's ``toolEvidence``
to that frozen attempt before the single judge runs, judging by the toolPolicy
the wiring grafts onto the frozen input document. The wrapper stays unwired in
L5-0A; these tests exercise it directly.
"""
import json
import unittest
from unittest.mock import patch

from test_decision import DecisionTestCase, PROFILE_ID

from buddy import tool_evidence

NONCE = "nonce-abcdefghijklmnop"
ROOT = {"sessionId": "session-1", "turnId": "turn-1"}
SECOND_ROOT = {"sessionId": "session-2", "inputId": "input-2"}


def settled(category, call_id, identity=None, tool="native-tool"):
    identity = dict(identity or ROOT)
    return [
        {"nativeIdentity": identity, "callId": call_id, "toolName": tool, "category": category, "phase": "start"},
        {"nativeIdentity": identity, "callId": call_id, "toolName": tool, "category": category, "phase": "end"},
    ]


class RouterToolEvidenceTests(DecisionTestCase):
    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def configure(self, board, **settings):
        revision = board.call("console_snapshot", {})["tableRevision"]
        grant = board.console_call("evaluation_write_begin", {
            "requestId": f"config-{revision}", "kind": "human", "expectedRevision": revision})
        return board.console_call("user_policy_publish", {
            **{key: grant[key] for key in ("writerId", "generation", "writerToken")},
            "commandId": f"publish-{revision}", "expectedRevision": revision, "configuration": settings})

    def request_route(self, board, request_id, worker="router"):
        result = board.call("selection_request", {"requestId": request_id, "task": "choose"})
        board.call("worker_register", {"workerId": worker, "adapter": "decision", "capabilities": ["decision"]})
        claim = board.client().claim(worker, f"claim-{request_id}", NONCE, task_id=result["runId"])["claim"]
        return result, claim

    def frozen(self, board, decision_id):
        with board.store.db.read() as connection:
            row = board.store.decisions._row(connection, decision_id)
            return row, json.loads(row["input_json"])

    def attempt_policy(self, board, attempt_id):
        with board.store.db.read() as connection:
            return board.store.decisions._attempt_tool_policy(connection, attempt_id)

    def wired_document(self, board, decision_id):
        """The frozen input exactly as the one wiring step will hand the wrapper.

        Publication grafts the attempt's claim-frozen toolPolicy onto the frozen
        input document; an attempt that froze none — an old or unbound result —
        contributes nothing and cannot publish.
        """
        row, document = self.frozen(board, decision_id)
        if row["decision_attempt_id"]:
            policy = self.attempt_policy(board, row["decision_attempt_id"])
            if policy is not None:
                document = {**document, "toolPolicy": policy}
        return row, document

    def problem(self, board, decision_id, output):
        row, document = self.wired_document(board, decision_id)
        return board.store.decisions._tool_evidence_problem(row, document, output)

    def package_for(self, claim, result, events, roots=None, stream_complete=True):
        evidence = tool_evidence.ToolEventEvidence({
            "adapter": "dsh", "taskId": result["runId"],
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
        })
        for event in events:
            evidence.observe(event)
        return evidence.finish(list(roots if roots is not None else [ROOT]), stream_complete)

    # -- claim-side freezing -------------------------------------------------
    def test_claim_freezes_the_local_system_sandbox_fact(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "freeze-false")
        self.assertEqual(self.attempt_policy(board, claim["attempt"]["attemptId"]),
                         {"systemSandbox": False})
        # The frozen model-input document itself stays byte-identical to its
        # admission form: the attempt fact lives in the claim-time row, not in
        # the input the Router consumed.
        _row, document = self.frozen(board, result["decisionId"])
        self.assertNotIn("toolPolicy", document)
        self.assertNotIn("toolPolicy", claim["decisionInput"])

    def test_the_frozen_fact_follows_the_claim_time_health_only(self):
        board = self.board()
        self.seed(board)
        result, first = self.request_route(board, "freeze-once")
        with patch("buddy.adapters.dsh.DshAdapter.local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": None,
                "systemSandbox": True, "sameAttemptContinuation": False}):
            _later, second = self.request_route(board, "freeze-true", worker="router-2")
            self.assertEqual(self.attempt_policy(board, second["attempt"]["attemptId"]),
                             {"systemSandbox": True})
        # The first attempt's frozen fact never changes with later health reads.
        self.assertEqual(self.attempt_policy(board, first["attempt"]["attemptId"]),
                         {"systemSandbox": False})

    def test_a_document_without_a_frozen_tool_policy_cannot_publish(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "unfrozen")
        package = self.package_for(claim, result, settled("read", "call-1", tool="read"))
        output = {"nativeIdentity": ROOT, "toolEvidence": package}
        # Grafted exactly as the wiring will, the same output passes.
        self.assertIsNone(self.problem(board, result["decisionId"], output))
        # The raw frozen document carries no toolPolicy, so an unbound or old
        # result — one whose document was never grafted — cannot publish.
        row, document = self.frozen(board, result["decisionId"])
        self.assertNotIn("toolPolicy", document)
        problem = board.store.decisions._tool_evidence_problem(row, document, output)
        self.assertEqual(problem["code"], "router-tool-evidence-unverified")

    def test_a_pre_claim_document_has_no_frozen_tool_policy(self):
        board = self.board()
        self.seed(board)
        result = board.call("selection_request", {"requestId": "pre-claim", "task": "choose"})
        row, document = self.frozen(board, result["decisionId"])
        self.assertNotIn("toolPolicy", document)
        self.assertIsNone(row["decision_attempt_id"])
        with board.store.db.read() as connection:
            self.assertIsNone(board.store.decisions._attempt_tool_policy(connection, "attempt-unbound"))
        problem = board.store.decisions._tool_evidence_problem(
            row, document, {"nativeIdentity": ROOT, "toolEvidence": collect_empty(row, result)})
        self.assertEqual(problem["code"], "router-tool-evidence-unverified")

    # -- the bound wrapper ---------------------------------------------------
    def test_bound_complete_evidence_passes_unwired_publication(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "bound-ok")
        events = settled("read", "call-1", tool="read") + settled("search", "call-2", tool="grep")
        package = self.package_for(claim, result, events)
        self.assertIsNone(self.problem(board, result["decisionId"],
                                       {"nativeIdentity": ROOT, "toolEvidence": package}))

    def test_every_binding_mismatch_is_rejected(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "bindings")
        package = self.package_for(claim, result, settled("read", "call-1", tool="read"))
        good = {"nativeIdentity": ROOT, "toolEvidence": package}
        self.assertIsNone(self.problem(board, result["decisionId"], good))
        for replace in ({"adapter": "zcode"}, {"taskId": "task-foreign"}, {"attemptId": "attempt-foreign"},
                        {"generation": package["binding"]["generation"] + 1}):
            with self.subTest(replace=replace):
                broken = {**good, "toolEvidence": {**package, "binding": {**package["binding"], **replace}}}
                problem = self.problem(board, result["decisionId"], broken)
                self.assertEqual(problem["code"], "router-tool-evidence-unverified")

    def test_missing_evidence_or_identity_is_rejected(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "missing")
        package = self.package_for(claim, result, settled("read", "call-1", tool="read"))
        for output in ({"nativeIdentity": ROOT}, {"toolEvidence": package},
                       {"nativeIdentity": [], "toolEvidence": package},
                       {"nativeIdentity": [SECOND_ROOT], "toolEvidence": package}):
            with self.subTest(output=output):
                problem = self.problem(board, result["decisionId"], output)
                self.assertEqual(problem["code"], "router-tool-evidence-unverified")

    def test_the_receipt_roots_must_be_covered_by_the_evidence(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "roots")
        events = settled("read", "call-1", tool="read")
        single = self.package_for(claim, result, events, roots=[ROOT])
        both = self.package_for(claim, result, events, roots=[ROOT, SECOND_ROOT])
        for evidence, reported, expected in (
                (single, [ROOT], None), (single, ROOT, None),
                (single, [ROOT, SECOND_ROOT], "router-tool-evidence-unverified"),
                (both, [SECOND_ROOT], None)):
            with self.subTest(roots=evidence["nativeIdentity"], reported=reported):
                problem = self.problem(board, result["decisionId"],
                                       {"nativeIdentity": reported, "toolEvidence": evidence})
                self.assertEqual(problem and problem["code"], expected)

    def test_the_frozen_policy_selects_the_review_matrix(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "no-sandbox")
        read_only = self.package_for(claim, result, settled("read", "call-1", tool="read"))
        execute = self.package_for(claim, result, settled("execute", "call-1", tool="shell"))
        output = {"nativeIdentity": ROOT}
        self.assertIsNone(self.problem(board, result["decisionId"], {**output, "toolEvidence": read_only}))
        problem = self.problem(board, result["decisionId"], {**output, "toolEvidence": execute})
        self.assertEqual(problem["code"], "router-tools-forbidden")
        with patch("buddy.adapters.dsh.DshAdapter.local_read_only_check", return_value={
                "eligible": True, "reasonCode": None, "reason": None,
                "systemSandbox": True, "sameAttemptContinuation": False}):
            sandboxed, sandboxed_claim = self.request_route(board, "sandbox", worker="router-2")
            self.assertEqual(self.attempt_policy(board, sandboxed_claim["attempt"]["attemptId"]),
                             {"systemSandbox": True})
            allowed = self.package_for(sandboxed_claim, sandboxed, settled("execute", "call-1", tool="shell"))
            edit = self.package_for(sandboxed_claim, sandboxed, settled("edit", "call-1", tool="Write"))
            self.assertIsNone(self.problem(board, sandboxed["decisionId"], {**output, "toolEvidence": allowed}))
            problem = self.problem(board, sandboxed["decisionId"], {**output, "toolEvidence": edit})
            self.assertEqual(problem["code"], "router-tools-forbidden")

    def test_incomplete_facts_stay_unverified_and_never_become_forbidden(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "incomplete")
        truncated = self.package_for(claim, result, settled("read", "call-1", tool="read"),
                                     stream_complete=False)
        unsettled = self.package_for(claim, result, [settled("read", "call-1", tool="read")[0]])
        foreign = self.package_for(claim, result, settled("read", "call-1", identity=SECOND_ROOT, tool="read"))
        for name, package in (("truncated", truncated), ("unsettled", unsettled), ("foreign", foreign)):
            with self.subTest(condition=name):
                problem = self.problem(board, result["decisionId"],
                                       {"nativeIdentity": ROOT, "toolEvidence": package})
                self.assertEqual(problem["code"], "router-tool-evidence-unverified")

    def test_fast_mode_allows_no_tool_call_through_the_wrapper(self):
        board = self.board()
        self.seed(board)
        self.enterContext(patch("buddy.adapters.dsh.DshAdapter.no_tool_structured", True, create=True))
        self.configure(board, routerProfileId=PROFILE_ID, defaultRoutingMode="fast")
        result, claim = self.request_route(board, "fast-zero")
        self.assertEqual(claim["decisionInput"]["routingMode"], "fast")
        empty = self.package_for(claim, result, [])
        self.assertIsNone(self.problem(board, result["decisionId"],
                                       {"nativeIdentity": ROOT, "toolEvidence": empty}))
        one_call = self.package_for(claim, result, settled("read", "call-1", tool="read"))
        problem = self.problem(board, result["decisionId"], {"nativeIdentity": ROOT, "toolEvidence": one_call})
        self.assertEqual(problem["code"], "router-tools-forbidden")


def collect_empty(row, result):
    """A well-formed zero-call package bound to the row's own frozen identity."""
    return tool_evidence.ToolEventEvidence({
        "adapter": "dsh", "taskId": result["runId"],
        "attemptId": row["decision_attempt_id"] or "attempt-unbound",
        "generation": row["decision_generation"] if row["decision_generation"] is not None else 0,
    }).finish([ROOT], True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
