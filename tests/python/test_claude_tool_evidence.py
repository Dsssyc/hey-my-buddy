"""Claude read-only tool evidence: frame projection and runner receipt wiring.

No model calls and no board: the collector is driven frame by frame, and the
dedicated stream fixture (``fixtures/mock_claude.py``) drives the real runner
subprocess for the receipt-level facts — the finish after the observed stream
close, the unified collector budget count, and the binding taken from the
private Python control file.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.claude import adapter as claude_module
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.claude.tool_evidence import ReadOnlyToolEvidence
from hey_my_buddy.blackboard.routing.router import answer_schema

FIXTURE = Path(__file__).parent / "fixtures/mock_claude.py"
BINDING = {"adapter": "claude", "taskId": "task-1", "attemptId": "attempt-1", "generation": 1}
ROOT = {"sessionId": "session-root"}


def init_frame(session="session-root"):
    return {"type": "system", "subtype": "init", "session_id": session, "cwd": "/tmp/checkout", "model": "claude-opus-5-5[1m]"}


def assistant_tool(name, call_id, session="session-root", parent=None, with_name=True):
    block = {"type": "tool_use", "id": call_id}
    if with_name:
        block["name"] = name
    fields = {}
    if session is not None:
        fields["session_id"] = session
    if parent is not None:
        fields["parent_tool_use_id"] = parent
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "checking"}, block]}, **fields}


def stream_start(name, call_id, session="session-root"):
    return {"type": "stream_event", "session_id": session, "event": {"type": "content_block_start", "index": 1,
            "content_block": {"type": "tool_use", "id": call_id, "name": name}}}


def block_stop(session="session-root"):
    return {"type": "stream_event", "session_id": session,
            "event": {"type": "content_block_stop", "index": 1}}


def tool_result(call_id, session="session-root", parent=None):
    fields = {}
    if session is not None:
        fields["session_id"] = session
    if parent is not None:
        fields["parent_tool_use_id"] = parent
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": call_id, "content": "ok"}]}, **fields}


def result_frame(session="session-root"):
    return {"type": "result", "subtype": "success", "is_error": False, "session_id": session}


def project(frames, stream_complete=True):
    collector = ReadOnlyToolEvidence(dict(BINDING))
    for frame in frames:
        collector.observe_frame(frame)
    return collector, collector.finish(stream_complete)


def settled_stream():
    return [init_frame(),
            assistant_tool("Read", "toolu_1"), tool_result("toolu_1"),
            stream_start("Grep", "toolu_2"), block_stop(), tool_result("toolu_2"),
            result_frame()]


class ProjectionTests(unittest.TestCase):
    def test_a_settled_root_stream_projects_the_shared_fact_package(self):
        collector, package = project(settled_stream())
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [ROOT])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 0))
        self.assertTrue(package["streamComplete"])
        self.assertFalse(package["truncated"])
        self.assertEqual([(event["toolName"], event["category"], event["phase"]) for event in package["events"]],
                         [("Read", "read", "start"), ("Read", "read", "end"),
                          ("Grep", "search", "start"), ("Grep", "search", "end")])
        self.assertTrue(all(event["nativeIdentity"] == ROOT for event in package["events"]))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))
        self.assertEqual(collector.tool_calls, 2)

    def test_replays_and_stream_projections_of_one_call_collapse(self):
        _, package = project([init_frame(),
                              assistant_tool("Read", "toolu_1"),
                              assistant_tool("Read", "toolu_1"),
                              stream_start("Read", "toolu_1"),
                              tool_result("toolu_1")])
        self.assertEqual((package["toolCalls"], len(package["events"])), (1, 2))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_conflicting_facts_for_one_call_are_kept(self):
        _, package = project([init_frame(),
                              assistant_tool("Read", "toolu_1"), tool_result("toolu_1"),
                              assistant_tool("Grep", "toolu_1"), tool_result("toolu_1")])
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(len(package["events"]), 4)
        self.assertEqual({event["toolName"] for event in package["events"]}, {"Read", "Grep"})
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_stream_block_stop_is_never_a_tool_end(self):
        _, package = project([init_frame(), stream_start("Read", "toolu_1"), block_stop(), result_frame()])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 1))
        self.assertEqual([event["phase"] for event in package["events"]], ["start"])
        self.assertTrue(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_subagent_facts_keep_their_parent_identity_and_never_inherit_the_root(self):
        _, package = project([init_frame(),
                              assistant_tool("Read", "toolu_1"), tool_result("toolu_1"),
                              assistant_tool("Glob", "toolu_sub_1", parent="toolu_parent_9"),
                              tool_result("toolu_sub_1", parent="toolu_parent_9")])
        self.assertEqual(package["nativeIdentity"], [ROOT])
        self.assertEqual(package["toolCalls"], 2)
        parent_identity = {"sessionId": "session-root", "callId": "toolu_parent_9"}
        sub_facts = [event for event in package["events"] if event["nativeIdentity"] != ROOT]
        self.assertEqual(sub_facts, [
            {"nativeIdentity": parent_identity, "callId": "toolu_sub_1", "toolName": "Glob",
             "category": "search", "phase": "start"},
            {"nativeIdentity": parent_identity, "callId": "toolu_sub_1", "toolName": "Glob",
             "category": "search", "phase": "end"}])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_foreign_session_is_kept_and_later_session_less_frames_turn_unprovable(self):
        _, package = project([init_frame(),
                              assistant_tool("Read", "toolu_1", session="session-other"),
                              tool_result("toolu_1", session="session-other"),
                              assistant_tool("Read", "toolu_2", session=None)])
        self.assertEqual(package["events"][:2], [
            {"nativeIdentity": {"sessionId": "session-other"}, "callId": "toolu_1",
             "toolName": "Read", "category": "read", "phase": "start"},
            {"nativeIdentity": {"sessionId": "session-other"}, "callId": "toolu_1",
             "toolName": "Read", "category": "read", "phase": "end"}])
        self.assertEqual(package["events"][2], {"nativeIdentity": {}, "callId": "toolu_2",
                                                "toolName": "Read", "category": "read", "phase": "start"})
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_handshake_confirmed_single_root_adopts_session_less_root_frames(self):
        _, early = project([assistant_tool("Read", "toolu_1", session=None), init_frame()])
        self.assertEqual(early["events"][0]["nativeIdentity"], {})
        self.assertFalse(early["streamComplete"])
        _, adopted = project([init_frame(), assistant_tool("Read", "toolu_1", session=None),
                              tool_result("toolu_1", session=None)])
        self.assertEqual(adopted["nativeIdentity"], [ROOT])
        self.assertEqual([event["nativeIdentity"] for event in adopted["events"]], [ROOT, ROOT])
        self.assertIsNone(tool_evidence.judge_tool_evidence(adopted, "review", True))

    def test_session_tagged_facts_stay_foreign_without_a_confirmed_root(self):
        _, package = project([assistant_tool("Read", "toolu_1"), tool_result("toolu_1")])
        self.assertEqual(package["nativeIdentity"], [])
        self.assertEqual([event["nativeIdentity"] for event in package["events"]],
                         [{"sessionId": "session-root"}, {"sessionId": "session-root"}])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_uncorrelatable_and_nameless_facts_are_retained_without_invented_ids(self):
        _, package = project([init_frame(),
                              {"type": "user", "message": {"role": "user", "content": [
                                  {"type": "tool_result", "tool_use_id": "toolu_9"}]},
                               "session_id": "session-root"},
                              assistant_tool("", "toolu_1", with_name=False)])
        self.assertEqual(package["events"], [
            {"nativeIdentity": ROOT, "callId": "toolu_9", "toolName": None, "category": "other", "phase": "end"},
            {"nativeIdentity": ROOT, "callId": "toolu_1", "toolName": None, "category": "other", "phase": "start"}])
        self.assertFalse(package["streamComplete"])

    def test_unknown_tool_names_stay_other_and_count(self):
        _, package = project([init_frame(),
                              assistant_tool("mcp__server__tool", "toolu_1"), tool_result("toolu_1"),
                              assistant_tool("Task", "toolu_2"), tool_result("toolu_2")])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 0))
        self.assertEqual({event["category"] for event in package["events"]}, {"other"})
        self.assertTrue(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_facts_after_the_result_frame_still_project_before_the_close(self):
        collector, package = project([init_frame(),
                                      assistant_tool("Read", "toolu_1"), tool_result("toolu_1"),
                                      result_frame(), assistant_tool("Grep", "toolu_2")])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 1))
        self.assertEqual([event["callId"] for event in package["events"]],
                         ["toolu_1", "toolu_1", "toolu_2"])
        self.assertFalse(package["streamComplete"])

    def test_the_event_bound_reports_truncation(self):
        frames = [init_frame()]
        for index in range(65):
            frames += [assistant_tool("Read", f"toolu_{index}"), tool_result(f"toolu_{index}")]
        _, package = project(frames)
        self.assertEqual(len(package["events"]), tool_evidence.MAX_TOOL_EVENTS)
        self.assertEqual(package["toolCalls"], 65)
        self.assertTrue(package["truncated"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_the_first_finish_fixes_the_package_and_its_roots_come_from_the_handshake_only(self):
        collector = ReadOnlyToolEvidence(dict(BINDING))
        collector.observe_frame(init_frame())
        collector.observe_frame(assistant_tool("Read", "toolu_1"))
        collector.observe_frame(tool_result("toolu_1"))
        self.assertEqual(collector.finish(False)["streamComplete"], False)
        self.assertEqual(collector.finish(True)["streamComplete"], False)
        foreign_roots = ReadOnlyToolEvidence(dict(BINDING))
        foreign_roots.observe_frame(assistant_tool("Read", "toolu_1", session="session-root"))
        self.assertEqual(foreign_roots.finish(True)["nativeIdentity"], [])


class RunnerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-claude-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "checkout"
        self.cwd.mkdir()
        FIXTURE.chmod(0o755)
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("BUDDY_") and not key.startswith(("ANTHROPIC_", "CLAUDE_"))
                            and key not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment.update(BUDDY_CONSOLE_PORT="0", BUDDY_CLAUDE_CLI=str(FIXTURE),
                                BUDDY_CLAUDE_FIXTURE_STATE=str(self.root / "fixture.json"),
                                BUDDY_CLAUDE_SETTINGS_POLICY="isolated",
                                BUDDY_STATE_DIR=str(self.root / "state"), BUDDY_RUNTIME_ROOT=str(self.root / "runtime"),
                                BUDDY_DEV_SOURCE="1")
        claude_module._reset_metadata_cache()
        self.addCleanup(claude_module._reset_metadata_cache)
        self.adapter = ClaudeAdapter()

    def context(self, case):
        return ExecutionContext(task_id="goal-1", attempt_id="attempt-1", generation=1,
                                spec={"cwd": str(self.cwd), "task": "Review the frozen copy", "timeoutSeconds": 20,
                                      "provider": "anthropic", "model": "claude-opus-5-5[1m]", "effort": "low"},
                                directory=self.root / "attempt-1", runtime={},
                                environment={**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": case}, turn=None)

    def run_read_only(self, case, *, tool_calls=8):
        from hey_my_buddy.buddy.roles.structured_call import collect
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet", answer_schema(["legal"]),
                                            {"timeoutSeconds": 20, "toolCalls": tool_calls})
        context = self.context(case)
        handle = self.adapter.start_read_only_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(30), "the stream fixture controller did not exit")
        return context, collect(handle)

    def control(self, context):
        return json.loads((context.directory / "readonly-control.json").read_text())

    def test_a_settled_receipt_finishes_evidence_after_the_observed_stream_close(self):
        context, outcome = self.run_read_only("clean")
        self.assertEqual(outcome.status, "ok", outcome.result)
        receipt = outcome.result
        session = self.control(context)["sessionId"]
        self.assertEqual(receipt["nativeIdentity"], {"sessionId": session})
        self.assertEqual(receipt["usage"]["toolCalls"], 2)
        self.assertIsNone(receipt["usage"]["bytesRead"])
        self.assertIsInstance(receipt["usage"]["elapsedMs"], int)
        evidence = receipt["toolEvidence"]
        self.assertEqual(evidence["version"], 1)
        self.assertEqual(evidence["binding"], {"adapter": "claude", "taskId": "goal-1",
                                               "attemptId": "attempt-1", "generation": 1})
        self.assertEqual(evidence["nativeIdentity"], [{"sessionId": session}])
        self.assertTrue(evidence["streamComplete"])
        self.assertEqual((evidence["toolCalls"], evidence["unsettledToolCalls"], evidence["truncated"]), (2, 0, False))
        self.assertEqual(len(evidence["events"]), 4)
        self.assertTrue(receipt["answerValid"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(evidence, "review", True))

    def test_replayed_and_stream_projected_calls_count_once_in_the_receipt(self):
        # A limit of one allows the one real call: the old per-frame block count
        # would have seen three projections and interrupted the turn instead.
        context, outcome = self.run_read_only("replay", tool_calls=1)
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual((evidence["toolCalls"], len(evidence["events"])), (1, 2))
        self.assertEqual(outcome.result["usage"]["toolCalls"], 1)

    def test_subagent_facts_reach_the_receipt_without_inheriting_the_root(self):
        context, outcome = self.run_read_only("subagent")
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        parent_identity = {"sessionId": self.control(context)["sessionId"], "callId": "toolu_parent_9"}
        self.assertEqual(evidence["toolCalls"], 2)
        self.assertEqual([event["nativeIdentity"] for event in evidence["events"]],
                         [evidence["nativeIdentity"][0], evidence["nativeIdentity"][0],
                          parent_identity, parent_identity])
        self.assertEqual(tool_evidence.judge_tool_evidence(evidence, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_block_stop_receipt_keeps_the_call_unsettled(self):
        context, outcome = self.run_read_only("block-stop")
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        self.assertEqual((evidence["toolCalls"], evidence["unsettledToolCalls"]), (1, 1))
        self.assertTrue(evidence["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(evidence, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_facts_after_the_result_frame_settle_only_after_the_stream_closes(self):
        context, outcome = self.run_read_only("late")
        self.assertEqual(outcome.status, "ok", outcome.result)
        evidence = outcome.result["toolEvidence"]
        # A call after the native result remains visible and taints the stream,
        # even if a later tool-result were to settle it before EOF.
        self.assertEqual((evidence["toolCalls"], evidence["unsettledToolCalls"]), (2, 1))
        self.assertEqual([event["callId"] for event in evidence["events"]],
                         ["toolu_1", "toolu_1", "toolu_2"])
        self.assertFalse(evidence["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(evidence, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_budget_exhaustion_reports_the_unified_count_and_an_unfinished_stream(self):
        context, outcome = self.run_read_only("budget", tool_calls=1)
        self.assertEqual(outcome.status, "failed", outcome.result)
        receipt = outcome.result
        self.assertEqual(receipt["code"], "readonly-budget-exhausted")
        self.assertEqual(receipt["usage"]["toolCalls"], 2)
        evidence = receipt["toolEvidence"]
        self.assertEqual((evidence["toolCalls"], len(evidence["events"])), (2, 3))
        self.assertFalse(evidence["streamComplete"])
        self.assertTrue(json.loads((self.root / "fixture.json").read_text())["interrupted"],
                        "the N+1 call must be interrupted through the native control request")

    def test_premature_tool_output_is_collected_before_the_boundary_rejection(self):
        context, outcome = self.run_read_only("premature")
        self.assertEqual(outcome.status, "failed", outcome.result)
        receipt = outcome.result
        self.assertEqual(receipt["code"], "native-turn-started-early")
        self.assertNotIn("rawAnswer", receipt)
        evidence = receipt["toolEvidence"]
        self.assertEqual(evidence["events"], [{"nativeIdentity": {}, "callId": "toolu_early_1",
                                               "toolName": "Read", "category": "read", "phase": "start"}])
        self.assertFalse(evidence["streamComplete"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
