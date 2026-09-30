#!/usr/bin/env python3
"""Prepare one tiny fast route; --execute requires a separately approved native call.

No board, daily service or execution Worker is started. Only the Router call is
run. Every receipt and harness session is under the explicit private output root.
An exclusive execution marker refuses repeat model calls against the same packet.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import uuid

from buddy import router, selection_policy
from buddy.adapters.base import ExecutionContext
from buddy.adapters.decision import DecisionAdapter
from buddy.adapters.turn_io import private_json
from buddy.db import canonical_json, sha256_text


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--adapter', choices=('dsh', 'zcode'), required=True)
    result.add_argument('--provider', required=True)
    result.add_argument('--model', required=True)
    result.add_argument('--effort', required=True)
    result.add_argument('--output-root', type=Path, required=True)
    result.add_argument('--execute', action='store_true', help='Only after separate user approval for this invocation')
    return result


def packet(args):
    profiles = [{**profile, 'profileId': ':'.join(profile.values()), 'enabled': True, 'available': True}
                for profile in (
                    {'adapter': 'dsh', 'provider': 'deepseek-official', 'model': 'deepseek-flash', 'effort': 'off'},
                    {'adapter': 'codex', 'provider': 'openai', 'model': 'gpt-6-sol', 'effort': 'high'})]
    preferences = [{'match': {'adapter': 'dsh', 'model': 'deepseek-flash', 'effort': 'off'},
                    'reason': '本次只是一个极小的文档标点修正，优先考虑此配置。'}]
    return {
        'profile': {key: getattr(args, key) for key in ('adapter', 'provider', 'model', 'effort')},
        'task': '极小任务：将 README 中一句中文句末的英文句点改成中文句号。只选择执行配置，不执行修改。',
        'routingMode': 'fast', 'requestedRoutingMode': 'fast', 'fallback': None,
        'captureEvidence': True,
        'profiles': profiles, 'cards': [], 'preferences': [], 'annotations': [],
        'routingPreferences': preferences,
        'policyFacts': selection_policy.policy_facts(profiles=profiles, routing_preferences=preferences,
                                                    prefer_profile_ids=[], hard_constraints={}),
        'requestId': 'native-fast-check', 'tableRevision': 0,
        'budget': dict(router.FAST_BUDGET),
        'outputSchema': router.answer_schema([p['profileId'] for p in profiles], 'fast'),
    }


def run(args):
    root = args.output_root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if root.stat().st_mode & 0o077:
        raise ValueError('output root must be owner-private')
    document = packet(args)
    prepared = root / 'prepared.json'
    if prepared.exists() and json.loads(prepared.read_text()) != document:
        raise ValueError('the prepared packet differs; use a new output root')
    if not prepared.exists():
        private_json(prepared, document)
    digest = sha256_text(canonical_json(document))
    if not args.execute:
        return {'status': 'prepared', 'modelCalls': 0, 'inputSha256': digest, 'packet': str(prepared)}
    marker = root / 'execution.started'
    with marker.open('x') as stream:
        os.chmod(marker, 0o600)
        stream.write(datetime.now(timezone.utc).isoformat())
    from buddy.checks import SANITIZED_VARIABLES
    environment = {key: value for key, value in os.environ.items() if key not in SANITIZED_VARIABLES}
    environment.update(BUDDY_DEV_SOURCE='1', BUDDY_STATE_DIR=str(root / 'state'),
                       BUDDY_RUNTIME_ROOT=str(root / 'runtime'))
    context = ExecutionContext(task_id=str(uuid.uuid4()), attempt_id=str(uuid.uuid4()), generation=1,
        spec={'adapter': 'decision', 'cwd': str(root), 'timeoutSeconds': 70},
        directory=root / 'attempt', runtime={'identity': 'source:fast-probe'},
        environment=environment, decision_input=document)
    implementation = DecisionAdapter()
    started = time.monotonic()
    handle = None
    report = {'status': 'failed', 'inputSha256': digest, 'routingProfile': document['profile'],
              'startedAt': datetime.now(timezone.utc).isoformat(), 'privateRoot': str(root)}
    try:
        handle = implementation.start(context)
        if handle.wait(timeout=70) is None:
            implementation.cancel(handle)
            handle.wait(timeout=15)
        outcome = implementation.collect(handle, context)
        report.update(status='passed' if outcome.status == 'ok' and outcome.shutdown_confirmed else 'failed',
                      result=outcome.result, shutdownConfirmed=outcome.shutdown_confirmed, error=outcome.error)
    finally:
        if handle and not handle.shutdown_confirmed():
            implementation.cancel(handle)
        report['elapsedSeconds'] = round(time.monotonic() - started, 3)
        private_json(root / 'report.json', report)
    return report


if __name__ == '__main__':
    report = run(parser().parse_args())
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(0 if report['status'] in ('prepared', 'passed') else 1)
