"""Codex native tool-event projection against fake native shapes, no model calls."""
from __future__ import annotations

import json
import os
import tempfile
from temporary import TemporaryDirectory
import unittest
from pathlib import Path
from buddy.harnesses.fixture_selection import select_fixture

from hey_my_buddy.paths import native_path
from hey_my_buddy.buddy.harnesses.base import ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.codex.adapter import CodexAdapter
from hey_my_buddy.buddy.harnesses.codex.tool_evidence import CodexToolEventProjector
from hey_my_buddy.buddy.roles.controller import ReviewPreparation, start_router_preparation
from hey_my_buddy.buddy.roles.structured_call import collect
from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.routing.router import answer_schema, budget
from hey_my_buddy.protocol import tool_evidence
from buddy.harnesses.codex.fixtures.protocol_items import PROTOCOL_ITEMS, FUTURE_ITEMS, item_frames

FIXTURE = Path(__file__).parent / "fixtures" / "mock_codex.py"
SOURCE = Path(__file__).resolve().parents[5] / "src"
SCHEMA = {"type": "object", "additionalProperties": False, "required": ["buddyId"],
          "properties": {"buddyId": {"type": "string", "enum": ["legal"]}}}
BINDING = {"harness": "codex", "taskId": "task-1", "attemptId": "attempt-1", "generation": 3}
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
    return CodexToolEventProjector(dict(BINDING), state_dir=Path(os.environ["HMB_STATE_DIR"]))


def calls(package, *, state_dir: Path):
    return list(tool_evidence.read_tool_calls(package, state_dir=state_dir))


