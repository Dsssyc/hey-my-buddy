"""Discovery generations, expiry, settings and cache reads use private boards."""
from datetime import datetime, timedelta, timezone
import json
import os
import threading
from unittest.mock import patch

from hey_my_buddy.blackboard.service.harness_health import HarnessHealth, MAX_PATH_HINTS
from support import BoardTestCase, FakeClock

CLI_SUFFIX = ".exe" if os.name == "nt" else ""


def probe_reason(code):
    return {'code': f'harness.{code}', 'params': {'operation': 'probe'}}


def account_reason(code):
    return {'code': f'harness.{code}', 'params': {'operation': 'account-read'}}


class HarnessHealthTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'health')
        self.board.initialize()
        self.refreshed = []
        self.health = HarnessHealth(self.board, catalog_refresh=lambda name, record, catalog_scan_claimed=False: self.refreshed.append((name, record)))
        self.health.initialize()
        self.snapshot = {'harness': 'codex', 'managerRecords': [], 'candidates': [
            {'path': '/native/codex', 'exists': True, 'fingerprint': {'mtimeNs': 1}}]}
        self.discovered = {'harness': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True,
                           'reason': None}
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=None))
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def test_cache_reads_do_not_discover_and_refresh_is_throttled(self):
        unknown = self.health.get('codex')
        self.assertEqual(unknown['status'], 'unknown')
        self.assertNotIn('available', unknown)
        self.assertEqual(unknown['providers'], [])
        self.native.assert_not_called()
        result = self.health.refresh('codex')
        self.assertEqual(result['status'], 'ready')
        self.assertNotIn('available', result)
        self.health.get('codex')
        self.health.refresh('codex')
        self.native.assert_called_once()
        self.assertEqual(len(self.refreshed), 1)

    def test_health_lists_only_persisted_provider_cooling_rows(self):
        self.health.refresh('codex')
        with self.board.db.write() as db:
            db.execute('INSERT INTO provider_cooling(harness,provider,consecutive_rejections,cooling_until,last_rejected_at) '
                       'VALUES(?,?,?,?,?)', ('codex', 'openai', 2, None, '2026-10-07T12:00:00.000Z'))
            db.execute('INSERT INTO provider_cooling(harness,provider,consecutive_rejections,cooling_until,last_rejected_at) '
                       'VALUES(?,?,?,?,?)', ('dsh', 'fixture', 1, '2026-10-07T12:30:00.000Z', '2026-10-07T12:00:00.000Z'))
        result = self.health.get('codex')
        self.assertEqual(result['providers'], [{'provider': 'openai', 'coolingUntil': None, 'consecutiveRejections': 2}])
        self.assertNotIn('quota', result)
        self.assertNotIn('quotaRouting', result)

    def test_periodic_health_observations_replace_only_the_same_harness_and_kind(self):
        import json

        self.health.refresh('codex', force=True)
        before = self.board.events_read({'after': 0})['head']
        self.discovered = {**self.discovered, 'version': '2.0'}
        self.health.refresh('codex', force=True)
        # Another harness has its own retained observation.
        self.discovered = {**self.discovered, 'harness': 'dsh', 'command': ['/native/dsh']}
        self.health.refresh('dsh', force=True)
        for reason in ('slow-first', 'slow-latest'):
            self.discovered = {'harness': 'codex', 'status': 'unknown', 'reason': probe_reason(reason)}
            self.health.refresh('codex', force=True)
        events = self.board.events_read({'after': 0})['events']
        checked = [row for row in events if row['kind'] == 'harness.checked']
        self.assertEqual({row['payload']['harness'] for row in checked}, {'codex', 'dsh'})
        self.assertEqual(len(checked), 2)
        self.assertEqual(next(row['payload']['version'] for row in checked
                              if row['payload']['harness'] == 'codex'), '2.0')
        unknown = [row for row in events if row['kind'] == 'harness.probe_unknown']
        self.assertEqual(len(unknown), 1)
        self.assertEqual(unknown[0]['payload']['reason'], probe_reason('slow-latest'))
        self.assertGreater(unknown[0]['seq'], before)
        self.assertEqual(self.health.get('codex')['lastProbe']['reason'], probe_reason('slow-latest'))

    def test_slow_scan_preserves_ready_health_and_next_scan_can_succeed(self):
        first = self.health.refresh('codex', force=True)
        self.discovered = {'harness': 'codex', 'status': 'unknown', 'reason': probe_reason('scan-timeout')}
        pending = self.health.refresh('codex', force=True)
        self.assertEqual(pending['status'], 'ready')
        self.assertEqual(pending['status'], 'ready')
        self.assertEqual(pending['command'], first['command'])
        self.assertEqual(pending['checkedAt'], first['checkedAt'])
        self.assertEqual(pending['lastProbe']['status'], 'unknown')
        self.discovered = {'harness': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'available': True, 'version': '2.0', 'reason': None}
        cached = self.health.refresh('codex', preflight=True)
        self.assertEqual(cached['lastProbe']['status'], 'unknown')
        self.assertEqual(self.native.call_count, 2)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='codex'")
        recovered = self.health.refresh('codex', preflight=True)
        self.assertEqual(recovered['version'], '2.0')
        self.assertEqual(recovered['lastProbe']['status'], 'complete')

    def test_first_slow_scan_remains_unknown_and_does_not_invalidate_buddies(self):
        self.discovered = {'harness': 'codex', 'status': 'unknown', 'reason': probe_reason('timeout')}
        result = self.health.refresh('codex', force=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(result['lastProbe']['reason'], probe_reason('timeout'))
        with self.board.db.read() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='harness.invalidated'").fetchone()[0], 0)

    def test_caller_path_is_a_transient_hint_and_never_stored(self):
        cli_dir = self.directory / 'new-cli'
        node_dir = self.directory / 'other-bin'
        cli_dir.mkdir()
        node_dir.mkdir()
        (cli_dir / ('codex' + CLI_SUFFIX)).write_text('fixture')
        (node_dir / ('node' + CLI_SUFFIX)).write_text('fixture')
        self.health.add_path_hint(os.pathsep.join([str(cli_dir), str(node_dir), 'relative']))
        self.health.refresh('codex', force=True)
        environment = self.native.call_args.args[2]
        self.assertTrue(environment['PATH'].startswith(os.pathsep.join([str(cli_dir), str(node_dir)]) + os.pathsep))
        with self.board.db.read() as db:
            stored = db.execute("SELECT record_json FROM harness_health WHERE harness='codex'").fetchone()[0]
        self.assertNotIn(str(cli_dir), stored)

    def test_path_hints_keep_first_seen_order_and_ignore_empty_directories_at_the_limit(self):
        directories = []
        for index in range(MAX_PATH_HINTS + 1):
            directory = self.directory / f'cli-{index}'
            directory.mkdir()
            (directory / (('node' if index % 2 else 'codex') + CLI_SUFFIX)).write_text('fixture')
            directories.append(str(directory))
        empty = [str(self.directory / f'empty-{index}') for index in range(100)]
        unrelated = self.directory / 'unrelated'
        unrelated.mkdir()
        (unrelated / 'other-tool').write_text('fixture')
        (unrelated / ('codex' + CLI_SUFFIX)).mkdir()
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
                (directory / ('codex' + CLI_SUFFIX)).write_text('fixture')
            directories.append(str(directory))
        self.health.add_path_hint(os.pathsep.join(directories[:2]))
        self.health.add_path_hint(os.pathsep.join([directories[2], directories[1], directories[0]]))
        self.assertEqual(self.health._path_hints, tuple(directories[:2]))
        (self.directory / 'c' / ('codex' + CLI_SUFFIX)).write_text('appeared')
        self.health.add_path_hint(os.pathsep.join([directories[2], directories[1], directories[0]]))
        self.assertEqual(self.health._path_hints, tuple(directories))

    def test_alternating_empty_path_hints_do_not_rebuild_health_or_reread_catalog(self):
        from hey_my_buddy.buddy.harnesses import discovery
        self.scan.stop()
        cli = self.directory / 'bin' / ('codex' + CLI_SUFFIX)
        cli.parent.mkdir()
        cli.write_text('fixture')
        other = self.directory / 'empty'
        paths = [str(cli.parent), os.pathsep.join([str(other), str(cli.parent)]),
                 os.pathsep.join([*(str(self.directory / f'empty-{index}') for index in range(100)),
                           str(cli.parent)])]
        with patch.object(discovery, '_home', return_value=self.directory), \
             patch.object(discovery, '_common_dirs', return_value=[]), \
             patch.object(discovery, '_app_paths', return_value=[]), \
             patch.dict('os.environ', {'PATH': ''}):
            self.health.add_path_hint(paths[0])
            first = self.health.refresh('codex')
            for path in [paths[1], paths[0], paths[2], paths[0], paths[1], paths[0]]:
                with self.board.db.write() as db:
                    db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='codex'")
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
            (directory / ('codex' + CLI_SUFFIX)).write_text('fixture')
        with patch.object(discovery, '_home', return_value=self.directory), \
             patch.object(discovery, '_common_dirs', return_value=[]), \
             patch.object(discovery, '_app_paths', return_value=[]), \
             patch.dict('os.environ', {'PATH': ''}):
            for directory in directories:
                self.health.add_path_hint(str(directory))
            first = self.health.refresh('codex')
            for directory in [directories[1], directories[0], directories[1], directories[0]]:
                with self.board.db.write() as db:
                    db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='codex'")
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
            executable = directory / ('codex' + CLI_SUFFIX)
            executable.write_text('fixture')
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
            executable = added / ('codex' + CLI_SUFFIX)
            executable.write_text('fixture')
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

    def test_stat_change_reprobes_without_changing_buddy_facts(self):
        from hey_my_buddy.blackboard.evaluation.evaluation import EvaluationStore
        evaluation = EvaluationStore(self.board)
        readings = []
        payload = {'source': 'stat-change-fixture',
                   'discoveries': [{'harness': 'codex', 'status': 'complete', 'accountStatus': 'confirmed'}],
                   'providers': [{'harness': 'codex', 'provider': 'fixture', 'models': [
                       {'id': 'alpha', 'efforts': ['high']}]}]}
        self.health.catalog_refresh = lambda name, record, catalog_scan_claimed=False: readings.append(
            evaluation.record_catalog(payload))
        self.health.refresh('codex', preflight=True)
        self.snapshot['candidates'][0]['fingerprint'] = {'mtimeNs': 2}
        self.health.refresh('codex', preflight=True)
        self.assertEqual(self.native.call_count, 2)
        self.assertEqual(len(readings), 2)
        self.assertGreater(readings[1]['observationId'], readings[0]['observationId'])
        self.assertEqual(readings[1]['buddies'], readings[0]['buddies'])

    def test_ready_record_expires_without_a_location_change(self):
        first = self.health.refresh('codex')
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET expires_at='2000',scan_after='2000' WHERE harness='codex'")
        result = self.health.refresh('codex', preflight=True)
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['locationFingerprint'], first['locationFingerprint'])
        self.assertEqual(self.native.call_count, 2)

    def test_launch_failure_invalidates_ready_record_and_reprobes(self):
        first = self.health.refresh('codex')
        self.health.invalidate('codex', first['revision'], cause=probe_reason('launch-failed'))
        invalidated = self.health.get('codex')
        self.assertNotEqual(invalidated['status'], 'ready')
        self.assertEqual(invalidated['reason'], probe_reason('launch-failed'))
        recovered = self.health.refresh('codex', preflight=True)
        self.assertEqual(recovered['status'], 'ready')
        self.assertEqual(self.native.call_count, 2)

    def test_negative_auth_observation_expires_even_without_file_changes(self):
        self.discovered = {'harness': 'codex', 'status': 'login-required', 'reason': probe_reason('login-required')}
        self.assertNotEqual(self.health.refresh('codex')['status'], 'ready')
        self.health.refresh('codex')
        self.native.assert_called_once()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET expires_at='2000',scan_after='2000' WHERE harness='codex'")
        self.discovered = {'harness': 'codex', 'status': 'ready', 'version': '1.0', 'reason': None}
        self.assertEqual(self.health.refresh('codex')['status'], 'ready')
        self.assertEqual(self.native.call_count, 2)

    def test_late_discovery_cannot_overwrite_new_manual_generation(self):
        def replaced(*args):
            with self.board.db.write() as db:
                db.execute("UPDATE harness_health SET manual_path='/new/codex',revision=revision+1 WHERE harness='codex'")
            return self.discovered
        self.native.side_effect = replaced
        result = self.health.refresh('codex')
        self.assertEqual(result['manualPath'], '/new/codex')
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.refreshed, [])


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
        self.snapshot = {'harness': 'codex', 'managerRecords': [], 'candidates': [
            {'path': '/native/codex', 'exists': True, 'fingerprint': {'mtimeNs': 1}}]}
        self.discovered = {'harness': 'codex', 'status': 'ready', 'command': ['/native/codex'],
                           'executable': '/native/codex', 'version': '1.0', 'source': 'common', 'available': True,
                           'reason': None}
        self.enterContext(patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=None))
        self.scan = patch('hey_my_buddy.blackboard.service.harness_health._snapshot', side_effect=lambda *args: self.snapshot)
        self.probe = patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=lambda *args: self.discovered)
        self.scan.start()
        self.native = self.probe.start()
        self.addCleanup(self.scan.stop)
        self.addCleanup(self.probe.stop)

    def set_read_at(self, seconds_ago):
        with self.board.db.read() as db:
            recorded = db.execute("SELECT 1 FROM catalog_current WHERE harness='codex'").fetchone()
        if recorded is None:
            self.record_native('confirmed')
        with self.board.db.write() as db:
            db.execute("UPDATE catalog_current SET read_at=? WHERE harness='codex'", (_stamp(seconds_ago),))

    def pass_throttles(self):
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000' WHERE harness='codex'")

    def expire_catalog_throttle(self):
        """The bounded catalog re-read cadence (SCAN_SECONDS) has passed."""
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='codex'")

    def record_native(self, account_status, *, observed_seconds_ago=None):
        from hey_my_buddy.blackboard.catalog import catalog_store
        payload = {'source': 'fixture-native',
                   'providers': [{'harness': 'codex', 'provider': 'openai',
                                  'models': [{'id': 'sol', 'efforts': ['high'], 'available': True}]}],
                   'discoveries': [{'harness': 'codex', 'status': 'complete', 'accountStatus': account_status}]}
        catalog_store.record(self.board.evaluation, payload)
        if observed_seconds_ago is not None:
            with self.board.db.write() as db:
                db.execute("UPDATE catalog_current SET updated_at=? WHERE harness='codex'", (_stamp(observed_seconds_ago),))

    def catalog_state(self):
        with self.board.db.read() as db:
            row = db.execute("SELECT status, reason FROM catalog_current WHERE harness='codex'").fetchone()
            buddies = db.execute("SELECT buddy_id, available FROM buddies WHERE harness='codex'").fetchall()
            read_at = db.execute("SELECT read_at FROM catalog_current WHERE harness='codex'").fetchone()
        return row, buddies, (read_at[0] if read_at else None)

    def test_throttled_scan_still_rereads_a_stale_catalog_once(self):
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        # Expire the shared scan window to allow the next stale catalog read.
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        result = self.health.refresh('codex')
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(self.native.call_count, 1, 'An unchanged harness reprobes nothing')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'The stale catalog is re-read once')
        # The re-read cadence stays bounded while the shared scan window is open.
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
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE harness='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.native.call_count, 2, 'The expired record reprobes the harness')
        self.assertEqual(self.refreshed, ['codex'], 'A fresh catalog needs no re-read')
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE harness='codex'")
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])

    def test_health_only_scan_does_not_consume_a_catalog_reread_window(self):
        from hey_my_buddy.blackboard.catalog.catalog import coordinate_catalog_reread
        self.health.refresh('codex')
        self.set_read_at(60)
        self.expire_catalog_throttle()
        first = list(self.refreshed)
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, first, 'A fresh catalog is not reread by a health-only scan')
        calls = []
        self.assertEqual(coordinate_catalog_reread(self.board.directory, 'codex',
                         reader=lambda: calls.append('native-read'), wait=True), 'started')
        self.assertEqual(calls, ['native-read'])
        self.assertEqual(coordinate_catalog_reread(self.board.directory, 'codex',
                         reader=lambda: calls.append('duplicate'), wait=True), 'window')
        self.assertEqual(calls, ['native-read'])

    def test_unparseable_read_time_counts_as_expired(self):
        self.health.refresh('codex')
        self.record_native('confirmed')
        with self.board.db.write() as db:
            db.execute("UPDATE catalog_current SET read_at='not-a-time' WHERE harness='codex'")
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
            db.execute("UPDATE harness_health SET scan_after='2000',expires_at='2000' WHERE harness='codex'")
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
        row, buddies, read_at = self.catalog_state()
        self.assertEqual(row['status'], 'complete')
        self.assertEqual([p['buddy_id'] for p in buddies], ['codex:openai:sol:high'])
        self.assertTrue(buddies[0]['available'])
        self.assertIsNotNone(read_at, 'The confirmed re-read parks the shelf clock')
        self.expire_catalog_throttle()
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'], 'A confirmed catalog needs no further re-read')

    def test_stale_reread_failure_returns_the_post_callback_record(self):
        # Host reproduction: the catalog is past its shelf life, and the bounded
        # re-read's native failure invalidates
        # the harness. refresh must answer with the record after the callback,
        # never with the stale pre-reread ready record.
        self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex'])
        self.set_read_at(6 * 3600 + 1)
        self.expire_catalog_throttle()

        def failing_refresh(name, record, catalog_scan_claimed=False):
            self.refreshed.append(name)
            self.health.invalidate(name, record['revision'],
                                   cause={'code': 'catalog.discovery-failed',
                                          'params': {'harness': name, 'errorCode': 'CATALOG_UNAVAILABLE'}})

        self.health.catalog_refresh = failing_refresh
        result = self.health.refresh('codex')
        self.assertEqual(self.refreshed, ['codex', 'codex'])
        self.assertNotEqual(result['status'], 'ready', 'The post-callback record leads')
        self.assertEqual(result['reason'], {'code': 'catalog.discovery-failed',
                                            'params': {'harness': 'codex', 'errorCode': 'CATALOG_UNAVAILABLE'}})
        self.assertEqual(self.health.get('codex')['revision'], result['revision'])


