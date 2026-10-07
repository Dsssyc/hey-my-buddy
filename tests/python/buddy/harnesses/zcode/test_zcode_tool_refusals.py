"""ZCode tool-refusal recovery and precise safe finish-error attribution.

The confirmed incident: an invalid finish argument (``suggestedProfileId: null``
under the old validator) made the private MCP answer ``isError`` with plain
validation text, the native wrapper surfaced that response as a tool result
marked ``success: true``, and the controller then tried to parse the error text
as a signed success receipt — failing the whole turn ``invalid-finish`` before
the still-live root could correct itself.

The contract under test here: expected argument, attention and inquiry
refusals are signed, attempt/input/tool-bound envelopes our own MCP mints; the
controller verifies the envelope and keeps the native turn alive for a
corrected same-turn retry whether the native wrapper marks the result
successful or failed. Forged, tampered, wrong-tool and wrong-identity refusals
stay fatal, and a success still requires the verified receipt, the native root
completion, the session close and the real shutdown evidence. Every test runs
the real runner, controller, native fixture and session-private MCP bridge with
private state/runtime roots and inherited Buddy credentials cleared.
"""
from __future__ import annotations

from buddy.harnesses.zcode.test_zcode import ZcodeFixtureCase


class ZcodeToolRefusalFlowTests(ZcodeFixtureCase):
    """End-to-end wrapper-success refusal flows against the real controller."""

    def test_invalid_argument_refusal_recovers_and_null_profile_means_no_suggestion(self):
        # The incident choreography: an invalid finish argument is refused with
        # a signed envelope the native wrapper reports as success: true; a child
        # relay and a foreign root deliver validly signed refusals first and are
        # ignored; the root corrects itself — an explicit null suggestedProfileId
        # is the accepted no-suggestion spelling — and the turn settles ok with
        # full provenance on the corrected call only.
        context = self.context("refusal-argument", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        request = turn["outcome"]["request"]
        self.assertIsNone(request["suggestedProfileId"], "an explicit null must pass through as no suggestion")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        self.assertEqual(turn["provenance"]["toolResultSuccess"], True)
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")
        # Exactly one admitted native input; the refusal never restarted the turn.
        from hey_my_buddy.private_dirs import context_root
        methods = (context_root(context, "zcode") / "native-logs" / "methods.jsonl").read_text()
        self.assertEqual(methods.split().count("session/send"), 1)

    def test_pending_inquiry_refusal_then_checkpoint_answer_and_finish(self):
        # A completed finish is refused with the pending question through the
        # wrapper-success envelope path; the root then checkpoints, answers and
        # retries the finish inside the same native turn. The question itself
        # travels through this holder's real C-Two channel of the live run.
        context = self.context("inquiry-refusal-wrapped", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Unblock the refusal retry?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        self.assertEqual(outcome.result["inquiry"]["answered"], 1)
        states = [record["state"] for record in self.inquiry_records(context, "q-1")]
        self.assertEqual(states, ["queued", "delivered", "answered"])

    def test_an_answer_refusal_recovers_in_the_same_turn_through_the_run(self):
        # The root answers with an unknown inquiryId first: the answer session
        # tool refuses through its signed envelope inside the native wrapper's
        # successful result, and the verified recovery — same tool, exact id —
        # runs through the whole native run path, not just the verifier unit.
        context = self.context("inquiry-answer-refused", timeout=40)
        handle = self.adapter.start(context)
        self.own_handle(handle)
        self.ask(handle, "q-1", "Recover from the wrong answer id?")
        self.release(context)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        turn = outcome.result["turn"]
        self.assertEqual(turn["outcome"]["disposition"], "completed")
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        states = [record["state"] for record in self.inquiry_records(context, "q-1")]
        self.assertEqual(states, ["queued", "delivered", "answered"])

    def test_forged_refusal_envelope_fails_the_whole_turn(self):
        context = self.context("refusal-forged", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-tool-refusal", outcome.to_report())
        self.assertIn("signature", outcome.result.get("error", ""))
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)

    def test_a_refusal_signed_for_another_tool_fails_the_finish_call(self):
        context = self.context("refusal-wrong-tool", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-tool-refusal", outcome.to_report())
        self.assertIn("different session tool", outcome.result.get("error", ""))
        self.assertNotIn("turn", outcome.result)

    def test_refusal_shaped_prose_is_fatal_not_recovered(self):
        context = self.context("refusal-malformed", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-tool-refusal", outcome.to_report())
        self.assertIn("refusal envelope", outcome.result.get("error", ""))
        self.assertNotIn("turn", outcome.result)

    def test_a_failed_marker_refusal_still_recovers_for_a_corrected_retry(self):
        # The same incident with the native wrapper marking the refused call
        # failed: the verified envelope must recover the turn exactly like the
        # successful-marker shape, and the corrected retry settles ok.
        context = self.context("refusal-argument-false", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        turn = outcome.result["turn"]
        self.assertIsNone(turn["outcome"]["request"]["suggestedProfileId"])
        self.assertEqual(turn["provenance"]["toolCallId"], "call-finish-final")
        self.assertEqual(turn["provenance"]["settlement"], "session-closed")

    def test_a_failed_marker_forged_refusal_is_fatal_not_retryable(self):
        # A tampered envelope arriving on a failed-marker result must fail the
        # turn instead of silently passing as an ordinary retryable tool error.
        context = self.context("refusal-forged-false", timeout=40)
        handle = self.adapter.start(context)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(40), "controller did not exit")
        outcome = self.adapter.collect(handle, context)
        self.assertEqual(outcome.status, "failed", outcome.to_report())
        self.assertEqual(outcome.result.get("code"), "invalid-tool-refusal", outcome.to_report())
        self.assertIn("signature", outcome.result.get("error", ""))
        self.assertNotIn("turn", outcome.result)
        self.assertTrue(outcome.shutdown_confirmed)


if __name__ == "__main__":
    import unittest
    unittest.main()
