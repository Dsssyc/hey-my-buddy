"""Discovery generations, expiry, settings and cache reads use private boards."""
from unittest.mock import patch

from hey_my_buddy.blackboard.service.harness_health import HarnessHealth
from support import BoardTestCase


class HarnessHealthTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record: self.refreshed.append((name, record)))
        self.health.initialize()
        self.snapshot = {'paths': [{'path': '/native/codex', 'mtimeNs': 1}]}
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True}
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def test_cache_reads_do_not_discover_and_refresh_is_throttled(self):
        self.assertEqual(self.health.get('codex')['status'], 'unknown')
        self.native.assert_not_called()
        result = self.health.refresh('codex')
        self.assertTrue(result['available'])
        self.health.get('codex')
        self.health.refresh('codex')
        self.native.assert_called_once()
        self.assertEqual(len(self.refreshed), 1)

    def test_caller_path_is_a_transient_hint_and_never_stored(self):
        self.health.add_path_hint('/private/new-cli:/other/bin:relative')
        self.health.refresh('codex', force=True)
        environment = self.native.call_args.args[2]
        self.assertTrue(environment['PATH'].startswith('/private/new-cli:/other/bin:'))
        with self.board.db.read() as db:
            stored = db.execute("SELECT record_json FROM harness_health WHERE adapter='codex'").fetchone()[0]
        self.assertNotIn('/private/new-cli', stored)

    def test_preflight_checks_fingerprints_without_reprobing_unchanged_binary(self):
        self.health.refresh('codex')
        self.health.refresh('codex', preflight=True)
        self.native.assert_called_once()
        self.snapshot = {'paths': [{'path': '/native/codex', 'mtimeNs': 2}]}
        self.discovered = {**self.discovered, 'version': '2.0'}
        self.assertEqual(self.health.refresh('codex', preflight=True)['version'], '2.0')
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(self.refreshed), 2)

    def test_negative_auth_observation_expires_even_without_file_changes(self):
        self.discovered = {'adapter': 'codex', 'status': 'login-required', 'reasonCode': 'HARNESS_LOGIN_REQUIRED'}
        self.assertFalse(self.health.refresh('codex')['available'])
        self.health.refresh('codex')
        self.native.assert_called_once()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET expires_at='2000',scan_after='2000' WHERE adapter='codex'")
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'version': '1.0'}
        self.assertTrue(self.health.refresh('codex')['available'])
        self.assertEqual(self.native.call_count, 2)

    def test_late_discovery_cannot_overwrite_new_manual_generation(self):
        def replaced(*args):
            with self.board.db.write() as db:
                db.execute("UPDATE harness_health SET manual_path='/new/codex',revision=revision+1 WHERE adapter='codex'")
            return self.discovered
        self.native.side_effect = replaced
        result = self.health.refresh('codex')
        self.assertEqual(result['manualPath'], '/new/codex')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.refreshed, [])

    def test_manual_path_compare_and_swap_does_not_invoke_shell(self):
        from hey_my_buddy.errors import BoardError
        self.health.set_path('codex', '/native/new', expected_revision=0)
        with self.assertRaises(BoardError) as error:
            self.health.set_path('codex', '/native/stale', expected_revision=0)
        self.assertEqual(error.exception.code, 'REVISION_CONFLICT')
        self.assertEqual(self.health.get('codex')['manualPath'], '/native/new')
