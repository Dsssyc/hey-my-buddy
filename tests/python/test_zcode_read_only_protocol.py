"""L6-A restricted read-only protocol: parameters, contract, evidence, budgets.

No paid model calls: the fixed API runs against a fake app-server subprocess
through the real NativeConnection and against a minimal in-process fake
connection, and the contract helper is checked against a synthetic mini-bundle
whose minified identifiers differ from the real one, plus the public installed
text when present. Wiring the runner branch, the adapter entry and the owned
group stop is L6-B and is deliberately absent here.
"""
from __future__ import annotations

import collections
import json
import math
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

from buddy import tool_evidence
from buddy.adapters.zcode_config import DEFAULT_CLI
from buddy.adapters.zcode_protocol import NativeConnection, NativeError
from buddy.adapters.zcode_read_only import (MAX_ANSWER_BYTES, ReadOnlyEvidence, native_contract_problem,
                                            read_only_call, session_parameters)
from buddy.adapters.zcode_tool_evidence import ZcodeToolFacts

FIXTURE = Path(__file__).parent / "fixtures" / "mock_zcode_read_only.py"
SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}
BINDING = {"adapter": "zcode", "taskId": "task-1", "attemptId": "attempt-1", "generation": 3}
SPEC = {"provider": "fixture-api", "model": "fixture-model", "effort": "low"}
CWD = "/private/frozen-copy"
WORKSPACE = {"workspacePath": CWD, "workspaceKey": CWD}

#: The strict session/create schema every synthetic bundle shares.
BUNDLE_SCHEMA = (
    'var Qm=m.enum(["build","plan","edit","yolo","auto"]),'
    'kR=m.object({sessionId:Dn.optional(),workspace:rd,parentSessionId:Dn.optional(),mode:Qm.optional(),'
    'model:Pu.optional(),titleGenerationEnabled:m.boolean().optional(),mcpServers:m.array(Ype).optional(),'
    'toolAllowlist:m.array(Dn).optional(),toolDenylist:m.array(Dn).optional(),'
    'offPeakToolEnabled:m.boolean().optional(),dynamicWorkflowEnabled:m.boolean().optional()}).strict();'
)
#: The three read-only registrations every synthetic bundle carries.
BUNDLE_TOOLS = (
    'var readTool={capability:"Read files",metadata:{name:"Read",readOnly:!0}};'
    'var globTool={metadata:{name:"Glob",readOnly:!0}};'
    'var grepTool={metadata:{name:"Grep",readOnly:!0}};'
)
#: The installed mechanism under fresh minified names: the short-circuit OR
#: registration chain with the disallowed-Set builder, the agent-name
#: predicate and a constant set, the tool transform on the register argument,
#: and the alias-map/explore-filter/root-child resolver chain.
TRANSFORM_HELPERS = (
    'function fO(e,t){return e.metadata.name==="Bash"?bR({bashTimeoutPolicy:t.bashTimeoutPolicy})'
    ':e.metadata.name===sK&&t.submitResultSchema!==void 0?sU(t.submitResultSchema)'
    ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})'
    ':e}'
    'function fT(e){let t=nL(e.toolAllowlist);'
    'return e.toolset!=="explore"?aC(e,t):t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}'
    'function nL(e){return e?.map(v=>aM(v))}'
    'function aM(v){return v==="web_search"?"WebSearch":v}'
    'function aC(e,t){return t&&(e.taskType==="subagent_child"?t.includes(rC)?t:[...t,rC]:t)}'
    'var eS=new Set(dL),dL=["Bash","Glob","Grep","Read","WebFetch","WebSearch","TodoWrite"],'
    'rC="RespondToCoordinator",sK="submit_result",wS=new Set(["Agent","Task"]),nS=new Set(["Agent","Task"]);'
    'function fD(e){if(!e||e.length===0)return;let t=new Set;'
    'for(let n of e){let o=nZ(n);o&&t.add(o)}return t.size>0?t:void 0}'
    'function nZ(e){let t=e.trim(),n=t.indexOf("("),o=n>0?t.slice(0,n):t;return aM(o)}'
    'function gA(e){return e?nS.has(e):!1}'
)
BUNDLE_MAPPING = 'r(Uw,"registerBuiltInTools");r(fT,"resolveBuiltInToolAllowlist");'
#: The refresh call site: a direct options object whose unique allowedTools
#: value is the verified resolver called on config, like the installation.
BUNDLE_CALLSITE = (
    'function refresh(e){Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,'
    'includeSkill:!!e.skillPort,embeddedSearchEnabled:!1,allowedTools:fT(e.config),'
    'disallowedTools:e.config.toolDisallowlist,silentDuplicateWarnings:!0})}'
)
#: The installed short-circuit OR flow: every rejection operand verified, the
#: membership rejection one complete operand, the register call last.
GOOD_BUNDLE = (BUNDLE_SCHEMA
               + 'function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0,'
                 'o=fD(t.disallowedTools);'
                 'for(let s of builtins)'
                 't.embeddedSearchEnabled===!0&&(s.metadata.name==="Glob"||s.metadata.name==="Grep")'
                 '||n&&!n.has(s.metadata.name)'
                 '||o?.has(s.metadata.name)'
                 '||gA(s.metadata.name)&&t.includeAgent!==!0'
                 '||s.metadata.name==="Skill"&&t.includeSkill===!1'
                 '||t.includeDynamicWorkflow===!1&&wS.has(s.metadata.name)'
                 '||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})}'
               + TRANSFORM_HELPERS + BUNDLE_MAPPING + BUNDLE_CALLSITE + BUNDLE_TOOLS)
