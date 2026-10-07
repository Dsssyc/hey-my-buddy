"""The Worker role's session-tool rules, independent of any harness.

ADR-025 decision 7 moves the governed prompt, the six-field finish contract and
the attention/inquiry completion refusals into the role layer. These tests pin
that boundary itself: the rules take the native, fully qualified tool names as
arguments, importing the role module pulls in no harness package, and the
finish boundary keeps its exact order — an invalid outcome is corrected before
an outstanding attention request is named, and a pending inquiry blocks only a
``completed`` disposition. The signed-envelope mint↔verify interplay with a
real native verifier stays in the ZCode suites; here the signatures are checked
by recomputing the one shared HMAC rule.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.buddy.harnesses.session_receipts import sign_receipt
from hey_my_buddy.buddy.roles import turn_io
from hey_my_buddy.buddy.roles import worker_services

IDENTITY = {"taskId": "task-1", "attemptId": "attempt-1", "generation": 1, "turnId": "turn-1"}
OTHER_IDENTITY = {"taskId": "task-2", "attemptId": "attempt-9", "generation": 1, "turnId": "turn-9"}


class WorkerServicesCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="buddy-worker-services-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.attention = self.root / "attention.json"
        self.journal = self.root / "inquiry.results.jsonl"
        self.configuration = {"identity": dict(IDENTITY), "inputSha256": "a" * 64, "key": "b" * 64,
                              "attentionPath": str(self.attention), "inquiryJournalPath": str(self.journal)}

    @staticmethod
    def outcome(disposition: str) -> dict:
        request = None if disposition == "completed" else {
            "summary": "help", "attempted": "tried", "neededWork": "decide",
            "expectedArtifacts": [], "acceptance": "decided"}
        return {"disposition": disposition, "summary": "fixture", "remaining": [], "decisions": [],
                "artifacts": [], "request": request}

    def call(self, name: str, arguments: object) -> dict:
        result = worker_services.call_session_tool(name, arguments, self.configuration)
        self.assertIsNotNone(result, f"{name} is a session tool of this role")
        return result

    def envelope(self, result: dict, tool: str) -> dict:
        self.assertTrue(result.get("isError"), result)
        envelope = json.loads(result["content"][0]["text"])
        self.assertEqual(envelope["kind"], "tool-refusal")
        self.assertEqual(envelope["tool"], tool)
        signature = envelope.pop("signature")
        self.assertEqual(signature, sign_receipt(envelope, self.configuration["key"]))
        return envelope

    def receipt(self, result: dict) -> dict:
        self.assertFalse(result.get("isError"), result)
        receipt = json.loads(result["content"][0]["text"])
        signature = receipt.pop("signature")
        self.assertEqual(signature, sign_receipt(receipt, self.configuration["key"]))
        return receipt

    def queue(self, inquiry_id="q-1", question="what is the deployment word?", *, identity=IDENTITY) -> None:
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": inquiry_id, "state": "queued",
                                     "question": question,
                                     "questionSha256": hashlib.sha256(question.encode()).hexdigest(),
                                     "askedAt": "2026-01-01T00:00:00Z",
                                     "taskId": identity["taskId"], "attemptId": identity["attemptId"],
                                     "generation": identity["generation"], "turnId": identity["turnId"]}) + "\n")


class GovernedPromptTests(unittest.TestCase):
    def test_scope_reminds_shared_git_ownership_without_rewriting_turn_protocol(self):
        for resume in ("initial", "reconstructed-new-session", "native-session"):
            with self.subTest(resume=resume):
                prompt = worker_services.governed_prompt("task", {"resumeMode": resume}, "finish")
                scope = prompt.split("\n\n")[0]
                self.assertIn("share Git stash, branches and tags", scope)
                self.assertIn("owner's repository", scope)
                self.assertIn("do not use git stash", scope)
                self.assertIn("create, switch, move or delete branches or tags", scope)
                self.assertIn("leave changes in the workspace", scope)
                self.assertIn("commit on the current detached HEAD", scope)

    def test_the_prompt_assembles_scope_finish_contract_hints_task_and_input_in_order(self):
        prompt = worker_services.governed_prompt("task text", {"resumeMode": "initial"}, "mcp__srv__buddy_finish_turn")
        sections = prompt.split("\n\n")
        self.assertTrue(sections[0].startswith("This is a governed Buddy root turn."))
        self.assertIn("obtain one successful receipt from mcp__srv__buddy_finish_turn", sections[1])
        self.assertIn("all six fields: disposition, summary, remaining, decisions, artifacts, request", sections[1])
        for hint in turn_io.ASSISTANCE_HINTS:
            self.assertIn(hint, sections)
        self.assertEqual(sections[-2], "task text")
        self.assertEqual(sections[-1], turn_io.canonical_json({"resumeMode": "initial"}))
        self.assertNotIn("buddy_checkpoint", prompt, "no inquiry paragraph without both inquiry tools")
        self.assertLessEqual(len(prompt.encode()), 64 * 1024)

    def test_the_inquiry_paragraph_arrives_only_with_both_fully_qualified_tool_names(self):
        finish, checkpoint, answer = ("mcp__other__buddy_finish_turn", "mcp__other__buddy_checkpoint",
                                      "mcp__other__buddy_answer_inquiry")
        neither = worker_services.governed_prompt("t", {"turn": 1}, finish)
        self.assertNotIn("Host inquiries arrive cooperatively", neither)
        half = worker_services.governed_prompt("t", {"turn": 1}, finish, checkpoint_tool=checkpoint)
        self.assertNotIn("Host inquiries arrive cooperatively", half)
        both = worker_services.governed_prompt("t", {"turn": 1}, finish, checkpoint_tool=checkpoint,
                                               answer_tool=answer)
        self.assertIn(f"call {checkpoint} at natural work milestones", both)
        self.assertIn(f"Answer each listed question with {answer}", both)
        self.assertIn("never on a timer", both)
        # The names are plain arguments: arbitrary identifiers flow through
        # verbatim, and no harness name appears anywhere in the role's own text
        # ("review"/"reviewer" are ordinary words of the frozen hints).
        for harness_word in ("zcode", "ZCode", "codex", "Claude", "claude", "dsh", "DSH"):
            self.assertNotIn(harness_word, both.split("task text")[0])


class RoleBoundaryTests(unittest.TestCase):
    def test_importing_the_role_module_imports_no_harness_package(self):
        # This file lives in <checkout>/tests/python/buddy/roles/, so the
        # package source it must exercise is the checkout's own src/, four
        # parents up — not a path that only exists under this test runner.
        source = Path(__file__).resolve().parents[4] / "src"
        environment = {"PYTHONPATH": str(source)}
        for name in ("PATH", "TMPDIR", "BUDDY_CHECKS_TMPDIR"):
            if name in os.environ:
                environment[name] = os.environ[name]
        with tempfile.TemporaryDirectory(prefix="buddy-role-boundary-") as outside:
            # A cwd outside the checkout plus the module's own reported file
            # prove the import resolved from this source, not from the cwd or
            # an installed copy the parent interpreter happened to carry.
            probe = subprocess.run([sys.executable, "-c",
                                    "import sys, hey_my_buddy.buddy.roles.worker_services as w;"
                                    "print(w.__file__);"
                                    "print(any('harnesses.zcode' in m or 'harnesses.codex' in m"
                                    " or 'harnesses.claude' in m or 'harnesses.dsh' in m for m in sys.modules));"
                                    "print(callable(w.call_session_tool), callable(w.governed_prompt))"],
                                   capture_output=True, text=True, env=environment, cwd=outside)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        module_file, harness_free, callables = probe.stdout.splitlines()
        self.assertTrue(Path(module_file).resolve().is_relative_to(source),
                        f"worker_services imported from {module_file}, not {source}")
        self.assertEqual(harness_free, "False")
        self.assertEqual(callables, "True True")

    def test_the_three_session_tools_carry_the_six_field_contract(self):
        tools = {tool["name"]: tool for tool in worker_services.session_tools()}
        self.assertEqual(set(tools), {"buddy_checkpoint", "buddy_answer_inquiry", "buddy_finish_turn"})
        schema = tools["buddy_finish_turn"]["inputSchema"]
        self.assertEqual(schema["required"], ["disposition", "summary", "remaining", "decisions",
                                              "artifacts", "request"])
        self.assertEqual(schema["properties"]["disposition"]["enum"], ["completed", "assistance", "attention"])
        self.assertIn("does not dispatch other tasks", tools["buddy_finish_turn"]["description"])


class FinishBoundaryTests(WorkerServicesCase):
    def test_an_accepted_finish_mints_a_receipt_bound_to_this_attempt_and_input(self):
        receipt = self.receipt(self.call("buddy_finish_turn", self.outcome("completed")))
        self.assertEqual(receipt["version"], 1)
        self.assertEqual(receipt["identity"], IDENTITY)
        self.assertEqual(receipt["inputSha256"], "a" * 64)
        self.assertEqual(receipt["outcome"]["disposition"], "completed")
        self.assertEqual(len(receipt["receiptId"]), 32)

    def test_an_invalid_outcome_is_corrected_before_any_host_condition_is_named(self):
        self.attention.write_text(json.dumps({"version": 1, "requests": [{"method": "interaction/x"}]}))
        self.queue()
        envelope = self.envelope(self.call("buddy_finish_turn", {"disposition": "completed"}), "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "invalid-arguments")
        self.assertIn("the outcome must contain exactly the current outcome fields", envelope["detail"])
        self.assertEqual(turn_io.validate_outcome(self.outcome("completed")), None)

    def test_an_outstanding_native_request_blocks_only_completed(self):
        self.attention.write_text(json.dumps({"version": 1, "requests": [{"method": "interaction/x"}]}))
        envelope = self.envelope(self.call("buddy_finish_turn", self.outcome("completed")), "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "attention-outstanding")
        self.assertIn("attention", envelope["detail"])
        for disposition in ("assistance", "attention"):
            with self.subTest(disposition=disposition):
                receipt = self.receipt(self.call("buddy_finish_turn", self.outcome(disposition)))
                self.assertEqual(receipt["outcome"]["disposition"], disposition)

    def test_a_pending_inquiry_blocks_only_completed_and_names_the_question(self):
        self.queue()
        envelope = self.envelope(self.call("buddy_finish_turn", self.outcome("completed")), "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "inquiry-pending")
        self.assertIn("[q-1] what is the deployment word?", envelope["detail"])
        self.assertIn("Call buddy_checkpoint", envelope["detail"])
        receipt = self.receipt(self.call("buddy_finish_turn", self.outcome("assistance")))
        self.assertEqual(receipt["outcome"]["disposition"], "assistance")

    def test_attention_is_named_before_a_pending_inquiry_for_a_completed_outcome(self):
        # Both Host conditions outstanding: the boundary order names the native
        # attention request first, exactly as the moved contract requires.
        self.attention.write_text(json.dumps({"version": 1, "requests": [{"method": "interaction/x"}]}))
        self.queue()
        envelope = self.envelope(self.call("buddy_finish_turn", self.outcome("completed")), "buddy_finish_turn")
        self.assertEqual(envelope["reason"], "attention-outstanding")

    def test_a_withdrawn_question_no_longer_blocks_completion(self):
        self.queue()
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-1", "state": "discarded", **IDENTITY}) + "\n")
        self.receipt(self.call("buddy_finish_turn", self.outcome("completed")))


class InquiryToolTests(WorkerServicesCase):
    def test_a_checkpoint_lists_only_this_attempts_still_answerable_questions(self):
        self.queue("q-mine", "mine?")
        self.queue("q-foreign", "other attempt?", identity=OTHER_IDENTITY)
        self.queue("q-answered", "done?")
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-answered", "state": "answered",
                                     "question": "done?", "questionSha256": "c" * 64, **IDENTITY}) + "\n")
        receipt = self.receipt(self.call("buddy_checkpoint", {}))
        self.assertEqual(receipt["kind"], "inquiry-checkpoint")
        self.assertEqual([item["inquiryId"] for item in receipt["inquiries"]], ["q-mine"])
        self.assertEqual(receipt["morePending"], 0)

    def test_answer_rules_are_bounded_and_bound_to_the_committed_question(self):
        self.queue()
        accepted = self.receipt(self.call("buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "deploy-ok"}))
        self.assertEqual(accepted["kind"], "inquiry-answer")
        self.assertEqual(accepted["questionSha256"],
                         hashlib.sha256("what is the deployment word?".encode()).hexdigest())
        for arguments, reason in (
                ({"inquiryId": "q-1"}, "invalid-arguments"),
                ({"inquiryId": "q-1", "answer": "  "}, "invalid-arguments"),
                ({"inquiryId": "q-1", "answer": "x" * 4001}, "invalid-arguments"),
                ({"inquiryId": "q-missing", "answer": "fine"}, "unknown-inquiry"),
                ({"inquiryId": "i" * 129, "answer": "fine"}, "invalid-arguments")):
            with self.subTest(reason=reason):
                envelope = self.envelope(self.call("buddy_answer_inquiry", arguments), "buddy_answer_inquiry")
                self.assertEqual(envelope["reason"], reason)
        with self.journal.open("a") as stream:
            stream.write(json.dumps({"version": 1, "inquiryId": "q-1", "state": "answered",
                                     "question": "what is the deployment word?",
                                     "questionSha256": hashlib.sha256(
                                         "what is the deployment word?".encode()).hexdigest(), **IDENTITY}) + "\n")
        replaced = self.envelope(self.call("buddy_answer_inquiry", {"inquiryId": "q-1", "answer": "another"}),
                                 "buddy_answer_inquiry")
        self.assertEqual(replaced["reason"], "inquiry-state")
        self.assertIn("cannot be replaced", replaced["detail"])

    def test_without_a_journal_the_channel_is_absent_for_every_tool(self):
        configuration = {key: value for key, value in self.configuration.items() if key != "inquiryJournalPath"}
        self.assertIsNone(worker_services.pending_inquiries(configuration))
        for name, arguments in (("buddy_checkpoint", {}), ("buddy_answer_inquiry", {"inquiryId": "q", "answer": "x"})):
            with self.subTest(tool=name):
                result = worker_services.call_session_tool(name, arguments, configuration)
                envelope = self.envelope(result, name)
                self.assertEqual(envelope["reason"], "inquiry-channel-absent")

    def test_an_attention_record_is_counted_but_never_fatal(self):
        self.assertEqual(worker_services.attention_requests(self.configuration), 0)
        self.attention.write_text(json.dumps({"version": 1, "requests": [
            {"kind": "unsupported-native-request", "method": "interaction/requestPermission"}]}))
        self.assertEqual(worker_services.attention_requests(self.configuration), 1)
        self.attention.write_text("not json")
        self.assertEqual(worker_services.attention_requests(self.configuration), 0)
        oversize = self.root / "oversize.json"
        oversize.write_bytes(b"0" * (worker_services.MAX_ATTENTION_BYTES + 1))
        self.assertEqual(worker_services.attention_requests({**self.configuration, "attentionPath": str(oversize)}), 1)


if __name__ == "__main__":
    unittest.main()
