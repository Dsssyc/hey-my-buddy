"""Discovery generations, expiry, settings and cache reads use private boards."""
from datetime import datetime, timedelta, timezone
import os
import threading
from unittest.mock import patch

from hey_my_buddy.blackboard.service.harness_health import HarnessHealth, MAX_PATH_HINTS, _later
from support import BoardTestCase, FakeClock


class HarnessHealthTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record, catalog_scan_claimed=False: self.refreshed.append((name, record)))
        self.health.initialize()
        self.snapshot = {'adapter': 'codex', 'managerRecords': [], 'candidates': [
            {'path': '/native/codex', 'exists': True, 'fingerprint': {'mtimeNs': 1}}]}
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True}
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None, None)))
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
        cli_dir = self.directory / 'new-cli'
        node_dir = self.directory / 'other-bin'
        cli_dir.mkdir()
        node_dir.mkdir()
        (cli_dir / 'codex').write_text('fixture')
        (node_dir / 'node').write_text('fixture')
        self.health.add_path_hint(os.pathsep.join([str(cli_dir), str(node_dir), 'relative']))
        self.health.refresh('codex', force=True)
        environment = self.native.call_args.args[2]
        self.assertTrue(environment['PATH'].startswith(os.pathsep.join([str(cli_dir), str(node_dir)]) + os.pathsep))
        with self.board.db.read() as db:
            stored = db.execute("SELECT record_json FROM harness_health WHERE adapter='codex'").fetchone()[0]
        self.assertNotIn(str(cli_dir), stored)

    def test_path_hints_keep_first_seen_order_and_ignore_empty_directories_at_the_limit(self):
        directories = []
        for index in range(MAX_PATH_HINTS + 1):
            directory = self.directory / f'cli-{index}'
            directory.mkdir()
            (directory / ('node' if index % 2 else 'codex')).write_text('fixture')
            directories.append(str(directory))
        empty = [str(self.directory / f'empty-{index}') for index in range(100)]
        unrelated = self.directory / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'other-tool').write_text('fixture')
        (unrelated / 'codex').mkdir()
        self.health.add_path_hint(os.pathsep.join([*empty, str(unrelated), *directories]))
        self.assertEqual(self.health._path_hints, tuple(directories[:MAX_PATH_HINTS]))
        self.health.add_path_hint(os.pathsep.join(reversed(directories)))
        self.assertEqual(self.health._path_hints, tuple(directories[:MAX_PATH_HINTS]))

    def test_reversed_caller_path_appends_new_candidates_without_reordering_known_directories(self):
        directories = []
        for name in ('a', 'b', 'c'):
            directory = self.directory / name
            directory.mkdir()
            if name != 'c':
                (directory / 'codex').write_text('fixture')
            directories.append(str(directory))
        self.health.add_path_hint(os.pathsep.join(directories[:2]))
        self.health.add_path_hint(os.pathsep.join([directories[2], directories[1], directories[0]]))
        self.assertEqual(self.health._path_hints, tuple(directories[:2]))
        (self.directory / 'c/codex').write_text('appeared')
        self.health.add_path_hint(os.pathsep.join([directories[2], directories[1], directories[0]]))
        self.assertEqual(self.health._path_hints, tuple(directories))

    def test_alternating_empty_path_hints_do_not_rebuild_health_or_reread_catalog(self):
        from hey_my_buddy.buddy.harnesses import discovery
        self.scan.stop()
        cli = self.directory / 'bin/codex'
        cli.parent.mkdir()
        cli.write_text('fixture')
        other = self.directory / 'empty'
        paths = [str(cli.parent), str(other) + ':' + str(cli.parent),
                 ':'.join([*(str(self.directory / f'empty-{index}') for index in range(100)),
                           str(cli.parent)])]
        with patch.object(discovery, '_home', return_value=self.directory), \
             patch.object(discovery, '_common_dirs', return_value=[]), \
             patch.object(discovery, '_app_paths', return_value=[]), \
             patch.dict('os.environ', {'PATH': ''}):
            self.health.add_path_hint(paths[0])
            first = self.health.refresh('codex')
            for path in [paths[1], paths[0], paths[2], paths[0], paths[1], paths[0]]:
                with self.board.db.write() as db:
                    db.execute("UPDATE harness_health SET scan_after=NULL WHERE adapter='codex'")
                self.health.add_path_hint(path)
                current = self.health.refresh('codex')
                self.assertEqual(current['revision'], first['revision'])
                self.assertEqual(current['checkedAt'], first['checkedAt'])
                self.assertEqual(current['locationFingerprint'], first['locationFingerprint'])
        self.native.assert_called_once()
        self.assertEqual(len(self.refreshed), 1)

    def test_alternating_callers_with_distinct_executables_do_not_rebuild_health_or_reread_catalog(self):
        from hey_my_buddy.buddy.harnesses import discovery
        self.scan.stop()
        directories = [self.directory / name for name in ('caller-a', 'caller-b')]
        for directory in directories:
            directory.mkdir()
            (directory / 'codex').write_text('fixture')
        with patch.object(discovery, '_home', return_value=self.directory), \
             patch.object(discovery, '_common_dirs', return_value=[]), \
             patch.object(discovery, '_app_paths', return_value=[]), \
             patch.dict('os.environ', {'PATH': ''}):
            for directory in directories:
                self.health.add_path_hint(str(directory))
            first = self.health.refresh('codex')
            for directory in [directories[1], directories[0], directories[1], directories[0]]:
                with self.board.db.write() as db:
                    db.execute("UPDATE harness_health SET scan_after=NULL WHERE adapter='codex'")
                self.health.add_path_hint(os.pathsep.join([str(self.directory / 'temporary-empty'), str(directory)]))
                current = self.health.refresh('codex')
                self.assertEqual(current['revision'], first['revision'])
                self.assertEqual(current['checkedAt'], first['checkedAt'])
                self.assertEqual(current['locationFingerprint'], first['locationFingerprint'])
        self.native.assert_called_once()
        self.assertEqual(len(self.refreshed), 1)

    def test_location_fingerprint_ignores_absent_candidates_without_rereading_catalog(self):
        from hey_my_buddy.buddy.harnesses import discovery
        self.scan.stop()
        binaries = [self.directory / name for name in ('bin-a', 'bin-b')]
        empty = [self.directory / name for name in ('empty-a', 'empty-b')]
        missing = [self.directory / name for name in ('missing-a', 'missing-b')]
        for directory in [*binaries, *empty]:
            directory.mkdir()
        for directory in binaries:
            executable = directory / 'codex'
            executable.write_text('fixture')
            executable.chmod(0o755)
        caller_paths = [
            [empty[0], binaries[0], missing[0], binaries[1]],
            [missing[1], binaries[0], empty[1], binaries[1], missing[0], empty[0]],
        ]
        environments = [{'PATH': os.pathsep.join(map(str, paths))} for paths in caller_paths]
        # Supply caller PATHs directly: add_path_hint already filters empty
        # directories and would hide a regression in the service fingerprint.
        with patch.object(discovery, '_home', return_value=self.directory), \
             patch.object(discovery, '_common_dirs', return_value=[]), \
             patch.object(discovery, '_app_paths', return_value=[]), \
             patch.object(discovery, '_registry_paths', return_value=[]), \
             patch.object(self.health, 'environment', return_value=environments[0]) as environment:
            self.assertNotEqual(
                discovery.candidate_snapshot('codex', environment=environments[0]),
                discovery.candidate_snapshot('codex', environment=environments[1]))
            first = self.health.refresh('codex', preflight=True)
            environment.return_value = environments[1]
            second = self.health.refresh('codex', preflight=True)
            self.assertEqual(second['locationFingerprint'], first['locationFingerprint'])
            self.assertEqual(second['revision'], first['revision'])
            self.native.assert_called_once()
            self.assertEqual(len(self.refreshed), 1)

            added = self.directory / 'bin-c'
            added.mkdir()
            executable = added / 'codex'
            executable.write_text('fixture')
            executable.chmod(0o755)
            environment.return_value = {'PATH': os.pathsep.join([str(added), environments[1]['PATH']])}
            third = self.health.refresh('codex', preflight=True)
            self.assertNotEqual(third['locationFingerprint'], first['locationFingerprint'])
            self.assertEqual(third['revision'], first['revision'] + 1)
            self.assertEqual(self.native.call_count, 2)
            self.assertEqual(len(self.refreshed), 2)

    def test_preflight_checks_fingerprints_without_reprobing_unchanged_binary(self):
        self.health.refresh('codex')
        self.health.refresh('codex', preflight=True)
        self.native.assert_called_once()
        self.snapshot['candidates'][0]['fingerprint'] = {'mtimeNs': 2}
        self.discovered = {**self.discovered, 'version': '2.0'}
        self.assertEqual(self.health.refresh('codex', preflight=True)['version'], '2.0')
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(self.refreshed), 2)

    def test_stat_change_reprobes_without_advancing_unchanged_catalog_revision(self):
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        evaluation = EvaluationStore(self.board)
        readings = []
        payload = {'source': 'stat-change-fixture',
                   'discoveries': [{'adapter': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}],
                   'providers': [{'adapter': 'codex', 'provider': 'fixture', 'models': [
                       {'id': 'alpha', 'efforts': ['high']}]}]}
        self.health.catalog_refresh = lambda name, record, catalog_scan_claimed=False: readings.append(
            evaluation.record_catalog(payload))
        self.health.refresh('codex', preflight=True)
        self.snapshot['candidates'][0]['fingerprint'] = {'mtimeNs': 2}
        self.health.refresh('codex', preflight=True)
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(readings), 2)
        self.assertGreater(readings[1]['observationId'], readings[0]['observationId'])
        self.assertEqual(readings[1]['tableRevision'], readings[0]['tableRevision'])

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


