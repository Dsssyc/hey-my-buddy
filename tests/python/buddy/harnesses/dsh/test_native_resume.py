"""The DSH native continuation: two real processes over one shared sessions root.

These cases drive the governed Worker carrier of
:func:`hey_my_buddy.buddy.harnesses.dsh.native_run.run` twice — a first turn
that opens a fresh root session in this task's native root, and a second turn
in its own invocation that resumes the exact same session through the public
``session/resume`` — against the real fake ACP agent, the real launch wrapper,
the real signed session tools and the real persistent rollout files. The
resume facts under test are the ones the acceptance record names: the request
sequence (resume, never a silent new), the identity sources, this attempt's
remounted completion and inquiry service, the replayed history's isolation
from this turn's tool, model, receipt and activity facts, the model/effort
readback and launch patch of the resumed launch, the definite failures for a
missing binding, a native refusal and a foreign resumed root, and the frozen
record boundary that makes only the appended segment this turn's usage.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import threading
import time
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.run_contract import RunContinuation
from hey_my_buddy.buddy.harnesses.dsh import native_run
from hey_my_buddy.buddy.harnesses.dsh.native_run import run
from hey_my_buddy.buddy.roles.run_observers import worker_observer

from buddy.harnesses.dsh.test_native_run import NativeRunCase


def _record_facts(result) -> dict:
    reference = next(ref for ref in result.evidence_refs if ref.kind == "dsh-session-record")
    return json.loads(Path(reference.location).read_text())


def _provenance(result) -> dict:
    reference = next(ref for ref in result.evidence_refs if ref.kind == "turn-provenance")
    return json.loads(Path(reference.location).read_text())


class NativeResumeCase(NativeRunCase):
    """Two governed turns: the first opens the root, the second resumes it."""

    def first_turn(self, *, record="usage", extra=(), inquiry=False):
        request, bound, mount = self.worker_request(inquiry=inquiry)
        self.governed_agent_args(mount)
        self.extra_agent_args += ["--session-record", record, *extra]
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        return request, bound, mount, result

    def resumed_turn(self, previous: str, *, record="resume", extra=(), inquiry=False):
        request, bound, mount = self.worker_request(
            inquiry=inquiry,
            continuation=RunContinuation(mode="native-session", previous_session_id=previous))
        self.governed_agent_args(mount)
        self.extra_agent_args += ["--session-record", record, *extra]
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        return request, bound, mount, result

    def methods_after_last_initialize(self) -> list[str | None]:
        """The inbound request methods of the most recent agent process."""
        entries = [entry for entry in self.agent_log() if entry.get("dir") == "in"]
        indexes = [index for index, entry in enumerate(entries)
                   if (entry.get("raw") or {}).get("method") == "initialize"]
        last = entries[indexes[-1]:] if indexes else []
        return [(entry.get("raw") or {}).get("method") for entry in last]


class ResumeTurnTests(NativeResumeCase):
    """V-R2 and V-R4: the resumed turn itself, its launch and its configuration."""

    def test_two_invocations_resume_the_same_session_over_the_shared_root(self):
        request1, _bound1, mount1, result1 = self.first_turn()
        self.assertEqual(result1.end.status, "ok", result1.end.message)
        previous = result1.native_identity.session_id
        self.assertTrue(result1.continuation.resumable)
        # The first turn's own usage stays the whole fresh record's projection.
        self.assertEqual(result1.usage.value["inputTokens"], 3150)
        self.assertEqual(result1.usage.value["completeness"], "complete")

        request2, bound2, mount2, result2 = self.resumed_turn(
            previous, extra=("--replay-history",))
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        # The identity is the resume request's own: same session, no new root.
        self.assertEqual(result2.native_identity.session_id, previous)
        self.assertEqual(self.methods_after_last_initialize(),
                         ["initialize", "session/resume", "session/set_config_option",
                          "session/set_config_option", "session/prompt", "session/close"])
        # This attempt's own service was mounted on the resume, not the first
        # attempt's: the fresh mount names a different server and the fake
        # actually mounted it in the second process.
        self.assertNotEqual(mount2.server_name, mount1.server_name)
        mounted = [entry for entry in self.agent_log()
                   if entry.get("event") == "stub-mounted" and entry.get("name")]
        self.assertIn(mount2.server_name, [entry["name"] for entry in mounted])
        # A complete verified resumed turn keeps the session resumable and the
        # binding unchanged under the native root.
        self.assertTrue(result2.continuation.resumable)
        binding = self.root / (hashlib.sha256(previous.encode()).hexdigest() + ".json")
        self.assertEqual(json.loads(binding.read_text())["sessionId"], previous)
        provenance = _provenance(result2)
        self.assertEqual(provenance["nativeSessionId"], previous)
        self.assertTrue(provenance["receiptVerified"])
        record = {"version": 1, "resumeMode": "native-session",
                  "previousSessionId": previous, "sessionId": previous,
                  "provenance": provenance}
        self.assertIsNone(native_run.validate_turn_provenance(record))

    def test_the_resumed_turn_counts_only_its_own_usage_tokens(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous)
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        usage = result2.usage.value
        # Only the appended turn-two rows, in the shared normalized shape
        # (input includes its cached part): the previous turn's counters never
        # leak into this turn's facts, and nothing was derived by subtracting
        # cumulative counters.
        self.assertEqual(usage["completeness"], "complete")
        self.assertEqual(usage["nativeRecords"], 2)
        self.assertEqual(usage["inputTokens"], 7350)
        self.assertEqual(usage["outputTokens"], 700)
        self.assertEqual(usage["cachedInputTokens"], 350)
        self.assertEqual(usage["reasoningOutputTokens"], 70)
        self.assertEqual(result2.last_assistant_message.value["text"], "the resumed final answer")
        facts = _record_facts(result2)
        boundary = facts["resumeBoundary"]
        self.assertTrue(boundary["proven"])
        self.assertIsNone(boundary["reason"])
        self.assertEqual(boundary["baselineFiles"], 1)
        self.assertEqual(boundary["baselineLines"], 10)
        self.assertEqual(boundary["appendedLines"], 9)
        self.assertEqual(facts["bootstrapUpdates"], 0)
        self.assertEqual(facts["sessionIdentitySource"], "resume-request")

    def test_replayed_history_never_becomes_this_turns_facts(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        request2, _bound2, _mount2, result2 = self.resumed_turn(
            previous, extra=("--replay-history",))
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        # The four replayed notifications — an old usage snapshot, an old
        # completed tool pair and an old message chunk — were drained as
        # bootstrap: no tool fact, no model-start side effects, no activity
        # and no unknown-event count from the previous turn.
        self.assertEqual(_record_facts(result2)["bootstrapUpdates"], 4)
        package = result2.tool_evidence.value
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(package["events"], [])
        self.assertEqual(package["nativeIdentity"], [{"sessionId": previous}])
        self.assertEqual(result2.activity.value["counts"]["modelTurns"], 1)
        # The replayed old tool pair is not this turn's activity: only this
        # turn's own checkpoint and finish calls are counted.
        self.assertEqual(result2.activity.value["counts"]["toolCalls"], 2)

    def test_the_resumed_launch_pins_the_shared_root_and_keeps_its_own_home(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        request2, _bound2, _mount2, result2 = self.resumed_turn(previous)
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        invocation = Path(request2.private_state.invocation_root)
        # The launch bookkeeping — DSH_HOME, the launch log, the frame log and
        # the patch — lives inside this invocation's private root, while the
        # shared sessions directory the rollout was pinned to lives under the
        # task's native root and the record is really there.
        launches = [json.loads(line) for line
                    in (invocation / "logs" / "launches.jsonl").read_text().splitlines() if line]
        record2 = launches[-1]
        self.assertTrue(record2["dshHome"].startswith(str(invocation)))
        self.assertTrue((invocation / "logs" / "frames.jsonl").is_file())
        patch_path = Path(record2["argv"][record2["argv"].index("--patch") + 1])
        self.assertTrue(str(patch_path).startswith(str(invocation)))
        rows = json.loads(patch_path.read_text())
        self.assertEqual(rows[0]["id"], "session-persistence-jsonl")
        self.assertEqual(rows[0]["config"]["root"], str(self.root / "sessions"))
        self.assertEqual([row["id"] for row in rows[1:]],
                         ["session-title-llm", "session-telemetry-otel"])
        rollout = list((self.root / "sessions").glob(f"{previous}.v3.jsonl*"))
        self.assertEqual(len(rollout), 1, "the resumed rollout lives in the shared root")
        # The fixture's prepared directory under the native root stays empty:
        # a Worker run's DSH_HOME lives in its invocation root, never here.
        self.assertEqual(list((self.root / "dsh-home").iterdir()), [])
        # The model and effort were selected and read back on the resumed
        # session through the public configuration options.
        checked = result2.configuration.checked
        self.assertEqual(checked.model.value, "m1")
        self.assertEqual(checked.effort.value, "high")


class ResumeFailureTests(NativeResumeCase):
    """V-R5: missing, refused and foreign resumes fail definitely, never fall back."""

    def test_a_previous_session_without_a_binding_fails_before_any_process(self):
        request, bound, mount = self.worker_request(
            continuation=RunContinuation(mode="native-session",
                                         previous_session_id="never-bound-session"))
        self.governed_agent_args(mount)
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        self.assertIn("no private goal binding", result.end.message)
        # No process was ever spawned and no session was silently rebuilt.
        self.assertEqual(self.agent_log(), [])
        self.assertIsNone(result.native_identity)
        self.assertIsNone(result.model_started)
        self.assertEqual(result.stop_evidence.native.group_state, "gone",
                         "the spawn never happened; gone is the honest fact")
        self.assertIsNone(result.continuation)

    def test_a_binding_for_another_goal_or_configuration_is_refused(self):
        # A binding that names this session but a foreign goal: existence
        # alone is not eligibility.
        request, bound, mount = self.worker_request(
            continuation=RunContinuation(mode="native-session",
                                         previous_session_id="foreign-goal-session"))
        self.governed_agent_args(mount)
        binding = self.root / (hashlib.sha256(b"foreign-goal-session").hexdigest() + ".json")
        binding.write_text(json.dumps({"taskId": "another-task", "sessionId": "foreign-goal-session",
                                       "cwd": request.cwd,
                                       "configuration": {"provider": "fake", "model": "m1",
                                                         "effort": "high"}}))
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-unavailable")
        self.assertIn("does not match this goal", result.end.message)
        self.assertEqual(self.agent_log(), [], "no process runs for a foreign binding")

    def test_a_native_resume_refusal_fails_without_a_new_session(self):
        request, bound, mount = self.worker_request(
            continuation=RunContinuation(mode="native-session",
                                         previous_session_id="ghost-session"))
        self.governed_agent_args(mount)
        binding = self.root / (hashlib.sha256(b"ghost-session").hexdigest() + ".json")
        binding.write_text(json.dumps({"taskId": request.identity.task_id,
                                       "sessionId": "ghost-session", "cwd": request.cwd,
                                       "configuration": {"provider": "fake", "model": "m1",
                                                         "effort": "high"}}))
        result = run(request, observer=worker_observer, services=bound.services,
                     cancelled=lambda: False)
        self.assertEqual(result.end.status, "error")
        self.assertEqual(result.end.reason_code, "native-resume-failed")
        self.assertIn("refused session/resume", result.end.message)
        # The agent that refused the resume was never asked for a new session.
        self.assertEqual(self.methods_after_last_initialize(), ["initialize", "session/resume"])
        self.assertIsNone(result.native_identity)

    def test_a_foreign_session_id_echoed_by_the_resume_answer_fails(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(
            previous, extra=("--resume-echo-foreign-id",))
        self.assertEqual(result2.end.status, "error")
        self.assertEqual(result2.end.reason_code, "wrong-native-session")
        self.assertIn("differs from the requested session", result2.end.message)
        self.assertIsNone(result2.continuation)

    def test_a_resumed_turn_that_runs_under_a_foreign_root_fails_honestly(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(
            previous, record="usage", extra=("--resume-foreign-root",))
        # The agent accepted the resume but framed its whole turn — the finish
        # tool call included — under a session this run never requested: the
        # root-bound evidence never verifies a finish, the run fails, and the
        # foreign tool call stays a retained fact instead of a delivery.
        self.assertEqual(result2.end.status, "error")
        self.assertEqual(result2.end.reason_code, "missing-finish")
        package = result2.tool_evidence.value
        foreign = [event for event in package["events"]
                   if event["nativeIdentity"].get("sessionId") == "foreign-live-root"]
        self.assertTrue(foreign, "the foreign root's finish attempt stays a retained fact")
        self.assertEqual(self.methods_after_last_initialize(),
                         ["initialize", "session/resume", "session/set_config_option",
                          "session/set_config_option", "session/prompt", "session/cancel"])
        self.assertIsNone(result2.continuation)


class ResumeUsageBoundaryTests(NativeResumeCase):
    """V-R6: an unprovable record boundary keeps this turn's usage unknown."""

    def assert_unknown_usage(self, result, reason):
        self.assertEqual(result.end.status, "ok", result.end.message)
        self.assertIsNone(result.usage, "an unprovable boundary is unknown, never cumulative")
        self.assertIsNone(result.last_assistant_message,
                          "the previous turn's assistant text is not this turn's fact")
        self.assertIsNone(result.native_failure)
        boundary = _record_facts(result)["resumeBoundary"]
        self.assertFalse(boundary["proven"])
        self.assertEqual(boundary["reason"], reason)

    def test_a_vanished_rollout_leaves_the_usage_unknown(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous, record="resume-missing")
        self.assert_unknown_usage(result2, "matching-record-set-changed")

    def test_a_rewritten_history_leaves_the_usage_unknown(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous, record="resume-rewrite")
        self.assert_unknown_usage(result2, "frozen-prefix-rewritten")

    def test_a_shortened_rollout_leaves_the_usage_unknown(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous, record="resume-shorten")
        self.assert_unknown_usage(result2, "record-shortened")

    def test_a_replayed_frozen_record_is_not_new_usage(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous, record="resume-duplicate")
        self.assert_unknown_usage(result2, "appended-line-replays-frozen-record")

    def test_an_over_bound_appended_record_is_partial_but_honest(self):
        _request1, _bound1, _mount1, result1 = self.first_turn()
        previous = result1.native_identity.session_id
        _request2, _bound2, _mount2, result2 = self.resumed_turn(previous, record="resume-flood")
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        usage = result2.usage.value
        self.assertEqual(usage["completeness"], "partial", "a bound stop is never complete")
        self.assertEqual(usage["inputTokens"], 7350, "the definite appended part stands")
        facts = _record_facts(result2)
        self.assertTrue(facts["truncated"])
        self.assertTrue(facts["resumeBoundary"]["proven"])


