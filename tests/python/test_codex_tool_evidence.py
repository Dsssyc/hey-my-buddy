"""Codex native tool-event projection against fake native shapes, no model calls."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from buddy.adapters.base import ExecutionContext, ReadOnlyStructuredRequest
from buddy.adapters.codex import CodexAdapter
from buddy.adapters.codex_tool_evidence import CodexToolEventProjector, control_binding
from buddy.adapters.read_only import collect
from buddy.errors import BoardError
from buddy.router import answer_schema, budget

FIXTURE = Path(__file__).parent / "fixtures" / "mock_codex.py"
SOURCE = Path(__file__).resolve().parents[2] / "src"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["profileId"],
          "properties": {"profileId": {"type": "string", "enum": ["legal"]}}}
BINDING = {"adapter": "codex", "taskId": "task-1", "attemptId": "attempt-1", "generation": 3}
ROOT = {"sessionId": "thread-1", "turnId": "turn-1"}


def frame(method: str, item: dict, *, session="thread-1", turn="turn-1") -> dict:
    """One App Server notification frame in the native params shape."""
    params = {}
    if session is not None:
        params["threadId"] = session
    if turn is not None:
        params["turnId"] = turn
    params["item"] = item
    return {"method": method, "params": params}


def raw(item: dict, **kwargs) -> dict:
    return frame("rawResponseItem/completed", item, **kwargs)


def new_projector() -> CodexToolEventProjector:
    return CodexToolEventProjector(dict(BINDING))


class CodexToolEventProjectionTests(unittest.TestCase):
    """The projector's facts over real typed and raw frame shapes."""

    def observe_root(self, projector):
        projector.observe_root("thread-1", "turn-1")

    def test_typed_execution_and_raw_native_call_join_by_actual_call_id(self):
        from buddy.tool_evidence import judge_tool_evidence
        for name in ('shell', 'shell_command', 'exec_command', 'write_stdin'):
            with self.subTest(name=name):
                projector = new_projector()
                self.observe_root(projector)
                projector.observe_notification(raw({'type': 'function_call', 'name': name,
                                                    'call_id': 'call-1', 'namespace': 'functions'}))
                projector.observe_notification(frame('item/started', {'type': 'commandExecution', 'id': 'call-1'}))
                projector.observe_notification(frame('item/completed', {'type': 'commandExecution', 'id': 'call-1'}))
                package = projector.finish(True)
                self.assertEqual(package['toolCalls'], 1)
                self.assertEqual({event['toolName'] for event in package['events']}, {name, 'commandExecution'})
                self.assertIsNone(judge_tool_evidence(package, 'review', True))

    def test_mcp_namespace_cannot_borrow_builtin_execution_classification(self):
        from buddy.tool_evidence import judge_tool_evidence
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({'type': 'function_call', 'name': 'exec_command',
                                            'namespace': 'mcp__server', 'call_id': 'mcp-1'}))
        projector.observe_notification(raw({'type': 'function_call_output', 'call_id': 'mcp-1'}))
        package = projector.finish(True)
        self.assertEqual({event['category'] for event in package['events']}, {'other'})
        self.assertEqual(judge_tool_evidence(package, 'review', True), 'router-tools-forbidden')

    def test_two_raw_names_or_conflicting_categories_are_not_execution_aliases(self):
        from buddy.tool_evidence import judge_tool_evidence
        for names in (('shell', 'exec_command'), ('shell', 'fileChange')):
            with self.subTest(names=names):
                projector = new_projector()
                self.observe_root(projector)
                for number, name in enumerate(names):
                    projector.observe_notification(raw({'type': 'function_call', 'name': name, 'call_id': 'call-1'}))
                    projector.observe_notification(raw({'type': 'function_call_output', 'call_id': 'call-1'}))
                package = projector.finish(True)
                self.assertEqual(package['toolCalls'], 1)
                self.assertEqual(judge_tool_evidence(package, 'review', True), 'router-tool-evidence-unverified')

    def test_typed_call_projects_start_and_end_over_the_real_identity(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/completed", {"type": "commandExecution", "id": "item-1", "exitCode": 0}))
        package = projector.finish(True)
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [ROOT])
        self.assertTrue(package["streamComplete"])
        self.assertFalse(package["truncated"])
        self.assertEqual(package["events"], [
            {"nativeIdentity": ROOT, "callId": "item-1", "toolName": "commandExecution",
             "category": "execute", "phase": "start"},
            {"nativeIdentity": ROOT, "callId": "item-1", "toolName": "commandExecution",
             "category": "execute", "phase": "end"},
        ])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))

    def test_raw_call_is_the_start_and_only_the_output_is_the_end(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "fileChange", "id": "item-2"}))
        projector.observe_notification(raw({"type": "function_call", "call_id": "call-1",
                                            "name": "shell", "arguments": '{"cmd":"ls"}'}))
        projector.observe_notification(raw({"type": "function_call_output", "call_id": "call-1", "output": "listing"}))
        package = projector.finish(True)
        self.assertEqual(package["toolCalls"], 2)
        # The typed fileChange item was started but never completed.
        self.assertEqual(package["unsettledToolCalls"], 1)
        raw_events = [event for event in package["events"] if event["callId"] == "call-1"]
        self.assertEqual(raw_events, [
            {"nativeIdentity": ROOT, "callId": "call-1", "toolName": "shell", "category": "execute", "phase": "start"},
            {"nativeIdentity": ROOT, "callId": "call-1", "toolName": "shell", "category": "execute", "phase": "end"},
        ])
        self.assertEqual(package["events"][0]["category"], "edit")

    def test_replayed_frames_collapse_but_two_native_calls_count_separately(self):
        projector = new_projector()
        self.observe_root(projector)
        replay = raw({"type": "function_call", "call_id": "call-1", "name": "shell", "arguments": "{}"})
        for _ in range(3):
            projector.observe_notification(replay)
        projector.observe_notification(raw({"type": "function_call_output", "call_id": "call-1", "output": "a"}))
        projector.observe_notification(raw({"type": "function_call_output", "call_id": "call-1", "output": "a"}))
        projector.observe_notification(raw({"type": "function_call", "call_id": "call-2", "name": "shell"}))
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-9"}))
        package = projector.finish(True)
        # The replayed call-1 facts collapse; call-2 and the typed item are
        # distinct native calls and each counts.
        self.assertEqual(package["toolCalls"], 3)
        self.assertEqual(package["unsettledToolCalls"], 2)
        self.assertEqual(len(package["events"]), 4)
        self.assertEqual([event["callId"] for event in package["events"]],
                         ["call-1", "call-1", "call-2", "item-9"])

    def test_fixed_classification_for_the_native_operation_types(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "web_search_call", "call_id": "w-1"}))
        projector.observe_notification(raw({"type": "local_shell_call", "call_id": "s-1",
                                            "action": {"type": "exec", "command": ["ls"]}}))
        projector.observe_notification(frame("item/started", {"type": "mcpToolCall", "id": "m-1"}))
        projector.observe_notification(frame("item/started", {"type": "dynamicToolCall", "id": "d-1"}))
        # A named call whose name is "reasoning" is still a call, classified other.
        projector.observe_notification(raw({"type": "function_call", "call_id": "r-1", "name": "reasoning"}))
        package = projector.finish(True)
        categories = {event["callId"]: event["category"] for event in package["events"]}
        self.assertEqual(categories, {"w-1": "fetch", "s-1": "execute", "m-1": "other",
                                      "d-1": "other", "r-1": "other"})
        self.assertEqual({event["toolName"] for event in package["events"] if event["callId"] == "r-1"},
                         {"reasoning"})

    def test_conversation_items_and_raw_message_frames_are_not_tool_events(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/completed", {"type": "agentMessage", "id": "a-1",
                                                                "phase": "final_answer", "text": "{}"}))
        projector.observe_notification(frame("item/started", {"type": "reasoning", "id": "a-2"}))
        projector.observe_notification(raw({"type": "message", "role": "assistant"}))
        projector.observe_notification(raw({"type": "reasoning"}))
        package = projector.finish(True)
        self.assertEqual(package["events"], [])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (0, 0))
        self.assertTrue(package["streamComplete"])

    def test_missing_turn_or_call_id_is_incomplete_without_invented_ids(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}, turn=None))
        projector.observe_notification(raw({"type": "function_call", "name": "shell"}))
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-2"}))
        package = projector.finish(True)
        self.assertEqual(package["toolCalls"], 1, "only the fully identified call counts")
        self.assertFalse(package["streamComplete"])
        unbound = [event for event in package["events"] if event["callId"] == "item-1"]
        self.assertEqual(unbound[0]["nativeIdentity"], {"sessionId": "thread-1"})
        missing = [event for event in package["events"] if event["toolName"] == "shell"]
        self.assertIsNone(missing[0]["callId"])
        self.assertEqual(missing[0]["nativeIdentity"], ROOT)

    def test_unrecognized_raw_shape_and_progress_frame_stay_incomplete(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "future_tool_thing", "id": "f-1"}))
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/updated", {"type": "commandExecution", "id": "item-1"}))
        package = projector.finish(True)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 1)
        self.assertGreaterEqual(len(package["events"]), 2)

    def test_typed_item_outside_the_known_tools_is_kept_as_other(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "todoList", "id": "t-1"}))
        projector.observe_notification(frame("item/completed", {"type": "todoList", "id": "t-1"}))
        package = projector.finish(True)
        self.assertEqual(package["events"][0]["category"], "other")
        self.assertEqual(package["toolCalls"], 1)

    def test_uncorrelated_output_keeps_only_its_own_frame_type(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "custom_tool_call_output", "call_id": "u-1", "output": "x"}))
        package = projector.finish(True)
        self.assertEqual(package["events"], [{"nativeIdentity": ROOT, "callId": "u-1",
                                              "toolName": "custom_tool_call_output", "category": "other",
                                              "phase": "end"}])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(package["unsettledToolCalls"], 0)

    def test_events_after_finish_are_late_and_never_complete_the_stream(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/completed", {"type": "commandExecution", "id": "item-1"}))
        self.assertTrue(projector.finish(True)["streamComplete"])
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-2"}))
        package = projector.finish(True)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 2)
        self.assertEqual(package["nativeIdentity"], [ROOT])

    def test_event_bound_of_128_is_reported_as_truncated(self):
        projector = new_projector()
        self.observe_root(projector)
        for number in range(129):
            projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": f"i-{number}"}))
            projector.observe_notification(frame("item/completed", {"type": "commandExecution", "id": f"i-{number}"}))
        package = projector.finish(True)
        self.assertEqual(len(package["events"]), 128)
        self.assertTrue(package["truncated"])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (129, 0))

    def test_arguments_and_output_bodies_never_enter_the_package(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "function_call", "call_id": "call-1",
                                            "name": "shell", "arguments": '{"secret":"tok- inside"}'}))
        projector.observe_notification(raw({"type": "function_call_output", "call_id": "call-1",
                                            "output": "secret output body"}))
        encoded = json.dumps(projector.finish(True))
        self.assertNotIn("tok- inside", encoded)
        self.assertNotIn("secret output body", encoded)

    def test_roots_come_only_from_receipts_and_finish_dedups_them(self):
        projector = new_projector()
        projector.observe_root("thread-1", "turn-1")
        # A correction's root turn arrives as its own receipt.
        projector.observe_root("thread-1", "turn-2")
        projector.observe_root("thread-1", "turn-1")
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-2"},
                                             turn="turn-2"))
        package = projector.finish(True)
        self.assertEqual(package["nativeIdentity"],
                         [{"sessionId": "thread-1", "turnId": "turn-1"}, {"sessionId": "thread-1", "turnId": "turn-2"}])
        self.assertEqual(package["toolCalls"], 2)

    def test_control_binding_takes_only_the_private_python_identity(self):
        self.assertEqual(control_binding({"taskId": "t", "attemptId": "a", "generation": 0}),
                         {"adapter": "codex", "taskId": "t", "attemptId": "a", "generation": 0})
        for control in ({}, {"taskId": "t", "attemptId": "a"}, {"taskId": "t", "attemptId": "a", "generation": "1"},
                        {"taskId": "t", "attemptId": "a", "generation": -1}, {"taskId": "", "attemptId": "a", "generation": 1},
                        {"taskId": 5, "attemptId": "a", "generation": 1}):
            self.assertIsNone(control_binding(control), control)
        with self.assertRaises(BoardError):
            CodexToolEventProjector({"adapter": "codex", "taskId": "t"})