def _stamp(seconds_ago: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    return moment.isoformat(timespec='milliseconds').replace('+00:00', 'Z')


class CatalogShelfLifeTests(BoardTestCase):
    """ADR-027 rule 4 (C06): health checks re-read a stale catalog on every path."""

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.board.evaluation = EvaluationStore(self.board)
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record, catalog_scan_claimed=False: self.refreshed.append(name))
        self.health.initialize()
        self.snapshot = {'adapter': 'codex', 'managerRecords': [], 'candidates': [
            {'path': '/native/codex', 'exists': True, 'fingerprint': {'mtimeNs': 1}}]}
        self.discovered = {'adapter': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True}
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None, None)))
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def set_read_at(self, seconds_ago):
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-read-at:codex',?) "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (_stamp(seconds_ago),))

    def pass_throttles(self):
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000' WHERE adapter='codex'")

    def expire_catalog_throttle(self):
        """The bounded catalog re-read cadence (SCAN_SECONDS) has passed."""
        with self.board.db.write() as db:
            db.execute("DELETE FROM meta WHERE key='catalog-scan-after:codex'")

    def record_native(self, account_status, *, observed_seconds_ago=None):
        from hey_my_buddy.blackboard.catalog import catalog_store
        payload = {'source': 'fixture-native',
                   'providers': [{'adapter': 'codex', 'provider': 'openai',
                                  'models': [{'id': 'sol', 'efforts': ['high'], 'available': True}]}],
                   'discoveries': [{'adapter': 'codex', 'status': 'complete', 'accountStatus': account_status}]}
        catalog_store.record(self.board.evaluation, payload)
        if observed_seconds_ago is not None:
            with self.board.db.write() as db:
                db.execute("UPDATE catalog_current SET updated_at=? WHERE adapter='codex'", (_stamp(observed_seconds_ago),))

    def catalog_state(self):
        with self.board.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE adapter='codex'").fetchone()
            profiles = db.execute("SELECT profile_id, available FROM evaluation_profiles WHERE adapter='codex'").fetchall()
            read_at = db.execute("SELECT value FROM meta WHERE key='catalog-read-at:codex'").fetchone()
        return row, profiles, (read_at[0] if read_at else None)

    def test_throttled_scan_still_rereads_a_stale_catalog_once(self):
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        # scanAfter stays in the future: no artificial throttle bypass.
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        result = self.health.refresh('codex')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.native.call_count, 1, 'A throttled scan reprobes nothing')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'The stale catalog is re-read once')
        # The re-read cadence stays bounded: while the catalog-scan window from
        # that re-read is open, an immediate call does not spawn again.
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_fresh_catalog_read_keeps_the_throttled_path_quiet(self):
        self.health.refresh('codex')
        first = list(self.refreshed)
        self.set_read_at(60)
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.pass_throttles()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, first)

    def test_a_reprobed_unchanged_harness_rereads_only_past_the_shelf_life(self):
        self.health.refresh('codex')
        self.set_read_at(60)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.native.call_count, 2, 'The expired record reprobes the harness')
        self.assertEqual(self.refreshed, ['codex'], 'A fresh catalog needs no re-read')
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_unparseable_read_time_counts_as_expired(self):
        self.health.refresh('codex')
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('catalog-read-at:codex','not-a-time') "
                       "ON CONFLICT(key) DO UPDATE SET value=excluded.value")
        self.expire_catalog_throttle()
        self.pass_throttles()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_cold_start_unknown_reading_still_expires_by_observation_time(self):
        # A full but account-unknown reading seven hours ago leaves no confirmed
        # read time; the observation time must still bring the next health check
        # to re-read the catalog instead of keeping the cold-start catalog forever.
        self.record_native('unknown', observed_seconds_ago=7 * 3600)
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.expire_catalog_throttle()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE adapter='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.native.call_count, 2, 'The expired record reprobes the harness')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'A never-confirmed catalog is re-read')

    def test_cold_start_unknown_then_confirmed_publishes_the_catalog(self):
        self.record_native('unknown', observed_seconds_ago=7 * 3600)

        def confirmed_on_reread(name, record, catalog_scan_claimed=False):
            self.refreshed.append(name)
            if len(self.refreshed) > 1:
                self.record_native('confirmed')

        self.health.catalog_refresh = confirmed_on_reread
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'The stale cold-start catalog is re-read')
        row, profiles, read_at = self.catalog_state()
        self.assertEqual(row['status'], 'complete')
        self.assertEqual([p['profile_id'] for p in profiles], ['codex:openai:sol:high'])
        self.assertTrue(profiles[0]['available'])
        self.assertIsNotNone(read_at, 'The confirmed re-read parks the shelf clock')
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'A confirmed catalog needs no further re-read')

    def test_stale_reread_failure_returns_the_post_callback_record(self):
        # Host reproduction: the health scan stays throttled, the catalog is past
        # its shelf life, and the bounded re-read's native failure invalidates
        # the harness. refresh must answer with the record after the callback,
        # never with the stale pre-reread ready record.
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()

        def failing_refresh(name, record, catalog_scan_claimed=False):
            self.refreshed.append(name)
            self.health.invalidate(name, record['revision'], 'CATALOG_UNAVAILABLE')

        self.health.catalog_refresh = failing_refresh
        result = self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])
        self.assertFalse(result['available'], 'The post-callback record leads')
        self.assertEqual(result['reasonCode'], 'CATALOG_UNAVAILABLE')
        self.assertEqual(self.health.get('codex')['revision'], result['revision'])


