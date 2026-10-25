"""Unified tool evidence classification, bounded call files and judgment.

The check runner supplies a private state directory. No board or model call is
needed: the collector writes digest-bound call summaries for the judge to read.
"""
import os
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy import private_dirs
from hey_my_buddy.paths import native_path

STATE_DIR = Path(os.environ["HMB_STATE_DIR"])

IDENTITY = {"sessionId": "session-1", "turnId": "turn-1"}
SECOND_IDENTITY = {"sessionId": "session-2", "inputId": "input-2"}
BINDING = {"harness": "zcode", "taskId": "task-1", "attemptId": "attempt-1", "generation": 2}

CLASSIFICATION = {
    "dsh": {"read": "read", "glob": "search", "grep": "search", "bash": "execute",
            "write": "edit", "edit": "edit"},
    "claude": {
        "Read": "read", "LS": "read", "Skill": "read", "Glob": "search", "Grep": "search", "Bash": "execute",
        "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "WebFetch": "fetch", "WebSearch": "fetch",
    },
    "codex": {
        "shell": "execute", "code-mode": "execute", "exec": "execute", "commandExecution": "execute",
        "exec_command": "execute", "shell_command": "execute", "write_stdin": "execute",
        "local_shell_call": "execute",
        "fileChange": "edit", "web_search_call": "fetch",
    },
    "zcode": {
        "Read": "read", "Glob": "search", "Grep": "search", "Bash": "execute",
        "Write": "edit", "Edit": "edit", "WebFetch": "fetch",
    },
}


def native_fact(tool, call_id="call-1", phase="start", identity=None, **extra):
    return {"nativeIdentity": dict(identity or IDENTITY), "callId": call_id, "toolName": tool, "phase": phase, **extra}


def settled_events(category, call_id="call-1", identity=None, tool="native-tool"):
    identity = dict(identity or IDENTITY)
    return [
        {"nativeIdentity": identity, "callId": call_id, "toolName": tool, "category": category, "phase": "start"},
        {"nativeIdentity": identity, "callId": call_id, "toolName": tool, "category": category, "phase": "end"},
    ]


def collect(events, roots=None, stream_complete=True, binding=BINDING):
    evidence = tool_evidence.ToolEventEvidence(binding, state_dir=STATE_DIR)
    for event in events:
        evidence.observe(event)
    return evidence.finish(list(roots if roots is not None else [IDENTITY]), stream_complete)


def call_rows(package):
    return list(tool_evidence.read_tool_calls(package, state_dir=STATE_DIR))


