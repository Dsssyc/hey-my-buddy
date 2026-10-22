"""The immutable Router request/dispatch/claim facts in owned tables, without models.

Everything runs on one private board. The sequence layer freezes one request
snapshot with its complete actor-free base input, records one immutable dispatch
per internal task and binds each claim attempt to its dispatch together with the
``router.claimed`` fact. Returned documents are fresh copies; relational keys
prevent duplicate writes; nothing rewrites an older document, hash or
receipt; and a rolled-back transaction leaves neither row nor event behind.
"""
from copy import deepcopy
import json
import sqlite3
import unittest

from support import BoardTestCase
from hey_my_buddy.blackboard.routing import router_history, router_sequence
from hey_my_buddy.blackboard.store.db import Database, canonical_json, sha256_text
from hey_my_buddy.errors import BoardError

T0 = "2026-01-01T00:00:00.000Z"
T1 = "2026-01-01T00:01:00.000Z"
FACTS = {
    "routerBuddyIds": ["dsh:fixture:alpha:max", "dsh:fixture:bravo:max"],
    "routerIdentities": [
        {"harness": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"},
        {"harness": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"},
    ],
    "routingMode": "fast",
    "routingBudget": "standard",
    "routerRetryIntervalSeconds": 600,
    "budget": {"timeoutSeconds": 60},
}
BASE_INPUT = {
    "operation": "select",
    "requestId": "pick-1",
    "task": "fix the parser",
    "buddies": [{"buddyId": "dsh:fixture:alpha:max"}, {"buddyId": "dsh:fixture:bravo:max"}],
    "preferences": [],
    "annotations": [],
    "policyFacts": {},
    "outputSchema": {"type": "object"},
    "budget": {"timeoutSeconds": 60},
    "routingMode": "fast",
}
ALPHA = {"buddyId": "dsh:fixture:alpha:max", "harness": "dsh", "provider": "fixture",
         "model": "alpha", "effort": "max"}
BRAVO = {"buddyId": "dsh:fixture:bravo:max", "harness": "dsh", "provider": "fixture",
         "model": "bravo", "effort": "max"}
INSPECTIONS = [{"index": 0, "buddyId": ALPHA["buddyId"], "eligible": True, "code": None,
                "selected": True}]


def snapshot_dict() -> dict:
    return {"facts": dict(FACTS), "baseInput": dict(BASE_INPUT), "inspections": [dict(entry) for entry in INSPECTIONS]}


def alpha_document() -> dict:
    identity = {key: ALPHA[key] for key in ("harness", "provider", "model", "effort")}
    return {**BASE_INPUT, "buddy": identity, "routerBuddyId": ALPHA["buddyId"],
            "routerBuddy": identity, "routerIndex": 0}


def bravo_document() -> dict:
    identity = {key: BRAVO[key] for key in ("harness", "provider", "model", "effort")}
    return {**BASE_INPUT, "buddy": identity, "routerBuddyId": BRAVO["buddyId"],
            "routerBuddy": identity, "routerIndex": 1}


class RouterSequenceTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.board_ = self.board()
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,task,"
                "created_at) VALUES('dec-1','queued','task',?)", (T0,))
            db.execute(
                "INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,"
                "requested_json,updated_at)"
                " VALUES('dec-1','pick-1','digest','{}',?)", (T0,))
            for task_id in ("task-alpha", "task-bravo"):
                db.execute(
                    "INSERT INTO tasks(task_id,request_id,owner,spec_json,input_fingerprint,"
                    "fingerprint_version,adapter,cwd,router_budget_seconds,state,revision,created_at,updated_at)"
                    " VALUES(?,?,'decision','{}','digest',1,'decision','/work',60,'queued',1,?,?)",
                    (task_id, f"decision:{task_id}", T0, T0))

    def stored(self, table, key, value):
        with self.board_.store.db.read() as db:
            row = db.execute(f"SELECT * FROM {table} WHERE {key}=?", (value,)).fetchone()
            return canonical_json(dict(row)) if row is not None else None

    def seed_attempt(self, db, attempt_id, task_id, generation):
        if isinstance(generation, int) and not isinstance(generation, bool) and generation >= 1:
            db.execute("INSERT OR IGNORE INTO attempts(attempt_id,task_id,generation,nonce_verifier,execution_state,adapter,shutdown_confirmed,created_at,updated_at) VALUES(?,?,?,'nonce','finished','decision',1,?,?)", (attempt_id, task_id, generation, T0, T0))

    def events(self, kind: str = "router.claimed") -> list[dict]:
        with self.board_.store.db.read() as db:
            rows = db.execute("SELECT payload_json FROM events WHERE kind=?", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]


class FreezeRequestTests(RouterSequenceTestCase):
    def test_complete_snapshot_body_survives_reopen_and_read_copy_mutation(self):
        snapshot = {**snapshot_dict(),
                    "additionalHostContext": {"evidence": [{"kind": "private", "text": "保留正文"}]}}
        expected = {**deepcopy(snapshot), "frozenAt": T0}
        with self.board_.store.db.write() as db:
            first = router_sequence.freeze_request(db, "dec-1", snapshot=snapshot, now=T0)
        self.assertEqual(first, expected)
        original = self.stored("router_requests", "decision_id", "dec-1")
        snapshot["baseInput"]["buddies"].append({"injected": True})
        first["additionalHostContext"]["evidence"][0]["text"] = "returned copy mutation"
        reopened = Database(self.board_.store.db.directory)
        self.addCleanup(reopened.close)
        reopened.initialize()
        with reopened.read() as db:
            persisted = router_sequence.request_snapshot(db, "dec-1")
            self.assertEqual(persisted, expected)
            persisted["baseInput"]["buddies"].append({"injected": True})
            self.assertEqual(router_sequence.request_snapshot(db, "dec-1"), expected)
            row = db.execute("SELECT snapshot_json,frozen_at FROM router_requests WHERE decision_id='dec-1'").fetchone()
            self.assertEqual(json.loads(row["snapshot_json"]), {key: value for key, value in expected.items() if key != "frozenAt"})
            self.assertEqual(row["frozen_at"], T0)
        self.assertEqual(self.stored("router_requests", "decision_id", "dec-1"), original)

    def test_snapshot_reads_return_fresh_copies(self):
        with self.board_.store.db.write() as db:
            router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
        with self.board_.store.db.read() as db:
            first = router_sequence.request_snapshot(db, "dec-1")
            first["baseInput"]["task"] = "mutated"
            first["facts"]["routingMode"] = "review"
            first["inspections"].append({"injected": True})
            second = router_sequence.request_snapshot(db, "dec-1")
        self.assertEqual(second["baseInput"]["task"], "fix the parser")
        self.assertEqual(second["facts"]["routingMode"], "fast")
        self.assertEqual(len(second["inspections"]), 1)
        stored = self.stored("router_requests", "decision_id", "dec-1")
        self.assertIn("fix the parser", stored)
        self.assertNotIn("mutated", stored)

    def test_unknown_decision_reads_none(self):
        with self.board_.store.db.read() as db:
            self.assertIsNone(router_sequence.request_snapshot(db, "dec-missing"))
            self.assertIsNone(router_sequence.dispatch(db, "task-missing"))


class ReserveDispatchTests(RouterSequenceTestCase):
    def reserved(self, task_id: str, *, buddy=ALPHA, document: dict | None = None, router_index: int = 0,
                 now: str = T0):
        with self.board_.store.db.write() as db:
            if router_sequence.request_snapshot(db, "dec-1") is None:
                router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
            return router_sequence.reserve_dispatch(
                db, decision_id="dec-1", task_id=task_id, router_index=router_index,
                buddy=buddy, document=document or alpha_document(), now=now)

    def test_reserve_records_the_complete_identity_document_and_hash(self):
        stored = self.reserved("task-alpha")
        self.assertEqual(stored["decisionId"], "dec-1")
        self.assertEqual(stored["taskId"], "task-alpha")
        self.assertEqual(stored["routerIndex"], 0)
        self.assertEqual(stored["buddyId"], ALPHA["buddyId"])
        self.assertEqual(stored["buddy"], {key: ALPHA[key] for key in ("harness", "provider", "model", "effort")})
        self.assertEqual(stored["document"]["routerBuddyId"], ALPHA["buddyId"])
        self.assertEqual(stored["createdAt"], T0)
        self.assertEqual(stored["inputSha256"], sha256_text(canonical_json(alpha_document())))

    def test_two_dispatches_of_one_request_keep_their_own_documents_and_hashes(self):
        alpha = self.reserved("task-alpha")
        bravo = self.reserved("task-bravo", buddy=BRAVO, document=bravo_document(), router_index=1)
        self.assertNotEqual(alpha["inputSha256"], bravo["inputSha256"])
        with self.board_.store.db.read() as db:
            first = router_sequence.dispatch(db, "task-alpha")
            first["document"]["task"] = "mutated"
            first["inputSha256"] = "mutated"
            again = router_sequence.dispatch(db, "task-alpha")
        self.assertEqual(again["document"]["task"], "fix the parser")
        self.assertEqual(again["inputSha256"], alpha["inputSha256"])
        stored = self.stored("router_dispatches", "task_id", "task-alpha")
        self.assertNotIn("mutated", stored)

    def test_dispatches_follow_router_index_when_task_ids_have_reverse_order(self):
        first = self.reserved("task-bravo")
        second = self.reserved("task-alpha", buddy=BRAVO, document=bravo_document(), router_index=1)
        with self.board_.store.db.read() as db:
            dispatches = router_sequence.dispatches(db, "dec-1")
            self.assertEqual([item["taskId"] for item in dispatches], ["task-bravo", "task-alpha"])
            self.assertEqual(dispatches, (first, second))
            self.assertEqual(router_sequence.dispatches(db, "dec-missing"), ())
            dispatches[0]["document"]["task"] = "caller mutation"
            self.assertEqual(router_sequence.dispatches(db, "dec-1"), (first, second))


class RecordClaimTests(RouterSequenceTestCase):
    def claim(self, attempt_id: str, *, task_id="task-alpha", generation=1, now=T0):
        with self.board_.store.db.write() as db:
            self.seed_attempt(db, attempt_id, task_id, generation)
            return router_sequence.record_claim(db, task_id=task_id, attempt_id=attempt_id,
                                                generation=generation, now=now)

    def test_claim_binds_the_attempt_and_appends_one_atomic_event(self):
        self.reserved_claim_setup()
        stored = self.claim("att-1")
        self.assertEqual(stored, {"taskId": "task-alpha", "attemptId": "att-1", "generation": 1,
                                  "decisionId": "dec-1", "routerIndex": 0, "buddyId": ALPHA["buddyId"]})
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"buddyId": ALPHA["buddyId"], "plane": "routing", "requestId": "pick-1",
                                     "routerIndex": 0, "taskId": "task-alpha", "attemptId": "att-1",
                                     "generation": 1})
        # The binding is a fresh copy; mutating it touches nothing stored.
        stored["buddyId"] = "mutated"
        with self.board_.store.db.read() as db:
            self.assertEqual(router_sequence.claim(db, "att-1")["buddyId"], ALPHA["buddyId"])
        self.assertEqual(len(self.events()), 1)

    def reserved_claim_setup(self):
        with self.board_.store.db.write() as db:
            router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
            router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-alpha",
                                             router_index=0, buddy=ALPHA,
                                             document=alpha_document(), now=T0)

    def test_claim_rolls_back_with_its_caller_transaction(self):
        self.reserved_claim_setup()
        with self.assertRaises(AssertionError):
            with self.board_.store.db.write() as db:
                self.seed_attempt(db, "att-rollback", "task-alpha", 1)
                router_sequence.record_claim(db, task_id="task-alpha", attempt_id="att-rollback",
                                             generation=1, now=T0)
                raise AssertionError("rollback")
        self.assertIsNone(self.stored("router_claims", "attempt_id", "att-rollback"))
        self.assertEqual(self.events(), [])