#: The same mechanism in the supported in-loop positive if form.
GOOD_IF_BUNDLE = (BUNDLE_SCHEMA
                  + 'function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0,'
                    'o=fD(t.disallowedTools);'
                    'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))}'
                  + TRANSFORM_HELPERS + BUNDLE_MAPPING + BUNDLE_CALLSITE + BUNDLE_TOOLS)
#: The minimal OR chain under wholly $-renamed identifiers with the direct
#: same-value resolver: the second supported registration and resolver shape.
GOOD_OR_CHAIN_BUNDLE = (BUNDLE_SCHEMA
                       + 'function $r($e,$t={}){let $n=$t.allowedTools?new Set($t.allowedTools):void 0;'
                         'for(let $s of $l)'
                         '$t.includeSkill===!1&&$s.metadata.name==="Skill"'
                         '||$n&&!$n.has($s.metadata.name)'
                         '||$t.includeNodeRepl!==!0&&$s.metadata.name==="js"'
                         '||$e.register($s)}'
                       + 'function $f(e){let t=e.toolAllowlist;return t}'
                       + 'r($r,"registerBuiltInTools");r($f,"resolveBuiltInToolAllowlist");'
                       + 'function refresh(e){$r(e.registry,{allowedTools:$f(e.config)})}'
                       + BUNDLE_TOOLS)
#: The same minimal if-form under renamed identifiers: names are never fixed.
RENAMED_IF_BUNDLE = (BUNDLE_SCHEMA
                     + 'function $w($e,$t={}){let $n=$t.allowedTools?new Set($t.allowedTools):void 0;'
                       'for(let $s of builtins)if(!$n||$n.has($s.metadata.name))$e.register($s)}'
                     + 'function $f(e){return e.toolAllowlist}'
                     + 'r($w,"registerBuiltInTools");r($f,"resolveBuiltInToolAllowlist");'
                     + 'function refresh(e){$w(e.registry,{allowedTools:$f(e.config)})}'
                     + BUNDLE_TOOLS)


