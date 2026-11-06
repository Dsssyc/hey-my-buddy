"""Real daemon/IPC wait observes a gated command descendant's actual shutdown."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import sqlite3
import sys
import threading

from blackboard.tasks import test_workflow_real as real_workspace
from support import wait_for
from hey_my_buddy.protocol.client import BoardClient


class DescendantStopWaitTests(real_workspace.RealWorkspaceTestCase):
    def test_cancelled_parent_waits_for_descendant_stop_without_a_parent_revision_change(self):
        board = self.board()
        parent = self.submit(board, access='read')
        board.call('workflow_cancel', {'runId': parent['runId']})
        board.close()
        started, release = self.directory / 'child-started', self.directory / 'child-release'
        script = self.directory / 'gated-child.py'
        script.write_text('import sys,time\nfrom pathlib import Path\n'
                          'Path(sys.argv[1]).touch()\n'
                          'while not Path(sys.argv[2]).exists(): time.sleep(0.02)\n', encoding='utf-8')
        env = self.child_environment({'HMB_MAX_CONCURRENT': '1'})
        with self.daemon(env=env):
            client = BoardClient(self.directory, autostart=False)
            child = self.submit_execution(requestId='gated-descendant', task='Private command stop evidence',
                                          cwd=str(self.repo), adapter='command', expectedSeconds=300,
                                          argv=[sys.executable, str(script), str(started), str(release)])['task']['runId']
            try:
                self.assertTrue(wait_for(started.exists, timeout=30))
                # The test-only command admission cannot create Host helpers.
                # Seed just its ownership edge to exercise the real observation
                # path; the child process, receipt and shutdown proof are real.
                with sqlite3.connect(self.directory / 'board.sqlite3') as connection:
                    connection.execute('INSERT INTO workflow_children VALUES(?,?,?,?,?,?)',
                                       (child, parent['runId'], 'model-free-stop-fixture', 'cancelled',
                                        '2026-11-06T00:00:00.000Z', '2026-11-06T00:00:00.000Z'))
                before = client.get(runId=parent['runId'])
                self.assertEqual(before['workflowState'], 'cancelled')
                self.assertFalse(before['workflowShutdown']['descendantsConfirmed'])
                with ThreadPoolExecutor(1) as pool:
                    future = pool.submit(self.cli, 'await', json.dumps({'runId': parent['runId'], 'waitSeconds': 30}),
                                         env=env, timeout=45)
                    try:
                        self.assertTrue(wait_for(lambda: client.health()['waitAdmission']['admitted'] > 0, timeout=15))
                        threading.Event().wait(0.2)
                        self.assertFalse(future.done(), 'await returned before descendant shutdown proof')
                    finally:
                        release.touch()
                    _, reply = future.result(timeout=15)
                # Queued cancellation has no execution report; its missing-report
                # response is still gated on confirmed whole-lineage shutdown.
                self.assertEqual(reply['outcome'], 'result-unavailable', reply)
                self.assertTrue(reply['shutdownConfirmed'], reply)
                self.assertTrue(reply['shutdown']['descendantsConfirmed'])
                self.assertEqual(client.get(runId=parent['runId'])['revision'], before['revision'])
            finally:
                release.touch()
