"""Router request freeze and claim dispatch through the real coordinator and store.

Every test runs the production store, decision coordinator and claim transaction
over a private board; no model is called and no process starts. Admission freezes
the complete actor-free packet — even with no usable Router — and each item gets
its own immutable internal task. Pre-claim preparation advances a model-前
unavailable item after recording its real preflight no-answer, queues behind
retries and capacity, and a
claim freezes the actual model service, tool policy and Router binding onto the
attempt inside one transaction. Old dispatch documents, hashes and receipts are
never rewritten by a later dispatch of the same request.
"""
from admission import admit_board, admit_execution, admit_store
import json
import unittest
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.routing import router, router_history, router_sequence
from hey_my_buddy.blackboard.store.db import canonical_json

A = "dsh:fixture:alpha:max"
B = "dsh:fixture:bravo:max"
C = "dsh:fixture:charlie:max"
T0 = "2026-01-01T00:00:00.000Z"
NONCE = "n" * 32


class RouterDispatchTestCase(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock(T0)
        self.board_ = self.board(clock=self.clock)
        self.add_buddy(A, model="alpha")
        self.add_buddy(B, model="bravo")
        self.set_settings(ids=[A, B])
        client = self.board_.client()
        pass  # Admission is owned by the service.
        self.client = client

    # -- fixtures ------------------------------------------------------------
    def add_buddy(self, buddy_id, *, model=None, available=1, enabled=1):
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO buddies(buddy_id,label,harness,provider,model,effort,"
                       "available,enabled,capabilities_json)"
                       " VALUES(?,?,?,?,?,?,?,?,?)",
                       (buddy_id, buddy_id, "dsh", "fixture", model or buddy_id, "max", available, enabled,
                        canonical_json([])))

    def set_settings(self, *, ids=None, mode=None, budget=None, interval=None):
        with self.board_.store.db.write() as db:
            merged = {**router.configuration(db)}
            if ids is not None:
                merged["routerBuddyIds"] = list(ids)
            if mode is not None:
                merged["defaultRoutingMode"] = mode
            if budget is not None:
                merged["routingBudget"] = budget
            if interval is not None:
                merged["routerRetryIntervalSeconds"] = interval
            router._write_settings(db, merged)

    def request(self, *, request_id="pick-1", task="fix the parser"):
        return self.board_.call("selection_request", {"requestId": request_id, "task": task})

    def decision_row(self, decision_id):
        return self.board_.call("selection_get", {"decisionId": decision_id})["decision"]

    def claim(self, suffix="1", *, task_id=None):
        return admit_execution(self.client, NONCE + suffix, task_id=task_id)

    def snapshot_of(self, decision_id):
        with self.board_.store.db.read() as db:
            return router_sequence.request_snapshot(db, decision_id)

    def dispatch_of(self, task_id):
        with self.board_.store.db.read() as db:
            return router_sequence.dispatch(db, task_id)

    def router_events(self, *kinds):
        markers = ",".join(f"'{kind}'" for kind in kinds)
        with self.board_.store.db.read() as db:
            rows = db.execute(f"SELECT kind,payload_json FROM events WHERE kind IN ({markers})").fetchall()
        return [(row[0], json.loads(row[1])) for row in rows]

    def tasks(self, *, state=None):
        with self.board_.store.db.read() as db:
            if state is None:
                return db.execute("SELECT task_id,state,adapter FROM tasks WHERE owner='decision'").fetchall()
            return db.execute("SELECT task_id,state,adapter FROM tasks WHERE owner='decision' AND state=?",
                              (state,)).fetchall()

    def attempts(self):
        with self.board_.store.db.read() as db:
            return db.execute("SELECT * FROM attempts").fetchall()

class AdmissionTests(RouterDispatchTestCase):
    def test_duplicate_request_replays_without_a_second_task(self):
        first = self.request()
        self.assertEqual(first["status"], "queued")
        self.assertTrue(first["runId"])
        before = len(self.tasks())
        duplicate = self.request()
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["decisionId"], first["decisionId"])
        self.assertEqual(duplicate["runId"], first["runId"])
        self.assertEqual(len(self.tasks()), before)
        # The claim receipt replays the same attempt rather than minting a second one.
        committed = self.claim("dup")
        self.assertIsNotNone(committed["claim"])
        replayed = admit_board(self.board_, {'workerId': 'w-route', 'claimRequestId': 'claim-dup', 'nonce': NONCE + 'dup'})
        self.assertIsNone(replayed["claim"])
        self.assertEqual(len(self.attempts()), 1)

    def test_no_usable_router_still_freezes_the_complete_packet(self):
        self.set_settings(ids=[])
        result = self.request()
        self.assertEqual(result["status"], "needs-host")
        snapshot = self.snapshot_of(result["decisionId"])
        self.assertIsNotNone(snapshot)
        base = snapshot["baseInput"]
        # The Host keeps the full frozen packet, never only a reason.
        self.assertEqual([item["buddyId"] for item in base["buddies"]], [A, B])
        self.assertIn("policyFacts", base)
        self.assertIn("outputSchema", base)
        self.assertNotIn("buddy", base)
        self.assertNotIn("routerBuddyId", base)
        self.assertEqual(snapshot["facts"]["routerBuddyIds"], [])
        self.assertEqual(self.tasks(), [])
        # An all-unavailable list freezes the same complete packet: the Router
        # buddies are separate disabled buddies, so the candidate set survives.
        self.add_buddy("dsh:fixture:router-one:max", model="router-one", enabled=0)
        self.add_buddy("dsh:fixture:router-two:max", model="router-two", enabled=0)
        self.set_settings(ids=["dsh:fixture:router-one:max", "dsh:fixture:router-two:max"])
        unavailable = self.request(request_id="pick-2")
        self.assertEqual(unavailable["status"], "needs-host")
        second = self.snapshot_of(unavailable["decisionId"])
        self.assertEqual([item["buddyId"] for item in second["baseInput"]["buddies"]], [A, B])
        self.assertEqual([entry["code"] for entry in second["inspections"]],
                         ["router-disabled", "router-disabled"])
        self.assertEqual(self.tasks(), [])

    def test_sole_candidate_takes_the_direct_path_without_a_router_task(self):
        with self.board_.store.db.write() as db:
            db.execute("DELETE FROM buddies WHERE buddy_id=?", (B,))
        result = self.request()
        self.assertEqual(result["status"], "completed")
        decision = self.decision_row(result["decisionId"])
        self.assertEqual(decision["buddyId"], A)
        self.assertFalse(decision["routerCalled"])
        self.assertIsNone(self.snapshot_of(result["decisionId"]))
        self.assertEqual(self.tasks(), [])
        self.assertEqual(self.attempts(), [])
        self.assertEqual(self.router_events("router.no_answer", "router.claimed"), [])

    def test_admission_records_real_preflight_failures_but_never_skip_windows(self):
        # A's earlier no-answer (another request) opens its skip window; B is fine.
        # A third candidate keeps the Router path alive even when A drops out.
        self.add_buddy(C, model="charlie")
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, buddy_id=A, plane="routing", request_id="route-earlier",
                                          task_id=None, attempt_id=None, outcome="no_answer",
                                          code="timeout", phase="runtime", facts={"routerIndex": 0}, now=T0)
        skipped = self.request(request_id="pick-skip")
        self.assertEqual(skipped["status"], "queued")
        snapshot = self.snapshot_of(skipped["decisionId"])
        self.assertEqual(snapshot["inspections"][0]["code"], "router-skip-window")
        self.assertEqual(self.dispatch_of(skipped["runId"])["buddyId"], B)
        events = self.router_events("router.no_answer")
        # The skip-window observation added no fact for A and extended nothing.
        self.assertEqual([payload["requestId"] for _, payload in events], ["route-earlier"])
        # Once the window expires, a genuinely ineligible first item records its
        # real preflight no-answer.
        self.clock.advance(601)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        ineligible = self.request(request_id="pick-disabled")
        self.assertEqual(ineligible["status"], "queued")
        self.assertEqual(self.dispatch_of(ineligible["runId"])["buddyId"], B)
        events = self.router_events("router.no_answer")
        recorded = [payload for _, payload in events if payload["requestId"] == "pick-disabled"]
        self.assertEqual(recorded, [{"plane": "routing", "requestId": "pick-disabled", "routerIndex": 0,
                                     "buddyId": A, "phase": "preflight", "taskId": None, "attemptId": None,
                                     "outcome": "no_answer", "code": "router-disabled"}])
        # Replaying the same request adds no second fact.
        self.request(request_id="pick-disabled")
        self.assertEqual(len(self.router_events("router.no_answer")), 2)

    def test_frozen_request_keeps_its_list_against_later_settings_changes(self):
        result = self.request()
        self.set_settings(ids=[B, A], interval=30)
        snapshot = self.snapshot_of(result["decisionId"])
        self.assertEqual(snapshot["facts"]["routerBuddyIds"], [A, B])
        self.assertEqual(snapshot["facts"]["routerRetryIntervalSeconds"], 600)


