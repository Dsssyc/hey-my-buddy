#!/usr/bin/env python3
"""Deterministic no-tool DSH protocol fixture; no model and no network.

The tests copy this script into a private directory and select one scenario
with the ``case`` file next to it, then present it to the controller as the
owned ``dsh`` command. It replays the two native calls the real CLI makes:
``--dump-config`` prints the folded headless rows, and the sentinel run reads
the mounted plugin's request file and writes its result file exactly like
``no-tool-structured.mjs``, including the native tool facts that stream
carried.
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

if '--dump-config' in args:
    print('- id: headless-runner')
    print('  disabled: ' + ('false' if case == 'unsafe' else 'true'))
    print('- id: buddy-no-tool-structured')
    print('  name: ' + plugin['name'])
    sys.exit(0)

if case == 'timeout':
    time.sleep(5)
if case == 'truncated':
    sys.exit(0)

request = json.loads(Path(config['requestFile']).read_text())
assert 'Return only one JSON value matching this schema:' in request['prompt']
identity = {'callId': request['callId']}
ok = {'status': 'ok', 'rawAnswer': '{"choice":"a"}', 'resolved': request['spec'],
      'observed': None, 'modelStarted': True, 'nativeIdentity': identity,
      'usage': {'toolCalls': 0, 'inputTokens': 8, 'outputTokens': 4},
      'streamComplete': True, 'nativeToolsDisabled': True,
      'nativeChunkCount': 3, 'nativeToolSchemaCount': 0,
      'nativeToolEvents': [], 'nativeToolEventsTruncated': False}
fact = {'nativeIdentity': identity}


def violation(events, *, complete=True):
    return {'status': 'error', 'code': 'no-tool-violation', 'modelStarted': True,
            'nativeFailure': {'finishKind': 'tool-calls' if complete else None},
            'nativeToolEvents': events, 'nativeToolEventsTruncated': False,
            'streamComplete': complete, 'nativeChunkCount': 9}


if case == 'enum':
    ok['rawAnswer'] = '{"choice":"b"}'
if case == 'correct' and 'call-1' in config['requestFile']:
    ok['rawAnswer'] = 'bad JSON'
if case == 'tool':
    result = violation([])
elif case == 'tool-ids':
    result = violation([
        {**fact, 'callId': 'delta-call-1', 'toolName': 'shell', 'phase': 'start'},
        {**fact, 'callId': 'block-call-1', 'toolName': 'shell', 'phase': 'start'},
        {**fact, 'callId': 'block-call-1', 'type': 'tool-result', 'phase': 'end'}])
elif case == 'tool-unknown':
    result = violation([
        {**fact, 'callId': 'mcp-call-1', 'toolName': 'mcp-server__lookup', 'phase': 'start'},
        {**fact, 'callId': 'nameless-1', 'type': 'tool-call', 'phase': 'start'}])
elif case == 'tool-missing-id':
    result = violation([{**fact, 'type': 'tool-call', 'phase': 'start'}])
elif case == 'events-truncated':
    result = violation([{**fact, 'callId': f't-{number}', 'toolName': 'shell', 'phase': 'start'}
                        for number in range(128)])
    result['nativeToolEventsTruncated'] = True
elif case == 'stream-broken':
    result = {'status': 'error', 'code': 'invalid-native-result', 'modelStarted': True,
              'nativeToolEvents': [{**fact, 'callId': 'lost-call-1', 'toolName': 'shell', 'phase': 'start'}],
              'nativeToolEventsTruncated': False, 'streamComplete': False, 'nativeChunkCount': 4}
elif case == 'native-turn-failed':
    result = {'status': 'error', 'code': 'native-turn-failed', 'modelStarted': True,
              'nativeFailure': {'finishKind': 'error', 'code': 'provider_stream_failed', 'status': 503},
              'nativeToolEvents': [], 'nativeToolEventsTruncated': False,
              'streamComplete': True, 'nativeChunkCount': 6}
elif case == 'ok-with-events':
    ok['nativeToolEvents'] = [{**fact, 'callId': 'sneaky-call-1', 'toolName': 'shell', 'phase': 'start'}]
    result = ok
elif case == 'ok-truncated':
    ok['nativeToolEventsTruncated'] = True
    result = ok
else:
    result = ok

Path(config['outputFile']).write_text(json.dumps(result))
sys.exit(0 if result['status'] == 'ok' else 1)
