"""Work-macro-task grouping is atomic display metadata, never Worker input."""
import json
from unittest.mock import patch

from hey_my_buddy.protocol import schemas
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing import router_sequence
from hey_my_buddy.blackboard.store.db import canonical_json, sha256_text
from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy


class ProjectLabelTests(WorkflowTestCase):
    """Read saved Git provenance using the workflow suite's workspace double."""

    def submit_from_repository(self, board, cwd, repository, *, request_id, grouped=True):
        original = self.workspace._manifest

        def manifest(**params):
            value = original(**params)
            if repository is None:
                value["snapshot"].pop("repositoryPath")
            else:
                value["snapshot"]["repositoryPath"] = repository
            value["manifestSha256"] = sha256_text(canonical_json({
                key: item for key, item in value.items() if key != "manifestSha256"
            }))
            return value

        with patch.object(self.workspace, "_manifest", side_effect=manifest):
            return self.submit(board, request_id=request_id, cwd=str(cwd), kind="worktree",
                               **({"macroTask": {"title": request_id}} if grouped else {}))

    def assert_projects(self, board, submitted, *, label, path, identifier):
        expected = {"id": identifier, "path": str(path), "label": label}
        metadata = board.call("task_get", {"runId": submitted["runId"]})["task"]["lineage"]
        self.assertEqual(metadata["project"], expected)
        group_id = submitted.get("macroTaskId") or f"run:{submitted['runId']}"
        summary = next(row for row in board.call("macro_task_list", {})["macroTasks"]
                       if row["macroTaskId"] == group_id)
        self.assertEqual(summary["project"], expected)
        timeline = board.call("macro_task_timeline", {"macroTaskId": group_id})
        self.assertEqual(timeline["macroTask"]["project"], expected)

    def test_main_worktree_and_managed_checkout_share_repository_name_and_identity(self):
        board = self.board()
        main = self.workdir("repository-name")
        worktree = self.workdir("other-worktree")
        # The double supplies one repository identity for the three source
        # checkouts; the read side consumes the persisted common Git directory.
        with patch.object(self.workspace, "_repository_root", return_value=str(main)):
            first = self.submit_from_repository(board, main, str(main / ".git"), request_id="checkout-0")
            self.assert_projects(board, first, label="repository-name", path=main,
                                 identifier=f"repo:{main}")
            managed = board.call("task_get", {"runId": first["runId"]})["task"]["cwd"]
            for index, cwd in enumerate((worktree, managed), 1):
                with self.subTest(checkout=str(cwd)):
                    submitted = self.submit_from_repository(
                        board, cwd, str(main / ".git"), request_id=f"checkout-{index}",
                    )
                    self.assert_projects(board, submitted, label="repository-name", path=cwd,
                                         identifier=f"repo:{main}")

    def test_bare_repository_uses_its_own_name_for_grouped_and_standalone_runs(self):
        board = self.board()
        cwd = self.workdir("execution-checkout")
        for grouped in (True, False):
            with self.subTest(grouped=grouped):
                submitted = self.submit_from_repository(
                    board, cwd, "/fictional/repositories/repository.git/",
                    request_id=f"bare-{grouped}", grouped=grouped,
                )
                self.assert_projects(board, submitted, label="repository.git", path=cwd,
                                     identifier=f"repo:{cwd}")

    def test_missing_repository_location_keeps_submitted_directory_name(self):
        board = self.board()
        cwd = self.workdir("plain-directory")
        for grouped in (True, False):
            with self.subTest(grouped=grouped):
                submitted = self.submit_from_repository(
                    board, cwd, None, request_id=f"plain-{grouped}", grouped=grouped,
                )
                self.assert_projects(board, submitted, label="plain-directory", path=cwd,
                                     identifier=f"repo:{cwd}")