class PreclaimAdvanceTests(RouterDispatchTestCase):
    def test_model_preflight_loss_advances_once_and_keeps_the_old_dispatch(self):
        result = self.request()
        first_task = result["runId"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        advanced = self.claim("adv")
        self.assertIsNone(advanced["claim"])
        self.assertEqual(advanced["reason"], "decision-dispatch-advanced")
        # The old queued task closed honestly without any fabricated attempt.
        self.assertEqual([row["state"] for row in self.tasks() if row["task_id"] == first_task], ["cancelled"])
        self.assertEqual(self.attempts(), [])
        events = self.router_events("router.no_answer")
        self.assertEqual([payload["requestId"] for _, payload in events], ["pick-1"])
        self.assertEqual(events[0][1]["phase"], "preflight")
        self.assertEqual(events[0][1]["buddyId"], A)
        # The next item has its own internal task and immutable dispatch.
        queued = self.tasks(state="queued")
        self.assertEqual(len(queued), 1)
        second_task = queued[0]["task_id"]
        second = self.dispatch_of(second_task)
        self.assertEqual(second["buddyId"], B)
        self.assertEqual(second["routerIndex"], 1)
        first = self.dispatch_of(first_task)
        self.assertEqual(first["buddyId"], A)
        self.assertNotEqual(first["inputSha256"], second["inputSha256"])
        # Only the actor graft differs between the two dispatch documents.
        actor_keys = ("buddy", "routerBuddyId", "routerBuddy", "routerIndex")

        def without_actor(document):
            return {key: value for key, value in document.items() if key not in actor_keys}

        self.assertEqual(without_actor(first["document"]), without_actor(second["document"]))
        # The request points at the current dispatch input and keeps its original ask.
        with self.board_.store.db.read() as db:
            row = db.execute("SELECT task_id,input_json,input_sha256,requested_json FROM decision_requests"
                             " WHERE decision_id=?", (result["decisionId"],)).fetchone()
        self.assertEqual(row["task_id"], second_task)
        self.assertEqual(row["input_sha256"], second["inputSha256"])
        self.assertEqual(json.loads(row["requested_json"])["task"], "fix the parser")
        # The same duplicate dispatch is not queued twice.
        with self.board_.store.db.read() as db:
            decision_row = self.board_.store.decisions._row(db, result["decisionId"])
            resolution = router.current_router(db, frozen=self.snapshot_of(result["decisionId"])["facts"],
                                               after_index=0, now=self.clock.value)
        with self.board_.store.db.write() as db:
            again = self.board_.store.decisions._queue_router_dispatch(db, decision_row, resolution=resolution,
                                                                       now=self.clock.value)
        self.assertEqual(again, second_task)
        self.assertEqual(len(self.tasks(state="queued")), 1)

    def test_changed_settings_keep_the_frozen_router_dispatch(self):
        result = self.request()
        frozen = self.dispatch_of(result["runId"])
        self.set_settings(ids=[B, A])
        claimed = self.claim("settings-edited")
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(claimed["claim"]["decisionInput"], frozen["document"])
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.router_events("router.no_answer"), [])

    def test_router_identity_edit_keeps_the_frozen_dispatch(self):
        result = self.request()
        frozen = self.dispatch_of(result["runId"])
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET model='replaced' WHERE buddy_id=?", (A,))
        claimed = self.claim("identity-edited")
        self.assertIsNotNone(claimed["claim"])
        self.assertEqual(claimed["claim"]["decisionInput"], frozen["document"])
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.router_events("router.no_answer"), [])



    def test_model_service_capacity_only_queues_without_a_bypass_task(self):
        result = self.request()
        # Two active attempts on an unrelated model service fill the machine-wide
        # ceiling (max_concurrent=2): the dispatch only queues behind it.
        with self.board_.store.db.write() as db:
            for index in (1, 2):
                db.execute(
                    "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                    "fingerprint_version,adapter,cwd,expected_seconds,state,revision,created_at,updated_at)"
                    " VALUES(?,?,?,?,?,'digest',1,'dsh','/work',60,'running',1,?,?)",
                    (f"busy-{index}", f"busy-{index}", "host", "{}", "{}", T0, T0))
                db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,model_harness,model_provider,model_model,started_at,created_at,updated_at) VALUES(?,?,1,'nonce','executing','dsh','dsh','other',?,?,?,?)", (f'att-busy-{index}', f'busy-{index}', f'model-{index}', T0, T0, T0))
        blocked = self.claim("cap")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "capacity")
        self.assertEqual(self.decision_row(result["decisionId"])["status"], "queued")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual([row["state"] for row in self.tasks()], ["queued"])
        self.assertEqual(self.router_events("router.no_answer"), [])

    def test_retry_in_progress_queues_behind_the_single_active_retry(self):
        result = self.request()
        # The dispatched item owes an answer and one retry execution is active
        # elsewhere: this request queues without switching or recording anything.
        with self.board_.store.db.write() as db:
            router_history.record_outcome(db, buddy_id=A, plane="routing", request_id="route-elsewhere",
                                          task_id=None, attempt_id=None, outcome="no_answer",
                                          code="timeout", phase="runtime", facts={"routerIndex": 0}, now=T0)
            db.execute(
                "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                "fingerprint_version,adapter,cwd,expected_seconds,state,revision,created_at,updated_at)"
                " VALUES('retry-1','retry','host','{}','{}','digest',1,'dsh','/work',60,'running',1,?,?)",
                (T0, T0))
            db.execute("INSERT INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,model_harness,model_provider,model_model,started_at,created_at,updated_at) VALUES('att-retry','retry-1',1,'nonce','executing','dsh','dsh','fixture','alpha',?,?,?)", (T0, T0, T0))
            self.board_.store._append_event(db, "router.claimed", task_id="retry-1", attempt_id="att-retry",
                                            payload={"buddyId": A, "plane": "routing", "requestId": "route-elsewhere",
                                                     "routerIndex": 0, "taskId": "retry-1", "attemptId": "att-retry",
                                                     "generation": 1})
        blocked = self.claim("retry")
        self.assertIsNone(blocked["claim"])
        self.assertEqual(blocked["reason"], "router-retry-in-progress")
        self.assertEqual(self.decision_row(result["decisionId"])["status"], "queued")
        self.assertEqual([row["state"] for row in self.tasks()], ["queued"])
        self.assertEqual([row["task_id"] for row in self.tasks()], [result["runId"]])
        # No attempt of this request's dispatch was minted and nothing was
        # recorded against its Router while the single retry stays in flight.
        with self.board_.store.db.read() as db:
            mine = db.execute("SELECT COUNT(*) AS count FROM attempts WHERE task_id=?",
                              (result["runId"],)).fetchone()["count"]
        self.assertEqual(int(mine), 0)
        facts = [payload for kind, payload in self.router_events("router.no_answer", "router.claimed")
                 if payload.get("requestId") == "pick-1"]
        self.assertEqual(facts, [])

    def test_late_superseded_task_never_claims(self):
        result = self.request()
        first_task = result["runId"]
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        self.claim("adv")
        second_task = self.tasks(state="queued")[0]["task_id"]
        # A stray re-queued shell of the superseded dispatch is refused and closed.
        with self.board_.store.db.write() as db:
            db.execute("UPDATE tasks SET state='queued' WHERE task_id=?", (first_task,))
        late = self.claim("late", task_id=first_task)
        self.assertIsNone(late["claim"])
        self.assertEqual(late["reason"], "decision-dispatch-superseded")
        with self.board_.store.db.read() as db:
            state = db.execute("SELECT state FROM tasks WHERE task_id=?", (first_task,)).fetchone()[0]
        self.assertEqual(state, "cancelled")
        # The current dispatch is untouched and still claimable.
        committed = self.claim("current", task_id=second_task)
        self.assertIsNotNone(committed["claim"])
        self.assertEqual(committed["claim"]["decisionInput"]["routerBuddyId"], B)


