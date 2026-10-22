"""ADR-027 service wiring: native reading and account facts survive refresh."""
import json
from types import SimpleNamespace
from unittest.mock import patch

from support import BoardTestCase, FakeClock
from hey_my_buddy.blackboard.catalog import catalog, catalog_store


def native_payload(account_status, *, status="complete", reason=None, models=("alpha",)):
    discoveries = [{"harness": "dsh", "status": status}]
    if account_status is not None:
        discoveries[0]["accountStatus"] = account_status
    if reason is not None:
        discoveries[0]["reason"] = reason
    return {"source": "fixture-native", "providers": [{"harness": "dsh", "provider": "fixture",
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
        stub = SimpleNamespace(discover_models=lambda: native_payload(account_status, **reading))
        with patch("hey_my_buddy.buddy.harnesses.registry.adapter", return_value=stub):
            self.service._refresh_harness_catalog("dsh", self.record)

    def catalog_state(self):
        with self.board.store.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE harness='dsh'").fetchone()
            buddies = db.execute("SELECT buddy_id FROM buddies WHERE harness='dsh'").fetchall()
            read_at = catalog.catalog_read_at(db, "dsh")
        row = dict(row)
        row['reason'] = json.loads(row['reason']) if row['reason'] is not None else None
        return row, buddies, read_at

    def test_refresh_preserves_native_status_and_optional_reason(self):
        for status, account_status, reason in (("unknown", "confirmed", {"code": "catalog.model-shape-unreadable", "params": {}}),
                                                ("unknown", "not-applicable", None),
                                                ("complete", "not-applicable", None),
                                                ("complete", "confirmed", None)):
            with self.subTest(status=status, accountStatus=account_status, reason=reason):
                with patch.object(catalog_store, "record", wraps=catalog_store.record) as record:
                    self.refresh_catalog(account_status, status=status, reason=reason)
                observation = {"harness": "dsh", "status": status, "accountStatus": account_status,
                               "reason": reason if status == "unknown" else None, "content": None}
                self.assertEqual(record.call_args.args[1]["discoveries"], [observation])
                row, _, _ = self.catalog_state()
                self.assertEqual(row["status"], status)
                expected = (reason or {"code": "catalog.native-unknown", "params": {"harness": "dsh"}})
                self.assertEqual(row["reason"], expected if status == "unknown" else None)

    def test_unknown_refresh_retains_catalog_without_confirming_model_absence(self):
        self.refresh_catalog("confirmed", models=("alpha", "beta"))
        _, _, confirmed_at = self.catalog_state()
        with self.board.store.db.read() as db:
            confirmed_discovery = db.execute("SELECT discovery_id FROM catalog_current WHERE harness='dsh'").fetchone()[0]
            confirmed_buddies = [tuple(row) for row in db.execute("SELECT * FROM buddies WHERE harness='dsh'")]
        for elapsed in (1, catalog.CONFIRMATION_SECONDS + 1):
            self.clock.advance(elapsed)
            self.refresh_catalog("confirmed", status="unknown", reason={"code": "catalog.model-shape-unreadable", "params": {}},
                                 models=("alpha", "untrusted"))
            row, _, read_at = self.catalog_state()
            self.assertEqual(row["status"], "unknown")
            self.assertEqual(row["reason"], {"code": "catalog.model-shape-unreadable", "params": {}})
            self.assertEqual(read_at, confirmed_at)
            with self.board.store.db.read() as db:
                self.assertEqual(db.execute("SELECT discovery_id FROM catalog_current WHERE harness='dsh'").fetchone()[0],
                                 confirmed_discovery)
                self.assertEqual([tuple(row) for row in db.execute("SELECT * FROM buddies WHERE harness='dsh'")],
                                 confirmed_buddies)
                self.assertEqual(catalog.pending_model_services(db), {})
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind LIKE 'catalog.model_%'").fetchone()[0], 0)

    def test_confirmed_account_fact_publishes_the_catalog(self):
        for account_status in ("confirmed", "not-applicable"):
            with self.subTest(accountStatus=account_status):
                self.refresh_catalog(account_status)
                row, buddies, _ = self.catalog_state()
                self.assertEqual(row["status"], "complete")
                self.assertIsNone(row["reason"])
                self.assertEqual([p["buddy_id"] for p in buddies], ["dsh:fixture:alpha:max"])

    def test_complete_refresh_updates_catalog_and_confirms_model_absence(self):
        self.refresh_catalog("confirmed", models=("alpha", "beta"))
        self.clock.advance(1)
        self.refresh_catalog("confirmed", models=("alpha", "gamma"))
        with self.board.store.db.read() as db:
            self.assertEqual(catalog.pending_model_services(db), {("dsh", "fixture", "beta"): self.clock.value})
        self.clock.advance(catalog.CONFIRMATION_SECONDS + 1)
        self.refresh_catalog("confirmed", models=("alpha", "gamma"))
        row, buddies, read_at = self.catalog_state()
        self.assertEqual(row["status"], "complete")
        self.assertIsNone(row["reason"])
        self.assertEqual(read_at, self.clock.value)
        self.assertEqual({p["buddy_id"] for p in buddies},
                         {"dsh:fixture:alpha:max", "dsh:fixture:beta:max", "dsh:fixture:gamma:max"})
        with self.board.store.db.read() as db:
            buddies = {p["model"]: p for p in db.execute("SELECT model,available,unavailable_code FROM buddies")}
            self.assertTrue(buddies["alpha"]["available"])
            self.assertTrue(buddies["gamma"]["available"])
            self.assertFalse(buddies["beta"]["available"])
            self.assertEqual(buddies["beta"]["unavailable_code"], catalog.CONFIRMED_ABSENCE_REASON)
            self.assertEqual(catalog.pending_model_services(db), {})

    def test_missing_or_unknown_account_fact_replaces_nothing(self):
        for account_status in (None, "unknown"):
            with self.subTest(accountStatus=account_status):
                self.refresh_catalog(account_status)
                row, buddies, read_at = self.catalog_state()
                self.assertEqual(row["status"], "unknown")
                self.assertEqual(row["reason"], {"code": "catalog.account-status-unknown", "params": {"harness": "dsh"}})
                self.assertEqual(buddies, [], "No buddies are published from an untrusted reading")
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
    CLI, probes) still get the task page that was part of this contract.
    """

    def test_operation_keeps_tasks(self):
        board = self.board()
        snapshot = board.call("console_snapshot", {})
        self.assertIn("runs", snapshot["tasks"])
        self.assertIn("total", snapshot["tasks"])
        self.assertIn("harnesses", snapshot)
        self.assertIn("routingHealth", snapshot)
        self.assertTrue(snapshot["capabilities"]["consoleAssets"] is False or
                        snapshot["capabilities"]["consoleAssets"] is True)
        # The slim console core and this operation agree on every shared key.
        core = board.evaluation.console_core()
        for key in core:
            self.assertEqual(snapshot[key], core[key], key)
