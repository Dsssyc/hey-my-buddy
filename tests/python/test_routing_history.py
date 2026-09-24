"""Bounded read APIs for routing history and decision browsing.

Every test drives the real store, the real resource validation and the real
transactions through the in-process board harness. No adapter process is started
here: needs-host routing boundaries, one synthetic selector receipt and never-run
decisions are established directly, so both reads are exercised without a paid or
mocked model call. The reads must never admit an evaluation lease, call a model or
write state.
"""
from __future__ import annotations

import json
from unittest.mock import patch

from test_decision import DecisionTestCase, PROFILE, PROFILE_ID, SECOND_PROFILE, SECOND_PROFILE_ID
from test_workflow import CONFIGURATION, NONCE, WorkflowTestCase

from buddy.errors import BoardError

ROUTING_TASK_TEXT = "Produce a verified implementation"


class RoutingHistoryTestCase(WorkflowTestCase):
    seed = DecisionTestCase.seed
    publish_more = DecisionTestCase.publish_more

    def setUp(self) -> None:
        super().setUp()
        self.catalog_fixture()
        self.enterContext(
            patch("buddy.decision.DecisionCoordinator._adapter_available", return_value=(True, None))
        )

    # -- driven helpers -----------------------------------------------------
    def routed(self, board, *, request_id="route-1", **constraints):
        response = board.call("workflow_submit", {
            "requestId": request_id, "hostId": "host-1", "submissionToken": "submission-secret-1",
            "task": ROUTING_TASK_TEXT, "cwd": str(self.workdir(request_id)),
            "executionWorkspace": {"kind": "existing", "access": "write"}, **constraints,
        })
        if response.get("control"):
            self.controls[response["runId"]] = response["control"]
        return response

    def router_claim(self, board, view, *, claim_id="router-1"):
        board.call("worker_register", {"workerId": "router", "adapter": "decision", "capabilities": ["decision"]})
        return self.claim(board, "router", run_id=view["routing"]["taskId"], claim_request_id=claim_id)

    def select(self, board, claim, *, profile_id=PROFILE_ID, shutdown=True, status="ok"):
        owned = claim["claim"]
        return board.call("worker_result", {
            "workerId": "router", "attemptId": owned["attempt"]["attemptId"],
            "generation": owned["attempt"]["generation"], "nonce": NONCE,
            "status": status, "shutdownConfirmed": shutdown,
            "result": {"status": "ok", "operation": "select", "tableRevision": owned["decisionInput"]["tableRevision"],
                       "decision": {"profileId": profile_id, "reason": "fixture selection", "evidenceIds": []}},
        })

    def history(self, board, run_id, **params):
        return board.call("workflow_get", {"runId": run_id, "routingHistory": params})["routingHistory"]

    def maintain(self, board, request_id: str) -> dict:
        """One historical ``kind='maintain'`` decision, seeded as read-only history.

        The blackboard no longer executes maintenance requests and there is no
        ``request_maintain``/``evaluation_maintain`` endpoint to call. The browsing
        tests therefore establish the historical record directly, exactly like the
        archived rows a real 0.6 deployment would carry: the row exists, is returned
        by the compact readers, and can never start work or a model call.
        """
        from buddy.db import utc_now

        decision_id = f"dec-{request_id}"
        now = utc_now()
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO evaluation_decisions(decision_id, status, task, profile_id, table_revision,"
                " reason, evidence_ids_json, created_at) VALUES(?,?,?,?,?,?,?,?)",
                (
                    decision_id,
                    "needs-host",
                    f"historical maintenance {request_id}",
                    None,
                    0,
                    "historical maintenance is read-only; an external Harness owns maintenance now",
                    "[]",
                    now,
                ),
            )
            connection.execute(
                "INSERT INTO decision_requests(decision_id, request_id, kind, input_fingerprint,"
                " configuration_revision, expected_revision, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (decision_id, request_id, "maintain", "historical-maintenance", 0, 0, now, now),
            )
        return {"decisionId": decision_id}

    def select_decision(self, board, request_id):
        return board.call("selection_request", {"requestId": request_id, "task": f"select for {request_id}"})

    def state_fingerprint(self, board) -> dict:
        tables = (
            "tasks", "attempts", "events", "commands", "evaluation_readers", "evaluation_writers",
            "evaluation_decisions", "decision_requests", "evaluation_revisions", "workflow_runs",
            "workflow_routes", "workflow_turns", "workflow_requests",
        )
        with board.store.db.read() as connection:
            return {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}

    def assert_code(self, code, callable_, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            callable_(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.message)
        return caught.exception


class WorkflowRoutingHistoryTests(RoutingHistoryTestCase):
    def test_history_pages_over_many_reroutes_and_isolates_one_run(self):
        board = self.board()
        submitted = self.routed(board)
        run_id = submitted["runId"]
        decision_ids = [submitted["routing"]["decisionId"]]
        view = submitted
        for index in range(1, 22):
            view = self.continue_run(board, view, command_id=f"reroute-{index}", reroute=True)
            decision_ids.append(view["routing"]["decisionId"])
        self.assertEqual(len(set(decision_ids)), 22)

        first = self.history(board, run_id, limit=3)
        self.assertEqual(first["total"], 22)
        self.assertEqual(len(first["entries"]), 3)
        self.assertEqual(
            [entry["decisionId"] for entry in first["entries"]], list(reversed(decision_ids))[:3]
        )
        self.assertIsInstance(first["nextCursor"], int)
        self.assertEqual([entry["status"] for entry in first["entries"]], ["needs-host"] * 3)
        self.assertEqual([entry["taskId"] for entry in first["entries"]], [None, None, None])
        self.assertEqual([entry["selectedProfile"] for entry in first["entries"]], [None, None, None])
        self.assertEqual([entry["ownerGeneration"] for entry in first["entries"]], [1, 1, 1])
        # Only the newest decision is the run's explicit current routing.
        self.assertEqual([entry["current"] for entry in first["entries"]], [True, False, False])
        for entry in first["entries"]:
            self.assertTrue(entry["createdAt"])
            self.assertNotIn("task", entry)
            self.assertNotIn("input", entry)
            self.assertNotIn("output", entry)
            self.assertNotIn("proposal", entry)
        self.assertNotIn(ROUTING_TASK_TEXT, json.dumps(first["entries"]))

        # The default page size is 20, so a 22-route history needs exactly two pages.
        default_page = self.history(board, run_id)
        self.assertEqual(len(default_page["entries"]), 20)
        self.assertEqual(default_page["total"], 22)
        self.assertIsInstance(default_page["nextCursor"], int)
        tail = self.history(board, run_id, before=default_page["nextCursor"])
        self.assertEqual([entry["decisionId"] for entry in tail["entries"]], list(reversed(decision_ids))[20:])
        self.assertIsNone(tail["nextCursor"])
        # A full final page is not misreported as having another page.
        exact = self.history(board, run_id, limit=22)
        self.assertEqual(len(exact["entries"]), 22)
        self.assertIsNone(exact["nextCursor"])

        collected: list[str] = []
        cursor = None
        while True:
            page = self.history(board, run_id, limit=5, **({} if cursor is None else {"before": cursor}))
            collected.extend(entry["decisionId"] for entry in page["entries"])
            cursor = page["nextCursor"]
            if cursor is None:
                break
        self.assertEqual(collected, list(reversed(decision_ids)))
        self.assertEqual(len(set(collected)), 22)

        # Another run's own route never leaks into this run's page.
        other = self.routed(board, request_id="route-isolated")
        other_history = self.history(board, other["runId"])
        self.assertEqual(other_history["total"], 1)
        self.assertEqual([entry["decisionId"] for entry in other_history["entries"]],
                         [other["routing"]["decisionId"]])
        self.assertTrue(other_history["entries"][0]["current"])
        self.assertEqual(self.history(board, run_id)["total"], 22)

        # An explicit-configuration run has no routing rows at all, and the current
        # routing view stays honest about that.
        explicit = self.submit(board, request_id="explicit-run")
        self.assertEqual(self.history(board, explicit["runId"]), {"entries": [], "nextCursor": None, "total": 0})
        explicit_view = board.call("workflow_get", {"runId": explicit["runId"], "routingHistory": {}})
        self.assertEqual(explicit_view["routing"]["status"], "explicit")
        self.assertIsNone(explicit_view["routing"]["decisionId"])

    def test_history_rejects_unknown_fields_and_invalid_values(self):
        board = self.board()
        submitted = self.routed(board)
        run_id = submitted["runId"]
        for params in (
            {"limit": 0},
            {"limit": 101},
            {"limit": True},
            {"limit": "20"},
            {"before": 0},
            {"before": -2},
            {"before": 2**63},
            {"before": "3"},
            {"offset": 0},
            {"limit": 20, "cursor": 1},
        ):
            with self.subTest(params=params):
                self.assert_code(
                    "INVALID_ARGUMENT", board.call, "workflow_get",
                    {"runId": run_id, "routingHistory": params},
                )
        self.assert_code(
            "INVALID_ARGUMENT", board.call, "workflow_get", {"runId": run_id, "routingHistory": 5}
        )
        self.assert_code(
            "INVALID_ARGUMENT", board.call, "workflow_get",
            {"runId": run_id, "routingHistory": {"limit": 20}, "unexpected": True},
        )
        # An absent or explicit-null routingHistory keeps the compact shape exactly.
        compact = board.call("workflow_get", {"runId": run_id})
        self.assertNotIn("routingHistory", compact)
        identical = board.call("workflow_get", {"runId": run_id, "routingHistory": None})
        self.assertEqual(set(identical), set(compact))
        self.assertNotIn("routingHistory", identical)
        # A cursor below every row is an honest empty page, not an error.
        empty = self.history(board, run_id, before=1)
        self.assertEqual(empty["entries"], [])
        self.assertIsNone(empty["nextCursor"])
        self.assertEqual(empty["total"], 1)

    def test_history_on_an_ungoverned_task_is_honestly_empty(self):
        board = self.board()
        plain = board.call("task_submit", {
            "requestId": "plain", "adapter": "command", "argv": ["true"],
            "task": "ordinary", "cwd": str(self.workdir("plain")),
        })
        view = board.call("workflow_get", {"runId": plain["task"]["runId"], "routingHistory": {"limit": 5}})
        self.assertFalse(view["governed"])
        self.assertEqual(view["routingHistory"], {"entries": [], "nextCursor": None, "total": 0})
        self.assertNotIn("routingHistory", board.call("workflow_get", {"runId": plain["task"]["runId"]}))

    def test_resolved_reroute_history_keeps_its_frozen_selection_after_profiles_change(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board, request_id="route-freeze")
        selector_task_id = board.call("workflow_get", {"runId": submitted["runId"]})["routing"]["taskId"]
        self.select(board, self.router_claim(board, submitted))

        entry = self.history(board, submitted["runId"], limit=1)["entries"][0]
        self.assertEqual(entry["status"], "completed")
        self.assertTrue(entry["current"])
        self.assertEqual(entry["taskId"], selector_task_id)
        self.assertEqual(entry["selectedProfile"]["profileId"], PROFILE_ID)
        self.assertEqual(entry["selectedProfile"]["model"], PROFILE["model"])
        self.assertEqual(entry["tableRevision"], 1)
        self.assertEqual(entry["configurationRevision"], 1)

        # The published table moves on: the originally selected profile is retired
        # and its identity is no longer part of the current catalog.
        self.publish_more(
            board, request_id="profiles-changed", command_id="profiles-changed",
            profiles=[SECOND_PROFILE],
            configuration={"decisionProfileId": SECOND_PROFILE_ID},
        )
        snapshot = board.call("console_snapshot", {})
        self.assertEqual(snapshot["tableRevision"], 2)
        self.assertEqual(snapshot["configuration"]["decisionProfileId"], SECOND_PROFILE_ID)
        frozen_config = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(frozen_config["executionConfiguration"]["model"], PROFILE["model"])
        self.assertEqual(frozen_config["executionConfigurationRevision"], 1)

        current = board.call("workflow_get", {"runId": submitted["runId"]})
        self.continue_run(board, current, command_id="reroute-after-change", reroute=True)
        history = self.history(board, submitted["runId"], limit=2)
        self.assertEqual(history["total"], 2)
        newest, frozen = history["entries"]
        self.assertEqual(newest["status"], "queued")
        self.assertEqual(newest["tableRevision"], 2)
        self.assertEqual(newest["configurationRevision"], 2)
        self.assertIsNone(newest["selectedProfile"])
        self.assertTrue(newest["current"])
        # The historical page is frozen: still the originally selected profile and
        # its own table/configuration revisions, not the newly published model.
        self.assertFalse(frozen["current"])
        self.assertEqual(frozen["decisionId"], entry["decisionId"])
        self.assertEqual(frozen["status"], "completed")
        self.assertEqual(frozen["selectedProfile"]["model"], PROFILE["model"])
        self.assertEqual(frozen["tableRevision"], 1)
        self.assertEqual(frozen["configurationRevision"], 1)
        # A reroute in flight is honest: no execution configuration is claimed until
        # the new decision resolves, and the compact routing view says so.
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(view["executionConfiguration"])
        self.assertEqual(view["routing"]["status"], "queued")


class DecisionBrowseTests(RoutingHistoryTestCase):
    def test_selection_list_filters_kind_before_the_limit_over_mixed_rows(self):
        board = self.board()
        self.seed(board)
        self.select_decision(board, "pick-1")
        tidy_one = self.maintain(board, "tidy-1")["decisionId"]
        self.select_decision(board, "pick-2")
        tidy_two = self.maintain(board, "tidy-2")["decisionId"]
        pick_mid = self.select_decision(board, "pick-mid")["decisionId"]
        tidy_three = self.maintain(board, "tidy-3")["decisionId"]
        newest_select = self.select_decision(board, "pick-newest")["decisionId"]

        page = board.call("selection_list", {"kind": "maintain", "limit": 2})
        self.assertEqual(page["total"], 3)
        self.assertEqual([item["decisionId"] for item in page["decisions"]], [tidy_three, tidy_two])
        self.assertIsInstance(page["nextCursor"], int)
        older = board.call("selection_list", {"kind": "maintain", "limit": 2, "before": page["nextCursor"]})
        self.assertEqual([item["decisionId"] for item in older["decisions"]], [tidy_one])
        self.assertIsNone(older["nextCursor"])
        self.assertEqual(older["total"], 3)

        # Every kind is returned newest first when no filter is given.
        everything = board.call("selection_list", {"limit": 3})
        self.assertEqual(
            [item["decisionId"] for item in everything["decisions"]],
            [newest_select, tidy_three, pick_mid],
        )
        self.assertEqual(everything["total"], 7)
        # No-run maintenance decisions are returned exactly like selection decisions.
        self.assertIsNone(next(item for item in page["decisions"] if item["decisionId"] == tidy_three)["runId"])

        # Records are the compact view only: the audit payload is never included.
        reference = board.call("selection_get", {"decisionId": tidy_three})["decision"]
        listed = next(item for item in page["decisions"] if item["decisionId"] == tidy_three)
        self.assertEqual(set(listed), set(reference))
        for item in page["decisions"]:
            self.assertFalse(
                {"input", "output", "proposal", "requested", "readerId", "writerId", "inputSha256"} & set(item)
            )

    def test_selection_list_pagination_is_stable_when_newer_decisions_arrive(self):
        board = self.board()
        self.seed(board)
        decisions = [self.maintain(board, f"tidy-{index}")["decisionId"] for index in range(1, 6)]
        first = board.call("selection_list", {"kind": "maintain", "limit": 2})
        self.assertEqual([item["decisionId"] for item in first["decisions"]], [decisions[4], decisions[3]])
        self.assertEqual(first["total"], 5)
        cursor = first["nextCursor"]

        # Newer maintenance rows arrive between page reads; the cursor never shifts.
        newer = [self.maintain(board, f"tidy-new-{index}")["decisionId"] for index in range(1, 3)]
        second = board.call("selection_list", {"kind": "maintain", "limit": 2, "before": cursor})
        third = board.call("selection_list", {"kind": "maintain", "limit": 2, "before": second["nextCursor"]})
        self.assertEqual([item["decisionId"] for item in second["decisions"]], [decisions[2], decisions[1]])
        self.assertEqual([item["decisionId"] for item in third["decisions"]], [decisions[0]])
        self.assertIsNone(third["nextCursor"])
        collected = [
            item["decisionId"]
            for item in (*first["decisions"], *second["decisions"], *third["decisions"])
        ]
        self.assertEqual(collected, list(reversed(decisions)))
        self.assertFalse(set(newer) & set(collected))

        fresh = board.call("selection_list", {"kind": "maintain", "limit": 2})
        self.assertEqual([item["decisionId"] for item in fresh["decisions"]], [newer[1], newer[0]])
        self.assertEqual(fresh["total"], 7)
        # A full final page is not misreported as having another page.
        exact = board.call("selection_list", {"kind": "maintain", "limit": 7})
        self.assertEqual(len(exact["decisions"]), 7)
        self.assertIsNone(exact["nextCursor"])

    def test_selection_list_rejects_invalid_kinds_limits_and_cursors(self):
        board = self.board()
        self.seed(board)
        self.maintain(board, "tidy-invalid")
        for params in (
            {"kind": "audit"},
            {"kind": 5},
            {"limit": 0},
            {"limit": 101},
            {"limit": True},
            {"limit": "20"},
            {"before": 0},
            {"before": -1},
            {"before": 2**63},
            {"before": "1"},
            {"offset": 1},
            {"limit": 20, "unknown": True},
        ):
            with self.subTest(params=params):
                self.assert_code("INVALID_ARGUMENT", board.call, "selection_list", params)
        # A cursor below every row is an honest empty page, not an error.
        page = board.call("selection_list", {"kind": "maintain", "before": 1})
        self.assertEqual(page["decisions"], [])
        self.assertIsNone(page["nextCursor"])
        self.assertEqual(page["total"], 1)

    def test_bounded_reads_take_no_lease_call_no_model_and_write_nothing(self):
        board = self.board()
        self.seed(board)
        self.maintain(board, "tidy-read-only")
        submitted = self.routed(board, request_id="route-read-only")
        before = self.state_fingerprint(board)
        head = board.store.head()
        with patch("buddy.adapters.decision.DecisionAdapter.start") as adapter_start, patch(
            "buddy.worker.worker.Worker.run_once"
        ) as worker_run:
            self.history(board, submitted["runId"], limit=5)
            board.call("selection_list", {"kind": "maintain", "limit": 5})
            board.call("selection_list", {})
            board.call("workflow_get", {"runId": submitted["runId"], "routingHistory": {"before": 1}})
            self.assertEqual(self.state_fingerprint(board), before)
            self.assertEqual(board.store.head(), head)
            adapter_start.assert_not_called()
            worker_run.assert_not_called()
        # A read admits no evaluation reader and starts no attempt; the seed's own
        # published writer row and command receipt are simply unchanged.
        self.assertEqual(before["evaluation_readers"], 0)
        self.assertEqual(before["attempts"], 0)
        self.assertEqual(before["workflow_routes"], 1)

    def test_named_operation_is_exposed_across_service_console_and_cli(self):
        from buddy.cli import METHODS
        from buddy.console import CONSOLE_OPERATIONS
        from buddy.service import CONTROL_OPERATIONS
        from buddy.workflow import AGENT_OPERATIONS

        self.assertIn("selection_list", CONTROL_OPERATIONS)
        self.assertIn("selection_list", CONSOLE_OPERATIONS)
        self.assertIn("selection-list", METHODS)
        # A read stays a read: no agent-scoped mutation or extra grant is added.
        self.assertNotIn("selection_list", AGENT_OPERATIONS)

        board = self.board()
        self.seed(board)
        created = self.select_decision(board, "named-operation")
        listed = board.console.command("selection_list", {"kind": "select"})
        self.assertEqual(listed["total"], 1)
        self.assertEqual(listed["decisions"][0]["decisionId"], created["decisionId"])
        self.assert_code("METHOD_NOT_FOUND", board.console.command, "selection-list", {})


class TurnRoutingBindingTests(RoutingHistoryTestCase):
    def test_turn_view_freezes_explicit_and_reconfigured_bindings(self):
        board = self.board()
        self.register(board)
        # No fixed constraints and no published profile: routing stops honestly at a
        # Host boundary, and the Host resolves it with a complete explicit
        # configuration before reconfiguring it.
        submitted = self.routed(board, request_id="explicit-bind")
        self.assertTrue(submitted["awaitingHost"])
        self.assertEqual(submitted["routing"]["status"], "needs-host")
        configured = self.continue_run(board, submitted, configuration=CONFIGURATION)
        self.assertEqual(configured["executionConfigurationRevision"], 1)
        first = self.claim(board, run_id=submitted["runId"], claim_request_id="first")
        turn = first["claim"]["turn"]
        self.assertEqual(turn["input"]["context"]["routing"],
                         {"decisionId": None, "executionConfigurationRevision": 1})
        self.assertEqual(turn["input"]["context"]["executionConfiguration"], CONFIGURATION)
        expected_first = {"decisionId": None, "executionConfigurationRevision": 1}
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["currentTurn"]["routing"], expected_first)
        self.assertEqual(view["currentTurn"]["executionConfiguration"], CONFIGURATION)
        self.assertEqual(view["turns"][0]["routing"], expected_first)
        self.assertEqual(view["turns"][0]["executionConfiguration"], CONFIGURATION)

        self.finish_turn(board, first, disposition="attention")
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        changed = self.continue_run(
            board, view, command_id="reconfigure", configuration={**CONFIGURATION, "effort": "high"}
        )
        self.assertEqual(changed["executionConfigurationRevision"], 2)
        second = self.claim(board, claim_request_id="second")
        self.assertEqual(second["claim"]["turn"]["input"]["context"]["routing"],
                         {"decisionId": None, "executionConfigurationRevision": 2})
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        expected_second = {"decisionId": None, "executionConfigurationRevision": 2}
        self.assertEqual(view["currentTurn"]["routing"], expected_second)
        self.assertEqual(view["currentTurn"]["executionConfiguration"]["effort"], "high")
        # The newest-first turn index keeps every turn's own frozen binding/revision.
        self.assertEqual([item["routing"] for item in view["turns"]], [expected_second, expected_first])
        self.assertEqual([item["executionConfiguration"]["effort"] for item in view["turns"]], ["high", "off"])

    def test_routing_turn_binding_and_prior_turn_without_binding_stays_null(self):
        board = self.board()
        self.seed(board)
        submitted = self.routed(board, request_id="route-bind")
        self.select(board, self.router_claim(board, submitted))
        current = board.call("workflow_get", {"runId": submitted["runId"]})
        decision_id = current["routing"]["decisionId"]
        self.assertEqual(current["routing"]["status"], "completed")
        self.register(board)
        claimed = self.claim(board, run_id=submitted["runId"], claim_request_id="first-turn")
        turn = claimed["claim"]["turn"]
        expected = {"decisionId": decision_id, "executionConfigurationRevision": 1}
        self.assertEqual(turn["input"]["context"]["routing"], expected)
        view = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertEqual(view["currentTurn"]["routing"], expected)
        self.assertEqual(view["turns"][0]["routing"], expected)
        self.assertEqual(view["currentTurn"]["executionConfiguration"], view["executionConfiguration"])
        self.assertEqual(view["routing"]["decisionId"], decision_id)

        # An older turn input that predates the binding must stay null even though
        # this run currently has a routing decision: it is never inferred from the
        # current run, timestamps or a matching model name.
        with board.store.db.write() as connection:
            row = connection.execute(
                "SELECT * FROM workflow_turns WHERE run_id=? AND turn_index=1", (submitted["runId"],)
            ).fetchone()
            document = json.loads(row["input_json"])
            self.assertIn("routing", document["context"])
            document["context"].pop("routing")
            connection.execute(
                "UPDATE workflow_turns SET input_json=? WHERE turn_id=?", (json.dumps(document), row["turn_id"])
            )
        legacy = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(legacy["currentTurn"]["routing"])
        self.assertIsNone(legacy["turns"][0]["routing"])
        self.assertEqual(legacy["currentTurn"]["executionConfiguration"], view["executionConfiguration"])
        # The current routing view and the frozen turn configuration are unaffected.
        self.assertEqual(legacy["routing"]["decisionId"], decision_id)
        self.assertEqual(legacy["routing"]["status"], "completed")

        # A partial binding does not prove an explicit Host configuration. Missing
        # decisionId and a recorded null decisionId are different evidence.
        document["context"]["routing"] = {"executionConfigurationRevision": 1}
        with board.store.db.write() as connection:
            connection.execute("UPDATE workflow_turns SET input_json=? WHERE turn_id=?",
                               (json.dumps(document), row["turn_id"]))
        partial = board.call("workflow_get", {"runId": submitted["runId"]})
        self.assertIsNone(partial["currentTurn"]["routing"])


if __name__ == "__main__":  # pragma: no cover - unittest discovery is the entrypoint
    import unittest

    unittest.main()