class ClaimTests(RouterDispatchTestCase):
    def test_claim_freezes_the_actual_model_service_policy_and_binding(self):
        result = self.request()
        committed = self.claim("ok")
        self.assertIsNotNone(committed["claim"])
        attempt = committed["claim"]["attempt"]
        document = committed["claim"]["decisionInput"]
        self.assertEqual(document["routerBuddyId"], A)
        self.assertEqual(document["routerIndex"], 0)
        self.assertEqual(document["buddy"], {"harness": "dsh", "provider": "fixture",
                                               "model": "alpha", "effort": "max"})
        # The claim froze the tool policy for this attempt.
        with self.board_.store.db.read() as db:
            binding = router_sequence.claim(db, attempt["attemptId"])
        self.assertEqual(binding, {"taskId": result["runId"], "attemptId": attempt["attemptId"],
                                   "generation": attempt["generation"], "decisionId": result["decisionId"],
                                   "routerIndex": 0, "buddyId": A})
        claimed = [payload for kind, payload in self.router_events("router.claimed")]
        self.assertEqual(claimed, [{"buddyId": A, "plane": "routing", "requestId": "pick-1",
                                    "routerIndex": 0, "taskId": result["runId"],
                                    "attemptId": attempt["attemptId"], "generation": attempt["generation"]}])
        # The decision is running and its current input is the dispatch document.
        self.assertEqual(self.decision_row(result["decisionId"])["status"], "running")
        with self.board_.store.db.read() as db:
            row = db.execute("SELECT input_sha256 FROM decision_requests WHERE decision_id=?",
                             (result["decisionId"],)).fetchone()
        self.assertEqual(row["input_sha256"], self.dispatch_of(result["runId"])["inputSha256"])

    def test_selector_model_service_reads_the_current_dispatch(self):
        result = self.request()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
            decision_row = self.board_.store.decisions._row(db, result["decisionId"])
            resolution = router.current_router(db, frozen=self.snapshot_of(result["decisionId"])["facts"],
                                               after_index=0, now=self.clock.value)
            self.board_.store.decisions._queue_router_dispatch(db, decision_row, resolution=resolution,
                                                               now=self.clock.value)
        with self.board_.store.db.read() as db:
            spec = json.loads(db.execute("SELECT spec_json FROM tasks t JOIN decision_requests r"
                                         " ON r.task_id=t.task_id WHERE r.decision_id=?",
                                         (result["decisionId"],)).fetchone()[0])
            model_service = self.board_.store.decisions.selector_model_service(db, spec)
        self.assertEqual(model_service, ("dsh", "fixture", "bravo"))


