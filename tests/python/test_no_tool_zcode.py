"""No paid model calls: a fake ZCode app-server and native-event adversarial cases."""
from __future__ import annotations

import json
import os
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

from buddy.adapters.base import ExecutionContext, NoToolStructuredRequest
from buddy.adapters.zcode import ZcodeAdapter
from buddy.adapters.zcode_protocol import NativeError
from buddy.adapters.zcode_runner import NoToolEvidence


SCHEMA = {"type": "object", "properties": {"choice": {"type": "string", "enum": ["a"]}},
          "required": ["choice"], "additionalProperties": False}

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
        assert reply['result']['nativeSearchEnhancementsEnabled'] is False
    if method == 'session/create':
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
        assert subscribed
        assert 'Return only one JSON value matching this schema:' in p['content']
        result = {'accepted':True,'sessionId':f's-{session}'}
    elif method == 'session/close':
        if closed:
            send({'id':call['id'],'error':{'code':-32004,'message':'Session is not active'}})
            continue
        closed = True
        result = {'closed':True}
    if case == 'native-stream' and method == 'session/create':
        event('session.created',1,{})
        send({'method':'state.updated','params':{'scope':'server','reason':'session_created'}})
    if case == 'native-stream' and method == 'session/setModel':
        event('session.updated',2,{})
    send({'id':call['id'],'result':result})
    if method == 'session/close' and case == 'post-close-tool':
        event('tool.updated',3,{'kind':'scheduled','toolName':'mcp'})
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
        if case == 'tool':
            event('tool.updated',2,{'kind':'scheduled','toolName':'shell'})
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


class NoToolEvidenceTests(unittest.TestCase):
    def event(self, kind, payload=None, *, session="root", turn="turn", seq=2):
        return {"method": "session/event", "params": {"sessionId": session, "turnId": turn,
                "seq": seq, "type": kind, "payload": payload or {}}}

    def test_all_tool_event_forms_violate_even_without_id_or_in_child(self):
        for kind, payload, session in (
            ("tool.updated", {"kind": "scheduled", "toolName": "shell"}, "root"),
            ("tool.updated", {"kind": "result", "toolName": "mcp"}, "root"),
            ("tool.updated", {"kind": "scheduled", "source": "child", "toolName": "search"}, "child"),
            ("agent.started", {}, "root"),
            ("part.started", {"part": {"type": "tool", "name": "Read"}}, "root"),
            ("message.upserted", {"message": {"parts": [{"type": "tool_use"}]}}, "root"),
        ):
            with self.subTest(kind=kind, session=session):
                evidence = NoToolEvidence("root", "input")
                evidence.observe(self.event("turn.started", {"inputId": "input"}, seq=1), 1)
                with self.assertRaises(NativeError) as caught:
                    evidence.observe(self.event(kind, payload, session=session), 2)
                self.assertEqual(caught.exception.code, "no-tool-violation")

    def test_unknown_and_incomplete_events_cannot_settle(self):
        evidence = NoToolEvidence("root", "input")
        evidence.observe(self.event("turn.started", {"inputId": "input"}, seq=1), 1)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("future.event"), 2)
        self.assertEqual(caught.exception.code, "invalid-protocol")
        self.assertFalse(evidence.settled)

    def test_projected_metadata_never_proves_completion_and_rejects_tools(self):
        for method, key, kind in (
            ("computer-use/operation-event", "sequenceNumber", "tool-scheduled"),
            ("computer-use/operation-event", "sequenceNumber", "tool-started"),
            ("v4/telemetry/event", "eventSeq", "tool.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "permission.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "subagent.lifecycle"),
            ("v4/telemetry/event", "eventSeq", "workflow.lifecycle"),
        ):
            with self.subTest(kind=kind):
                evidence = NoToolEvidence("root", "input")
                with self.assertRaises(NativeError) as caught:
                    evidence.observe({"method": method, "params": {
                        "sessionId": "child", "turnId": "other", "kind": kind, key: 1}}, 1)
                self.assertEqual(caught.exception.code, "no-tool-violation")
        evidence = NoToolEvidence("root", "input")
        evidence.observe({"method": "computer-use/operation-event", "params": {
            "sessionId": "root", "turnId": "turn", "kind": "turn-completed", "sequenceNumber": 1}}, 1)
        self.assertFalse(evidence.completed)
        self.assertFalse(evidence.settled)
        with self.assertRaises(NativeError) as caught:
            evidence.observe(self.event("turn.started", {"inputId": "input"}, turn="foreign", seq=1), 2)
        self.assertEqual(caught.exception.code, "wrong-native-turn")

    def test_unknown_or_reordered_metadata_fails_closed(self):
        for kinds in (("compaction.terminal",), ("usage.delta", "usage.delta")):
            evidence = NoToolEvidence("root", "input")
            with self.assertRaises(NativeError) as caught:
                for kind in kinds:
                    evidence.observe({"method": "v4/telemetry/event", "params": {
                        "sessionId": "root", "turnId": "turn", "kind": kind, "eventSeq": 1}}, 1)
            self.assertEqual(caught.exception.code, "invalid-protocol")


class NoToolFakeProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-no-tool-")
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
        self.environment["PYTHONPATH"] = str(Path(__file__).parents[2] / "src") + os.pathsep + os.environ.get("PYTHONPATH", "")
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
        handle = ZcodeAdapter().start_no_tool_structured(context, request)
        self.addCleanup(lambda: handle.terminate(grace_seconds=0.1) if handle.group_alive() else None)
        if cancel:
            time.sleep(0.2)
            handle.terminate(grace_seconds=2)
        self.assertIsNotNone(handle.wait(timeout + 8))
        outcome = ZcodeAdapter().collect(handle, context)
        if outcome.result.get("code") == "invalid-native-result":
            self.fail(f"controller emitted no result: {Path(handle.log_paths['stderr']).read_text()}")
        return outcome

    def test_zero_tool_and_one_format_correction(self):
        for case, count in (("ok", 0), ("correct", 1), ("native-stream", 0)):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "ok", outcome.to_report())
                self.assertTrue(outcome.result["zeroToolVerified"])
                self.assertEqual(outcome.result["usage"]["toolCalls"], 0)
                self.assertEqual(outcome.result["correctionCount"], count)
                self.assertNotIn("private-test-secret", json.dumps(outcome.to_report()))

    def test_out_of_bounds_choice_is_not_corrected(self):
        outcome = self.execute("enum")
        self.assertEqual(outcome.status, "ok", outcome.to_report())
        self.assertFalse(outcome.result["answerValid"])
        self.assertEqual(outcome.result["correctionCount"], 0)

    def test_tool_unknown_and_truncated_stream_fail_closed(self):
        for case, code in (("tool", "no-tool-violation"), ("post-close-tool", "no-tool-violation"),
                           ("unknown", "invalid-protocol"),
                           ("truncated", "native-disconnected")):
            with self.subTest(case=case):
                outcome = self.execute(case)
                self.assertEqual(outcome.status, "failed")
                self.assertEqual(outcome.result["code"], code)
                self.assertNotIn("zeroToolVerified", outcome.result)
                if code == "no-tool-violation":
                    self.assertGreater(outcome.result["usage"]["toolCalls"], 0)

    def test_deadline(self):
        outcome = self.execute("timeout", timeout=1)
        self.assertEqual(outcome.status, "failed")
        self.assertEqual(outcome.result["code"], "timeout")
        self.assertTrue(outcome.shutdown_confirmed)

    def test_cancel(self):
        outcome = self.execute("timeout", timeout=10, cancel=True)
        self.assertEqual(outcome.status, "cancelled", outcome.to_report())
        self.assertTrue(outcome.shutdown_confirmed)
        self.assertNotIn("zeroToolVerified", outcome.result)


if __name__ == "__main__":
    unittest.main()
