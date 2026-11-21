"""Pause facts are visible to readers and interruption delivery is one-time."""
from __future__ import annotations

from unittest.mock import patch

from blackboard.tasks.test_workflow import WorkflowTestCase
from hey_my_buddy.blackboard.tasks.macro_tasks import pending_root_count


class PauseProjectionTests(WorkflowTestCase):
    def test_macro_and_standalone_views_read_current_pause(self):
        board = self.board()
        grouped = self.submit(board, request_id='grouped-pause',
                              cwd=str(self.workdir('grouped-pause')),
                              create_macro={'title': 'Grouped', 'goal': 'Grouped'})
        standalone = self.submit(board, request_id='standalone-pause',
                                 cwd=str(self.workdir('standalone-pause')))
        macro_id, run_id = grouped['macroTaskId'], standalone['runId']
        self.assertIsNone(board.call('workflow_get', {'macroTaskId': macro_id})['paused'])
        paused_at = '2026-11-11T00:00:00Z'
        with board.store.db.write() as db:
            db.execute('INSERT INTO work_pauses(macro_task_id,paused_at) VALUES(?,?)',
                       (macro_id, paused_at))
            db.execute('INSERT INTO work_pauses(run_id,paused_at) VALUES(?,?)',
                       (run_id, paused_at))
            db.execute("UPDATE tasks SET queue_reason='paused' WHERE task_id IN (?,?)",
                       (grouped['runId'], run_id))
        pause = {'pausedAt': paused_at, 'shutdownConfirmed': True}
        self.assertEqual(board.call('workflow_get', {'macroTaskId': macro_id})['paused'], pause)
        summaries = board.call('macro_task_list', {})
        groups = [item for project in summaries['groups'] for item in project['macroTasks']]
        self.assertEqual(next(item for item in groups if item['macroTaskId'] == macro_id)['paused'], pause)
        timeline = board.call('macro_task_timeline', {'macroTaskId': macro_id})
        self.assertEqual(timeline['macroTask']['paused'], pause)
        self.assertEqual(timeline['rows'][0]['queueReason'], 'paused')
        standalone_timeline = board.call('macro_task_timeline', {'macroTaskId': f'run:{run_id}'})
        self.assertEqual(standalone_timeline['macroTask']['paused'], pause)
        self.assertEqual(pending_root_count(board.store), 0)

    def test_interrupted_attempt_delivers_pause_once_across_waits(self):
        board = self.board()
        submitted = self.submit(board, request_id='interrupted-pause')
        self.claim(board, run_id=submitted['runId'])
        run_id = submitted['runId']
        paused_at = '2026-11-11T00:00:00Z'
        with board.store.db.write() as db:
            attempt_id = db.execute('SELECT active_attempt_id FROM tasks WHERE task_id=?',
                                    (run_id,)).fetchone()[0]
            board.store._append_event(
                db, 'workflow.paused', task_id=run_id, attempt_id=attempt_id,
                payload={'pausedAt': paused_at, 'continuationId': 'next-turn',
                         'actor': {'role': 'console', 'id': 'user'}},
            )
        def wait(wait_id):
            return board.call('task_wait', {'runId': run_id, 'timeoutMs': 0,
                                            'consumeReminders': True,
                                            'reminderWaitId': wait_id})
        first = wait('pause-wait-1')
        self.assertEqual(first['pause']['pausedAt'], paused_at)
        with patch.object(board.store.db, 'write', side_effect=AssertionError('unexpected writer')):
            self.assertEqual(wait('pause-wait-1')['pause'], first['pause'])
            self.assertNotIn('pause', wait('pause-wait-2'))
        with board.store.db.read() as db:
            deliveries = db.execute(
                "SELECT COUNT(*) FROM events WHERE kind='workflow.pause_delivered' AND task_id=?",
                (run_id,),
            ).fetchone()[0]
            self.assertEqual(board.store.head(), db.execute('SELECT MAX(seq) FROM events').fetchone()[0])
        self.assertEqual(deliveries, 1)
