"""Schema-24 service stop signals are mechanical history, not Host notes."""
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store.upgrade import upgrade_schema_24_to_25


class UpgradeSignalNoteTests(unittest.TestCase):
    def test_service_stop_signal_is_cleared_without_losing_authored_note(self):
        with TemporaryDirectory(prefix='upgrade-signal-note-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                events = [row[0] for row in connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 2')]
                self.assertEqual(len(events), 2)
                for seq, reason in zip(events, ('service stop: signal', 'service stop: Host requested shutdown  ')):
                    payload = {'reason': reason, 'actor': {'role': 'service', 'id': 'fixture'}}
                    connection.execute("UPDATE events SET kind='workflow.cancelled',payload_json=? WHERE seq=?",
                                       (json.dumps(payload), seq))
            upgrade_schema_24_to_25(fixture['state'])
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                payloads = [json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                          (seq,)).fetchone()[0]) for seq in events]
            self.assertNotIn('reason', payloads[0])
            self.assertNotIn('note', payloads[0])
            self.assertEqual(payloads[1]['note'], 'Host requested shutdown  ')
            self.assertNotIn('reason', payloads[1])


if __name__ == '__main__':
    unittest.main()
