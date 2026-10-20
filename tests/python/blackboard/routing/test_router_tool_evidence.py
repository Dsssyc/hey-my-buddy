"""Router evidence is judged from the digest-bound routing mode."""
from admission import admit_execution
import json
import uuid
import unittest
from unittest.mock import patch

from blackboard.routing.test_decision import DecisionTestCase, BUDDY_ID

from hey_my_buddy.protocol import tool_evidence
from support import InProcessBoard
from blackboard.routing.test_single_router_claims import stopped_answer

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
    def test_publication_uses_one_matrix_and_the_claim_policy(self):
        cases = (
            ('fast', None, True, None),
            ('fast', 'read', True, 'router-tools-forbidden'),
            ('review', 'execute', True, None),
            ('review', 'read', True, None),
            ('review', 'search', True, None),
            ('review', 'edit', True, 'router-tools-forbidden'),
            ('review', 'fetch', True, None),
            ('review', 'other', True, 'router-tools-forbidden'),
            ('review', 'read', False, 'router-tool-evidence-unverified'),
        )
        for number, (mode, category, complete, expected) in enumerate(cases):
            with self.subTest(mode=mode, category=category, complete=complete):
                board = InProcessBoard(self.directory / f'publication-{number}')
                self._stack.append(board)
                self.seed(board)
                self.configure(board, defaultRoutingMode=mode)
                requested, claim = self.request_route(board, f'matrix-{number}')
                events = settled(category, 'call-1') if category else []
                package = self.package_for(claim, requested, events, stream_complete=complete)
                output = {**stopped_answer(claim), 'nativeIdentity': ROOT, 'toolEvidence': package,
                          'usage': {'elapsedMs': 100, 'toolCalls': 1 if category else 0}}
                board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': NONCE, 'status': 'ok', 'shutdownConfirmed': True, 'result': output})
                view = self.decision(board, requested['decisionId'])
                self.assertEqual(view['status'], 'needs-host' if expected else 'completed')
                self.assertEqual((view['reason'] or {}).get('code'), expected)
                self.assertEqual(view['selectedBuddy'] is None, expected is not None)
                with board.store.db.read() as connection:
                    self.assertEqual(connection.execute('SELECT COUNT(*) FROM attempts').fetchone()[0], 1)
                board.close()
                self._stack.remove(board)

    def test_publication_rejects_usage_mismatch(self):
        board = self.board()
        self.seed(board)
        requested, claim = self.request_route(board, 'usage-mismatch')
        output = stopped_answer(claim)
        output['usage']['toolCalls'] = 1
        board.call('execution_result', {'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'], 'nonce': NONCE, 'status': 'ok', 'shutdownConfirmed': True, 'result': output})
        self.assertEqual(self.decision(board, requested['decisionId'])['reason']['code'], 'router-tool-evidence-unverified')

    def test_frozen_routing_mode_defines_the_permission_without_repeated_storage(self):
        import hashlib
        board = self.board()
        self.seed(board)
        requested, claim = self.request_route(board, 'frozen-mode')
        row, document = self.frozen(board, requested['decisionId'])
        with board.store.db.read() as db:
            original = db.execute('SELECT input_json,input_sha256 FROM decision_requests WHERE decision_id=?', (requested['decisionId'],)).fetchone()
            self.assertEqual(hashlib.sha256(original['input_json'].encode()).hexdigest(), original['input_sha256'])
            self.assertEqual(set(db.execute('SELECT * FROM router_claims WHERE attempt_id=?', (claim['attempt']['attemptId'],)).fetchone().keys()), {'attempt_id', 'task_id', 'generation'})
        self.assertEqual(document['routingMode'], 'review')
        package = self.package_for(claim, requested, settled('execute', 'read-command'))
        self.assertIsNone(self.problem(board, requested['decisionId'], {'nativeIdentity': ROOT, 'toolEvidence': package}))

    def setUp(self):
        super().setUp()
        self.catalog_fixture()

    def configure(self, board, **settings):
        return board.console_call("user_policy_publish", {
            "commandId": "publish-" + str(uuid.uuid4()), "configuration": settings})

    def request_route(self, board, request_id, worker="router"):
        result = board.call("selection_request", {"requestId": request_id, "task": "choose"})
        pass  # Admission is owned by the service.
        claim = admit_execution(board.client(), NONCE, task_id=result["runId"])["claim"]
        return result, claim

    def frozen(self, board, decision_id):
        with board.store.db.read() as connection:
            row = board.store.decisions._row(connection, decision_id)
            return row, json.loads(row["input_json"])

    def problem(self, board, decision_id, output):
        row, document = self.frozen(board, decision_id)
        return board.store.decisions._tool_evidence_problem(row, document, output)

    def package_for(self, claim, result, events, roots=None, stream_complete=True):
        evidence = tool_evidence.ToolEventEvidence({
            "harness": "dsh", "taskId": result["runId"],
            "attemptId": claim["attempt"]["attemptId"], "generation": claim["attempt"]["generation"],
        })
        for event in events:
            evidence.observe(event)
        return evidence.finish(list(roots if roots is not None else [ROOT]), stream_complete)

    # -- claim-side freezing -------------------------------------------------
    def test_a_pre_claim_document_cannot_bind_evidence(self):
        board = self.board()
        self.seed(board)
        result = board.call("selection_request", {"requestId": "pre-claim", "task": "choose"})
        row, document = self.frozen(board, result["decisionId"])
        self.assertIsNone(row["decision_attempt_id"])
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
        for replace in ({"harness": "zcode"}, {"taskId": "task-foreign"}, {"attemptId": "attempt-foreign"},
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

    def test_read_level_allows_execution_and_network_and_forbids_modification(self):
        board = self.board()
        self.seed(board)
        result, claim = self.request_route(board, "shared-read")
        for category in ("read", "search", "execute", "fetch", "edit", "other"):
            package = self.package_for(claim, result, settled(category, "call-1"))
            problem = self.problem(board, result["decisionId"], {"nativeIdentity": ROOT, "toolEvidence": package})
            self.assertEqual(problem, {"code": "router-tools-forbidden"} if category in ("edit", "other") else None)

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
        self.configure(board, routerBuddyIds=[BUDDY_ID], defaultRoutingMode="fast")
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
        "harness": "dsh", "taskId": result["runId"],
        "attemptId": row["decision_attempt_id"] or "attempt-unbound",
        "generation": row["decision_generation"] if row["decision_generation"] is not None else 0,
    }).finish([ROOT], True)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
