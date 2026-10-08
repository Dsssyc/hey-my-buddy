"""Claude read-only tool evidence: frame projection and role-run receipt wiring.

No model calls and no board: the collector is driven frame by frame, and the
dedicated stream fixture (``fixtures/mock_claude.py``) drives the real
registered role run — the shared role controller over the claude native run —
for the receipt-level facts: the finish after the observed stream close, the
unified collector budget count, and the binding taken from the stored public
run request.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.claude import adapter as claude_module
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, ReadOnlyStructuredRequest
from hey_my_buddy.buddy.harnesses.claude.adapter import ClaudeAdapter
from hey_my_buddy.buddy.harnesses.claude.tool_evidence import ReadOnlyToolEvidence
from hey_my_buddy.buddy.harnesses.registry import run_seam
from hey_my_buddy.buddy.roles.run_execution import start_review
from hey_my_buddy.buddy.roles.structured_call import collect
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


def structured_use(call_id, value, session="session-root", parent=None):
    block = {"type": "tool_use", "id": call_id, "name": "StructuredOutput", "input": value}
    fields = {}
    if session is not None:
        fields["session_id"] = session
    if parent is not None:
        fields["parent_tool_use_id"] = parent
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": "delivering"}, block]}, **fields}


def armed(frames):
    """An armed collector over the frames: this run's schema command line is on."""
    collector = ReadOnlyToolEvidence(dict(BINDING), native_schema_delivery=True)
    for frame in frames:
        collector.observe_frame(frame)
    return collector


DELIVERED = {"profileId": "legal", "reason": "the delivered value", "evidence": ["one"]}


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