class RelationalBindingTests(RouterSequenceTestCase):
    def reserve(self, db):
        router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
        router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-alpha", router_index=0,
                                         buddy=ALPHA, document=alpha_document(), now=T0)

    def outcome(self, db, *, task_id="task-alpha", attempt_id=None):
        return router_history.record_outcome(db, buddy_id=ALPHA["buddyId"], plane="routing",
            request_id="pick-1", task_id=task_id, attempt_id=attempt_id, outcome="answered", code=None,
            phase="result", facts={"routerIndex":0}, now=T0)

    def test_request_and_dispatch_foreign_keys_require_their_actual_parents(self):
        with self.assertRaises(sqlite3.IntegrityError):
            with self.board_.store.db.write() as db:
                router_sequence.freeze_request(db, "missing", snapshot=snapshot_dict(), now=T0)
        for decision_id, task_id in (("dec-1", "task-alpha"), ("dec-1", "missing-task")):
            with self.subTest(decision_id=decision_id, task_id=task_id), self.assertRaises(sqlite3.IntegrityError):
                with self.board_.store.db.write() as db:
                    if task_id == "missing-task":
                        router_sequence.freeze_request(db, decision_id, snapshot=snapshot_dict(), now=T0)
                    router_sequence.reserve_dispatch(db, decision_id=decision_id, task_id=task_id,
                        router_index=0, buddy=ALPHA, document=alpha_document(), now=T0)
        self.assertIsNone(self.stored("router_requests", "decision_id", "dec-1"))
        self.assertIsNone(self.stored("router_dispatches", "task_id", "task-alpha"))

    def test_one_request_position_keeps_independent_immutable_dispatches(self):
        with self.board_.store.db.write() as db:
            self.reserve(db)
            second = router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-bravo", router_index=0,
                                                      buddy=BRAVO, document=bravo_document(), now=T0)
        with self.board_.store.db.read() as db:
            first = router_sequence.dispatch(db, "task-alpha")
            self.assertEqual(router_sequence.dispatch(db, "task-bravo"), second)
            self.assertEqual(first["document"], alpha_document())
            self.assertEqual(second["document"], bravo_document())
            self.assertNotEqual(first["inputSha256"], second["inputSha256"])
        with self.assertRaises(sqlite3.IntegrityError):
            with self.board_.store.db.write() as db:
                router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-alpha", router_index=0,
                                                 buddy=BRAVO, document=bravo_document(), now=T0)
        with self.board_.store.db.read() as db:
            self.assertEqual(router_sequence.dispatch(db, "task-alpha"), first)

    def test_deferred_claim_accepts_only_the_exact_attempt_task_and_generation_at_commit(self):
        with self.board_.store.db.write() as db:
            self.reserve(db)
        for task_id, generation in (("task-bravo", 1), ("task-alpha", 2)):
            with self.subTest(task_id=task_id, generation=generation), self.assertRaises(sqlite3.IntegrityError):
                with self.board_.store.db.write() as db:
                    router_sequence.record_claim(db, task_id="task-alpha", attempt_id="wrong-binding",
                                                 generation=1, now=T0)
                    self.seed_attempt(db, "wrong-binding", task_id, generation)
            self.assertIsNone(self.stored("router_claims", "attempt_id", "wrong-binding"))
            self.assertEqual(self.events(), [])
        with self.board_.store.db.write() as db:
            router_sequence.record_claim(db, task_id="task-alpha", attempt_id="actual-binding", generation=1, now=T0)
            self.seed_attempt(db, "actual-binding", "task-alpha", 1)
        self.assertEqual(len(self.events()), 1)

    def test_claim_task_generation_is_unique_before_deferred_attempt_binding(self):
        with self.board_.store.db.write() as db:
            self.reserve(db)
            # Both candidate attempts are absent: the second INSERT must fail
            # immediately on router_claims, independently of attempts' UNIQUE
            # and its deferred composite FK. Bind the first before committing.
            db.execute("INSERT INTO router_claims(attempt_id,task_id,generation)"
                       " VALUES('first-pending','task-alpha',1)")
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE task_id='task-alpha'").fetchone()[0], 0)
            with self.assertRaisesRegex(sqlite3.IntegrityError,
                                        r"UNIQUE constraint failed: router_claims.task_id, router_claims.generation"):
                db.execute("INSERT INTO router_claims(attempt_id,task_id,generation)"
                           " VALUES('second-pending','task-alpha',1)")
            self.seed_attempt(db, "first-pending", "task-alpha", 1)
        with self.board_.store.db.read() as db:
            self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM router_claims")],
                             [("first-pending", "task-alpha", 1)])
            self.assertEqual(db.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_outcome_event_sequence_cannot_be_shared_by_two_receipts(self):
        with self.board_.store.db.write() as db:
            first = self.outcome(db)
            # Different receipt, same existing event: only event_seq uniqueness
            # can reject this row; every FK already has its parent.
            with self.assertRaisesRegex(sqlite3.IntegrityError,
                                        r"UNIQUE constraint failed: router_outcomes.event_seq"):
                db.execute("INSERT INTO router_outcomes SELECT 'another-receipt',plane,request_id,router_index,"
                           "buddy_id,phase,task_id,attempt_id,outcome,code,event_seq FROM router_outcomes")
        with self.board_.store.db.read() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT event_seq FROM router_outcomes")], [first['seq']])
        self.assertEqual(len(self.events("router.answered")), 1)

    def test_outcome_event_sequence_requires_an_existing_event(self):
        with self.board_.store.db.write() as db:
            first = self.outcome(db)
            absent_seq = db.execute("SELECT MAX(seq)+1 FROM events").fetchone()[0]
            # The new sequence is unique. Only the events FK can reject it.
            with self.assertRaisesRegex(sqlite3.IntegrityError, "FOREIGN KEY constraint failed"):
                db.execute("UPDATE router_outcomes SET event_seq=?", (absent_seq,))
        with self.board_.store.db.read() as db:
            self.assertEqual([row[0] for row in db.execute("SELECT event_seq FROM router_outcomes")], [first['seq']])
        self.assertEqual(len(self.events("router.answered")), 1)

    def test_outcome_attempt_cannot_name_a_different_task_or_omit_its_task(self):
        with self.board_.store.db.write() as db:
            self.seed_attempt(db, "other-attempt", "task-bravo", 1)
        for task_id in ("task-alpha", None):
            with self.subTest(task_id=task_id), self.assertRaises(sqlite3.IntegrityError):
                with self.board_.store.db.write() as db:
                    self.outcome(db, task_id=task_id, attempt_id="other-attempt")
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM router_outcomes").fetchone()[0], 0)
        self.assertEqual(self.events("router.answered"), [])

    def test_receipt_is_persistent_idempotent_and_atomic_with_its_event(self):
        with self.board_.store.db.write() as db:
            first = self.outcome(db)
        with self.board_.store.db.write() as db:
            replay = self.outcome(db)
        self.assertTrue(first["recorded"])
        self.assertFalse(replay["recorded"])
        self.assertEqual(first["seq"], replay["seq"])
        with self.assertRaises(AssertionError):
            with self.board_.store.db.write() as db:
                self.outcome(db, task_id="task-bravo")
                raise AssertionError("private transaction rollback")
        self.assertEqual(len(self.events("router.answered")), 1)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT event_seq FROM router_outcomes").fetchone()[0], first["seq"])
            self.assertIsNone(db.execute("SELECT key FROM meta WHERE key LIKE 'router-request:%'"
                " OR key LIKE 'router-dispatch:%' OR key LIKE 'attempt-router:%' OR key LIKE 'router-outcome:%'").fetchone())


if __name__ == "__main__":
    unittest.main()
