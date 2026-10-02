"""Unified tool-event evidence: pure normalization, collection and judgment.

No board, no state directory and no model call: every test drives the pure data
functions of ``buddy.tool_evidence`` directly, covering the fixed ACP
classification, the fact-only collection semantics (dedup, conflicts, the
128-event bound, late and foreign facts) and the single publication matrix.
"""
import unittest

from buddy import tool_evidence
from buddy.errors import BoardError

IDENTITY = {"sessionId": "session-1", "turnId": "turn-1"}
SECOND_IDENTITY = {"sessionId": "session-2", "inputId": "input-2"}
BINDING = {"adapter": "zcode", "taskId": "task-1", "attemptId": "attempt-1", "generation": 2}

CLASSIFICATION = {
    "dsh": {"read": "read", "glob": "search", "grep": "search"},
    "claude": {
        "Read": "read", "LS": "read", "Glob": "search", "Grep": "search", "Bash": "execute",
        "Write": "edit", "Edit": "edit", "MultiEdit": "edit", "WebFetch": "fetch", "WebSearch": "fetch",
    },
    "codex": {
        "shell": "execute", "code-mode": "execute", "exec": "execute", "commandExecution": "execute",
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
    evidence = tool_evidence.ToolEventEvidence(binding)
    for event in events:
        evidence.observe(event)
    return evidence.finish(list(roots if roots is not None else [IDENTITY]), stream_complete)


class NormalizeToolEventTests(unittest.TestCase):
    def test_each_harness_maps_its_native_tools_to_the_fixed_categories(self):
        for adapter, table in CLASSIFICATION.items():
            for tool, category in table.items():
                with self.subTest(adapter=adapter, tool=tool):
                    event = tool_evidence.normalize_tool_event(adapter, native_fact(tool))
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
        for adapter, tools in unregistered.items():
            for tool in tools:
                with self.subTest(adapter=adapter, tool=tool):
                    event = tool_evidence.normalize_tool_event(adapter, native_fact(tool))
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
            self.assertEqual(tool_evidence.judge_tool_evidence(collect([event]), "fast", True),
                             tool_evidence.TOOLS_FORBIDDEN)

    def test_name_and_type_disagreement_settles_as_other(self):
        disagreements = (
            ("claude", {"toolName": "Bash", "type": "read"}),
            ("codex", {"toolName": "shell", "type": "fileChange"}),
            ("zcode", {"toolName": "Read", "type": "read"}),
            ("dsh", {"toolName": "read", "type": "grep"}),
        )
        for adapter, fact in disagreements:
            with self.subTest(adapter=adapter, fact=fact):
                event = tool_evidence.normalize_tool_event(
                    adapter, {"nativeIdentity": dict(IDENTITY), "callId": "call-1", "phase": "start", **fact})
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
        for adapter, fact in invalid:
            with self.subTest(adapter=adapter, fact=fact):
                with self.assertRaises(BoardError):
                    tool_evidence.normalize_tool_event(adapter, fact)
        with self.assertRaises(BoardError) as caught:
            tool_evidence.normalize_tool_event("unknown", native_fact("read"))
        self.assertEqual(caught.exception.code, "UNSUPPORTED_ADAPTER")

    def test_arguments_bodies_and_model_claims_are_dropped(self):
        event = tool_evidence.normalize_tool_event("zcode", native_fact(
            "Read", arguments={"path": "/etc/passwd"}, content="file body", modelClaim="read-only"))
        self.assertEqual(event, {"nativeIdentity": dict(IDENTITY), "callId": "call-1",
                                 "toolName": "Read", "category": "read", "phase": "start"})


class ToolEventEvidenceTests(unittest.TestCase):
    def test_missing_native_ids_are_retained_without_inventing_a_call(self):
        tracker = tool_evidence.ToolEventEvidence(BINDING)
        tracker.observe_incomplete("codex", {"toolName": "shell", "phase": "start",
                                             "arguments": {"text": "private body"}})
        package = tracker.finish([IDENTITY], True)
        self.assertEqual(package["events"], [{"nativeIdentity": {}, "callId": None,
                         "toolName": "shell", "category": "execute", "phase": "start"}])
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_live_start_count_and_returned_snapshot_do_not_change_stored_facts(self):
        tracker = tool_evidence.ToolEventEvidence(BINDING)
        start, end = settled_events("read")
        tracker.observe(start)
        self.assertEqual(tracker.tool_calls, 1)
        tracker.observe(start)
        self.assertEqual(tracker.tool_calls, 1)
        tracker.observe(end)
        package = tracker.finish([IDENTITY], True)
        package["events"][0]["nativeIdentity"]["sessionId"] = "changed-by-caller"
        self.assertEqual(tracker.finish([IDENTITY], True)["events"][0]["nativeIdentity"], IDENTITY)

    def test_an_end_before_its_start_never_becomes_complete(self):
        package = collect(list(reversed(settled_events("read"))))
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_package_carries_exactly_the_fixed_fields(self):
        package = collect(settled_events("read"))
        self.assertEqual(set(package), tool_evidence.PACKAGE_FIELDS)
        self.assertEqual(package["version"], 1)
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [IDENTITY])
        self.assertTrue(package["streamComplete"])
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(package["unsettledToolCalls"], 0)
        self.assertFalse(package["truncated"])

    def test_a_settled_call_counts_once_and_raw_high_level_duplicates_collapse(self):
        events = settled_events("read") + settled_events("read") + settled_events("read")
        package = collect(events)
        self.assertEqual(len(package["events"]), 2)
        self.assertEqual(package["toolCalls"], 1)

    def test_conflicting_facts_for_one_call_are_kept_and_fail_the_judge(self):
        conflicting = settled_events("read") + settled_events("edit")
        package = collect(conflicting)
        self.assertEqual(len(package["events"]), 4)
        self.assertEqual(
            tool_evidence.judge_tool_evidence(package, "review", False),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_an_end_without_a_start_is_kept_but_counts_no_call(self):
        package = collect([settled_events("read")[1]])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(package["unsettledToolCalls"], 0)
        self.assertEqual(
            tool_evidence.judge_tool_evidence(package, "review", True),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_an_unsettled_call_is_counted(self):
        package = collect([settled_events("read")[0]])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 1))
        self.assertEqual(
            tool_evidence.judge_tool_evidence(package, "review", True),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_events_after_the_closed_stream_are_recorded_and_leave_it_incomplete(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING)
        for event in settled_events("read"):
            evidence.observe(event)
        package = evidence.finish([IDENTITY], True)
        self.assertTrue(package["streamComplete"])
        for event in settled_events("search", call_id="call-2"):
            evidence.observe(event)
        refreshed = evidence.finish([IDENTITY], True)
        self.assertEqual(len(refreshed["events"]), 4)
        self.assertFalse(refreshed["streamComplete"])
        self.assertEqual(
            tool_evidence.judge_tool_evidence(refreshed, "review", True),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_truncation_starts_beyond_the_retained_event_bound(self):
        full = []
        for index in range(64):
            full.extend(settled_events("read", call_id=f"call-{index}"))
        package = collect(full)
        self.assertEqual(len(package["events"]), tool_evidence.MAX_TOOL_EVENTS)
        self.assertEqual(package["toolCalls"], 64)
        self.assertFalse(package["truncated"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))
        full.extend(settled_events("read", call_id="call-64"))
        overflowing = collect(full)
        self.assertTrue(overflowing["truncated"])
        self.assertEqual(overflowing["toolCalls"], 65)
        self.assertEqual(
            tool_evidence.judge_tool_evidence(overflowing, "review", True),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_duplicates_do_not_consume_the_retention_bound(self):
        events = settled_events("read") * 200
        package = collect(events)
        self.assertEqual(len(package["events"]), 2)
        self.assertFalse(package["truncated"])

    def test_correction_roots_accumulate_within_one_package(self):
        events = settled_events("read") + settled_events(
            "search", call_id="call-2", identity=SECOND_IDENTITY, tool="grep")
        package = collect(events, roots=[IDENTITY, SECOND_IDENTITY])
        self.assertEqual(package["toolCalls"], 2)
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))

    def test_a_foreign_root_is_kept_but_not_trusted(self):
        foreign = settled_events("read", identity=SECOND_IDENTITY)
        package = collect(foreign, roots=[IDENTITY])
        self.assertEqual(len(package["events"]), 2)
        self.assertEqual(
            tool_evidence.judge_tool_evidence(package, "review", True),
            tool_evidence.TOOL_EVIDENCE_UNVERIFIED,
        )

    def test_binding_and_identity_shapes_are_validated(self):
        for binding in (None, {}, {"adapter": "zcode"}, {**BINDING, "generation": True},
                        {**BINDING, "extra": 1}, {**BINDING, "generation": -1}):
            with self.subTest(binding=binding):
                with self.assertRaises(BoardError):
                    tool_evidence.ToolEventEvidence(binding)
        with self.assertRaises(BoardError):
            tool_evidence.ToolEventEvidence(BINDING).finish(IDENTITY, True)

    def test_a_truncated_stream_cannot_be_completed_by_a_later_close(self):
        evidence = tool_evidence.ToolEventEvidence(BINDING)
        # A first close that reports an incomplete stream stays incomplete forever.
        first = evidence.finish([IDENTITY], False)
        second = evidence.finish([IDENTITY], True)
        self.assertFalse(first["streamComplete"])
        self.assertFalse(second["streamComplete"])


class JudgeToolEvidenceTests(unittest.TestCase):
    def test_fast_mode_requires_complete_zero_tool_evidence(self):
        self.assertIsNone(tool_evidence.judge_tool_evidence(collect([]), "fast", False))
        self.assertIsNone(tool_evidence.judge_tool_evidence(collect([]), "fast", True))
        forbidden = collect(settled_events("read"))
        self.assertEqual(tool_evidence.judge_tool_evidence(forbidden, "fast", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_review_with_a_system_sandbox_allows_read_search_and_execute(self):
        for category in ("read", "search", "execute"):
            with self.subTest(category=category):
                package = collect(settled_events(category))
                self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", True))

    def test_review_without_a_system_sandbox_allows_only_read_and_search(self):
        for category in ("read", "search"):
            with self.subTest(category=category):
                package = collect(settled_events(category))
                self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))
        self.assertEqual(
            tool_evidence.judge_tool_evidence(collect(settled_events("execute")), "review", False),
            tool_evidence.TOOLS_FORBIDDEN,
        )

    def test_every_clearly_disallowed_category_is_forbidden_in_review(self):
        for category in ("edit", "delete", "move", "fetch", "switch_mode", "think", "other"):
            for sandbox in (True, False):
                with self.subTest(category=category, sandbox=sandbox):
                    package = collect(settled_events(category))
                    self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", sandbox),
                                     tool_evidence.TOOLS_FORBIDDEN)

    def test_missing_or_malformed_evidence_is_unverified(self):
        broken = [
            None,
            {},
            {**collect(settled_events("read")), "version": 2},
            {**collect(settled_events("read")), "version": True},
            {**collect(settled_events("read")), "unexpected": 1},
            {key: value for key, value in collect(settled_events("read")).items() if key != "events"},
        ]
        for evidence in broken:
            with self.subTest(evidence=evidence):
                self.assertEqual(tool_evidence.judge_tool_evidence(evidence, "review", True),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        for mode, sandbox in ((None, True), ("quick", True), ("review", "yes"), ("fast", None)):
            with self.subTest(mode=mode, sandbox=sandbox):
                self.assertEqual(tool_evidence.judge_tool_evidence(collect([]), mode, sandbox),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_summary_fields_with_wrong_types_or_values_are_unverified(self):
        for change in ({"toolCalls": "1"}, {"toolCalls": True}, {"toolCalls": -1},
                       {"unsettledToolCalls": -1}, {"streamComplete": "yes"}, {"truncated": 1},
                       {"binding": {"adapter": "zcode"}}, {"nativeIdentity": IDENTITY},
                       {"events": settled_events("read")[0]}):
            with self.subTest(change=change):
                package = {**collect(settled_events("read")), **change}
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_counts_that_disagree_with_the_events_are_unverified(self):
        for change in ({"toolCalls": 2}, {"toolCalls": 0}, {"unsettledToolCalls": 1}):
            with self.subTest(change=change):
                package = {**collect(settled_events("read")), **change}
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_forbidden_recorded_call_outranks_stream_incompleteness(self):
        unsettled_edit = collect([settled_events("edit")[0]])
        self.assertEqual(tool_evidence.judge_tool_evidence(unsettled_edit, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)
        truncated_read = collect(settled_events("read"), stream_complete=False)
        self.assertEqual(tool_evidence.judge_tool_evidence(truncated_read, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        truncated_edit = collect(settled_events("edit"), stream_complete=False)
        self.assertEqual(tool_evidence.judge_tool_evidence(truncated_edit, "review", True),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_an_empty_foreign_identity_list_leaves_no_room_for_events(self):
        package = collect(settled_events("read"), roots=[])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", True),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        self.assertEqual(tool_evidence.judge_tool_evidence(collect([], roots=[]), "fast", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