class NormalizeToolEventTests(unittest.TestCase):
    def test_each_harness_maps_its_native_tools_to_the_fixed_categories(self):
        for harness, table in CLASSIFICATION.items():
            for tool, category in table.items():
                with self.subTest(harness=harness, tool=tool):
                    event = tool_evidence.normalize_tool_event(harness, native_fact(tool))
                    self.assertEqual(event["category"], category)
                    self.assertEqual(
                        set(event),
                        {"nativeIdentity", "callId", "toolName", "category", "phase"},
                    )

    def test_unregistered_tools_stay_other_and_never_borrow_a_legal_category(self):
        unregistered = {
            "dsh": ("Bash", "shell", "mcp__server__tool", "Task", "read_file"),
            "claude": ("read", "bash", "mcp__server__tool", "Task", "WebSearch".lower()),
            "codex": ("Read", "read", "mcp__server__tool", "apply_patch"),
            "zcode": ("LS", "MultiEdit", "WebSearch", "mcp__server__tool", "dispatch_agent"),
        }
        for harness, tools in unregistered.items():
            for tool in tools:
                with self.subTest(harness=harness, tool=tool):
                    event = tool_evidence.normalize_tool_event(harness, native_fact(tool))
                    self.assertEqual(event["category"], "other")

    def test_reasoning_text_is_not_a_tool_event(self):
        for fact in ({"type": "reasoning"}, {"type": "thinking"},
                     {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "type": "reasoning", "phase": "start"}):
            with self.subTest(fact=fact):
                self.assertIsNone(tool_evidence.normalize_tool_event("codex", fact))

    def test_named_calls_cannot_hide_as_reasoning_or_two_unknown_identifiers(self):
        for fact in (native_fact("reasoning"), native_fact("thinking"),
                     native_fact("unknown", type="another-unknown")):
            event = tool_evidence.normalize_tool_event("codex", fact)
            self.assertEqual(event["category"], "other")
            self.assertEqual(tool_evidence.judge_tool_evidence(collect([event]), "none", state_dir=STATE_DIR),
                             tool_evidence.TOOLS_FORBIDDEN)

    def test_name_and_type_disagreement_settles_as_other(self):
        disagreements = (
            ("claude", {"toolName": "Bash", "type": "read"}),
            ("codex", {"toolName": "shell", "type": "fileChange"}),
            ("zcode", {"toolName": "Read", "type": "read"}),
            ("dsh", {"toolName": "read", "type": "grep"}),
        )
        for harness, fact in disagreements:
            with self.subTest(harness=harness, fact=fact):
                event = tool_evidence.normalize_tool_event(
                    harness, {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "phase": "start", **fact})
                self.assertEqual(event["category"], "other")

    def test_one_identifier_alone_uses_its_own_mapping(self):
        by_type = tool_evidence.normalize_tool_event(
            "codex",
            {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "type": "commandExecution", "phase": "start"})
        self.assertEqual((by_type["category"], by_type["toolName"]), ("execute", "commandExecution"))
        by_name = tool_evidence.normalize_tool_event("claude", native_fact("Grep"))
        self.assertEqual((by_name["category"], by_name["toolName"]), ("search", "Grep"))

    def test_facts_that_cannot_form_an_event_are_rejected(self):
        invalid = (
            ("codex", {"nativeIdentity": dict(IDENTITY), "callId": "", "toolName": "shell", "phase": "start"}),
            ("codex", {"nativeIdentity": {}, "callId": "call-1", "toolName": "shell", "phase": "start"}),
            ("codex", {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "toolName": "shell", "phase": "running"}),
            ("codex", {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "phase": "start"}),
            ("codex", {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "toolName": "", "phase": "start"}),
            ("codex", {"nativeIdentity": {"sessionId": 4}, "callId": "call-1", "toolName": "shell", "phase": "start"}),
            ("dsh", None),
        )
        for harness, fact in invalid:
            with self.subTest(harness=harness, fact=fact):
                with self.assertRaises(BoardError):
                    tool_evidence.normalize_tool_event(harness, fact)
        with self.assertRaises(BoardError) as caught:
            tool_evidence.normalize_tool_event("unknown", native_fact("read"))
        self.assertEqual(caught.exception.code, "UNSUPPORTED_HARNESS")

    def test_arguments_bodies_and_model_claims_are_dropped(self):
        event = tool_evidence.normalize_tool_event("zcode", native_fact(
            "Read", arguments={"path": "/etc/passwd"}, content="file body", modelClaim="read-only"))
        self.assertEqual(event, {"nativeIdentity": dict(IDENTITY), "callId": "call-1",
                                 "toolName": "Read", "category": "read", "phase": "start"})


