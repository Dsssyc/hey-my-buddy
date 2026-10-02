"""The immutable Router request/dispatch/claim facts over meta, without models.

Everything runs on one private board. The sequence layer freezes one request
snapshot with its complete actor-free base input, records one immutable dispatch
per internal task and binds each claim attempt to its dispatch together with the
``router.claimed`` fact. Identical content replays; any difference conflicts;
returned documents are fresh copies; nothing rewrites an older document, hash or
receipt; and a rolled-back transaction leaves neither meta nor event behind.
"""
import json
import unittest

from support import BoardTestCase
from buddy import router_sequence
from buddy.db import canonical_json, sha256_text
from buddy.errors import BoardError

T0 = "2026-01-01T00:00:00.000Z"
T1 = "2026-01-01T00:01:00.000Z"
FACTS = {
    "routerProfileIds": ["dsh:fixture:alpha:max", "dsh:fixture:bravo:max"],
    "routerIdentities": [
        {"adapter": "dsh", "provider": "fixture", "model": "alpha", "effort": "max"},
        {"adapter": "dsh", "provider": "fixture", "model": "bravo", "effort": "max"},
    ],
    "routingMode": "fast",
    "routingBudget": "standard",
    "routerRetryIntervalSeconds": 600,
    "configurationRevision": 3,
    "budget": {"timeoutSeconds": 60},
}
BASE_INPUT = {
    "operation": "select",
    "requestId": "pick-1",
    "tableRevision": 7,
    "task": "fix the parser",
    "profiles": [{"profileId": "dsh:fixture:alpha:max"}, {"profileId": "dsh:fixture:bravo:max"}],
    "cards": [],
    "preferences": [],
    "evidence": [],
    "annotations": [],
    "policyFacts": {},
    "outputSchema": {"type": "object"},
    "accounts": {"dsh": {"source": "native", "credentialRevision": 0}},
    "budget": {"timeoutSeconds": 60},
    "routingMode": "fast",
}
ALPHA = {"profileId": "dsh:fixture:alpha:max", "adapter": "dsh", "provider": "fixture",
         "model": "alpha", "effort": "max"}
BRAVO = {"profileId": "dsh:fixture:bravo:max", "adapter": "dsh", "provider": "fixture",
         "model": "bravo", "effort": "max"}
INSPECTIONS = [{"index": 0, "profileId": ALPHA["profileId"], "eligible": True, "code": None,
                "reason": None, "selected": True}]


def snapshot_dict() -> dict:
    return {"facts": dict(FACTS), "baseInput": dict(BASE_INPUT), "inspections": [dict(entry) for entry in INSPECTIONS]}


def alpha_document() -> dict:
    identity = {key: ALPHA[key] for key in ("adapter", "provider", "model", "effort")}
    return {**BASE_INPUT, "profile": identity, "routerProfileId": ALPHA["profileId"],
            "routerProfile": identity, "routerIndex": 0}


def bravo_document() -> dict:
    identity = {key: BRAVO[key] for key in ("adapter", "provider", "model", "effort")}
    return {**BASE_INPUT, "profile": identity, "routerProfileId": BRAVO["profileId"],
            "routerProfile": identity, "routerIndex": 1}


