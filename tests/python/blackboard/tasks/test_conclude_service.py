"""One real CLI call cancels and concludes work through a private offline service."""
import json
from pathlib import Path
import threading

from blackboard.tasks.test_workflow_worker import BUDDY, FIXTURE, MicroTaskWorkerTestCase
from fake_executable import make_fake_executable
from hey_my_buddy.blackboard.store.store import BoardStore
from support import wait_for


class ConcludeServiceTests(MicroTaskWorkerTestCase):
    def held_prompt(self):
        started = self.directory / "prompt-started"
        release = self.directory / "release-prompt"
        # Hold the offline model's prompt until the real service records cancel.
        # The harness then uses its usual finish and process shutdown paths.
        source = (
            "import importlib.util, sys, time\n"
            "from pathlib import Path\n"
            f"spec = importlib.util.spec_from_file_location('conclude_agent', {str(FIXTURE)!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "spec.loader.exec_module(module)\n"
            "original = module.WorkflowAgent.run_micro_task_prompt\n"
            "def held_prompt(self, message):\n"
            f"    Path({str(started)!r}).write_text('running')\n"
            f"    while not Path({str(release)!r}).exists():\n"
            "        time.sleep(0.02)\n"
            "    return original(self, message)\n"
            "module.WorkflowAgent.run_micro_task_prompt = held_prompt\n"
            f"sys.argv[1:1] = ['--fixture-root', {str(self.directory)!r}]\n"
            "module.main()\n"
        )
        self.harness_cli = make_fake_executable(self.directory / "conclude-bin", "dsh", source=source)
        return started, release

    def test_running_conclude_waits_for_real_process_stop_and_reclaims_the_checkout(self):
        started, release = self.held_prompt()
        with self.daemon(env=self.env()):
            code, submitted = self.cli("submit", json.dumps({
                "macroTaskId": "standalone", "title": "Running conclusion",
                "task": "Hold the offline prompt until the Host concludes",
                "buddy": " / ".join(BUDDY.values()), "cwd": str(self.repo),
                "checkout": "worktree", "access": "write", "inputs": [],
                "writeScope": ["."], "expectedSeconds": 600,
            }), env=self.env())
            self.assertEqual(code, 0, submitted)
            self.assertTrue(wait_for(started.exists, timeout=30))
            run_id = submitted["runId"]
            observation = []

            def observe_cancel():
                store = BoardStore(self.directory)
                try:
                    def cancelled():
                        with store.db.read() as connection:
                            return connection.execute(
                                "SELECT state FROM workflow_runs WHERE run_id=?", (run_id,),
                            ).fetchone()[0] == "cancelled"
                    if wait_for(cancelled, timeout=30):
                        with store.db.read() as connection:
                            row = connection.execute("SELECT stop_initiator,grace_deadline FROM attempts WHERE task_id=?",
                                                     (run_id,)).fetchone()
                            conclusion = connection.execute("SELECT 1 FROM workflow_host_conclusions WHERE run_id=?",
                                                            (run_id,)).fetchone()
                        observation.append((tuple(row), conclusion))
                finally:
                    release.write_text("finish")
                    store.db.close()

            observer = threading.Thread(target=observe_cancel)
            observer.start()
            try:
                code, answer = self.cli("conclude", json.dumps({
                    "runId": run_id, "note": "Preserve and close the running work", "waitSeconds": 45,
                }), env=self.env(), timeout=60)
            finally:
                release.write_text("finish")
                observer.join(timeout=35)
            self.assertFalse(observer.is_alive())
            self.assertEqual(code, 0, answer)
            self.assertEqual(answer["state"], "concluded")
            self.assertEqual(observation[0][0][0], "host")
            self.assertIsNotNone(observation[0][0][1])
            self.assertIsNone(observation[0][1])
            self.assertTrue(answer["reclaim"]["removed"], answer)
            self.assertFalse(Path(answer["reclaim"]["path"]).exists())
            code, view = self.cli("get", json.dumps({"runId": run_id}), env=self.env())
            self.assertEqual(code, 0, view)
            self.assertTrue(view["shutdown"]["selfConfirmed"])
            self.assertTrue(view["shutdown"]["descendantsConfirmed"])
            self.assertIsNotNone(view["hostConclusion"])

    def test_unconfirmed_process_stop_times_out_without_conclusion_and_can_be_retried(self):
        started, release = self.held_prompt()
        with self.daemon(env=self.env()):
            code, submitted = self.submit("unknown-stop")
            self.assertEqual(code, 0, submitted)
            self.assertTrue(wait_for(started.exists, timeout=30))
            packet = {"runId": submitted["runId"], "note": "Close after actual stop", "waitSeconds": 1}
            try:
                code, answer = self.cli("conclude", json.dumps(packet), env=self.env(), timeout=20)
                self.assertEqual(code, 1, answer)
                self.assertEqual(answer["error"]["code"], "SHUTDOWN_UNCONFIRMED")
                self.assertTrue(answer["timedOut"])
                self.assertGreaterEqual(answer["waitedSeconds"], 1)
                self.assertTrue(answer["cancellationRecorded"])
                self.assertFalse(answer["conclusionRecorded"])
                self.assertFalse(answer["shutdownConfirmed"])
                code, view = self.cli("get", json.dumps({"runId": submitted["runId"]}), env=self.env())
                self.assertEqual(code, 0, view)
                self.assertIsNotNone(view["cancellation"])
                self.assertIsNone(view["hostConclusion"])
            finally:
                release.write_text("finish")
            store = BoardStore(self.directory)
            try:
                def stopped():
                    with store.db.read() as connection:
                        summary = store.workflow.shutdown_summary(connection, submitted["runId"])
                    return summary["selfConfirmed"] and summary["descendantsConfirmed"]
                self.assertTrue(wait_for(stopped, timeout=30))
            finally:
                store.db.close()
            code, answer = self.cli("conclude", json.dumps(packet), env=self.env(), timeout=20)
            self.assertEqual(code, 0, answer)
            self.assertEqual(answer["state"], "concluded")


def load_tests(loader, _tests, _pattern):
    return loader.loadTestsFromTestCase(ConcludeServiceTests)