class ToolEventEvidenceTests(unittest.TestCase):
    def test_product_tool_path_over_260_characters_is_published_and_read_as_ordinary_path(self):
        state = STATE_DIR / ('long-state-' + 'a' * 50)
        binding = {**BINDING, 'taskId': 'f' * 36, 'attemptId': 'a' * 36}
        evidence = tool_evidence.ToolEventEvidence(binding, state_dir=state)
        for event in settled_events('read'):
            evidence.observe(event)
        try:
            package = evidence.finish([IDENTITY], True)
            reference = package['calls']
            self.assertGreater(len(reference['path']), 260)
            self.assertNotIn('\\\\?\\', reference['path'])
            self.assertEqual(len(list(tool_evidence.read_tool_calls(package, state_dir=state))), 1)
        finally:
            private_dirs.remove_tree(state)

    def test_verified_delivery_exclusion_keeps_a_foreign_id_collision_and_an_open_call(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        delivery = settled_events("other", call_id="shared", tool="mcp__session__finish")
        foreign = settled_events("execute", call_id="shared", identity=SECOND_IDENTITY, tool="Bash")
        opened = settled_events("execute", call_id="open", tool="Bash")[0]
        for event in delivery + foreign + [opened]:
            evidence.observe(event)
        package = evidence.finish([IDENTITY], True,
                                  exclude_calls={(canonical_json(IDENTITY), "shared")})
        rows = call_rows(package)
        self.assertEqual([(row["callId"], row["belongsToRoot"]) for row in rows],
                         [("shared", False), ("open", True)])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 1))
        self.assertEqual(evidence.tool_calls, 3, "live observations still include all starts")

    def test_delivery_exclusion_counts_all_calls_beyond_the_old_event_bound(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        for event in settled_events("other", call_id="delivery"):
            evidence.observe(event)
        for index in range(130):
            for event in settled_events("read", call_id=f"read-{index}"):
                evidence.observe(event)
        evidence.observe(settled_events("execute", call_id="open", tool="Bash")[0])
        package = evidence.finish([IDENTITY], True,
                                  exclude_calls={(canonical_json(IDENTITY), "delivery")})
        self.assertEqual(len(call_rows(package)), 131)
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (131, 1))

    def test_verified_delivery_cannot_hide_conflicting_or_incomplete_call_facts(self):
        for broken in ("conflict", "incomplete"):
            with self.subTest(broken=broken):
                evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
                for event in settled_events("other", call_id="delivery", tool="finish"):
                    evidence.observe(event)
                if broken == "conflict":
                    evidence.observe(settled_events("execute", call_id="delivery", tool="Bash")[0])
                else:
                    evidence.observe_incomplete("zcode", native_fact("finish", "delivery", phase=None))
                package = evidence.finish([IDENTITY], True,
                                          exclude_calls={(canonical_json(IDENTITY), "delivery")})
                self.assertEqual(len(call_rows(package)), 1)
                self.assertEqual(package["toolCalls"], 1)
                self.assertEqual(call_rows(package)[0]["incomplete"], broken == "incomplete")
                self.assertEqual(call_rows(package)[0]["consistent"], broken != "conflict")
                if broken == "incomplete":
                    self.assertFalse(package["streamComplete"])

    def test_delivery_exclusion_keeps_late_stream_facts_and_rejects_bare_ids(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        for event in settled_events("other", call_id="delivery"):
            evidence.observe(event)
        evidence.finish([IDENTITY], True)
        evidence.observe(settled_events("other", call_id="delivery")[1])
        package = evidence.finish([IDENTITY], True,
                                  exclude_calls={(canonical_json(IDENTITY), "delivery")})
        self.assertFalse(package["streamComplete"])
        for invalid in ({"delivery"}, [(IDENTITY, "delivery")], [(canonical_json(IDENTITY), None)]):
            with self.subTest(invalid=invalid):
                with self.assertRaises(BoardError):
                    evidence.finish([IDENTITY], True, exclude_calls=invalid)

    def test_settled_calls_after_a_native_turn_end_remain_late(self):
        tracker = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        tracker.close_root(IDENTITY)
        for event in settled_events("read"):
            tracker.observe(event)
        package = tracker.finish([IDENTITY], True)
        self.assertEqual(package["unsettledToolCalls"], 0)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_missing_native_ids_are_retained_without_inventing_a_call(self):
        tracker = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        tracker.observe_incomplete("codex", {"toolName": "shell", "phase": "start",
                                             "arguments": {"text": "private body"}})
        package = tracker.finish([IDENTITY], True)
        self.assertEqual(call_rows(package), [{
            "nativeIdentity": {}, "callId": None, "categories": ["execute"], "phases": ["start"],
            "facts": [{"toolName": "shell", "category": "execute"}],
            "consistent": True, "endBeforeStart": False, "incomplete": True,
            "malformed": True, "belongsToRoot": False,
        }])
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_live_start_count_and_returned_snapshot_do_not_change_stored_facts(self):
        tracker = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        start, end = settled_events("read")
        tracker.observe(start)
        self.assertEqual(tracker.tool_calls, 1)
        tracker.observe(start)
        self.assertEqual(tracker.tool_calls, 1)
        tracker.observe(end)
        package = tracker.finish([IDENTITY], True)
        package["nativeIdentity"][0]["sessionId"] = "changed-by-caller"
        self.assertEqual(tracker.finish([IDENTITY], True)["nativeIdentity"], [IDENTITY])
        self.assertEqual(call_rows(tracker.finish([IDENTITY], True))[0]["nativeIdentity"], IDENTITY)

    def test_an_end_before_its_start_never_becomes_complete(self):
        package = collect(list(reversed(settled_events("read"))))
        self.assertTrue(call_rows(package)[0]["endBeforeStart"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_package_carries_exactly_the_fixed_fields_and_digest_file(self):
        package = collect(settled_events("read"))
        self.assertEqual(set(package), tool_evidence.PACKAGE_FIELDS)
        self.assertEqual(package["version"], 2)
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [IDENTITY])
        self.assertTrue(package["streamComplete"])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertEqual(set(package["calls"]), {"path", "sha256", "bytes"})
        self.assertEqual(call_rows(package), [{
            "nativeIdentity": IDENTITY, "callId": "call-1", "categories": ["read"],
            "phases": ["end", "start"], "facts": [{"toolName": "native-tool", "category": "read"}],
            "consistent": True, "endBeforeStart": False,
            "incomplete": False, "malformed": False, "belongsToRoot": True,
        }])

    def test_a_settled_call_counts_once_and_raw_high_level_duplicates_collapse(self):
        package = collect(settled_events("read") * 3)
        self.assertEqual(len(call_rows(package)), 1)
        self.assertEqual(package["toolCalls"], 1)

    def test_conflicting_facts_for_one_call_are_kept_and_fail_the_judge(self):
        package = collect(settled_events("read") + settled_events("edit"))
        self.assertEqual(len(call_rows(package)), 1)
        self.assertEqual(call_rows(package)[0]["categories"], ["edit", "read"])
        self.assertFalse(call_rows(package)[0]["consistent"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_many_conflicting_names_cannot_grow_a_call_row(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        for index in range(1000):
            evidence.observe({**settled_events("read")[0], "toolName": f"dynamic-{index}"})
        evidence.observe(settled_events("read")[1])
        package = evidence.finish([IDENTITY], True)
        row = call_rows(package)[0]
        self.assertFalse(row["consistent"])
        self.assertNotIn("toolName", row)
        self.assertLess(package["calls"]["bytes"], tool_evidence.MAX_CALL_BYTES)

    def test_an_end_without_a_start_is_kept_but_counts_no_call(self):
        package = collect([settled_events("read")[1]])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (0, 0))
        self.assertEqual(call_rows(package)[0]["phases"], ["end"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_an_unsettled_call_is_counted(self):
        package = collect([settled_events("read")[0]])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 1))
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_events_after_the_closed_stream_are_recorded_and_leave_it_incomplete(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        for event in settled_events("read"):
            evidence.observe(event)
        self.assertTrue(evidence.finish([IDENTITY], True)["streamComplete"])
        for event in settled_events("search", call_id="call-2"):
            evidence.observe(event)
        refreshed = evidence.finish([IDENTITY], True)
        self.assertEqual(len(call_rows(refreshed)), 2)
        self.assertFalse(refreshed["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(refreshed, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_more_than_128_events_are_all_judged_without_a_count_cutoff(self):
        events = []
        for index in range(150):
            events.extend(settled_events("read", call_id=f"call-{index}"))
        package = collect(events)
        self.assertEqual(len(call_rows(package)), 150)
        self.assertEqual(package["toolCalls"], 150)
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR))

    def test_duplicates_do_not_add_call_rows(self):
        package = collect(settled_events("read") * 200)
        self.assertEqual(len(call_rows(package)), 1)
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR))

    def test_correction_roots_accumulate_within_one_package(self):
        events = settled_events("read") + settled_events(
            "search", call_id="call-2", identity=SECOND_IDENTITY, tool="grep")
        package = collect(events, roots=[IDENTITY, SECOND_IDENTITY])
        self.assertEqual(package["toolCalls"], 2)
        self.assertTrue(all(row["belongsToRoot"] for row in call_rows(package)))
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR))

    def test_a_foreign_root_is_kept_but_not_trusted(self):
        package = collect(settled_events("read", identity=SECOND_IDENTITY), roots=[IDENTITY])
        self.assertEqual(len(call_rows(package)), 1)
        self.assertFalse(call_rows(package)[0]["belongsToRoot"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_binding_and_identity_shapes_are_validated(self):
        for binding in (None, {}, {"harness": "zcode"}, {**BINDING, "generation": True},
                        {**BINDING, "extra": 1}, {**BINDING, "generation": -1}):
            with self.subTest(binding=binding):
                with self.assertRaises(BoardError):
                    tool_evidence.ToolEventEvidence(binding, state_dir=STATE_DIR)
        with self.assertRaises(BoardError):
            tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR).finish(IDENTITY, True)

    def test_an_incomplete_stream_cannot_be_completed_by_a_later_close(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        self.assertFalse(evidence.finish([IDENTITY], False)["streamComplete"])
        self.assertFalse(evidence.finish([IDENTITY], True)["streamComplete"])


class JudgeToolEvidenceTests(unittest.TestCase):
    def test_fast_mode_requires_complete_zero_tool_evidence(self):
        self.assertIsNone(tool_evidence.judge_tool_evidence(collect([]), "none", state_dir=STATE_DIR))
        self.assertEqual(tool_evidence.judge_tool_evidence(collect(settled_events("read")), "none", state_dir=STATE_DIR),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_read_level_allows_read_search_execute_and_network(self):
        for category in ("read", "search", "execute", "fetch"):
            with self.subTest(category=category):
                self.assertIsNone(tool_evidence.judge_tool_evidence(collect(settled_events(category)), "read", state_dir=STATE_DIR))

    def test_every_other_category_is_forbidden_at_read_level(self):
        for category in ("edit", "delete", "move", "switch_mode", "think", "other"):
            with self.subTest(category=category):
                self.assertEqual(tool_evidence.judge_tool_evidence(collect(settled_events(category)), "read", state_dir=STATE_DIR),
                                 tool_evidence.TOOLS_FORBIDDEN)

    def test_missing_or_malformed_evidence_is_unverified(self):
        good = collect(settled_events("read"))
        broken = [None, {}, {**good, "version": 1}, {**good, "version": True},
                  {**good, "unexpected": 1}, {key: value for key, value in good.items() if key != "calls"}]
        for evidence in broken:
            with self.subTest(evidence=evidence):
                self.assertEqual(tool_evidence.judge_tool_evidence(evidence, "read", state_dir=STATE_DIR),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        for mode in (None, "write", "full", "review", "fast", True, {}):
            with self.subTest(permission=mode):
                self.assertEqual(tool_evidence.judge_tool_evidence(good, mode, state_dir=STATE_DIR),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_summary_fields_with_wrong_types_or_values_are_unverified(self):
        good = collect(settled_events("read"))
        for change in ({"toolCalls": "1"}, {"toolCalls": True}, {"toolCalls": -1},
                       {"unsettledToolCalls": -1}, {"streamComplete": "yes"},
                       {"binding": {"harness": "zcode"}}, {"nativeIdentity": IDENTITY},
                       {"calls": {"path": "elsewhere"}}):
            with self.subTest(change=change):
                self.assertEqual(tool_evidence.judge_tool_evidence({**good, **change}, "read", state_dir=STATE_DIR),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_counts_that_disagree_with_the_call_file_are_unverified(self):
        good = collect(settled_events("read"))
        for change in ({"toolCalls": 2}, {"toolCalls": 0}, {"unsettledToolCalls": 1}):
            with self.subTest(change=change):
                self.assertEqual(tool_evidence.judge_tool_evidence({**good, **change}, "read", state_dir=STATE_DIR),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_conflicting_categories_remain_unverified_even_if_one_is_forbidden(self):
        package = collect(settled_events("read") + settled_events("edit"))
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_consistency_is_recomputed_from_bounded_native_facts(self):
        package = collect(settled_events("read"))
        row = call_rows(package)[0]
        forged = {**row, "categories": ["read", "edit"]}
        self.assertEqual(tool_evidence.judge_tool_evidence({**package, "calls": tool_evidence.write_tool_calls(package["binding"], [forged], state_dir=STATE_DIR)}, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        forged = {**row, "consistent": False}
        self.assertEqual(tool_evidence.judge_tool_evidence({**package, "calls": tool_evidence.write_tool_calls(package["binding"], [forged], state_dir=STATE_DIR)}, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_malformed_fact_outranks_a_forbidden_category_at_read_level(self):
        collector = tool_evidence.ToolEventEvidence(BINDING, state_dir=STATE_DIR)
        collector.observe_incomplete("zcode", {"toolName": "Write", "phase": "start"})
        package = collector.finish([IDENTITY], False)
        self.assertEqual(call_rows(package)[0]["categories"], ["edit"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_forbidden_recorded_call_outranks_stream_incompleteness(self):
        self.assertEqual(tool_evidence.judge_tool_evidence(collect([settled_events("edit")[0]]), "read", state_dir=STATE_DIR),
                         tool_evidence.TOOLS_FORBIDDEN)
        self.assertEqual(tool_evidence.judge_tool_evidence(
            collect(settled_events("read"), stream_complete=False), "read", state_dir=STATE_DIR),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        self.assertEqual(tool_evidence.judge_tool_evidence(
            collect(settled_events("edit"), stream_complete=False), "read", state_dir=STATE_DIR),
            tool_evidence.TOOLS_FORBIDDEN)

    def test_an_empty_root_list_cannot_establish_any_evidence(self):
        self.assertEqual(tool_evidence.judge_tool_evidence(
            collect(settled_events("read"), roots=[]), "read", state_dir=STATE_DIR),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        self.assertEqual(tool_evidence.judge_tool_evidence(collect([], roots=[]), "none", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_digest_tampering_is_rejected(self):
        package = collect(settled_events("read", call_id="digest-tamper-unique"))
        path = Path(package["calls"]["path"])
        original = native_path(path).read_bytes()
        try:
            changed = original.replace(b"digest-tamper-unique", b"digest-tamper-forged")
            self.assertEqual(len(changed), len(original))
            self.assertNotEqual(changed, original)
            native_path(path).write_bytes(changed)
            self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                             tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        finally:
            native_path(path).write_bytes(original)

    def test_oversized_call_row_is_rejected(self):
        good = collect(settled_events("read"))
        oversized = {**call_rows(good)[0], "padding": "x" * tool_evidence.MAX_CALL_BYTES}
        package = {**good, "calls": tool_evidence.write_tool_calls(BINDING, [oversized], state_dir=STATE_DIR)}
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_foreign_attempt_file_reference_is_rejected(self):
        good = collect(settled_events("read"))
        foreign_binding = {**BINDING, "attemptId": "attempt-foreign"}
        foreign = collect(settled_events("read", call_id="foreign-ref"), binding=foreign_binding)
        package = {**good, "calls": foreign["calls"]}
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    @unittest.skipIf(os.name == "nt", "symlink creation requires Windows privilege")
    def test_foreign_attempt_file_link_is_rejected(self):
        good = collect(settled_events("read"))
        foreign_binding = {**BINDING, "attemptId": "attempt-linked"}
        foreign = collect(settled_events("read", call_id="foreign-link"), binding=foreign_binding)
        target = Path(foreign["calls"]["path"])
        expected = Path(good["calls"]["path"]).with_name(target.name)
        expected.symlink_to(target)
        try:
            package = {**good, "calls": {**foreign["calls"], "path": str(expected)}}
            self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                             tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        finally:
            expected.unlink()

    def test_invalid_call_rows_and_forged_root_membership_are_rejected(self):
        good = collect(settled_events("read"))
        row = call_rows(good)[0]
        for rows in ([{**row, "extra": True}], [{**row, "belongsToRoot": False}], [row, row]):
            with self.subTest(rows=rows):
                package = {**good, "calls": tool_evidence.write_tool_calls(BINDING, rows, state_dir=STATE_DIR)}
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=STATE_DIR),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)


class ExplicitStateRootTests(unittest.TestCase):
    def test_collection_without_an_environment_root_only_writes_the_explicit_private_root(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-tool-root-") as directory, ExitStack() as cleanup:
            cleanup.callback(private_dirs.remove_tree, Path(directory))
            root = Path(directory)
            state_dir = root / "selected"
            with mock.patch.dict(os.environ), mock.patch(
                    "hey_my_buddy.home.default_state_dir", side_effect=AssertionError("implicit state root")):
                os.environ.pop("HMB_STATE_DIR", None)
                collector = tool_evidence.ToolEventEvidence(BINDING, state_dir=state_dir)
                for event in settled_events("read"):
                    collector.observe(event)
                package = collector.finish([IDENTITY], True)
                self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=state_dir))
            self.assertTrue(Path(package["calls"]["path"]).is_relative_to(state_dir))
            self.assertEqual(list(root.iterdir()), [state_dir])
            self.assertEqual(len(list(native_path(state_dir).rglob("*.jsonl"))), 1)

    def test_another_state_root_cannot_adopt_a_valid_evidence_reference(self):
        with tempfile.TemporaryDirectory(prefix="hey-my-buddy-tool-root-") as directory, ExitStack() as cleanup:
            cleanup.callback(private_dirs.remove_tree, Path(directory))
            selected = Path(directory) / "selected"
            other = Path(directory) / "other"
            collector = tool_evidence.ToolEventEvidence(BINDING, state_dir=selected)
            for event in settled_events("read"):
                collector.observe(event)
            package = collector.finish([IDENTITY], True)
            self.assertIsNone(tool_evidence.judge_tool_evidence(package, "read", state_dir=selected))
            with mock.patch.object(tool_evidence.private_dirs, "open_regular_fd", side_effect=AssertionError("foreign read")):
                with self.assertRaises(BoardError):
                    list(tool_evidence.read_tool_calls(package, state_dir=other))
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "read", state_dir=other),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
            self.assertFalse(other.exists())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
