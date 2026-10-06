"""ZCode native tool facts: projection into the shared evidence and receipt wiring.

No paid model calls: the projection matrix runs against raw fake app-server
frames and the controller wiring runs against the fake ZCode app-server only.
The fake app-server and its harness live here and are shared with
``test_no_tool_zcode`` so the no-tool regression keeps its native-parameter
assertions against the same server.
"""
from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from hey_my_buddy.protocol import tool_evidence
from hey_my_buddy.buddy.harnesses.base import ExecutionContext, NoToolStructuredRequest
from hey_my_buddy.buddy.harnesses.zcode.adapter import ZcodeAdapter
from hey_my_buddy.buddy.roles.controller import FastPreparation, start_router_preparation
from hey_my_buddy.buddy.roles.structured_call import collect
from hey_my_buddy.buddy.harnesses.zcode.protocol import NativeError
from hey_my_buddy.buddy.harnesses.zcode.tool_evidence import ZcodeToolFacts
from hey_my_buddy.json_codec import canonical_json


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}

BINDING = {"adapter": "zcode", "taskId": "task-1", "attemptId": "attempt-1", "generation": 3}
ROOT = {"sessionId": "s-1", "turnId": "t-1"}

FAKE = '''#!/usr/bin/env python3
import json, os, sys, time
from pathlib import Path
if '--version' in sys.argv:
    print('fixture-0.16.9'); sys.exit(0)
case = os.environ.get('BUDDY_ZCODE_TEST_CASE', 'ok')
session = 0
subscribed = False
closed = False
workspace = {}
def send(data):
    print(json.dumps(data), flush=True)
def event(kind, seq, payload):
    send({'method':'session/event','params':{'sessionId':f's-{session}','turnId':f't-{session}',
          'seq':seq,'type':kind,'payload':payload}})
for line in sys.stdin:
    call = json.loads(line); method = call['method']; p = call.get('params', {})
    result = {}
    if method == 'runtime/capabilities':
        send({'method':'startup/storageState','params':{'phase':'ready','elapsedMs':10}})
        send({'id':'runtime-preferences','method':'session/requestRuntimePreferences','params':{}})
        reply = json.loads(sys.stdin.readline())
        assert reply['result']['nativeSearchEnhancementsEnabled'] is (case in ('write-final', 'write-post-close'))
    if method == 'session/create':
        if case == 'discovery':
            assert p['toolAllowlist'] == [] and p['titleGenerationEnabled'] is False
        elif case in ('write-final', 'write-post-close'):
            assert p.get('mode') == 'yolo' and p['mcpServers'] == [] and p['titleGenerationEnabled'] is False
        else:
            assert p['toolAllowlist'] == [] and p['titleGenerationEnabled'] is False and p['mcpServers'] == []
            assert p['offPeakToolEnabled'] is False and p['dynamicWorkflowEnabled'] is False
        session += 1; workspace = p['workspace']; subscribed = False; closed = False
        result = {'session':{'sessionId':f's-{session}','sessionKind':'interactive','workspace':workspace},
          'settings':{'model':{'available':[{'ref':{'providerId':'fixture-api','modelId':'fixture-model'},
          'reasoning':{'levels':[{'value':'low'}]}}]},'thoughtLevel':{'current':'low'}}}
    elif method in ('session/setModel','session/setThoughtLevel'):
        result = {'session':{'sessionId':f's-{session}','workspace':workspace},
          'settings':{'model':{'current':{'providerId':'fixture-api','modelId':'fixture-model',
          'options':{'reasoningLevel':'low'}}},'thoughtLevel':{'current':'low'}}}
    elif method == 'session/subscribe':
        assert p['deliveryKind'] == 'web-remote-replayable' and p['includeSnapshot'] is False
        subscribed = True
    elif method == 'session/send':
        assert case != 'discovery', 'discovery must never send an input'
        assert subscribed
        assert 'Return only one JSON value matching this schema:' in p['content']
        if case == 'reverse-before-send-reply':
            send({'id':'srv-before-reply','method':'interaction/requestPermission',
                  'params':{'sessionId':f's-{session}','kind':'shell'}})
            reply = json.loads(sys.stdin.readline())
            Path('refusal-reply.json').write_text(json.dumps(
                {'id':reply.get('id'),'refused':'error' in reply}))
            time.sleep(30)
            sys.exit(0)
        result = {'accepted':True,'sessionId':f's-{session}'}
    elif method == 'session/close':
        if closed:
            send({'id':call['id'],'error':{'code':-32004,'message':'Session is not active'}})
            continue
        closed = True
        result = {'closed': case != 'close-fail'}
    if case == 'native-stream' and method == 'session/create':
        event('session.created',1,{})
        send({'method':'state.updated','params':{'scope':'server','reason':'session_created'}})
    if case == 'native-stream' and method == 'session/setModel':
        event('session.updated',2,{})
    send({'id':call['id'],'result':result})
    if method == 'session/close' and case == 'post-close-foreign':
        send({'method':'session/event','params':{'sessionId':'s-child','turnId':'t-child',
              'seq':3,'type':'tool.updated','payload':{'kind':'scheduled','toolCallId':'c-foreign',
              'toolName':'Glob','source':'sub-agent'}}})
    if method == 'session/close' and case in ('post-close-tool', 'write-post-close'):
        payload = {'kind':'scheduled','toolCallId':'c-late','toolName':'Bash'} if case == 'write-post-close' \
                  else {'kind':'scheduled','toolName':'mcp'}
        event('tool.updated',3,payload)
    if method == 'session/send':
        ident = p['inputId']
        if case == 'native-stream':
            send({'method':'computer-use/operation-event','params':{'sessionId':f's-{session}',
                  'turnId':f't-{session}','kind':'turn-started','sequenceNumber':2}})
            send({'method':'v4/telemetry/event','params':{'sessionId':f's-{session}',
                  'turnId':f't-{session}','kind':'turn.started','eventSeq':1}})
        event('turn.started',1,{'inputId':ident})
        if case == 'native-stream':
            send({'method':'process/mcpTelemetry','params':{'status':'connected'}})
            send({'method':'process/mcpResourceSamples','params':{'samples':[]}})
            event('part.started',2,{'part':{'type':'text','text':''}})
            event('part.delta',3,{'delta':{'type':'text','text':'{'}})
            event('message.upserted',4,{'message':{'parts':[{'type':'text','text':'{"choice":"a"}'}]}})
            event('model.streaming',5,{'streaming':False})
        if case == 'reverse-request':
            send({'id':'srv-1','method':'interaction/requestPermission',
                  'params':{'sessionId':f's-{session}','kind':'shell'}})
            reply = json.loads(sys.stdin.readline())
            Path('refusal-reply.json').write_text(json.dumps(
                {'id': reply.get('id'), 'refused': 'error' in reply}))
            sys.exit(0)
        elif case == 'reverse-oauth':
            send({'id':'srv-2','method':'interaction/requestProviderRuntimeHeaders',
                  'params':{'sessionId':f's-{session}','providerId':'fixture-account'}})
            time.sleep(30)
        if case == 'tool':
            event('tool.updated',2,{'kind':'scheduled','toolName':'shell'})
        elif case == 'tool-foreign':
            send({'method':'session/event','params':{'sessionId':'s-child','turnId':'t-child',
                  'seq':2,'type':'tool.updated','payload':{'kind':'scheduled','toolCallId':'c-f',
                  'toolName':'Glob','source':'sub-agent'}}})
        elif case == 'unknown':
            event('future.unknown',2,{})
        elif case == 'timeout':
            time.sleep(5)
        elif case == 'truncated':
            sys.exit(0)
        else:
            answer = ('bad JSON' if case == 'correct' and session == 1 else
                      '{"choice":"b"}' if case == 'enum' else '{"choice":"a"}')
            if case == 'native-stream':
                send({'method':'computer-use/operation-event','params':{'sessionId':f's-{session}',
                      'turnId':f't-{session}','kind':'turn-completed','sequenceNumber':12}})
                send({'method':'v4/telemetry/event','params':{'sessionId':f's-{session}',
                      'turnId':f't-{session}','kind':'turn.terminal','eventSeq':9,'toolCallCount':0}})
            event('turn.completed',6 if case == 'native-stream' else 2,{'inputId':ident,'resultType':'success','response':answer})
            send({'method':'state.updated','params':{'sessionId':f's-{session}','reason':'prompt_completed'}})
'''