class CodexToolEventProjectionTests(unittest.TestCase):
    """The projector's facts over real typed and raw frame shapes."""

    def test_collector_uses_its_explicit_state_root(self):
        with TemporaryDirectory(prefix="codex-evidence-root-") as directory:
            state_dir = Path(directory) / "state"
            self.assertNotEqual(state_dir, Path(os.environ["HMB_STATE_DIR"]))
            projector = CodexToolEventProjector(dict(BINDING), state_dir=state_dir)
            projector.observe_root("thread-1", "turn-1")
            projector.observe_notification(raw({"type": "function_call", "call_id": "call-1", "name": "shell"}))
            package = projector.finish(True)
            self.assertEqual([call["callId"] for call in tool_evidence.read_tool_calls(
                package, state_dir=state_dir)], ["call-1"])

    def observe_root(self, projector):
        projector.observe_root("thread-1", "turn-1")

    def test_protocol_variants_and_future_items_in_both_router_modes(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        for name, _carrier, _fields, expected_calls, phases, complete in PROTOCOL_ITEMS + FUTURE_ITEMS:
            with self.subTest(name=name):
                projector = new_projector()
                self.observe_root(projector)
                for method, item in item_frames(name):
                    projector.observe_notification(frame(method, item))
                package = projector.finish(True)
                self.assertEqual(package["toolCalls"], expected_calls)
                self.assertEqual(tuple(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))[0]["phases"]) if phases else (),
                                 tuple(sorted(phase for phase in phases if phase is not None)))
                self.assertEqual(package["streamComplete"], complete)
                self.assertEqual(package["unsettledToolCalls"], int(phases == ("start",)))
                self.assertEqual({category for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])) for category in call["categories"]},
                                 {"other"} if phases else set())
                for permission in ("none", "read"):
                    self.assertEqual(judge_tool_evidence(package, permission, state_dir=Path(os.environ["HMB_STATE_DIR"])),
                                     "router-tool-evidence-unverified" if not complete
                                     else "router-tools-forbidden" if phases else None)

    def test_typed_function_output_ends_a_correlated_request_without_a_new_start(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "function_call", "call_id": "item-1", "name": "shell"}))
        for method, item in item_frames("functionCallOutput"):
            projector.observe_notification(frame(method, item))
        package = projector.finish(True)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertEqual([(call["callId"], call["categories"], call["phases"]) for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))],
                         [("item-1", ["execute"], ["end", "start"])])
        self.assertIsNone(judge_tool_evidence(package, "read", state_dir=Path(os.environ["HMB_STATE_DIR"])))
        self.assertEqual(judge_tool_evidence(package, "none", state_dir=Path(os.environ["HMB_STATE_DIR"])), "router-tools-forbidden")

    def test_raw_tool_search_result_settles_only_its_actual_call(self):
        projector = new_projector()
        self.observe_root(projector)
        for name in ("tool_search_call", "tool_search_output"):
            for method, item in item_frames(name):
                projector.observe_notification(frame(method, item))
        package = projector.finish(True)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertEqual([(call["callId"], call["categories"], call["phases"]) for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))],
                         [("item-1", ["other"], ["end", "start"])])

    def test_typed_execution_and_raw_native_call_join_by_actual_call_id(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
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
                self.assertEqual([(call['categories'], call['phases'], call['consistent']) for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))],
                                 [(['execute'], ['end', 'start'], True)])
                self.assertIsNone(judge_tool_evidence(package, 'read', state_dir=Path(os.environ["HMB_STATE_DIR"])))

    def test_mcp_namespace_cannot_borrow_builtin_execution_classification(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({'type': 'function_call', 'name': 'exec_command',
                                            'namespace': 'mcp__server', 'call_id': 'mcp-1'}))
        projector.observe_notification(raw({'type': 'function_call_output', 'call_id': 'mcp-1'}))
        package = projector.finish(True)
        self.assertEqual([call['categories'] for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))], [['other']])
        self.assertEqual(judge_tool_evidence(package, 'read', state_dir=Path(os.environ["HMB_STATE_DIR"])), 'router-tools-forbidden')

    def test_two_raw_names_or_conflicting_categories_are_not_execution_aliases(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        for names in (('shell', 'exec_command'), ('shell', 'fileChange')):
            with self.subTest(names=names):
                projector = new_projector()
                self.observe_root(projector)
                for number, name in enumerate(names):
                    projector.observe_notification(raw({'type': 'function_call', 'name': name, 'call_id': 'call-1'}))
                    projector.observe_notification(raw({'type': 'function_call_output', 'call_id': 'call-1'}))
                package = projector.finish(True)
                self.assertEqual(package['toolCalls'], 1)
                self.assertFalse(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))[0]['consistent'])
                self.assertEqual(judge_tool_evidence(package, 'read', state_dir=Path(os.environ["HMB_STATE_DIR"])), 'router-tool-evidence-unverified')

    def test_typed_call_projects_start_and_end_over_the_real_identity(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/completed", {"type": "commandExecution", "id": "item-1", "exitCode": 0}))
        package = projector.finish(True)
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [ROOT])
        self.assertTrue(package["streamComplete"])
        self.assertEqual(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])), [{
            "nativeIdentity": ROOT, "callId": "item-1", "categories": ["execute"],
            "phases": ["end", "start"], "facts": [{"toolName": "commandExecution", "category": "execute"}],
            "consistent": True, "endBeforeStart": False,
            "incomplete": False, "malformed": False, "belongsToRoot": True,
        }])
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
        raw_calls = [call for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])) if call["callId"] == "call-1"]
        self.assertEqual([(call["categories"], call["phases"]) for call in raw_calls],
                         [(["execute"], ["end", "start"])])
        self.assertEqual(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))[0]["categories"], ["edit"])

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
        self.assertEqual(len(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))), 3)
        self.assertEqual([call["callId"] for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))],
                         ["call-1", "call-2", "item-9"])
        self.assertEqual(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))[0]["phases"], ["end", "start"])

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
        categories = {call["callId"]: call["categories"] for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))}
        self.assertEqual(categories, {"w-1": ["fetch"], "s-1": ["execute"], "m-1": ["other"],
                                      "d-1": ["other"], "r-1": ["other"]})
        self.assertEqual(package["toolCalls"], 5)

    def test_conversation_items_and_raw_message_frames_are_not_tool_events(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/completed", {"type": "agentMessage", "id": "a-1",
                                                                "phase": "final_answer", "text": "{}"}))
        projector.observe_notification(frame("item/started", {"type": "reasoning", "id": "a-2"}))
        projector.observe_notification(raw({"type": "message", "role": "assistant"}))
        projector.observe_notification(raw({"type": "reasoning"}))
        package = projector.finish(True)
        self.assertEqual(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])), [])
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
        unbound = [call for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])) if call["callId"] == "item-1"]
        self.assertEqual(unbound[0]["nativeIdentity"], {"sessionId": "thread-1"})
        missing = [call for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"])) if call["callId"] is None]
        self.assertIsNone(missing[0]["callId"])
        self.assertEqual(missing[0]["nativeIdentity"], ROOT)
        self.assertTrue(missing[0]["incomplete"])

    def test_unrecognized_raw_shape_and_progress_frame_stay_incomplete(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "future_tool_thing", "id": "f-1"}))
        projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": "item-1"}))
        projector.observe_notification(frame("item/updated", {"type": "commandExecution", "id": "item-1"}))
        package = projector.finish(True)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 1)
        self.assertGreaterEqual(len(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))), 2)

    def test_typed_item_outside_the_known_tools_is_kept_as_other(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(frame("item/started", {"type": "todoList", "id": "t-1"}))
        projector.observe_notification(frame("item/completed", {"type": "todoList", "id": "t-1"}))
        package = projector.finish(True)
        self.assertEqual(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))[0]["categories"], ["other"])
        self.assertEqual(package["toolCalls"], 1)

    def test_uncorrelated_output_keeps_only_its_own_frame_type(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "custom_tool_call_output", "call_id": "u-1", "output": "x"}))
        package = projector.finish(True)
        self.assertEqual([(call["nativeIdentity"], call["callId"], call["categories"], call["phases"],
                           call["endBeforeStart"]) for call in calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))],
                         [(ROOT, "u-1", ["other"], ["end"], True)])
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

    def test_more_than_128_settled_calls_have_bounded_rows_and_remain_judgeable(self):
        projector = new_projector()
        self.observe_root(projector)
        for number in range(129):
            projector.observe_notification(frame("item/started", {"type": "commandExecution", "id": f"i-{number}"}))
            projector.observe_notification(frame("item/completed", {"type": "commandExecution", "id": f"i-{number}"}))
        package = projector.finish(True)
        self.assertEqual(len(calls(package, state_dir=Path(os.environ["HMB_STATE_DIR"]))), 129)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (129, 0))
        self.assertLess(package["calls"]["bytes"], 129 * 512)
        self.assertLess(len(json.dumps(package)), 2048)
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=Path(os.environ["HMB_STATE_DIR"])))

    def test_arguments_and_output_bodies_never_enter_the_package(self):
        projector = new_projector()
        self.observe_root(projector)
        projector.observe_notification(raw({"type": "function_call", "call_id": "call-1",
                                            "name": "shell", "arguments": '{"secret":"tok- inside"}'}))
        projector.observe_notification(raw({"type": "function_call_output", "call_id": "call-1",
                                            "output": "secret output body"}))
        package = projector.finish(True)
        encoded = json.dumps(package) + native_path(Path(package["calls"]["path"])).read_text(encoding="utf-8")
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

    def test_the_binding_comes_only_from_a_validated_request_identity(self):
        # The legacy control-file binding check now lives at the frame the
        # driver actually consumes: a run request identity that is missing a
        # field, of the wrong type, empty or negative never reaches the
        # projector, and a validated identity projects exactly the private
        # Python binding the evidence carries.
        from hey_my_buddy.buddy.harnesses.run_contract import (
            PrivateStatePaths, RunBudget, RunBuddy, RunIdentity, RunRequest,
            RunRequest,
        )
        root = Path(tempfile.gettempdir()).resolve() / "fixture"
        request = RunRequest(
            identity=RunIdentity(task_id="t", attempt_id="a", generation=0, invocation_id="inv"),
            harness="codex", buddy=RunBuddy(provider="openai", model="m", effort="low"),
            session_record_path=str(root / "session.jsonl"),
            cwd=str(root), private_state=PrivateStatePaths(state_dir=str(root / "state"), invocation_root=str(root / "i"), native_root=str(root / "n")),
            input_text="prompt", permission="read", output_schema={},
            budget=RunBudget(timeout_seconds=8))
        frame = request.to_payload()
        decoded = RunRequest.from_payload(frame)
        projector = CodexToolEventProjector({"harness": "codex", "taskId": decoded.identity.task_id,
                                              "attemptId": decoded.identity.attempt_id,
                                              "generation": decoded.identity.generation},
                                             state_dir=Path(decoded.private_state.state_dir))
        self.assertEqual(projector.finish(False)["binding"], {"harness": "codex", "taskId": "t",
                                                              "attemptId": "a", "generation": 0})
        mutations = {"missing task": lambda identity: identity.pop("taskId"),
                     "missing attempt": lambda identity: identity.pop("attemptId"),
                     "numeric task": lambda identity: identity.update(taskId=5),
                     "empty task": lambda identity: identity.update(taskId=""),
                     "empty attempt": lambda identity: identity.update(attemptId=""),
                     "string generation": lambda identity: identity.update(generation="1"),
                     "negative generation": lambda identity: identity.update(generation=-1)}
        for label, mutate in mutations.items():
            with self.subTest(label=label):
                identity = dict(frame["identity"])
                mutate(identity)
                with self.assertRaises((ValueError, TypeError, BoardError)):
                    RunRequest.from_payload({**frame, "identity": identity})
        # The projector itself still refuses an incomplete binding, so a
        # caller that bypasses the frame validation cannot invent one.
        with self.assertRaises(BoardError):
            CodexToolEventProjector({"harness": "codex", "taskId": "t"},
                                    state_dir=Path(os.environ["HMB_STATE_DIR"]))


