"""In-flight executions keep their work and receipts across an install journal fence."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
from unittest import mock

from support import BoardTestCase, wait_for
from blackboard.service import test_execution_recovery as recovery_fixture
from install import test_install_without_upgrade as install_fixture
from hey_my_buddy.install import runtime, upgrade
from hey_my_buddy.protocol.process_identity import inspect_process_identity
from hey_my_buddy.private_dirs import attempt_root


class ExecutionInstallFenceTests(BoardTestCase):
    private_runtime = install_fixture.InstallWithoutUpgradeTests.private_runtime
    installation = install_fixture.InstallWithoutUpgradeTests.installation
    start = install_fixture.InstallWithoutUpgradeTests.start
    client = install_fixture.InstallWithoutUpgradeTests.client
    command = install_fixture.InstallWithoutUpgradeTests.command
    running = install_fixture.InstallWithoutUpgradeTests.running
    finished = install_fixture.InstallWithoutUpgradeTests.finished

    def install_at_fence(self, release: Path, observed: Path, *,
                         not_started: Path | None = None, after_observed=None) -> None:
        original_write = upgrade.write_journal
        held = []
        observation = []

        def after_first_journal(path, value):
            original_write(path, value)
            if path == self.directory / "upgrade.json" and not held:
                held.append(value["phase"])
                release.touch()
                observation.append((bool(wait_for(observed.exists, timeout=20)),
                                    not_started is not None and not_started.exists()))
                if after_observed is not None:
                    after_observed()

        with mock.patch.object(upgrade, "write_journal", side_effect=after_first_journal):
            installed = upgrade.upgrade({})
        self.assertEqual(held, ["preflight"])
        self.assertTrue(installed["upgraded"], installed)
        self.assertNotIn("backup", installed)
        self.assertFalse((self.directory / "upgrade.json").exists())
        self.assertEqual(observation, [(True, False)],
                         f"execution did not retry {observed.name} while the native turn stayed gated")

    def test_result_receipt_is_retried_during_real_install_and_accepted_after_cutover(self):
        baseline = self.private_runtime("delivery-baseline", 1)
        candidate = self.private_runtime("delivery-candidate", 2)
        self.catalog_fixture()
        with self.installation(candidate):
            self.start(baseline)
            run_id, release, attempt = self.command("fenced-delivery")
            directory = attempt_root(self.directory, "command", run_id, attempt["attemptId"])
            log = self.directory / "attempts" / run_id / attempt["attemptId"] / "execution.stdout.log"
            observed = self.directory / "result-fence-observed"
            original_write = upgrade.write_journal
            held = []

            def after_first_journal(path, value):
                original_write(path, value)
                if path == self.directory / "upgrade.json" and not held:
                    held.append(value["phase"])
                    release.touch()
                    self.assertTrue(wait_for(lambda: log.exists() and
                        "result delivery pending: UPGRADE_IN_PROGRESS" in log.read_text(encoding="utf-8"), timeout=20),
                        "the real execution did not attempt result delivery through the fence")
                    self.assertTrue((directory / "result-receipt.json").exists())
                    self.assertFalse((directory / "result-delivery.json").exists())
                    self.assertEqual(inspect_process_identity(attempt["processIdentity"]), "alive")
                    observed.touch()

            with mock.patch.object(upgrade, "write_journal", side_effect=after_first_journal):
                installed = upgrade.upgrade({})
            self.assertEqual(held, ["preflight"])
            self.assertTrue(installed["upgraded"], installed)
            self.assertFalse((self.directory / "upgrade.json").exists())
            client = self.client()
            finished = wait_for(lambda: self.finished(run_id), timeout=35)
            self.assertIsNotNone(finished, client.get(runId=run_id))
            self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")
            self.assertEqual(json.loads((directory / "result-delivery.json").read_text(encoding="utf-8")),
                             {"taskId": run_id, "attemptId": attempt["attemptId"], "generation": attempt["generation"],
                              "accepted": True, "error": None})
            self.assertFalse((directory / "result-receipt.json").exists())

    def gated_runtime(self, *, activity_refusal: bool = False) -> Path:
        """Pause this private runtime after identity acknowledgement, before preflight."""
        original_copy = runtime._copy_assets
        ready = self.directory / "preflight.ready"
        release = self.directory / "preflight.release"
        refused = self.directory / "preflight.refused"
        self.addCleanup(release.touch)

        def copy_with_gate(source, destination):
            original_copy(source, destination)
            execution = destination / "src/hey_my_buddy/buddy/runtime/execution.py"
            text = execution.read_text(encoding="utf-8")
            if activity_refusal:
                error = "            except BoardError as error:\n                if error.code in RETRYABLE:\n"
                self.assertEqual(text.count(error), 1)
                observation = ("            except BoardError as error:\n"
                               f"                if error.code == 'UPGRADE_IN_PROGRESS' and pending.get('activity') is not None: Path({str(refused)!r}).touch()\n"
                               "                if error.code in RETRYABLE:\n")
                text = text.replace(error, observation, 1)
            else:
                start = "        receipt = self.execute()\n"
                self.assertEqual(text.count(start), 1)
                gate = (f"        Path({str(ready)!r}).touch()\n"
                        f"        while not Path({str(release)!r}).exists(): time.sleep(0.05)\n")
                text = text.replace(start, gate + start, 1)
                error = "                    except BoardError as error:\n"
                self.assertEqual(text.count(error), 1)
                observation = ("                    except BoardError as error:\n"
                               f"                        if error.code == 'UPGRADE_IN_PROGRESS': Path({str(refused)!r}).touch()\n")
                text = text.replace(error, observation, 1)
                text = text.replace("PREFLIGHT_RETRY_SECONDS = 15.0", "PREFLIGHT_RETRY_SECONDS = 0.2", 1)
            execution.write_text(text, encoding="utf-8", newline="\n")

        with mock.patch.object(runtime, "_copy_assets", side_effect=copy_with_gate):
            target = self.private_runtime("activity-baseline" if activity_refusal else "preflight-baseline", 1)
        return target

    def test_offline_harness_prepare_retries_during_journal_and_native_turn_completes(self):
        baseline = self.gated_runtime()
        candidate_runtime = self.private_runtime("preflight-candidate", 2)
        script, started, release, candidate = recovery_fixture.PrivateExecutionRecoveryTests.offline_router(self)
        self.addCleanup(release.touch)
        with self.installation(candidate_runtime, overrides={"HMB_DSH_CLI": str(script)}):
            self.start(baseline)
            created = self.client().call("selection_request", {
                "requestId": "fenced-router", "task": "Choose an offline fixture buddy"})
            self.assertTrue(wait_for((self.directory / "preflight.ready").exists, timeout=30), created)
            attempt = self.client().get(runId=created["runId"])["selectedAttempt"]
            self.install_at_fence(self.directory / "preflight.release", self.directory / "preflight.refused",
                                  not_started=started)
            self.assertTrue(wait_for(started.exists, timeout=40), "preflight did not retry")
            self.assertEqual(inspect_process_identity(attempt["processIdentity"]), "alive")
            release.touch()
            decision = wait_for(lambda: install_fixture.InstallWithoutUpgradeTests._completed_decision(
                self, created["decisionId"]), timeout=45)
            self.assertIsNotNone(decision)
            self.assertEqual(decision["buddyId"], candidate)
            finished = wait_for(lambda: self.finished(created["runId"]), timeout=35)
            self.assertIsNotNone(finished)
            self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")

    def test_native_activity_hint_waits_through_install_without_cancelling_turn(self):
        baseline = self.gated_runtime(activity_refusal=True)
        candidate_runtime = self.private_runtime("activity-candidate", 2)
        script, _router_started, _router_release, _candidate = recovery_fixture.PrivateExecutionRecoveryTests.offline_router(self)
        started = self.directory / "worker.started"
        release = self.directory / "worker.release"
        self.addCleanup(release.touch)
        script_source = self.directory / "fixture-router.py"
        script_text = script_source.read_text(encoding="utf-8")
        point = "            (root/'worker.started').touch()\n"
        self.assertEqual(script_text.count(point), 1)
        emission = ("            (root/'worker.started').touch()\n"
                    "            while not (root/'activity.release').exists(): time.sleep(.05)\n"
                    "            session_id=(message.get('params') or {}).get('sessionId')\n"
                    "            self.notify_client('session/update', {'sessionId':session_id,'update':"
                    "{'sessionUpdate':'agent_message_chunk','content':"
                    "{'type':'text','text':'offline fixture activity'}}})\n"
                    "            (root/'activity.sent').touch()\n")
        script_source.write_text(script_text.replace(point, emission, 1), encoding="utf-8", newline="\n")
        repo = self.workdir("worker-repo")
        for command in (("init", "-q", "-b", "main"),
                        ("config", "user.email", "buddy@example.invalid"),
                        ("config", "user.name", "Buddy Test")):
            subprocess.run(["git", *command], cwd=repo, check=True, capture_output=True)
        (repo / "tracked.txt").write_text("private fixture\n", encoding="utf-8", newline="\n")
        subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=repo,
                       check=True, capture_output=True)
        with self.installation(candidate_runtime, overrides={"HMB_DSH_CLI": str(script)}):
            self.start(baseline)
            created = self.client().call("workflow_submit", {
                "requestId": "fenced-activity", "hostId": "host-1",
                "task": "Complete a gated offline fixture turn", "cwd": str(repo),
                "submissionToken": "t" * 32, "executionWorkspace": {"kind": "existing", "access": "write"},
                "harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash",
                "effort": "max"})
            self.assertTrue(wait_for(started.exists, timeout=60), created)
            attempt = self.running(created["runId"])["selectedAttempt"]
            self.install_at_fence(self.directory / "activity.release", self.directory / "preflight.refused",
                                  after_observed=release.touch)
            self.assertTrue((self.directory / "activity.sent").exists())
            client = self.client()
            finished = wait_for(lambda: self.finished(created["runId"]), timeout=35)
            self.assertIsNotNone(finished, client.get(runId=created["runId"]))
            self.assertEqual(finished["selectedAttempt"]["attemptId"], attempt["attemptId"])
            self.assertEqual(finished["selectedAttempt"]["result"]["status"], "ok")
            self.assertIsNotNone(finished["activity"])
            self.assertEqual(finished["terminationReason"], "completed")