def frame(payload, *, session="s-1", turn="t-1", seq=2, kind="tool.updated"):
    return {"method": "session/event", "params": {"sessionId": session, "turnId": turn,
            "seq": seq, "type": kind, "payload": payload}}


class ProjectionTests(unittest.TestCase):
    """The fixed projection of real tool.updated frames into shared facts."""

    def facts(self) -> ZcodeToolFacts:
        facts = ZcodeToolFacts(dict(BINDING))
        facts.add_root("s-1", "t-1")
        return facts

    def test_verified_delivery_exclusion_joins_the_root_and_preserves_unsettled_counts(self):
        facts = self.facts()
        for session, turn, name in (("s-1", "t-1", "mcp__session__finish"),
                                    ("s-foreign", "t-foreign", "Bash")):
            facts.observe(frame({'kind': 'scheduled', 'toolCallId': 'shared', 'toolName': name},
                                session=session, turn=turn))
            facts.observe(frame({'kind': 'result', 'toolCallId': 'shared',
                                 'result': {'success': True}}, session=session, turn=turn))
        facts.observe(frame({'kind': 'scheduled', 'toolCallId': 'open', 'toolName': 'Bash'}))
        package = facts.finish(True, exclude_calls={(canonical_json(ROOT), "shared")})
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (2, 1))
        self.assertEqual([(event["nativeIdentity"]["sessionId"], event["callId"])
                          for event in package["events"]],
                         [("s-foreign", "shared"), ("s-foreign", "shared"), ("s-1", "open")])

    def test_native_started_and_progress_belong_to_the_existing_call(self):
        facts = self.facts()
        for seq, payload in enumerate((
                {'kind': 'scheduled', 'toolCallId': 'read', 'toolName': 'Read'},
                {'kind': 'started', 'toolCallId': 'read'},
                {'kind': 'progress', 'toolCallId': 'read'},
                {'kind': 'result', 'toolCallId': 'read'}), 2):
            facts.observe(frame(payload, seq=seq))
        package = facts.finish(True)
        self.assertEqual(package['toolCalls'], 1)
        self.assertEqual([event['phase'] for event in package['events']], ['start', 'end'])
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, 'review', False))

    def test_unbound_conflicting_and_late_intermediate_frames_stay_incomplete(self):
        for payload in ({'kind': 'started', 'toolCallId': 'foreign'},
                        {'kind': 'progress', 'toolCallId': 'read', 'toolName': 'Bash'}):
            facts = self.facts()
            facts.observe(frame({'kind': 'scheduled', 'toolCallId': 'read', 'toolName': 'Read'}))
            facts.observe(frame(payload, seq=3))
            self.assertIsNotNone(tool_evidence.judge_tool_evidence(facts.finish(True), 'review', False))
        facts = self.facts()
        facts.observe(frame({'kind': 'scheduled', 'toolCallId': 'read', 'toolName': 'Read'}))
        facts.observe(frame({'kind': 'result', 'toolCallId': 'read'}, seq=3))
        facts.observe(frame({'kind': 'progress', 'toolCallId': 'read'}, seq=4))
        self.assertIsNotNone(tool_evidence.judge_tool_evidence(facts.finish(True), 'review', False))
        facts = self.facts()
        facts.observe(frame({'kind': 'scheduled', 'toolCallId': 'read', 'toolName': 'Read'}))
        facts.observe({'method': 'session/event', 'params': {**ROOT, 'type': 'turn.completed'}})
        facts.observe(frame({'kind': 'progress', 'toolCallId': 'read'}, seq=4))
        self.assertIsNotNone(tool_evidence.judge_tool_evidence(facts.finish(True), 'review', False))

    def test_settled_pair_keeps_the_scheduled_name_and_fixed_fields_only(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read",
                             "title": "Reading main.py", "arguments": {"path": "/etc/passwd"}}))
        facts.observe(frame({"kind": "result", "toolCallId": "c-1",
                             "result": {"content": "whole file body"}}, seq=3))
        package = facts.finish(True)
        self.assertEqual(package["events"], [
            {"nativeIdentity": dict(ROOT), "callId": "c-1", "toolName": "Read", "category": "read", "phase": "start"},
            {"nativeIdentity": dict(ROOT), "callId": "c-1", "toolName": "Read", "category": "read", "phase": "end"}])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))
        self.assertEqual(package["binding"], BINDING)
        self.assertEqual(package["nativeIdentity"], [ROOT])
        self.assertTrue(package["streamComplete"])
        self.assertFalse(package["truncated"])
        # The blackboard consumes the projection: the recorded read call is a
        # legal review fact, and fast mode's zero-call rule rejects it.
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "review", False))
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "fast", False),
                         tool_evidence.TOOLS_FORBIDDEN)

    def test_error_kind_ends_the_call_from_the_scheduled_record(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Grep"}))
        facts.observe(frame({"kind": "error", "toolCallId": "c-1",
                             "error": {"message": "the pattern failed"}}, seq=3))
        package = facts.finish(True)
        self.assertEqual([event["phase"] for event in package["events"]], ["start", "end"])
        self.assertEqual(package["events"][1]["toolName"], "Grep")
        self.assertEqual(package["events"][1]["category"], "search")
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (1, 0))

    def test_each_known_tool_maps_and_unknown_names_stay_other(self):
        for name, category in (("Read", "read"), ("Glob", "search"), ("Grep", "search"),
                               ("Bash", "execute"), ("Write", "edit"), ("Edit", "edit"),
                               ("WebFetch", "fetch"), ("mcp__server__tool", "other"),
                               ("dispatch_agent", "other")):
            with self.subTest(name=name):
                facts = self.facts()
                facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": name}))
                event = facts.finish(True)["events"][0]
                self.assertEqual((event["toolName"], event["category"]), (name, category))

    def test_result_before_its_scheduled_stays_an_unnamed_end_fact(self):
        facts = self.facts()
        facts.observe(frame({"kind": "result", "toolCallId": "c-1"}, seq=2))
        package = facts.finish(True)
        self.assertEqual(package["events"], [
            {"nativeIdentity": dict(ROOT), "callId": "c-1", "toolName": None,
             "category": "other", "phase": "end"}])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"]), (0, 0))
        self.assertFalse(package["streamComplete"])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_missing_call_id_is_retained_and_never_invented(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolName": "Bash"}))
        package = facts.finish(True)
        self.assertEqual(package["events"], [
            {"nativeIdentity": dict(ROOT), "callId": None, "toolName": "Bash",
             "category": "execute", "phase": "start"}])
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_missing_turn_or_session_identity_stays_incomplete(self):
        for params in ({"turnId": ""}, {"sessionId": None}, {"turnId": 5}):
            with self.subTest(params=params):
                facts = self.facts()
                message = frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"})
                message["params"].update(params)
                facts.observe(message)
                package = facts.finish(True)
                surviving = {"sessionId": "s-1", "turnId": "t-1"}
                for key, value in params.items():
                    if not (isinstance(value, str) and value):
                        surviving.pop(key, None)
                self.assertEqual(package["events"][0]["nativeIdentity"], surviving)
                self.assertEqual(package["events"][0]["callId"], "c-1")
                self.assertFalse(package["streamComplete"])

    def test_unknown_payload_kind_is_an_incomplete_fact_without_a_phase(self):
        facts = self.facts()
        facts.observe(frame({"kind": "updated", "toolCallId": "c-1", "toolName": "Read"}))
        event = facts.finish(True)["events"][0]
        self.assertEqual((event["phase"], event["toolName"], event["category"]), (None, None, "other"))

    def test_a_title_never_becomes_the_tool_name(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "title": "Read /etc/passwd"}))
        event = facts.finish(True)["events"][0]
        self.assertIsNone(event["toolName"])
        self.assertEqual(event["category"], "other")

    def test_identical_projections_collapse_and_conflicting_names_are_kept(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}))
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}, seq=3))
        package = facts.finish(True)
        self.assertEqual(len(package["events"]), 1)
        self.assertEqual(package["toolCalls"], 1)
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}))
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Write"}, seq=3))
        package = facts.finish(True)
        self.assertEqual(len(package["events"]), 2)
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_foreign_child_and_old_turn_frames_are_kept_but_never_trusted(self):
        for session, turn in (("s-child", "t-child"), ("s-1", "t-old"), ("s-other", "t-1")):
            with self.subTest(session=session, turn=turn):
                facts = self.facts()
                facts.observe(frame({"kind": "scheduled", "toolCallId": "c-9", "toolName": "Read",
                                     "source": "sub-agent", "parentToolCallId": "c-root",
                                     "childSessionId": "s-deep"}, session=session, turn=turn))
                package = facts.finish(True)
                self.assertEqual(package["events"], [
                    {"nativeIdentity": {"sessionId": session, "turnId": turn}, "callId": "c-9",
                     "toolName": "Read", "category": "read", "phase": "start"}])
                self.assertEqual(package["toolCalls"], 1)
                self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                                 tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_late_frames_after_the_close_leave_the_stream_incomplete(self):
        facts = self.facts()
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}))
        facts.observe(frame({"kind": "result", "toolCallId": "c-1"}, seq=3))
        self.assertTrue(facts.finish(True)["streamComplete"])
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-2", "toolName": "Read"}, seq=4))
        package = facts.finish(True)
        self.assertEqual(package["toolCalls"], 2)
        self.assertFalse(package["streamComplete"])

    def test_root_identities_come_only_from_controller_receipts(self):
        facts = ZcodeToolFacts(dict(BINDING))
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}))
        package = facts.finish(True)
        self.assertEqual(package["nativeIdentity"], [])
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)
        facts.add_root("s-1", "t-1")
        facts.add_root("s-1", "t-1")
        facts.add_root("s-2", "t-2")
        self.assertEqual(facts.finish(True)["nativeIdentity"], [ROOT, {"sessionId": "s-2", "turnId": "t-2"}])

    def test_non_tool_frames_project_nothing(self):
        facts = self.facts()
        for message in ({"method": "state.updated", "params": {"sessionId": "s-1", "reason": "prompt_completed"}},
                        {"method": "session/event", "params": {"sessionId": "s-1", "turnId": "t-1", "seq": 1,
                         "type": "turn.started", "payload": {"inputId": "input"}}},
                        {"method": "session/event", "params": {"sessionId": "s-1", "turnId": "t-1", "seq": 2,
                         "type": "message.upserted", "payload": {"message": {"parts": []}}}}):
            facts.observe(message)
        package = facts.finish(True)
        self.assertEqual(package["events"], [])
        self.assertTrue(package["streamComplete"])

    def test_correction_sessions_accumulate_in_one_collector(self):
        facts = ZcodeToolFacts(dict(BINDING))
        facts.add_root("s-1", "t-1")
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}))
        facts.observe(frame({"kind": "result", "toolCallId": "c-1"}, seq=3))
        facts.close_pending = True
        facts.close_pending = False
        facts.add_root("s-2", "t-2")
        facts.observe(frame({"kind": "scheduled", "toolCallId": "c-2", "toolName": "Glob"},
                            session="s-2", turn="t-2", seq=1))
        facts.observe(frame({"kind": "result", "toolCallId": "c-2"}, session="s-2", turn="t-2", seq=2))
        package = facts.finish(True)
        self.assertEqual(package["nativeIdentity"], [ROOT, {"sessionId": "s-2", "turnId": "t-2"}])
        self.assertEqual(package["toolCalls"], 2)
        self.assertEqual(len(package["events"]), 4)
        self.assertTrue(package["streamComplete"])


