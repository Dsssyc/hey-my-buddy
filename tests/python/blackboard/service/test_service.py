"""ADR-027 service wiring: the harness's account fact survives the refresh path."""
from types import SimpleNamespace
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.blackboard.catalog import catalog


def native_payload(account_status):
    discoveries = [{"adapter": "dsh", "status": "complete"}]
    if account_status is not None:
        discoveries[0]["accountStatus"] = account_status
    return {"source": "fixture-native", "providers": [{"adapter": "dsh", "provider": "fixture",
            "models": [{"id": "alpha", "efforts": ["max"], "available": True}]}],
            "discoveries": discoveries}


class CatalogRefreshServiceTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        self.board = self.board()
        self.service = self.board.service
        self.record = self.service.harnesses.get("dsh")

    def refresh_catalog(self, account_status):
        stub = SimpleNamespace(discover_models=lambda: native_payload(account_status),
                               local_read_only_check=lambda: {"eligible": False, "systemSandbox": False})
        with patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=stub):
            self.service._refresh_harness_catalog("dsh", self.record)

    def catalog_state(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE adapter='dsh'").fetchone()
            profiles = db.execute("SELECT profile_id FROM evaluation_profiles WHERE adapter='dsh'").fetchall()
            read_at = catalog.catalog_read_at(db, "dsh")
        return row, profiles, read_at

    def test_confirmed_account_fact_publishes_the_catalog(self):
        for account_status in ("confirmed", "not-applicable"):
            with self.subTest(accountStatus=account_status):
                self.refresh_catalog(account_status)
                row, profiles, _ = self.catalog_state()
                self.assertEqual(row["status"], "complete")
                self.assertIsNone(row["reason"])
                self.assertEqual([p["profile_id"] for p in profiles], ["dsh:fixture:alpha:max"])

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
        self.assertEqual(calls, [("dsh", {"force": True, "expected_binding": None})],
                         "A hook called without a claim binding refreshes unbound")


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
