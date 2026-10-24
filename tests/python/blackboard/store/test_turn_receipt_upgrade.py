"""Schema-28 turn copies are checked against receipts before being retired."""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.blackboard.store import upgrade
from hey_my_buddy.blackboard.store.db import UPGRADE_BASE_SCHEMA, canonical_json
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.tasks.workflow import WorkflowCoordinator, MAX_RESULT_SUMMARY
from hey_my_buddy.errors import BoardError


REMOVED = ('disposition', 'outcome_json', 'outcome_truncated_json', 'provenance_json',
           'session_id', 'prompt_sha256', 'input_sha256', 'turn_result_path', 'sealed_artifacts_json')


class TurnReceiptUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = load_fixture(Path(tempfile.mkdtemp(prefix='turn-receipt-upgrade-')))
        self.state = self.fixture['state']
        self.connection = sqlite3.connect(self.state / 'board.sqlite3')
        self.connection.row_factory = sqlite3.Row
        self.addCleanup(self.connection.close)

    def test_old_writer_stock_keeps_every_result_field_and_receipt_byte(self):
        connection = self.connection
        last_rowid = connection.execute('SELECT MAX(rowid) FROM workflow_turns').fetchone()[0]
        connection.execute('UPDATE workflow_turns SET rowid=rowid*11+?', (last_rowid + 4,))
        connection.commit()
        stock = {row['turn_id']: dict(row) for row in connection.execute(
            'SELECT rowid AS original_rowid,* FROM workflow_turns')}
        receipts = dict(connection.execute('SELECT attempt_id,result_json FROM attempts'))
        self.assertEqual(sum(row['outcome_truncated_json'] is not None for row in stock.values()), 2)
        self.assertTrue(any(row['turn_result_path'] is not None for row in stock.values()))
        self.assertTrue(any(row['state'] == 'failed' and json.loads(row['sealed_artifacts_json'])
                            for row in stock.values()))
        rejected = stock[self.fixture['edgeStock']['rejected-turn']['turnId']]
        self.assertEqual(rejected['state'], 'failed')
        self.assertIsNotNone(json.loads(receipts[rejected['attempt_id']])['result']['turn']['outcome'])
        rejected_seal = stock[self.fixture['edgeStock']['rejected-seal']['turnId']]
        self.assertEqual(rejected_seal['state'], 'failed')
        self.assertEqual(rejected_seal['sealed_artifacts_json'], '[]')
        self.assertEqual(json.loads(receipts[rejected_seal['attempt_id']])['result']['workspaceSeal'],
                         {'manifestSha256': 'not-a-hash'})
        for row in stock.values():
            self.assertEqual(canonical_json(WorkflowCoordinator._sealed_artifacts(
                connection, run_id=row['run_id'], turn_id=row['turn_id'])), row['sealed_artifacts_json'])
            for column in REMOVED:
                if row['state'] != 'returned' and column != 'sealed_artifacts_json':
                    self.assertIsNone(row[column], (row['turn_id'], column))

        upgrade.migrate_schema(self.state)
        self.assertFalse(set(REMOVED) & {row['name'] for row in connection.execute('PRAGMA table_info(workflow_turns)')})
        self.assertEqual(dict(connection.execute('SELECT attempt_id,result_json FROM attempts')), receipts)
        for row in connection.execute(
                'SELECT t.rowid AS original_rowid,t.*,a.result_json,a.token_usage_json,a.adapter AS execution_adapter'
                ' FROM workflow_turns t LEFT JOIN attempts a USING(attempt_id)'):
            old = stock[row['turn_id']]
            self.assertEqual(row['original_rowid'], old['original_rowid'])
            for column in upgrade.RETAINED_COLUMN_PROJECTIONS['workflow_turns']:
                self.assertEqual(row[column], old[column], column)
            outcome = json.loads(old['outcome_json']) if old['outcome_json'] is not None else None
            common = {'sessionId': old['session_id'], 'disposition': old['disposition'],
                      'outcomeTruncated': json.loads(old['outcome_truncated_json'])
                      if old['outcome_truncated_json'] is not None else None}
            for compact, audit in ((True, False), (False, False), (False, True)):
                with self.subTest(turn=row['turn_id'], compact=compact, audit=audit):
                    view = WorkflowCoordinator._turn_view(connection, row, compact=compact, include_audit=audit)
                    expected = dict(common)
                    fields = set(common) | {'summary', 'summaryTruncated', 'remaining', 'request',
                                            'artifactReferences', 'promptSha256', 'inputSha256', 'input',
                                            'outcome', 'provenance', 'turnResultPath', 'sealedArtifacts'}
                    if not compact and outcome is not None:
                        summary = outcome.get('summary') or ''
                        expected.update(summary=summary if audit else summary[:MAX_RESULT_SUMMARY],
                                        remaining=outcome.get('remaining', []))
                        if audit:
                            expected.update(request=outcome.get('request'), artifactReferences=outcome.get('artifacts', []))
                        else:
                            expected['summaryTruncated'] = len(summary) > MAX_RESULT_SUMMARY
                    if audit:
                        expected.update(promptSha256=old['prompt_sha256'], inputSha256=old['input_sha256'],
                                        input=json.loads(old['input_json']), outcome=outcome,
                                        provenance=json.loads(old['provenance_json']) if old['provenance_json'] else None,
                                        turnResultPath=old['turn_result_path'],
                                        sealedArtifacts=[WorkflowCoordinator._bounded_path_fields(
                                            item, ('changedPaths', 'outsideScope'))
                                            for item in json.loads(old['sealed_artifacts_json'])])
                    self.assertEqual(json.dumps({key: view[key] for key in fields if key in view}, sort_keys=True),
                                     json.dumps(expected, sort_keys=True))
                    if audit:
                        self.assertEqual(json.dumps(view['sealedArtifacts']), json.dumps(expected['sealedArtifacts']))

        store = BoardStore(self.state)
        store.initialize()
        self.addCleanup(store.db.close)
        for entry in self.fixture['edgeStock'].values():
            view = store.workflow.get({'runId': entry['runId'], 'includeAudit': True})
            self.assertIsNone(view['currentTurn']['disposition'])
            self.assertIsNone(view['audit']['turns'][0]['outcome'])

    def test_every_conflicting_copy_aborts_and_rolls_back(self):
        connection = self.connection
        returned = connection.execute("SELECT turn_id FROM workflow_turns WHERE state='returned' LIMIT 1").fetchone()[0]
        failed = self.fixture['edgeStock']['partial-failed']['turnId']
        replacements = {'disposition': 'attention', 'outcome_json': '{}', 'outcome_truncated_json': '{"changed":true}',
                        'provenance_json': '{}', 'session_id': 'changed', 'prompt_sha256': 'b' * 64,
                        'input_sha256': 'b' * 64, 'turn_result_path': '/fictional/changed', 'sealed_artifacts_json': '[]'}
        for turn_id in (returned, failed):
            for column, value in replacements.items():
                with self.subTest(turn=turn_id, column=column):
                    original = connection.execute(f'SELECT {column} FROM workflow_turns WHERE turn_id=?', (turn_id,)).fetchone()[0]
                    self.assertNotEqual(original, value)
                    connection.execute(f'UPDATE workflow_turns SET {column}=? WHERE turn_id=?', (value, turn_id))
                    connection.commit()
                    before = (self.state / 'board.sqlite3').read_bytes()
                    with self.assertRaises(BoardError) as caught:
                        upgrade.migrate_schema(self.state)
                    self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
                    self.assertEqual(caught.exception.details['field'], column)
                    self.assertEqual((self.state / 'board.sqlite3').read_bytes(), before)
                    self.assertEqual(connection.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
                                     str(UPGRADE_BASE_SCHEMA))
                    connection.execute(f'UPDATE workflow_turns SET {column}=? WHERE turn_id=?', (original, turn_id))
                    connection.commit()

    def test_receipt_input_digest_must_still_bind_the_frozen_input(self):
        self.connection.execute("UPDATE workflow_turns SET input_json=json_set(input_json,'$.context.task','changed')"
                                " WHERE state='returned'")
        self.connection.commit()
        with self.assertRaises(BoardError) as caught:
            upgrade.migrate_schema(self.state)
        self.assertEqual(caught.exception.code, 'UPGRADE_INVALID_BOARD')
        self.assertEqual(caught.exception.details['field'], 'input_sha256')
