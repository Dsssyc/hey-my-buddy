"""Idle Workers wait on the board cursor, without polling claims or losing work."""
import threading
import time
from unittest.mock import patch

from support import BoardTestCase
from hey_my_buddy.blackboard.service.daemon import Daemon
from hey_my_buddy.blackboard.service.service import WaitAdmission
from hey_my_buddy.protocol import rpc_config
from hey_my_buddy.buddy.runtime.worker import Worker
from hey_my_buddy.errors import BoardError


class IdleWorkerTests(BoardTestCase):
    def test_worker_waits_preserve_public_slots_and_the_aggregate_control_reserve(self):
        admission = WaitAdmission(32)
        for index in range(32):
            self.assertTrue(admission.acquire(worker_id=f"w-{index}"))
        for _ in range(rpc_config.MAX_WAIT_CAPACITY - 32):
            self.assertTrue(admission.acquire())
        self.assertFalse(admission.acquire())
        self.assertFalse(admission.acquire(worker_id="w-extra"))
        self.assertEqual(admission.stats()["available"], 0)
        admission.release(worker_id="w-0")
        self.assertEqual(admission.stats()["available"], 1)
        self.assertTrue(admission.acquire())

    def test_worker_wait_admission_requires_registration_and_one_wait_per_worker(self):
        board = self.board()
        with self.assertRaises(BoardError) as unknown:
            board.client().wait_events(after=0, timeout_ms=0, workerId="unregistered")
        self.assertEqual(unknown.exception.code, "NOT_REGISTERED")
        admission = WaitAdmission(1)
        self.assertTrue(admission.acquire(worker_id="registered"))
        self.assertFalse(admission.acquire(worker_id="registered"))
        self.assertEqual(admission.stats()["available"], 1)
        self.assertTrue(admission.acquire())
        admission.release(worker_id="registered")
        self.assertTrue(admission.acquire(worker_id="registered"))

    def start_worker(self, board, name="idle"):
        worker = Worker(name, self.directory, client=board.client(), adapters=("command",))
        worker.live.start = lambda: None
        worker.live.stop = lambda: None
        failures = []

        def run():
            try:
                worker.run()
            except BaseException as error:
                failures.append(error)

        thread = threading.Thread(target=run)

        def stop():
            worker.stop.set()
            thread.join(4)
            self.assertFalse(thread.is_alive())
            self.assertEqual(failures, [])

        self.addCleanup(stop)
        return worker, thread

    def submit(self, board, request_id="wake"):
        return board.client().submit(requestId=request_id, task="wake the idle Worker",
                                     cwd=str(self.workdir(request_id)), adapter="command", argv=["/bin/true"])["task"]

    def observe_wait(self, board, entered):
        original = board.store._condition.wait

        def wait(timeout):
            entered.set()
            return original(timeout)

        return patch.object(board.store._condition, "wait", side_effect=wait)

    def execute_once(self, worker, claimed):
        def execute(claim):
            claimed.append(claim)
            worker.stop.set()
            return {}

        worker.execute = execute
        worker.deliver = lambda receipt: True

    def test_queued_work_wakes_the_waiting_worker_without_a_two_second_delay(self):
        board = self.board()
        entered = threading.Event()
        claimed = []
        worker, thread = self.start_worker(board)
        self.execute_once(worker, claimed)
        with self.observe_wait(board, entered):
            thread.start()
            self.assertTrue(entered.wait(3))
            started = time.monotonic()
            task = self.submit(board)
            thread.join(1)
            self.assertFalse(thread.is_alive(), "work was not claimed within one second of submission")
            self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual([claim["task"]["taskId"] for claim in claimed], [task["taskId"]])
        self.assertFalse((worker.spool.directory / "heartbeat.json").exists())

    def test_work_between_the_empty_claim_and_wait_is_not_lost(self):
        board = self.board()
        worker, thread = self.start_worker(board)
        original = worker.client.claim
        claimed, empty_claims = [], []
        self.execute_once(worker, claimed)

        def claim(*args, **kwargs):
            response = original(*args, **kwargs)
            if response["claim"] is None:
                empty_claims.append(response["reason"])
                self.submit(board)
            return response

        worker.client.claim = claim
        thread.start()
        thread.join(1)
        self.assertFalse(thread.is_alive(), "the task queued before subscription was missed")
        self.assertEqual(len(empty_claims), 1)
        self.assertEqual(len(claimed), 1)

    def test_wait_timeouts_only_continue_waiting_without_claims_or_spool_writes(self):
        board = self.board()
        worker, thread = self.start_worker(board)
        original_wait = worker.client.wait_events
        cursors = []
        quiet_baseline = []

        def wait(**params):
            page = original_wait(**params)
            if not page["timedOut"]:
                # Startup registration can wake this new Worker before it reaches
                # steady waiting. Only empty windows belong to this observation.
                return page
            if not quiet_baseline:
                quiet_baseline.extend((claim.call_count, write.call_count))
            self.assertEqual((claim.call_count, write.call_count), tuple(quiet_baseline))
            cursors.append(params["after"])
            if len(cursors) == 3:
                worker.stop.set()
            return page

        worker.client.wait_events = wait
        with patch("hey_my_buddy.buddy.runtime.worker.IDLE_WAIT_TIMEOUT_MS", 50), \
                patch.object(worker.client, "claim", wraps=worker.client.claim) as claim, \
                patch.object(worker.spool, "write_startup", wraps=worker.spool.write_startup) as write:
            thread.start()
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual((claim.call_count, write.call_count), tuple(quiet_baseline))
        self.assertEqual(len(cursors), 3)
        self.assertEqual(len(set(cursors)), 1)

    def test_local_stop_and_retirement_end_an_idle_wait_within_three_seconds(self):
        for intent in ("stop", "retire", "signal"):
            with self.subTest(intent=intent):
                board = self.board()
                worker, thread = self.start_worker(board, name=f"idle-{intent}")
                entered = threading.Event()
                with self.observe_wait(board, entered):
                    thread.start()
                    self.assertTrue(entered.wait(3))
                    if intent == "signal":
                        worker.stop.set()
                    else:
                        path = worker.stop_request_path if intent == "stop" else worker.retire_request_path
                        path.touch()
                    thread.join(3)
                    self.assertFalse(thread.is_alive())

    def test_two_woken_workers_cannot_claim_the_same_task(self):
        board = self.board()
        workers = [self.start_worker(board, name=f"idle-{index}") for index in range(2)]
        entered = {worker.worker_id: threading.Event() for worker, _ in workers}
        claimed = []
        for worker, _ in workers:
            self.execute_once(worker, claimed)
            original = worker.client.wait_events

            def wait(*, _worker=worker, _original=original, **params):
                entered[_worker.worker_id].set()
                return _original(**params)

            worker.client.wait_events = wait
        for _, thread in workers:
            thread.start()
        self.assertTrue(all(event.wait(3) for event in entered.values()))
        task = self.submit(board)
        deadline = time.monotonic() + 1
        while not claimed and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertEqual(len(claimed), 1)
        for worker, thread in workers:
            worker.stop.set()
        for _, thread in workers:
            thread.join(3)
            self.assertFalse(thread.is_alive())
        with board.store.db.read() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM attempts WHERE task_id=?",
                                                (task["taskId"],)).fetchone()[0], 1)

    def test_service_stop_and_restart_release_waiters_before_their_deadline(self):
        for action in ("stop", "restart"):
            with self.subTest(action=action):
                board = self.board()
                daemon = Daemon(self.directory)
                daemon.store = board.store
                entered, finished = threading.Event(), threading.Event()
                errors = []

                def wait():
                    try:
                        board.client().wait_events(after=board.store.head(), timeout_ms=30000)
                    except BoardError as error:
                        errors.append(error.code)
                    finally:
                        finished.set()

                thread = threading.Thread(target=wait)
                with self.observe_wait(board, entered):
                    thread.start()
                    try:
                        self.assertTrue(entered.wait(3))
                        getattr(daemon, f"on_{action}")({"drainSeconds": 0})
                        self.assertTrue(finished.wait(.5), "service shutdown did not wake its waiters")
                    finally:
                        board.store.stop_waiters()
                        thread.join(3)
                self.assertEqual(errors, ["SERVICE_STOPPING"])
