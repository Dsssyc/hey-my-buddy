"""Work-objective grouping is atomic display metadata, never Worker input."""
import json

from buddy import schemas
from buddy.errors import BoardError
from test_workflow import WorkflowTestCase


class ObjectiveAdmissionTests(WorkflowTestCase):
    def test_group_and_title_replay_are_separate_from_execution(self):
        board = self.board()
        first = self.submit(board, kind='worktree', objective={'title':'Human group sentinel'}, title='Human run sentinel')
        identifier = first['objectiveId']
        self.assertTrue(identifier.startswith('obj-'))
        self.assertEqual(first['title'], 'Human run sentinel')
        replay = self.submit(board, kind='worktree', objective={'title':'Human group sentinel'}, title='Human run sentinel')
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['objectiveId'], identifier)
        second = self.submit(board, request_id='second', kind='worktree', objectiveId=identifier)
        self.assertEqual(second['objectiveId'], identifier)
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM objectives').fetchone()[0], 1)
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (first['runId'],)).fetchone()
            self.assertNotIn('Human group sentinel', row['spec_json'])
            self.assertNotIn('Human run sentinel', row['spec_json'])
            group = db.execute('SELECT * FROM objectives').fetchone()
            self.assertGreater(group['activity_seq'], 0)
        self.register(board)
        claim = self.claim(board)
        text = json.dumps(claim)
        for hidden in ('Human group sentinel', 'Human run sentinel', identifier):
            self.assertNotIn(hidden, text)

    def test_changed_presentation_conflicts_without_creating_another_group(self):
        board = self.board()
        self.submit(board, objective={'title':'one'}, title='first')
        for extra in ({'objective':{'title':'two'}, 'title':'first'},
                      {'objective':{'title':'one'}, 'title':'changed'}, {}):
            with self.assertRaises(BoardError) as error:
                self.submit(board, **extra)
            self.assertEqual(error.exception.code, 'CONFLICT')
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM objectives').fetchone()[0], 1)

    def test_a_foreign_host_cannot_attach_and_unknown_group_admits_nothing(self):
        board = self.board()
        first = self.submit(board, objective={'title':'one'})
        for request_id, host_id, identifier, code in (
            ('foreign','host-2',first['objectiveId'],'CONFLICT'),
            ('unknown','host-1','obj-unknown','NOT_FOUND'),
        ):
            with self.assertRaises(BoardError) as error:
                self.submit(board, request_id=request_id, host_id=host_id, objectiveId=identifier)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(board.store.count_tasks(), 1)

    def test_metadata_input_is_strict_and_omission_keeps_original_fingerprint(self):
        board = self.board()
        for extra in ({'title':None},{'title':False},{'title':' '}, {'title':'x'*201},
                      {'objective':None},{'objective':{'title':'ok','extra':1}},
                      {'objective':{'title':'ok'},'objectiveId':None},{'objectiveId':'run:old'}):
            with self.assertRaises(BoardError):
                self.submit(board, **extra)
        old = schemas.workflow_request_fingerprint({'task':'x'}, {'kind':'existing'}, 'host')
        self.assertEqual(old, schemas.workflow_request_fingerprint({'task':'x'}, {'kind':'existing'}, 'host', presentation={}))
        self.assertEqual(board.store.count_tasks(), 0)

    def test_snapshot_reads_do_not_advance_activity(self):
        board = self.board()
        first = self.submit(board, objective={'title':'read only index'})
        with board.store.db.read() as db:
            before = tuple(db.execute('SELECT activity_seq,activity_at FROM objectives').fetchone())
        for _ in range(2):
            board.call('workflow_get', {'runId':first['runId']})
            board.call('console_snapshot', {})
        with board.store.db.read() as db:
            self.assertEqual(tuple(db.execute('SELECT activity_seq,activity_at FROM objectives').fetchone()), before)