class ProjectionOrderingTests(unittest.TestCase):
    """Facts are projected and classified before the role decides and before the
    driver's own protocol checks run — the ordering the unified run preserves."""

    def facts(self) -> ZcodeToolFacts:
        facts = ZcodeToolFacts(dict(BINDING))
        facts.add_root("s-1", "t-1")
        return facts

    def chain(self, facts: ZcodeToolFacts):
        from hey_my_buddy.buddy.harnesses.zcode import native_run
        from hey_my_buddy.buddy.roles.run_observers import FastCorrection
        run_facts = native_run._RunFacts()
        classifier = native_run._Classifier(run_facts)
        classifier.admitted = True
        protocol = native_run.NoToolProtocol("s-1", "input", facts)
        correction = FastCorrection({"type": "object"}, "base prompt")
        return protocol, classifier, run_facts, correction

    def test_rejected_tool_frames_are_projected_before_the_no_tool_refusal(self):
        for payload, session in (({"kind": "scheduled", "toolCallId": "c-1", "toolName": "shell"}, "s-1"),
                                 ({"kind": "scheduled", "source": "child", "toolCallId": "c-2",
                                   "toolName": "Glob"}, "s-child")):
            with self.subTest(session=session):
                facts = self.facts()
                protocol, classifier, run_facts, correction = self.chain(facts)
                protocol.observe(frame({"inputId": "input"}, session="s-1", turn="t-1", seq=1, kind="turn.started"), 1)
                self.assertEqual(facts.roots, [{"sessionId": "s-1", "turnId": "t-1"}])
                tool_frame = frame(payload, session=session)
                facts.observe(tool_frame)
                changed = classifier.observe(tool_frame)
                self.assertTrue(changed)
                self.assertEqual(correction.observer(run_facts.mapping(tool_calls=facts.tool_calls,
                                                                       settled=False, raw_answer=None)).action,
                                 "stop")
                self.assertEqual(correction.stop_reason, "no-tool-violation")
                package = facts.finish(False)
                self.assertEqual(len(package["events"]), 1)
                self.assertEqual(package["toolCalls"], 1)
                self.assertFalse(package["streamComplete"])

    def test_a_tool_frame_with_a_stale_sequence_is_a_fact_before_the_protocol_check(self):
        facts = self.facts()
        protocol, classifier, run_facts, correction = self.chain(facts)
        protocol.observe(frame({"inputId": "input"}, session="s-1", turn="t-1", seq=5, kind="turn.started"), 1)
        tool_frame = frame({"kind": "scheduled", "toolCallId": "c-1", "toolName": "Read"}, seq=2)
        facts.observe(tool_frame)
        self.assertTrue(classifier.observe(tool_frame))
        self.assertEqual(correction.observer(run_facts.mapping(tool_calls=facts.tool_calls,
                                                               settled=False, raw_answer=None)).action,
                         "stop")
        self.assertEqual(correction.stop_reason, "no-tool-violation")
        self.assertEqual(facts.finish(False)["toolCalls"], 1)

    def test_a_non_tool_frame_with_a_stale_sequence_stays_a_protocol_error(self):
        facts = self.facts()
        protocol, classifier, run_facts, correction = self.chain(facts)
        protocol.observe(frame({"inputId": "input"}, session="s-1", turn="t-1", seq=5, kind="turn.started"), 1)
        stale = {"method": "session/event", "params": {"sessionId": "s-1", "turnId": "t-1",
                 "seq": 2, "type": "message.upserted", "payload": {}}}
        self.assertFalse(classifier.observe(stale))
        with self.assertRaises(NativeError) as caught:
            protocol.observe(stale, 2)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        self.assertEqual(facts.finish(False)["events"], [])