class SharedStaleCatalogFlightTests(BoardTestCase):
    """A health stale read and an explicit validation re-read share one flight.

    Both go through the same claim of the ``scan_after`` window, so one
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
        self.ready = {'harness': 'dsh', 'status': 'ready', 'version': '1.0',
                      'command': ['/fixture/dsh'], 'executable': '/fixture/dsh', 'reason': None}
        for target, result in (('_snapshot', {'harness': 'dsh', 'managerRecords': [], 'candidates': []}),
                               ('_discover', self.ready)):
            stub = patch('hey_my_buddy.blackboard.service.harness_health.' + target, return_value=result)
            stub.start()
            self.addCleanup(stub.stop)

    def reading(self, models):
        return {'source': 'fixture-native',
                'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}],
                'providers': [{'harness': 'dsh', 'provider': 'fixture',
                               'models': [{'id': model, 'efforts': ['max'], 'available': True} for model in models]}]}

    def prime_ready_health(self, health):
        """One ready health record plus a shelf-expired catalog and a due window."""
        self.evaluation.record_catalog(self.reading(('alpha',)))
        health.refresh('dsh', force=True)
        with self.board.db.write() as db:
            db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='dsh'")
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
        target = {'harness': 'dsh', 'provider': 'fixture', 'model': 'beta', 'effort': 'max'}
        outcome, error = {}, {}

        def request():
            try:
                outcome['value'] = catalog.validate_buddy(target, directory=self.board.directory)
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
        target = {'harness': 'dsh', 'provider': 'fixture', 'model': 'beta', 'effort': 'max'}
        outcome, error = {}, {}

        def request():
            try:
                outcome['value'] = catalog.validate_buddy(target, directory=self.board.directory)
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
        self.assertEqual(scanned['status'], 'ready')
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
        self.ready = {'harness': 'dsh', 'status': 'ready', 'version': '1.0', 'command': ['/fixture/dsh'], 'reason': None}
        for target, result in (
            ('_snapshot', {'harness': 'dsh', 'managerRecords': [], 'candidates': []}),
            ('_discover', self.ready),
        ):
            stub = patch('hey_my_buddy.blackboard.service.harness_health.' + target, return_value=result)
            stub.start()
            self.addCleanup(stub.stop)

    def reading(self, efforts):
        models = [{'id': 'alpha', 'efforts': list(efforts), 'available': True},
                  {'id': 'gamma', 'efforts': ['max'], 'available': False, 'unavailableReason': {'code': 'catalog.native-unavailable', 'params': {}},
                   'unavailableContent': 'native-disabled'}]
        return {'source': 'fixture-native', 'providers': [{'harness': 'dsh', 'provider': 'fixture', 'models': models}],
                'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

    def buddies(self):
        from hey_my_buddy.blackboard.catalog import catalog_store
        return {item['buddyId']: item for item in catalog_store.buddies(
            self.evaluation, {'includeUnavailable': True})['buddies']}

    def test_recovery_restores_the_retained_catalog_without_any_publication(self):
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        self.evaluation.record_catalog(self.reading(('max', 'high')))
        self.evaluation.record_catalog(self.reading(('max',)))  # alpha:high retires
        with self.board.store.db.write() as db:
            db.execute("UPDATE buddies SET enabled=1 WHERE harness='dsh'")
        self.health.invalidate('dsh', self.health.get('dsh')['revision'])
        broken = self.buddies()['dsh:fixture:alpha:max']
        self.assertFalse(broken['available'], 'The health failure zeroed the retained route')
        self.assertEqual(broken.get('unavailableReason'), probe_reason('launch-failed'))
        self.assertEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        buddies = self.buddies()
        self.assertTrue(buddies['dsh:fixture:alpha:max']['available'],
                        'Recovery itself restores the retained reading, before any publication')
        self.assertFalse(buddies['dsh:fixture:alpha:high']['available'], 'A retired effort is a catalog fact')
        self.assertEqual(buddies['dsh:fixture:alpha:high']['unavailableReason']['code'], catalog.RETIRED_EFFORT_REASON)
        self.assertFalse(buddies['dsh:fixture:gamma:max']['available'], 'A native unavailable declaration stays down')
        with self.board.store.db.read() as db:
            self.assertEqual([row['buddy_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)],
                             ['dsh:fixture:alpha:max'])


class CatalogHealthOwnershipTests(BoardTestCase):
    """Every health write preserves adopted catalog facts, including NULL rows."""

    def setUp(self):
        super().setUp()
        self.clock = FakeClock()
        self.board = self.board(clock=self.clock)
        self.evaluation = self.board.evaluation
        self.health = HarnessHealth(self.board.store, catalog_refresh=self.unknown_read)
        self.ready = {'harness': 'dsh', 'status': 'ready', 'version': '1.0', 'command': ['/fixture/dsh'], 'reason': None}
        for target, result in (
            ('_snapshot', {'harness': 'dsh', 'managerRecords': [], 'candidates': []}),
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
            db.execute("UPDATE buddies SET enabled=1 WHERE harness='dsh'")
        self.before = self.buddies()

    def reading(self, efforts, *, beta):
        models = [{'id': 'alpha', 'efforts': list(efforts), 'available': True},
                  {'id': 'gamma', 'efforts': ['high'], 'available': False, 'unavailableReason': {'code': 'catalog.native-unavailable', 'params': {}},
                   'unavailableContent': 'native-disabled'}]
        if beta:
            models.append({'id': 'beta', 'efforts': ['high'], 'available': True})
        return {'source': 'fixture-native', 'providers': [{'harness': 'dsh', 'provider': 'fixture', 'models': models}],
                'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

    def unknown_read(self, *args, catalog_scan_claimed=False):
        self.evaluation.record_catalog({'source': 'fixture-native', 'providers': [],
            'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'unknown'}]})

    def buddies(self):
        from hey_my_buddy.blackboard.catalog import catalog_store
        return {item['buddyId']: item for item in catalog_store.buddies(
            self.evaluation, {'includeUnavailable': True})['buddies']}

    def assert_catalog_survives(self):
        from hey_my_buddy.blackboard.catalog import catalog
        from hey_my_buddy.blackboard.routing.decision import DecisionCoordinator
        buddies = self.buddies()
        self.assertTrue(buddies['dsh:fixture:alpha:high']['available'])
        self.assertFalse(buddies['dsh:fixture:alpha:low']['available'])
        self.assertEqual(buddies['dsh:fixture:alpha:low']['unavailableReason']['code'], catalog.RETIRED_EFFORT_REASON)
        self.assertFalse(buddies['dsh:fixture:beta:high']['available'])
        self.assertEqual(buddies['dsh:fixture:beta:high']['catalogStatus'], 'unavailable')
        self.assertEqual(buddies['dsh:fixture:beta:high']['unavailableReason']['code'], catalog.CONFIRMED_ABSENCE_REASON)
        self.assertFalse(buddies['dsh:fixture:gamma:high']['available'], 'Native unavailable stays grounded')
        self.assertTrue(all(item['enabled'] for item in buddies.values()))
        self.assertEqual(set(buddies), set(self.before), 'Buddy identities survive')
        with self.board.store.db.read() as db:
            candidates = [row['buddy_id'] for row in DecisionCoordinator._select_candidates(db, [], coding_only=True)]
            transitions = [row[0] for row in db.execute("SELECT kind FROM events WHERE kind LIKE 'catalog.model_%' ORDER BY seq")]
        self.assertEqual(candidates, ['dsh:fixture:alpha:high'])
        self.assertEqual(transitions, ['catalog.model_pending', 'catalog.model_unavailable'])

    def assert_not_checked_keeps_catalog_reasons(self):
        from hey_my_buddy.blackboard.catalog import catalog
        buddies = self.buddies()
        self.assertFalse(buddies['dsh:fixture:alpha:high']['available'])
        self.assertEqual(buddies['dsh:fixture:alpha:high']['unavailableReason'], probe_reason('not-checked'),
                         'A NULL reason must still receive the health write')
        self.assertEqual(buddies['dsh:fixture:alpha:low']['unavailableReason']['code'], catalog.RETIRED_EFFORT_REASON)
        self.assertEqual(buddies['dsh:fixture:beta:high']['unavailableReason']['code'], catalog.CONFIRMED_ABSENCE_REASON)

    def test_same_harness_path_recheck_preserves_confirmed_absence_and_retired_effort(self):
        # Reproduce the in-process cache stub boundary before a normal health
        # publication, then exercise the real refresh and unknown catalog read.
        with patch.object(self.health, 'refresh', return_value={}):
            self.health.set_path('dsh', None)
        self.assert_not_checked_keeps_catalog_reasons()
        self.assertEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        self.assert_catalog_survives()

    def test_initialize_existing_buddies_preserves_catalog_reasons(self):
        with self.board.store.db.write() as db:
            db.execute("DELETE FROM harness_health WHERE harness='dsh'")
        self.health.initialize()
        self.assert_not_checked_keeps_catalog_reasons()
        self.assertEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        self.assert_catalog_survives()

    def test_invalidate_updates_null_reason_and_preserves_catalog_reasons(self):
        revision = self.health.get('dsh')['revision']
        self.health.invalidate('dsh', revision)
        high = self.buddies()['dsh:fixture:alpha:high']
        self.assertFalse(high['available'])
        self.assertEqual(high.get('unavailableReason'), probe_reason('launch-failed'))
        self.assertEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        self.assert_catalog_survives()

    def test_unhealthy_refresh_updates_null_reason_and_preserves_catalog_reasons(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover',
                   return_value={'harness': 'dsh', 'status': 'unhealthy', 'reason': probe_reason('probe-failed')}):
            self.assertNotEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        high = self.buddies()['dsh:fixture:alpha:high']
        self.assertFalse(high['available'])
        self.assertEqual(high.get('unavailableReason'), probe_reason('probe-failed'))
        self.assertEqual(self.health.refresh('dsh', force=True)['status'], 'ready')
        self.assert_catalog_survives()


class StaleRereadServiceCallbackTests(BoardTestCase):
    """The stale-catalog re-read through the real service callback (rule 4)."""

    def test_non_object_native_receipt_is_a_catalog_failure(self):
        from types import SimpleNamespace
        board = self.board()
        service = board.service
        record = service.harnesses.get('dsh')
        with patch('hey_my_buddy.buddy.harnesses.registry.adapter',
                   return_value=SimpleNamespace(discover_models=lambda: [])):
            service._refresh_harness_catalog('dsh', record)
        self.assertEqual(service.harnesses.get('dsh')['reason'], {
            'code': 'catalog.discovery-failed',
            'params': {'harness': 'dsh', 'errorCode': 'CATALOG_INVALID'},
        })

    def test_native_failure_inside_the_service_callback_invalidates_for_the_caller(self):
        from types import SimpleNamespace
        from hey_my_buddy.blackboard.service.harness_health import HarnessHealth
        from hey_my_buddy.errors import BoardError
        board = self.board()
        service = board.service
        health = HarnessHealth(service.store, catalog_refresh=service._refresh_harness_catalog)
        health.initialize()
        snapshot = {'harness': 'dsh', 'managerRecords': [], 'candidates': []}
        ready = {'harness': 'dsh', 'status': 'ready', 'version': '1.0',
                 'command': ['/fixture/dsh'], 'executable': '/fixture/dsh', 'reason': None}
        good = {'source': 'fixture-native', 'providers': [{'harness': 'dsh', 'provider': 'fixture',
                'models': [{'id': 'alpha', 'efforts': ['max'], 'available': True}]}],
                'discoveries': [{'harness': 'dsh', 'status': 'complete', 'accountStatus': 'confirmed'}]}

        def discover_models():
            if calls:
                raise BoardError('CATALOG_UNAVAILABLE', 'fixture native discovery failed')
            calls.append(True)
            return good

        calls: list[bool] = []
        stub = SimpleNamespace(discover_models=discover_models)
        with patch('hey_my_buddy.buddy.harnesses.registry.adapter', return_value=stub), \
             patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value=snapshot), \
             patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value=ready):
            first = health.refresh('dsh')
            self.assertEqual(first['status'], 'ready')
            with board.store.db.write() as db:
                db.execute("UPDATE catalog_current SET read_at=? WHERE harness='dsh'", (_stamp(6 * 3600 + 1),))
                db.execute("UPDATE harness_health SET scan_after=NULL WHERE harness='dsh'")
            again = health.refresh('dsh')
        self.assertNotEqual(again['status'], 'ready', 'The failed native re-read speaks through the record')
        self.assertEqual(again['reason'], {'code': 'catalog.discovery-failed',
                                           'params': {'harness': 'dsh', 'errorCode': 'CATALOG_UNAVAILABLE'}})
        self.assertEqual(health.get('dsh')['revision'], again['revision'])


class HealthProbeFailureTests(BoardTestCase):
    def setUp(self):
        super().setUp()
        from hey_my_buddy.blackboard.store.store import BoardStore
        self.board = BoardStore(self.directory / 'native-stop-health')
        self.board.initialize()
        self.health = HarnessHealth(self.board)
        self.addCleanup(self.health.close)
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}))

    def test_unconfirmed_probe_shutdown_remains_unavailable(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
                'harness': 'codex', 'status': 'unhealthy', 'reason': probe_reason('shutdown-unverified')}):
            result = self.health.refresh('codex', force=True)
        self.assertNotEqual(result['status'], 'ready')
        self.assertEqual(result['reason'], probe_reason('shutdown-unverified'))

    def test_failed_probe_remains_unavailable(self):
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', side_effect=ValueError('fixture probe failed')):
            result = self.health.refresh('codex', force=True)
        self.assertNotEqual(result['status'], 'ready')
        self.assertEqual(result['reason'], probe_reason('probe-failed'))

    def test_invalid_native_return_is_refused(self):
        from hey_my_buddy.errors import BoardError
        with patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={'harness': 'codex', 'status': 'illegal'}):
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
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._snapshot', return_value={'harness': 'codex', 'managerRecords': [], 'candidates': []}))
        self.enterContext(patch('hey_my_buddy.blackboard.service.harness_health._discover', return_value={
            'harness': 'codex', 'status': 'ready', 'command': ['fixture-codex'], 'version': 'fixture',
            'reason': None}))

    def events(self, kind):
        import json
        with self.board.db.read() as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload_json FROM events WHERE kind=?', (kind,))]

    def test_account_observations_keep_the_latest_read_and_advance_the_cursor(self):
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=None):
            self.health.refresh('codex', force=True)
            previous = self.board.events_read({'after': 0})['head']
            with self.board.db.write() as db:
                db.execute("UPDATE harness_account_reads SET checked_at='2000' WHERE harness='codex'")
            self.health.refresh('codex', force=True)
        events = self.board.events_read({'after': previous})['events']
        account = [row for row in events if row['kind'] == 'harness.account_observed']
        self.assertEqual(len(account), 1)
        self.assertGreater(account[0]['seq'], previous)
        self.assertEqual(len(self.events('harness.account_observed')), 1)

    def test_unconfirmed_account_stop_stays_unavailable_until_a_real_successful_read(self):
        from hey_my_buddy.errors import BoardError
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read',
                   side_effect=BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'fixture stop was not confirmed')) as probe:
            first = self.health.refresh('codex', force=True)
            self.assertNotEqual(first['status'], 'ready')
            self.assertEqual(first['status'], 'unhealthy')
            self.assertEqual(first['reason'], account_reason('shutdown-unverified'))
            again = self.health.refresh('codex', force=True)
            self.assertNotEqual(again['status'], 'ready')
            self.assertEqual(again['reason'], first['reason'])
            probe.assert_called_once()  # Read throttling cannot erase the failure.
        self.assertEqual(self.catalog_reads, [])
        self.assertEqual(self.events('harness.account_observed'), [])
        self.assertEqual(self.events('harness.invalidated')[0]['reason'], first['reason'])
        with self.board.db.write() as db:
            db.execute("UPDATE harness_account_reads SET checked_at='2000' WHERE harness='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=None):
            recovered = self.health.refresh('codex', force=True)
        self.assertEqual(recovered['status'], 'ready')
        self.assertIsNone(recovered['reason'])
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
                    db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
                with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=error):
                    result = self.health.refresh('codex', force=True)
                self.assertEqual(result['status'], 'ready')
                self.assertEqual(result['status'], 'ready')
                self.assertIsNone(result['reason'])
                self.assertEqual(result['accountReadFailure']['reason'], account_reason('account-read-failed'))
                self.assertEqual(result['billingByProvider']['unknown']['kind'], 'unknown')
                self.assertEqual(result['accountReadFailure']['errorType'], type(error).__name__)
                self.assertEqual(result['accountReadFailure']['message'], str(error))
                self.assertEqual(self.events('harness.account_read_failed')[-1]['reason'], account_reason('account-read-failed'))
        self.assertEqual(self.events('harness.account_observed'), [])
        self.assertEqual(self.events('harness.invalidated'), [])
        self.assertEqual(len(self.catalog_reads), 3)
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=None):
            recovered = self.health.refresh('codex', force=True)
        self.assertEqual(recovered['status'], 'ready')
        self.assertNotIn('accountReadFailure', recovered)

    def test_account_wait_expiry_keeps_health_and_billing_then_retries(self):
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        billing = {'kind': 'metered', 'provider': 'openai', 'source': 'codex/account-read', 'observedAt': _stamp(0)}
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=billing):
            first = self.health.refresh('codex', force=True)
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=CodexProtocolError('deadline', 'wait expired')):
            pending = self.health.refresh('codex', force=True)
        self.assertEqual(pending['status'], 'ready')
        self.assertEqual(pending['status'], 'ready')
        self.assertEqual(pending['billingByProvider'], first['billingByProvider'])
        self.assertEqual(pending['accountReadFailure']['status'], 'unknown')
        self.assertEqual(len(self.events('harness.account_read_unknown')), 1)
        self.assertEqual(self.events('harness.invalidated'), [])
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', return_value=billing):
            recovered = self.health.refresh('codex', force=True)
        self.assertNotIn('accountReadFailure', recovered)

    def test_late_account_failure_cannot_invalidate_a_new_health_generation(self):
        from hey_my_buddy.errors import BoardError
        def fail_after_setting_change(*args):
            with self.board.db.write() as db:
                db.execute("UPDATE harness_health SET revision=revision+1,status='unknown',record_json=? WHERE harness='codex'",
                           (json.dumps({'reason': probe_reason('not-checked')}),))
            raise BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'old fixture read')
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=fail_after_setting_change):
            result = self.health.refresh('codex', force=True)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.events('harness.invalidated'), [])
        failure = self.events('harness.account_read_failed')[0]
        self.assertEqual(failure['reason'], account_reason('shutdown-unverified'))
        self.assertLess(failure['revision'], failure['currentRevision'])

    def test_account_success_reads_billing_without_rate_limit_rpc(self):
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [{}, {'account': {'type': 'chatgpt', 'planType': 'pro'}},
                                                       {'config': {'model_provider': 'openai'}}]
            result = self.health.refresh('codex', force=True)
            handle.return_value.terminate.assert_called_once()
        self.assertEqual(result['status'], 'ready')
        self.assertEqual(result['billingByProvider']['openai']['kind'], 'subscription')
        self.assertNotIn('accountReadFailure', result)
        self.assertEqual(self.events('harness.account_read_failed'), [])
        self.assertEqual(self.events('harness.account_observed')[0]['billing']['openai']['kind'], 'subscription')
        self.assertNotIn('quotaRecorded', self.events('harness.account_observed')[0])
        self.assertEqual(self.events('harness.invalidated'), [])
        self.assertEqual([call.args[0] for call in connection.return_value.call.call_args_list],
                         ['initialize', 'account/read', 'config/read'])

    def test_confirmed_stop_with_account_data_failure_restores_health_and_records_failure(self):
        from hey_my_buddy.errors import BoardError
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        from hey_my_buddy.buddy.harnesses.codex.protocol import CodexProtocolError
        with patch.object(account_probe, 'read', side_effect=BoardError('HARNESS_ACCOUNT_STOP_UNCONFIRMED', 'unknown stop')):
            self.assertNotEqual(self.health.refresh('codex', force=True)['status'], 'ready')
        with self.board.db.write() as db:
            db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [{}, CodexProtocolError('native-rpc-error', 'account RPC denied')]
            result = self.health.refresh('codex', force=True)
        self.assertEqual(result['status'], 'ready')
        self.assertIsNone(result['reason'])
        self.assertEqual(result['accountReadFailure']['reason'], account_reason('account-read-failed'))
        self.assertEqual(result['billingByProvider']['unknown']['kind'], 'unknown')

    def test_non_openai_native_provider_keeps_unknown_billing_without_rejecting_login(self):
        from hey_my_buddy.buddy.harnesses.codex import account_probe
        with patch.object(account_probe, 'owned_popen'), patch.object(account_probe, 'ProcessHandle') as handle, \
             patch.object(account_probe, 'Connection') as connection:
            handle.return_value.shutdown_confirmed.return_value = True
            connection.return_value.call.side_effect = [
                {}, {'account': {'type': 'apiKey'}},
                {'config': {'model_provider': 'configured-provider'}},
            ]
            result = self.health.refresh('codex', force=True)
            self.assertEqual([call.args[0] for call in connection.return_value.call.call_args_list],
                             ['initialize', 'account/read', 'config/read'])
        self.assertEqual(result['status'], 'ready')
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
                    db.execute("DELETE FROM harness_account_reads WHERE harness='codex'")
                handle.return_value.shutdown_confirmed.return_value = True
                connection.return_value.call.side_effect = [
                    {}, {'account': {'type': 'chatgpt', 'planType': 'plus'}}, {'config': configured},
                ]
                result = self.health.refresh('codex', force=True)
                self.assertEqual([call.args[0] for call in connection.return_value.call.call_args_list],
                                 ['initialize', 'account/read', 'config/read'])
                self.assertEqual(set(result['billingByProvider']), {'openai'})
                self.assertEqual(result['billingByProvider']['openai']['kind'], 'subscription')

    def test_unexpected_account_reader_exception_is_not_silently_recorded(self):
        with patch('hey_my_buddy.buddy.harnesses.codex.account_probe.read', side_effect=TypeError('fixture programming error')):
            with self.assertRaises(TypeError):
                self.health.refresh('codex', force=True)
        self.assertEqual(self.events('harness.account_read_failed'), [])