class SessionParameterTests(unittest.TestCase):
    def test_the_fixed_restricted_parameters_never_inherit_a_user_mode(self):
        workspace = {"workspacePath": "/frozen", "workspaceKey": "/frozen"}
        self.assertEqual(session_parameters(workspace), {
            "workspace": workspace, "mode": "plan", "titleGenerationEnabled": False,
            "toolAllowlist": ["Read", "Glob", "Grep"], "mcpServers": [],
            "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False})

    def test_each_call_gets_a_fresh_tool_list(self):
        first = session_parameters({})
        first["toolAllowlist"].append("Bash")
        self.assertEqual(session_parameters({})["toolAllowlist"], ["Read", "Glob", "Grep"])


class ContractProblemTests(unittest.TestCase):
    """The helper identifies mechanisms; mutating any one yields its own reason."""

    def test_the_well_formed_mechanism_set_is_accepted(self):
        for label, bundle in (("installed-or-form", GOOD_BUNDLE), ("installed-if-form", GOOD_IF_BUNDLE),
                              ("minimal-or-chain", GOOD_OR_CHAIN_BUNDLE), ("renamed-if", RENAMED_IF_BUNDLE)):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(bundle))

    def test_registration_control_flows_bind_or_fail_closed(self):
        """Only the two complete flows qualify; near-misses carry their reason."""
        for label, mutated, reason in (
            ("inverted-if-membership",
             GOOD_IF_BUNDLE.replace("if(!n||n.has(s.metadata.name))", "if(n&&!n.has(s.metadata.name))"),
             "unrecognized control flow"),
            ("inverted-chain-member",
             GOOD_BUNDLE.replace("n&&!n.has(s.metadata.name)", "n&&n.has(s.metadata.name)"),
             "unrecognized control flow"),
            ("unconditional-register",
             GOOD_IF_BUNDLE.replace("if(!n||n.has(s.metadata.name))", ""),
             "unrecognized control flow"),
            ("register-not-chain-final",
             GOOD_BUNDLE.replace("||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})}",
                                 "||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})||x}"),
             "unrecognized control flow"),
            ("register-outside-the-flow",
             GOOD_IF_BUNDLE.replace("e.register(fO(s,t))}", "e.register(fO(s,t));e.register(s)}"),
             "outside the one allowlist-gated flow"),
            ("membership-on-another-set",
             GOOD_IF_BUNDLE.replace("if(!n||n.has(s.metadata.name))", "if(!q||q.has(s.metadata.name))"),
             "unrecognized control flow"),
            ("set-not-from-the-options-parameter",
             GOOD_BUNDLE.replace("new Set(t.allowedTools)", "new Set(globalAllow)"),
             "no Set membership filter"),
            ("membership-on-another-variable",
             GOOD_IF_BUNDLE.replace("n.has(s.metadata.name)", "n.has(other.metadata.name)"),
             "unrecognized control flow"),
            ("register-on-a-foreign-registry",
             GOOD_IF_BUNDLE.replace("e.register(fO(s,t))}", "k.register(fO(s,t))}"),
             "own registry parameters"),
            ("register-on-two-registries",
             GOOD_IF_BUNDLE.replace("e.register(fO(s,t))}", "e.register(fO(s,t));k.register(s)}"),
             "own registry parameters"),
            ("unknown-loop-structure",
             GOOD_IF_BUNDLE.replace("for(let s of builtins)", "builtins.forEach(function(s)"),
             "for-of loop"),
            ("bypassed-resolver-call",
             GOOD_BUNDLE.replace("function refresh(e){Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,"
                                 "includeSkill:!!e.skillPort,embeddedSearchEnabled:!1,allowedTools:fT(e.config),"
                                 "disallowedTools:e.config.toolDisallowlist,silentDuplicateWarnings:!0})}",
                                 "function refresh(e){Uw(e.registry,{embeddedSearchEnabled:!1});"
                                 "wire(e.registry,{allowedTools:fT(e.config)})}"),
             "not called with allowedTools from resolveBuiltInToolAllowlist"),
            ("decoy-resolver-named",
             GOOD_BUNDLE.replace('r(Uw,"registerBuiltInTools");r(fT,"resolveBuiltInToolAllowlist");',
                                 'function fG(e){return null}'
                                 'r(Uw,"registerBuiltInTools");r(fG,"resolveBuiltInToolAllowlist");'),
             "does not read the config toolAllowlist"),
        ):
            with self.subTest(label=label):
                problem = native_contract_problem(mutated)
                self.assertIsNotNone(problem)
                self.assertIn(reason, problem)

    def test_mutations_fail_with_their_specific_reason(self):
        for label, mutated, reason in (
            ("no-strict-schema", GOOD_BUNDLE.replace("}).strict()", "}"), "no strict session/create schema"),
            ("missing-field", GOOD_BUNDLE.replace(",toolAllowlist:m.array(Dn).optional()", ""),
             "no strict session/create schema"),
            ("no-plan", GOOD_BUNDLE.replace('"plan",', ""), "does not offer plan"),
            ("unnamed-register", GOOD_BUNDLE.replace('r(Uw,"registerBuiltInTools")', 'r(Uw,"registerTools")'),
             "does not name registerBuiltInTools"),
            ("no-set-filter", GOOD_BUNDLE.replace("new Set(t.allowedTools)", "t.allowedTools"),
             "Set membership filter"),
            ("no-name-membership", GOOD_BUNDLE.replace("n.has(s.metadata.name)", "!0"),
             "Set membership filter"),
            ("no-allowlist-read", GOOD_BUNDLE.replace("e.toolAllowlist", "e.toolDenylist"),
             "does not read the config toolAllowlist"),
            ("no-call-chain", GOOD_BUNDLE.replace("allowedTools:fT(e.config),", ""),
             "not called with allowedTools from resolveBuiltInToolAllowlist"),
            ("missing-grep", GOOD_BUNDLE.replace('var grepTool={metadata:{name:"Grep",readOnly:!0}};', ""),
             "Grep built-in tool is not registered"),
            ("not-read-only", GOOD_BUNDLE.replace('metadata:{name:"Read",readOnly:!0}',
                                                  'metadata:{name:"Read",readOnly:!1}'),
             "Read built-in tool is not registered as read-only"),
        ):
            with self.subTest(label=label):
                problem = native_contract_problem(mutated)
                self.assertIsNotNone(problem)
                self.assertIn(reason, problem)

    def test_a_bare_mention_or_empty_text_proves_nothing(self):
        for label, text, reason in (("mention-only", "toolAllowlist:m.array(Dn)", "no strict session/create schema"),
                                    ("none", None, "no public CLI bundle text was provided"),
                                    ("bytes", b"toolAllowlist", "no public CLI bundle text was provided"),
                                    ("blank", "  \n", "no public CLI bundle text was provided")):
            with self.subTest(label=label):
                self.assertIn(reason, native_contract_problem(text))


@unittest.skipUnless(DEFAULT_CLI.is_file(), "the public ZCode installation is required")
class InstalledBundleContractTests(unittest.TestCase):
    def test_the_public_install_carries_every_read_only_mechanism(self):
        self.assertIsNone(native_contract_problem(DEFAULT_CLI.read_text(errors="replace")))


class ReadOnlyEvidenceTests(unittest.TestCase):
    """The canonical no-tool rules with real tool frames and the run budget."""

    def evidence(self, budget=8):
        facts = ZcodeToolFacts(dict(BINDING))
        return ReadOnlyEvidence("s-1", "input", facts, budget), facts

    def event(self, kind, payload=None, *, session="s-1", turn="t-1", seq=2):
        return {"method": "session/event", "params": {"sessionId": session, "turnId": turn,
                "seq": seq, "type": kind, "payload": payload or {}}}

    def start(self, evidence):
        evidence.observe(self.event("turn.started", {"inputId": "input"}, seq=1), 1)

    def settle(self, evidence, *, answer='{"choice":"a"}', seq=4):
        evidence.observe(self.event("turn.completed", {"inputId": "input", "resultType": "success",
                                       "response": answer}, seq=seq), 5)
        evidence.observe({"method": "state.updated", "params": {"sessionId": "s-1",
                          "reason": "prompt_completed"}}, 6)

    def test_real_frames_pair_and_the_judge_accepts_the_review_facts(self):
        evidence, facts = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", {"kind": "scheduled", "toolCallId": "c-1",
                                     "toolName": "Read"}), 2)
        evidence.observe(self.event("tool.updated", {"kind": "result", "toolCallId": "c-1"}, seq=3), 3)
        self.settle(evidence)
        self.assertTrue(evidence.settled)
        self.assertEqual(facts.roots, [{"sessionId": "s-1", "turnId": "t-1"}])
        package = facts.finish(True)
        self.assertEqual(package["toolCalls"], 1)
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))

    def test_a_foreign_tool_frame_passes_protocol_and_stays_a_fact(self):
        evidence, facts = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", {"kind": "scheduled", "toolCallId": "c-f",
                                     "toolName": "Glob", "source": "sub-agent"},
                                    session="s-child", turn="t-child"), 2)
        self.settle(evidence, seq=3)
        self.assertTrue(evidence.settled)
        package = facts.finish(True)
        self.assertEqual(package["events"][0]["nativeIdentity"], {"sessionId": "s-child", "turnId": "t-child"})
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_current_root_tool_frames_carry_the_monotonic_sequence(self):
        scheduled = {"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}
        result = {"kind": "result", "toolCallId": "c-1"}
        # No integer sequence on a root tool frame fails after its fact lands.
        evidence, facts = self.evidence()
        self.start(evidence)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("tool.updated", scheduled, seq=None), 2)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        self.assertEqual(len(facts.finish(False)["events"]), 1)
        # A descending frame is a regression ...
        evidence, _ = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", scheduled, seq=3), 2)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("tool.updated", result, seq=2), 3)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        # ... and so is a repeated sequence number.
        evidence, _ = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", scheduled, seq=3), 2)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("tool.updated", result, seq=3), 3)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        # A root tool frame advances last_seq: a later canonical frame cannot
        # regress below it.
        evidence, _ = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", scheduled, seq=5), 2)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("turn.completed", {"inputId": "input", "resultType": "success",
                                           "response": "{}"}, seq=4), 3)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        # Legal read/search pairs on the root still pass after the discipline.
        evidence, facts = self.evidence()
        self.start(evidence)
        evidence.observe(self.event("tool.updated", {"kind": "scheduled", "toolCallId": "c-2",
                                     "toolName": "Grep"}), 2)
        evidence.observe(self.event("tool.updated", {"kind": "result", "toolCallId": "c-2"}, seq=3), 3)
        self.settle(evidence)
        self.assertTrue(evidence.settled)
        self.assertIsNone(tool_evidence.judge_tool_evidence(facts.finish(True), "review", False))

    def test_unknown_tool_and_agent_kinds_fail_closed_with_facts_kept(self):
        for kind in ("tool.started", "agent.started"):
            with self.subTest(kind=kind):
                evidence, facts = self.evidence()
                self.start(evidence)
                with self.assertRaises(NativeError) as caught:
                    evidence.observe(self.event(kind, {}, seq=2), 2)
                self.assertEqual(caught.exception.code, "invalid-protocol")
                self.assertFalse(evidence.settled)

    def test_unknown_frames_and_bad_sequence_fail_after_projection(self):
        evidence, facts = self.evidence()
        self.start(evidence)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("future.event", {}, seq=2), 2)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("turn.completed", {"inputId": "input", "resultType": "success",
                                         "response": "{}"}, seq=1), 3)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        self.assertEqual(facts.finish(False)["events"], [])

    def test_wrong_input_or_turn_identity_is_refused(self):
        evidence, _ = self.evidence()
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("turn.started", {"inputId": "other"}, seq=1), 1)
        self.assertEqual(caught.exception.code, "wrong-native-turn")
        evidence, _ = self.evidence()
        self.start(evidence)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("message.upserted", {"message": {"parts": []}},
                                        turn="t-other", seq=2), 2)
        self.assertEqual(caught.exception.code, "wrong-native-turn")

    def test_completion_bounds_the_answer_bytes(self):
        for answer, ok in ((None, False), ("x" * (MAX_ANSWER_BYTES + 1), False),
                           ("x" * MAX_ANSWER_BYTES, True)):
            with self.subTest(length=len(answer) if answer else 0):
                evidence, _ = self.evidence()
                self.start(evidence)
                if ok:
                    self.settle(evidence, answer=answer, seq=4)
                    self.assertEqual(evidence.raw_answer, answer)
                else:
                    with self.assertRaises(NativeError) as caught:
                        evidence.observe(self.event("turn.completed", {"inputId": "input",
                                                     "resultType": "success", "response": answer}, seq=4), 5)
                    self.assertEqual(caught.exception.code, "invalid-native-result")

    def test_telemetry_and_state_never_settle_without_the_canonical_turn(self):
        evidence, _ = self.evidence()
        with self.assertRaises(NativeError) as caught:
            evidence.observe({"method": "state.updated", "params": {"sessionId": "s-1",
                              "reason": "prompt_completed"}}, 1)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        evidence, _ = self.evidence()
        self.assertFalse(evidence.settled)
        evidence.observe_metadata("computer-use/operation-event", {
            "sessionId": "s-1", "turnId": "t-1", "kind": "turn-completed", "sequenceNumber": 1})
        self.assertFalse(evidence.settled)
        self.assertFalse(evidence.completed)

    def test_metadata_turn_failure_and_identity_are_enforced(self):
        evidence, _ = self.evidence()
        evidence.observe_metadata("computer-use/operation-event", {
            "sessionId": "s-1", "turnId": "t-1", "kind": "turn-started", "sequenceNumber": 1})
        with self.assertRaises(NativeError) as caught:
            evidence.observe_metadata("computer-use/operation-event", {
                "sessionId": "s-1", "turnId": "t-1", "kind": "turn-failed", "sequenceNumber": 2})
        self.assertEqual(caught.exception.code, "native-turn-failed")
        evidence, _ = self.evidence()
        with self.assertRaises(NativeError) as caught:
            evidence.observe_metadata("v4/telemetry/event", {
                "sessionId": "s-child", "turnId": "t-child", "kind": "turn.started", "eventSeq": 1})
        self.assertEqual(caught.exception.code, "invalid-protocol")

    def test_the_run_budget_trips_on_any_projected_start(self):
        for budget, frames, trips in ((0, [("c-1", "Read")], True), (1, [("c-1", "Read")], False),
                                      (1, [("c-1", "Read"), ("c-2", "Glob")], True)):
            with self.subTest(budget=budget, frames=len(frames)):
                evidence, facts = self.evidence(budget)
                self.start(evidence)
                error = None
                for index, (call_id, name) in enumerate(frames):
                    try:
                        evidence.observe(self.event("tool.updated", {"kind": "scheduled",
                                                     "toolCallId": call_id, "toolName": name},
                                                    seq=2 + index), 2 + index)
                    except NativeError as failure:
                        error = failure
                if trips:
                    self.assertEqual(error.code, "readonly-budget-exhausted")
                else:
                    self.assertIsNone(error)
                self.assertEqual(facts.finish(False)["toolCalls"], len(frames))


