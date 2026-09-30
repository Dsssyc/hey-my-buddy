"""Declared attempt evidence; native state and credentials never match this policy.

Add a writer and its invariant test alongside every new category. Matching is
structural and exact, never a glob such as ``*.json`` or a private-tree exclusion.
"""
from pathlib import PurePosixPath
import re

POLICY = 'attempt-evidence-v1'
FILES = frozenset({
    'task.txt', 'turn-input.json', 'turn-output.json', 'result.json',
    'spawn.intent', 'spawn.marker', 'harness-selection.json', 'harness-prestart-failure.json',
    'runner.stdout.log', 'runner.stderr.log', 'native.stderr.log',
    'activity.json', 'native-usage.json', 'attention.json',
    'inquiry.results.jsonl', 'inquiry.sock.error.json',
    'codex-control.json', 'claude-control.json', 'readonly-control.json', 'no-tool-control.json',
    # Retained historical routing evidence, not produced by the current runner.
    'decision-input.json', 'decision-output.json',
})
DSH_FILES = frozenset({'stdout.log', 'stderr.log', 'session-capture.json', 'capture.json'})
CALL_FILES = frozenset({'request.json', 'result.json', 'stdout.log', 'stderr.log'})
NO_TOOL = re.compile(r'no-tool-[0-9a-f]{32}\Z')
OLD_DSH_RUN = re.compile(r'deepseek-delegate-run-[A-Za-z0-9_-]+\Z')
CALL = re.compile(r'call-[12]\Z')


def _parts(relative) -> tuple[str, ...]:
    value = PurePosixPath(relative)
    if value.is_absolute() or '..' in value.parts:
        return ()
    return value.parts


def is_evidence(relative) -> bool:
    """A path relative to one attempt, including retained old evidence locations."""
    parts = _parts(relative)
    if len(parts) == 1:
        return parts[0] in FILES
    if len(parts) == 2 and (parts[0] == 'dsh-run' or OLD_DSH_RUN.fullmatch(parts[0])):
        return parts[1] in DSH_FILES
    if parts and NO_TOOL.fullmatch(parts[0]):
        if len(parts) == 2:
            return parts[1] == 'native.stderr.log'
        if len(parts) == 3 and CALL.fullmatch(parts[1]):
            return parts[2] in CALL_FILES
    return False


def is_evidence_directory(relative) -> bool:
    """Only containers of declared evidence may be walked by backup."""
    parts = _parts(relative)
    if len(parts) == 1:
        return parts[0] == 'dsh-run' or bool(OLD_DSH_RUN.fullmatch(parts[0]) or NO_TOOL.fullmatch(parts[0]))
    return len(parts) == 2 and bool(NO_TOOL.fullmatch(parts[0]) and CALL.fullmatch(parts[1]))
