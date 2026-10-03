"""Work-objective grouping is atomic display metadata, never Worker input."""
import json

from hey_my_buddy.protocol import schemas
from hey_my_buddy.errors import BoardError
from blackboard.tasks.test_workflow import WorkflowTestCase


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

    def test_description_is_atomic_immutable_display_metadata(self):
        board = self.board()
        metadata = {'title': 'Readable goal', 'description': 'User wording sentinel.'}
        run = self.submit(board, kind='worktree', objective=metadata, title='Short title', task='Task opening.\nMore detail.')
        identifier = run['objectiveId']
        summary = board.call('objective_timeline', {'objectiveId': identifier})
        self.assertEqual(summary['objective']['description'], metadata['description'])
        self.assertEqual(summary['objective']['counts']['accepted'], 0)
        self.assertEqual(summary['rows'][0]['taskSummary'], 'Task opening.')
        with board.store.db.read() as db:
            shape = [tuple(r) for r in db.execute('PRAGMA table_info(objectives)')]
            head = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        self.assertTrue(self.submit(board, kind='worktree', objective=metadata, title='Short title', task='Task opening.\nMore detail.')['duplicate'])
        with self.assertRaises(BoardError) as conflict:
            self.submit(board, kind='worktree', objective={**metadata, 'description': 'Changed'}, title='Short title', task='Task opening.\nMore detail.')
        self.assertEqual(conflict.exception.code, 'CONFLICT')
        with board.store.db.read() as db:
            self.assertEqual(head, db.execute('SELECT MAX(seq) FROM events').fetchone()[0])
            self.assertEqual(shape, [tuple(r) for r in db.execute('PRAGMA table_info(objectives)')])
            for row in db.execute('SELECT goal_json FROM workflow_runs'):
                self.assertNotIn(metadata['description'], row[0])
        self.register(board)
        self.assertNotIn(metadata['description'], json.dumps(self.claim(board)))
        for value in ('', ' ', 'x' * 301, None, False):
            with self.assertRaises(BoardError):
                schemas.normalize_presentation({'objective': {'title': 'ok', 'description': value}})
        self.assertEqual(len(schemas.normalize_presentation({'objective': {'title': 'ok', 'description': '字' * 300}})['objective']['description']), 300)

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

    def test_intent_title_is_not_replaced_by_worker_result_summary(self):
        board = self.board()
        submitted = self.submit(board, kind='worktree', task='Implement bounded routing\nDetailed request')
        self.register(board)
        claimed = self.claim(board, run_id=submitted['runId'])
        self.finish_turn(board, claimed)
        group = self.list_(board)['objectives'][0]
        self.assertEqual(group['title'], 'Implement bounded routing')
        self.assertEqual(group['titleSource'], 'task')
        self.assertTrue(group['summary'])
        timeline = board.call('objective_timeline', {'objectiveId':group['objectiveId']})
        self.assertEqual(timeline['rows'][0]['title'], group['title'])
        self.assertEqual(timeline['rows'][0]['summary'], group['summary'])

    def test_execution_spans_use_their_own_receipt_status_not_turn_disposition(self):
        board = self.board()
        self.register(board)
        for status in ('ok', 'failed', 'cancelled'):
            with self.subTest(status=status):
                submitted = self.submit(board, request_id=f'receipt-{status}', kind='worktree')
                claimed = self.claim(board, claim_request_id=f'claim-{status}', run_id=submitted['runId'])
                if status == 'cancelled':
                    board.call('workflow_cancel', {
                        'runId': submitted['runId'], 'commandId': 'cancel-receipt',
                        **self.controls[submitted['runId']],
                    })
                self.finish_turn(board, claimed, runner_status=status)
                timeline = board.call('objective_timeline', {'objectiveId': f"run:{submitted['runId']}"})
                execution = next(span for span in timeline['spans'] if span['kind'] == 'execution')
                self.assertEqual(execution.get('resultStatus'), status)
                self.assertTrue(execution['shutdownConfirmed'])

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

    def test_a_run_parked_on_its_host_has_one_open_wait_that_cancellation_closes(self):
        board, first, _second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        spans = board.call('objective_timeline', {'objectiveId': first['objectiveId']})['spans']
        mine = [span for span in spans if span['runId'] == first['runId']]
        # The host wait is the whole open interval; no open queue span overlaps it.
        self.assertEqual([span['kind'] for span in mine if span['endAt'] is None], ['host'])
        board.call('workflow_cancel', {'runId': first['runId'], 'reason': 'stop', **self.control(first)})
        spans = board.call('objective_timeline', {'objectiveId': first['objectiveId']})['spans']
        wait = next(span for span in spans if span['runId'] == first['runId'] and span['kind'] == 'host')
        self.assertEqual(wait['state'], 'cancelled')
        self.assertIsNotNone(wait['endAt'])
        self.assertGreaterEqual(wait['endAt'], wait['startAt'])

    def test_a_host_continuation_closes_the_superseded_wait(self):
        board, first, _second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        current = board.call('workflow_get', {'runId': first['runId']})
        self.continue_run(board, {**first, 'revision': current['revision']})
        spans = board.call('objective_timeline', {'objectiveId': first['objectiveId']})['spans']
        wait = next(span for span in spans if span['runId'] == first['runId'] and span['kind'] == 'host')
        self.assertEqual(wait['state'], 'superseded')
        self.assertIsNotNone(wait['endAt'])
        self.assertGreaterEqual(wait['endAt'], wait['startAt'])

    def test_a_requeue_after_a_host_continuation_starts_after_the_wait(self):
        board, first, _second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        current = board.call('workflow_get', {'runId': first['runId']})
        self.continue_run(board, {**first, 'revision': current['revision']})
        spans = board.call('objective_timeline', {'objectiveId': first['objectiveId']})['spans']
        wait = next(s for s in spans if s['runId'] == first['runId'] and s['kind'] == 'host')
        queued = [s for s in spans if s['runId'] == first['runId'] and s['kind'] == 'queue' and s['endAt'] is None]
        self.assertEqual(len(queued), 1)
        self.assertGreaterEqual(queued[0]['startAt'], wait['endAt'])

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