class ObjectiveReadTests(WorkflowTestCase):
    """objective_list / objective_timeline are bounded, read-only derivations."""

    def grouped_board(self):
        board = self.board()
        first = self.submit(board, request_id='g-1', kind='worktree', objective={'title': 'Ship the timeline'},
                            title='Backend')
        second = self.submit(board, request_id='g-2', kind='worktree', objectiveId=first['objectiveId'], title='Frontend')
        loose = self.submit(board, request_id='loose', kind='worktree', task='standalone work\nsecond line')
        return board, first, second, loose

    def list_(self, board, **params):
        return board.call('objective_list', params)

    def test_list_groups_objectives_and_standalone_roots_by_latest_activity(self):
        board, first, second, loose = self.grouped_board()
        listed = self.list_(board)
        self.assertEqual(listed['total'], 2)
        self.assertIsNone(listed['nextCursor'])
        self.assertFalse(listed['changed'])
        ids = [item['objectiveId'] for item in listed['objectives']]
        self.assertEqual(ids, [f"run:{loose['runId']}", first['objectiveId']], 'latest activity first')
        standalone, grouped = listed['objectives']
        self.assertEqual(standalone['kind'], 'standalone')
        self.assertEqual(standalone['title'], 'standalone work')
        self.assertEqual(standalone['titleSource'], 'task')
        self.assertEqual(grouped['kind'], 'objective')
        self.assertEqual(grouped['title'], 'Ship the timeline')
        self.assertEqual((grouped['counts']['roots'], grouped['counts']['helpers']), (2, 0))
        self.assertEqual(grouped['lastActivitySeq'], max(grouped['lastActivitySeq'], 1))
        self.assertEqual(sorted(grouped['rootRunIds']), sorted([first['runId'], second['runId']]))
        self.assertEqual(grouped['sourceHostId'], 'host-1')
        # Activity on an older group moves it up; reads never do.
        self.register(board)
        claim = self.claim(board, run_id=first['runId'])
        self.finish_turn(board, claim)
        self.list_(board)
        self.assertEqual(self.list_(board)['objectives'][0]['objectiveId'], first['objectiveId'])

    def test_filters_match_members_and_cursor_is_bound_to_them(self):
        board, first, second, loose = self.grouped_board()
        page = self.list_(board, limit=1)
        self.assertEqual(len(page['objectives']), 1)
        self.assertTrue(page['nextCursor'])
        rest = self.list_(board, limit=1, before=page['nextCursor'])
        self.assertEqual(len(rest['objectives']), 1)
        self.assertNotEqual(rest['objectives'][0]['objectiveId'], page['objectives'][0]['objectiveId'])
        self.assertIsNone(rest['nextCursor'])
        self.assertFalse(rest['changed'])
        with self.assertRaises(BoardError) as error:
            self.list_(board, limit=1, before=page['nextCursor'], query='Frontend')
        self.assertEqual(error.exception.code, 'INVALID_ARGUMENT')
        for bad in ({'before': 'not a cursor'}, {'filter': 'mine'}, {'limit': 101}, {'unknown': 1}):
            with self.assertRaises(BoardError):
                self.list_(board, **bad)
        # A filter keeps overall counts and reports how many members matched.
        queried = self.list_(board, query='standalone')
        self.assertEqual([item['objectiveId'] for item in queried['objectives']], [f"run:{loose['runId']}"])
        self.assertEqual(queried['objectives'][0]['matchingRuns'], 1)

    def test_changed_signals_activity_after_the_first_page(self):
        board, first, second, loose = self.grouped_board()
        page = self.list_(board, limit=1)
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']))
        rest = self.list_(board, limit=1, before=page['nextCursor'])
        self.assertTrue(rest['changed'])

    def test_timeline_rows_spans_markers_and_unknown_stop(self):
        board, first, second, loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']))
        self.finish_turn(board, self.claim(board, claim_request_id='c2', run_id=second['runId']),
                         disposition='attention', shutdown=False)
        timeline = board.call('objective_timeline', {'objectiveId': first['objectiveId']})
        self.assertEqual([row['runId'] for row in timeline['rows']], [first['runId'], second['runId']])
        self.assertEqual(timeline['rows'][0]['title'], 'Backend')
        self.assertTrue(timeline['scopeComplete'])
        executions = [span for span in timeline['spans'] if span['kind'] == 'execution']
        self.assertEqual(len(executions), 2)
        by_run = {span['runId']: span for span in executions}
        self.assertEqual(by_run[first['runId']]['state'], 'finished')
        self.assertTrue(by_run[first['runId']]['shutdownConfirmed'])
        self.assertFalse(by_run[first['runId']]['uncertain'])
        self.assertEqual(by_run[first['runId']]['configuration']['model'], 'deepseek-flash')
        # An unconfirmed stop stays uncertain and open-ended: it is never drawn as ended.
        self.assertEqual(by_run[second['runId']]['state'], 'uncertain')
        self.assertIsNone(by_run[second['runId']]['endAt'])
        self.assertFalse(by_run[second['runId']]['shutdownConfirmed'])
        self.assertIn('queue', {span['kind'] for span in timeline['spans']})
        self.assertIn('dispatch', {marker['kind'] for marker in timeline['events']})
        for span in timeline['spans']:
            self.assertFalse(span['clockSkew'])
            self.assertTrue(span['startAt'].endswith('Z'))
        self.assertTrue(timeline['rows'][0]['shutdownConfirmed'])
        self.assertFalse(timeline['rows'][1]['shutdownConfirmed'])
        self.assertTrue(by_run[second['runId']]['uncertain'])
        self.assertEqual([row['depth'] for row in timeline['rows']], [0, 0])
        self.assertEqual(timeline['events'][0]['label'], '派发')
        self.assertNotIn('controlToken', json.dumps(timeline))

    def test_timeline_truncation_filters_and_identity(self):
        board, first, second, loose = self.grouped_board()
        limited = board.call('objective_timeline', {'objectiveId': first['objectiveId'], 'limit': 1})
        self.assertEqual(len(limited['rows']), 1)
        self.assertTrue(limited['truncated']['rows'])
        self.assertEqual(limited['totals']['rows'], 2)
        self.assertFalse(limited['scopeComplete'])
        filtered = board.call('objective_timeline', {'objectiveId': first['objectiveId'], 'query': 'no such text'})
        self.assertEqual(filtered['rows'], [])
        self.assertTrue(filtered['filtered'])
        self.assertFalse(filtered['scopeComplete'])
        standalone = board.call('objective_timeline', {'objectiveId': f"run:{loose['runId']}"})
        self.assertEqual([row['runId'] for row in standalone['rows']], [loose['runId']])
        for identifier, code in (('obj-missing', 'NOT_FOUND'), (f"run:{first['runId']}", 'NOT_FOUND'),
                                 ('something', 'INVALID_ARGUMENT')):
            with self.assertRaises(BoardError) as error:
                board.call('objective_timeline', {'objectiveId': identifier})
            self.assertEqual(error.exception.code, code)

    def test_reads_are_host_only_and_do_not_move_activity(self):
        board, first, second, loose = self.grouped_board()
        self.register(board)
        claim = self.claim(board, run_id=first['runId'])
        credential = claim['claim']['agentCredential']
        for operation, params in (('objective_list', {}), ('objective_timeline', {'objectiveId': first['objectiveId']})):
            with self.assertRaises(BoardError) as error:
                board.call(operation, {**params, 'credential': credential})
            self.assertEqual(error.exception.code, 'FORBIDDEN')
        with board.store.db.read() as db:
            before = [tuple(row) for row in db.execute('SELECT objective_id, activity_seq FROM objectives')]
        self.list_(board)
        board.call('objective_timeline', {'objectiveId': first['objectiveId']})
        with board.store.db.read() as db:
            after = [tuple(row) for row in db.execute('SELECT objective_id, activity_seq FROM objectives')]
        self.assertEqual(before, after)