class SharedStaleCatalogFlightTests(BoardTestCase):
    """A health stale read and an explicit validation re-read share one flight.

    Both go through the same claim of the ``catalog-scan-after`` window, so one
    harness gets one bounded native read per window however it was triggered:
    the explicit side boundedly joins a health read in flight, and a health scan
    that finds an explicit read in flight starts none of its own.
    """

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        self.clock = FakeClock()
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.board.evaluation = EvaluationStore(self.board, clock=self.clock)
        self.evaluation = self.board.evaluation
        self.ready = {'adapter': 'dsh', 'status': 'ready', 'version': '1.0',
                      'command': ['/fixture/dsh'], 'executable': '/fixture/dsh'}
        for target, result in (('_snapshot', {'adapter': 'dsh', 'managerRecords': [], 'candidates': []}),
                               ('_discover', self.ready)):
            stub = patch('hey_my_buddy.blackboard.service.harness_health.' + target, return_value=result)
            stub.start()
            self.addCleanup(stub.stop)

    def reading(self, models):
        return {'source': 'fixture-native',
                'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
                'providers': [{'adapter': 'dsh', 'provider': 'fixture',
                               'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}]}

    def prime_ready_health(self, health):
        """One ready health record plus a shelf-expired catalog and an open window."""
        self.evaluation.record_catalog(self.reading(('alpha',)))
        health.refresh('dsh', force=True)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=? WHERE adapter='dsh'", (_later(3600),))
            db.execute("DELETE FROM meta WHERE key='catalog-scan-after:dsh'")
        # The confirmed read time stays at the FakeClock epoch, far in the past.

    def test_an_explicit_request_joins_a_health_stale_read_in_flight(self):
        from hey_my_buddy.blackboard.catalog import catalog
        quiet = []
        health = HarnessHealth(self.board, catalog_refresh=lambda name, record, catalog_scan_claimed=False: quiet.append(name))
        health.initialize()
        self.prime_ready_health(health)
        refreshed, entered, release = [], threading.Event(), threading.Event()

        def slow_refresh(name, record, catalog_scan_claimed=False):
            refreshed.append(name)
            entered.set()
            self.assertTrue(release.wait(timeout=10), "The parked health read was never released")
            self.evaluation.record_catalog(self.reading(('alpha', 'beta')))

        health.catalog_refresh = slow_refresh
        hook_calls = []
        catalog.register_catalog_reread(self.board.directory, lambda name: hook_calls.append(name))
        self.addCleanup(lambda: catalog.register_catalog_reread(self.board.directory, None))
        target = {'adapter': 'dsh', 'provider': 'fixture', 'model': 'beta', 'effort': 'max'}
        outcome, error = {}, {}

        def request():
            try:
                outcome['value'] = catalog.validate_configuration(target, directory=self.board.directory)
            except Exception as caught:  # noqa: BLE001 - recorded for the assertion below
                error['value'] = caught

        scanner = threading.Thread(target=lambda: health.refresh('dsh'))
        scanner.start()
        self.assertTrue(entered.wait(timeout=10), "The health stale read never started")
        validator = threading.Thread(target=request)
        validator.start()
        validator.join(timeout=0.3)
        self.assertTrue(validator.is_alive(), "The explicit request waits for the health read in flight")
        release.set()
        scanner.join(timeout=10)
        validator.join(timeout=10)
        self.assertFalse(scanner.is_alive() or validator.is_alive())
        self.assertEqual(error, {}, "The joining request cannot reject while the shared read is in flight")
        self.assertEqual(outcome.get('value'), target, "The joined facts admit the route")
        self.assertEqual(refreshed, ['dsh'], "Exactly one native catalog read ran")
        self.assertEqual(hook_calls, [], "The explicit side started no native read of its own")
        self.assertEqual(quiet, ['dsh'], "Priming ran its own quiet read once")

    def test_a_health_stale_scan_skips_when_an_explicit_read_is_in_flight(self):
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.errors import BoardError
        quiet = []
        health = HarnessHealth(self.board, catalog_refresh=lambda name, record, catalog_scan_claimed=False: quiet.append(name))
        health.initialize()
        self.prime_ready_health(health)
        hook_calls, entered, release = [], threading.Event(), threading.Event()

        def slow_hook(name):
            hook_calls.append(name)
            entered.set()
            self.assertTrue(release.wait(timeout=10), "The parked explicit read was never released")
            self.evaluation.record_catalog(self.reading(('alpha', 'beta')))

        catalog.register_catalog_reread(self.board.directory, slow_hook)
        self.addCleanup(lambda: catalog.register_catalog_reread(self.board.directory, None))
        target = {'adapter': 'dsh', 'provider': 'fixture', 'model': 'beta', 'effort': 'max'}
        outcome, error = {}, {}

        def request():
            try:
                outcome['value'] = catalog.validate_configuration(target, directory=self.board.directory)
            except BoardError as caught:
                error['value'] = caught

        # The scan passes its staleness check first and pauses; the explicit
        # request claims and parks; the scan then resumes and must start no
        # second native read even though its check already said yes.
        from hey_my_buddy.blackboard.store.db import utc_now
        was_stale = health._stale_catalog('dsh', utc_now())
        self.assertTrue(was_stale)
        health._stale_catalog = lambda name, now: entered.wait(timeout=10) and was_stale
        validator = threading.Thread(target=request)
        validator.start()
        self.assertTrue(entered.wait(timeout=10), "The explicit read never started")
        scanned = health.refresh('dsh')
        self.assertTrue(scanned['available'])
        self.assertEqual(quiet, ['dsh'], "Only the priming read ran; the stale scan started none")
        release.set()
        validator.join(timeout=10)
        self.assertFalse(validator.is_alive())
        self.assertEqual(error, {})
        self.assertEqual(outcome.get('value'), target)
        self.assertEqual(hook_calls, ['dsh'], "Exactly one native catalog read ran, on the explicit side")


class HealthRecoveryRestoreTests(BoardTestCase):
    """The direct restore on health recovery, with no catalog publication behind it.

    The publication layer (a trusted reading upserting its rows, or an unknown
    reading calling the same restore) is the other restorer; this is the one
    that runs when recovery publishes nothing, inside the health write itself.
    """

    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.health = HarnessHealth(self.board.store, catalog_refresh=None)
        self.ready = {'adapter': 'dsh', 'status': 'ready', 'version': '1.0', 'command': ['/fixture/dsh']}
        for target, result in (
            ('_snapshot', {'adapter': 'dsh', 'managerRecords': [], 'candidates': []}),
            ('_discover', self.ready),
        ):
            stub = patch('hey_my_buddy.blackboard.service.harness_health.' + target, return_value=result)
            stub.start()
            self.addCleanup(stub.stop)

    def reading(self, efforts):
        models = [{'id': 'alpha', 'efforts': list(efforts), 'available': True},
                  {'id': 'gamma', 'efforts': ['max'], 'available': False, 'unavailableReason': 'native-disabled'}]
        return {'source': 'fixture-native', 'providers': [{'adapter': 'dsh', 'provider': 'fixture', 'models': models}],
                'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

    def profiles(self):
        from hey_my_buddy.blackboard.catalog import catalog_store
        return {item['profileId']: item for item in catalog_store.profiles(
            self.evaluation, {'includeUnavailable': True})['profiles']}

    def test_recovery_restores_the_retained_catalog_without_any_publication(self):
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        self.evaluation.record_catalog(self.reading(('max', 'high')))
        self.evaluation.record_catalog(self.reading(('max',)))  # alpha:high retires
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.health.invalidate('dsh', self.health.get('dsh')['revision'], 'HARNESS_HANDSHAKE_FAILED')
        broken = self.profiles()['dsh:fixture:alpha:max']
        self.assertFalse(broken['available'], 'The health failure zeroed the retained route')
        self.assertEqual(broken.get('unavailableReason'), 'HARNESS_HANDSHAKE_FAILED')
        self.assertTrue(self.health.refresh('dsh', force=True)['available'])
        profiles = self.profiles()
        self.assertTrue(profiles['dsh:fixture:alpha:max']['available'],
                        'Recovery itself restores the retained reading, before any publication')
        self.assertFalse(profiles['dsh:fixture:alpha:high']['available'], 'A retired effort is a catalog fact')
        self.assertEqual(profiles['dsh:fixture:alpha:high']['unavailableReason'], catalog.RETIRED_EFFORT_REASON)
        self.assertFalse(profiles['dsh:fixture:gamma:max']['available'], 'A native unavailable declaration stays down')
        with self.board.store.db.read() as db:
            self.assertEqual([row['profile_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)],
                             ['dsh:fixture:alpha:max'])


class CatalogHealthOwnershipTests(BoardTestCase):
    """Every health write preserves adopted catalog facts, including NULL rows."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.health = HarnessHealth(self.board.store, catalog_refresh=self.unknown_read)
        self.ready = {'adapter': 'dsh', 'status': 'ready', 'version': '1.0', 'command': ['/fixture/dsh']}
        for target, result in (
            ('_snapshot', {'adapter': 'dsh', 'managerRecords': [], 'candidates': []}),
            ('_discover', self.ready),
        ):
            stub = patch('hey_my_buddy.blackboard.service.harness_health.' + target, return_value=result)
            stub.start()
            self.addCleanup(stub.stop)
        self.evaluation.record_catalog(self.reading(('high', 'low'), beta=True))
        self.evaluation.record_catalog(self.reading(('high',), beta=False))
        self.clock.advance(3601)
        self.evaluation.record_catalog(self.reading(('high',), beta=False))
        with self.board.store.db.write() as db:
            db.execute("UPDATE evaluation_profiles SET enabled=1 WHERE adapter='dsh'")
        self.before = self.profiles()

    def reading(self, efforts, *, beta):
        models = [{'id': 'alpha', 'efforts': list(efforts), 'available': True},
                  {'id': 'gamma', 'efforts': ['high'], 'available': False, 'unavailableReason': 'native-disabled'}]
        if beta:
            models.append({'id': 'beta', 'efforts': ['high'], 'available': True})
        return {'source': 'fixture-native', 'providers': [{'adapter': 'dsh', 'provider': 'fixture', 'models': models}],
                'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

    def unknown_read(self, *args, catalog_scan_claimed=False):
        self.evaluation.record_catalog({'source': 'fixture-native', 'providers': [],
            'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'unknown'}]})

    def profiles(self):
        from hey_my_buddy.blackboard.catalog import catalog_store
        return {item['profileId']: item for item in catalog_store.profiles(
            self.evaluation, {'includeUnavailable': True})['profiles']}

    def assert_catalog_survives(self):
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        profiles = self.profiles()
        self.assertTrue(profiles['dsh:fixture:alpha:high']['available'])
        self.assertFalse(profiles['dsh:fixture:alpha:low']['available'])
        self.assertEqual(profiles['dsh:fixture:alpha:low']['unavailableReason'], catalog.RETIRED_EFFORT_REASON)
        self.assertFalse(profiles['dsh:fixture:beta:high']['available'])
        self.assertEqual(profiles['dsh:fixture:beta:high']['catalogStatus'], 'unavailable')
        self.assertEqual(profiles['dsh:fixture:beta:high']['unavailableReason'], catalog.CONFIRMED_ABSENCE_REASON)
        self.assertFalse(profiles['dsh:fixture:gamma:high']['available'], 'Native unavailable stays grounded')
        self.assertTrue(all(item['enabled'] for item in profiles.values()))
        self.assertEqual(set(profiles), set(self.before), 'Profile identities survive')
        with self.board.store.db.read() as db:
            candidates = [row['profile_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
            transitions = [row[0] for row in db.execute("SELECT kind FROM events WHERE kind LIKE 'catalog.model_%' ORDER BY seq")]
        self.assertEqual(candidates, ['dsh:fixture:alpha:high'])
        self.assertEqual(transitions, ['catalog.model_pending', 'catalog.model_unavailable'])

    def assert_not_checked_keeps_catalog_reasons(self):
        from hey_my_buddy.blackboard.catalog import catalog
        profiles = self.profiles()
        self.assertFalse(profiles['dsh:fixture:alpha:high']['available'])
        self.assertEqual(profiles['dsh:fixture:alpha:high']['unavailableReason'], 'HARNESS_NOT_CHECKED',
                         'A NULL reason must still receive the health write')
        self.assertEqual(profiles['dsh:fixture:alpha:low']['unavailableReason'], catalog.RETIRED_EFFORT_REASON)
        self.assertEqual(profiles['dsh:fixture:beta:high']['unavailableReason'], catalog.CONFIRMED_ABSENCE_REASON)

    def test_same_harness_path_recheck_preserves_confirmed_absence_and_retired_effort(self):
        # Reproduce the in-process cache stub boundary before a normal health
        # publication, then exercise the real refresh and unknown catalog read.
        with patch.object(self.health, 'refresh', return_value={}):
            self.health.set_path('dsh', None)
        self.assert_not_checked_keeps_catalog_reasons()
        self.assertTrue(self.health.refresh('dsh', force=True)['available'])
        self.assert_catalog_survives()

    def test_initialize_existing_profiles_preserves_catalog_reasons(self):
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM harness_health WHERE adapter='dsh'")
        self.health.initialize()
        self.assert_not_checked_keeps_catalog_reasons()
        self.assertTrue(self.health.refresh('dsh', force=True)['available'])
        self.assert_catalog_survives()

    def test_invalidate_updates_null_reason_and_preserves_catalog_reasons(self):
        revision = self.health.get('dsh')['revision']
        self.health.invalidate('dsh', revision, 'HARNESS_HANDSHAKE_FAILED')
        high = self.profiles()['dsh:fixture:alpha:high']
        self.assertFalse(high['available'])
        self.assertEqual(high.get('unavailableReason'), 'HARNESS_HANDSHAKE_FAILED')
        self.assertTrue(self.health.refresh('dsh', force=True)['available'])
        self.assert_catalog_survives()

    def test_unhealthy_refresh_updates_null_reason_and_preserves_catalog_reasons(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover',
                   return_value={'adapter': 'dsh', 'status': 'unhealthy', 'reasonCode': 'HARNESS_HANDSHAKE_FAILED'}):
            self.assertFalse(self.health.refresh('dsh', force=True)['available'])
        high = self.profiles()['dsh:fixture:alpha:high']
        self.assertFalse(high['available'])
        self.assertEqual(high.get('unavailableReason'), 'HARNESS_HANDSHAKE_FAILED')
        self.assertTrue(self.health.refresh('dsh', force=True)['available'])
        self.assert_catalog_survives()


class StaleRereadServiceCallbackTests(BoardTestCase):
    """The stale-catalog re-read through the real service callback (rule 4)."""

    def test_native_failure_inside_the_service_callback_invalidates_for_the_caller(self):
        from types import SimpleNamespace
        from hey_my_buddy.blackboard.service.harness_health import HarnessHealth
        from hey_my_buddy.errors import BoardError
        board = self.board()
        service = board.service
        health = HarnessHealth(service.store, catalog_refresh=service._refresh_harness_catalog)
        health.initialize()
        snapshot = {'adapter': 'dsh', 'managerRecords': [], 'candidates': []}
        ready = {'adapter': 'dsh', 'status': 'ready', 'version': '1.0',
                 'command': ['/fixture/dsh'], 'executable': '/fixture/dsh'}
        good = {'source': 'fixture-native', 'providers': [{'adapter': 'dsh', 'provider': 'fixture',
                'models': [{'id': 'alpha', 'efforts': ['max'], 'available': True}]}],
                'discoveries': [{'adapter': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

        def discover_models():
            if calls:
                raise BoardError('CATALOG_UNAVAILABLE', 'fixture native discovery failed')
            calls.append(True)
            return good

        calls: list[bool] = []
        stub = SimpleNamespace(discover_models=discover_models,
                               local_read_only_check=lambda: {"eligible": False, "systemSandbox": False})
        with patch('hey_my_buddy.buddy.harnesses.registry.adapter', return_value=stub), \
             patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value=snapshot), \
             patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready):
            first = health.refresh('dsh')
            self.assertTrue(first['available'])
            with board.store.db.write() as db:
                db.execute("UPDATE meta SET value=? WHERE key='catalog-read-at:dsh'", (_stamp(6 * 3600 + 1),))
                db.execute("DELETE FROM meta WHERE key='catalog-scan-after:dsh'")
            again = health.refresh('dsh')
        self.assertFalse(again['available'], 'The failed native re-read speaks through the record')
        self.assertEqual(again['reasonCode'], 'CATALOG_UNAVAILABLE')
        self.assertEqual(health.get('dsh')['revision'], again['revision'])


class HealthProbeFailureTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'native-stop-health')
        self.board.initialize()
        self.health = HarnessHealth(self.board)
        self.addCleanup(self.health.close)
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'adapter': 'codex', 'managerRecords': [], 'candidates': []}))

    def test_unconfirmed_probe_shutdown_remains_unavailable(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
                'adapter': 'codex', 'status': 'unhealthy', 'reasonCode': 'shutdown-unverified'}):
            result = self.health.refresh('codex', force=True)
        self.assertFalse(result['available'])
        self.assertEqual(result['reasonCode'], 'shutdown-unverified')

    def test_failed_probe_remains_unavailable(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=ValueError('fixture probe failed')):
            result = self.health.refresh('codex', force=True)
        self.assertFalse(result['available'])
        self.assertEqual(result['reasonCode'], 'HARNESS_HANDSHAKE_FAILED')

    def test_invalid_native_return_is_refused(self):
        from hey_my_buddy.errors import BoardError
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={'adapter': 'codex', 'status': 'illegal'}):
            with self.assertRaises(BoardError) as caught:
                self.health.refresh('codex', force=True)
        self.assertEqual(caught.exception.code, 'HARNESS_INVALID_RESULT')


class NativeAccountReadFailureTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'account-read-health')
        self.board.initialize()
        self.catalog_reads = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda *args, **kwargs: self.catalog_reads.append(args))
        self.health.initialize()
        self.addCleanup(self.health.close)
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'adapter': 'codex', 'managerRecords': [], 'candidates': []}))
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
            'adapter': 'codex', 'status': 'ready', 'command': ['fixture-codex'], 'version': 'fixture'}))

    def events(self, kind):
        import json
        with self.board.db.read() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM events WHERE kind=?', (kind,))]

    def test_unconfirmed_account_stop_stays_unavailable_until_a_real_successful_read(self):
        from hey_my_buddy.errors import BoardError
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read',
                   side_effect=BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'fixture stop was not confirmed')) as probe:
            first = self.health.refresh('codex', force=True)
            self.assertFalse(first['available'])
            self.assertEqual(first['status'], 'unhealthy')
            self.assertEqual(first['reasonCode'], 'HARNESS_ACCOUNT_STOP_UNCONFIRMED')
            again = self.health.refresh('codex', force=True)
            self.assertFalse(again['available'])
            self.assertEqual(again['reasonCode'], first['reasonCode'])
            probe.assert_called_once()  # Read throttling cannot erase the failure.
        self.assertEqual(self.catalog_reads, [])
        self.assertEqual(self.events('harness.account_observed'), [])
        self.assertEqual(self.events('harness.invalidated')[0]['reasonCode'], first['reasonCode'])
        with self.board.db.write() as db:
            db.execute("UPDATE harness_account_reads SET checked_at='2000' WHERE adapter='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None, None)):
            recovered = self.health.refresh('codex', force=True)
        self.assertTrue(recovered['available'])
        self.assertNotIn('reasonCode', recovered)
        self.assertNotIn('accountReadFailure', recovered)
        self.assertEqual(len(self.catalog_reads), 1)
        self.assertEqual(len(self.events('harness.account_observed')), 1)

    def test_ordinary_account_read_failure_records_its_reason_without_success_event(self):
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        for error, reason in ((BoardError('ACCOUNT_QUERY_DENIED', 'fixture query denied'), 'ACCOUNT_QUERY_DENIED'),
                              (CodexProtocolError('native-rpc-error', 'fixture RPC denied'), 'native-rpc-error'),
                              (OSError('fixture read transport failed'), 'HARNESS_ACCOUNT_READ_FAILED')):
            with self.subTest(reason=reason):
                with self.board.db.write() as db:
                    db.execute("DELETE FROM harness_account_reads WHERE adapter='codex'")
                with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=error):
                    result = self.health.refresh('codex', force=True)
                self.assertTrue(result['available'])
                self.assertEqual(result['status'], 'ready')
                self.assertNotIn('reasonCode', result)
                self.assertEqual(result['accountReadFailure']['reasonCode'], reason)
                self.assertEqual(result['billingByProvider']['unknown']['kind'], 'unknown')
                self.assertEqual(result['accountReadFailure']['errorType'], type(error).__name__)
                self.assertEqual(result['accountReadFailure']['message'], str(error))
                self.assertEqual(self.events('harness.account_read_failed')[-1]['reasonCode'], reason)
        self.assertEqual(self.events('harness.account_observed'), [])
        self.assertEqual(self.events('harness.invalidated'), [])
        self.assertEqual(len(self.catalog_reads), 3)
        with self.board.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_quota_observations').fetchone()[0], 0)
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE adapter='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=(None, None, None)):
            recovered = self.health.refresh('codex', force=True)
        self.assertTrue(recovered['available'])
        self.assertNotIn('accountReadFailure', recovered)

    def test_late_account_failure_cannot_invalidate_a_new_health_generation(self):
        from hey_my_buddy.errors import BoardError
        def fail_after_setting_change(*args):
            with self.board.db.write() as db:
                db.execute("UPDATE harness_health SET revision=revision+1,status='unknown',record_json='{}' WHERE adapter='codex'")
            raise BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'old fixture read')
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=fail_after_setting_change):
            result = self.health.refresh('codex', force=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.events('harness.invalidated'), [])
        failure = self.events('harness.account_read_failed')[0]
        self.assertEqual(failure['reasonCode'], 'HARNESS_ACCOUNT_STOP_UNCONFIRMED')
        self.assertLess(failure['revision'], failure['currentRevision'])

    def test_account_success_survives_quota_rpc_failure_after_confirmed_stop(self):
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [{}, {'account': {'type': 'chatgpt', 'planType': 'pro'}},
                                                       {'config': {'model_provider': 'openai'}},
                                                       CodexProtocolError('native-rpc-error', 'quota RPC denied')]
            result = self.health.refresh('codex', force=True)
            handle.return_value.terminate.assert_called_once()
        self.assertTrue(result['available'])
        self.assertEqual(result['billingByProvider']['openai']['kind'], 'subscription')
        self.assertEqual(result['accountReadFailure']['reasonCode'], 'native-rpc-error')
        self.assertEqual(self.events('harness.account_read_failed')[0]['reasonCode'], 'native-rpc-error')
        self.assertEqual(self.events('harness.account_observed')[0]['billing']['openai']['kind'], 'subscription')
        self.assertFalse(self.events('harness.account_observed')[0]['quotaRecorded'])
        self.assertEqual(self.events('harness.invalidated'), [])
        with self.board.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM harness_quota_observations').fetchone()[0], 0)

    def test_confirmed_stop_with_account_data_failure_restores_health_and_records_failure(self):
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        with patch.object(account_probe, 'read', side_effect=BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'unknown stop')):
            self.assertFalse(self.health.refresh('codex', force=True)['available'])
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE adapter='codex'")
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [{}, CodexProtocolError('native-rpc-error', 'account RPC denied')]
            result = self.health.refresh('codex', force=True)
        self.assertTrue(result['available'])
        self.assertNotIn('reasonCode', result)
        self.assertEqual(result['accountReadFailure']['reasonCode'], 'native-rpc-error')
        self.assertEqual(result['billingByProvider']['unknown']['kind'], 'unknown')

    def test_non_openai_native_provider_keeps_unknown_billing_without_rejecting_login(self):
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [
                {}, {'account': {'type': 'apiKey'}},
                {'config': {'model_provider': 'configured-provider'}}, {},
            ]
            result = self.health.refresh('codex', force=True)
            self.assertEqual([call.args[0] for call in connection.return_value.call.call_args_list],
                             ['initialize', 'account/read', 'config/read', 'account/rateLimits/read'])
        self.assertTrue(result['available'])
        self.assertNotIn('accountReadFailure', result)
        self.assertEqual(set(result['billingByProvider']), {'configured-provider'})
        self.assertEqual(result['billingByProvider']['configured-provider']['kind'], 'unknown')
        self.assertNotIn('provider', result['billingByProvider']['configured-provider'])
        self.assertEqual(self.events('harness.account_observed')[0]['billing'], result['billingByProvider'])

    def test_missing_or_null_provider_keeps_default_provider_account_facts(self):
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        for configured in ({}, {'model_provider': None}):
            with self.subTest(configured=configured), \
                 patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
                 patch.object(account_probe, 'Connection') as connection:
                with self.board.db.write() as db:
                    db.execute("DELETE FROM harness_account_reads WHERE adapter='codex'")
                handle.return_value.shutdown_confirmed.return_value = True
                connection.return_value.call.side_effect = [
                    {}, {'account': {'type': 'chatgpt', 'planType': 'plus'}}, {'config': configured}, {},
                ]
                result = self.health.refresh('codex', force=True)
                self.assertEqual([call.args[0] for call in connection.return_value.call.call_args_list],
                                 ['initialize', 'account/read', 'config/read', 'account/rateLimits/read'])
                self.assertEqual(set(result['billingByProvider']), {'openai'})
                self.assertEqual(result['billingByProvider']['openai']['kind'], 'subscription')

    def test_unexpected_account_reader_exception_is_not_silently_recorded(self):
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=TypeError('fixture programming error')):
            with self.assertRaises(TypeError):
                self.health.refresh('codex', force=True)
        self.assertEqual(self.events('harness.account_read_failed'), [])