class FakeAppServerTests(unittest.TestCase):
    """The shared fake ZCode app-server harness (migrated from test_no_tool_zcode)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-zcode-evidence-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.cwd = self.root / "empty"
        self.cwd.mkdir(mode=0o700)
        self.fake = self.root / "fake-zcode"
        self.fake.write_text(textwrap.dedent(FAKE))
        self.fake.chmod(0o755)
        self.builtin = self.root / "builtin.json"
        self.personal = self.root / "personal.json"
        self.builtin.write_text(json.dumps({"config": {"providerConfigRules": {"templateRules": [], "providerRules": []}}}))
        self.personal.write_text(json.dumps({"config": {"providerConfigRules": {"providerRules": [
            {"providerId": "fixture-api", "config": {"access": {"type": "api-key", "apiKey": "private-test-secret"}}}]}}}))
        self.environment = {k: v for k, v in os.environ.items() if not k.startswith("BUDDY_")
                            and k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[5] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
        self.environment.update(BUDDY_DEV_SOURCE="1", BUDDY_STATE_DIR=str(self.root / "state"),
                                BUDDY_RUNTIME_ROOT=str(self.root / "runtime"), BUDDY_ZCODE_CLI=str(self.fake),
                                ZCODE_BUILTIN_PROVIDER_CONFIG_FILE=str(self.builtin),
                                ZCODE_PERSONAL_PROVIDER_CONFIG_FILE=str(self.personal))

    def execute(self, case="ok", *, timeout=3, cancel=False):
        context = ExecutionContext("task", "attempt", 1,
            {"provider": "fixture-api", "model": "fixture-model", "effort": "low", "cwd": str(self.cwd),
             "timeoutSeconds": timeout}, self.root / "attempt", {},
            {**self.environment, "BUDDY_ZCODE_TEST_CASE": case})
        request = NoToolStructuredRequest(str(self.cwd), "Choose a profile", SCHEMA, timeout_seconds=timeout)
        handle = start_router_preparation(FastPreparation("zcode", ZcodeAdapter(), request, context, self.cwd))
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        if cancel:
            time.sleep(0.2)
            handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(timeout + 8))
        outcome = collect(handle)
        if outcome.result.get("code") == "invalid-native-result":
            self.fail(f"controller emitted no result: {Path(handle.log_paths['stderr']).read_text()}")
        return outcome


class NoToolReceiptWiringTests(FakeAppServerTests):
    """Every structured no-tool receipt carries the attempt's toolEvidence."""

    def test_zero_tool_receipt_carries_complete_judge_ready_evidence(self):
        outcome = self.execute("ok")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["version"], 1)
        self.assertEqual(package["binding"], {"adapter": "zcode", "taskId": "task",
                                              "attemptId": "attempt", "generation": 1})
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"}])
        self.assertEqual(package["events"], [])
        self.assertEqual((package["toolCalls"], package["unsettledToolCalls"], package["truncated"]), (0, 0, False))
        self.assertTrue(package["streamComplete"])
        self.assertTrue(outcome.result["zeroToolVerified"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
        self.assertIsNone(tool_evidence.judge_tool_evidence(package, "fast", False))

    def test_format_correction_accumulates_roots_counts_and_one_stream(self):
        outcome = self.execute("correct")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["nativeIdentity"], [{"sessionId": "s-1", "turnId": "t-1"},
                                                     {"sessionId": "s-2", "turnId": "t-2"}])
        self.assertEqual(package["toolCalls"], 0)
        self.assertTrue(package["streamComplete"])
        self.assertEqual(outcome.result["correctionCount"], 1)
        self.assertIn(outcome.result["nativeIdentity"], package["nativeIdentity"])
        self.assertTrue(outcome.result["zeroToolVerified"])

    def test_tool_call_receipt_keeps_the_fact_and_fails_the_judge(self):
        outcome = self.execute("tool")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        self.assertNotIn("zeroToolVerified", outcome.result)
        package = outcome.result["toolEvidence"]
        # The fixture's frame carries no toolCallId: the fact is kept unnamed
        # and the observed count stays unknown rather than inventing one call.
        self.assertEqual(package["events"], [
            {"nativeIdentity": {"sessionId": "s-1", "turnId": "t-1"}, "callId": None,
             "toolName": "shell", "category": "other", "phase": "start"}])
        self.assertEqual(package["toolCalls"], 0)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "fast", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_foreign_child_call_is_a_kept_foreign_fact(self):
        outcome = self.execute("tool-foreign")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"][0]["nativeIdentity"], {"sessionId": "s-child", "turnId": "t-child"})
        self.assertEqual(package["events"][0]["callId"], "c-f")
        self.assertEqual(package["toolCalls"], 1)
        self.assertEqual(tool_evidence.judge_tool_evidence(package, "review", False),
                         tool_evidence.TOOL_EVIDENCE_UNVERIFIED)

    def test_a_late_frame_after_the_close_is_recorded_and_incomplete(self):
        outcome = self.execute("post-close-tool")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "no-tool-violation")
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["toolCalls"], 0)
        self.assertEqual(len(package["events"]), 1)
        self.assertFalse(package["streamComplete"])
        self.assertEqual(outcome.result["usage"]["toolCalls"], 0)

    def test_close_failure_keeps_the_facts_and_reports_the_actual_error(self):
        outcome = self.execute("close-fail")
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "session-close-unconfirmed")
        package = outcome.result["toolEvidence"]
        self.assertEqual(package["events"], [])
        self.assertEqual(package["toolCalls"], 0)
        self.assertFalse(package["streamComplete"])
        self.assertNotIn("zeroToolVerified", outcome.result)

    def test_cancelled_and_timeout_receipts_carry_incomplete_evidence(self):
        for case, cancel in (("timeout", False), ("timeout", True)):
            with self.subTest(case=case, cancel=cancel):
                outcome = self.execute(case, timeout=1, cancel=cancel)
                self.assertIn(outcome.status, ("failed", "cancelled"))
                self.assertNotIn("zeroToolVerified", outcome.result)
                package = outcome.result["toolEvidence"]
                self.assertEqual(package["events"], [])
                self.assertFalse(package["streamComplete"])
                self.assertEqual(package["toolCalls"], 0)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
