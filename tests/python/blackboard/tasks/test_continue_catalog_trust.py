"""ADR-027 rule 3 for continuation overrides: real catalog facts, real re-read.

A Host's explicit ``workflow_continue`` buddy must meet the same catalog
facts as a submission: a missing or unavailable route gets exactly one bounded
re-read of its harness before rejection, and the rejection names the catalog read
time and a structured reason. The user's enabled choice stays a separate gate
that neither a refresh nor the override can flip, and its refusal carries a
different reason and facts than a catalog refusal. These tests run the real
``catalog.validate_buddy`` (WorkflowTestCase's passthrough mock is
replaced) and never auto-enable the override target.
"""
import json
from unittest.mock import patch

from support import FakeClock, enable_fixture_buddy
from blackboard.tasks.test_catalog_trust import IDENTITY, reading
from blackboard.tasks.test_workflow import WorkflowTestCase

from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.errors import BoardError

# Bound at import time, before WorkflowTestCase's setUp mock replaces the module
# attribute, so these tests can put the real validation back underneath it.
_real_validate_buddy = catalog.validate_buddy


class ContinueCatalogTrustTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        # WorkflowTestCase replaces catalog validation with a passthrough mock;
        # a continuation override here must face the real recorded catalog, the
        # real pending rules and the real single bounded re-read.
        self.enterContext(patch.object(catalog, "validate_buddy", _real_validate_buddy))
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.directory = self.board.store.directory
        # The service hook registered by this board is replaced per test; the
        # cleanup puts the board's own bounded refresh back.
        self.service_hook = catalog._catalog_reread.get(str(self.directory))
        self.addCleanup(lambda: catalog.register_catalog_reread(self.directory, self.service_hook))

    def seed(self):
        """One admitted catalog and one submitted run waiting for a continuation."""
        self.evaluation.record_catalog(reading())
        submitted = self.submit(self.board, provider="fixture", model="alpha", effort="max")
        self.finish_turn(self.board, self.claim(self.board, run_id=submitted["runId"]))
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id='dsh:fixture:alpha:max'")
        return self.board.call("workflow_get", {"runId": submitted["runId"]})

    def continue_with(self, view, buddy):
        """A Host continuation override without the test helper's auto-enable."""
        if view["state"] == "executing":
            self.finish_turn(self.board, self.claim(self.board, run_id=view["runId"]))
            view = self.board.call("workflow_get", {"runId": view["runId"]})
        response = self.board.call("workflow_continue", {
            "runId": view["runId"], "artifactId": view["finalArtifactId"],
            "input": "continue with the explicit route",
            "buddy": ":".join(buddy[key] for key in ("harness", "provider", "model", "effort")),
            'note': "Route the continuation through this explicit buddy",
        })
        return {**self.board.call("workflow_get", {"runId": view["runId"]}), **response}

    def enable_buddy(self, buddy_id):
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=1 WHERE buddy_id=?", (buddy_id,))

    def buddy_enabled(self, buddy_id):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT enabled FROM buddies WHERE buddy_id=?", (buddy_id,)).fetchone()
            return None if row is None else bool(row[0])

    def overridden_event(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT payload_json FROM events WHERE kind='workflow.buddy_overridden'").fetchone()
        return json.loads(row[0]) if row is not None else None

    def test_missing_route_admits_after_one_bounded_reread_recovers(self):
        submitted = self.seed()
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_buddy(self.board.store, override)
        continued = self.continue_with(submitted, override)
        self.assertEqual(calls, ["dsh"], "Exactly one bounded re-read of exactly that harness")
        self.assertEqual(continued["buddy"], override)
        self.assertEqual(self.overridden_event()["buddy"], override)

    def test_missing_route_without_recovery_still_rejects_with_catalog_facts(self):
        submitted = self.seed()
        read_at = self.clock.value
        calls = []

        def reread(name):
            calls.append(name)  # The bounded refresh runs and brings nothing new.

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_buddy(self.board.store, override)  # The user gate is satisfied.
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, override)
        error = rejected.exception
        self.assertEqual(error.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("catalogReadAt"), read_at)
        self.assertNotIn("remedy", error.details)
        self.assertEqual(error.details.get("reason"), {"code": "catalog-unavailable", "params": {}},
                         "The cause is the catalog, not the user gate")
        self.assertNotIn("enabled", error.details)

    def test_confirmed_unavailable_route_still_rejects_after_one_reread(self):
        submitted = self.seed()
        self.evaluation.record_catalog(reading(models=()))  # First absence starts the window.
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))  # A confirmed absence ends it.
        confirmed_at = self.clock.value
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=()))  # The refresh still reports absence.

        catalog.register_catalog_reread(self.directory, reread)
        self.enable_buddy("dsh:fixture:alpha:max")  # The user gate is satisfied.
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, IDENTITY)
        error = rejected.exception
        self.assertEqual(error.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("catalogReadAt"), confirmed_at)
        self.assertNotIn("remedy", error.details)
        self.assertNotIn("enabled", error.details)

    def test_confirmed_unavailable_route_recovers_when_the_reread_lists_it_again(self):
        submitted = self.seed()
        self.evaluation.record_catalog(reading(models=()))
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha',)))

        catalog.register_catalog_reread(self.directory, reread)
        self.enable_buddy("dsh:fixture:alpha:max")
        continued = self.continue_with(submitted, IDENTITY)
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(continued["buddy"], IDENTITY)

    def test_unavailable_and_disabled_route_reports_catalog_reason_first(self):
        submitted = self.seed()
        self.evaluation.record_catalog(reading(models=()))
        self.clock.advance(3601)
        self.evaluation.record_catalog(reading(models=()))
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, IDENTITY)
        self.assertEqual(rejected.exception.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(rejected.exception.details["reason"]["code"], "catalog-unavailable")
        self.assertNotIn("enabled", rejected.exception.details)
        self.assertEqual(calls, ["dsh"])

    def test_override_without_a_recorded_buddy_name_is_invalid_input(self):
        # A named Host choice must identify a recorded buddy before catalog
        # validation or the enabled gate can interpret it.
        self.catalog_fixture(reading())
        submitted = self.submit(self.board, provider="fixture", model="alpha", effort="max")
        self.finish_turn(self.board, self.claim(self.board, run_id=submitted["runId"]))
        submitted = self.board.call("workflow_get", {"runId": submitted["runId"]})
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM buddies WHERE buddy_id='dsh:fixture:alpha:max'")
            self.assertIsNone(db.execute(
                "SELECT 1 FROM buddies WHERE buddy_id='dsh:fixture:alpha:max'").fetchone(),
                "The removed user policy row must not be recreated by validation")
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, IDENTITY)
        gate = rejected.exception
        self.assertEqual(gate.code, "INVALID_ARGUMENT")
        self.assertEqual(gate.details["buddy"], "dsh:fixture:alpha:max")
        self.assertEqual(calls, [], "The operator pin admits validation without a native re-read")
        self.assertIsNone(self.overridden_event())

    def test_disabled_and_catalog_refusals_carry_different_reasons_and_facts(self):
        submitted = self.seed()
        read_at = self.clock.value
        calls = []
        catalog.register_catalog_reread(self.directory, lambda name: calls.append(name))
        # An admitted catalog route whose buddy the user never enabled.
        with self.assertRaises(BoardError) as disabled:
            self.continue_with(submitted, IDENTITY)
        gate = disabled.exception
        self.assertEqual(gate.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(gate.details.get("reason"), {"code": "not-enabled", "params": {}})
        self.assertIn("enabled", gate.details, "The user-gate refusal states the enablement fact itself")
        self.assertIs(gate.details["enabled"], False)
        self.assertEqual(gate.details.get("catalogReadAt"), read_at, "The refusal still names the catalog read time")
        self.assertNotIn("remedy", gate.details)
        self.assertEqual(calls, [], "An admitted route is not re-read for the user gate")
        # A route missing from the catalog whose buddy row the user enabled.
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_buddy(self.board.store, override)
        with self.assertRaises(BoardError) as missing:
            self.continue_with(submitted, override)
        absent = missing.exception
        self.assertEqual(absent.code, "BUDDY_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertNotIn("remedy", absent.details)
        self.assertEqual(absent.details.get("reason"), {"code": "catalog-unavailable", "params": {}})
        self.assertNotIn("enabled", absent.details)
        self.assertNotEqual(gate.message, absent.message)
        self.assertNotEqual(gate.details["reason"], absent.details["reason"])

    def test_refresh_never_flips_the_users_enabled_choice(self):
        submitted = self.seed()  # alpha is listed; both buddies start disabled.
        calls = []

        def reread(name):
            calls.append(name)
            self.clock.advance(60)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}  # Absent now; the re-read will list it.
        enable_fixture_buddy(self.board.store, override)
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=0 WHERE buddy_id='dsh:fixture:beta:max'")
        with self.assertRaises(BoardError) as refused:
            self.continue_with(submitted, override)
        error = refused.exception
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("reason"), {"code": "not-enabled", "params": {}},
                         "The refreshed catalog admitted the route; only the user gate refuses")
        self.assertEqual(error.details.get("catalogReadAt"), self.clock.value,
                         "The refusal names the refreshed reading's time")
        self.assertEqual(self.buddy_enabled("dsh:fixture:beta:max"), False,
                         "The refresh that listed beta did not enable it")
        self.assertEqual(self.buddy_enabled("dsh:fixture:alpha:max"), False)
        # Enabling it afterwards is the user's act, and only then does the override pass.
        self.enable_buddy("dsh:fixture:beta:max")
        continued = self.continue_with(submitted, override)
        self.assertEqual(continued["buddy"], override)