class MacroTaskAdmissionTests(WorkflowTestCase):
    def test_deleting_standalone_tree_fences_its_run(self):
        board = self.board()
        run = self.submit(board, request_id="standalone-fence")
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions"
                " (deletion_id,target_kind,target_id)"
                " VALUES('standalone-deletion','micro-task-tree',?)",
                ("run:" + run["runId"],),
            )
        with self.assertRaises(BoardError) as continued:
            board.call("workflow_continue", {
                "runId": run["runId"], "commandId": "standalone-continue",
                "expectedRevision": run["revision"], "input": "Continue this work",
                **self.control(run),
            })
        self.assertEqual(continued.exception.code, "TASK_DELETING")
        self.assertEqual(continued.exception.details["deletionId"], "standalone-deletion")
        with self.assertRaises(BoardError) as retried:
            board.store.task_retry({"runId": run["runId"], **self.control(run)})
        self.assertEqual(retried.exception.code, "TASK_DELETING")

    def test_deleting_group_rejects_named_and_inherited_admission(self):
        board = self.board()
        first = self.submit(board, macroTask={"title": "group"})
        identifier = first["macroTaskId"]
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions"
                " (deletion_id,target_kind,target_id)"
                " VALUES('deletion-1','macro-task',?)",
                (identifier,),
            )
        for presentation in ({"macroTaskId": identifier}, {"macroTaskOf": first["runId"]}):
            with self.subTest(presentation=presentation), self.assertRaises(BoardError) as caught:
                self.submit(board, request_id=f"blocked-{len(presentation)}-{next(iter(presentation))}",
                            **presentation)
            self.assertEqual(caught.exception.code, "MACRO_TASK_DELETING")
            self.assertNotIn("state", caught.exception.details)
            self.assertNotIn("deletedAt", caught.exception.details)
            self.assertEqual(caught.exception.details["macroTaskId"], identifier)
        with self.assertRaises(BoardError) as continued:
            board.call("workflow_continue", {
                "runId": first["runId"], "commandId": "blocked-continue",
                "expectedRevision": first["revision"], "input": "Continue this work",
                **self.control(first),
            })
        self.assertEqual(continued.exception.code, "TASK_DELETING")
        self.assertEqual(continued.exception.details["deletionId"], "deletion-1")
        with self.assertRaises(BoardError) as retried:
            board.store.task_retry({"runId": first["runId"], **self.control(first)})
        self.assertEqual(retried.exception.code, "TASK_DELETING")
        self.assertEqual(board.store.count_tasks(), 1)

    def test_deleting_group_rejects_submission_recovery(self):
        board = self.board()
        run = self.submit(board, macroTask={"title":"recovering group"})
        with board.store.db.write() as connection:
            connection.execute(
                "INSERT INTO macro_task_deletions"
                " (deletion_id,target_kind,target_id)"
                " VALUES('recovery-deletion','macro-task',?)",
                (run["macroTaskId"],),
            )
        with self.assertRaises(BoardError) as caught:
            self.submit(board, macroTask={"title":"recovering group"})
        self.assertEqual(caught.exception.code,"MACRO_TASK_DELETING")

    def test_group_and_title_replay_are_separate_from_execution(self):
        board = self.board()
        first = self.submit(board, kind='worktree', macroTask={'title':'Human group sentinel'}, title='Human run sentinel')
        identifier = first['macroTaskId']
        self.assertTrue(identifier.startswith('macro-'))
        self.assertEqual(first['title'], 'Human run sentinel')
        replay = self.submit(board, kind='worktree', macroTask={'title':'Human group sentinel'}, title='Human run sentinel')
        self.assertTrue(replay['duplicate'])
        self.assertEqual(replay['macroTaskId'], identifier)
        second = self.submit(board, request_id='second', kind='worktree', macroTaskId=identifier)
        self.assertEqual(second['macroTaskId'], identifier)
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM macro_tasks').fetchone()[0], 1)
            row = db.execute('SELECT * FROM tasks WHERE task_id=?', (first['runId'],)).fetchone()
            self.assertNotIn('Human group sentinel', row['spec_json'])
            self.assertNotIn('Human run sentinel', row['spec_json'])
            group = db.execute('SELECT * FROM macro_tasks').fetchone()
            self.assertGreater(group['activity_seq'], 0)
        self.register(board)
        claim = self.claim(board)
        text = json.dumps({key: value for key, value in claim["claim"].items()
                           if key != "sessionRecordPath"})
        for hidden in ('Human group sentinel', 'Human run sentinel', identifier):
            self.assertNotIn(hidden, text)

    def test_description_is_atomic_immutable_display_metadata(self):
        board = self.board()
        metadata = {'title': 'Readable micro task', 'description': 'User wording sentinel.'}
        run = self.submit(board, kind='worktree', macroTask=metadata, title='Short title', task='Task opening.\nMore detail.')
        identifier = run['macroTaskId']
        summary = board.call('macro_task_timeline', {'macroTaskId': identifier})
        self.assertEqual(summary['macroTask']['description'], metadata['description'])
        self.assertEqual(summary['macroTask']['counts']['accepted'], 0)
        self.assertEqual(summary['rows'][0]['taskSummary'], 'Task opening.')
        with board.store.db.read() as db:
            shape = [tuple(r) for r in db.execute('PRAGMA table_info(macro_tasks)')]
            head = db.execute('SELECT MAX(seq) FROM events').fetchone()[0]
        self.assertTrue(self.submit(board, kind='worktree', macroTask=metadata, title='Short title', task='Task opening.\nMore detail.')['duplicate'])
        with self.assertRaises(BoardError) as conflict:
            self.submit(board, kind='worktree', macroTask={**metadata, 'description': 'Changed'}, title='Short title', task='Task opening.\nMore detail.')
        self.assertEqual(conflict.exception.code, 'CONFLICT')
        with board.store.db.read() as db:
            self.assertEqual(head, db.execute('SELECT MAX(seq) FROM events').fetchone()[0])
            self.assertEqual(shape, [tuple(r) for r in db.execute('PRAGMA table_info(macro_tasks)')])
            for row in db.execute('SELECT spec_json FROM tasks'):
                self.assertNotIn(metadata['description'], row[0])
        self.register(board)
        self.assertNotIn(metadata['description'], json.dumps(self.claim(board)))
        for value in ('', ' ', 'x' * 301, None, False):
            with self.assertRaises(BoardError):
                schemas.normalize_presentation({'macroTask': {'title': 'ok', 'description': value}})
        self.assertEqual(len(schemas.normalize_presentation({'macroTask': {'title': 'ok', 'description': '字' * 300}})['macroTask']['description']), 300)

    def test_changed_presentation_conflicts_without_creating_another_group(self):
        board = self.board()
        self.submit(board, macroTask={'title':'one'}, title='first')
        for extra in ({'macroTask':{'title':'two'}, 'title':'first'},
                      {'macroTask':{'title':'one'}, 'title':'changed'}, {}):
            with self.assertRaises(BoardError) as error:
                self.submit(board, **extra)
            self.assertEqual(error.exception.code, 'CONFLICT')
        with board.store.db.read() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM macro_tasks').fetchone()[0], 1)

    def test_a_foreign_host_cannot_attach_and_unknown_group_admits_nothing(self):
        board = self.board()
        first = self.submit(board, macroTask={'title':'one'})
        for request_id, host_id, identifier, code in (
            ('foreign','host-2',first['macroTaskId'],'CONFLICT'),
            ('unknown','host-1','macro-unknown','NOT_FOUND'),
        ):
            with self.assertRaises(BoardError) as error:
                self.submit(board, request_id=request_id, host_id=host_id, macroTaskId=identifier)
            self.assertEqual(error.exception.code, code)
        self.assertEqual(board.store.count_tasks(), 1)

    def test_metadata_input_is_strict_and_omission_keeps_original_fingerprint(self):
        board = self.board()
        for extra in ({'title':None},{'title':False},{'title':' '}, {'title':'x'*201},
                      {'macroTask':None},{'macroTask':{'title':'ok','extra':1}},
                      {'macroTask':{'title':'ok'},'macroTaskId':None},{'macroTaskId':'run:old'}):
            with self.assertRaises(BoardError):
                self.submit(board, **extra)
        old = schemas.workflow_request_fingerprint({'task':'x'}, {'kind':'existing'}, 'host')
        self.assertEqual(old, schemas.workflow_request_fingerprint({'task':'x'}, {'kind':'existing'}, 'host', presentation={}))
        self.assertEqual(board.store.count_tasks(), 0)

    def test_snapshot_reads_do_not_advance_activity(self):
        board = self.board()
        first = self.submit(board, macroTask={'title':'read only index'})
        with board.store.db.read() as db:
            before = tuple(db.execute('SELECT activity_seq,activity_at FROM macro_tasks').fetchone())
        for _ in range(2):
            board.call('workflow_get', {'runId':first['runId']})
            board.call('console_snapshot', {})
        with board.store.db.read() as db:
            self.assertEqual(tuple(db.execute('SELECT activity_seq,activity_at FROM macro_tasks').fetchone()), before)