class WorkflowWiringTests(RouterDispatchTestCase):
    def route_micro_task(self, run_id="run-1", *, required_capabilities=()):
        """One micro task admitted through the coordinator's workflow entry."""
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                "fingerprint_version,adapter,cwd,expected_seconds,state,queue_reason,revision,created_at,updated_at)"
                " VALUES(?,?,'host','{}','{}','digest',1,'unresolved','/work',60,'queued',"
                "'awaiting-model-selection',1,?,?)",
                (run_id, run_id, T0, T0))
            db.execute(
                "INSERT INTO workflow_runs(run_id,host_id,control_verifier,micro_task_json,micro_task_fingerprint,"
                " request_fingerprint,execution_workspace_json,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?)",
                (run_id, "host-1", "verifier", canonical_json({"task": "do the thing"}),
                 "micro-task-digest", "request-digest", canonical_json({"kind": "existing"}), "executing", T0, T0))
            response = self.board_.store.decisions.route_workflow(
                db, run_id=run_id, sequence=1, spec={"task": "do the thing", "requiredCapabilities": list(required_capabilities)}, manifest=None)
        return response

    def attach_pending_link(self, decision_id, run_id="run-1"):
        """The Micro task's owning transaction inserts its pending route link."""
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
                " VALUES(?,?,1,'pending',?,?)",
                (decision_id, run_id, T0, T0))
            db.execute("UPDATE workflow_runs SET current_routing_id=? WHERE run_id=?", (decision_id, run_id))

    def test_first_dispatch_needs_no_link_and_the_pending_route_follows_the_pointer(self):
        response = self.route_micro_task()
        decision_id = response["decisionId"]
        first_task = response["runId"]
        # The first dispatch queued before the route link existed; nothing was
        # fabricated for it and the Micro task keeps waiting for routing.
        with self.board_.store.db.read() as db:
            links = db.execute("SELECT * FROM workflow_routes WHERE decision_id=?", (decision_id,)).fetchall()
            micro_task = db.execute("SELECT state,adapter FROM tasks WHERE task_id='run-1'").fetchone()
        self.assertEqual(links, [])
        self.assertEqual((micro_task["state"], micro_task["adapter"]), ("queued", "unresolved"))
        self.assertIsNotNone(self.dispatch_of(first_task))
        self.attach_pending_link(decision_id)
        # An advance re-points the internal task and records it on the pending
        # route only; the parent business task still waits for routing.
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        advanced = self.claim("wf", task_id=first_task)
        self.assertIsNone(advanced["claim"])
        self.assertEqual(advanced["reason"], "decision-dispatch-advanced")
        with self.board_.store.db.read() as db:
            link = db.execute("SELECT state,updated_at FROM workflow_routes WHERE decision_id=?",
                              (decision_id,)).fetchone()
            current = db.execute("SELECT task_id FROM decision_requests WHERE decision_id=?",
                                 (decision_id,)).fetchone()["task_id"]
            reason = db.execute("SELECT queue_reason FROM tasks WHERE task_id='run-1'").fetchone()["queue_reason"]
        self.assertEqual(link["state"], "pending")
        self.assertNotEqual(current, first_task)
        self.assertEqual(reason, "awaiting-model-selection")
        committed = self.claim("wf2", task_id=current)
        self.assertIsNotNone(committed["claim"])
        self.assertEqual(committed["claim"]["decisionInput"]["routerBuddyId"], B)

    def test_resolved_and_fenced_routes_are_not_touched_by_a_dispatch(self):
        response = self.route_micro_task()
        decision_id = response["decisionId"]
        self.attach_pending_link(decision_id)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_routes SET state='fenced',updated_at=? WHERE decision_id=?",
                       (T0, decision_id))
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
            row = self.board_.store.decisions._row(db, decision_id)
            resolution = router.current_router(db, frozen=self.snapshot_of(decision_id)["facts"],
                                               after_index=0, now=self.clock.value)
            self.board_.store.decisions._queue_router_dispatch(db, row, resolution=resolution,
                                                               now=self.clock.value)
        with self.board_.store.db.read() as db:
            link = db.execute("SELECT state,updated_at FROM workflow_routes WHERE decision_id=?",
                              (decision_id,)).fetchone()
        self.assertEqual(link["state"], "fenced")
        self.assertEqual(link["updated_at"], T0)


