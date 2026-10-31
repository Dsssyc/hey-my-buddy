"""Native realtime inquiry flows through the adapter, C-Two and journal.

The fixture accepts guide commands, emits root-bound steer events and answers
through the real session tool. Tests cover late answers, held withdrawal,
unanswered settlement, concurrent private owners, forged/child evidence and
cancellation, while the original session/send remains single.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest import mock

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase
from buddy.harnesses.session_record_support import updates
from hey_my_buddy.buddy.harnesses.zcode.inquiry import NativeInquiry
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError

from hey_my_buddy.protocol.inquiry_journal import read_inquiry_journal, merged_inquiry_records, normalize_inquiry_record
from hey_my_buddy.private_dirs import context_root
from hey_my_buddy.paths import native_path


class ZcodeRealtimeFlowTests(ZcodeFixtureCase):
    def setUp(self):
        if self._testMethodName in {
            'test_private_fixture_states_keep_concurrent_inquiry_bridges_separate',
            'test_concurrent_private_attempts_keep_late_questions_bound',
        }:
            # The parent only drives pipes. Each holder configures its one
            # process-level C-Two domain in the private child fixture.
            return
        super().setUp()

    def assert_private_ipc(self, first, second):
        if os.name == 'nt':
            self.assertIsNone(first['ipcRoot'])
            self.assertIsNone(second['ipcRoot'])
            self.assertTrue(first['ipcNamespace'])
            self.assertEqual(first['ipcNamespace'], second['ipcNamespace'])
        else:
            self.assertNotEqual(first['ipcRoot'], second['ipcRoot'])

    def test_private_fixture_states_keep_concurrent_inquiry_bridges_separate(self):
        from buddy.harnesses.zcode.fixtures.inquiry_domain import InquiryDomain

        own = InquiryDomain(self, 'inquiry-live')
        peer = InquiryDomain(self, 'inquiry-live')
        # Both admitted turns must still be live before any inquiry/release.
        for domain in (own, peer):
            self.assertTrue(domain.command({'op': 'live'})['live'])
        self.assertNotEqual(own.live['ownerPid'], peer.live['ownerPid'])
        for key in ('state', 'runtime', 'home'):
            self.assertNotEqual(own.live[key], peer.live[key], key)
        self.assert_private_ipc(own.live, peer.live)
        self.assertNotEqual(own.live['attemptId'], peer.live['attemptId'])
        self.assertNotEqual(own.live['controllerPid'], peer.live['controllerPid'])
        own_receipt = own.command({'op': 'ask', 'id': 'own-question', 'question': 'Answer this private fixture.'})
        peer_receipt = peer.command({'op': 'ask', 'id': 'peer-question', 'question': 'Answer the other private fixture.'})
        for domain in (own, peer):
            domain.command({'op': 'release'})
        results = [domain.command({'op': 'collect', 'ids': ['own-question', 'peer-question']})
                   for domain in (own, peer)]
        for result in results:
            self.assertEqual(result['status'], 'ok', result['report'])
            self.assertTrue(result['shutdown'])
            self.assertTrue(result['controllerStopped'])
            self.assertTrue(result['nativeStopped'])
            self.assertEqual(result['result']['turn']['outcome']['disposition'], 'completed')
            self.assertEqual(result['methods'].split().count('session/send'), 1)
        own_records, peer_records = results[0]['records'], results[1]['records']
        self.assertNotIn('peer-question', json.dumps(own_records['own-question']))
        self.assertEqual(own_records['peer-question'], [])
        self.assertEqual(peer_records['own-question'], [])
        self.assertEqual([record['state'] for record in own_records['own-question']],
                         ['queued', 'delivered', 'answered'])
        self.assertEqual(own_records['own-question'][0]['attemptId'], own.live['attemptId'])
        self.assertEqual(peer_records['peer-question'][0]['attemptId'], peer.live['attemptId'])
        for domain, result, inquiry_id, receipt in (
            (own, results[0], 'own-question', own_receipt),
            (peer, results[1], 'peer-question', peer_receipt),
        ):
            records = result['records'][inquiry_id]
            self.assertEqual([record['state'] for record in records], ['queued', 'delivered', 'answered'])
            self.assertEqual(records[0]['questionSha256'], receipt['correlation']['questionSha256'])
            self.assertEqual(records[2]['answer']['text'], f'fixture answer for {inquiry_id}')
            self.assertTrue(all(record['attemptId'] == domain.live['attemptId'] for record in records))
            self.assertEqual([json.loads(line) for line in result['journal'].splitlines()], records)

    def test_one_send_delivers_and_answers_a_question_and_finishes_completed(self):
        context = self.context("inquiry-live", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel, asked = self.ask(handle, "q-1", "Reply with exactly: inquiry-ok")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")
        # The bound channel carries the run's complete six-component identity.
        identity = channel.identity
        self.assertEqual((identity.task_id, identity.attempt_id, identity.generation,
                          identity.invocation_id, identity.turn_id),
                         ("micro-task-1", context.attempt_id, 1, handle.role_run_identity.invocation_id, "turn-1"))
        self.assertEqual(identity.input_sha256, handle.role_run_identity.input_sha256)
        # One original input; a guide command carries the Host question.
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        commands = [json.loads(line) for line in self.native_log(context, "commands.jsonl").splitlines()]
        self.assertEqual([c["type"] for c in commands], ["setAutoDrain", "sendText"])
        inputs = [event for event in updates(native_path(context.session_record_path))
                  if event["sessionUpdate"] == "user_message_chunk"]
        self.assertEqual(len(inputs), 2, "the Host input must appear once in the root session record")
        self.assertIn('"inquiryId": "q-1"', inputs[1]["content"]["text"])
        self.assertIn("Reply with exactly: inquiry-ok", inputs[1]["content"]["text"])
        self.assertEqual(inputs[1]["_meta"]["nativeItemId"], commands[1]["commandId"])
        # The journal carries the verified ordered transitions with root evidence.
        records = self.inquiry_records(context, "q-1")
        states = [record["state"] for record in records]
        self.assertEqual(states, ["queued", "delivered", "answered"], records)
        self.assertEqual(records[0]["question"], "Reply with exactly: inquiry-ok")
        self.assertEqual(records[0]["questionSha256"], asked.native_correlation.value["questionSha256"])
        self.assertEqual(records[1]["nativeCorrelation"]["method"], "v4/command")
        self.assertEqual(records[2]["answer"]["text"], "fixture answer for q-1")
        self.assertEqual(records[2]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertEqual(records[2]["answer"]["via"], "tool:hey_my_buddy_answer_inquiry")
        self.assertEqual(records[2]["taskId"], "micro-task-1")
        self.assertEqual(records[2]["attemptId"], context.attempt_id)
        self.assertEqual(outcome.result["inquiry"]["requested"], 1)
        self.assertEqual(outcome.result["inquiry"]["answered"], 1)
        self.assertEqual(outcome.result["inquiry"]["queued"], 0)
        # The real board importer can read the real journal of this attempt.
        journal = read_inquiry_journal({key: records[0][key] for key in ("taskId", "attemptId", "generation", "turnId")}, str(self.inquiry_results_path(context)))
        self.assertEqual(merged_inquiry_records(journal["records"])["q-1"]["state"], "answered")
        answer = normalize_inquiry_record(merged_inquiry_records(journal["records"])["q-1"])["answer"]
        self.assertEqual(answer["text"], "fixture answer for q-1")
        self.assertEqual(answer["toolCallId"], "call-answer-q-1")

    def test_a_late_answer_after_finish_preserves_the_completed_result(self):
        context = self.context("inquiry-late-answer", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "What color is the sky?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual(outcome.result["turn"]["provenance"]["toolCallId"], "call-finish-final")
        self.assertEqual(self.inquiry_records(context, "q-1")[-1]["state"], "answered")

    def test_rejected_queue_preparation_keeps_work_and_settles_the_unsent_question(self):
        context = self.context("inquiry-hold-rejected", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "This question cannot enter the native queue.")
        self.release(context)
        self.assertIsNotNone(handle.wait(40))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual(outcome.result["inquiry"]["error"], "native-queue-hold-failed")
        self.assertEqual([r["state"] for r in self.inquiry_records(context, "q-1")], ["queued", "unavailable"])
        commands = [json.loads(line) for line in self.native_log(context, "commands.jsonl").splitlines()]
        self.assertEqual([c["type"] for c in commands], ["setAutoDrain"])
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        self.assertTrue(outcome.shutdown_confirmed)


    def test_a_held_fallback_is_deleted_without_blocking_completed(self):
        context = self.context("inquiry-fallback", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Should we continue?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual([r["state"] for r in self.inquiry_records(context, "q-1")], ["queued", "unavailable"])
        commands = [json.loads(line) for line in self.native_log(context, "commands.jsonl").splitlines()]
        self.assertEqual([c["type"] for c in commands], ["setAutoDrain", "sendText", "deleteQueueItem"])
        self.assertFalse(commands[0]["payload"]["autoDrain"])
        self.assertTrue(outcome.result["nativeSession"]["resumable"])

    def test_a_failed_withdrawal_keeps_work_and_removes_an_existing_resume_binding(self):
        first = self.context("ok", timeout=40)
        _, finished = self.execute(first)
        previous = finished.result["turn"]["sessionId"]
        context = self.context("inquiry-delete-stale", index=2, previous=previous, timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "This item will survive the delete ack.")
        self.release(context)
        self.assertIsNotNone(handle.wait(40))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertFalse(outcome.result["nativeSession"]["resumable"])
        self.assertFalse(outcome.result["nativeSession"]["bindingPresent"])
        self.assertTrue(outcome.shutdown_confirmed)

    def test_a_native_failure_observed_during_cleanup_still_stops_the_owned_process(self):
        original = NativeInquiry.snapshot
        def snapshot(driver):
            if driver.evidence.completed_ordinal:
                raise NativeError("wrong-native-turn", "second root during cleanup")
            return original(driver)
        context = self.context("inquiry-fallback", timeout=40)
        self.adapter.prepare(context)
        with mock.patch.object(NativeInquiry, "snapshot", snapshot):
            handle = self.adapter.start(context)
            self.own_handle(handle)
            self.ask(handle, "q-1", "This queue read encounters a bad native root.")
            self.release(context)
            self.assertIsNotNone(handle.wait(40))
            outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result["code"], "wrong-native-turn")
        self.assertTrue(outcome.shutdown_confirmed)


    def test_an_unanswered_delivered_question_settles_unavailable(self):
        context = self.context("inquiry-unanswered", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Anything else?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        records = self.inquiry_records(context, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "unavailable"])
        self.assertNotIn("reason", records[-1])

    def test_a_question_after_the_final_receipt_becomes_honest_unavailable(self):
        context = self.context("inquiry-late", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel = self.ready_channel(handle)
        self.release(context)
        # Wait until the fixture's finish receipt is already accepted, then ask:
        # the race must end unavailable without any new native work.
        self.wait_file(context, "finish-accepted")
        self.ask(handle, "q-late", "Too late?", channel)
        next(native_path(context_root(context, "zcode")).glob("run-*/native-logs")).joinpath("late-asked").touch()
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        records = self.inquiry_records(context, "q-late")
        self.assertEqual([record["state"] for record in records], ["queued", "unavailable"])
        self.assertNotIn("reason", records[-1])

    def test_a_child_relay_cannot_deliver_or_answer(self):
        context = self.context("inquiry-child", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Who may answer?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        records = self.inquiry_records(context, "q-1")
        delivered = [record for record in records if record["state"] == "delivered"]
        answered = [record for record in records if record["state"] == "answered"]
        self.assertEqual(len(delivered), 1, records)
        self.assertEqual(len(answered), 1, records)
        self.assertEqual(delivered[0]["nativeCorrelation"]["method"], "v4/command")
        self.assertEqual(answered[0]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertNotIn("child", json.dumps(answered[0]["answer"]))
        # The child's own answer text never reached the journal.
        self.assertNotIn("child answer must not count", json.dumps(records))

    def test_concurrent_private_attempts_keep_late_questions_bound(self):
        from buddy.harnesses.zcode.fixtures.inquiry_domain import InquiryDomain

        attempts = [InquiryDomain(self, 'inquiry-late') for _ in range(2)]
        # Both endpoints are alive before either finishes. A question must reach
        # its own authenticated channel and journal even with another suite live.
        for domain in attempts:
            self.assertTrue(domain.command({'op': 'live'})['live'])
        for key in ('ownerPid', 'attemptId', 'controllerPid', 'state', 'runtime', 'home'):
            self.assertNotEqual(attempts[0].live[key], attempts[1].live[key], key)
        self.assert_private_ipc(attempts[0].live, attempts[1].live)
        for domain in attempts:
            domain.command({'op': 'release'})
        for domain in attempts:
            domain.command({'op': 'finish-accepted'})
        receipts = [domain.command({'op': 'ask', 'id': 'q-late', 'question': 'Too late?'})
                    for domain in attempts]
        for domain in attempts:
            domain.command({'op': 'late-asked'})
        for domain, asked in zip(attempts, receipts):
            outcome = domain.command({'op': 'collect', 'ids': ['q-late']})
            self.assertEqual(outcome['status'], 'ok', outcome['report'])
            self.assertTrue(outcome['shutdown'])
            self.assertTrue(outcome['controllerStopped'])
            self.assertTrue(outcome['nativeStopped'])
            self.assertEqual(outcome['result']['turn']['outcome']['disposition'], 'completed')
            records = outcome['records']['q-late']
            self.assertEqual([record["state"] for record in records], ["queued", "unavailable"])
            self.assertEqual(records[0]['questionSha256'], asked['correlation']['questionSha256'])
            self.assertEqual(records[0]['attemptId'], domain.live['attemptId'])
            self.assertEqual(outcome['methods'].split().count('session/send'), 1)
            self.assertTrue(all(record['attemptId'] == domain.live['attemptId'] for record in records))
            self.assertEqual([json.loads(line) for line in outcome['journal'].splitlines()], records)

    def test_a_forged_answer_receipt_fails_the_whole_turn(self):
        context = self.context("inquiry-forged", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Answer honestly.")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-inquiry-receipt", outcome.to_report())
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)

    def test_cancellation_with_a_queued_question_ends_honestly(self):
        context = self.context("inquiry-live", timeout=40)
        self.adapter.prepare(context)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel, _ = self.ask(handle, "q-1", "Will be cancelled.")
        time.sleep(0.2)
        self.adapter.cancel(handle)
        self.assertIsNotNone(handle.wait(15))
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)
        records = self.inquiry_records(context, "q-1")
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[-1]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