class StructuredDeliveryProjectionTests(unittest.TestCase):
    """The built-in StructuredOutput delivery identified by its native facts only."""

    def delivery_key(self, call_id="toolu_so_1", identity=None):
        from hey_my_buddy.json_codec import canonical_json
        return (canonical_json(identity or ROOT), call_id)

    def test_the_root_delivery_is_verified_and_excluded_from_the_package(self):
        collector = armed([init_frame(), structured_use("toolu_so_1", DELIVERED),
                           tool_result("toolu_so_1")])
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), (self.delivery_key(),))
        # The one settlement is frozen; a second call returns the same answer.
        self.assertEqual(collector.settle_delivery({"profileId": "other"}, ROOT),
                         (self.delivery_key(),))
        self.assertEqual(collector.tool_calls, 0)
        package = collector.finish(True, exclude_calls=collector.settle_delivery(DELIVERED, ROOT))
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], package["events"]),
                         (0, 0, []))
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_the_live_budget_count_holds_the_candidate_before_verification(self):
        armed_collector = armed([init_frame(), structured_use("toolu_so_1", DELIVERED)])
        self.assertEqual(armed_collector.tool_calls, 0)
        # The same frames under a run without the schema command line keep the
        # ordinary-tool projection: the call counts as one ordinary start.
        disarmed = ReadOnlyToolEvidence(dict(BINDING))
        for frame in (init_frame(), structured_use("toolu_so_1", DELIVERED)):
            disarmed.observe_frame(frame)
        self.assertEqual(disarmed.tool_calls, 1)
        self.assertEqual(disarmed.settle_delivery(DELIVERED, ROOT), ())

    def test_an_unarmed_collector_never_treats_the_builtin_call_as_delivery(self):
        # The negative witness beside the armed positive above: under the
        # actual native_schema_delivery=False branch, the exact same frames
        # keep the built-in call an ordinary tool. The settlement verifies
        # nothing, the call stays counted through the exclusion seam, and the
        # finished package carries its facts instead of dropping them from the
        # tool count — the switch constant alone proves none of that.
        collector = ReadOnlyToolEvidence(dict(BINDING), native_schema_delivery=False)
        for frame in (init_frame(), structured_use("toolu_so_1", DELIVERED),
                      tool_result("toolu_so_1")):
            collector.observe_frame(frame)
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        self.assertEqual(collector.tool_calls, 1)
        package = collector.finish(True, exclude_calls=collector.settle_delivery(DELIVERED, ROOT))
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertEqual([(event["toolName"], event["phase"]) for event in package["events"]],
                         [("StructuredOutput", "start"), ("StructuredOutput", "end")])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN,
                         "an unarmed StructuredOutput call is an ordinary forbidden tool")

    def test_settlement_releases_unverified_candidates_before_the_role_sees_the_count(self):
        # A candidate whose input never matches the final value is held while
        # it waits for its association, but the settlement that precedes the
        # role's settled facts releases it: the last count the role may act on
        # and the finished package report the same classification.
        collector = armed([init_frame(),
                           structured_use("toolu_so_1", {"profileId": "forged"}), tool_result("toolu_so_1")])
        self.assertEqual(collector.tool_calls, 0)
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        self.assertEqual(collector.tool_calls, 1)
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 1)

    def test_a_missing_final_value_settles_to_no_verification(self):
        collector = armed([init_frame(), structured_use("toolu_so_1", DELIVERED), tool_result("toolu_so_1")])
        self.assertEqual(collector.settle_delivery(None, ROOT), ())
        self.assertEqual(collector.tool_calls, 1)
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 1)

    def test_stream_and_assistant_duplicate_projections_verify_as_one_call(self):
        collector = armed([init_frame(),
                           stream_start("StructuredOutput", "toolu_so_1"), block_stop(),
                           structured_use("toolu_so_1", DELIVERED), tool_result("toolu_so_1")])
        verified = collector.settle_delivery(DELIVERED, ROOT)
        self.assertEqual(verified, (self.delivery_key(),))
        package = collector.finish(True, exclude_calls=verified)
        self.assertEqual((package["toolCalls"], package["events"]), (0, []))

    def test_a_value_mismatch_verifies_nothing_and_keeps_the_call_counted(self):
        collector = armed([init_frame(),
                           structured_use("toolu_so_1", {"profileId": "forged"}), tool_result("toolu_so_1")])
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual([event["category"] for event in package["events"]], ["other", "other"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_a_second_different_full_input_refuses_the_exemption_in_either_order(self):
        other = {"profileId": "conflict", "reason": "a different complete input", "evidence": []}
        for name, first, second in (("delivered-first", DELIVERED, other), ("conflict-first", other, DELIVERED)):
            with self.subTest(order=name):
                collector = armed([init_frame(), structured_use("toolu_so_1", first),
                                   structured_use("toolu_so_1", second), tool_result("toolu_so_1")])
                # The conflicted candidate is never held out of a count, and
                # the final value — whichever input it matches — verifies none.
                self.assertEqual(collector.tool_calls, 1)
                self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
                self.assertEqual(collector.tool_calls, 1)
                package = collector.finish(True)
                self.assertEqual(package["toolCalls"], 1)
                self.assertEqual([(event["toolName"], event["phase"]) for event in package["events"]],
                                 [("StructuredOutput", "start"), ("StructuredOutput", "end")])
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                                 tool_evidence.TOOLS_FORBIDDEN)

    def test_an_incomplete_fact_on_the_call_refuses_the_exemption(self):
        # A nameless end before the start is a broken fact on the same native
        # call: the shared exclusion guard refuses such a call, so the
        # candidate is never held or verified even with a matching input.
        broken_end = {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_so_1"}]}, "session_id": "session-root"}
        collector = armed([init_frame(), broken_end,
                           structured_use("toolu_so_1", DELIVERED)])
        self.assertEqual(collector.tool_calls, 1)
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 1)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_subagent_foreign_and_mounted_lookalikes_are_not_candidates(self):
        collector = armed([init_frame(),
                           structured_use("toolu_sub", DELIVERED, parent="toolu_parent_9"),
                           tool_result("toolu_sub", parent="toolu_parent_9"),
                           structured_use("toolu_foreign", DELIVERED, session="session-other"),
                           tool_result("toolu_foreign", session="session-other"),
                           assistant_tool("mcp__server__StructuredOutput", "toolu_mcp"),
                           tool_result("toolu_mcp")])
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        self.assertEqual(collector.tool_calls, 3)
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 3)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_a_conflicting_start_name_on_the_same_call_refuses_the_exemption(self):
        collector = armed([init_frame(), structured_use("toolu_so_1", DELIVERED),
                           assistant_tool("Read", "toolu_so_1")])
        self.assertEqual(collector.settle_delivery(DELIVERED, ROOT), ())
        self.assertEqual(collector.tool_calls, 1)
        package = collector.finish(True)
        self.assertEqual(len(package["events"]), 2)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_candidate_on_an_unconfirmed_root_verifies_nothing(self):
        foreign = armed([init_frame(session="session-other"),
                         structured_use("toolu_so_1", DELIVERED, session="session-other"),
                         tool_result("toolu_so_1", session="session-other")])
        # The handshake confirmed another session: this run's root identity can
        # never verify the delivery of a run rooted elsewhere, and the one
        # settlement is frozen with that answer.
        self.assertEqual(foreign.settle_delivery(DELIVERED, ROOT), ())
        self.assertEqual(foreign.tool_calls, 1)
        own = armed([init_frame(), structured_use("toolu_so_1", DELIVERED), tool_result("toolu_so_1")])
        self.assertEqual(own.settle_delivery(DELIVERED, ROOT), (self.delivery_key(),))

    def test_repeated_deliveries_settle_consistently_under_the_input_bound(self):
        frames = [init_frame()]
        for index in range(10):
            frames += [structured_use(f"toolu_so_{index}", DELIVERED), tool_result(f"toolu_so_{index}")]
        collector = armed(frames)
        verified = collector.settle_delivery(DELIVERED, ROOT)
        # The input retention bound keeps the most recent eight candidates; the
        # two oldest keep their facts but can no longer prove their value, and
        # the settled count releases them exactly as the finished package does.
        self.assertEqual([call_id for _identity, call_id in verified],
                         [f"toolu_so_{index}" for index in range(2, 10)])
        self.assertEqual(collector.tool_calls, 2)
        package = collector.finish(True, exclude_calls=verified)
        self.assertEqual((package["toolCalls"], len(package["events"])), (2, 4))

    def test_an_evicted_first_input_still_refuses_a_conflicting_second(self):
        # The Host probe's shape: c1 first carries {"answer": "first"}; c2..c9
        # each carry a different complete input and the eight-slot bound evicts
        # c1's full text; c1 then carries the delivered {"answer": "conflict"}.
        # The first input's fingerprint survives the eviction, so the conflict
        # refuses the exemption and all nine distinct calls stay counted.
        frames = [init_frame(), structured_use("toolu_so_1", {"answer": "first"})]
        for index in range(2, 10):
            frames.append(structured_use(f"toolu_so_{index}", {"answer": f"different-{index}"}))
        frames.append(structured_use("toolu_so_1", {"answer": "conflict"}))
        collector = armed(frames)
        # Pre-settlement only the conflicted call charges the count; the eight
        # consistent candidates still wait for their final association.
        self.assertEqual(collector.tool_calls, 1)
        self.assertEqual(collector.settle_delivery({"answer": "conflict"}, ROOT), ())
        self.assertEqual(collector.tool_calls, 9)
        package = collector.finish(True)
        self.assertEqual(package["toolCalls"], 9)

    def test_an_evicted_input_reproves_the_value_when_the_same_input_returns(self):
        frames = [init_frame(), structured_use("toolu_so_1", DELIVERED)]
        for index in range(2, 10):
            frames.append(structured_use(f"toolu_so_{index}", {"answer": f"different-{index}"}))
        frames.append(structured_use("toolu_so_1", DELIVERED))
        collector = armed(frames)
        verified = collector.settle_delivery(DELIVERED, ROOT)
        # The same input returning after the eviction is a consistent repeat:
        # its recaptured full text re-proves the value, the eight mismatched
        # calls stay counted, and the settled count and package agree.
        self.assertEqual(verified, (self.delivery_key("toolu_so_1"),))
        self.assertEqual(collector.tool_calls, 8)
        package = collector.finish(True, exclude_calls=verified)
        self.assertEqual(package["toolCalls"], 8)

    def test_a_mixed_candidate_field_settles_to_one_agreed_count(self):
        collector = armed([init_frame(),
                           structured_use("toolu_so_1", DELIVERED), tool_result("toolu_so_1"),
                           structured_use("toolu_so_2", {"profileId": "forged"}), tool_result("toolu_so_2")])
        self.assertEqual(collector.tool_calls, 0)
        verified = collector.settle_delivery(DELIVERED, ROOT)
        self.assertEqual(verified, (self.delivery_key("toolu_so_1"),))
        self.assertEqual(collector.tool_calls, 1)
        package = collector.finish(True, exclude_calls=verified)
        self.assertEqual((package["toolCalls"], [event["callId"] for event in package["events"]]),
                         (1, ["toolu_so_2", "toolu_so_2"]))