class HostReviewRegressionTests(RouterDispatchTestCase):
    def test_new_catalog_reason_does_not_rewrite_sent_dispatch_bytes(self):
        frozen = self.request()
        with self.board_.store.db.read() as db:
            before = db.execute('SELECT document_json,input_sha256 FROM router_dispatches WHERE task_id=?',
                                (frozen['runId'],)).fetchone()
            original = tuple(before)
        with self.board_.store.db.write() as db:
            db.execute('UPDATE buddies SET available=0,unavailable_code=?,unavailable_params_json=?,unavailable_content=? WHERE buddy_id=?',
                       ('catalog.native-unavailable', '{}', '  native text\n', B))
            current_input = self.board_.store.decisions._buddy_input(
                db.execute('SELECT * FROM buddies WHERE buddy_id=?', (B,)).fetchone())
        self.assertEqual(current_input['unavailableReason'],
                         {'code': 'catalog.native-unavailable', 'params': {}})
        self.assertEqual(current_input['unavailableContent'], '  native text\n')
        with self.board_.store.db.read() as db:
            after = db.execute('SELECT document_json,input_sha256 FROM router_dispatches WHERE task_id=?',
                               (frozen['runId'],)).fetchone()
        self.assertEqual(tuple(after), original)

    def catalog_read(self, *, changed=False):
        return self.board_.evaluation.record_catalog({
            'source': 'routing-reread-fixture',
            'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
            'providers': [{'harness': 'dsh', 'provider': 'fixture', 'models': [
                {'id': model, 'efforts': ['max'], 'description': 'changed' if changed else ''}
                for model in ('alpha', 'bravo')]}]})

    def test_noop_catalog_read_preserves_frozen_dispatch(self):
        self.catalog_read()
        frozen = self.request()
        original = self.dispatch_of(frozen['runId'])
        self.clock.advance(21601)
        self.catalog_read()
        committed = self.claim('catalog-noop')
        self.assertIsNotNone(committed['claim'])
        self.assertEqual(committed['claim']['decisionInput'], original['document'])

    def test_changed_catalog_description_does_not_stop_frozen_dispatch(self):
        self.catalog_read()
        frozen = self.request()
        original = self.dispatch_of(frozen['runId'])
        self.catalog_read(changed=True)
        committed = self.claim('catalog-changed')
        self.assertIsNotNone(committed['claim'])
        self.assertEqual(committed['claim']['decisionInput'], original['document'])
        self.assertEqual(self.router_events('router.no_answer'), [])

    def test_actor_graft_is_included_in_admission_byte_ceiling(self):
        first = self.request(request_id="pick-1")
        bound = len(canonical_json(self.snapshot_of(first["decisionId"])["baseInput"]).encode("utf-8"))
        with patch("hey_my_buddy.blackboard.routing.decision.MAX_DECISION_INPUT_BYTES", bound):
            refused = self.request(request_id="pick-2")
        self.assertEqual(refused["status"], "needs-host")
        self.assertIsNone(refused["runId"])
        self.assertEqual(len(self.tasks()), 1)
        self.assertIsNotNone(self.snapshot_of(refused["decisionId"]))
        oversized = self.decision_row(refused["decisionId"])["reason"]
        self.assertEqual(oversized["code"], "router-input-too-large")
        self.assertEqual(oversized["params"]["limit"], bound)
        self.assertGreater(oversized["params"]["bytes"], bound)

    def test_next_actor_byte_ceiling_keeps_the_original_task_and_input(self):
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET model=? WHERE buddy_id=?", ("b" * 40, B))
        first = self.request()
        original = self.dispatch_of(first["runId"])
        bound = len(canonical_json(original["document"]).encode("utf-8"))
        with self.board_.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id=?", (A,))
        with patch("hey_my_buddy.blackboard.routing.decision.MAX_DECISION_INPUT_BYTES", bound):
            response = self.claim("oversize")
        self.assertIsNone(response["claim"])
        self.assertEqual(self.decision_row(first["decisionId"])["status"], "needs-host")
        oversized = self.decision_row(first["decisionId"])["reason"]
        self.assertEqual(oversized["code"], "router-input-too-large")
        self.assertEqual(oversized["params"]["limit"], bound)
        self.assertGreater(oversized["params"]["bytes"], bound)
        self.assertEqual(self.dispatch_of(first["runId"]), original)
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual([event[1]["buddyId"] for event in self.router_events("router.no_answer")], [A])

    def test_unhealthy_router_records_preflight_failure_without_an_attempt(self):
        first = self.request()
        with self.board_.store.db.write() as db:
            db.execute("UPDATE harness_health SET status='unhealthy' WHERE harness='dsh'")
        response = self.claim("changed")
        self.assertIsNone(response["claim"])
        self.assertEqual(self.decision_row(first["decisionId"])["status"], "needs-host")
        self.assertEqual(len(self.tasks()), 1)
        self.assertEqual(self.attempts(), [])

    def test_request_snapshot_and_dispatch_preserve_the_settings_budget(self):
        self.set_settings(mode="review")
        with patch.dict('hey_my_buddy.protocol.capabilities.PERMISSION_LEVELS', {'dsh': {'none': 'enforced', 'read': 'enforced', 'write': 'enforced', 'full': 'enforced'}}):
            response = self.board_.call("selection_request", {"requestId": "deadline", "task": "choose"})
        with self.board_.store.db.read() as db:
            requested = json.loads(self.board_.store.decisions._row(db, response["decisionId"])["requested_json"])
        snapshot = self.snapshot_of(response["decisionId"])
        dispatch = self.dispatch_of(response["runId"])
        self.assertEqual(requested["budget"], snapshot["facts"]["budget"])
        self.assertEqual(dispatch["document"]["budget"], requested["budget"])
        self.assertEqual(requested["budget"]["timeoutSeconds"], 300)


if __name__ == "__main__":
    unittest.main()