class RoutingSpanTests(WorkflowTestCase):
    def recorded_task(self, *, result_status='ok', shutdown=True):
        board = self.board()
        self.register(board)
        run = self.submit(board, kind='worktree')
        claim = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, claim, runner_status=result_status, shutdown=shutdown)
        return board, run['runId']

    def decision(self, board, task_id, *, output=None, status='completed', selected=None, profile_id='frozen-profile'):
        with board.store.db.write() as db:
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,profile_id,table_revision,reason,created_at)"
                       " VALUES('recorded-decision',?,'task',?,1,'frozen reason','2026-01-01T00:00:00Z')", (status, profile_id))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,selected_json,output_json,requested_json,created_at,updated_at)"
                       " VALUES('recorded-decision','recorded-request','select','fingerprint',?,?,?,?,'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')",
                       (task_id, json.dumps(selected) if selected else None, json.dumps(output) if output else None,
                        json.dumps({'budget': {'preset': 'deep'}})))

    def spans(self, board, task_id):
        from hey_my_buddy.blackboard.tasks.objectives import _routing_spans
        with board.store.db.read() as db:
            return _routing_spans(db, task_id, task_id)

    def test_frozen_selection_and_top_level_facts_come_from_exact_decision_task(self):
        board, task_id = self.recorded_task()
        selected = {'adapter': 'codex', 'provider': 'openai', 'model': 'historic-model', 'effort': 'high'}
        output = {'status': 'ok', 'decision': {'profileId': 'frozen-profile', 'reason': 'frozen reason',
                  'evidence': [], 'policyCheck': {'ignored': True}},
                  'policyCheck': {'taskPreference': {'ruleIndex': 2, 'outcome': 'matched'}, 'userPreference': 'alternative'},
                  'budget': {'preset': 'quick', 'toolCalls': 8},
                  'usage': {'elapsedMs': 120, 'toolCalls': 3, 'bytesRead': None},
                  'stopEvidence': {'shutdownConfirmed': True}}
        self.decision(board, task_id, output=output, selected=selected)
        before = self.spans(board, task_id)[0]
        self.assertEqual(before['decisionId'], 'recorded-decision')
        self.assertEqual(before['routing'], {'routerProfileIds': None, 'routerIdentities': None, 'routerIndex': None,
                         'routerRetryIntervalSeconds': None, 'routingBudget': None,
                         'routerProfileId': None, 'routerProfile': None, 'routerProblem': None, 'routingMode': None,
                         'selectedProfile': selected, 'reason': 'frozen reason',
                         'policyCheck': output['policyCheck'], 'budget': output['budget'], 'usage': output['usage']})
        # Changing the owner's current execution configuration cannot rewrite this history.
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.model','current-model') WHERE task_id=?", (task_id,))
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,profile_id,table_revision,reason,created_at)"
                       " VALUES('current-decision','completed','current task','current-profile',2,'current reason','2026-01-02T00:00:00Z')")
            db.execute("INSERT INTO decision_requests(decision_id,request_id,kind,input_fingerprint,task_id,selected_json,output_json,created_at,updated_at)"
                       " VALUES('current-decision','current-request','select','other-fingerprint','other-calculation',?,?,'2026-01-02T00:00:00Z','2026-01-02T00:00:00Z')",
                       (json.dumps({**selected, 'model': 'current-model'}), json.dumps({'budget': {'preset': 'deep'}})))
            for decision_id in ('recorded-decision', 'current-decision'):
                db.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at,updated_at)"
                           " VALUES(?,?,1,'resolved','2026-01-02T00:00:00Z','2026-01-02T00:00:00Z')", (decision_id, task_id))
        self.assertEqual(self.spans(board, task_id)[0], before)

    def test_missing_records_are_null_and_reads_do_not_backfill(self):
        board, task_id = self.recorded_task()
        before = self.spans(board, task_id)[0]
        self.assertIsNone(before['decisionId'])
        self.assertEqual(before['routing'], {'routerProfileIds': None, 'routerIdentities': None, 'routerIndex': None,
                         'routerRetryIntervalSeconds': None, 'routingBudget': None,
                         'routerProfileId': None, 'routerProfile': None, 'routerProblem': None, 'routingMode': None,
                         'selectedProfile': None, 'reason': None, 'policyCheck': None,
                         'budget': None, 'usage': {'elapsedMs': None, 'toolCalls': None, 'bytesRead': None}})
        self.decision(board, task_id)
        span = self.spans(board, task_id)[0]
        self.assertEqual(span['decisionId'], 'recorded-decision')
        for field in ('selectedProfile', 'policyCheck', 'budget'):
            self.assertIsNone(span['routing'][field])
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT output_json FROM decision_requests").fetchone()[0])
        self.assertEqual(self.spans(board, 'no-task-or-attempt'), [])
        with board.store.db.write() as db:
            db.execute("UPDATE decision_requests SET task_id='not-started'")
        self.assertEqual(self.spans(board, 'not-started'), [])

    def test_only_new_valid_abstention_is_marked_and_cancel_is_not(self):
        cases = [
            ('ok', True, 'needs-host', {'status': 'ok', 'decision': {'profileId': None, 'reason': 'insufficient', 'evidence': []}}, True),
            ('ok', False, 'needs-host', {'status': 'ok', 'decision': {'profileId': None, 'reason': 'insufficient', 'evidence': []}}, True),
            ('cancelled', True, 'needs-host', {'status': 'ok', 'decision': {'profileId': None, 'reason': 'insufficient', 'evidence': []}}, False),
            ('ok', True, 'needs-host', {'status': 'ok', 'decision': {'profileId': None, 'reason': 'legacy', 'evidenceIds': []}}, False),
            ('ok', True, 'needs-host', {'status': 'error', 'decision': {'profileId': None, 'reason': 'failed', 'evidence': []}}, False),
            ('ok', True, 'completed', {'status': 'ok', 'decision': {'profileId': None, 'reason': 'other', 'evidence': []}}, False),
        ]
        board, task_id = self.recorded_task()
        self.decision(board, task_id, profile_id=None)
        for result_status, shutdown, status, output, expected in cases:
            with self.subTest(result_status=result_status, shutdown=shutdown, status=status, output=output):
                with board.store.db.write() as db:
                    db.execute("UPDATE evaluation_decisions SET status=?", (status,))
                    db.execute("UPDATE decision_requests SET output_json=?", (json.dumps(output),))
                    db.execute("UPDATE attempts SET result_json=?, shutdown_confirmed=? WHERE task_id=?",
                               (json.dumps({'status': result_status}), int(shutdown), task_id))
                span = self.spans(board, task_id)[0]
                self.assertEqual(span.get('disposition') == 'abstention', expected)
                if not shutdown:
                    self.assertTrue(span['uncertain'])
                    self.assertIsNone(span['endAt'])

    def test_program_boundary_rejection_is_visible_after_native_success(self):
        board, task_id = self.recorded_task()
        self.decision(board, task_id, status='needs-host', profile_id=None,
                      output={'status': 'ok', 'code': 'router-out-of-bounds',
                              'decision': {'profileId': 'old-candidate', 'reason': 'chosen', 'evidence': []}})
        span = self.spans(board, task_id)[0]
        self.assertEqual(span['resultStatus'], 'ok')
        self.assertEqual(span['disposition'], 'routing-failed')
