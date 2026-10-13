"""ADR-027 service wiring: native reading and account facts survive refresh."""
from types import SimpleNamespace
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog, catalog_store


def native_payload(account_status, *, status="complete", reason=None, models=("alpha",)):
    discoveries = [{"adapter": "dsh", "status": status}]
    if account_status is not None:
        discoveries[0]["accountStatus"] = account_status
    if reason is not None:
        discoveries[0]["reason"] = reason
    return {"source": "fixture-native", "providers": [{"adapter": "dsh", "provider": "fixture",
            "models": [{"id": model, "efforts": ["max"], "available": True} for model in models]}],
            "discoveries": discoveries}


class CatalogRefreshServiceTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.service = self.board.service
        self.record = self.service.harnesses.get("dsh")

    def refresh_catalog(self, account_status, **reading):
        stub = SimpleNamespace(discover_models=lambda: native_payload(account_status, **reading),
                               local_read_only_check=lambda: {"eligible": False, "systemSandbox": False})
        with patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=stub):
            self.service._refresh_harness_catalog("dsh", self.record)

    def catalog_state(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE adapter='dsh'").fetchone()
            profiles = db.execute("SELECT profile_id FROM evaluation_profiles WHERE adapter='dsh'").fetchall()
            read_at = catalog.catalog_read_at(db, "dsh")
        return row, profiles, read_at

    def test_refresh_preserves_native_status_and_optional_reason(self):
        for status, account_status, reason in (("unknown", "confirmed", "native model shape unreadable"),
                                                ("unknown", "not-applicable", None),
                                                ("complete", "not-applicable", None),
                                                ("complete", "confirmed", "native discovery note")):
            with self.subTest(status=status, accountStatus=account_status, reason=reason):
                with patch.object(catalog_store, "record", wraps=catalog_store.record) as record:
                    self.refresh_catalog(account_status, status=status, reason=reason)
                observation = {"adapter": "dsh", "status": status, "accountStatus": account_status}
                if reason is not None:
                    observation["reason"] = reason
                self.assertEqual(record.call_args.args[1]["discoveries"], [observation])
                row, _, _ = self.catalog_state()
                self.assertEqual(row["status"], status)
                self.assertEqual(row["reason"], reason if status == "unknown" else None)

    def test_unknown_refresh_retains_catalog_without_confirming_model_absence(self):
        self.refresh_catalog("confirmed", models=("alpha", "beta"))
        _, _, confirmed_at = self.catalog_state()
        with self.board.store.db.read() as db:
            confirmed_discovery = db.execute("SELECT discovery_id FROM catalog_current WHERE adapter='dsh'").fetchone()[0]
            confirmed_profiles = [tuple(row) for row in db.execute("SELECT * FROM evaluation_profiles WHERE adapter='dsh'")]
        for elapsed in (1, catalog.CONFIRMATION_SECONDS + 1):
            self.clock.advance(elapsed)
            self.refresh_catalog("confirmed", status="unknown", reason="native model shape unreadable",
                                 models=("alpha", "untrusted"))
            row, _, read_at = self.catalog_state()
            self.assertEqual(row["status"], "unknown")
            self.assertEqual(row["reason"], "native model shape unreadable")
            self.assertEqual(read_at, confirmed_at)
            with self.board.store.db.read() as db:
                self.assertEqual(db.execute("SELECT discovery_id FROM catalog_current WHERE adapter='dsh'").fetchone()[0],
                                 confirmed_discovery)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM evaluation_profiles WHERE adapter='dsh'")],
                                 confirmed_profiles)
                self.assertEqual(catalog.pending_model_services(db), {})
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind LIKE 'catalog.model_%'").fetchone()[0], 0)

    def test_confirmed_account_fact_publishes_the_catalog(self):
        for account_status in ("confirmed", "not-applicable"):
            with self.subTest(accountStatus=account_status):
                self.refresh_catalog(account_status)
                row, profiles, _ = self.catalog_state()
                self.assertEqual(row["status"], "complete")
                self.assertIsNone(row["reason"])
                self.assertEqual([p["profile_id"] for p in profiles], ["dsh:fixture:alpha:max"])

    def test_complete_refresh_updates_catalog_and_confirms_model_absence(self):
        self.refresh_catalog("confirmed", models=("alpha", "beta"))
        self.clock.advance(1)
        self.refresh_catalog("confirmed", models=("alpha", "gamma"))
        with self.board.store.db.read() as db:
            self.assertEqual(catalog.pending_model_services(db), {("dsh", "fixture", "beta"): self.clock.value})
        self.clock.advance(catalog.CONFIRMATION_SECONDS + 1)
        self.refresh_catalog("confirmed", models=("alpha", "gamma"))
        row, profiles, read_at = self.catalog_state()
        self.assertEqual(row["status"], "complete")
        self.assertIsNone(row["reason"])
        self.assertEqual(read_at, self.clock.value)
        self.assertEqual({p["profile_id"] for p in profiles},
                         {"dsh:fixture:alpha:max", "dsh:fixture:beta:max", "dsh:fixture:gamma:max"})
        with self.board.store.db.read() as db:
            profiles = {p["model"]: p for p in db.execute("SELECT model,available,unavailable_reason FROM evaluation_profiles")}
            self.assertTrue(profiles["alpha"]["available"])
            self.assertTrue(profiles["gamma"]["available"])
            self.assertFalse(profiles["beta"]["available"])
            self.assertEqual(profiles["beta"]["unavailable_reason"], catalog.CONFIRMED_ABSENCE_REASON)
            self.assertEqual(catalog.pending_model_services(db), {})

    def test_refresh_without_own_observation_keeps_complete_and_unknown_account(self):
        payload = native_payload("confirmed")
        payload["discoveries"] = [{"adapter": "codex", "status": "unknown", "accountStatus": "confirmed",
                                   "reason": "another harness reading"}]
        stub = SimpleNamespace(discover_models=lambda: payload,
                               local_read_only_check=lambda: {"eligible": False, "systemSandbox": False})
        with patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=stub), \
             patch.object(catalog_store, "record", wraps=catalog_store.record) as record:
            self.service._refresh_harness_catalog("dsh", self.record)
        self.assertEqual(record.call_args.args[1]["discoveries"],
                         [{"adapter": "dsh", "status": "complete", "accountStatus": "unknown"}])
        row, profiles, read_at = self.catalog_state()
        self.assertEqual(row["status"], "unknown")
        self.assertEqual(row["reason"], "ACCOUNT_STATUS_UNKNOWN")
        self.assertEqual(profiles, [])
        self.assertIsNone(read_at)

    def test_missing_or_unknown_account_fact_replaces_nothing(self):
        for account_status in (None, "unknown"):
            with self.subTest(accountStatus=account_status):
                self.refresh_catalog(account_status)
                row, profiles, read_at = self.catalog_state()
                self.assertEqual(row["status"], "unknown")
                self.assertEqual(row["reason"], "ACCOUNT_STATUS_UNKNOWN")
                self.assertEqual(profiles, [], "No profiles are published from an untrusted reading")
                self.assertIsNone(read_at)

    def test_explicit_validation_reread_hook_uses_the_service_refresh(self):
        hook = catalog._catalog_reread.get(str(self.board.store.directory))
        self.assertIsNotNone(hook, "BoardService registers its own bounded re-read")
        refresh = self.service.harnesses.refresh
        calls = []

        def recording(name, **kwargs):
            calls.append((name, kwargs))
            return refresh(name, **kwargs)

        self.service.harnesses.refresh = recording
        try:
            hook("dsh")
        finally:
            self.service.harnesses.refresh = refresh
        self.assertEqual(calls, [("dsh", {"force": True, "catalog_scan_claimed": True})],
                         "A reread hook uses the claimed scan window")


class ConsoleSnapshotContractTests(BoardTestCase):
    """The named console_snapshot operation keeps its full C-Two shape.

    The console's periodic HTTP read moved to the slim projection in
    ``hey_my_buddy.console.reads``; callers of the operation itself (C-Two,
    CLI, probes) still get the task page and the backup preflight that were
    always part of this contract.
    """

    def test_operation_keeps_tasks_and_preflight(self):
        board = self.board()
        snapshot = board.call("console_snapshot", {})
        self.assertIn("runs", snapshot["tasks"])
        self.assertIn("total", snapshot["tasks"])
        self.assertIn("backupPreflight", snapshot)
        self.assertIn("harnesses", snapshot)
        self.assertIn("routingHealth", snapshot)
        self.assertTrue(snapshot["capabilities"]["consoleAssets"] is False or
                        snapshot["capabilities"]["consoleAssets"] is True)
        # The slim console core and this operation agree on every shared key.
        core = board.evaluation.console_core()
        for key in core:
            self.assertEqual(snapshot[key], core[key], key)