class UnreadableCodexToolEvidenceTests(unittest.TestCase):
    def test_unreadable_typed_and_raw_items_cannot_prove_no_tools(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        for method in ("item/started", "item/updated", "item/completed", "rawResponseItem/completed"):
            for item in (None, [], "opaque", {}, {"type": 17}, {"type": []}):
                with self.subTest(method=method, item=item):
                    projector = new_projector()
                    projector.observe_root("thread-1", "turn-1")
                    projector.observe_notification(frame(method, item))
                    evidence = projector.finish(True)
                    self.assertFalse(evidence["streamComplete"])
                    self.assertIsNotNone(judge_tool_evidence(evidence, "none", state_dir=Path(os.environ["HMB_STATE_DIR"])))

    def test_unknown_connection_and_thread_notifications_preserve_tool_completeness(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        projector = new_projector()
        projector.observe_root("thread-1", "turn-1")
        projector.observe_notification({"method": "account/updated", "params": {}})
        projector.observe_notification({"method": "thread/futureNotification", "params": {"threadId": "thread-1"}})
        evidence = projector.finish(True)
        self.assertTrue(evidence["streamComplete"])
        self.assertIsNone(judge_tool_evidence(evidence, "none", state_dir=Path(os.environ["HMB_STATE_DIR"])))


class CodexReviewToolEvidenceTests(unittest.TestCase):
    """The review controller projects unified evidence over the fixture stream."""

    def setUp(self):
        self.temp = TemporaryDirectory(prefix="hey-my-buddy-codex-tool-evidence-",
                                                dir=os.environ.get("HMB_CHECKS_TMPDIR", tempfile.gettempdir()))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.home = self.root / "account-home"
        self.home.mkdir(mode=0o700)
        self.adapter = CodexAdapter()

    def context(self, case, *, index, task="review-task", attempt="review-attempt", generation=7):
        from hey_my_buddy.buddy.harnesses.base import ExecutionContext
        environment = {key: value for key, value in os.environ.items()
                       if not key.startswith("HMB_") and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        environment.update(HMB_CONSOLE_PORT="0",
                           HMB_CODEX_FIXTURE_STATE=str(self.root / f"fixture-{index}.json"),
                           CODEX_HOME=str(self.home), HOME=str(self.root), USERPROFILE=str(self.root),
                           HMB_STATE_DIR=str(self.root / "state"), HMB_RUNTIME_ROOT=str(self.root / "runtime"),
                           HMB_DEV_SOURCE="1", HMB_CODEX_FIXTURE_CASE=case,
                           PYTHONPATH=str(Path(__file__).parents[5] / "src") + os.pathsep + environment.get("PYTHONPATH", ""))
        select_fixture(environment, self.root, "codex", FIXTURE)
        context = ExecutionContext(task_id=task, attempt_id=attempt, generation=generation,
                                  session_record_path=str(self.root / f"session-{attempt}.jsonl"),
                                  spec={"cwd": str(self.cwd), "task": "Select", "routerBudgetSeconds": 8,
                                        "provider": "openai", "model": "fixture-model", "effort": "low"},
                                  directory=self.root / f"attempt-{index}", runtime={}, environment=environment,
                                  turn=None)
        return context

    def run_review(self, case, *, index, **request_options):
        context = self.context(case, index=index)
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet",
                                            answer_schema(["legal"]), {**budget(), **request_options},
                                            capture_evidence=True)
        handle = start_router_preparation(ReviewPreparation("codex", self.adapter, request, context,
                                                            (None, self.cwd, "fixture-digest")))
        self.addCleanup(lambda: (handle.terminate(grace_seconds=0.2), handle.wait(15)) if not handle.finished else None)
        self.assertIsNotNone(handle.wait(30), "Codex fixture controller did not exit")
        return collect(handle), context

    def test_protocol_variants_retain_answers_and_report_tools_for_publication(self):
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        for index, (name, _carrier, _fields, expected_calls, phases, complete) in enumerate(PROTOCOL_ITEMS + FUTURE_ITEMS):
            with self.subTest(name=name):
                outcome, context = self.run_review("protocol-" + name, index="protocol-" + str(index))
                self.assertEqual(outcome.status, "ok", outcome.result)
                self.assertEqual(json.loads(outcome.result["rawAnswer"])["buddyId"], "legal")
                evidence = outcome.result["toolEvidence"]
                self.assertEqual(evidence["toolCalls"], expected_calls)
                self.assertEqual(outcome.result["usage"]["toolCalls"], expected_calls)
                self.assertEqual(tuple(calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"]))[0]["phases"]) if phases else (),
                                 tuple(sorted(phase for phase in phases if phase is not None)))
                self.assertEqual(evidence["streamComplete"], complete)
                self.assertEqual(judge_tool_evidence(evidence, "read", state_dir=Path(context.environment["HMB_STATE_DIR"])),
                                 "router-tool-evidence-unverified" if not complete
                                     else "router-tools-forbidden" if phases else None)

    def test_settled_typed_and_raw_calls_project_as_two_counted_calls(self):
        outcome, context = self.run_review("readonly-evidence", index=1)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"harness": "codex", "taskId": context.task_id,
                                               "attemptId": context.attempt_id, "generation": context.generation})
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"}])
        self.assertTrue(evidence["streamComplete"])
        self.assertEqual(evidence["version"], 2)
        self.assertEqual(evidence["toolCalls"], 2)
        self.assertEqual(evidence["unsettledToolCalls"], 0)
        self.assertEqual([(call["callId"], call["categories"], call["phases"])
                          for call in calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"]))],
                         [("item-1", ["execute"], ["end", "start"]),
                          ("call-1", ["execute"], ["end", "start"])])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 2)
        # The prior bounded capture keeps its raw replayed frame unchanged.
        self.assertEqual(len(outcome.result["nativeRawToolEvents"]), 3)

    def test_equivalent_typed_and_raw_names_of_one_call_stay_recorded(self):
        outcome, context = self.run_review("readonly-conflict", index=2)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["toolCalls"], 1)
        self.assertEqual([(call["categories"], call["phases"], call["consistent"])
                          for call in calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"])) if call["callId"] == "call-1"],
                         [(["execute"], ["end", "start"], True)])
        from hey_my_buddy.protocol.tool_evidence import judge_tool_evidence
        self.assertIsNone(judge_tool_evidence(evidence, 'read', state_dir=Path(context.environment["HMB_STATE_DIR"])))

    def test_foreign_and_old_turn_facts_are_kept_with_their_real_identity(self):
        outcome, context = self.run_review("readonly-foreign", index=3)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        identities = {json.dumps(call["nativeIdentity"], sort_keys=True) for call in calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"]))}
        self.assertEqual(identities, {json.dumps(identity, sort_keys=True) for identity in (
            {"sessionId": "thread-sub", "turnId": "sub-turn-1"},
            {"sessionId": "thread-1", "turnId": "native-turn-99"})})
        # The trusted roots are only the native turn receipt, never the events.
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"}])
        self.assertEqual(evidence["toolCalls"], 2)

    def test_stream_above_old_bound_keeps_all_judgeable_calls(self):
        outcome, context = self.run_review("readonly-truncated", index=4, toolCalls=500)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(len(calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"]))), 130)
        self.assertEqual(evidence["toolCalls"], 130)
        self.assertEqual(evidence["unsettledToolCalls"], 0)
        self.assertLess(evidence["calls"]["bytes"], 130 * 512)
        self.assertLess(len(json.dumps(evidence)), 2048)
        self.assertIsNone(tool_evidence.judge_tool_evidence(evidence, "read", state_dir=Path(context.environment["HMB_STATE_DIR"])))
        self.assertTrue(outcome.result["nativeEvidenceTruncated"])

    def test_correction_accumulates_roots_tool_counts_and_keeps_old_turn_facts(self):
        outcome, context = self.run_review("readonly-repair-evidence", index=5)
        self.assertEqual(outcome.status, "ok", outcome.result)
        self.assertEqual(outcome.result["correctionCount"], 1)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": "thread-1", "turnId": "native-turn-1"},
                                                      {"sessionId": "thread-1", "turnId": "native-turn-2"}])
        self.assertEqual(evidence["toolCalls"], 3)
        self.assertEqual(evidence["unsettledToolCalls"], 1)
        stale = [call for call in calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"])) if call["callId"] == "item-stale"]
        self.assertEqual([(call["nativeIdentity"], call["categories"], call["phases"]) for call in stale],
                         [({"sessionId": "thread-1", "turnId": "native-turn-1"}, ["execute"], ["start"])])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 3)
        self.assertFalse(evidence["streamComplete"])

    def test_failure_before_any_turn_still_carries_an_incomplete_package(self):
        outcome, context = self.run_review("no-efforts-row", index=6)
        self.assertNotEqual(outcome.status, "ok")
        self.assertEqual(outcome.result["code"], "invalid-buddy")
        self.assertIs(outcome.result["modelStarted"], False)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["binding"], {"harness": "codex", "taskId": context.task_id,
                                               "attemptId": context.attempt_id, "generation": context.generation})
        self.assertEqual(evidence["nativeIdentity"], [])
        self.assertFalse(evidence["streamComplete"])
        self.assertEqual(calls(evidence, state_dir=Path(context.environment["HMB_STATE_DIR"])), [])

    def test_budget_interrupt_leaves_the_open_call_unsettled_in_the_package(self):
        outcome, context = self.run_review("readonly-budget", index=7, toolCalls=0)
        self.assertEqual(outcome.result["code"], "readonly-budget-exhausted")
        evidence = outcome.result["toolEvidence"]
        self.assertEqual(evidence["toolCalls"], 1)
        self.assertEqual(evidence["unsettledToolCalls"], 1)
        self.assertFalse(evidence["streamComplete"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 1)


if __name__ == "__main__":
    unittest.main()
