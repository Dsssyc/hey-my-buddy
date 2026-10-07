"""ADR-027 rule 3 for continuation overrides: real catalog facts, real re-read.

A Host's explicit ``workflow_continue`` configuration must meet the same catalog
facts as a submission: a missing or unavailable route gets exactly one bounded
re-read of its harness before rejection, and the rejection names the catalog read
time and the refresh remedy. The user's enabled choice stays a separate gate
that neither a refresh nor the override can flip, and its refusal carries a
different reason and facts than a catalog refusal. These tests run the real
``catalog.validate_configuration`` (WorkflowTestCase's passthrough mock is
replaced) and never auto-enable the override target.
"""
import json
from unittest.mock import patch

from support import FakeClock, enable_fixture_configuration
from blackboard.tasks.test_catalog_trust import IDENTITY, reading
from blackboard.tasks.test_workflow import WorkflowTestCase

from hey_my_buddy.blackboard.catalog import catalog
from hey_my_buddy.errors import BoardError

# Bound at import time, before WorkflowTestCase's setUp mock replaces the module
# attribute, so these tests can put the real validation back underneath it.
_real_validate_configuration = catalog.validate_configuration


class ContinueCatalogTrustTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        # WorkflowTestCase replaces catalog validation with a passthrough mock;
        # a continuation override here must face the real recorded catalog, the
        # real pending rules and the real single bounded re-read.
        self.enterContext(patch.object(catalog, "validate_configuration", _real_validate_configuration))
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
        return self.submit(self.board, provider="fixture", model="alpha", effort="max")

    def continue_with(self, view, configuration, *, command_id="continue-1"):
        """A Host continuation override without the test helper's auto-enable."""
        return self.board.call("workflow_continue", {
            "runId": view["runId"], "commandId": command_id,
            "expectedRevision": view["revision"], "input": "continue with the explicit route",
            "helperPolicy": "keep", "configuration": configuration,
            "reason": "Route the continuation through this explicit configuration",
            **self.control(view),
        })

    def enable_profile(self, profile_id):
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE profile_id=?", (profile_id,))

    def profile_enabled(self, profile_id):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT enabled FROM evaluation_profiles WHERE profile_id=?", (profile_id,)).fetchone()
            return None if row is None else bool(row[0])

    def overridden_event(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT payload_json FROM events WHERE kind='workflow.configuration_overridden'").fetchone()
        return json.loads(row[0]) if row is not None else None

    def test_missing_route_admits_after_one_bounded_reread_recovers(self):
        submitted = self.seed()
        calls = []

        def reread(name):
            calls.append(name)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_configuration(self.board.store, override)
        continued = self.continue_with(submitted, override)
        self.assertEqual(calls, ["dsh"], "Exactly one bounded re-read of exactly that harness")
        self.assertEqual(continued["executionConfiguration"], override)
        self.assertEqual(self.overridden_event()["configuration"], override)

    def test_missing_route_without_recovery_still_rejects_with_catalog_facts(self):
        submitted = self.seed()
        read_at = self.clock.value
        calls = []

        def reread(name):
            calls.append(name)  # The bounded refresh runs and brings nothing new.

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_configuration(self.board.store, override)  # The user gate is satisfied.
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, override)
        error = rejected.exception
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("catalogReadAt"), read_at)
        self.assertEqual(error.details.get("remedy"), catalog.CATALOG_REMEDY)
        self.assertNotIn("reason", error.details, "The cause is the catalog, not the user gate")
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
        self.enable_profile("dsh:fixture:alpha:max")  # The user gate is satisfied.
        with self.assertRaises(BoardError) as rejected:
            self.continue_with(submitted, IDENTITY)
        error = rejected.exception
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("catalogReadAt"), confirmed_at)
        self.assertEqual(error.details.get("remedy"), catalog.CATALOG_REMEDY)
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
        self.enable_profile("dsh:fixture:alpha:max")
        continued = self.continue_with(submitted, IDENTITY)
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(continued["executionConfiguration"], IDENTITY)

    def test_catalog_absence_confirmed_before_write_fences_the_override(self):
        submitted = self.seed()
        self.enable_profile("dsh:fixture:alpha:max")
        self.evaluation.record_catalog(reading(models=()))
        self.clock.advance(3601)
        calls = []
        catalog.register_catalog_reread(self.directory, calls.append)
        workflow = self.board.store.workflow
        validate = workflow._validated_configuration

        def workflow_state():
            with self.board.store.db.read() as db:
                return {
                    table: [tuple(row) for row in db.execute(query, (submitted["runId"],))]
                    for table, query in {
                        "run": "SELECT * FROM workflow_runs WHERE run_id=?",
                        "task": "SELECT * FROM tasks WHERE task_id=?",
                        "continuations": "SELECT * FROM workflow_continuations WHERE run_id=?",
                        "receipts": "SELECT * FROM commands WHERE task_id=?",
                        "events": "SELECT * FROM events WHERE task_id=? ORDER BY seq",
                    }.items()
                }

        before = workflow_state()

        def confirm_after_validation(configuration):
            # The real validator accepts pending alpha even after one hour;
            # only a second confirmed absence retires it. Publish that reading
            # after validation returns, before continue enters its write txn.
            admitted = validate(configuration)
            self.assertEqual(admitted, IDENTITY)
            self.assertEqual(calls, [], "Pending admission needs no native re-read")
            self.evaluation.record_catalog(reading(models=()))
            return admitted

        with patch.object(workflow, "_validated_configuration", side_effect=confirm_after_validation):
            with self.assertRaises(BoardError) as rejected:
                self.continue_with(submitted, IDENTITY)
        error = rejected.exception
        self.assertEqual(error.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(error.details.get("reason"), "catalog-unavailable")
        self.assertEqual(error.details.get("catalogReadAt"), self.clock.value)
        self.assertEqual(error.details.get("remedy"), catalog.CATALOG_REMEDY)
        self.assertNotIn("enabled", error.details, "User enablement is not the cause")
        self.assertEqual(calls, [], "The write fence must not start a native re-read")
        self.assertTrue(self.profile_enabled("dsh:fixture:alpha:max"))
        self.assertIsNone(self.overridden_event())
        self.assertEqual(workflow_state(), before, "No configuration, requeue, continuation, receipt or event writes")

    def test_disabled_and_catalog_refusals_carry_different_reasons_and_facts(self):
        submitted = self.seed()
        read_at = self.clock.value
        calls = []
        catalog.register_catalog_reread(self.directory, calls.append)
        # An admitted catalog route whose profile the user never enabled.
        with self.assertRaises(BoardError) as disabled:
            self.continue_with(submitted, IDENTITY)
        gate = disabled.exception
        self.assertEqual(gate.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(gate.details.get("reason"), "not-enabled")
        self.assertFalse(gate.details.get("enabled"))
        self.assertEqual(gate.details.get("catalogReadAt"), read_at, "The refusal still names the catalog read time")
        self.assertNotEqual(gate.details.get("remedy"), catalog.CATALOG_REMEDY)
        self.assertIn("Enable", gate.details["remedy"])
        self.assertEqual(calls, [], "An admitted route is not re-read for the user gate")
        # A route missing from the catalog whose profile row the user enabled.
        override = {**IDENTITY, "model": "beta"}
        enable_fixture_configuration(self.board.store, override)
        with self.assertRaises(BoardError) as missing:
            self.continue_with(submitted, override, command_id="continue-2")
        absent = missing.exception
        self.assertEqual(absent.code, "CONFIGURATION_UNAVAILABLE")
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(absent.details.get("remedy"), catalog.CATALOG_REMEDY)
        self.assertNotIn("reason", absent.details)
        self.assertNotIn("enabled", absent.details)
        self.assertNotEqual(gate.message, absent.message)
        self.assertNotEqual(gate.details["remedy"], absent.details["remedy"])

    def test_refresh_never_flips_the_users_enabled_choice(self):
        submitted = self.seed()  # alpha is listed; both profiles start disabled.
        calls = []

        def reread(name):
            calls.append(name)
            self.clock.advance(60)
            self.evaluation.record_catalog(reading(models=('alpha', 'beta')))

        catalog.register_catalog_reread(self.directory, reread)
        override = {**IDENTITY, "model": "beta"}  # Absent now; the re-read will list it.
        with self.assertRaises(BoardError) as refused:
            self.continue_with(submitted, override)
        error = refused.exception
        self.assertEqual(calls, ["dsh"])
        self.assertEqual(error.details.get("reason"), "not-enabled",
                         "The refreshed catalog admitted the route; only the user gate refuses")
        self.assertEqual(error.details.get("catalogReadAt"), self.clock.value,
                         "The refusal names the refreshed reading's time")
        self.assertEqual(self.profile_enabled("dsh:fixture:beta:max"), False,
                         "The refresh that listed beta did not enable it")
        self.assertEqual(self.profile_enabled("dsh:fixture:alpha:max"), False)
        # Enabling it afterwards is the user's act, and only then does the override pass.
        self.enable_profile("dsh:fixture:beta:max")
        continued = self.continue_with(submitted, override, command_id="continue-2")
        self.assertEqual(continued["executionConfiguration"], override)
