"""Private upgrade refusal, admission fence and backup restoration."""
import json
from pathlib import Path
from unittest import mock
from buddy import backup, upgrade
from buddy.errors import BoardError
from support import BoardTestCase

class UpgradeTests(BoardTestCase):
    def test_nonidle_preflight_refuses_without_cancelling(self):
        board=self.board()
        run=board.call('task_submit', {'requestId':'busy', 'task':'protected work', 'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/echo','ok']})['task']['runId']
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, 'UPGRADE_NOT_IDLE')
        self.assertEqual(board.call('task_get', {'runId':run})['task']['state'], 'queued')

    def test_upgrade_journal_fences_admission_and_worker_claim_without_cancellation(self):
        board=self.board()
        marker=board.directory/'upgrade.json';marker.write_text('{}')
        self.assertEqual(board.call('ping', {})['status'], 'ok')
        with self.assertRaises(BoardError) as caught:
            board.call('task_submit', {'requestId':'blocked', 'task':'work', 'cwd':str(self.workdir()), 'adapter':'command', 'argv':['/bin/echo','ok']})
        self.assertEqual(caught.exception.code,'UPGRADE_IN_PROGRESS')
        with self.assertRaises(BoardError):
            board.call('worker_claim', {'workerId':'local','claimRequestId':'x','nonce':'x'*16})
        self.assertEqual(board.store.count_tasks(),0)

    def test_restore_verified_backup_preserves_regenerable_directories(self):
        board=self.board()
        control=board.directory/'controls/x.json';control.parent.mkdir();control.write_text('{"original":true}')
        native=board.directory/'harnesses/zcode/retained';native.mkdir(parents=True);(native/'sessions.sqlite').write_bytes(b'native')
        current=Path(backup.create(board.store)['path'])
        control.write_text('{"changed":true}')
        with board.store.db.write() as connection:
            connection.execute("INSERT INTO meta(key,value) VALUES('post-backup','changed')")
        upgrade.restore(board.directory,current)
        self.assertEqual(json.loads(control.read_text()),{'original':True})
        self.assertEqual((native/'sessions.sqlite').read_bytes(),b'native')
        with board.store.db.read() as connection:
            self.assertIsNone(connection.execute("SELECT value FROM meta WHERE key='post-backup'").fetchone())
        upgrade.idle_snapshot(board.directory)

    def test_startup_timeout_never_signals_a_daemon_or_cancels_work(self):
        board=self.board()
        child=mock.Mock()
        child.poll.return_value=None
        with mock.patch('buddy.upgrade.subprocess.Popen',return_value=child), mock.patch('buddy.upgrade.time.monotonic',side_effect=[0,46]):
            with self.assertRaises(BoardError) as caught:
                upgrade.start(board.directory, self.directory/'runtime-target')
        self.assertEqual(caught.exception.code,'UPGRADE_SHUTDOWN_UNCONFIRMED')
        child.terminate.assert_not_called()
        child.kill.assert_not_called()

    def test_upgrade_refuses_another_schema_without_conversion(self):
        board=self.board()
        with board.store.db.write() as connection:
            connection.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
        with self.assertRaises(BoardError) as caught:
            upgrade.idle_snapshot(board.directory)
        self.assertEqual(caught.exception.code, 'UNSUPPORTED_SCHEMA')
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],'999')

    def test_retained_event_prefix_detects_rewrites_but_allows_new_events(self):
        board=self.board()
        board.call('task_submit',{'requestId':'event-fixture','task':'private','cwd':str(self.workdir()),'adapter':'command','argv':['/bin/true']})
        with board.store.db.connect() as connection:
            before=backup.database_snapshot(connection)
        with board.store.db.write() as connection:
            board.store._append_event(connection, 'private-lifecycle-observation', payload={})
        with board.store.db.connect() as connection:
            appended=backup.database_snapshot(connection,event_head=before['eventHead'])
        self.assertEqual(before['fingerprints']['events'],appended['fingerprints']['events'])
        with board.store.db.write() as connection:
            connection.execute("UPDATE events SET payload_json=? WHERE seq=(SELECT MIN(seq) FROM events)", ('{"tampered":true}',))
        with board.store.db.connect() as connection:
            after=backup.database_snapshot(connection,event_head=before['eventHead'])
        self.assertNotEqual(before['fingerprints']['events'],after['fingerprints']['events'])