class FakeConnection:
    """Minimal in-process connection stand-in with canned results and feeds."""

    def __init__(self, results=None, pushes=None):
        self.results = {name: list(items) for name, items in (results or {}).items()}
        self.pushes = {name: list(items) for name, items in (pushes or {}).items()}
        self.feed = collections.deque()
        self.calls = []
        self.deadline = math.inf
        self.cancelled = threading.Event()
        self.observe = lambda message, ordinal: None

    def call(self, method, params):
        self.calls.append((method, params))
        queue = self.results.get(method)
        outcome = queue.pop(0) if queue else NativeError("native-rpc-error", f"{method} refused")
        if isinstance(outcome, Exception):
            raise outcome
        pending = self.pushes.get(method)
        if pending:
            pushed = pending.pop(0)
            self.feed.extend(pushed(params) if callable(pushed) else pushed)
        return outcome

    def pump(self):
        if self.cancelled.is_set():
            raise NativeError("cancelled", "the owned execution was cancelled")
        if self.deadline - time.monotonic() <= 0:
            raise NativeError("timeout", "the owned execution exceeded its deadline")
        if not self.feed:
            raise NativeError("native-disconnected", "the fake stream ended without settlement")
        self.observe(self.feed.popleft(), 1)


def snapshot(session_id="s-1", cwd=CWD, parent=None):
    return {"session": {"sessionId": session_id, "parentSessionId": parent,
                        "workspace": {"workspacePath": cwd}},
            "settings": {"model": {"available": [
                {"ref": {"providerId": "fixture-api", "modelId": "fixture-model"},
                 "reasoning": {"levels": [{"value": "low"}]}}]},
                "thoughtLevel": {"current": "low"}}}


