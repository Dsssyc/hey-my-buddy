"""End-to-end cooperative ZCode inquiry flows against the real runner and fixture.

Every test here drives the real adapter, controller, native fixture and the
run's own C-Two live endpoint: the test process is the holder, binds the
channel through the held control's ``live`` material and ready descriptor,
queues a Host question through the real ``request`` operation while the turn
is live, and reads the durable journal through the attempt-private
``resultsPath`` the integrated inquiry entry publishes. The fixture's root
model picks the question up through the real ``buddy_checkpoint`` tool,
answers through the real ``buddy_answer_inquiry`` tool, and only the
controller's verified root-turn evidence moves journal state. The flows cover
the finish-refusal retry, settlement races, child relays, forged receipts,
cancellation and the unchanged single native ``session/send``.

The retired raw-socket bridge — including its Host ``discard`` operation — is
gone with its backend; the two old withdrawal flows are mapped in
``docs/acceptance/adr025-step5-native-fixture-migration.md``.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase

from hey_my_buddy.blackboard.tasks import inquiry as inquiry_module
from hey_my_buddy.private_dirs import context_root


class ZcodeCheckpointFlowTests(ZcodeFixtureCase):
    def setUp(self):
        if self._testMethodName in {
            'test_private_fixture_states_keep_concurrent_inquiry_bridges_separate',
            'test_concurrent_private_attempts_keep_late_questions_bound',
        }:
            # The parent only drives pipes. Each holder configures its one
            # process-level C-Two domain in the private child fixture.
            return
        super().setUp()

    def test_private_fixture_states_keep_concurrent_inquiry_bridges_separate(self):
        from buddy.harnesses.zcode.fixtures.checkpoint_domain import CheckpointDomain

        own = CheckpointDomain(self, 'inquiry-live')
        peer = CheckpointDomain(self, 'inquiry-live')
        # Both admitted turns must still be live before any inquiry/release.
        for domain in (own, peer):
            self.assertTrue(domain.command({'op': 'live'})['live'])
        self.assertNotEqual(own.live['ownerPid'], peer.live['ownerPid'])
        for key in ('state', 'runtime', 'home', 'ipcRoot'):
            self.assertNotEqual(own.live[key], peer.live[key], key)
        self.assertNotEqual(own.live['attemptId'], peer.live['attemptId'])
        # Each controller owns one endpoint: the holder-held live materials and
        # the published ready descriptors never cross the two private runs.
        own_material, peer_material = own.live, peer.live
        self.assertNotEqual(own_material['instanceId'], peer_material['instanceId'])
        self.assertNotEqual(own_material['token'], peer_material['token'])
        self.assertNotEqual(own_material['address'], peer_material['address'])
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
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel, asked = self.ask(handle, "q-1", "Reply with exactly: checkpoint-ok")
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
                         ("goal-1", context.attempt_id, 1, handle.role_run_identity.invocation_id, "turn-1"))
        self.assertEqual(identity.input_sha256, handle.role_run_identity.input_sha256)
        # Exactly one admitted native input, and no injected command ever.
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        self.assertNotIn("v4/command", self.native_log(context, "methods.jsonl"))
        self.assertEqual(self.native_log(context, "commands.jsonl"), "")
        # The journal carries the verified ordered transitions with root evidence.
        records = self.inquiry_records(context, "q-1")
        states = [record["state"] for record in records]
        self.assertEqual(states, ["queued", "delivered", "answered"], records)
        self.assertEqual(records[0]["question"], "Reply with exactly: checkpoint-ok")
        self.assertEqual(records[0]["questionSha256"], asked.native_correlation.value["questionSha256"])
        self.assertEqual(records[1]["toolCallId"], "call-checkpoint-root")
        self.assertEqual(records[2]["answer"]["text"], "fixture answer for q-1")
        self.assertEqual(records[2]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertEqual(records[2]["answer"]["via"], "tool:buddy_answer_inquiry")
        self.assertEqual(records[2]["taskId"], "goal-1")
        self.assertEqual(records[2]["attemptId"], context.attempt_id)
        self.assertEqual(outcome.result["inquiry"]["requested"], 1)
        self.assertEqual(outcome.result["inquiry"]["answered"], 1)
        self.assertEqual(outcome.result["inquiry"]["queued"], 0)
        # The real board importer can read the real journal of this attempt.
        journal = inquiry_module.read_journal(str(self.inquiry_results_path(context)))
        self.assertEqual(journal["entries"]["q-1"]["state"], "answered")
        answer = inquiry_module.normalize_journal_answer(journal["entries"]["q-1"])
        self.assertEqual(answer["text"], "fixture answer for q-1")
        self.assertEqual(answer["toolCallId"], "call-answer-q-1")

    def test_a_completed_finish_is_refused_with_the_question_then_retries_after_answering(self):
        context = self.context("inquiry-finish-refused", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "What color is the sky?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        records = self.inquiry_records(context, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "answered"])

    def test_a_pending_question_blocks_completed_but_not_assistance(self):
        context = self.context("inquiry-live-blocked", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Should we continue?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        refusal = self.native_log(context, "finish-refusal.json")
        self.assertIsNotNone(refusal, "the fixture did not record the finish refusal")
        self.assertIn("Should we continue?", refusal)
        self.assertIn("q-1", refusal)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")
        records = self.inquiry_records(context, "q-1")
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[-1]["state"], "unavailable")
        self.assertEqual(outcome.result["inquiry"]["refused"], 1)

    def test_an_unanswered_delivered_question_settles_unavailable(self):
        context = self.context("inquiry-unanswered", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Anything else?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "assistance")
        records = self.inquiry_records(context, "q-1")
        self.assertEqual([record["state"] for record in records], ["queued", "delivered", "unavailable"])
        self.assertIn("ended before this inquiry was answered", records[-1]["reason"])

    def test_a_question_after_the_final_receipt_becomes_honest_unavailable(self):
        context = self.context("inquiry-late", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel = self.ready_channel(handle)
        self.release(context)
        # Wait until the fixture's finish receipt is already accepted, then ask:
        # the race must end unavailable without any new native work.
        self.wait_file(context, "finish-accepted")
        self.ask(handle, "q-late", "Too late?", channel)
        (context_root(context, "zcode") / "native-logs" / "late-asked").touch()
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertEqual(outcome.result["turn"]["outcome"]["disposition"], "completed")
        self.assertEqual(self.native_log(context, "methods.jsonl").split().count("session/send"), 1)
        records = self.inquiry_records(context, "q-late")
        self.assertEqual([record["state"] for record in records], ["queued", "unavailable"])
        self.assertIn("ended before this inquiry was answered", records[-1]["reason"])

    def test_a_child_relay_cannot_deliver_or_answer(self):
        context = self.context("inquiry-child", timeout=40)
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
        self.assertEqual(delivered[0]["toolCallId"], "call-checkpoint-root")
        self.assertEqual(answered[0]["answer"]["toolCallId"], "call-answer-q-1")
        self.assertNotIn("child", json.dumps(answered[0]["answer"]))
        # The child's own answer text never reached the journal.
        self.assertNotIn("child answer must not count", json.dumps(records))

    def test_concurrent_private_attempts_keep_late_questions_bound(self):
        from buddy.harnesses.zcode.fixtures.checkpoint_domain import CheckpointDomain

        attempts = [CheckpointDomain(self, 'inquiry-late') for _ in range(2)]
        # Both endpoints are alive before either finishes. A question must reach
        # its own authenticated channel and journal even with another suite live.
        for domain in attempts:
            self.assertTrue(domain.command({'op': 'live'})['live'])
        for key in ('ownerPid', 'attemptId', 'instanceId', 'token', 'address', 'ipcRoot', 'state', 'runtime', 'home'):
            self.assertNotEqual(attempts[0].live[key], attempts[1].live[key], key)
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
        handle = self.adapter.start(context)
        self.own_handle(handle)
        channel, _ = self.ask(handle, "q-1", "Will be cancelled.")
        time.sleep(0.2)
        self.adapter.cancel(handle)
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("turn", outcome.result)
        records = self.inquiry_records(context, "q-1")
        self.assertEqual(records[0]["state"], "queued")
        self.assertEqual(records[-1]["state"], "unavailable")


if __name__ == "__main__":
    unittest.main()