class CodexReviewToolEvidenceTests(unittest.TestCase):
    """The review controller projects unified evidence over the fixture stream."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-codex-tool-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.home = self.root / "account-home"
        self.home.mkdir(mode=0o700)
        self.adapter = CodexAdapter()

    def context(self, case, *, index, task="review-task", attempt="review-attempt", generation=7):
        turn_input = {"version": 1, "taskId": task, "attemptId": attempt, "generation": generation,
                      "turnId": f"turn-{generation}", "resumeMode": "initial", "previousSessionId": None,
                      "context": {}, "executionWorkspace": {}}
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("BUDDY_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CODEX_CLI=str(FIXTURE),
                           BUDDY_CODEX_FIXTURE_STATE=str(self.root / f"fixture-{index}.json"),
                           CODEX_HOME=str(self.home), HOME=str(self.root),
                           BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                           BUDDY_DEV_SOURCE="1", BUDDY_CODEX_FIXTURE_CASE=case)
        context = ExecutionContext(task_id=task, attempt_id=attempt, generation=generation,
                                  spec={"cwd": str(self.cwd), "task": "Select", "timeoutSeconds": 8,
                                        "provider": "openai", "model": "fixture-model", "effort": "low"},
                                  directory=self.root / f"attempt-{index}", runtime={}, environment=environment,
                                  turn={"turnId": f"turn-{generation}", "input": turn_input})
        context.turn = None
        return context

    def run_review(self, case, *, index, **request_options):
        context = self.context(case, index=index)
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet",
                                            answer_schema(["legal"]), {**budget(), **request_options},
                                            capture_evidence=True)
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(30), "Codex fixture controller did not exit")
        return collect(handle), context

    def test_settled_typed_and_raw_calls_project_as_two_counted_calls(self):
        outcome, context = self.run_review("readonly-evidence", index=1)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"adapter": "codex", "taskId": context.task_id,
                                               "attemptId": context.attempt_id, "generation": context.generation})
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"}])
        self.assertTrue(evidence["streamComplete"])
        self.assertFalse(evidence["truncated"])
        self.assertEqual(evidence["toolCalls"], 2)
        self.assertEqual(evidence["unsettledToolCalls"], 0)
        self.assertEqual([(event["callId"], event["toolName"], event["category"], event["phase"])
                          for event in evidence["events"]],
                         [("item-1", "commandExecution", "execute", "start"),
                          ("item-1", "commandExecution", "execute", "end"),
                          ("call-1", "shell", "execute", "start"),
                          ("call-1", "shell", "execute", "end")])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 2)
        # The prior bounded capture keeps its raw replayed frame unchanged.
        self.assertEqual(len(outcome.result["nativeRawToolEvents"]), 3)

    def test_equivalent_typed_and_raw_names_of_one_call_stay_recorded(self):
        outcome, _context = self.run_review("readonly-conflict", index=2)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["toolCalls"], 1)
        facts = {(event["toolName"], event["phase"]) for event in evidence["events"] if event["callId"] == "call-1"}
        self.assertEqual(facts, {("commandExecution", "start"), ("commandExecution", "end"),
                                 ("shell", "start"), ("shell", "end")})
        from buddy.tool_evidence import judge_tool_evidence
        self.assertIsNone(judge_tool_evidence(evidence, 'review', True))

    def test_foreign_and_old_turn_facts_are_kept_with_their_real_identity(self):
        outcome, _context = self.run_review("readonly-foreign", index=3)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        identities = {json.dumps(event["nativeIdentity"], sort_keys=True) for event in evidence["events"]}
        self.assertEqual(identities, {json.dumps(identity, sort_keys=True) for identity in (
            {"sessionId": "thread-sub", "turnId": "sub-turn-1"},
            {"sessionId": "thread-1", "turnId": "native-turn-99"})})
        # The trusted roots are only the native turn receipt, never the events.
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"}])
        self.assertEqual(evidence["toolCalls"], 2)

    def test_stream_bound_marks_the_package_truncated(self):
        outcome, _context = self.run_review("readonly-truncated", index=4, toolCalls=500)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(len(evidence["events"]), 128)
        self.assertTrue(evidence["truncated"])
        self.assertEqual(evidence["toolCalls"], 130)
        self.assertEqual(evidence["unsettledToolCalls"], 0)
        self.assertTrue(outcome.result["nativeEvidenceTruncated"])

    def test_correction_accumulates_roots_tool_counts_and_keeps_old_turn_facts(self):
        outcome, _context = self.run_review("readonly-repair-evidence", index=5)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertEqual(outcome.result["correctionCount"], 1)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"},
                                                      {"sessionId": "thread-1", "turnId": "native-turn-2"}])
        self.assertEqual(evidence["toolCalls"], 3)
        self.assertEqual(evidence["unsettledToolCalls"], 1)
        stale = [event for event in evidence["events"] if event["callId"] == "item-stale"]
        self.assertEqual(stale, [{"nativeIdentity": {"sessionId": "thread-1", "turnId": "native-turn-1"},
                                  "callId": "item-stale", "toolName": "commandExecution",
                                  "category": "execute", "phase": "start"}])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 3)
        self.assertFalse(evidence["streamComplete"])

    def test_failure_before_any_turn_still_carries_an_incomplete_package(self):
        outcome, context = self.run_review("readonly-policy-mismatch", index=6)
        self.assertNotEqual(outcome.status, "ok")
        self.assertEqual(outcome.result["code"], "readonly-policy-unverified")
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"adapter": "codex", "taskId": context.task_id,
                                               "attemptId": context.attempt_id, "generation": context.generation})
        self.assertEqual(evidence["nativeIdentity"], [])
        self.assertFalse(evidence["streamComplete"])
        self.assertEqual(evidence["events"], [])

    def test_budget_interrupt_leaves_the_open_call_unsettled_in_the_package(self):
        outcome, _context = self.run_review("readonly-budget", index=7, toolCalls=0)
        self.assertEqual(outcome.result["code"], "readonly-budget-exhausted")
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["toolCalls"], 1)
        self.assertEqual(evidence["unsettledToolCalls"], 1)
        self.assertFalse(evidence["streamComplete"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 1)


if __name__ == "__main__":
    unittest.main()