def configured():
    return {"session": {"sessionId": "s-1", "workspace": {"workspacePath": CWD}},
            "settings": {"model": {"current": {"providerId": "fixture-api", "modelId": "fixture-model",
                                               "options": {"reasoningLevel": "low"}}},
                         "thoughtLevel": {"current": "low"}}}


def round_events(answer, tools=(), *, session="s-1", turn="t-1"):
    def build(params):
        messages = [{"method": "session/event", "params": {"sessionId": session, "turnId": turn,
                    "seq": 1, "type": "turn.started", "payload": {"inputId": params["inputId"]}}}]
        seq = 2
        for call_id, name in tools:
            for kind in ({"kind": "scheduled", "toolCallId": call_id, "toolName": name},
                         {"kind": "result", "toolCallId": call_id}):
                messages.append({"method": "session/event", "params": {"sessionId": session,
                                "turnId": turn, "seq": seq, "type": "tool.updated", "payload": kind}})
                seq += 1
        messages.append({"method": "session/event", "params": {"sessionId": session, "turnId": turn,
                         "seq": seq, "type": "turn.completed",
                         "payload": {"inputId": params["inputId"], "resultType": "success",
                                     "response": answer}}})
        messages.append({"method": "state.updated", "params": {"sessionId": session,
                                                              "reason": "prompt_completed"}})
        return messages
    return build


def subscribe_report(session_id="s-1", *, event_seq=0, events=None):
    """The native subscribe handshake shape for one root session."""
    return {"sessionId": session_id, "eventSeq": event_seq, "events": events or []}


def script(rounds, *, send=None, close=None):
    """Canned results for one happy call shape with ``rounds`` answer rounds."""
    send = send or [{"accepted": True, "sessionId": f"s-{index + 1}"} for index in range(rounds)]
    close = close or [{"closed": True} for _ in range(rounds)]
    return {"session/create": [snapshot(f"s-{index + 1}") for index in range(rounds)],
            "session/setModel": [configured() for _ in range(rounds)],
            "session/setThoughtLevel": [configured() for _ in range(rounds)],
            "session/subscribe": [subscribe_report(f"s-{index + 1}") for index in range(rounds)],
            "session/send": send, "session/close": close}


