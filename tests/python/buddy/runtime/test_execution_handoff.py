"""The scheduling Worker reaps execution before supervising its native survivors."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support import BoardTestCase, wait_for
from hey_my_buddy.buddy.runtime import execution as execution_module
from hey_my_buddy.buddy.runtime import worker as worker_module
from hey_my_buddy.buddy.runtime.receipts import read_receipt, write_receipt
from hey_my_buddy.buddy.runtime.worker import Worker, fsync_json
from hey_my_buddy.protocol.client import BoardClient
from hey_my_buddy.protocol.process_identity import (inspect_process_identity,
    native_process_journal_path, read_native_process_journal)


@unittest.skipIf(os.name == 'nt', 'POSIX signals and process suspension')
class ExecutionHandoffTests(BoardTestCase):
    def claimed_command(self, name):
        work = self.workdir(name)
        ready = work / 'ready'
        script = ('from pathlib import Path\nimport time\n'
                  f'Path({str(ready)!r}).touch()\n'
                  'while True: time.sleep(.05)\n')
        client = BoardClient(self.directory, autostart=False)
        worker = Worker(name, self.directory, client=client, adapters=('command',))
        worker.spool.ensure()
        client.register_worker(name, adapter='command', capabilities=['command', 'handoff-fixture'])
        task = client.submit(requestId=name, task='supervise native survivor', cwd=str(work),
                             adapter='command', argv=[sys.executable, '-c', script],
                             expectedSeconds=120, requiredCapabilities=['handoff-fixture'])['task']
        nonce = 'h' * 64
        claim = client.claim(name, 'claim-' + name, nonce, task_id=task['taskId'],
                             worker_instance=worker.instance_id)['claim']
        worker.spool.write_startup({'workerId': name, 'instanceId': worker.instance_id,
            'claimRequestId': 'claim-' + name, 'nonce': nonce, **claim['attempt'],
            'privateAdapter': 'command'})
        return worker, client, claim, ready

    def run_worker(self, worker, claim):
        result, errors = [], []
        def run():
            try:
                result.append(worker.execute(claim))
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=run)
        thread.start()
        self.addCleanup(thread.join, 30)
        return thread, result, errors

    def native_journal(self, worker, claim, identity):
        intent = {**claim['attempt'], 'privateAdapter': 'command'}
        return read_native_process_journal(native_process_journal_path(worker._receipt_directory(intent), identity),
                                          {key: intent[key] for key in ('taskId', 'attemptId', 'generation')})

    def test_killed_execution_transfers_its_live_command_to_worker(self):
        with self.daemon():
            worker, client, claim, ready = self.claimed_command('killed-execution')
            thread, result, errors = self.run_worker(worker, claim)
            marker = worker.spawn_marker(claim['attempt'])
            self.assertTrue(wait_for(lambda: ready.exists() and marker.exists(), 30))
            identity = json.loads(marker.read_text())['processIdentity']
            journal = self.native_journal(worker, claim, identity)
            self.assertEqual(journal['pending'], 0)
            self.assertEqual(len(journal['identities']), 1)
            native = journal['identities'][0]
            self.assertEqual(inspect_process_identity(native), 'alive')
            os.kill(identity['pid'], signal.SIGKILL)
            thread.join(30)
            self.assertFalse(thread.is_alive())
            self.assertEqual(errors, [])
            self.assertTrue(result[0]['report']['shutdownConfirmed'])
            self.assertEqual(inspect_process_identity(native), 'identity-absent')
            view = client.result(taskId=claim['task']['taskId'])
            self.assertTrue(view['resultMeta']['shutdownConfirmed'])
            self.assertEqual(view['resultMeta']['terminationReason'], 'harness-error')

    def test_marker_write_failure_reaps_real_execution_and_native_command(self):
        with self.daemon():
            worker, _client, claim, ready = self.claimed_command('marker-failure')
            launched = []
            original_launch = execution_module.launch_execution
            def launch(*args, **kwargs):
                handle = original_launch(*args, **kwargs)
                launched.append(handle)
                return handle
            original_write = worker_module.fsync_json
            def fail_marker(path, value):
                if path.name == 'spawn.marker':
                    self.assertTrue(wait_for(ready.exists, 30), 'native work must exist at the injected failure')
                    raise OSError('injected spawn.marker write failure')
                return original_write(path, value)
            with mock.patch.object(execution_module, 'launch_execution', side_effect=launch), \
                 mock.patch.object(worker_module, 'fsync_json', side_effect=fail_marker):
                receipt = worker.execute(claim)
            self.assertEqual(len(launched), 1)
            self.assertIsNotNone(launched[0].process.poll())
            journal = self.native_journal(worker, claim, launched[0].identity())
            self.assertEqual(journal['pending'], 0)
            self.assertTrue(journal['identities'])
            self.assertTrue(receipt is None or receipt['report']['shutdownConfirmed'])
            self.assertTrue(all(inspect_process_identity(identity) == 'identity-absent'
                                for identity in journal['identities']))

    def test_frozen_execution_is_killed_after_external_grace_and_native_is_reaped(self):
        with self.daemon():
            worker, client, claim, ready = self.claimed_command('frozen-execution')
            thread, result, errors = self.run_worker(worker, claim)
            marker = worker.spawn_marker(claim['attempt'])
            self.assertTrue(wait_for(lambda: ready.exists() and marker.exists(), 30))
            identity = json.loads(marker.read_text())['processIdentity']
            native = self.native_journal(worker, claim, identity)['identities'][0]
            os.kill(identity['pid'], signal.SIGSTOP)
            started = time.monotonic()
            client.cancel(taskId=claim['task']['taskId'], reason='force frozen execution')
            thread.join(25)
            self.assertFalse(thread.is_alive(), 'Worker must enforce the execution grace externally')
            self.assertGreaterEqual(time.monotonic() - started, worker_module.EXECUTION_STOP_GRACE_SECONDS)
            self.assertLess(time.monotonic() - started, 25)
            self.assertEqual(errors, [])
            self.assertTrue(result[0]['report']['shutdownConfirmed'])
            self.assertEqual(inspect_process_identity(native), 'identity-absent')

    def test_root_pid_exit_does_not_transfer_delivery_while_its_group_survives(self):
        from hey_my_buddy.buddy.harnesses.base import ProcessHandle
        from hey_my_buddy.buddy.runtime.windows_process import owned_popen
        with self.daemon():
            worker, client, claim, ready = self.claimed_command('group-survivor')
            script = ("import subprocess, sys, time\nfrom pathlib import Path\n"
                      "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
                      f"Path({str(ready)!r}).touch()\n"
                      "time.sleep(.2)\n")
            process = owned_popen([sys.executable, '-c', script], start_new_session=True,
                env=self.child_environment(), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            handle = ProcessHandle(process, own_group=True, log_paths={})
            self.addCleanup(handle.terminate, grace_seconds=0)
            self.assertEqual(handle.wait(10), 0)
            self.assertTrue(ready.exists())
            self.assertTrue(handle.group_alive())
            intent = worker.spool.read_startup()
            private = worker._receipt_directory(intent)
            receipt = {'workerId': worker.worker_id, 'taskId': claim['task']['taskId'],
                'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'],
                'commandId': 'result:' + claim['attempt']['attemptId'],
                'report': {'status': 'ok', 'result': {}, 'shutdownConfirmed': True}}
            write_receipt(private, receipt)
            with mock.patch.object(client, 'submit_result') as submit:
                worker._settle_execution(claim, handle)
                submit.assert_not_called()
            self.assertIsNotNone(read_receipt(private))

    def test_live_execution_is_the_only_receipt_sender_across_worker_entry_points(self):
        with self.daemon():
            worker, client, claim, ready = self.claimed_command('single-sender')
            # Launch directly to let the replacement Worker observe durable files
            # while the execution remains alive. It owns no handle for this process.
            handle = execution_module.launch_execution(claim, self.directory, worker.worker_id, worker._nonce())
            self.addCleanup(lambda: (execution_module.stop_execution(handle, force=True), handle.wait(20)))
            self.assertTrue(wait_for(ready.exists, 30))
            intent = worker.spool.read_startup()
            fsync_json(worker.spawn_marker(intent), {'processIdentity': handle.identity()})
            receipt = {'workerId': worker.worker_id, 'taskId': claim['task']['taskId'],
                'attemptId': claim['attempt']['attemptId'], 'generation': claim['attempt']['generation'],
                'commandId': 'result:' + claim['attempt']['attemptId'],
                'report': {'status': 'failed', 'result': None, 'shutdownConfirmed': False}}
            private = worker._receipt_directory(intent)
            write_receipt(private, receipt)
            with mock.patch.object(client, 'submit_result') as submit, \
                 mock.patch.object(worker, 'reconcile_startup_intent', return_value=False), \
                 mock.patch.object(worker, 'recover_orphans', return_value=True):
                worker.reattach()
                self.assertEqual(worker.run_once(), 'retry')
                self.assertTrue(worker.settle_retirement())
                submit.assert_not_called()
            self.assertIsNotNone(read_receipt(private))
            os.kill(handle.pid, signal.SIGKILL)
            handle.wait(15)
            self.assertTrue(worker.deliver(receipt))
            self.assertIsNone(read_receipt(private))