class PendingRootCountTests(WorkflowTestCase):
    """The console top bar's board-wide pending count, with the old dedup semantics."""

    def park_on_host(self, board, request_id, *, task='parked work'):
        submitted = self.submit(board, request_id=request_id, task=task,
                                cwd=str(self.workdir(request_id)))
        claim = self.claim(board, claim_request_id=f'{request_id}-claim')
        self.finish_turn(board, claim, disposition='assistance')
        return submitted

    def assign_lead_ownership(self, board, owned_run_id, lead_run_id):
        with board.store.db.write() as connection:
            turn = connection.execute(
                "SELECT turn_id FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
                (lead_run_id,),
            ).fetchone()
            connection.execute(
                "INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at)"
                " VALUES(?,?,?,?)",
                (owned_run_id, lead_run_id, turn["turn_id"], board.store.now()),
            )

    def test_pending_root_count_dedups_helpers_under_their_root(self):
        from hey_my_buddy.blackboard.tasks.macro_tasks import pending_root_count
        board = self.board()
        self.register(board)
        self.assertEqual(pending_root_count(board.store), 0)
        root = self.park_on_host(board, 'pending-dedupe')
        self.assertEqual(pending_root_count(board.store), 1)
        # A helper of the same tree parked on the Host still counts once.
        view = board.call('workflow_get', {'runId': root['runId']})
        self.assertTrue(view['awaitingHost'])
        self.decide(board, view, view['activeRequest']['requestId'],
                    helpers=[{'requestId': 'pending-helper', 'task': 'assist',
                              'cwd': str(self.workdir('pending-helper')),
                              'executionWorkspace': {'kind': 'existing', 'access': 'write'}}])
        helper_claim = self.claim(board, claim_request_id='pending-helper-claim')
        self.finish_turn(board, helper_claim, disposition='assistance')
        self.assertEqual(pending_root_count(board.store), 1)
        # A second distinct root awaiting its Host counts once more.
        self.park_on_host(board, 'pending-second')
        self.assertEqual(pending_root_count(board.store), 2)
        # A queued run that nobody parked is not pending.
        self.submit(board, request_id='pending-queued', cwd=str(self.workdir('pending-queued')))
        self.assertEqual(pending_root_count(board.store), 2)

    def test_pending_root_count_stays_correct_beyond_one_page(self):
        from hey_my_buddy.blackboard.tasks.macro_tasks import pending_root_count
        board = self.board()
        self.register(board)
        # Claim by explicit run: the scheduler's bounded candidate window fills
        # with parked runs long before one page worth, which is exactly the
        # situation the board-wide count must survive.
        runs = [self.submit(board, request_id=f'pending-bulk-{index:03d}', task=f'bulk {index}',
                            cwd=str(self.workdir(f'pending-bulk-{index:03d}')))['runId']
                for index in range(105)]
        for index, run_id in enumerate(runs):
            claim = self.claim(board, claim_request_id=f'pending-bulk-{index:03d}-claim', run_id=run_id)
            self.finish_turn(board, claim, disposition='assistance')
        self.assertEqual(pending_root_count(board.store), 105)

    def test_pending_count_ignores_a_lead_owned_helper_boundary(self):
        from hey_my_buddy.blackboard.tasks.macro_tasks import pending_root_count
        board = self.board()
        self.register(board)
        lead = self.submit(board, request_id='pending-lead-owner', cwd=str(self.workdir('pending-lead-owner')))
        self.finish_turn(board, self.claim(board, run_id=lead['runId'], claim_request_id='pending-lead-claim'))

        root = self.submit(board, request_id='pending-owned-root', cwd=str(self.workdir('pending-owned-root')))
        self.finish_turn(board, self.claim(board, run_id=root['runId'], claim_request_id='pending-root-claim'),
                         disposition='assistance')
        view = board.call('workflow_get', {'runId': root['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], command_id='owned-helper',
                               helpers=[{'requestId': 'pending-owned-helper', 'task': 'assist',
                                         'cwd': str(self.workdir('pending-owned-helper')),
                                         'executionWorkspace': {'kind': 'existing', 'access': 'write'}}])
        helper = approved['children'][0]['taskId']
        self.finish_turn(board, self.claim(board, run_id=helper, claim_request_id='owned-helper-yields'),
                         disposition='assistance')
        self.assign_lead_ownership(board, root['runId'], lead['runId'])

        self.assertEqual(pending_root_count(board.store), 0)
        self.park_on_host(board, 'pending-unowned')
        self.assertEqual(pending_root_count(board.store), 1)


class MacroTaskReadTests(WorkflowTestCase):
    """macro_task_list / macro_task_timeline are bounded, read-only derivations."""

    def grouped_board(self):
        board = self.board()
        first = self.submit(board, request_id='g-1', kind='worktree', macroTask={'title': 'Ship the timeline'},
                            title='Backend')
        second = self.submit(board, request_id='g-2', kind='worktree', macroTaskId=first['macroTaskId'], title='Frontend')
        loose = self.submit(board, request_id='loose', kind='worktree', task='standalone work\nsecond line')
        return board, first, second, loose

    def list_(self, board, **params):
        return board.call('macro_task_list', params)

    def test_fix_target_title_uses_target_presentation_across_macro_tasks(self):
        board = self.board()
        target = self.submit(board, request_id='target', kind='worktree',
                             macroTask={'title': 'First group'}, title='Backend')
        correction = self.submit(board, request_id='correction', kind='worktree',
                                 macroTask={'title': 'Second group'}, title='Repair')
        self.assertNotEqual(target['macroTaskId'], correction['macroTaskId'])
        with board.store.db.write() as db:
            db.execute(
                'INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)',
                (correction['runId'], target['runId'], '2026-01-01T00:00:00Z'),
            )
        timeline = board.call('macro_task_timeline', {'macroTaskId': correction['macroTaskId']})
        row = timeline['rows'][0]
        self.assertEqual((row['fixes'], row['fixesTitle'], row['fixesTitleSource']),
                         (target['runId'], 'Backend', 'title'))
        self.assertNotIn('fixesTitle', board.call('macro_task_timeline', {
            'macroTaskId': target['macroTaskId'],
        })['rows'][0])

        with board.store.db.write() as db:
            db.execute('UPDATE workflow_runs SET title=NULL WHERE run_id=?', (target['runId'],))
        row = board.call('macro_task_timeline', {
            'macroTaskId': correction['macroTaskId'],
        })['rows'][0]
        self.assertEqual((row['fixesTitle'], row['fixesTitleSource']), ('do the thing', 'task'))

        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','') WHERE task_id=?",
                       (target['runId'],))
        target_timeline = board.call('macro_task_timeline', {'macroTaskId': target['macroTaskId']})
        self.assertEqual((target_timeline['rows'][0]['title'], target_timeline['rows'][0]['titleSource']),
                         (None, 'none'))
        row = board.call('macro_task_timeline', {'macroTaskId': correction['macroTaskId']})['rows'][0]
        self.assertEqual((row['fixesTitle'], row['fixesTitleSource']), (None, 'none'))

    def test_standalone_summary_keeps_an_absent_intent_title(self):
        board = self.board()
        run = self.submit(board, request_id='untitled')
        with board.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET title=NULL WHERE run_id=?", (run['runId'],))
            db.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','') WHERE task_id=?", (run['runId'],))
        standalone = next(group for group in self.list_(board)['macroTasks']
                          if group['macroTaskId'] == f"run:{run['runId']}")
        self.assertEqual((standalone['title'], standalone['titleSource']), (None, 'none'))

    def test_intent_title_is_not_replaced_by_worker_result_summary(self):
        board = self.board()
        submitted = self.submit(board, kind='worktree', task='Implement bounded routing\nDetailed request')
        self.register(board)
        claimed = self.claim(board, run_id=submitted['runId'])
        self.finish_turn(board, claimed)
        group = self.list_(board)['macroTasks'][0]
        self.assertEqual(group['title'], 'Implement bounded routing')
        self.assertEqual(group['titleSource'], 'task')
        self.assertTrue(group['summary'])
        timeline = board.call('macro_task_timeline', {'macroTaskId':group['macroTaskId']})
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
                timeline = board.call('macro_task_timeline', {'macroTaskId': f"run:{submitted['runId']}"})
                execution = next(span for span in timeline['spans'] if span['kind'] == 'execution')
                self.assertEqual(execution.get('resultStatus'), status)
                self.assertTrue(execution['shutdownConfirmed'])

    def test_list_groups_macro_tasks_and_standalone_roots_by_latest_activity(self):
        board, first, second, loose = self.grouped_board()
        listed = self.list_(board)
        self.assertEqual(listed['totals'], {'all': 2, 'active': 2, 'host': 0, 'review': 0})
        self.assertNotIn('total', listed)
        self.assertIsNone(listed['nextCursor'])
        self.assertFalse(listed['changed'])
        ids = [item['macroTaskId'] for item in listed['macroTasks']]
        self.assertEqual(ids, [f"run:{loose['runId']}", first['macroTaskId']], 'latest activity first')
        standalone, grouped = listed['macroTasks']
        self.assertEqual(standalone['kind'], 'standalone')
        self.assertEqual(standalone['title'], 'standalone work')
        self.assertEqual(standalone['titleSource'], 'task')
        self.assertEqual(grouped['kind'], 'macro-task')
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
        self.assertEqual(self.list_(board)['macroTasks'][0]['macroTaskId'], first['macroTaskId'])

    def test_list_sort_orders_and_paginates_by_activity_or_creation(self):
        board, first, _second, loose = self.grouped_board()
        other = self.submit(board, request_id='another-loose', kind='worktree')
        macro_id = first['macroTaskId']
        loose_id, other_id = f"run:{loose['runId']}", f"run:{other['runId']}"
        with board.store.db.write() as db:
            db.execute("UPDATE macro_tasks SET created_at=?,activity_seq=? WHERE macro_task_id=?",
                       ('2026-01-02T00:00:00Z', 30, macro_id))
            db.execute("UPDATE workflow_runs SET created_at=?,activity_seq=? WHERE run_id=?",
                       ('2026-01-01T00:00:00Z', 10, loose['runId']))
            db.execute("UPDATE workflow_runs SET created_at=?,activity_seq=? WHERE run_id=?",
                       ('2026-01-01T00:00:00Z', 20, other['runId']))

        tied = sorted([loose_id, other_id])
        expected = {
            'activity-desc': [macro_id, other_id, loose_id],
            'activity-asc': [loose_id, other_id, macro_id],
            'created-desc': [macro_id, *reversed(tied)],
            'created-asc': [*tied, macro_id],
        }
        self.assertEqual([row['macroTaskId'] for row in self.list_(board)['macroTasks']],
                         expected['activity-desc'])
        for sort, order in expected.items():
            with self.subTest(sort=sort):
                full = self.list_(board, sort=sort)
                self.assertEqual([row['macroTaskId'] for row in full['macroTasks']], order)
                seen, before = [], None
                for _ in order:
                    page = self.list_(board, sort=sort, limit=1, **({'before': before} if before else {}))
                    self.assertEqual(len(page['macroTasks']), 1)
                    seen.extend(row['macroTaskId'] for row in page['macroTasks'])
                    before = page['nextCursor']
                self.assertEqual(seen, order)
                self.assertIsNone(before)

        first_page = self.list_(board, sort='created-asc', limit=1)
        with self.assertRaises(BoardError) as error:
            self.list_(board, sort='created-desc', limit=1, before=first_page['nextCursor'])
        self.assertEqual((error.exception.code, error.exception.details.get('field')),
                         ('INVALID_ARGUMENT', 'before'))
        for sort in ('created', 'activity', 'updated-desc', 'created-oldest', ''):
            with self.subTest(invalid_sort=sort), self.assertRaises(BoardError):
                self.list_(board, sort=sort)

    def test_failed_count_keeps_host_concluded_failures_and_excludes_cancelled_roots(self):
        board, first, second, loose = self.grouped_board()
        group_id = first['macroTaskId']
        self.register(board)

        def counts():
            return next(item['counts'] for item in self.list_(board)['macroTasks']
                        if item['macroTaskId'] == group_id)

        self.assertEqual(counts()['failed'], 0)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), runner_status='failed',
                         exit_code=1, seal=False)
        self.assertEqual(counts()['failed'], 1)
        failed = board.call('workflow_get', {'runId': first['runId']})
        self.assertEqual(failed['state'], 'failed')
        concluded = board.call('workflow_conclude', {
            'runId': first['runId'], 'note': 'Failure reviewed', **self.control(failed),
        })
        self.assertEqual(concluded['state'], 'concluded')
        self.assertEqual(concluded['hostConclusion']['executionStatus'], 'failed')
        self.assertEqual(counts()['failed'], 1)

        claim = self.claim(board, claim_request_id='cancelled-root-claim', run_id=second['runId'])
        board.call('workflow_cancel', {
            'runId': second['runId'], 'commandId': 'cancelled-root',
            **self.controls[second['runId']],
        })
        self.finish_turn(board, claim, runner_status='cancelled')
        cancelled = board.call('workflow_get', {'runId': second['runId']})
        board.call('workflow_conclude', {
            'runId': second['runId'], 'note': 'Cancellation reviewed', **self.control(cancelled),
        })
        self.assertEqual(counts()['failed'], 1)
        self.assertEqual(self.list_(board, query='standalone')['macroTasks'][0]['counts']['failed'], 0)

    def test_filters_match_members_and_cursor_is_bound_to_them(self):
        board, first, second, loose = self.grouped_board()
        page = self.list_(board, limit=1)
        self.assertEqual(len(page['macroTasks']), 1)
        self.assertTrue(page['nextCursor'])
        rest = self.list_(board, limit=1, before=page['nextCursor'])
        self.assertEqual(len(rest['macroTasks']), 1)
        self.assertEqual(rest['totals'], page['totals'])
        self.assertNotEqual(rest['macroTasks'][0]['macroTaskId'], page['macroTasks'][0]['macroTaskId'])
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
        self.assertEqual([item['macroTaskId'] for item in queried['macroTasks']], [f"run:{loose['runId']}"])
        self.assertEqual(queried['totals'], {'all': 1, 'active': 1, 'host': 0, 'review': 0})
        self.assertEqual(queried['macroTasks'][0]['matchingRuns'], 1)

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
        timeline = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})
        self.assertEqual([row['runId'] for row in timeline['rows']], [first['runId'], second['runId']])
        for row in timeline['rows']:
            self.assertIs(row['delegationAuthority'], False)
        self.assertEqual(timeline['rows'][0]['title'], 'Backend')
        self.assertTrue(timeline['scopeComplete'])
        executions = [span for span in timeline['spans'] if span['kind'] == 'execution']
        self.assertEqual(len(executions), 2)
        by_run = {span['runId']: span for span in executions}
        self.assertEqual(by_run[first['runId']]['state'], 'finished')
        self.assertTrue(by_run[first['runId']]['shutdownConfirmed'])
        self.assertFalse(by_run[first['runId']]['uncertain'])
        self.assertEqual(by_run[first['runId']]['buddy']['model'], 'deepseek-flash')
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
        dispatch = timeline['events'][0]
        self.assertEqual({key: dispatch[key] for key in ('kind', 'decision', 'verdict', 'state', 'strategy', 'note')},
                         {'kind': 'dispatch', 'decision': None, 'verdict': None, 'state': None,
                          'strategy': None, 'note': None})
        self.assertEqual(timeline['events'][0]['actor'], {'role': 'host', 'id': 'host-1'})
        self.assertNotIn('controlToken', json.dumps(timeline))

    def test_execution_spans_project_turn_duration_live_reminders_and_host_question_metadata(self):
        board = self.board()
        run = self.submit(board, kind='worktree', expectedSeconds=37)
        run_id = run['runId']
        board.store._message_post(run_id, 'before-turn', 'Question before a turn')
        claim = self.claim(board, run_id=run_id)
        attempt_id = claim['claim']['attempt']['attemptId']
        asked = board.store._message_post(run_id, 'during-turn', 'Sensitive question body')['message']
        now = board.store.now()
        with board.store.db.write() as connection:
            attempt = connection.execute('SELECT * FROM attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            board.store._store_activity(connection, attempt, {
                'phase': 'tool-running', 'eventSeq': 1,
                'lastNativeActivityAt': now, 'observedAt': now,
            })
            connection.execute(
                'INSERT INTO attempt_reminders(task_id,attempt_id,kind,expected_multiple,'
                ' tool_in_progress,created_at) VALUES(?,?,?,?,?,?)',
                (run_id, attempt_id, 'expected-duration', 1, 0, now),
            )
            connection.execute(
                'INSERT INTO attempt_reminders(task_id,attempt_id,kind,silence_started_at,'
                ' last_native_event_at,tool_in_progress,created_at)'
                ' VALUES(?,?,?,?,?,?,?)',
                (run_id, attempt_id, 'native-silence', now, now, 1, now),
            )
        timeline = board.call('macro_task_timeline', {'macroTaskId': f'run:{run_id}'})
        execution = next(span for span in timeline['spans'] if span['kind'] == 'execution')
        self.assertEqual(execution['expectedSeconds'], 37)
        self.assertEqual(execution['reminders'], {
            'overdue': True, 'possiblyStuck': True, 'lastNativeEventAt': now, 'toolInProgress': True,
        })
        self.assertEqual(execution['reminders'],
                         board.call('task_get', {'runId': run_id})['task']['selectedAttempt']['reminders'])
        self.assertEqual(execution['inquiries'], [{
            'inquiryId': 'during-turn', 'createdAt': asked['createdAt'],
            'author': 'cli', 'state': asked['state'],
        }])
        self.assertNotIn('Sensitive question body', json.dumps(timeline))
        for span in timeline['spans']:
            if span['kind'] != 'execution':
                self.assertEqual((span['expectedSeconds'], span['reminders'], span['inquiries']),
                                 (None, None, None))
        self.finish_turn(board, claim)
        ended = board.call('macro_task_timeline', {'macroTaskId': f'run:{run_id}'})
        execution = next(span for span in ended['spans'] if span['kind'] == 'execution')
        self.assertEqual(execution['expectedSeconds'], 37)
        self.assertIsNone(execution['reminders'])
        self.assertEqual(execution['inquiries'][0]['inquiryId'], 'during-turn')

        # A later turn may freeze a different expectation. The earlier span keeps
        # its own turn's value, and a turn with no questions has an empty list.
        with board.store.db.write() as connection:
            connection.execute('UPDATE tasks SET expected_seconds=59 WHERE task_id=?', (run_id,))
        view = board.call('workflow_get', {'runId': run_id})
        self.continue_run(board, view, note='review the output again')
        self.claim(board, claim_request_id='c2', run_id=run_id)
        timeline = board.call('macro_task_timeline', {'macroTaskId': f'run:{run_id}'})
        executions = [span for span in timeline['spans'] if span['kind'] == 'execution']
        self.assertEqual([span['expectedSeconds'] for span in executions], [37, 59])
        self.assertEqual([span['inquiries'] for span in executions], [execution['inquiries'], []])
        self.assertIsNone(executions[0]['reminders'])
        self.assertEqual(executions[1]['reminders'],
                         board.call('task_get', {'runId': run_id})['task']['selectedAttempt']['reminders'])

    def test_timeline_markers_keep_actor_roles_and_explicit_host_fallbacks(self):
        board = self.board()
        run = self.submit(board, kind='worktree')
        run_id = run['runId']
        with board.store.db.write() as connection:
            lead = board.store._append_event(connection, 'workflow.request_approved', task_id=run_id,
                                             payload={'actor': {'role': 'lead', 'id': 'lead-run'},
                                                      'decision': 'approve', 'note': 'Host content ' * 40})
            console = board.store._append_event(connection, 'workflow.continued', task_id=run_id,
                                                payload={'actor': {'role': 'console', 'id': 'session-1'},
                                                         'verdict': 'accepted', 'state': 'recorded', 'strategy': 'merge'})
            takeover = board.store._append_event(connection, 'workflow.takeover', task_id=run_id,
                                                 payload={'newHostId': 'next-host'})
            submitted = board.store._append_event(connection, 'task.submitted', task_id=run_id,
                                                  payload={'microTask': {'sourceHostId': 'source-host'}})
            unknown = board.store._append_event(connection, 'workflow.cancelled', task_id=run_id)
        timeline = board.call('macro_task_timeline', {'macroTaskId': f'run:{run_id}'})
        actors = {event['seq']: event['actor'] for event in timeline['events']}
        events = {event['seq']: event for event in timeline['events']}
        self.assertEqual((events[lead]['decision'], events[lead]['note']), ('approve', 'Host content ' * 40))
        self.assertEqual(tuple(events[console][key] for key in ('verdict', 'state', 'strategy')),
                         ('accepted', 'recorded', 'merge'))
        self.assertEqual(actors[lead], {'role': 'lead', 'id': 'lead-run'})
        self.assertEqual(actors[console], {'role': 'console', 'id': 'session-1'})
        self.assertEqual(actors[takeover], {'role': 'host', 'id': 'next-host'})
        self.assertEqual(actors[submitted], {'role': 'host', 'id': 'source-host'})
        self.assertIsNone(actors[unknown])

    def test_timeline_projects_lead_authority_before_and_after_owned_submission(self):
        board = self.board()
        enable_fixture_buddy(board.store, BUDDY)
        lead = self.submit(board, request_id='authority-root', cwd=str(self.workdir('authority-root')),
                           executionWorkspace={'kind': 'existing', 'access': 'full', 'accessNote': 'Lead checks'},
                           delegationAuthority=True, macroTask={'title': 'Timeline authority'})
        timeline = board.call('macro_task_timeline', {'macroTaskId': lead['macroTaskId']})
        self.assertEqual(len(timeline['rows']), 1)
        row = timeline['rows'][0]
        self.assertIs(row['delegationAuthority'], True)
        self.assertEqual(row['kind'], 'micro-task')
        self.assertIsNone(row['leadOwnership'])
        self.assertIsNone(row['rootLeadRunId'])

        claim = self.claim(board, run_id=lead['runId'])['claim']
        owned = board.call('workflow_submit', {
            **BUDDY, 'requestId': 'authority-owned', 'submissionToken': 's' * 32,
            'task': 'Do owned work', 'cwd': str(self.workdir('authority-owned')),
            'executionWorkspace': {'kind': 'existing', 'access': 'write'},
            'credential': claim['agentCredential'],
        })
        timeline = board.call('macro_task_timeline', {'macroTaskId': lead['macroTaskId']})
        rows = {row['runId']: row for row in timeline['rows']}
        self.assertEqual(set(rows), {lead['runId'], owned['runId']})
        self.assertIs(rows[lead['runId']]['delegationAuthority'], True)
        self.assertIs(rows[owned['runId']]['delegationAuthority'], False)
        self.assertEqual(rows[owned['runId']]['leadOwnership']['leadRunId'], lead['runId'])
        self.assertEqual(rows[owned['runId']]['rootLeadRunId'], lead['runId'])

    def test_lead_owned_waits_count_as_active_and_timeline_projects_ownership(self):
        board = self.board()
        self.register(board)
        lead = self.submit(board, request_id='timeline-lead-owner', cwd=str(self.workdir('timeline-lead-owner')),
                           macroTask={'title': 'Lead owned work'})
        self.finish_turn(board, self.claim(board, run_id=lead['runId'], claim_request_id='timeline-lead-claim'))
        root = self.submit(board, request_id='timeline-owned-root', kind='worktree',
                           cwd=str(self.workdir('timeline-lead-owner')),
                           macroTaskId=lead['macroTaskId'])
        self.finish_turn(board, self.claim(board, run_id=root['runId'], claim_request_id='timeline-root-claim'),
                         disposition='assistance')
        view = board.call('workflow_get', {'runId': root['runId']})
        approved = self.decide(board, view, view['activeRequest']['requestId'], command_id='timeline-helper',
                               helpers=[{'requestId': 'timeline-owned-helper', 'task': 'assist',
                                         'cwd': str(self.workdir('timeline-owned-helper')),
                                         'executionWorkspace': {'kind': 'existing', 'access': 'write'}}])
        helper = approved['children'][0]['taskId']
        self.finish_turn(board, self.claim(board, run_id=helper, claim_request_id='timeline-helper-claim'),
                         disposition='assistance')
        with board.store.db.write() as connection:
            turn = connection.execute(
                "SELECT turn_id FROM workflow_turns WHERE run_id=? ORDER BY turn_index DESC LIMIT 1",
                (lead['runId'],),
            ).fetchone()
            connection.execute(
                "INSERT INTO workflow_lead_tasks(owned_run_id,lead_run_id,lead_turn_id,created_at)"
                " VALUES(?,?,?,?)",
                (root['runId'], lead['runId'], turn['turn_id'], board.store.now()),
            )
            connection.execute("UPDATE workflow_runs SET state='waiting-owned' WHERE run_id=?", (root['runId'],))

        timeline = board.call('macro_task_timeline', {'macroTaskId': root['macroTaskId']})
        summary = timeline['macroTask']
        self.assertEqual(summary['state'], 'active')
        self.assertEqual(summary['counts']['active'], 2)
        self.assertEqual(summary['counts']['host'], 0)
        rows = {row['runId']: row for row in timeline['rows']}
        self.assertIs(rows[root['runId']]['delegationAuthority'], False)
        self.assertIs(rows[helper]['delegationAuthority'], False)
        self.assertEqual(rows[root['runId']]['leadOwnership']['leadRunId'], lead['runId'])
        self.assertIsNone(rows[helper]['leadOwnership'])
        self.assertEqual(rows[root['runId']]['rootLeadRunId'], lead['runId'])
        self.assertEqual(rows[helper]['rootLeadRunId'], lead['runId'])

    def test_a_restarted_execution_that_reported_and_confirmed_its_stop_ends_definitely(self):
        board = self.board()
        self.register(board)
        run = self.submit(board, kind='worktree')
        claim = self.claim(board, run_id=run['runId'])
        from hey_my_buddy.protocol.execution import fsync_json
        attempt = claim['claim']['attempt']
        fsync_json(board.directory / 'attempts' / run['runId'] / attempt['attemptId'] / 'spawn.intent',
                   {'taskId': run['runId'], 'attemptId': attempt['attemptId'], 'generation': attempt['generation']})
        self.assertEqual(board.store.reconcile_startup()['uncertain'], 1)
        # The execution reports after service restart: its result and stop proof
        # settle the end even though ownership stayed uncertain.
        self.finish_turn(board, claim)
        timeline = board.call('macro_task_timeline', {'macroTaskId': f"run:{run['runId']}"})
        execution = next(span for span in timeline['spans'] if span['kind'] == 'execution')
        self.assertEqual(execution['state'], 'finished')
        self.assertTrue(execution['shutdownConfirmed'])
        self.assertFalse(execution['uncertain'])
        self.assertIsNotNone(execution['endAt'])
        self.assertTrue(timeline['rows'][0]['shutdownConfirmed'])

    def test_a_restarted_execution_without_a_confirmed_stop_stays_uncertain(self):
        board = self.board()
        self.register(board)
        run = self.submit(board, kind='worktree')
        claim = self.claim(board, run_id=run['runId'])
        from hey_my_buddy.protocol.execution import fsync_json
        attempt = claim['claim']['attempt']
        fsync_json(board.directory / 'attempts' / run['runId'] / attempt['attemptId'] / 'spawn.intent',
                   {'taskId': run['runId'], 'attemptId': attempt['attemptId'], 'generation': attempt['generation']})
        self.assertEqual(board.store.reconcile_startup()['uncertain'], 1)
        self.finish_turn(board, claim, disposition='attention', shutdown=False)
        timeline = board.call('macro_task_timeline', {'macroTaskId': f"run:{run['runId']}"})
        execution = next(span for span in timeline['spans'] if span['kind'] == 'execution')
        self.assertEqual(execution['state'], 'uncertain')
        self.assertFalse(execution['shutdownConfirmed'])
        self.assertTrue(execution['uncertain'])
        self.assertIsNone(execution['endAt'])
        self.assertFalse(timeline['rows'][0]['shutdownConfirmed'])

    def test_system_request_wait_carries_reason_and_worker_wait_carries_only_content(self):
        board, first, second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        with board.store.db.write() as connection:
            run = board.store.workflow._run_row(connection, second['runId'])
            board.store.workflow._record_helper_report(
                connection, run, [], reason={'code': 'helpers-settled', 'params': {}}, now=board.store.workflow.now())
        spans = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['spans']
        content = next(span for span in spans if span['runId'] == first['runId'] and span['kind'] == 'host')
        system = next(span for span in spans if span['runId'] == second['runId'] and span['kind'] == 'host')
        self.assertEqual(content['summary'], 'please review the approach')
        self.assertNotIn('reason', content)
        self.assertNotIn('summary', system)
        self.assertEqual(system['reason'], {'code': 'helpers-settled', 'params': {}})

    def test_a_run_parked_on_its_host_has_one_open_wait_that_cancellation_closes(self):
        board, first, _second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        spans = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['spans']
        mine = [span for span in spans if span['runId'] == first['runId']]
        # The host wait is the whole open interval; no open queue span overlaps it.
        self.assertEqual([span['kind'] for span in mine if span['endAt'] is None], ['host'])
        board.call('workflow_cancel', {'runId': first['runId'], 'note': 'stop', **self.control(first)})
        spans = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['spans']
        wait = next(span for span in spans if span['runId'] == first['runId'] and span['kind'] == 'host')
        self.assertEqual(wait['state'], 'cancelled')
        self.assertIsNotNone(wait['endAt'])
        self.assertGreaterEqual(wait['endAt'], wait['startAt'])
        events = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['events']
        cancelled = next(event for event in events if event['eventKind'] == 'workflow.cancelled')
        self.assertEqual(cancelled['stopInitiator'], 'host')

    def test_a_host_continuation_closes_the_superseded_wait(self):
        board, first, _second, _loose = self.grouped_board()
        self.register(board)
        self.finish_turn(board, self.claim(board, run_id=first['runId']), disposition='assistance')
        current = board.call('workflow_get', {'runId': first['runId']})
        self.continue_run(board, {**first, 'revision': current['revision']})
        spans = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['spans']
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
        spans = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})['spans']
        wait = next(s for s in spans if s['runId'] == first['runId'] and s['kind'] == 'host')
        queued = [s for s in spans if s['runId'] == first['runId'] and s['kind'] == 'queue' and s['endAt'] is None]
        self.assertEqual(len(queued), 1)
        self.assertGreaterEqual(queued[0]['startAt'], wait['endAt'])

    def test_timeline_truncation_filters_and_identity(self):
        board, first, second, loose = self.grouped_board()
        limited = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId'], 'limit': 1})
        self.assertEqual(len(limited['rows']), 1)
        self.assertTrue(limited['truncated']['rows'])
        self.assertEqual(limited['totals']['rows'], 2)
        self.assertFalse(limited['scopeComplete'])
        filtered = board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId'], 'query': 'no such text'})
        self.assertEqual(filtered['rows'], [])
        self.assertTrue(filtered['filtered'])
        self.assertFalse(filtered['scopeComplete'])
        standalone = board.call('macro_task_timeline', {'macroTaskId': f"run:{loose['runId']}"})
        self.assertEqual([row['runId'] for row in standalone['rows']], [loose['runId']])
        for identifier, code in (('macro-missing', 'NOT_FOUND'), (f"run:{first['runId']}", 'NOT_FOUND'),
                                 ('something', 'INVALID_ARGUMENT')):
            with self.assertRaises(BoardError) as error:
                board.call('macro_task_timeline', {'macroTaskId': identifier})
            self.assertEqual(error.exception.code, code)

    def test_reads_are_host_only_and_do_not_move_activity(self):
        board, first, second, loose = self.grouped_board()
        self.register(board)
        claim = self.claim(board, run_id=first['runId'])
        credential = claim['claim']['agentCredential']
        for operation, params in (('macro_task_list', {}), ('macro_task_timeline', {'macroTaskId': first['macroTaskId']})):
            with self.assertRaises(BoardError) as error:
                board.call(operation, {**params, 'credential': credential})
            self.assertEqual(error.exception.code, 'FORBIDDEN')
        with board.store.db.read() as db:
            before = [tuple(row) for row in db.execute('SELECT macro_task_id, activity_seq FROM macro_tasks')]
        self.list_(board)
        board.call('macro_task_timeline', {'macroTaskId': first['macroTaskId']})
        with board.store.db.read() as db:
            after = [tuple(row) for row in db.execute('SELECT macro_task_id, activity_seq FROM macro_tasks')]
        self.assertEqual(before, after)