class ResumeInquiryTests(NativeResumeCase):
    """V-R3: the completion, inquiry and live channel rebind to this attempt."""

    def test_a_resumed_turn_delivers_this_attempts_question_over_its_own_channel(self):
        from buddy.harnesses.zcode.test_native_run import fixture_live_channel
        from hey_my_buddy.buddy.harnesses.live import InquiryPayload, LiveRequest
        request1, _bound1, _mount1, result1 = self.first_turn(inquiry=True)
        self.assertEqual(result1.end.status, "ok", result1.end.message)
        previous = result1.native_identity.session_id

        request2, bound2, mount2 = self.worker_request(
            inquiry=True,
            continuation=RunContinuation(mode="native-session", previous_session_id=previous))
        self.governed_agent_args(mount2)
        self.extra_agent_args += ["--session-record", "resume", "--wait-for-inquiry", "12",
                                  "--answer-inquiry", "--replay-history"]
        credentials = bound2.services.inquiry
        channel = fixture_live_channel(
            self, request2.identity, credentials=dict(credentials),
            journal_path=credentials["resultsPath"],
            activity_path=self.base / "unused-activity.json", harness="dsh")
        bound2 = dataclasses.replace(bound2, services=dataclasses.replace(
            bound2.services, live=channel.fixture_endpoint))
        replies: list = []

        def ask():
            self.assertTrue(self.wait_for(lambda: len(
                [entry for entry in self.agent_log() if entry.get("event") == "startup"]) >= 2))
            request = LiveRequest(identity=channel.identity, request_id="live-2", kind="inquiry",
                                  payload=InquiryPayload(question_id="inq-2",
                                                         question="What does the resumed turn see?"))
            end = time.monotonic() + 8
            reply = None
            while time.monotonic() < end:
                reply = channel.request(request, timeout_ms=1000)
                if reply.status != "unavailable":
                    return reply
                time.sleep(0.05)
            return reply

        asking = threading.Thread(target=lambda: replies.append(ask()))
        asking.start()
        try:
            result2 = run(request2, observer=worker_observer, services=bound2.services,
                          cancelled=lambda: False)
        finally:
            asking.join()
        self.assertEqual(result2.end.status, "ok", result2.end.message)
        self.assertEqual(replies[0].status, "queued")
        self.assertEqual(result2.value.parsed.value["disposition"], "completed")
        # The journal carries only this attempt's delivered and answered
        # question; the replayed history delivered nothing.
        records = [json.loads(line) for line
                   in Path(credentials["resultsPath"]).read_text().splitlines() if line]
        self.assertEqual([record.get("inquiryId") for record in records],
                         ["inq-2", "inq-2", "inq-2"])
        self.assertEqual([record.get("state") for record in records],
                         ["queued", "delivered", "answered"])
        self.assertEqual([record.get("attemptId") for record in records],
                         [request2.identity.attempt_id] * 3)
        report = json.loads(Path(next(ref for ref in result2.evidence_refs
                                      if ref.kind == "inquiry-report").location).read_text())
        self.assertEqual(report["answered"], 1)
        self.assertEqual(_record_facts(result2)["bootstrapUpdates"], 4)
        self.assertEqual(result2.native_identity.session_id, previous)
        self.assertTrue(_provenance(result2)["receiptVerified"])


if __name__ == "__main__":  # pragma: no cover - direct execution convenience
    unittest.main()