class RouterSequenceTestCase(BoardTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.board_ = self.board()
        with self.board_.store.db.write() as db:
            db.execute(
                "INSERT INTO evaluation_decisions(decision_id,status,task,table_revision,reason,"
                "evidence_ids_json,created_at) VALUES('dec-1','queued','task',0,'','[]',?)", (T0,))
            db.execute(
                "INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,"
                "expected_revision,requested_json,created_at,updated_at)"
                " VALUES('dec-1','pick-1','select','digest',0,'{}',?,?)", (T0, T0))
            for task_id in ("task-alpha", "task-bravo"):
                db.execute(
                    "INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
                    "fingerprint_version,adapter,cwd,timeout_seconds,state,revision,created_at,updated_at)"
                    " VALUES(?,?,'decision','{}','{}','digest',1,'decision','/work',60,'queued',1,?,?)",
                    (task_id, f"decision:{task_id}", T0, T0))

    def meta(self, key: str) -> str | None:
        with self.board_.store.db.read() as db:
            row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
            return row[0] if row is not None else None

    def events(self, kind: str = "router.claimed") -> list[dict]:
        with self.board_.store.db.read() as db:
            rows = db.execute("SELECT payload_json FROM events WHERE kind=?", (kind,)).fetchall()
        return [json.loads(row[0]) for row in rows]


class FreezeRequestTests(RouterSequenceTestCase):
    def test_freeze_is_idempotent_for_identical_content_and_conflicts_on_difference(self):
        with self.board_.store.db.write() as db:
            first = router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
            replay = router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T0)
        self.assertEqual(first, replay)
        self.assertEqual(first["frozenAt"], T0)
        changed = snapshot_dict()
        changed["facts"]["routerRetryIntervalSeconds"] = 30
        with self.assertRaises(BoardError) as caught:
            with self.board_.store.db.write() as db:
                router_sequence.freeze_request(db, "dec-1", snapshot=changed, now=T0)
        self.assertEqual(caught.exception.code, "CONFLICT")
        with self.board_.store.db.write() as db:
            later = router_sequence.freeze_request(db, "dec-1", snapshot=snapshot_dict(), now=T1)
        self.assertEqual(later, first)
        self.assertEqual(later["frozenAt"], T0)
        with self.board_.store.db.read() as db:
            stored = router_sequence.request_snapshot(db, "dec-1")
        self.assertEqual(stored["facts"]["routerRetryIntervalSeconds"], 600)

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
        stored = self.meta("router-request:dec-1")
        self.assertIn("fix the parser", stored)
        self.assertNotIn("mutated", stored)

    def test_snapshot_shape_is_validated(self):
        for broken in (42, {}, {"facts": FACTS}, {"facts": FACTS, "baseInput": 3, "inspections": []}):
            with self.subTest(broken=broken), self.assertRaises(BoardError) as caught:
                with self.board_.store.db.write() as db:
                    router_sequence.freeze_request(db, "dec-1", snapshot=broken, now=T0)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIsNone(self.meta("router-request:dec-1"))

    def test_unknown_decision_reads_none(self):
        with self.board_.store.db.read() as db:
            self.assertIsNone(router_sequence.request_snapshot(db, "dec-missing"))
            self.assertIsNone(router_sequence.dispatch(db, "task-missing"))


class ReserveDispatchTests(RouterSequenceTestCase):
    def reserved(self, task_id: str, *, profile=ALPHA, document: dict | None = None, router_index: int = 0,
                 now: str = T0):
        with self.board_.store.db.write() as db:
            return router_sequence.reserve_dispatch(
                db, decision_id="dec-1", task_id=task_id, router_index=router_index,
                profile=profile, document=document or alpha_document(), now=now)

    def test_reserve_records_the_complete_identity_document_and_hash(self):
        stored = self.reserved("task-alpha")
        self.assertEqual(stored["decisionId"], "dec-1")
        self.assertEqual(stored["taskId"], "task-alpha")
        self.assertEqual(stored["routerIndex"], 0)
        self.assertEqual(stored["profileId"], ALPHA["profileId"])
        self.assertEqual(stored["profile"], {key: ALPHA[key] for key in ("adapter", "provider", "model", "effort")})
        self.assertEqual(stored["document"]["routerProfileId"], ALPHA["profileId"])
        self.assertEqual(stored["createdAt"], T0)
        self.assertEqual(stored["inputSha256"], sha256_text(canonical_json(alpha_document())))

    def test_identical_reserve_replays_and_any_difference_conflicts(self):
        self.reserved("task-alpha")
        with self.board_.store.db.write() as db:
            replay = router_sequence.reserve_dispatch(
                db, decision_id="dec-1", task_id="task-alpha", router_index=0,
                profile=ALPHA, document=alpha_document(), now=T0)
        self.assertEqual(replay["createdAt"], T0)
        later = self.reserved("task-alpha", now=T1)
        self.assertEqual(later, replay)
        self.assertEqual(later["createdAt"], T0)
        with self.assertRaises(BoardError) as caught:
            self.reserved("task-alpha", document=bravo_document())
        self.assertEqual(caught.exception.code, "CONFLICT")
        with self.assertRaises(BoardError) as caught:
            self.reserved("task-alpha", router_index=3)
        self.assertEqual(caught.exception.code, "CONFLICT")

    def test_two_dispatches_of_one_request_keep_their_own_documents_and_hashes(self):
        alpha = self.reserved("task-alpha")
        bravo = self.reserved("task-bravo", profile=BRAVO, document=bravo_document(), router_index=1)
        self.assertNotEqual(alpha["inputSha256"], bravo["inputSha256"])
        with self.board_.store.db.read() as db:
            first = router_sequence.dispatch(db, "task-alpha")
            first["document"]["task"] = "mutated"
            first["inputSha256"] = "mutated"
            again = router_sequence.dispatch(db, "task-alpha")
        self.assertEqual(again["document"]["task"], "fix the parser")
        self.assertEqual(again["inputSha256"], alpha["inputSha256"])
        stored = self.meta("router-dispatch:task-alpha")
        self.assertNotIn("mutated", stored)

    def test_incomplete_identity_or_document_is_rejected(self):
        broken_identity = {**ALPHA, "effort": ""}
        for profile, index in ((broken_identity, 0), ({"profileId": "x"}, 0), (ALPHA, -1), (ALPHA, True)):
            with self.subTest(profile=profile, index=index), self.assertRaises(BoardError) as caught:
                self.reserved("task-alpha", profile=profile, router_index=index)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.assertRaises(BoardError) as caught:
            self.reserved("task-alpha", document="not-an-object")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertIsNone(self.meta("router-dispatch:task-alpha"))


