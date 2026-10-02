#!/usr/bin/env python3
"""Deterministic read-only DSH protocol fixture; no model and no network.

The tests copy this script into a private directory and select one scenario
with the ``case`` file next to it, then present it to the controller as the
owned ``dsh`` command. It replays the two native calls the real CLI makes:
``--dump-config`` prints the composed headless rows the controller's free
preflight inspects, and the sentinel run reads the mounted bridge's request
file and writes its result file exactly like ``read-only-structured.mjs``,
including the native tool facts that root session log carried.
"""
import json
import sys
import time
from pathlib import Path

args = sys.argv[1:]
patch = json.loads(Path(args[args.index('--patch') + 1]).read_text())
plugin = next(row for row in patch if 'insert' in row)['insert'][0]
config = plugin['config']
case = (Path(__file__).parent / 'case').read_text().strip()

SAFE_ROWS = [('agent', True), ('agent-default-model', True), ('agent-loop', True),
             ('tools', True), ('session', True), ('tool-fs', True),
             ('tool-fs-search', True), ('llm', True), ('credentials', True),
             ('session-persistence-jsonl', True), ('workflow', False),
             ('headless-runner', False), ('session-title-llm', False),
             ('session-telemetry-otel', False)]

if '--dump-config' in args:
    rows = list(SAFE_ROWS)
    if case == 'dump-title':
        rows = [('session-title-llm', True) if row[0] == 'session-title-llm' else row for row in rows]
    elif case == 'dump-telemetry':
        rows = [('session-telemetry-otel', True) if row[0] == 'session-telemetry-otel' else row
                for row in rows]
    elif case == 'dump-workflow':
        rows = [('workflow', True) if row[0] == 'workflow' else row for row in rows]
    elif case == 'dump-stack':
        rows = [row for row in rows if row[0] != 'tool-fs']
    elif case == 'unsafe':
        rows = [('headless-runner', True) if row[0] == 'headless-runner' else row for row in rows]
    for entry, enabled in rows:
        print(f'- id: {entry}')
        if not enabled:
            print('  disabled: true')
    if case != 'dump-bridge':
        print(f"- id: buddy-read-only-structured\n  name: {plugin['name']}")
    sys.exit(0)

if case == 'timeout':
    time.sleep(5)

request = json.loads(Path(config['requestFile']).read_text())
assert request['cwd'].startswith('/') and request['spec']['provider']
assert request['budget']['timeoutSeconds'] >= 1 and request['budget']['toolCalls'] >= 0
first_round = Path(config['requestFile']).parent.name == 'call-1'
if case == 'correct-zero' and not first_round:
    assert request['budget']['toolCalls'] == 0, 'a correction round must carry only the remaining tools'
identity = {'sessionId': 'buddy-read-only-' + request['callId']}
ok = {'status': 'ok', 'rawAnswer': '{"choice":"a"}', 'resolved': request['spec'],
      'observed': None, 'modelStarted': True, 'nativeIdentity': identity,
      'usage': {'toolCalls': 0, 'inputTokens': 8, 'outputTokens': 4},
      'streamComplete': True, 'nativeToolEvents': [], 'nativeToolEventsTruncated': False}
fact = {'nativeIdentity': identity}


def call(name, call_id):
    return [{**fact, 'callId': call_id, 'toolName': name, 'phase': 'start'},
            {**fact, 'callId': call_id, 'toolName': name, 'phase': 'end'}]


def refusal(code):
    return {'status': 'error', 'code': code, 'modelStarted': False,
            'usage': {'toolCalls': 0}, 'nativeToolEvents': [],
            'nativeToolEventsTruncated': False, 'streamComplete': False}


if case == 'tools-ok':
    ok['usage'] = {'toolCalls': 2, 'inputTokens': 30, 'outputTokens': 9}
    ok['nativeToolEvents'] = call('read', 'call-1') + call('grep', 'call-2')
elif case in ('correct', 'correct-tools', 'correct-zero'):
    if first_round:
        ok['rawAnswer'] = 'not json at all'
        if case != 'correct':
            ok['usage'] = {'toolCalls': 2, 'inputTokens': 40, 'outputTokens': 12}
            ok['nativeToolEvents'] = call('read', 'call-1') + call('grep', 'call-2')
    elif case == 'correct-tools':
        ok['usage'] = {'toolCalls': 1, 'inputTokens': 21, 'outputTokens': 6}
        ok['nativeToolEvents'] = call('grep', 'call-3')
elif case == 'enum':
    ok['rawAnswer'] = '{"choice":"b"}'
elif case == 'missing-id':
    ok = {'status': 'error', 'code': 'stream-incomplete', 'modelStarted': True,
          'nativeIdentity': identity, 'usage': {'toolCalls': 0},
          'nativeToolEvents': [{**fact, 'toolName': 'read', 'phase': 'start'}],
          'nativeToolEventsTruncated': False, 'streamComplete': False}
elif case == 'native-turn-failed':
    ok = {'status': 'error', 'code': 'native-turn-failed', 'modelStarted': True,
          'nativeIdentity': identity, 'nativeTurnEnd': 'error', 'usage': {'toolCalls': 0},
          'nativeToolEvents': [], 'nativeToolEventsTruncated': False, 'streamComplete': False}
elif case == 'stop-unknown':
    ok = {'status': 'error', 'code': 'stop-unknown', 'modelStarted': True,
          'nativeIdentity': identity, 'usage': {'toolCalls': 0},
          'nativeToolEvents': [], 'nativeToolEventsTruncated': False, 'streamComplete': False}
elif case == 'provider-missing':
    ok = refusal('configuration-unavailable')
elif case.startswith('provider-stage-'):
    ok = refusal('configuration-unavailable')
    ok['failureStage'] = case.removeprefix('provider-stage-') if case != 'provider-stage-invalid' else ['private detail']
elif case == 'tools-unavailable':
    ok = refusal('read-only-tools-unavailable')
elif case == 'resolved-provider':
    ok['resolved'] = {**request['spec'], 'provider': 'other-provider'}
elif case == 'resolved-model':
    ok['resolved'] = {**request['spec'], 'model': 'other-model'}
elif case == 'resolved-effort':
    ok['resolved'] = {**request['spec'], 'effort': 'low'}
elif case == 'usage-mismatch':
    ok['usage'] = {'toolCalls': 5, 'inputTokens': 8, 'outputTokens': 4}
    ok['nativeToolEvents'] = call('read', 'call-1')
elif case == 'identity-invalid':
    ok['nativeIdentity'] = {'sessionId': ''}
elif case == 'events-missing':
    ok.pop('nativeToolEvents')
elif case == 'truncated-missing':
    ok.pop('nativeToolEventsTruncated')
elif case == 'model-not-started':
    ok['modelStarted'] = False
elif case == 'tool-budget-exhausted':
    ok.update(status='error', code='tool-budget-exhausted', streamComplete=False)

Path(config['outputFile']).write_text(json.dumps(ok))
sys.exit(0 if ok['status'] == 'ok' else 1)
