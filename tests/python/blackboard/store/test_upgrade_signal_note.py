"""Schema-24 service stop signals are mechanical history, not Host notes."""
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store.upgrade import upgrade_schema_24_to_25
from hey_my_buddy.errors import BoardError


class UpgradeSignalNoteTests(unittest.TestCase):
    def test_service_stop_signal_is_cleared_without_losing_authored_note(self):
        with TemporaryDirectory(prefix='upgrade-signal-note-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                events = [row[0] for row in connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 2')]
                self.assertEqual(len(events), 2)
                for seq, reason in zip(events, ('service stop: signal', 'service stop: Host requested shutdown  ')):
                    payload = {'reason': reason, 'stopInitiator': 'service'}
                    connection.execute("UPDATE events SET kind='workflow.cancelled',payload_json=?,"
                                       " actor_role='service',actor_id='fixture' WHERE seq=?",
                                       (json.dumps(payload), seq))
            upgrade_schema_24_to_25(fixture['state'])
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                payloads = [json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                          (seq,)).fetchone()[0]) for seq in events]
            self.assertNotIn('reason', payloads[0])
            self.assertIsNone(payloads[0]['note'])
            self.assertEqual(payloads[1]['note'], 'Host requested shutdown  ')
            self.assertNotIn('reason', payloads[1])

    def test_default_looking_text_survives_outside_its_writer(self):
        with TemporaryDirectory(prefix='upgrade-note-sources-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                integration = connection.execute('SELECT integration_id,verification_json FROM workflow_integrations LIMIT 1').fetchone()
                verification = json.loads(integration[1])
                verification['reason'] = 'Host cancelled'
                connection.execute('UPDATE workflow_integrations SET reason=?,verification_json=? WHERE integration_id=?',
                                   ('Host cancelled', json.dumps(verification), integration[0]))
                request = connection.execute('SELECT request_id,decision_json FROM workflow_requests LIMIT 1').fetchone()
                self.assertIsNotNone(request)
                decision = json.loads(request[1] or '{}')
                decision['reason'] = ''
                connection.execute('UPDATE workflow_requests SET decision_json=? WHERE request_id=?',
                                   (json.dumps(decision), request[0]))
                run = connection.execute('SELECT run_id,execution_workspace_json FROM workflow_runs LIMIT 1').fetchone()
                workspace = json.loads(run[1])
                workspace['accessReason'] = 'Host cancelled'
                connection.execute('UPDATE workflow_runs SET execution_workspace_json=? WHERE run_id=?',
                                   (json.dumps(workspace), run[0]))
                event = connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 1').fetchone()[0]
                connection.execute("UPDATE events SET kind='workflow.cancelled',payload_json=?,"
                                   " actor_role='service',actor_id='stop' WHERE seq=?",
                                   (json.dumps({'reason': 'Router superseded'}), event))
            upgrade_schema_24_to_25(fixture['state'])
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                row = connection.execute('SELECT note,verification_json FROM workflow_integrations WHERE integration_id=?',
                                         (integration[0],)).fetchone()
                self.assertEqual(row[0], 'Host cancelled')
                self.assertEqual(json.loads(row[1])['note'], 'Host cancelled')
                self.assertEqual(json.loads(connection.execute('SELECT decision_json FROM workflow_requests WHERE request_id=?',
                                                               (request[0],)).fetchone()[0])['note'], None)
                self.assertEqual(json.loads(connection.execute('SELECT execution_workspace_json FROM workflow_runs WHERE run_id=?',
                                                               (run[0],)).fetchone()[0])['accessNote'], 'Host cancelled')
                self.assertEqual(json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?',
                                                               (event,)).fetchone()[0])['reason'], 'Router superseded')

    def test_manual_helper_cancel_binds_one_continuation(self):
        with TemporaryDirectory(prefix='upgrade-helper-cancel-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            run_id = fixture['stableIds']['delivered']
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                event = connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 1').fetchone()[0]
                connection.execute("INSERT INTO workflow_continuations"
                                   "(continuation_id,run_id,kind,expected_revision,input_text,input_bytes,reason,"
                                   "helper_policy,state,created_at) VALUES(?,?,'manual',1,'continue',8,NULL,'cancel','queued',?)",
                                   ('manual-one', run_id, 'manual-time'))
                connection.execute("UPDATE events SET kind='workflow.helper_cancelled',payload_json=?,"
                                   "actor_role='host',actor_id='host',created_at='manual-time' WHERE seq=?",
                                   (json.dumps({'reason': 'manual continuation cancelled live helpers',
                                                'cancelledByRunId': run_id}), event))
            upgrade_schema_24_to_25(fixture['state'])
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                payload = json.loads(connection.execute('SELECT payload_json FROM events WHERE seq=?', (event,)).fetchone()[0])
                self.assertEqual(payload['continuationId'], 'manual-one')
                self.assertIsNone(payload['note'])

    def test_unlinked_manual_helper_cancel_refuses_upgrade(self):
        with TemporaryDirectory(prefix='upgrade-helper-cancel-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            run_id = fixture['stableIds']['delivered']
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                event = connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 1').fetchone()[0]
                connection.execute("UPDATE events SET kind='workflow.helper_cancelled',payload_json=?,"
                                   "actor_role='host',actor_id='host' WHERE seq=?",
                                   (json.dumps({'reason': 'manual continuation cancelled live helpers',
                                                'cancelledByRunId': run_id}), event))
            with self.assertRaises(BoardError) as caught:
                upgrade_schema_24_to_25(fixture['state'])
            self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')

    def test_review_rejection_note_clears_only_for_matching_empty_event_reason(self):
        for reason, expected in ((None, None), ('Host cancelled', 'review opinion')):
            with self.subTest(reason=reason), TemporaryDirectory(prefix='upgrade-review-note-') as root:
                fixture = load_fixture(Path(root) / 'fixture')
                run_id = fixture['stableIds']['delivered']
                with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                    artifact = connection.execute('SELECT final_artifact_id FROM workflow_runs WHERE run_id=?',
                                                  (run_id,)).fetchone()[0]
                    self.assertIsNotNone(artifact)
                    connection.execute('UPDATE tasks SET review_rejected_at=?,review_rejection_note=? WHERE task_id=?',
                                       ('review-time', 'review opinion', run_id))
                    event = connection.execute('SELECT seq FROM events ORDER BY seq LIMIT 1').fetchone()[0]
                    connection.execute("UPDATE events SET kind='workflow.review_rejected',task_id=?,payload_json=?,"
                                       "actor_role='host',actor_id='host' WHERE seq=?",
                                       (run_id, json.dumps({'artifactId': artifact, 'reason': reason}), event))
                upgrade_schema_24_to_25(fixture['state'])
                with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                    self.assertEqual(connection.execute('SELECT review_rejection_note FROM tasks WHERE task_id=?',
                                                        (run_id,)).fetchone()[0], expected)

    def test_scope_and_continuation_defaults_require_their_recorded_source(self):
        with TemporaryDirectory(prefix='upgrade-scope-notes-') as root:
            fixture = load_fixture(Path(root) / 'fixture')
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                scope = connection.execute('SELECT run_id FROM workflow_scope_versions WHERE scope_version=1 LIMIT 1').fetchone()[0]
                connection.execute('UPDATE workflow_scope_versions SET reason=? WHERE run_id=? AND scope_version=1',
                                   ('Host cancelled', scope))
                connection.execute("INSERT INTO workflow_continuations"
                                   "(continuation_id,run_id,kind,expected_revision,input_text,input_bytes,reason,"
                                   "state,created_at) VALUES(?,?,'manual',1,'continue',8,?,'queued','sample')",
                                   ('manual-note', scope, 'owned-micro-task-boundary'))
            upgrade_schema_24_to_25(fixture['state'])
            with sqlite3.connect(fixture['state'] / 'board.sqlite3') as connection:
                self.assertEqual(connection.execute('SELECT note FROM workflow_scope_versions WHERE run_id=? AND scope_version=1',
                                                    (scope,)).fetchone()[0], 'Host cancelled')
                self.assertEqual(connection.execute('SELECT note FROM workflow_continuations WHERE continuation_id=?',
                                                    ('manual-note',)).fetchone()[0], 'owned-micro-task-boundary')


if __name__ == '__main__':
    unittest.main()