class FakeConnectionCallTests(unittest.TestCase):
    """read_only_call's own sequencing, refunds and round handling."""

    def exercise(self, results, pushes=None, *, budget=8, workspace=None, cwd=CWD):
        connection = FakeConnection(results, pushes)
        facts = ZcodeToolFacts(dict(BINDING))
        control = {"cwd": cwd, "spec": SPEC,
                   "readOnlyRequest": {"prompt": "Pick one profile", "outputSchema": SCHEMA,
                                       "budget": {"timeoutSeconds": 30, "toolCalls": budget}}}
        result = {"status": "error", "requested": SPEC, "resolved": None, "observed": None,
                  "modelStarted": False}
        error = None
        try:
            session_id = read_only_call(connection, control, result,
                                        workspace or {"workspacePath": cwd, "workspaceKey": cwd},
                                        {"fixture-api": "api-key"}, facts)
        except NativeError as failure:
            session_id, error = None, failure
        return session_id, result, facts, error, connection

    def creates(self, connection):
        return [params for method, params in connection.calls if method == "session/create"]

    def test_one_round_carries_every_native_fact_and_field(self):
        session_id, result, facts, error, connection = self.exercise(
            script(1), {"session/send": [round_events('{"choice":"a"}')]})
        self.assertIsNone(error)
        self.assertEqual(session_id, "s-1")
        self.assertEqual([method for method, _ in connection.calls],
                         ["session/create", "session/setModel", "session/setThoughtLevel",
                          "session/subscribe", "session/send", "session/close"])
        self.assertEqual(self.creates(connection), [session_parameters(WORKSPACE)])
        self.assertEqual(result["resolved"], SPEC)
        self.assertIsNone(result["observed"])
        self.assertTrue(result["modelStarted"])
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-1", "turnId": "t-1"})
        self.assertEqual((result["rawAnswer"], result["answerValid"], result["correctionCount"]),
                         ('{"choice":"a"}', True, 0))
        self.assertEqual(result["usage"], {"toolCalls": 0})
        self.assertIs(connection.deadline, math.inf)
        package = facts.finish(True)
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"}])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))

    def test_create_refusal_fails_before_any_model_claim(self):
        session_id, result, _, error, connection = self.exercise(
            {"session/create": [NativeError("native-rpc-error", "the plan mode was refused")]})
        self.assertEqual(error.code, "native-rpc-error")
        self.assertIsNone(session_id)
        self.assertFalse(result["modelStarted"])
        self.assertEqual([method for method, _ in connection.calls], ["session/create"])

    def test_admission_refusal_keeps_model_started_false(self):
        _, result, _, error, _ = self.exercise(
            script(1, send=[{"accepted": False, "sessionId": "s-1"}]))
        self.assertEqual(error.code, "native-admission-failed")
        self.assertFalse(result["modelStarted"])
        self.assertIsNone(result["observed"])

    def test_a_refused_send_never_fills_a_served_report(self):
        _, result, _, error, _ = self.exercise(
            script(1, send=[NativeError("native-rpc-error", "the read-only input was refused")]))
        self.assertEqual(error.code, "native-rpc-error")
        self.assertEqual(result["resolved"], SPEC)
        self.assertIsNone(result["observed"])
        self.assertFalse(result["modelStarted"])

    def test_every_bad_subscribe_report_fails_before_the_send(self):
        replayed = [{"eventId": "e-1", "type": "message.upserted", "sessionId": "s-1", "seq": 1}]
        for label, report in (
            ("foreign-session", {"sessionId": "s-child", "eventSeq": 0, "events": []}),
            ("boolean-sequence", {"sessionId": "s-1", "eventSeq": True, "events": []}),
            ("string-sequence", {"sessionId": "s-1", "eventSeq": "0", "events": []}),
            ("negative-sequence", {"sessionId": "s-1", "eventSeq": -1, "events": []}),
            ("missing-sequence", {"sessionId": "s-1", "events": []}),
            ("missing-events", {"sessionId": "s-1", "eventSeq": 0}),
            ("events-not-an-array", {"sessionId": "s-1", "eventSeq": 0, "events": {}}),
            ("empty-report", {}),
            ("replayed-events", {"sessionId": "s-1", "eventSeq": 1, "events": replayed}),
        ):
            with self.subTest(label=label):
                session_id, result, _, error, connection = self.exercise(
                    dict(script(1), **{"session/subscribe": [report]}))
                self.assertIsNone(session_id)
                self.assertEqual(error.code, "invalid-protocol")
                self.assertFalse(result["modelStarted"])
                self.assertIsNone(result["observed"])
                self.assertEqual([method for method, _ in connection.calls][-1], "session/subscribe")

    def test_a_well_shaped_report_with_no_replay_admits_the_round(self):
        _, result, _, error, connection = self.exercise(
            script(1), {"session/send": [round_events('{"choice":"a"}')]})
        self.assertIsNone(error)
        self.assertEqual([params for method, params in connection.calls
                          if method == "session/subscribe"],
                         [{"sessionId": "s-1", "deliveryKind": "web-remote-replayable",
                           "includeSnapshot": False}])

    def test_non_root_session_and_workspace_mismatch_fail_closed(self):
        *_, error, connection = self.exercise({"session/create": [snapshot(parent="s-0")]})
        self.assertEqual(error.code, "wrong-native-session")
        *_, error, connection = self.exercise({"session/create": [snapshot(cwd="/other")]})
        self.assertEqual(error.code, "wrong-native-workspace")
        _, result, _, error, connection = self.exercise(
            {"session/create": [snapshot()]},
            workspace={"workspacePath": "/other", "workspaceKey": "/other"})
        self.assertEqual(error.code, "wrong-native-workspace")
        self.assertFalse(result["modelStarted"])
        self.assertEqual(connection.calls, [])

    def test_close_failure_keeps_the_pending_marker_and_the_facts(self):
        _, result, facts, error, _ = self.exercise(
            script(1, close=[{"closed": False}]), {"session/send": [round_events('{"choice":"a"}')]})
        self.assertEqual(error.code, "session-close-unconfirmed")
        self.assertTrue(result["modelStarted"])
        self.assertTrue(facts.close_pending)
        self.assertFalse(facts.finish(False)["streamComplete"])

    def test_one_correction_shares_parameters_and_accumulates_roots(self):
        session_id, result, facts, error, connection = self.exercise(
            script(2), {"session/send": [round_events('bad JSON'),
                                         round_events('{"choice":"a"}', session="s-2", turn="t-2")]})
        self.assertIsNone(error)
        self.assertEqual(session_id, "s-2")
        self.assertEqual(result["correctionCount"], 1)
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-2", "turnId": "t-2"})
        created = self.creates(connection)
        self.assertEqual(len(created), 2)
        self.assertEqual(created[0], created[1])
        self.assertEqual(created[0], session_parameters(WORKSPACE))
        self.assertEqual(facts.finish(True)["nativeIdentity"],
                         [{"sessionId": "s-1", "turnId": "t-1"}, {"sessionId": "s-2", "turnId": "t-2"}])

    def test_an_out_of_bounds_choice_is_never_corrected(self):
        _, result, _, error, connection = self.exercise(
            script(1), {"session/send": [round_events('{"choice":"b"}')]})
        self.assertIsNone(error)
        self.assertFalse(result["answerValid"])
        self.assertEqual(result["correctionCount"], 0)
        self.assertEqual(len(self.creates(connection)), 1)

    def test_the_budget_counts_projected_calls_across_kinds(self):
        session_id, result, facts, error, _ = self.exercise(
            script(1), {"session/send": [round_events('{"choice":"a"}', tools=[("c-1", "Read")])]},
            budget=0)
        self.assertEqual(error.code, "readonly-budget-exhausted")
        self.assertEqual(facts.finish(False)["toolCalls"], 1)
        session_id, result, facts, error, _ = self.exercise(
            script(1), {"session/send": [round_events('{"choice":"a"}',
                                                      tools=[("c-1", "Read"), ("c-2", "Glob")])]},
            budget=1)
        self.assertEqual(error.code, "readonly-budget-exhausted")
        self.assertEqual(facts.finish(False)["toolCalls"], 2)

    def test_an_unusable_budget_is_refused_without_any_call(self):
        for budget in (None, -1, "8", True, 1.5):
            with self.subTest(budget=budget):
                *_, error, connection = self.exercise(script(1), budget=budget)
                self.assertEqual(error.code, "invalid-control")
                self.assertEqual(connection.calls, [])

    def test_cancel_and_preserved_deadline(self):
        connection = FakeConnection(script(1), {"session/send": [round_events('{"choice":"a"}')]})
        facts = ZcodeToolFacts(dict(BINDING))
        control = {"cwd": CWD, "spec": SPEC,
                   "readOnlyRequest": {"prompt": "Pick", "outputSchema": SCHEMA,
                                       "budget": {"timeoutSeconds": 5, "toolCalls": 8}}}
        connection.cancelled.set()
        with self.assertRaises(NativeError) as caught:
            read_only_call(connection, control, {}, WORKSPACE, {"fixture-api": "api-key"}, facts)
        self.assertEqual(caught.exception.code, "cancelled")
        self.assertIs(connection.deadline, math.inf)


