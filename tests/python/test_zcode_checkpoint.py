"""End-to-end cooperative ZCode inquiry flows against the real runner and fixture.

Every test here drives the real adapter, controller, native fixture and the real
session-private MCP bridge: a Host question is queued through the private bridge
socket while the turn is live, the fixture's root model picks it up through the
real ``buddy_checkpoint`` tool, answers through the real ``buddy_answer_inquiry``
tool, and only the controller's verified root-turn evidence moves journal state.
The flows cover the finish-refusal retry, settlement races, child relays, forged
receipts, cancellation and the unchanged single native ``session/send``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from test_zcode import ZcodeFixtureCase

from buddy import inquiry as inquiry_module
from buddy.private_dirs import context_root


class ZcodeCheckpointFlowTests(ZcodeFixtureCase):
    def credentials(self, context, timeout=20):
        """Wait until the attempt mounted its bridge AND admitted the root turn."""
        path = context_root(context, "zcode") / "inquiry.json"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if path.is_file():
                credentials = json.loads(path.read_text())
                observed = inquiry_module.bridge_request(credentials, "observe", {}, timeout_ms=500)
                if observed.get("ok") and (observed.get("value") or {}).get("ready") is True:
                    return credentials
            time.sleep(0.05)
        self.fail("the attempt never mounted its private inquiry bridge and admitted the root turn")

    def ask(self, context, inquiry_id, question, credentials=None, timeout_ms=4000):
        credentials = credentials or self.credentials(context)
        asked = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": inquiry_id, "question": question},
                                              timeout_ms=timeout_ms)
        self.assertTrue(asked["ok"], asked)
        self.assertTrue(asked["value"]["accepted"], asked)
        return credentials, asked["value"]

    def release(self, context):
        (context_root(context, "zcode") / "native-logs" / "release-turn").touch()

    def records(self, credentials, inquiry_id):
        path = Path(credentials["resultsPath"])
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text().splitlines()
                if line.strip() and json.loads(line).get("inquiryId") == inquiry_id]

    def native_log(self, context, name):
        path = context_root(context, "zcode") / "native-logs" / name
        return path.read_text() if path.exists() else ""

    def wait_file(self, context, name, timeout=20):
        path = context_root(context, "zcode") / "native-logs" / name
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and not path.exists():
            time.sleep(0.05)
        return path.read_text() if path.exists() else None

    def test_one_send_delivers_and_answers_a_question_and_finishes_completed(self):
        context = self.context("inquiry-live", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials, asked = self.ask(context, "q-1", "Reply with exactly: checkpoint-ok")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")
        # Exactly one admitted native input, and no injected command ever.
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        self.assertNotIn("v4/command", self.native_log(context, "methods.jsonl"))
        self.assertEqual(self.native_log(context, "commands.jsonl"), "")
        # The journal carries the verified ordered transitions with root evidence.
        records = self.records(credentials, "q-1")
        states = [record["state"] for record in records]
        self.assertEqual(states, ["queued", "delivered", "answered"], records)
        self.assertEqual(records[0]["questionSha256"], asked["questionSha256"])
        self.assertEqual(records[1]["toolCallId"], "call-checkpoint-root")
        self.assertEqual(records[2]["answer"]["text"], "fixture answer for q-1")
        self.assertEqual(records[2]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertEqual(records[2]["answer"]["via"], "tool:buddy_answer_inquiry")
        self.assertEqual(records[2]["taskId"], "goal-1")
        self.assertEqual(records[2]["attemptId"], "attempt-1")
        self.assertEqual(outcome.result["inquiry"]["requested"], 1)
        self.assertEqual(outcome.result["inquiry"]["answered"], 1)
        self.assertEqual(outcome.result["inquiry"]["queued"], 0)
        # The real board importer can read the real journal of this attempt.
        journal = inquiry_module.read_journal(credentials["resultsPath"])
        self.assertEqual(journal["entries"]["q-1"]["state"], "answered")
        answer = inquiry_module.normalize_journal_answer(journal["entries"]["q-1"])
        self.assertEqual(answer["text"], "fixture answer for q-1")
        self.assertEqual(answer["toolCallId"], "call-answer-q-1")

    def test_a_completed_finish_is_refused_with_the_question_then_retries_after_answering(self):
        context = self.context("inquiry-finish-refused", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.ask(context, "q-1", "What color is the sky?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        credentials = json.loads((context_root(context, "zcode") / "inquiry.json").read_text())
        records = self.records(credentials, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "answered"])

    def test_a_pending_question_blocks_completed_but_not_assistance(self):
        context = self.context("inquiry-live-blocked", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.ask(context, "q-1", "Should we continue?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        refusal = self.native_log(context, "finish-refusal.json")
        self.assertIsNotNone(refusal, "the fixture did not record the finish refusal")
        self.assertIn("Should we continue?", refusal)
        self.assertIn("q-1", refusal)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")
        credentials = json.loads((context_root(context, "zcode") / "inquiry.json").read_text())
        records = self.records(credentials, "q-1")
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[-1]["state"], "unavailable")
        self.assertEqual(outcome.result["inquiry"]["refused"], 1)

    def test_an_unanswered_delivered_question_settles_unavailable(self):
        context = self.context("inquiry-unanswered", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.ask(context, "q-1", "Anything else?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")
        credentials = json.loads((context_root(context, "zcode") / "inquiry.json").read_text())
        records = self.records(credentials, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "unavailable"])
        self.assertIn("ended before this inquiry was answered", records[-1]["reason"])

    def test_a_withdrawn_question_no_longer_blocks_completion(self):
        context = self.context("inquiry-discarded", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials, _ = self.ask(context, "q-1", "Forgot to ask this.")
        discarded = inquiry_module.bridge_request(credentials, "discard", {"inquiryId": "q-1"})
        self.assertTrue(discarded["ok"], discarded)
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        records = self.records(credentials, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "discarded"])

    def test_a_valid_answer_racing_a_host_discard_finishes_completed(self):
        # The root's answer receipt is minted while the question is answerable;
        # the Host withdraws it before the native result event arrives. The
        # controller must keep the discarded state, drop the late valid answer
        # instead of failing the turn as forged, and the coding work still
        # finishes completed.
        context = self.context("inquiry-discard-race", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials, _ = self.ask(context, "q-1", "Withdraw me mid-answer.")
        self.release(context)
        receipt_text = self.wait_file(context, "answer-receipt.json", timeout=30)
        self.assertIsNotNone(receipt_text, "the fixture never minted the racing answer receipt")
        self.assertTrue(receipt_text.lstrip().startswith("{"), receipt_text)
        discarded = inquiry_module.bridge_request(credentials, "discard", {"inquiryId": "q-1"})
        self.assertTrue(discarded["ok"], discarded)
        (context_root(context, "zcode") / "native-logs" / "discard-done").touch()
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        records = self.records(credentials, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "discarded"], records)
        self.assertNotIn("late but valid", json.dumps(records), "a late answer after discard must not be recorded")
        self.assertEqual(outcome.result["inquiry"]["discarded"], 1)
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)

    def test_a_question_after_the_final_receipt_becomes_honest_unavailable(self):
        context = self.context("inquiry-late", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials = self.credentials(context)
        self.release(context)
        # Wait until the fixture's finish receipt is already accepted, then ask:
        # the race must end unavailable without any new native work.
        self.wait_file(context, "finish-accepted")
        late = inquiry_module.bridge_request(credentials, "ask", {"inquiryId": "q-late", "question": "Too late?"},
                                             timeout_ms=4000)
        self.assertTrue(late["ok"], late)
        (context_root(context, "zcode") / "native-logs" / "late-asked").touch()
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        records = self.records(credentials, "q-late")
        self.assertEqual([record["state"] for record in records], ["queued", "unavailable"])
        self.assertIn("ended before this inquiry was answered", records[-1]["reason"])

    def test_a_child_relay_cannot_deliver_or_answer(self):
        context = self.context("inquiry-child", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        credentials, _ = self.ask(context, "q-1", "Who may answer?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        records = self.records(credentials, "q-1")
        delivered = [record for record in records if record["state"] == "delivered"]
        answered = [record for record in records if record["state"] == "answered"]
        self.assertEqual(len(delivered), 1, records)
        self.assertEqual(len(answered), 1, records)
        self.assertEqual(delivered[0]["toolCallId"], "call-checkpoint-root")
        self.assertEqual(answered[0]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertNotIn("child", json.dumps(answered[0]["answer"]))
        # The child's own answer text never reached the journal.
        self.assertNotIn("child answer must not count", json.dumps(records))

    def test_a_forged_answer_receipt_fails_the_whole_turn(self):
        context = self.context("inquiry-forged", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.ask(context, "q-1", "Answer honestly.")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-inquiry-receipt", outcome.to_report())
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)

    def test_cancellation_with_a_queued_question_ends_honestly(self):
        context = self.context("inquiry-live", timeout=40)
        handle = self.adapter.start(context)
        credentials, _ = self.ask(context, "q-1", "Will be cancelled.")
        time.sleep(0.2)
        self.adapter.cancel(handle)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)
        records = self.records(credentials, "q-1")
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[-1]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