class RecordClaimTests(RouterSequenceTestCase):
    def claim(self, attempt_id: str, *, task_id="task-alpha", generation=1, now=T0):
        with self.board_.store.db.write() as db:
            return router_sequence.record_claim(db, task_id=task_id, attempt_id=attempt_id,
                                                generation=generation, now=now)

    def test_claim_binds_the_attempt_and_appends_one_atomic_event(self):
        self.reserved_claim_setup()
        stored = self.claim("att-1")
        self.assertEqual(stored, {"taskId": "task-alpha", "attemptId": "att-1", "generation": 1,
                                  "decisionId": "dec-1", "routerIndex": 0, "profileId": ALPHA["profileId"]})
        events = self.events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0], {"profileId": ALPHA["profileId"], "plane": "work", "requestId": "pick-1",
                                     "routerIndex": 0, "taskId": "task-alpha", "attemptId": "att-1",
                                     "generation": 1})
        # The binding is a fresh copy; mutating it touches nothing stored.
        stored["profileId"] = "mutated"
        self.assertEqual(self.claim("att-1")["profileId"], ALPHA["profileId"])
        self.assertEqual(len(self.events()), 1)

    def reserved_claim_setup(self):
        with self.board_.store.db.write() as db:
            router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-alpha",
                                             router_index=0, profile=ALPHA,
                                             document=alpha_document(), now=T0)

    def test_different_binding_for_one_attempt_conflicts(self):
        self.reserved_claim_setup()
        self.claim("att-1", generation=1)
        with self.assertRaises(BoardError) as caught:
            self.claim("att-1", generation=2)
        self.assertEqual(caught.exception.code, "CONFLICT")
        self.assertEqual(len(self.events()), 1)
        # Another attempt of another dispatch claims its own binding.
        with self.board_.store.db.write() as db:
            router_sequence.reserve_dispatch(db, decision_id="dec-1", task_id="task-bravo",
                                             router_index=1, profile=BRAVO,
                                             document=bravo_document(), now=T0)
        second = self.claim("att-2", task_id="task-bravo", generation=1)
        self.assertEqual(second["profileId"], BRAVO["profileId"])
        self.assertEqual(len(self.events()), 2)

    def test_claim_requires_a_frozen_dispatch_and_a_positive_generation(self):
        with self.assertRaises(BoardError) as caught:
            self.claim("att-orphan")
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.reserved_claim_setup()
        for generation in (0, -1, True, "1"):
            with self.subTest(generation=generation), self.assertRaises(BoardError) as caught:
                self.claim("att-bad", generation=generation)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        self.assertEqual(self.events(), [])

    def test_claim_rolls_back_with_its_caller_transaction(self):
        self.reserved_claim_setup()
        with self.assertRaises(AssertionError):
            with self.board_.store.db.write() as db:
                router_sequence.record_claim(db, task_id="task-alpha", attempt_id="att-rollback",
                                             generation=1, now=T0)
                raise AssertionError("rollback")
        self.assertIsNone(self.meta("attempt-router:att-rollback"))
        self.assertEqual(self.events(), [])


if __name__ == "__main__":
    unittest.main()