@unittest.skipUnless(run_seam("claude") is not None,
                     "the claude run seam is registered only in the activated verification copy")
class RunnerReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-claude-evidence-",
                                                dir=os.environ.get("BUDDY_CHECKS_TMPDIR", "/tmp"))
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
        return ExecutionContext(task_id="goal-1", attempt_id=f"attempt-{uuid.uuid4().hex[:8]}", generation=1,
                                spec={"cwd": str(self.cwd), "task": "Review the frozen copy", "timeoutSeconds": 20,
                                      "provider": "anthropic", "model": "claude-opus-5-5[1m]", "effort": "low"},
                                directory=self.root / "attempt-1", runtime={},
                                environment={**self.environment, "BUDDY_CLAUDE_FIXTURE_CASE": case}, turn=None)

    def run_read_only(self, case, *, tool_calls=8):
        request = ReadOnlyStructuredRequest(str(self.cwd), "Select from the frozen packet", answer_schema(["legal"]),
                                            {"timeoutSeconds": 20, "toolCalls": tool_calls})
        context = self.context(case)
        handle = start_review("claude", context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.2) if handle.group_alive() else None)
        self.assertIsNotNone(handle.wait(30), "the role-run controller did not exit")
        return context, collect(handle)

    def test_a_settled_receipt_finishes_evidence_after_the_observed_stream_close(self):
        context, outcome = self.run_read_only("clean")
        self.assertEqual(outcome.status, "ok", outcome.result)
        receipt = outcome.result
        session = receipt["nativeIdentity"]["sessionId"]
        uuid.UUID(session, version=4)
        self.assertEqual(receipt["usage"]["toolCalls"], 2)
        self.assertIsNone(receipt["usage"]["bytesRead"])
        self.assertIsInstance(receipt["usage"]["elapsedMs"], int)
        evidence = receipt["toolEvidence"]
        self.assertEqual(evidence["version"], 1)
        self.assertEqual(evidence["binding"], {"adapter": "claude", "taskId": "goal-1",
                                               "attemptId": context.attempt_id, "generation": 1})
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
        parent_identity = {"sessionId": outcome.result["nativeIdentity"]["sessionId"], "callId": "toolu_parent_9"}
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