class RoutingSpanTests(WorkflowTestCase):
    def recorded_task(self, *, result_status='ok', shutdown=True):
        board = self.board()
        self.register(board)
        run = self.submit(board, kind='worktree')
        claim = self.claim(board, run_id=run['runId'])
        self.finish_turn(board, claim, runner_status=result_status, shutdown=shutdown)
        return board, run['runId']

    def decision(self, board, task_id, *, output=None, status='completed', selected=None, buddy_id='frozen-buddy'):
        with board.store.db.write() as db:
            note = (output or {}).get('decision', {}).get('note')
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,buddy_id,note,created_at)"
                       " VALUES('recorded-decision',?,'task',?,?,'2026-01-01T00:00:00Z')", (status, buddy_id, note))
            db.execute("INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,task_id,selected_json,output_json,requested_json,updated_at)"
                       " VALUES('recorded-decision','recorded-request','fingerprint',?,?,?,?,'2026-01-01T00:00:00Z')",
                       (task_id, json.dumps(selected) if selected else None, json.dumps(output) if output else None,
                        json.dumps({'budget': {'preset': 'deep'}})))
            now = '2026-01-01T00:00:00Z'
            router_sequence.freeze_request(db, 'recorded-decision',
                                           snapshot={'facts': {}, 'baseInput': {}, 'inspections': []}, now=now)
            router_sequence.reserve_dispatch(db, decision_id='recorded-decision', task_id=task_id, router_index=0,
                                             buddy={'buddyId': 'frozen-router', 'harness': 'dsh', 'provider': 'fixture',
                                                    'model': 'router', 'effort': 'max'}, document={}, now=now)

    def spans(self, board, task_id):
        from hey_my_buddy.blackboard.tasks.macro_tasks import _routing_spans
        with board.store.db.read() as db:
            return _routing_spans(db, task_id, task_id)

    def test_frozen_selection_and_top_level_facts_come_from_exact_decision_task(self):
        board, task_id = self.recorded_task()
        selected = {'harness': 'codex', 'provider': 'openai', 'model': 'historic-model', 'effort': 'high'}
        output = {'status': 'ok', 'decision': {'buddyId': 'frozen-buddy', 'note': 'frozen note',
                  'evidence': [], 'policyCheck': {'ignored': True}},
                  'policyCheck': {'taskPreference': {'ruleIndex': 2, 'outcome': 'matched'}, 'userPreference': 'alternative'},
                  'budget': {'preset': 'quick', 'toolCalls': 8},
                  'usage': {'elapsedMs': 120, 'toolCalls': 3, 'bytesRead': None},
                  'stopEvidence': {'shutdownConfirmed': True}}
        self.decision(board, task_id, output=output, selected=selected)
        before = self.spans(board, task_id)[0]
        self.assertEqual(before['decisionId'], 'recorded-decision')
        self.assertEqual(before['routing'], {'routerBuddyIds': None, 'routerIdentities': None, 'routerIndex': None,
                         'routerRetryIntervalSeconds': None, 'routingBudget': None,
                         'routerBuddyId': None, 'routerBuddy': None, 'routerProblem': None, 'routingMode': None,
                         'selectedBuddy': selected, 'reason': None, 'note': 'frozen note',
                         'policyCheck': output['policyCheck'], 'budget': output['budget'], 'usage': output['usage']})
        # Changing the owner's current execution buddy cannot rewrite this history.
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.model','current-model') WHERE task_id=?", (task_id,))
            db.execute("INSERT INTO evaluation_decisions(decision_id,status,task,buddy_id,created_at)"
                       " VALUES('current-decision','completed','current task','current-buddy','2026-01-02T00:00:00Z')")
            db.execute("INSERT INTO decision_requests(decision_id,request_id,input_fingerprint,task_id,selected_json,output_json,updated_at)"
                       " VALUES('current-decision','current-request','other-fingerprint','other-calculation',?,?,'2026-01-02T00:00:00Z')",
                       (json.dumps({**selected, 'model': 'current-model'}), json.dumps({'budget': {'preset': 'deep'}})))
            for decision_id in ('recorded-decision', 'current-decision'):
                db.execute("INSERT INTO workflow_routes(decision_id,run_id,owner_generation,state,created_at)"
                           " VALUES(?,?,1,'resolved','2026-01-02T00:00:00Z')", (decision_id, task_id))
        self.assertEqual(self.spans(board, task_id)[0], before)

    def test_missing_details_are_null_and_current_pointer_cannot_rewrite_dispatch(self):
        board, task_id = self.recorded_task()
        before = self.spans(board, task_id)[0]
        self.assertIsNone(before['decisionId'])
        self.assertEqual(before['routing'], {'routerBuddyIds': None, 'routerIdentities': None, 'routerIndex': None,
                         'routerRetryIntervalSeconds': None, 'routingBudget': None,
                         'routerBuddyId': None, 'routerBuddy': None, 'routerProblem': None, 'routingMode': None,
                         'selectedBuddy': None, 'reason': None, 'note': None, 'policyCheck': None,
                         'budget': None, 'usage': {'elapsedMs': None, 'toolCalls': None, 'bytesRead': None}})
        self.decision(board, task_id)
        span = self.spans(board, task_id)[0]
        self.assertEqual(span['decisionId'], 'recorded-decision')
        for field in ('selectedBuddy', 'policyCheck', 'budget'):
            self.assertIsNone(span['routing'][field])
        with board.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT output_json FROM decision_requests").fetchone()[0])
        self.assertEqual(self.spans(board, 'no-task-or-attempt'), [])
        with board.store.db.write() as db:
            db.execute("UPDATE decision_requests SET task_id='not-started'")
        self.assertEqual(self.spans(board, task_id)[0], span)
        self.assertEqual(self.spans(board, 'not-started'), [])

    def test_abstention_uses_recorded_decision_code_and_cancel_is_not(self):
        cases = [
            ('ok', True, 'needs-host', {'status': 'ok', 'decision': {'buddyId': None, 'note': 'insufficient', 'evidence': []}}, True),
            ('ok', False, 'needs-host', {'status': 'ok', 'decision': {'buddyId': None, 'note': 'insufficient', 'evidence': []}}, True),
            ('cancelled', True, 'needs-host', {'status': 'ok', 'decision': {'buddyId': None, 'note': 'insufficient', 'evidence': []}}, False),
            ('ok', True, 'needs-host', {'status': 'ok', 'decision': {'buddyId': None, 'reason': 'legacy', 'evidenceIds': []}}, False),
            ('ok', True, 'needs-host', {'status': 'error', 'decision': {'buddyId': None, 'note': 'failed', 'evidence': []}}, False),
            ('ok', True, 'completed', {'status': 'ok', 'decision': {'buddyId': None, 'note': 'other', 'evidence': []}}, False),
        ]
        board, task_id = self.recorded_task()
        self.decision(board, task_id, buddy_id=None)
        for result_status, shutdown, status, output, expected in cases:
            with self.subTest(result_status=result_status, shutdown=shutdown, status=status, output=output):
                with board.store.db.write() as db:
                    db.execute("UPDATE evaluation_decisions SET status=?,reason_code=?,reason_params_json=?",
                               (status, 'router-abstained' if expected and result_status == 'ok' else None,
                                '{}' if expected and result_status == 'ok' else None))
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
        self.decision(board, task_id, status='needs-host', buddy_id=None,
                      output={'status': 'ok', 'code': 'router-out-of-bounds',
                              'decision': {'buddyId': 'old-candidate', 'note': 'chosen', 'evidence': []}})
        span = self.spans(board, task_id)[0]
        self.assertEqual(span['resultStatus'], 'ok')
        self.assertEqual(span['disposition'], 'routing-failed')
