"""One native read-only tool probe; preparation calls no model.

Each --execute invocation requires separate human approval. The prepared
packet is immutable and an exclusive marker prevents a repeat invocation.
No Buddy board or service is started, and no credentials are inspected.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
import uuid

from buddy import router, router_input, tool_evidence
from buddy.adapters import adapter as adapter_for, read_only
from buddy.adapters.base import ExecutionContext, ReadOnlyStructuredRequest
from buddy.adapters.turn_io import private_json
from buddy.db import canonical_json, sha256_text

ADAPTERS = ('codex', 'claude', 'dsh', 'zcode')
CANDIDATES = ('dsh:deepseek-official:deepseek-flash:off', 'codex:openai:gpt-6-sol:high')


def system_sandbox(adapter):
    # The same shipped platform declaration used by the free eligibility
    # contract; native execution still checks the effective policy itself.
    from buddy.adapters.codex import CodexAdapter
    from buddy.adapters.claude import ClaudeAdapter
    platforms = {'codex': CodexAdapter.system_sandbox_platforms,
                 'claude': ClaudeAdapter.system_sandbox_platforms}
    return sys.platform in platforms.get(adapter, ())


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--adapter', choices=ADAPTERS, required=True)
    for field in ('provider', 'model', 'effort'):
        result.add_argument('--' + field, required=True)
    result.add_argument('--output-root', type=Path, required=True)
    result.add_argument('--execute', action='store_true', help='Only after separate approval for this prepared packet')
    result.add_argument('--expected-packet-sha256', help='The prepared digest covered by the approval; required for --execute')
    return result


def _prepare(root, configuration):
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.is_symlink() or root.stat().st_mode & 0o077:
        raise ValueError('output root must be owner-private')
    path = root / 'prepared.json'
    if path.exists():
        packet = json.loads(path.read_text())
        if packet['configuration'] != configuration or router_input.digest(root / 'frozen') != packet['inputDigest']:
            raise ValueError('the prepared configuration or input changed; use a new packet')
        return packet
    frozen = root / 'frozen'
    frozen.mkdir(mode=0o700)
    marker = uuid.uuid4().hex
    guide = frozen / 'selection-guide.txt'
    guide.write_text(f'Choose profileId {CANDIDATES[1]} and include marker {marker} in the reason.\n')
    guide.chmod(0o600)
    packet = {
        'configuration': configuration,
        'systemSandbox': system_sandbox(configuration['adapter']),
        'binding': {'adapter': configuration['adapter'], 'taskId': 'probe-' + uuid.uuid4().hex,
                    'attemptId': str(uuid.uuid4()), 'generation': 1},
        'expected': {'profileId': CANDIDATES[1], 'marker': marker},
        'inputDigest': router_input.digest(frozen),
        'request': {'prompt': ('Read selection-guide.txt using one native sandboxed command. ' if configuration['adapter'] == 'codex'
                              else 'Read selection-guide.txt using the native read tool. ') + 'Follow its choice and marker instructions. '
                              'Return one Router selection and cite selection-guide.txt. Do not modify files or request other tools.',
                    'outputSchema': router.answer_schema(list(CANDIDATES), 'review'),
                    'budget': router.budget('brief')},
    }
    private_json(path, packet)
    return packet


def evaluate(packet, outcome, *, elapsed_ms, unchanged):
    result = outcome.result
    evidence = result.get('toolEvidence')
    roots = evidence.get('nativeIdentity', []) if isinstance(evidence, dict) else []
    reported = result.get('nativeIdentity')
    reported = reported if isinstance(reported, list) else [reported]
    usage = result.get('usage') or {}
    limits = packet['request']['budget']
    sandbox = system_sandbox(packet['configuration']['adapter'])
    try:
        answer = router.validate_answer(result.get('rawAnswer'), list(CANDIDATES), 'review')
    except Exception:
        answer = None
    tool_problem = tool_evidence.judge_tool_evidence(evidence, 'review', sandbox)
    counts_match = (isinstance(evidence, dict) and type(usage.get('toolCalls')) is int
                    and usage['toolCalls'] == evidence.get('toolCalls'))
    checks = {
        'nativeOutcome': outcome.status == 'ok',
        'shutdown': outcome.shutdown_confirmed is True and
                    (result.get('processState') or {}).get('shutdownConfirmed') is True,
        'configuration': all((result.get('resolved') or {}).get(key) == value
                             for key, value in packet['configuration'].items() if key != 'adapter'),
        'binding': isinstance(evidence, dict) and evidence.get('binding') == packet['binding'],
        'policyClass': type(packet.get('systemSandbox', False)) is bool
                       and packet.get('systemSandbox', False) is sandbox,
        'nativeIdentity': bool(reported) and all(isinstance(item, dict) and item in roots for item in reported),
        'toolEvidence': tool_problem is None,
        'readCompleted': isinstance(evidence, dict) and any((event.get('category') == 'read'
                                                           or sandbox and event.get('category') == 'execute')
                                                          and event.get('phase') == 'end'
                                                          for event in evidence.get('events', [])),
        'toolBudget': counts_match and 0 < usage['toolCalls'] <= limits['toolCalls'],
        'deadline': 0 <= elapsed_ms <= limits['timeoutSeconds'] * 1000,
        'correctionBudget': type(result.get('correctionCount')) is int and 0 <= result['correctionCount'] <= 1,
        'inputUnchanged': unchanged,
        'readResult': answer is not None and answer['profileId'] == packet['expected']['profileId']
                      and packet['expected']['marker'] in answer['reason'],
    }
    return {'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
            'toolProblem': tool_problem, 'result': result, 'elapsedMs': elapsed_ms,
            'bytesRead': usage.get('bytesRead'), 'systemSandbox': sandbox}


def run(args):
    configuration = {key: getattr(args, key) for key in ('adapter', 'provider', 'model', 'effort')}
    if configuration['adapter'] not in ADAPTERS or any(not isinstance(value, str) or not value.strip()
                                                      for value in configuration.values()):
        raise ValueError('a complete supported native configuration is required')
    root = args.output_root.expanduser().absolute()
    packet = _prepare(root, configuration)
    digest = sha256_text(canonical_json(packet))
    if not args.execute:
        return {'status': 'prepared', 'modelCalls': 0, 'packetSha256': digest,
                'packet': str(root / 'prepared.json'), 'budget': packet['request']['budget'],
                'maxAnswerRounds': 1 if configuration['adapter'] == 'claude' else 2,
                'systemSandbox': system_sandbox(configuration['adapter'])}
    if getattr(args, 'expected_packet_sha256', None) != digest:
        raise ValueError('execution requires the exact prepared packet digest covered by approval')
    with (root / 'execution.started').open('x') as stream:
        os.chmod(stream.fileno(), 0o600)
        stream.write(datetime.now(timezone.utc).isoformat() + '\n')
    from buddy.checks import SANITIZED_VARIABLES, create_private_root, teardown_private_root
    # Native app servers put Unix sockets in TMPDIR; repository paths can
    # exceed the socket-address limit before any model input is admitted.
    private = create_private_root(directory=Path('/tmp') if os.name == 'posix' else Path(tempfile.gettempdir()))
    environment = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES}
    environment.update(BUDDY_DEV_SOURCE='1', BUDDY_STATE_DIR=str(private / 'state'),
                       BUDDY_RUNTIME_ROOT=str(private / 'runtime'), TMPDIR=str(private / 'tmp'))
    context = ExecutionContext(task_id=packet['binding']['taskId'], attempt_id=packet['binding']['attemptId'],
        generation=packet['binding']['generation'], spec={**configuration, 'cwd': str(root / 'frozen')},
        directory=private / 'attempt', runtime={}, environment=environment)
    request = ReadOnlyStructuredRequest(str(root / 'frozen'), **{
        'prompt': packet['request']['prompt'], 'output_schema': packet['request']['outputSchema'],
        'budget': packet['request']['budget'], 'capture_evidence': False})
    native = adapter_for(configuration['adapter'])
    handle = None
    started = time.monotonic()
    report = {'status': 'failed', 'packetSha256': digest, 'configuration': configuration,
              'modelCalls': None, 'systemSandbox': system_sandbox(configuration['adapter'])}
    try:
        handle = native.start_read_only_structured(context, request)
        if handle.wait(timeout=request.budget['timeoutSeconds'] + 10) is None:
            native.cancel(handle)
            handle.wait(timeout=15)
        outcome = read_only.collect(handle)
        report.update(evaluate(packet, outcome, elapsed_ms=round((time.monotonic() - started) * 1000),
                               unchanged=router_input.digest(root / 'frozen') == packet['inputDigest']))
    except (Exception, KeyboardInterrupt) as error:
        report.update(error=type(error).__name__, reason='The probe failed; no automatic retry is authorized')
    finally:
        if handle is not None:
            if not handle.shutdown_confirmed():
                native.cancel(handle)
            report['ownedGroupShutdownConfirmed'] = handle.shutdown_confirmed()
            if not report['ownedGroupShutdownConfirmed']:
                report['status'] = 'failed'
        # Retain only the controller logs, never native credentials/provider
        # snapshots. The test framework owns and verifies runtime/state cleanup.
        if handle is None or report.get('ownedGroupShutdownConfirmed') is True:
            logs = root / 'logs'
            logs.mkdir(mode=0o700, exist_ok=True)
            for name in ('stdout.log', 'stderr.log', 'native.stderr.log'):
                source = context.directory / name
                if source.is_file():
                    shutil.copyfile(source, logs / name)
                    (logs / name).chmod(0o600)
            cleanup = teardown_private_root(private)
            report['privateRootCleanupConfirmed'] = not bool(cleanup)
            if cleanup:
                report['status'] = 'failed'
        else:
            report['privateRootCleanupConfirmed'] = False
        private_json(root / 'report.json', report)
    return report


if __name__ == '__main__':
    report = run(parser().parse_args())
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report['status'] in ('prepared', 'passed') else 1)