class FakeAppServerTests(unittest.TestCase):
    """The fixed protocol against the fake app-server over the real transport."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-read-only-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.cwd = self.root / "frozen"
        self.cwd.mkdir(mode=0o700)
        self.record = self.root / "create-params.jsonl"

    def run_call(self, case, *, tool_budget=8, seconds=10, deadline=None, cancel_after=None):
        environment = {key: value for key, value in os.environ.items() if not key.startswith("BUDDY_")}
        environment["MOCK_ZCODE_READ_ONLY_RECORD"] = str(self.record)
        cancelled = threading.Event()
        process = subprocess.Popen([sys.executable, str(FIXTURE), case], stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, env=environment)
        self.addCleanup(self._stop, process)
        connection = NativeConnection(process, deadline if deadline is not None else time.monotonic() + seconds,
                                      cancelled, no_tools=True)
        facts = ZcodeToolFacts(dict(BINDING))
        control = {"cwd": str(self.cwd), "spec": dict(SPEC),
                   "readOnlyRequest": {"prompt": "Choose a profile", "outputSchema": SCHEMA,
                                       "budget": {"timeoutSeconds": seconds, "toolCalls": tool_budget}}}
        workspace = {"workspacePath": str(self.cwd), "workspaceKey": str(self.cwd)}
        result = {"status": "error", "requested": SPEC, "resolved": None, "observed": None,
                  "modelStarted": False}
        timer = None
        if cancel_after is not None:
            timer = threading.Timer(cancel_after, cancelled.set)
            timer.start()
        error = None
        try:
            connection.call("runtime/capabilities", {})
            read_only_call(connection, control, result, workspace, {"fixture-api": "api-key"}, facts)
        except NativeError as failure:
            error = failure
        if timer is not None:
            timer.join()
        return error, result, facts, process

    def finish_ok(self, process, facts):
        """Drain the settled fake server to EOF, then finish with the real state."""
        try:
            process.stdin.close()
        except OSError:
            pass
        return facts.finish(process.wait(timeout=5) == 0)

    def _stop(self, process):
        try:
            process.stdin.close()
        except (OSError, ValueError):
            pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def recorded_creates(self):
        return [json.loads(line) for line in self.record.read_text().splitlines()]

    def test_restricted_parameters_model_report_and_read_search_pairs(self):
        error, result, facts, process = self.run_call("tools3")
        self.assertIsNone(error)
        self.assertEqual(self.recorded_creates(), [{
            "workspace": {"workspacePath": str(self.cwd), "workspaceKey": str(self.cwd)},
            "mode": "plan", "titleGenerationEnabled": False,
            "toolAllowlist": ["Read", "Glob", "Grep"], "mcpServers": [],
            "offPeakToolEnabled": False, "dynamicWorkflowEnabled": False}])
        self.assertEqual(result["resolved"], SPEC)
        self.assertIsNone(result["observed"])
        self.assertTrue(result["modelStarted"])
        self.assertEqual(result["sessionId"], "s-1")
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-1", "turnId": "t-1"})
        self.assertEqual((result["rawAnswer"], result["answerValid"], result["correctionCount"]),
                         ('{"choice":"a"}', True, 0))
        self.assertEqual(result["usage"], {"toolCalls": 3})
        package = self.finish_ok(process, facts)
        self.assertEqual([(event["toolName"], event["category"], event["phase"]) for event in package["events"]],
                         [("Read", "read", "start"), ("Read", "read", "end"),
                          ("Glob", "search", "start"), ("Glob", "search", "end"),
                          ("Grep", "search", "start"), ("Grep", "search", "end")])
        self.assertEqual(package["toolCalls"], 3)
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))

    def test_tool_budget_zero_n_and_n_plus_one(self):
        for budget, trips in ((0, True), (2, True), (3, False)):
            with self.subTest(budget=budget):
                error, result, facts, process = self.run_call("tools3", tool_budget=budget)
                if trips:
                    self.assertEqual(error.code, "readonly-budget-exhausted")
                    self.assertEqual(facts.finish(False)["toolCalls"], budget + 1)
                    self.assertFalse(facts.close_pending)
                else:
                    self.assertIsNone(error)
                    self.assertEqual(result["usage"]["toolCalls"], 3)
                    self.assertTrue(self.finish_ok(process, facts)["streamComplete"])

    def test_foreign_mcp_missing_and_late_calls_stay_projected(self):
        for case, identity, name, category, verdict in (
            ("foreign", {"sessionId": "s-child", "turnId": "t-child"}, "Glob", "search",
             tool_evidence.TOOL_EVIDENCE_UNVERIFIED),
            ("mcp", {"sessionId": "s-1", "turnId": "t-1"}, "mcp__fix__tool", "other",
             tool_evidence.TOOLS_FORBIDDEN),
            ("missing-id", {"sessionId": "s-1", "turnId": "t-1"}, "Bash", "execute",
             tool_evidence.TOOL_EVIDENCE_UNVERIFIED),
        ):
            with self.subTest(case=case):
                error, result, facts, process = self.run_call(case)
                self.assertIsNone(error)
                self.assertTrue(result["modelStarted"])
                package = self.finish_ok(process, facts)
                event = package["events"][0]
                self.assertEqual(event["nativeIdentity"], identity)
                self.assertEqual((event["toolName"], event["category"]), (name, category))
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False), verdict)

    def test_a_late_frame_marks_the_stream_incomplete(self):
        error, result, facts, process = self.run_call("late")
        self.assertIsNone(error)
        self.assertEqual(result["correctionCount"], 0)
        package = self.finish_ok(process, facts)
        self.assertEqual(package["events"][0]["toolName"], "Read")
        self.assertEqual(package["toolCalls"], 1)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_bad_identity_sequence_unknown_and_truncated_streams_fail_closed(self):
        for case, code, roots in (("badseq", "invalid-protocol", 1), ("wronginput", "wrong-native-turn", 0),
                                  ("unknown", "invalid-protocol", 1), ("truncated", "native-disconnected", 1)):
            with self.subTest(case=case):
                error, result, facts, process = self.run_call(case)
                self.assertEqual(error.code, code)
                # Every one of these failures happens after a real admitted send.
                self.assertTrue(result["modelStarted"])
                package = facts.finish(False)
                self.assertEqual(len(package["nativeIdentity"]), roots)
                self.assertFalse(package["streamComplete"])

    def test_one_correction_accumulates_roots_and_tool_counts(self):
        error, result, facts, process = self.run_call("correct-tools")
        self.assertIsNone(error)
        self.assertEqual(result["correctionCount"], 1)
        self.assertEqual(result["nativeIdentity"], {"sessionId": "s-2", "turnId": "t-2"})
        self.assertEqual(result["usage"]["toolCalls"], 2)
        self.assertEqual(len(self.recorded_creates()), 2)
        package = self.finish_ok(process, facts)
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"},
                                                     {"sessionId": "s-2", "turnId": "t-2"}])
        self.assertEqual(package["toolCalls"], 2)
        self.assertTrue(package["streamComplete"])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))

    def test_an_out_of_bounds_choice_stops_after_one_round(self):
        error, result, facts, process = self.run_call("enum")
        self.assertIsNone(error)
        self.assertFalse(result["answerValid"])
        self.assertEqual(result["correctionCount"], 0)
        self.assertEqual(len(self.recorded_creates()), 1)
        self.assertTrue(self.finish_ok(process, facts)["streamComplete"])

    def test_close_failure_keeps_the_pending_marker_and_the_facts(self):
        error, result, facts, _ = self.run_call("close-fail")
        self.assertEqual(error.code, "session-close-unconfirmed")
        self.assertTrue(result["modelStarted"])
        self.assertTrue(facts.close_pending)
        package = facts.finish(False)
        self.assertEqual(package["events"], [])
        self.assertFalse(package["streamComplete"])

    def test_a_configuration_mismatch_fails_before_model_input(self):
        error, result, facts, _ = self.run_call("config-mismatch")
        self.assertEqual(error.code, "configuration-mismatch")
        self.assertFalse(result["modelStarted"])
        self.assertEqual(len(self.recorded_creates()), 1)
        self.assertEqual(facts.finish(False)["nativeIdentity"], [])

    def test_subscribe_report_failures_precede_any_model_input(self):
        for case in ("sub-wrong-sid", "sub-bad-seq", "sub-replay"):
            with self.subTest(case=case):
                self.record.write_text("")  # one create per case, not per class
                error, result, facts, _ = self.run_call(case)
                self.assertEqual(error.code, "invalid-protocol")
                self.assertFalse(result["modelStarted"])
                self.assertEqual(len(self.recorded_creates()), 1)
                package = facts.finish(False)
                self.assertEqual((package["events"], package["nativeIdentity"]), ([], []))

    def test_the_single_deadline_and_cancel_end_the_call(self):
        error, result, facts, _ = self.run_call("timeout", deadline=time.monotonic() + 1.5)
        self.assertEqual(error.code, "timeout")
        self.assertTrue(result["modelStarted"])
        self.assertFalse(facts.close_pending)
        self.assertFalse(facts.finish(False)["streamComplete"])
        error, result, facts, _ = self.run_call("timeout", cancel_after=0.3)
        self.assertEqual(error.code, "cancelled")
        self.assertTrue(result["modelStarted"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
