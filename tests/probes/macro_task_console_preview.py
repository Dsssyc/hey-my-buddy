#!/usr/bin/env python3
"""Deterministic synthetic preview server for the macro task console.

Safety boundary:

* Every ``/api/`` byte is a synthetic fixture declared in this file. Nothing is
  read from a board, state directory, credential or running service, and no
  daemon, Worker, model call or production connection is started. Responses are
  labelled by the ``X-Hey-My-Buddy-Preview: synthetic-fixture-data`` header.
* The server binds ``127.0.0.1`` only (ephemeral port by default).
* It never reads or sets a browser cookie. ``hey_my_buddy_console_session`` is scoped
  to host ``127.0.0.1`` without a port, so emitting one here could clobber a
  real private console on another port; the fixed synthetic session and CSRF
  strings exist only inside the JSON snapshot.
* ``POST /api/command`` serves the
  read-only operations the real console allows (``workflow_get``,
  ``message_list``, ``selection_get``, ``selection_list``, ``buddy_list``)
  with fixed-shape fixture payloads, and answers a
  check of the harnesses (``capabilities``) with the same synthetic rows
  without probing anything. Operations the
  0.15.1 browser allowlist removed (decide/continue/acknowledge/takeover/
  per-run cancel/retry and friends) answer ``METHOD_NOT_FOUND`` exactly like
  the real console; the still-allowed writes (settings operations and
  ``macro_task_stop``) are refused here with ``CONSOLE_READ_ONLY``. The deletion
  preview and confirmed deletion operate only on an instance's in-memory
  synthetic records; no files, processes, sessions or Git objects are removed.
* Fixture records reuse the same field names as the console DTOs
  (``apps/console/src/macro-task-types.ts`` and the console snapshot/task views).
  Reads use one fixed page with a synthetic event head and
  ``nextCursor`` is always null. Reads stay identical until a synthetic deletion,
  which advances that instance's cursor and removes the target from later reads.

Usage::

    python tests/probes/macro_task_console_preview.py --assets ABS_PATH
    python tests/probes/macro_task_console_preview.py --assets ABS_PATH --scenario truncated
    python tests/probes/macro_task_console_preview.py --assets ABS_PATH --smoke
    python tests/probes/macro_task_console_preview.py --check
    python tests/probes/macro_task_console_preview.py --emit-fixtures DIR

Scenarios: ``normal`` (complete, writable-seeming), ``readonly`` (superseded
session), ``truncated`` (explicit row/span/event truncation), ``error``
(timeline reads fail with a 503 envelope while the list stays readable).

``--emit-fixtures DIR`` writes the deterministic fixture tree a frontend
regression test consumes: the exact JSON bodies of every scenario plus
``manifest.json``, which lists each file with its endpoint, its exact request
parameters, its HTTP status and the parser outcome (``accepted``, or
``rejected`` with the expected ``ApiError`` code). ``incompatible/`` holds
deliberately wrong shapes a strict parser must refuse; each documents its
``mutation``. The mode needs no ``--assets`` and writes nothing else.

ADR-018 (schema 15) fields carried by the fixture data: micro task
``workflow_get`` views add ``buddyLocked`` and ``hostConclusion``;
artifacts add ``partial``/``verified``/``final`` flags, ``kind`` may be
``partial-output`` and an artifact may carry ``cumulativePatch``; integration
verification adds ``hostPaths``; task views, attempts and turns carry a
per-attempt ``tokenUsage`` (``null`` when unknown, never a zero); and the
console snapshot adds four harness rows with retained ``quota`` observations.
Absent or unknown values stay explicitly null.

The asset directory is Host-supplied; the repository's current bundle lives at
``src/hey_my_buddy/console/assets``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PREVIEW_HEADER = "X-Hey-My-Buddy-Preview"
PREVIEW_VALUE = "synthetic-fixture-data"
SCENARIOS = ("normal", "readonly", "truncated", "error")
SYNTHETIC_CSRF = "synthetic-preview-csrf-not-a-credential"
SYNTHETIC_SESSION_ID = "synthetic-preview-session-not-a-credential"
HOST_A = "synthetic-host-alpha"
HOST_B = "synthetic-host-beta"
HOST_B2 = "synthetic-host-beta-2"


def host_actor(identifier: str) -> dict[str, str]:
    return {"role": "host", "id": identifier}


FIXTURE_DAY = datetime(2026, 9, 27, tzinfo=timezone.utc)
OBSERVED_AT = "2026-09-27T10:00:00.000Z"
HEAD = 21
MAX_BODY_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024

MACRO_TASK_ID = re.compile(r"^(macro-[A-Za-z0-9-]{1,120}|run:[A-Za-z0-9._:-]{1,128})$")
RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
INTEGER = re.compile(r"^[0-9]{1,7}$")
CONSOLE_ID = re.compile(r"^[0-9a-f]{24}$")

MACRO_A = "macro-11111111-1111-4111-8111-111111111111"
MACRO_B = "macro-22222222-2222-4222-8222-222222222222"
MACRO_C = "macro-33333333-3333-4333-8333-333333333333"
STANDALONE = "preview-run-c1"
STANDALONE_GROUP = f"run:{STANDALONE}"

PROJECTS = {
    "alpha": {"id": "synthetic-preview-alpha", "path": "/synthetic-preview/hey-my-buddy",
              "label": "hey-my-buddy"},
    "beta": {"id": "synthetic-preview-beta", "path": "/synthetic-preview/dsh-harness",
             "label": "dsh-harness"},
}
CFG_FLASH = {"harness": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "max"}
CFG_CODEX = {"harness": "codex", "provider": "openai", "model": "gpt-5-codex", "effort": "high"}
CFG_GLM = {"harness": "zcode", "provider": "zai", "model": "glm-5", "effort": "high"}
CFG_CLAUDE = {"harness": "claude", "provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
CONFIGS = (CFG_FLASH, CFG_CODEX, CFG_GLM, CFG_CLAUDE)
ROUTER_BUDDY = CFG_CODEX
ROUTER_BUDDY_ID = ":".join(ROUTER_BUDDY[key] for key in ("harness", "provider", "model", "effort"))
ROUTING_FACT_KEYS = ("routerBuddyIds", "routerIdentities", "routerBuddyId", "routerBuddy", "routerIndex",
                     "routingMode", "routingBudget", "routerRetryIntervalSeconds", "budget", "routerProblem")
#: The synthetic model catalogue: every model with its reasoning-effort levels,
#: and the levels that are enabled. The four buddies above, which the runs use,
#: are among them. The other rows only fill the catalogue, so that the models
#: table shows every kind of row: several levels, a model nobody enabled, a
#: pending model, a new one, and a harness that is not logged in.
CATALOG = (
    ("dsh", "deepseek-official", "deepseek-flash", ("off", "max"), ("off", "max")),
    ("dsh", "deepseek-official", "deepseek-v4-pro", ("high", "max"), ()),
    ("codex", "openai", "gpt-5-codex", ("medium", "high", "max"), ("high", "max")),
    ("codex", "openai", "gpt-5-mini", ("low", "medium", "high", "max"), ("high",)),
    ("zcode", "zai", "glm-5", ("high", "max"), ("high",)),
    ("claude", "anthropic", "claude-opus-5-5", ("medium", "high", "max"), ("high",)),
    ("claude", "anthropic", "claude-sonnet-5-5", ("low", "medium", "high", "max"), ()),
    ("claude", "anthropic", "claude-haiku-4-5", ("default",), ()),
)
#: The harness whose buddies cannot work in the preview, and the recorded reason.
LOGGED_OUT_HARNESS = "claude"
PENDING_MODEL = ("zcode", "zai", "glm-5")
#: The one 503 message both the server's error scenario and the emitted
#: ``error/`` fixtures use, so the frontend test asserts the same string.
ERROR_SCENARIO_MESSAGE = "synthetic preview: simulated read failure (--scenario error)"
#: The synthetic harness quota failure the schema-15 fixtures model.
QUOTA_ERROR = "synthetic preview: harness quota exhausted before completion"
#: The ADR-018 Host conclusion: the Host's own recorded target outcome of a
#: failed or cancelled micro task, never a worker success claim. ``buddyLocked``
#: is separate: a Host submission may be re-chosen, a user-locked one may not.
HOST_CONCLUSION_KEYS = {"conclusionId", "attemptId", "executionStatus", "note", "reason", "evidence",
                        "artifactId", "integrationId", "actor", "createdAt", "ownerGeneration",
                        "runRevision"}
TOKEN_USAGE_KEYS = {"inputTokens", "cachedInputTokens", "outputTokens", "source", "scope"}

#: ``--emit-fixtures`` manifest: the scenario order and exact fixture files
#: (entries are sorted by scenario, then path).
EMIT_SOURCE = "tests/probes/macro_task_console_preview.py"
EMIT_SCENARIOS = ("normal", "readonly", "truncated", "error")
EMIT_LAYOUT = {
    "normal": ("console.json", "macro-tasks.json", "messages-preview-run-a1.json", "messages-preview-run-a3.json",
               "runtime-version.json", "tasks.json",
               "timeline-macro-a.json", "timeline-macro-b.json", "timeline-macro-c.json", "timeline-standalone.json",
               "workflow-audit-preview-run-a1.json", "workflow-audit-preview-run-a2.json",
               "workflow-preview-run-a1.json", "workflow-preview-run-a2.json",
               "workflow-preview-run-b1-h1.json", "workflow-preview-run-b1.json", "workflow-preview-run-b10.json",
               "workflow-preview-run-b2.json", "workflow-preview-run-b3.json",
               "workflow-preview-run-b4.json", "workflow-preview-run-b5.json",
               "workflow-preview-run-b6.json", "workflow-preview-run-b7.json", "workflow-preview-run-b8.json",
               "workflow-preview-run-b9.json", "workflow-preview-run-d1.json", "workflow-preview-run-d2.json"),
    "readonly": ("console.json", "macro-tasks.json", "timeline-macro-a.json", "workflow-preview-run-a1.json"),
    "truncated": ("console.json", "macro-tasks.json", "timeline-macro-a.json", "workflow-preview-run-a1.json"),
    "error": ("console.json", "macro-tasks.json", "timeline-macro-a.json", "workflow-preview-run-a1.json"),
    "incompatible": ("harness-quota-malformed.json",
                     "snapshot-legacy-single-router.json", "snapshot-missing-session.json",
                     "snapshot-missing-tasks.json", "workflow-missing-revision.json"),
}

MARKER_EVENTS = {"dispatch": "task.submitted", "decide": "workflow.request_approved",
                 "continue": "workflow.continued", "integrate": "workflow.integration_recorded",
                 "accept": "workflow.acknowledged", "reject": "workflow.acknowledged",
                 "conclude": "workflow.host_concluded",
                 "cancel": "workflow.cancelled", "takeover": "workflow.takeover"}
#: The preview's browser command allowlist: five reads plus settings operations
#: and the two synthetic deletion operations. Everything else is METHOD_NOT_FOUND.
ALLOWED_OPERATIONS = (
    "selection_get", "selection_list", "buddy_list", "workflow_get", "message_list", "user_policy_publish",
    "model_catalog_refresh", "macro_task_stop", "capabilities", "reminder_settings_set",
    "macro_task_deletion_preview", "macro_task_delete",
)
#: Operations the 0.15.1 read-only repair removed from the browser surface.
REMOVED_OPERATIONS = (
    "task_cancel", "task_retry", "task_acknowledge", "workflow_submit",
    "workflow_decide", "workflow_continue", "workflow_takeover", "workflow_cancel",
    "workflow_acknowledge", "workflow_scope_amend", "workflow_workspace_resolve",
    "workflow_integration_record", "workspace_cleanup_plan", "workspace_cleanup_apply", "workflow_suggest",
)
#: The exact read-only operation set of the real console (console_sessions.py).
#: A 403 CONSOLE_READ_ONLY would latch the browser session as read-only, so every
#: one of them must be answered instead of refused.
READ_OPERATIONS = ("workflow_get", "message_list", "selection_get", "selection_list", "buddy_list")

STATUS_BY_CODE = {"INVALID_ARGUMENT": 400, "METHOD_NOT_FOUND": 404, "NOT_FOUND": 404, "FORBIDDEN": 403,
                  "CONSOLE_READ_ONLY": 403, "UNSUPPORTED": 501, "SERVICE_UNAVAILABLE": 503,
                  "INTERNAL_ERROR": 500, "MESSAGE_TOO_LARGE": 413,
                  "REVISION_CONFLICT": 409, "MACRO_TASK_NOT_SETTLED": 409}

ASSET_TYPES = {
    ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8", ".map": "application/json; charset=utf-8",
    ".svg": "image/svg+xml", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".webp": "image/webp", ".gif": "image/gif", ".ico": "image/x-icon", ".woff": "font/woff",
    ".woff2": "font/woff2", ".ttf": "font/ttf", ".txt": "text/plain; charset=utf-8",
    ".wasm": "application/wasm",
}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "font-src 'self'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")

SETUP_PAGE = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>工作目标预览：合成夹具</title>
<style>body{{font:16px system-ui,sans-serif;max-width:760px;margin:8vh auto;padding:0 24px;color:#20332e;background:#f4f7f5}}
code{{background:#e5ece8;padding:2px 6px;border-radius:4px}}pre{{background:#fff;padding:16px;border-radius:10px;white-space:pre-wrap}}</style>
<h1>合成预览：资产目录没有 index.html</h1>
<p><strong>此地址只提供合成夹具数据，不是真实黑板，也不会启动任何后台进程。</strong></p>
<p><code>{assets}</code> 下缺少 <code>index.html</code>；JSON 接口仍然可用：</p>
<pre>{endpoints}</pre></html>"""


class PreviewError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def error_envelope(code: str, message: str, **details: Any) -> dict:
    """The one error body the server sends and the emitted error fixtures reuse."""
    return {"ok": False, "error": {"code": code, "message": message, **({"details": details} if details else {})}}


def instant(hour: int, minute: int, second: int = 0) -> str:
    return (FIXTURE_DAY + timedelta(hours=hour, minutes=minute, seconds=second)
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def long_title(text: str, limit: int = 200) -> str:
    pad = " synthetic-preview-long-title-padding"
    value = text
    while len(value) < limit:
        value += pad
    return value[:limit]


def digest(tag: str) -> str:
    return hashlib.sha256(f"synthetic-preview:{tag}".encode()).hexdigest()


def reason(code: str, **params: Any) -> dict:
    """A program fact; all author-supplied text lives in a separate note."""
    return {"code": code, "params": params}


# --------------------------------------------------------------------------- #
# Fixture records (the payloads themselves, not a board simulation)            #
# --------------------------------------------------------------------------- #
def attempt(attempt_id: str, turn_index: int, state: str, confirmed: bool, started: str,
            finished: str | None, buddy: dict | None, *, error: str | None = None,
            result: str | dict | None = None, disposition: str = "completed",
            usage: dict | None = None, expected_seconds: int = 1800,
            stop_initiator: str | None = None, cancel_requested_at: str | None = None,
            grace_deadline: str | None = None, result_status: str | None = None,
            termination_reason: str | None = None, release_reason: dict | None = None) -> dict:
    # `result` is the stored worker receipt: {"status", "result", "shutdownConfirmed"},
    # exactly what store.task_result unpacks; the adapter payload nests under `result`.
    # `usage` is the ADR-018 per-attempt token usage: null when the record is
    # unknown, never a synthetic zero.
    receipt = None if result is None else {"status": result_status or ("failed" if error else "ok"),
                                           "result": result if isinstance(result, dict) else {"finalText": result}, "shutdownConfirmed": confirmed,
                                           "terminationReason": termination_reason or ("harness-error" if error else "completed")}
    return {"attemptId": attempt_id, "turnId": f"turn-{attempt_id}", "turnIndex": turn_index,
            "generation": turn_index, "state": state, "shutdownConfirmed": confirmed,
            "startedAt": started, "finishedAt": finished, "buddy": buddy,
            "error": error, "result": receipt, "disposition": disposition, "usage": usage,
            "expectedSeconds": expected_seconds,
            "stopInitiator": stop_initiator, "cancelRequestedAt": cancel_requested_at,
            "graceDeadline": grace_deadline,
            "releaseReason": release_reason,
            "uncertain": state == "uncertain" or (state == "finished" and not confirmed)}


def usage(source: str, input_tokens: int | None, cached_input_tokens: int | None,
          output_tokens: int | None) -> dict:
    """One ADR-018 per-attempt token usage record (never session-cumulative).

    ``inputTokens`` always includes the cached tokens; ``cachedInputTokens`` is
    a subset and must not be added again.
    """
    return {"inputTokens": input_tokens, "cachedInputTokens": cached_input_tokens,
            "outputTokens": output_tokens, "source": source, "scope": "attempt"}


def patch_for(run_id: str, output_commit: str, paths: list[str]) -> dict:
    """A synthetic cumulative patch from the run workspace input to an output."""
    return {"baseCommit": digest(f"{run_id}-input")[:40], "outputCommit": output_commit,
            "path": f"/synthetic-preview/patches/{run_id}.patch", "sha256": digest(f"{run_id}-patch"),
            "changedPaths": list(paths)}


def host_conclusion(conclusion_id: str, *, attempt_id: str | None, execution_status: str, note: str,
                    evidence: list[str], artifact_id: str | None, integration_id: str | None,
                    actor: dict[str, str], created_at: str, owner_generation: int = 1,
                    run_revision: int = 3) -> dict:
    """An ADR-018 Host conclusion: the recorded target outcome of a failed micro task."""
    return {"conclusionId": conclusion_id, "attemptId": attempt_id, "executionStatus": execution_status,
            "note": note, "reason": None, "evidence": list(evidence), "artifactId": artifact_id,
            "integrationId": integration_id, "actor": actor, "createdAt": created_at,
            "ownerGeneration": owner_generation, "runRevision": run_revision}


def span(span_id: str, kind: str, start: str, end: str | None, state: str, *, attempt_id: str | None = None,
         request_id: str | None = None, buddy: dict | None = None,
         confirmed: bool | None = None, uncertain: bool = False, generation: int | None = None,
         disposition: str | None = None, error: str | None = None, request_kind: str | None = None,
         summary: str | None = None, decision_task: str | None = None,
         turn_index: int | None = None, result_status: str | None = None,
         routing_record: dict | None = None) -> dict:
    view = {"spanId": span_id, "kind": kind, "startAt": start, "endAt": end, "state": state,
            "attemptId": attempt_id, "turnId": f"turn-{attempt_id}" if attempt_id and kind != "routing" else None,
            "turnIndex": turn_index, "requestId": request_id, "buddy": buddy,
            "shutdownConfirmed": confirmed, "uncertain": uncertain,
            "clockSkew": bool(start and end and end < start)}
    extras = {"generation": generation, "disposition": disposition, "error": error,
              "requestKind": request_kind, "summary": summary, "decisionTaskId": decision_task}
    view.update({key: value for key, value in extras.items() if value is not None})
    if routing_record is not None:
        view["decisionId"] = routing_record["decisionId"]
        view["routing"] = {key: routing_record[key] for key in
                           (*ROUTING_FACT_KEYS, "selectedBuddy", "reason", "note", "policyCheck", "usage")}
    if kind in ("execution", "routing") and state == "finished":
        view["resultStatus"] = result_status or ("failed" if error else "ok")
    return view


def marker(at: str, kind: str, *, actor: dict[str, str] | None = None, note: str | None = None,
           **extra: Any) -> dict:
    names = {"request_id": "requestId", "artifact_id": "artifactId", "integration_id": "integrationId",
             "continuation_id": "continuationId", "attempt_id": "attemptId"}
    return {"seq": 0, "runId": None, "kind": kind, "at": at,
            "decision": None, "verdict": None, "state": None, "strategy": None, "note": note,
            "actor": actor, "attemptId": None,
            "requestId": None, "artifactId": None, "integrationId": None, "continuationId": None,
            "eventKind": MARKER_EVENTS[kind], **{names.get(key, key): value for key, value in extra.items()}}


ACTIVE_REQUEST = {
    "requestId": "req-a2-2", "kind": "assistance", "routing": False, "state": "open",
    "createdAt": instant(9, 20), "decidedAt": None,
    "summary": "需要 Host 决定是否允许在合成预览中访问外部样例仓库（等待中）",
    "attempted": "合成预览请求已执行到需要 Host 决定的位置",
    "neededWork": ["由 Host 确认合成预览范围"], "expectedArtifacts": [],
    "acceptance": "合成预览：仅检查渲染分支，不产生真实验收", "childTaskId": None, "reason": None,
}
LEAD_REQUEST = {
    "requestId": "req-b4-1", "kind": "assistance", "routing": False, "state": "open",
    "createdAt": instant(7, 25), "decidedAt": None,
    "summary": "需要 Lead 决定路径样例的核对范围",
    "attempted": "已核对合成路径样例，等待 Lead 处理差异",
    "neededWork": ["由 Lead 确定后续核对范围"], "expectedArtifacts": [],
    "acceptance": "合成预览：只展示等待 Lead 的请求", "childTaskId": None, "reason": None,
}
def routing(decision_id: str, task_id: str, status: str, selected: dict | None, note: str,
            constraints: dict, *,
            attempt_id: str,
            user_outcome: str = "none",
            elapsed_ms: int = 180000, tool_calls: int = 3, bytes_read: int = 32768,
            mode: str = "review",
            program_reason: dict | None = None) -> dict:
    """One recorded routing decision, shaped like ``workflow._routing_view``."""
    return {"status": status, "decisionId": decision_id, "taskId": task_id, "attemptId": attempt_id,
            "generation": 1, "selectedBuddy": selected, "reason": program_reason, "note": note,
            "routerBuddyIds": [ROUTER_BUDDY_ID], "routerIdentities": [ROUTER_BUDDY],
            "routerBuddyId": ROUTER_BUDDY_ID, "routerBuddy": ROUTER_BUDDY, "routerIndex": 0,
            "routingMode": mode, "routingBudget": "standard", "routerRetryIntervalSeconds": 600, "routerProblem": None,
            "constraints": constraints,
            "requiredCapabilities": [], "routingBasis": None,
            "source": "model-selection" if decision_id else None,
            "policyCheck": {"hardConstraints": constraints,
                            "userPreference": user_outcome},
            # Illustrative fixture values only; these are not runtime defaults.
            "budget": ({"timeoutSeconds": 60} if mode == "fast" else
                       {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24,
                        "bytesRead": 524288}),
            "usage": {"elapsedMs": elapsed_ms, "toolCalls": tool_calls, "bytesRead": None if mode == "fast" else bytes_read}}


ROUTING_A1 = routing("preview-decision-a1", "preview-run-a1-router", "completed", CFG_FLASH,
                     "合成预览：选择 DeepSeek Flash；符合委派偏好，偏离用户偏好以缩短路由等待。",
                     {}, attempt_id="att-router-a1", user_outcome="alternative",
                     mode="fast",
                     elapsed_ms=60000, tool_calls=0, bytes_read=0)
ROUTING_A2 = routing("preview-decision-a2", "preview-run-a2-router", "failed", None,
                     "合成预览：候选超出路由边界，等待 Host 选择配置。", {"harness": "claude"},
                     attempt_id="att-router-a2", program_reason=reason("router-out-of-bounds", bound="constraints"))
ROUTING_A3 = routing("preview-decision-a3", "preview-run-a3-router", "cancelled", None,
                     "合成预览：Host 取消路由并显式指定执行配置。", {"harness": "claude"},
                     attempt_id="att-router-a3", program_reason=reason("decision-cancelled"))
ROUTING_A2_ABSTENTION = routing("preview-decision-a2-abstention", "preview-run-a2-router-abstention",
                               "needs-host", None, "合成预览：证据不足，主动放弃选择并等待 Host。",
                               {"harness": "claude"}, elapsed_ms=60000, tool_calls=2, bytes_read=16384,
                               attempt_id="att-router-a2-abstention", program_reason=reason("router-abstained"))


def decision_result(record: dict) -> dict:
    """The structured result of DecisionAdapter.collect, not a Worker finalText."""
    selected = record["selectedBuddy"]
    ok = record["status"] in ("completed", "needs-host")
    return {
        "operation": "select", "status": "ok" if ok else "error",
        "requested": record["constraints"], "resolved": None, "observed": None,
        "modelStarted": True, "nativeIdentity": None,
        "usage": record["usage"], "toolEvidence": [], "harnessVersion": "synthetic-preview",
        "nativeEvidence": None, "nativeFailure": None, "correctionCount": 0,
        "budget": record["budget"], "inputVerification": None,
        **{key: record[key] for key in ROUTING_FACT_KEYS},
        "zeroToolVerified": record["routingMode"] == "fast",
        "stopEvidence": {"shutdownConfirmed": True, "native": "stopped",
                         "nativeInterruptRequested": False, "nativeInterruptAcknowledged": False},
        **({"decision": {"buddyId": ":".join(selected[key] for key in ("harness", "provider", "model", "effort")) if selected else None,
                         "note": record["note"], "evidence": []}} if ok else
           {"code": "router-out-of-bounds" if record["status"] == "failed" else "cancelled"}),
    }


def run(run_id: str, *, project: str | None, title: str | None, title_source: str, task_text: str, created: str,
        updated: str, state: str, status: str, category: str, buddy: dict | None,
        hosts: tuple[str | None, str | None] = (HOST_A, HOST_A), parent: str | None = None,
        group: str | None = None, lead_owner: tuple[str, str] | None = None,
        accepted: str | None = None, verdict: str | None = None, result: bool = False,
        result_summary: str | None = None, artifacts: list[dict] | None = None,
        integrations: list[dict] | None = None, routing: dict | None = None,
        active_request: dict | None = None, spans: list[dict] | None = None,
        markers: list[dict] | None = None, attempts: list[dict] | None = None,
        shutdown_confirmed: bool = True, micro_task: bool = True, adapter: str | None = None,
        continuation_count: int = 0, expected_seconds: int = 1800,
        router_budget_seconds: int | None = None, revision: int = 3,
        buddy_locked: bool = False, delegation_authority: bool = False,
        conclusion: dict | None = None, messages: list[dict] | None = None,
        continuations: list[dict] | None = None, activity: dict | None = None,
        cancellation: dict | None = None) -> dict:
    return {
        "runId": run_id, "parentRunId": parent, "group": group, "project": project, "title": title,
        "titleSource": title_source, "task": task_text, "createdAt": created, "updatedAt": updated,
        "state": state, "status": status, "category": category, "buddy": buddy,
        "sourceHostId": hosts[0], "currentHostId": hosts[1], "acceptedAt": accepted,
        "leadOwner": lead_owner,
        "acceptanceVerdict": verdict, "resultAvailable": result, "resultSummary": result_summary,
        "artifacts": artifacts or [], "integrations": integrations or [], "routing": routing,
        "activeRequest": active_request, "spans": spans or [], "markers": markers or [],
        "attempts": attempts or [], "shutdownConfirmed": shutdown_confirmed,
        "adapter": adapter, "continuationCount": continuation_count,
        **({"routerBudgetSeconds": router_budget_seconds} if adapter == "decision"
           else {"expectedSeconds": expected_seconds}),
        "revision": revision, "buddyLocked": buddy_locked,
        "delegationAuthority": delegation_authority,
        "hostConclusion": conclusion, "messages": messages or [], "continuations": continuations or [],
        "activity": activity,
        "cancellation": cancellation,
        "kind": ("decision" if adapter == "decision" else "execution") if not micro_task else (
            "helper" if parent is not None else "micro-task"),
    }


def artifact(artifact_id: str, attempt_id: str, commit: str, diff_path: str,
             paths: list[str], *, kind: str = "output", partial: bool = False,
             verified: bool = True, final: bool = True,
             cumulative_patch: dict | None = None) -> dict:
    # ADR-018: a sealed partial output is `partial-output` with
    # partial/verified/final = true/false/false and is never the final artifact.
    view = {"artifactId": artifact_id, "kind": kind, "attemptId": attempt_id,
            "turnId": f"turn-{attempt_id}", "manifestSha256": digest(f"{artifact_id}-manifest"),
            "outputCommit": commit, "diffPath": diff_path, "diffSha256": digest(f"{artifact_id}-diff"),
            "changedPaths": paths, "partial": partial, "verified": verified, "final": final}
    if cumulative_patch is not None:
        view["cumulativePatch"] = cumulative_patch
    return view


def artifact_views(entry: dict) -> list[dict]:
    """Every seal of a run as the compact record lists it. The outputs carry the flags of a partial output only;
    the seal of what the micro task was delegated with has no attempt and no source task, and each turn is
    sealed with its input and its shared refs, none of which has an output."""
    base = digest(f"{entry['runId']}-input")[:40]
    seal = {"path": entry["cwd"], "baseCommit": base, "inputCommit": base}
    seals = [{"artifactId": f"seal-{entry['runId']}-input", "kind": "input", "attemptId": None, "turnId": None,
              "sourceTaskId": None, "manifestSha256": digest(f"{entry['runId']}-input-seal"),
              "createdAt": entry["createdAt"], **seal}]
    for item in entry["attempts"]:
        for kind in ("turn-input", "shared-refs"):
            view = {"artifactId": f"seal-{item['attemptId']}-{kind}", "kind": kind, "attemptId": item["attemptId"],
                    "turnId": item["turnId"], "sourceTaskId": entry["runId"],
                    "manifestSha256": digest(f"{item['attemptId']}-{kind}"), "createdAt": item["startedAt"]}
            seals.append({**view, **seal} if kind == "turn-input" else
                         {**view, "sharedStash": {"state": "observed", "entryCount": 0}})
    outputs = [item if item["kind"] == "partial-output"
               else {key: value for key, value in item.items() if key not in ("partial", "verified", "final")}
               for item in entry["artifacts"]]
    return [*outputs, *seals]


def integration(integration_id: str, artifact_id: str, attempt_id: str, host: str, *,
                host_paths: list[str] | None = None) -> dict:
    return {"integrationId": integration_id, "artifactId": artifact_id, "attemptId": attempt_id,
            "state": "verified", "strategy": "cherry-pick",
            "target": {"kind": "git", "path": PROJECTS["alpha"]["path"],
                       "ref": "refs/heads/synthetic-preview", "repositoryId": digest("repo")[:40],
                       "checkoutId": digest("checkout")[:40]},
            "sourceCommit": digest("source")[:40], "sourceTree": digest("source-tree")[:40],
            "beforeCommit": digest("before")[:40], "afterCommit": digest("after")[:40],
            "beforeTree": digest("before-tree")[:40], "afterTree": digest("after-tree")[:40],
            "verification": {"verified": True, "notRequired": False,
                             "summary": "synthetic preview verification: fixture-only, no real repository",
                             "hostPaths": list(host_paths or [])},
            "notRequired": False, "note": "synthetic preview integration record", "actor": host_actor(host),
            "createdAt": None}


def message(message_id: str, attempt_id: str | None, question: str, *, author: str, created: str, state: str,
            reason: dict | None = None, note: str | None = None,
            delivered: str | None = None, answer: str | None = None,
            answered: str | None = None) -> dict:
    """One inquiry as ``message_list`` returns it; ``taskId`` is set when the run is assembled."""
    return {"messageId": message_id, "taskId": None, "attemptId": attempt_id, "inquiryId": message_id,
            "direction": "question", "author": author, "recipient": "worker", "correlationId": None,
            "question": question, "questionBytes": len(question.encode()),
            "questionSha256": digest(f"question-{message_id}"), "state": state, "reason": reason, "note": note,
            "delivery": {"deliveredAt": delivered} if delivered else None,
            "answer": ({"text": answer, "bytes": len(answer.encode()), "via": "tool", "toolCallId": None,
                        "at": answered, "truncated": False, "source": "native"} if answer else None),
            "attempts": 1, "createdAt": created, "updatedAt": answered or delivered or created, "revision": 2}


def continuation(continuation_id: str, attempt_id: str, text: str, *, request_id: str | None,
                 created: str, note: str | None = None) -> dict:
    """One Host continuation as the audit lists it: the input the next turn was given."""
    return {"continuationId": continuation_id, "kind": "manual", "state": "consumed", "requestId": request_id,
            "note": note, "helperPolicy": "none", "helperOutcomes": [], "input": text,
            "expectedRevision": 3, "attemptId": attempt_id, "turnId": f"turn-{attempt_id}",
            "createdAt": created, "consumedAt": created}


def build_runs() -> dict[str, dict]:
    """The micro tasks, including a parked Lead, and four internal routing rows."""
    runs = [
        run("preview-run-a1", project="alpha", group=MACRO_A,
            title=long_title("工作目标时间轴：预览服务器合成夹具与只读接口验证 —— 这是一个刻意加长的委派标题，"
                             "用来检查列表两行截断、时间轴标签单行省略、详情头部两行截断与 title 完整文本；"),
            title_source="title",
            task_text="设计并实现工作目标时间轴的只读预览夹具，核对列表、时间轴与详情在长标题下的截断。",
            created=instant(6, 0), updated=instant(7, 5), state="accepted", status="completed",
            category="ended", buddy=CFG_CODEX, accepted=instant(7, 5), verdict="accepted",
            result=True, result_summary="合成预览：工作目标时间轴夹具的第一轮交付摘要",
            spans=[
                span("queue:att-a1-1", "queue", instant(6, 0), instant(6, 2), "admitted",
                     attempt_id="att-a1-1"),
                span("routing:att-router-a1", "routing", instant(6, 2), instant(6, 5), "finished",
                     attempt_id="att-router-a1", confirmed=True, decision_task="preview-run-a1-router",
                     disposition="completed", routing_record=ROUTING_A1),
                span("execution:att-a1-1", "execution", instant(6, 5), instant(6, 40), "finished",
                     attempt_id="att-a1-1", buddy=CFG_FLASH, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("host:req-a1-1", "host", instant(6, 40), instant(6, 44), "approved",
                     request_id="req-a1-1", request_kind="assistance",
                     summary="需要 Host 批准在隔离工作树外读取合成样例目录"),
                span("queue:att-a1-2", "queue", instant(6, 45), instant(6, 46), "admitted",
                     attempt_id="att-a1-2"),
                span("execution:att-a1-2", "execution", instant(6, 46), instant(7, 0), "finished",
                     attempt_id="att-a1-2", buddy=CFG_CODEX, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-a1-1", 1, "finished", True, instant(6, 5), instant(6, 40), CFG_FLASH,
                        result="合成预览：第一轮完成并确认停止。",
                        usage=usage("dsh-native", 12000, 9000, 1500)),
                attempt("att-a1-2", 2, "finished", True, instant(6, 46), instant(7, 0), CFG_CODEX,
                        result="合成预览：第二轮完成并确认停止。",
                        usage=usage("codex-native", 20000, 15000, 800), expected_seconds=900),
            ],
            markers=[
                marker(instant(6, 0), "dispatch", actor=host_actor(HOST_A)),
                marker(instant(6, 44), "decide", actor=host_actor(HOST_A), request_id="req-a1-1", decision="approve"),
                marker(instant(6, 45), "continue", actor=host_actor(HOST_A), continuation_id="cont-a1-1"),
                marker(instant(7, 3), "integrate", actor=host_actor(HOST_A), artifact_id="art-a1",
                       integration_id="int-a1", strategy="cherry-pick", state="verified"),
                marker(instant(7, 5), "accept", actor=host_actor(HOST_A), artifact_id="art-a1", verdict="accepted"),
            ],
            artifacts=[artifact("art-a1", "att-a1-2", digest("a1-output")[:40], "/synthetic-preview/diffs/a1.diff",
                                ["tests/probes/macro_task_console_preview.py"],
                                cumulative_patch=patch_for("preview-run-a1", digest("a1-output")[:40],
                                                           ["tests/probes/macro_task_console_preview.py"]))],
            integrations=[integration("int-a1", "art-a1", "att-a1-2", HOST_A,
                                      host_paths=["docs/reference/console.md"])],
            buddy_locked=True,
            routing=ROUTING_A1,
            messages=[
                message("inq-a1-1", "att-a1-1", "目前读到哪个文件了？", author=HOST_A, created=instant(6, 20),
                        state="answered", delivered=instant(6, 20, 4), answered=instant(6, 21),
                        answer="正在核对 tests/probes/macro_task_console_preview.py 的夹具；还剩路径与合成数据两处。"),
                message("inq-a1-2", "att-a1-1", "收到请回复。", author=HOST_A, created=instant(6, 41),
                        state="unavailable", reason=reason("inquiry-no-live-agent")),
                # Put while no turn was executing, and naming no attempt: it belongs to the turn that came next.
                message("inq-a1-3", None, "上一轮停下了吗？", author=HOST_A, created=instant(6, 42),
                        state="discarded", reason=reason("inquiry-root-turn-ended")),
            ],
            continuations=[continuation("cont-a1-1", "att-a1-2", "继续。读取合成样例目录已获批准；把路径边界也核对一遍。",
                                        request_id="req-a1-1", created=instant(6, 45), note="批准读取后继续")]),
        run("preview-run-a1-h1", project="alpha", group=MACRO_A, parent="preview-run-a1",
            title="补充 schema 升级离线副本的验证测试", title_source="title",
            task_text="为 board_prepare 的离线副本补充验证测试，覆盖行数不变与来源文件哈希不变。",
            created=instant(6, 20), updated=instant(6, 55), state="delivered", status="completed",
            category="review", buddy=CFG_GLM, result=True,
            result_summary="补充 schema 升级离线副本的验证测试",
            spans=[
                span("queue:att-h1-1", "queue", instant(6, 20), instant(6, 24), "admitted",
                     attempt_id="att-h1-1"),
                span("execution:att-h1-1", "execution", instant(6, 24), instant(6, 36), "finished",
                     attempt_id="att-h1-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1,
                     error="synthetic preview failure: test command timed out"),
                span("queue:att-h1-2", "queue", instant(6, 36), instant(6, 38), "admitted",
                     attempt_id="att-h1-2"),
                span("execution:att-h1-2", "execution", instant(6, 38), instant(6, 55), "finished",
                     attempt_id="att-h1-2", buddy=CFG_GLM, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-h1-1", 1, "finished", True, instant(6, 24), instant(6, 36), CFG_GLM,
                        error="synthetic preview failure: test command timed out",
                        result="synthetic preview failure: test command timed out",
                        usage=None),
                attempt("att-h1-2", 2, "finished", True, instant(6, 38), instant(6, 55), CFG_GLM,
                        result="合成预览：重试后通过。",
                        usage=usage("zcode-native", 8000, 2000, 1200)),
            ],
            markers=[marker(instant(6, 20), "dispatch", actor=host_actor(HOST_A))],
            artifacts=[artifact("art-a1h", "att-h1-2", digest("a1h-output")[:40],
                                "/synthetic-preview/diffs/a1h.diff", ["tests/python/test_board_prepare.py"])]),
        run("preview-run-a1-h1-h1", project="alpha", group=MACRO_A, parent="preview-run-a1-h1",
            title="核对离线副本的跨平台路径边界", title_source="task",
            task_text="核对 Windows 与 macOS 下离线副本路径大小写与分隔符的边界记录。",
            created=instant(6, 56), updated=instant(7, 2), state="executing", status="reconciliation-needed",
            category="active", buddy=CFG_CLAUDE, result=True, shutdown_confirmed=False,
            result_summary="核对离线副本的跨平台路径边界",
            spans=[
                span("queue:att-h2-1", "queue", instant(6, 56), instant(6, 57), "admitted",
                     attempt_id="att-h2-1"),
                span("execution:att-h2-1", "execution", instant(6, 57), instant(7, 2), "finished",
                     attempt_id="att-h2-1", buddy=CFG_CLAUDE, confirmed=False, uncertain=True,
                     generation=1, disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-h2-1", 1, "finished", False, instant(6, 57), instant(7, 2), CFG_CLAUDE,
                              result="合成预览：结果已收到，但停止未确认。",
                              usage=None)],
            markers=[marker(instant(6, 56), "dispatch", actor=host_actor(HOST_A))]),
        run("preview-run-a2", project="alpha", group=MACRO_A,
            title="修正 Host 等待片段与决定标记的渲染", title_source="title",
            task_text="修正第一轮时间轴中等待 Host 的开放片段、决定标记与协助请求的呈现。",
            created=instant(8, 30), updated=instant(9, 20), state="awaiting-host", status="completed",
            category="host", buddy=CFG_CLAUDE, result=True, continuation_count=1,
            result_summary="核对 Host 等待片段与决定标记的渲染", routing=ROUTING_A2_ABSTENTION,
            active_request=ACTIVE_REQUEST,
            spans=[
                span("queue:att-a2-1", "queue", instant(8, 30), instant(8, 33), "admitted",
                     attempt_id="att-a2-1"),
                span("routing:att-router-a2", "routing", instant(8, 33), instant(8, 36), "finished",
                     attempt_id="att-router-a2", confirmed=True, decision_task="preview-run-a2-router",
                     disposition="completed", result_status="failed", routing_record=ROUTING_A2,
                     error="synthetic preview: routing bounds rejected"),
                span("execution:att-a2-1", "execution", instant(8, 36), instant(8, 55), "finished",
                     attempt_id="att-a2-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("host:req-a2-1", "host", instant(8, 55), instant(9, 0), "declined",
                     request_id="req-a2-1", request_kind="routing",
                     summary="合成预览：路由需要 Host 决定，已按预览策略拒绝"),
                span("routing:att-router-a2-abstention", "routing", instant(9, 0), instant(9, 1), "finished",
                     attempt_id="att-router-a2-abstention", confirmed=True, disposition="abstention",
                     decision_task="preview-run-a2-router-abstention", routing_record=ROUTING_A2_ABSTENTION),
                span("queue:att-a2-2", "queue", instant(9, 1), instant(9, 2), "admitted",
                     attempt_id="att-a2-2"),
                span("execution:att-a2-2", "execution", instant(9, 2), instant(9, 20), "finished",
                     attempt_id="att-a2-2", buddy=CFG_CLAUDE, confirmed=True, generation=2,
                     disposition="assistance", turn_index=2),
                span("host:req-a2-2", "host", instant(9, 20), None, "open", request_id="req-a2-2",
                     request_kind="assistance",
                     summary="需要 Host 决定是否允许在合成预览中访问外部样例仓库（等待中）"),
            ],
            attempts=[
                attempt("att-a2-1", 1, "finished", True, instant(8, 36), instant(8, 55), CFG_GLM,
                        result="合成预览：第一轮结束，交给 Host 决定。",
                        usage=usage("zcode-native", 6000, 4000, 900)),
                attempt("att-a2-2", 2, "finished", True, instant(9, 2), instant(9, 20), CFG_CLAUDE,
                        result="合成预览：第二轮结束，需要 Host 协助。",
                        usage=usage("claude-native", 9500, 7000, 1100)),
            ],
            markers=[
                marker(instant(8, 30), "dispatch", actor=host_actor(HOST_A)),
                marker(instant(9, 0), "decide", actor=host_actor(HOST_A), request_id="req-a2-1", decision="decline"),
                marker(instant(9, 1), "continue", actor=host_actor(HOST_A), continuation_id="cont-a2-1"),
            ],
            continuations=[continuation("cont-a2-1", "att-a2-2", "路由放弃了选择；改用 Claude 继续，保留第一轮的结论。",
                                        request_id="req-a2-1", created=instant(9, 1))]),
        run("preview-run-a3", project="alpha", group=MACRO_A,
            title="修正折叠区间的键盘焦点顺序", title_source="title",
            task_text="修正第一轮时间轴中折叠空闲区间的键盘焦点顺序，并补充可访问性检查。",
            created=instant(9, 35), updated=instant(9, 39), state="executing", status="running",
            category="active", buddy=CFG_CLAUDE, shutdown_confirmed=False, routing=ROUTING_A3,
            expected_seconds=600,
            spans=[
                span("queue:att-a3-1", "queue", instant(9, 35), instant(9, 36), "admitted",
                     attempt_id="att-a3-1"),
                span("routing:att-router-a3", "routing", instant(9, 36), instant(9, 39), "finished",
                     attempt_id="att-router-a3", confirmed=True, decision_task="preview-run-a3-router",
                     disposition="cancelled", result_status="cancelled", routing_record=ROUTING_A3),
                span("execution:att-a3-1", "execution", instant(9, 39), None, "executing",
                     attempt_id="att-a3-1", buddy=CFG_CLAUDE, confirmed=False, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-a3-1", 1, "executing", False, instant(9, 39), None, CFG_CLAUDE,
                              usage=None, expected_seconds=600)],
            markers=[marker(instant(9, 35), "dispatch", actor=host_actor(HOST_A))],
            activity={"phase": "tool-running", "observedAt": instant(9, 41), "eventSeq": 12,
                      "lastToolActivityAt": instant(9, 41), "toolName": "Bash",
                      "counts": {"modelTurns": 7, "toolCalls": 23}},
            messages=[message("inq-a3-1", "att-a3-1", "测试跑了很久还没有结束，卡在哪一步？", author=HOST_A,
                              created=instant(9, 55), state="delivered", delivered=instant(9, 55, 3))]),
        run("preview-run-b1", project="beta", group=MACRO_B, hosts=(HOST_B, HOST_B2),
            title=long_title("DSH 插件在 Windows 路径下的沙箱回归排查与修复记录，包含长标题截断检查："),
            title_source="title",
            task_text="排查 DSH 插件在 Windows 路径下的沙箱回归，修复后等待最终验收。",
            created=instant(3, 0), updated=instant(5, 35), state="delivered", status="completed",
            category="review", buddy=CFG_GLM, result=True, continuation_count=1,
            delegation_authority=True,
            result_summary="合成预览：沙箱回归修复完成，等待验收",
            spans=[
                span("queue:att-b1-1", "queue", instant(3, 0), instant(3, 4), "admitted",
                     attempt_id="att-b1-1"),
                span("execution:att-b1-1", "execution", instant(3, 4), instant(3, 30), "finished",
                     attempt_id="att-b1-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("queue:att-b1-2", "queue", instant(5, 1), instant(5, 2), "admitted",
                     attempt_id="att-b1-2"),
                span("execution:att-b1-2", "execution", instant(5, 2), instant(5, 30), "finished",
                     attempt_id="att-b1-2", buddy=CFG_GLM, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-b1-1", 1, "finished", True, instant(3, 4), instant(3, 30), CFG_GLM,
                        result="合成预览：第一轮探测完成。",
                        usage=usage("zcode-native", None, None, 4200)),
                attempt("att-b1-2", 2, "finished", True, instant(5, 2), instant(5, 30), CFG_GLM,
                        result="合成预览：修复完成，等待验收。",
                        usage=usage("zcode-native", 15000, 11000, 2200)),
            ],
            markers=[
                marker(instant(3, 0), "dispatch", actor=host_actor(HOST_B)),
                marker(instant(4, 1), "takeover", actor=host_actor(HOST_B2)),
                marker(instant(5, 1), "continue", actor=host_actor(HOST_B2), continuation_id="cont-b1-1"),
                marker(instant(5, 35), "integrate", actor=host_actor(HOST_B2), artifact_id="art-b1",
                       integration_id="int-b1", strategy="cherry-pick", state="verified"),
            ],
            artifacts=[artifact("art-b1", "att-b1-2", digest("b1-output")[:40], "/synthetic-preview/diffs/b1.diff",
                                ["harnesses/dsh/plugins/sandbox.js"])],
            integrations=[integration("int-b1", "art-b1", "att-b1-2", HOST_B2, host_paths=[])],
            buddy_locked=True),
        run("preview-run-b1-h1", project="beta", group=MACRO_B, parent="preview-run-b1",
            hosts=(HOST_B, HOST_B),
            title="整理 macOS 与 Windows 沙箱规则差异清单", title_source="title",
            task_text="整理两个平台的沙箱规则差异清单。",
            created=instant(3, 10), updated=instant(3, 25), state="cancelled", status="cancelled",
            category="ended", buddy=CFG_GLM, result=True,
            result_summary="整理 macOS 与 Windows 沙箱规则差异清单",
            spans=[
                span("queue:att-b1h-1", "queue", instant(3, 10), instant(3, 11), "admitted",
                     attempt_id="att-b1h-1"),
                span("execution:att-b1h-1", "execution", instant(3, 11), instant(3, 25), "finished",
                     attempt_id="att-b1h-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1,
                     error="synthetic preview cancellation: Host cancelled the helper", result_status="cancelled"),
            ],
            attempts=[attempt("att-b1h-1", 1, "finished", True, instant(3, 11), instant(3, 25), CFG_GLM,
                              error="synthetic preview cancellation: Host cancelled the helper",
                              result="synthetic preview cancellation: Host cancelled the helper",
                              usage=usage("zcode-native", 2500, 1500, 300),
                              stop_initiator="host", cancel_requested_at=instant(3, 24, 30),
                              grace_deadline=instant(3, 25, 15), result_status="cancelled", termination_reason="wrap-up")],
            cancellation={"actor": host_actor(HOST_B), "note": "Host 接手剩余工作"},
            artifacts=[artifact("art-b1h-partial", "att-b1h-1", digest("b1h-partial-output")[:40],
                                "/synthetic-preview/diffs/b1h.partial.diff",
                                ["harnesses/dsh/plugins/sandbox-rules.md"],
                                kind="partial-output", partial=True, verified=False, final=False,
                                cumulative_patch=patch_for("preview-run-b1-h1",
                                                           digest("b1h-partial-output")[:40],
                                                           ["harnesses/dsh/plugins/sandbox-rules.md"]))],
            conclusion=host_conclusion("preview-conclusion-b1-h1", attempt_id="att-b1h-1",
                                       execution_status="cancelled",
                                       note="synthetic preview cancellation: Host cancelled the helper",
                                       evidence=["att-b1h-1", "art-b1h-partial"],
                                       artifact_id="art-b1h-partial", integration_id=None,
                                       actor=host_actor(HOST_B), created_at=instant(3, 25), run_revision=3),
            markers=[marker(instant(3, 10), "dispatch", actor=host_actor(HOST_B)),
                     marker(instant(3, 24, 30), "cancel", actor=host_actor(HOST_B), stopInitiator="host", note="Host 接手剩余工作"),
                     marker(instant(3, 26), "conclude", actor=host_actor(HOST_B), state="cancelled")]),
        run("preview-run-b2", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b1", "turn-att-b1-2"),
            title="封存配额耗尽时的部分输出", title_source="title",
            task_text="排查执行器在配额耗尽后仍未封存部分输出的路径，并补齐封存记录。",
            created=instant(6, 5), updated=instant(6, 26), state="failed", status="failed",
            category="ended", buddy=CFG_GLM, result=True,
            result_summary="合成预览：执行器配额耗尽，仅保留封存的部分输出",
            spans=[
                span("queue:att-b2-1", "queue", instant(6, 5), instant(6, 7), "admitted",
                     attempt_id="att-b2-1"),
                span("execution:att-b2-1", "execution", instant(6, 7), instant(6, 26), "finished",
                     attempt_id="att-b2-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1, error=QUOTA_ERROR, result_status="failed"),
            ],
            attempts=[attempt("att-b2-1", 1, "finished", True, instant(6, 7), instant(6, 26), CFG_GLM,
                              error=QUOTA_ERROR, result=QUOTA_ERROR,
                              usage=usage("zcode-native", None, None, 4200))],
            markers=[marker(instant(6, 5), "dispatch", actor={"role": "lead", "id": "preview-run-b1"}),
                     marker(instant(6, 26), "conclude", actor={"role": "lead", "id": "preview-run-b1"}, state="failed")],
            artifacts=[artifact("art-b2-partial", "att-b2-1", digest("b2-partial-output")[:40],
                                "/synthetic-preview/diffs/b2.partial.diff",
                                ["harnesses/dsh/plugins/quota.js"],
                                kind="partial-output", partial=True, verified=False, final=False,
                                cumulative_patch=patch_for("preview-run-b2", digest("b2-partial-output")[:40],
                                                           ["harnesses/dsh/plugins/quota.js"]))],
            conclusion=host_conclusion("preview-conclusion-b2", attempt_id="att-b2-1",
                                       execution_status="failed", note=QUOTA_ERROR,
                                       evidence=["art-b2-partial", QUOTA_ERROR],
                                       artifact_id="art-b2-partial", integration_id=None,
                                       actor={"role": "lead", "id": "preview-run-b1"},
                                       created_at=instant(6, 26), run_revision=3)),
        # The assistance request wakes the Lead after its first waiting return.
        # Its second input already includes that request; another owned micro
        # task remains queued, with no later boundary to wake the parked Lead.
        run("preview-run-b3", project="beta", group=MACRO_B, hosts=(HOST_B, HOST_B),
            title="Lead 核对平台路径", title_source="title",
            task_text="协调路径样例核对，等待名下微任务交回结果后继续。",
            created=instant(7, 0), updated=instant(7, 35), state="waiting-owned", status="completed",
            category="active", buddy=CFG_CODEX, delegation_authority=True, result=True, continuation_count=1,
            result_summary="合成预览：已收到范围请求，等待另一条名下微任务执行",
            spans=[
                span("queue:att-b3-1", "queue", instant(7, 0), instant(7, 5), "admitted",
                     attempt_id="att-b3-1"),
                span("execution:att-b3-1", "execution", instant(7, 5), instant(7, 20), "finished",
                     attempt_id="att-b3-1", buddy=CFG_CODEX, confirmed=True, generation=1,
                     disposition="waiting", turn_index=1),
                span("queue:att-b3-2", "queue", instant(7, 25), instant(7, 30), "admitted",
                     attempt_id="att-b3-2"),
                span("execution:att-b3-2", "execution", instant(7, 30), instant(7, 35), "finished",
                     attempt_id="att-b3-2", buddy=CFG_CODEX, confirmed=True, generation=2,
                     disposition="waiting", turn_index=2),
            ],
            attempts=[attempt("att-b3-1", 1, "finished", True, instant(7, 5), instant(7, 20), CFG_CODEX,
                              disposition="waiting", result="等待名下的路径核对微任务。",
                              usage=usage("codex-native", 8000, 3000, 1200)),
                      attempt("att-b3-2", 2, "finished", True, instant(7, 30), instant(7, 35), CFG_CODEX,
                              disposition="waiting", result="已收到范围请求，等待另一条名下微任务执行。",
                              usage=usage("codex-native", 10000, 7000, 500))],
            markers=[marker(instant(7, 0), "dispatch", actor=host_actor(HOST_B))]),
        run("preview-run-b4", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b3", "turn-att-b3-1"), title="确认路径样例范围", title_source="title",
            task_text="核对合成路径样例，遇到范围差异时请求 Lead 决定。",
            created=instant(7, 10), updated=instant(7, 25), state="awaiting-host", status="completed",
            category="active", buddy=CFG_GLM, result=True, active_request=LEAD_REQUEST,
            result_summary="合成预览：已交回范围请求，等待 Lead 决定",
            spans=[
                span("queue:att-b4-1", "queue", instant(7, 10), instant(7, 11), "admitted",
                     attempt_id="att-b4-1"),
                span("execution:att-b4-1", "execution", instant(7, 11), instant(7, 25), "finished",
                     attempt_id="att-b4-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="assistance", turn_index=1),
                span("host:req-b4-1", "host", instant(7, 25), None, "open", request_id="req-b4-1",
                     request_kind="assistance", summary=LEAD_REQUEST["summary"]),
            ],
            attempts=[attempt("att-b4-1", 1, "finished", True, instant(7, 11), instant(7, 25), CFG_GLM,
                              disposition="assistance", result=LEAD_REQUEST["summary"],
                              usage=usage("zcode-native", 3000, 1000, 400))],
            markers=[marker(instant(7, 10), "dispatch", actor={"role": "lead", "id": "preview-run-b3"})]),
        run("preview-run-b5", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b3", "turn-att-b3-1"), title="执行平台路径核对", title_source="title",
            task_text="持续核对合成平台路径样例，完成后向 Lead 交回。",
            created=instant(7, 12), updated=instant(7, 12), state="executing", status="queued",
            category="active", buddy=CFG_FLASH,
            spans=[span("queue:preview-run-b5", "queue", instant(7, 12), None, "queued")],
            markers=[marker(instant(7, 12), "dispatch", actor={"role": "lead", "id": "preview-run-b3"})]),
        run("preview-run-b6", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b3", "turn-att-b3-1"), title="停止重复的路径核对", title_source="title",
            task_text="核对重复的路径样例，Lead 确认范围重复后停止。",
            created=instant(7, 13), updated=instant(7, 19), state="cancelled", status="cancelled",
            category="ended", buddy=CFG_GLM, result=True,
            result_summary="合成预览：已按 Lead 的请求收尾并确认停止",
            cancellation={"actor": {"role": "lead", "id": "preview-run-b3"}, "note": "路径样例与已有核对重复"},
            spans=[span("execution:att-b6-1", "execution", instant(7, 14), instant(7, 19), "finished",
                        attempt_id="att-b6-1", buddy=CFG_GLM, confirmed=True, generation=1,
                        turn_index=1, result_status="cancelled")],
            attempts=[attempt("att-b6-1", 1, "finished", True, instant(7, 14), instant(7, 19), CFG_GLM,
                              result="合成预览：已收尾。", result_status="cancelled", termination_reason="wrap-up",
                              usage=usage("zcode-native", 1800, 500, 200),
                              stop_initiator="lead", cancel_requested_at=instant(7, 18, 30), grace_deadline=instant(7, 19, 15))],
            markers=[marker(instant(7, 13), "dispatch", actor={"role": "lead", "id": "preview-run-b3"}),
                     marker(instant(7, 18, 30), "cancel", actor={"role": "lead", "id": "preview-run-b3"},
                            stopInitiator="lead", note="路径样例与已有核对重复")]),
        run("preview-run-b9", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b3", "turn-att-b3-1"), title="核对路径样例编码", title_source="title",
            task_text="核对合成平台路径中的字符编码并交回结果。",
            created=instant(7, 14), updated=instant(7, 18), state="accepted", status="completed",
            category="ended", buddy=CFG_FLASH, result=True, accepted=instant(7, 18), verdict="accepted",
            spans=[span("execution:att-b9-1", "execution", instant(7, 16), instant(7, 18), "finished",
                        attempt_id="att-b9-1", buddy=CFG_FLASH, confirmed=True, generation=1, turn_index=1)],
            attempts=[attempt("att-b9-1", 1, "finished", True, instant(7, 16), instant(7, 18), CFG_FLASH,
                              result="合成预览：编码样例一致。", usage=usage("dsh-native", 1700, 500, 200))],
            artifacts=[artifact("art-b9", "att-b9-1", digest("b9-output")[:40],
                                "/synthetic-preview/diffs/b9.diff", ["synthetic/path-encoding.txt"])],
            markers=[marker(instant(7, 14), "dispatch", actor={"role": "lead", "id": "preview-run-b3"}),
                     marker(instant(7, 18), "accept", actor={"role": "lead", "id": "preview-run-b3"}, verdict="accepted")]),
        run("preview-run-b10", project="beta", group=MACRO_B, hosts=(HOST_B, HOST_B),
            title="独立核对路径文档", title_source="title", task_text="独立核对合成路径文档中的示例。",
            created=instant(7, 15), updated=instant(7, 18), state="accepted", status="completed",
            category="ended", buddy=CFG_CLAUDE, result=True, accepted=instant(7, 18), verdict="accepted",
            spans=[span("execution:att-b10-1", "execution", instant(7, 16), instant(7, 18), "finished",
                        attempt_id="att-b10-1", buddy=CFG_CLAUDE, confirmed=True, generation=1, turn_index=1)],
            attempts=[attempt("att-b10-1", 1, "finished", True, instant(7, 16), instant(7, 18), CFG_CLAUDE,
                              result="合成预览：文档样例一致。", usage=None)],
            artifacts=[artifact("art-b10", "att-b10-1", digest("b10-output")[:40],
                                "/synthetic-preview/diffs/b10.diff", ["synthetic/path-guide.txt"])],
            markers=[marker(instant(7, 15), "dispatch", actor=host_actor(HOST_B))]),
        # Lead stopped its owned run first. Host then stopped the Lead; its own
        # process is confirmed gone while the owned execution is still wrapping up.
        run("preview-run-b7", project="beta", group=MACRO_B, hosts=(HOST_B, HOST_B),
            title="Lead 停止平台核对", title_source="title", task_text="协调另一批平台核对并停止名下的微任务。",
            created=instant(9, 44), updated=instant(9, 59, 50), state="cancelled", status="cancelled",
            category="ended", buddy=CFG_CODEX, delegation_authority=True, result=True,
            cancellation={"actor": host_actor(HOST_B), "note": "停止本批平台核对"},
            spans=[span("execution:att-b7-1", "execution", instant(9, 45), instant(9, 59, 50), "finished",
                        attempt_id="att-b7-1", buddy=CFG_CODEX, confirmed=True, generation=1,
                        turn_index=1, result_status="cancelled")],
            attempts=[attempt("att-b7-1", 1, "finished", True, instant(9, 45), instant(9, 59, 50), CFG_CODEX,
                              result="合成预览：已请求停止名下微任务，自身已收尾。", result_status="cancelled",
                              usage=usage("codex-native", 3500, 1200, 400),
                              termination_reason="wrap-up", stop_initiator="host",
                              cancel_requested_at=instant(9, 59, 40), grace_deadline=instant(10, 0, 25))],
            markers=[marker(instant(9, 44), "dispatch", actor=host_actor(HOST_B)),
                     marker(instant(9, 59, 40), "cancel", actor=host_actor(HOST_B),
                            stopInitiator="host", note="停止本批平台核对")]),
        run("preview-run-b8", project="beta", group=MACRO_B, hosts=(None, None),
            lead_owner=("preview-run-b7", "turn-att-b7-1"), title="等待平台核对停止", title_source="title",
            task_text="核对平台样例，停止后须交回真实的进程退出证据。",
            created=instant(9, 46), updated=instant(9, 59, 40), state="cancelled", status="cancelling",
            category="active", buddy=CFG_GLM, shutdown_confirmed=False,
            cancellation={"actor": {"role": "lead", "id": "preview-run-b7"}, "note": "Lead 停止这条平台核对"},
            spans=[span("execution:att-b8-1", "execution", instant(9, 47), None, "executing",
                        attempt_id="att-b8-1", buddy=CFG_GLM, confirmed=False, generation=1, turn_index=1)],
            attempts=[attempt("att-b8-1", 1, "executing", False, instant(9, 47), None, CFG_GLM,
                              usage=usage("zcode-native", None, None, None),
                              stop_initiator="lead", cancel_requested_at=instant(9, 59, 30),
                              grace_deadline=instant(10, 0, 15))],
            markers=[marker(instant(9, 46), "dispatch", actor={"role": "lead", "id": "preview-run-b7"}),
                     marker(instant(9, 59, 30), "cancel", actor={"role": "lead", "id": "preview-run-b7"},
                            stopInitiator="lead", note="Lead 停止这条平台核对")]),
        run(STANDALONE, project="beta", hosts=(HOST_B, HOST_B2),
            title="修复标题回退在 CRLF 输入下的显示", title_source="task",
            task_text="修复标题回退在 CRLF 输入下的显示，并补充回归测试。",
            created=instant(5, 0), updated=instant(5, 35), state="accepted", status="completed",
            category="ended", buddy=CFG_FLASH, accepted=instant(5, 35), verdict="accepted",
            result=True, result_summary="修复标题回退在 CRLF 输入下的显示",
            spans=[
                span("queue:att-c1-1", "queue", instant(5, 0), instant(5, 4), "admitted",
                     attempt_id="att-c1-1"),
                span("execution:att-c1-1", "execution", instant(5, 4), instant(5, 20), "finished",
                     attempt_id="att-c1-1", buddy=CFG_FLASH, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-c1-1", 1, "finished", True, instant(5, 4), instant(5, 20), CFG_FLASH,
                              result="合成预览：CRLF 标题回退修复完成。",
                              usage=usage("dsh-native", 5000, 1000, 700))],
            markers=[marker(instant(5, 0), "dispatch", actor=host_actor(HOST_B)),
                     marker(instant(5, 30), "takeover", actor=host_actor(HOST_B2)),
                     marker(instant(5, 35), "accept", actor=host_actor(HOST_B2), artifact_id="art-c1", verdict="accepted")],
            artifacts=[artifact("art-c1", "att-c1-1", digest("c1-output")[:40], "/synthetic-preview/diffs/c1.diff",
                                ["apps/console/src/task-title.test.tsx"])],
            integrations=[integration("int-c1", "art-c1", "att-c1-1", HOST_B2)]),
        run("preview-run-c1-h1", project="beta", group=None, parent=STANDALONE, hosts=(HOST_B, HOST_B),
            title=None, title_source="none", task_text="   ",
            created=instant(5, 6), updated=instant(5, 25), state="delivered", status="completed",
            category="review", buddy=CFG_GLM, verdict="rejected", result=True,
            result_summary="合成预览：交付被记录为验收问题。",
            spans=[
                span("queue:att-c1h-1", "queue", instant(5, 6), instant(5, 8), "admitted",
                     attempt_id="att-c1h-1"),
                span("execution:att-c1h-1", "execution", instant(5, 8), instant(5, 18), "finished",
                     attempt_id="att-c1h-1", buddy=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-c1h-1", 1, "finished", True, instant(5, 8), instant(5, 18), CFG_GLM,
                              result="合成预览：交付被记录为验收问题。", usage=None)],
            markers=[marker(instant(5, 6), "dispatch", actor=host_actor(HOST_B)),
                     marker(instant(5, 25), "reject", actor=host_actor(HOST_B), artifact_id="art-c1h", verdict="rejected")],
            artifacts=[artifact("art-c1h", "att-c1h-1", digest("c1h-output")[:40],
                                "/synthetic-preview/diffs/c1h.diff", ["apps/console/src/task-title.ts"])]),
        # A settled target for the entirely synthetic deletion flow. Its Lead
        # has no owned work, and its untitled failure still needs a Host conclusion.
        run("preview-run-d1", project="alpha", group=MACRO_C, title="合成删除核对已完成", title_source="title",
            task_text="只核对虚构的删除范围，不接触任何真实文件或会话。",
            created=instant(1, 0), updated=instant(1, 20), state="accepted", status="completed",
            category="ended", buddy=CFG_CODEX, delegation_authority=True,
            accepted=instant(1, 20), verdict="accepted", result=True,
            spans=[span("execution:att-d1-1", "execution", instant(1, 2), instant(1, 10), "finished",
                        attempt_id="att-d1-1", buddy=CFG_CODEX, confirmed=True, generation=1, turn_index=1)],
            attempts=[attempt("att-d1-1", 1, "finished", True, instant(1, 2), instant(1, 10), CFG_CODEX,
                              result="合成预览：已核对虚构的删除范围。", usage=usage("codex-native", 1200, 500, 150))],
            artifacts=[artifact("art-d1", "att-d1-1", digest("d1-output")[:40],
                                "/synthetic-preview/diffs/d1.diff", ["synthetic/deletion-scope.txt"])],
            markers=[marker(instant(1, 0), "dispatch", actor=host_actor(HOST_A)),
                     marker(instant(1, 20), "accept", actor=host_actor(HOST_A), verdict="accepted")]),
        run("preview-run-d2", project="alpha", group=MACRO_C, title=None, title_source="none", task_text="   ",
            created=instant(1, 15), updated=instant(1, 16), state="failed", status="failed",
            category="ended", buddy=CFG_GLM,
            attempts=[attempt("att-d2-1", 1, "finished", True, instant(1, 16), instant(1, 16), CFG_GLM,
                              error="synthetic preview: service stopped before launch",
                              release_reason=reason("service-stopped-before-launch"))],
            markers=[marker(instant(1, 15), "dispatch", actor=host_actor(HOST_A))]),
    ]
    # Internal routing rows: real task history, never macro task rows.
    for owner, record in (("preview-run-a1", ROUTING_A1), ("preview-run-a2", ROUTING_A2),
                          ("preview-run-a3", ROUTING_A3), ("preview-run-a2", ROUTING_A2_ABSTENTION)):
        router = record["taskId"]
        owner_entry = next(entry for entry in runs if entry["runId"] == owner)
        recorded_span = next(item for item in owner_entry["spans"] if item.get("decisionId") == record["decisionId"])
        runs.append(run(router, project="alpha", parent=owner, title=None, title_source="none",
                        task_text="Choose the execution buddy for this synthetic task.",
                        created=recorded_span["startAt"], updated=recorded_span["endAt"],
                        state="cancelled" if record["status"] == "cancelled" else "accepted",
                        status=record["status"] if record["status"] in ("failed", "cancelled") else "completed",
                        category="ended", buddy=CFG_CODEX, micro_task=False, routing=record,
                        adapter="decision", router_budget_seconds=record["budget"]["timeoutSeconds"] + 10,
                        accepted=recorded_span["endAt"], result=True,
                        attempts=[attempt(record["attemptId"], 1, "finished", True,
                                          recorded_span["startAt"], recorded_span["endAt"], CFG_CODEX,
                                          error=("router-out-of-bounds" if record["status"] == "failed" else None),
                                          result=decision_result(record), disposition=("abstention" if record["status"] == "needs-host" else record["status"]),
                                          usage=usage("synthetic-preview", 2100, 400, 200))],
                        revision=1))
        runs[-1]["decisionId"] = record["decisionId"]
        runs[-1]["requestId"] = f"request-{router}"
    # An internal command execution record has no collaboration row.
    # The board may also have no project attribution for such a record.
    runs.append(run("preview-command-c1", project=None, title=None, title_source="none",
                    task_text="printf 'synthetic preview command'", created=instant(-20, 10),
                    updated=instant(-20, 11), state="accepted", status="completed",
                    category="ended", buddy=None, micro_task=False, adapter="command",
                    accepted=instant(-20, 11), result=True,
                    attempts=[attempt("att-command-c1", 1, "finished", True,
                                      instant(-20, 10, 5), instant(-20, 10, 8), None,
                                      result="synthetic preview command")]))
    by_id = {entry["runId"]: entry for entry in runs}
    for entry in runs:
        root = entry["runId"]
        while by_id[root]["parentRunId"] is not None:
            root = by_id[root]["parentRunId"]
        entry["rootRunId"] = root
        entry["requestId"] = entry.get("requestId") or f"request-{entry['runId']}"
        entry["cwd"] = f"/synthetic-preview/checkouts/{entry['runId']}"
        project = PROJECTS[entry["project"]] if entry["project"] else None
        routing_owner = by_id[entry["parentRunId"]] if entry["adapter"] == "decision" else None
        entry["lineage"] = {
            "kind": entry["kind"], "sourceHostId": entry["sourceHostId"] if entry["kind"] != "execution" else None,
            "currentHostId": entry["currentHostId"] if entry["kind"] != "execution" else None,
            "parentRunId": entry["parentRunId"],
            "rootRunId": root if entry["kind"] != "execution" else None,
            "rootLeadRunId": entry["leadOwner"][0] if entry["leadOwner"] else None,
            "leadOwnership": ({"leadRunId": entry["leadOwner"][0], "leadTurnId": entry["leadOwner"][1],
                               "createdAt": entry["createdAt"]} if entry["leadOwner"] else None),
            "delegationAuthority": entry["delegationAuthority"],
            "project": ({"id": project["id"], "path": project["path"], "label": project["label"]}
                        if project else None),
            "buddy": entry["buddy"],
            "routingTask": ({"runId": routing_owner["runId"], "title": routing_owner["title"],
                             "titleSource": "title", "macroTaskId": routing_owner["group"],
                             "macroTaskTitle": (MACRO_TASKS[routing_owner["group"]]["title"]
                                                if routing_owner["group"] else None)} if routing_owner else None),
        }
        for item in entry["artifacts"]:
            item["sourceTaskId"] = entry["runId"]
            item["createdAt"] = entry["updatedAt"]
        for item in entry["integrations"]:
            item["runId"] = entry["runId"]
            item["createdAt"] = entry["updatedAt"]
        for item in entry["spans"]:
            item["runId"] = entry["runId"]
        for item in entry["messages"]:
            item["taskId"] = entry["runId"]
    order = [entry["runId"] for entry in runs]
    sequence = sorted(((entry["runId"], item) for entry in runs for item in entry["markers"]),
                      key=lambda pair: (pair[1]["at"], pair[0], pair[1]["kind"]))
    for number, (run_id, item) in enumerate(sequence, start=1):
        item["seq"] = number
        item["runId"] = run_id
    activity = {entry["runId"]: (0, entry["createdAt"]) for entry in runs}
    for run_id, item in sequence:
        activity[run_id] = (item["seq"], item["at"])
    groups: dict[str, list[str]] = {}
    for entry in runs:
        if entry["kind"] in ("decision", "execution"):
            continue
        group = entry["group"] or (f"run:{by_id[entry['parentRunId']]['rootRunId']}"
                                   if entry["parentRunId"] else f"run:{entry['runId']}")
        groups.setdefault(group, []).append(entry["runId"])
    group_activity = {group: max((activity[run_id] for run_id in members), key=lambda item: item[0])
                      for group, members in groups.items()}
    return {"runs": by_id, "order": order, "groups": groups, "activity": activity,
            "group_activity": group_activity, "head": len(sequence), "markers": sequence}


MACRO_TASKS = {
    MACRO_A: {"title": long_title("工作目标时间轴：设计、接口与合成预览夹具的实现与核对（含超长标题与折叠区间）："),
            "description": "按用户认可的第 X 节设计工作目标时间轴。本目标覆盖只读接口、前端呈现与合成预览夹具的自检。",
            "project": "alpha", "host": HOST_A, "createdAt": instant(6, 0)},
    MACRO_B: {"title": "0.13 控制台入口候选版收尾与文档", "description": None, "project": "beta",
            "host": HOST_B, "createdAt": instant(3, 0)},
    MACRO_C: {"title": "合成删除范围核对", "description": None, "project": "alpha",
            "host": HOST_A, "createdAt": instant(1, 0)},
}


FIXTURES = build_runs()


class PreviewState:
    """One server's volatile deletion receipts, never a connection to a board."""

    def __init__(self):
        self.deleted: dict[str, dict] = {}
        self.removed_runs: set[str] = set()
        self.lock = threading.RLock()

    @property
    def cursor(self) -> int:
        return FIXTURES["head"] + len(self.deleted)


def fixture_run(run_id: str, state: PreviewState | None = None) -> dict:
    if run_id not in FIXTURES["runs"] or state is not None and run_id in state.removed_runs:
        raise PreviewError("NOT_FOUND", "Unknown runId")
    return FIXTURES["runs"][run_id]


def deletion_scope(group: str) -> dict:
    """Invented storage facts; no file, native session or Git inspection occurs."""
    entries = [FIXTURES["runs"][run_id] for run_id in FIXTURES["groups"][group]]
    return {
        "artifacts": {"count": len({item["artifactId"] for entry in entries for item in artifact_views(entry)}),
                      "bytes": 16384 if group == MACRO_C else None},
        "sessions": {"count": {MACRO_A: 7, MACRO_B: 9, MACRO_C: 1, STANDALONE_GROUP: 2}[group],
                     "bytes": None},
        "checkouts": len(entries),
        "gitRefs": {MACRO_A: 15, MACRO_B: 27, MACRO_C: 0, STANDALONE_GROUP: 6}[group],
    }


def deletion_preview(group: str, state: PreviewState | None = None) -> dict:
    if state is not None and group in state.deleted:
        return {"macroTaskId": group, "microTaskCount": state.deleted[group]["microTaskCount"],
                "unconcludedFailureCount": 0, "blockerTotal": 0, "blockers": [],
                "backupRetention": "until-backup-rotation", "deletionState": "deleted",
                "scope": {"artifacts": {"count": None, "bytes": None},
                          "sessions": {"count": None, "bytes": None}, "checkouts": None, "gitRefs": None}}
    if group not in FIXTURES["groups"]:
        raise PreviewError("NOT_FOUND", "Unknown macroTaskId")
    members = set(FIXTURES["groups"][group])
    entries = [entry for entry in FIXTURES["runs"].values()
               if entry["runId"] in members or entry["kind"] == "decision" and entry["parentRunId"] in members]
    blockers: dict[tuple[str, str], dict] = {}

    def block(entry: dict, code: str, attempt_ids: list[str] | None = None) -> None:
        micro_task = entry["runId"] in members
        title = entry["title"] if micro_task else task_first_line(entry["task"])
        title_source = entry["titleSource"] if micro_task else "task" if title is not None else "none"
        blockers[(entry["runId"], code)] = {
            "runId": entry["runId"] if micro_task else None, "taskId": entry["runId"],
            "title": title, "titleSource": title_source,
            "state": entry["status"], "runState": entry["state"] if micro_task else None,
            "reason": reason(f"macro-task-deletion.{code}"), "attemptIds": sorted(attempt_ids or []),
        }

    for entry in entries:
        unconfirmed = [item["attemptId"] for item in entry["attempts"]
                       if item["state"] != "finished" or not item["shutdownConfirmed"]]
        if unconfirmed or not entry["shutdownConfirmed"]:
            block(entry, "stop-unconfirmed", unconfirmed)
        if entry["status"] in ("queued", "running", "cancelling", "reconciliation-needed"):
            block(entry, "queued" if entry["status"] == "queued" else "running")
        if entry["runId"] in members and entry["state"] not in ("accepted", "concluded", "cancelled", "failed"):
            block(entry, {"delivered": "review", "awaiting-host": "awaiting-host"}.get(entry["state"], "running"))
        if entry["activeRequest"] is not None and entry["activeRequest"]["state"] == "open":
            block(entry, "awaiting-host")
        if any(item["state"] in ("recorded", "queued") for item in entry["continuations"]):
            block(entry, "queued")
    ordered = sorted(blockers.values(), key=lambda item: (item["taskId"], item["reason"]["code"]))
    return {"macroTaskId": group, "microTaskCount": len(members),
            "unconcludedFailureCount": sum(entry["runId"] in members and entry["state"] in ("failed", "cancelled")
                                           and entry["acceptedAt"] is None and entry["hostConclusion"] is None
                                           for entry in entries),
            "blockerTotal": len(ordered), "blockers": ordered[:50],
            "backupRetention": "until-backup-rotation", "deletionState": None, "scope": deletion_scope(group)}


def delete_macro_task(params: dict, state: PreviewState) -> dict:
    """The command route holds the instance lock across its mutation and reply."""
    group, count = params["macroTaskId"], params["microTaskCount"]
    if group in state.deleted:
        return {**state.deleted[group], "duplicate": True}
    preview = deletion_preview(group, state)
    if count != preview["microTaskCount"]:
        raise PreviewError("REVISION_CONFLICT", "The micro task count changed", microTaskCount=preview["microTaskCount"])
    if preview["blockerTotal"]:
        raise PreviewError("MACRO_TASK_NOT_SETTLED", "The macro task is not settled",
                           macroTaskId=group, blockerTotal=preview["blockerTotal"], blockers=preview["blockers"])
    members = set(FIXTURES["groups"][group])
    state.removed_runs.update(entry["runId"] for entry in FIXTURES["runs"].values()
                              if entry["runId"] in members or entry["kind"] == "decision" and entry["parentRunId"] in members)
    result = {"deletionId": f"synthetic-deletion-{group}", "macroTaskId": group,
              "microTaskCount": count, "state": "deleted", "deletedAt": OBSERVED_AT,
              "duplicate": False, "skipped": []}
    state.deleted[group] = result
    return result


# --------------------------------------------------------------------------- #
# Thin projections: fixture record -> console DTO                              #
# --------------------------------------------------------------------------- #
def shutdown_view(entry: dict) -> dict:
    """Recorded stop evidence over helpers and Lead-owned micro tasks, excluding routing."""
    unconfirmed: list[str] = []
    stack = [entry]
    while stack:
        current = stack.pop()
        if not current["shutdownConfirmed"]:
            unconfirmed.append(current["runId"])
        stack.extend(child for child in FIXTURES["runs"].values()
                     if child["kind"] in ("micro-task", "helper")
                     and (child["parentRunId"] == current["runId"]
                          or child["leadOwner"] is not None and child["leadOwner"][0] == current["runId"]))
    own = entry["shutdownConfirmed"]
    return {"selfConfirmed": own, "descendantsConfirmed": unconfirmed == ([] if own else [entry["runId"]]),
            "unconfirmedRunIds": unconfirmed[:32], "unconfirmedCount": len(unconfirmed), "truncated": False}


def attempt_view(entry: dict, item: dict) -> dict:
    buddy = item["buddy"]
    return {
        "attemptId": item["attemptId"], "taskId": entry["runId"], "generation": item["generation"],
        "workerId": "synthetic-worker-1", "workerIdentity": "synthetic preview worker (fixture)",
        "workerInstance": "synthetic-instance-1", "adapter": entry["adapter"] or buddy["harness"],
        "executionState": item["state"], "ownership": "owned",
        "runtimeIdentity": None, "leaseExpiresAt": None, "leaseSeconds": 900, "logPaths": {},
        "resultAvailable": item["result"] is not None, "shutdownConfirmed": item["shutdownConfirmed"],
        "cancelRequested": item["cancelRequestedAt"] is not None, "cancelRequestedAt": item["cancelRequestedAt"],
        "stopInitiator": item["stopInitiator"], "graceDeadline": item["graceDeadline"],
        "exitCode": 0 if item["state"] == "finished" else None, "signal": None, "error": item["error"],
        "startedAt": item["startedAt"], "finishedAt": item["finishedAt"], "createdAt": item["startedAt"],
        "updatedAt": item["finishedAt"] or item["startedAt"], "revision": 1,
        "tokenUsage": item["usage"],
        "releaseReason": item["releaseReason"],
        "reminders": ({"overdue": True, "possiblyStuck": True, "lastNativeEventAt": instant(9, 40), "toolInProgress": False}
                      if entry["runId"] == "preview-run-a3"
                      else {"overdue": False, "possiblyStuck": False, "lastNativeEventAt": None, "toolInProgress": False}),
        **({"result": item["result"]} if item["result"] is not None else {}),
    }


def task_view(entry: dict) -> dict:
    selected = entry["attempts"][-1] if entry["attempts"] else None
    receipt = selected["result"] if selected is not None and entry["resultAvailable"] else None
    adapter = entry["adapter"] or (entry["buddy"] or {}).get("harness", "dsh")
    duration = ({"routerBudgetSeconds": entry["routerBudgetSeconds"]} if adapter == "decision"
                else {"expectedSeconds": entry["expectedSeconds"]})
    spec: dict[str, Any] = {"task": entry["task"], "adapter": adapter, "cwd": entry["cwd"],
                            "workspace": True, **duration}
    if adapter == "command":
        spec["argv"] = ["/bin/printf", "synthetic preview command"]
    if entry["group"]:
        spec["macroTaskId"] = entry["group"]
    if entry["adapter"] == "decision" and entry.get("decisionId"):
        spec["decision"] = {"decisionId": entry["decisionId"], "kind": "select",
                            "routerBudgetSeconds": entry["routing"]["budget"]["timeoutSeconds"]}
    view: dict[str, Any] = {
        "runId": entry["runId"], "taskId": entry["runId"], "requestId": entry["requestId"],
        "owner": None if entry["kind"] in ("micro-task", "helper") else "synthetic-worker-1", "adapter": adapter, "cwd": entry["cwd"], "task": entry["task"],
        "spec": spec, "inputFingerprint": digest(f"{entry['runId']}-fingerprint"), "fingerprintVersion": 3,
        "requiredCapabilities": [], "exclusiveResources": [], **duration,
        "status": entry["status"], "state": entry["status"],
        "queueReason": "awaiting-owned" if entry["state"] == "waiting-owned" else None,
        "revision": entry["revision"],
        "selectedAttemptId": selected["attemptId"] if selected else None,
        "activeAttemptId": (selected["attemptId"] if selected and selected["state"] == "executing" else None),
        "createdAt": entry["createdAt"], "updatedAt": entry["updatedAt"], "acceptedAt": entry["acceptedAt"],
        "acceptanceNote": None, "acceptanceVerdict": entry["acceptanceVerdict"],
        "resultAvailable": receipt is not None, "shutdownConfirmed": entry["shutdownConfirmed"],
        "cancellation": entry["cancellation"],
        "stopInitiator": selected["stopInitiator"] if selected else None,
        "graceDeadline": selected["graceDeadline"] if selected else None,
        "activity": entry["activity"], "lineage": entry["lineage"], "artifacts": entry["artifacts"],
        "artifactCount": len(entry["artifacts"]), "inquiries": {},
        # ADR-018: the selected attempt's per-attempt usage, also inside
        # `selectedAttempt`; null when no usage was recorded (never 0).
        "tokenUsage": selected["usage"] if selected is not None else None,
    }
    if entry["kind"] in ("micro-task", "helper"):
        view["workflow"] = workflow_extension(entry)
        view["workflowState"] = entry["state"]
        view["awaitingHost"] = entry["state"] == "awaiting-host"
        view["workflowShutdown"] = shutdown_view(entry)
    if selected is not None:
        view["selectedAttempt"] = attempt_view(entry, selected)
        view["attemptGeneration"] = selected["generation"]
        view["attemptState"] = selected["state"]
        view["workerId"] = "synthetic-worker-1"
        view["logPaths"] = {}
        view["cancelRequested"] = selected["cancelRequestedAt"] is not None
    if receipt is not None:
        # The route merges `task_result`: the adapter payload at `result` and the
        # receipt's remaining fields at `resultMeta`.
        view.update({"result": receipt["result"],
                     "resultMeta": {key: value for key, value in receipt.items() if key != "result"},
                     "attemptId": selected["attemptId"], "resultDelivered": True})
    return view


def workflow_extension(entry: dict) -> dict:
    extension = {"state": entry["state"], "awaitingHost": entry["state"] == "awaiting-host",
                 "ownerGeneration": 1, "hostId": entry["currentHostId"], "revision": entry["revision"],
                 "continuationCount": entry["continuationCount"],
                 "activeRequestId": (entry["activeRequest"]["requestId"] if entry["activeRequest"] else None),
                 "resultSummary": entry["resultSummary"], "title": entry["title"],
                 "macroTaskId": entry["group"]}
    if entry["activeRequest"]:
        extension["requestKind"] = entry["activeRequest"]["kind"]
        extension["requestSummary"] = entry["activeRequest"]["summary"]
        extension["requestRouting"] = entry["activeRequest"]["routing"]
    return extension


def turn_view(entry: dict, item: dict) -> dict:
    """One turn as the board lists it: no turn carries its result here."""
    return {
        "turnId": item["turnId"], "turnIndex": item["turnIndex"], "attemptId": item["attemptId"],
        "expectedSeconds": item["expectedSeconds"],
        "generation": item["generation"], "resumeMode": "reconstructed-new-session",
        "sessionId": None, "state": "returned" if item["state"] == "finished" else "open",
        "disposition": item["disposition"], "createdAt": item["startedAt"],
        "updatedAt": item["finishedAt"] or item["startedAt"],
        "buddy": item["buddy"],
        "routing": ({"decisionId": entry["routing"]["decisionId"], "buddyRevision": 2}
                    if entry["routing"] and entry["routing"]["decisionId"] and item["turnIndex"] == 1
                    else None),
        # Each turn keeps its own per-attempt usage record; never a
        # session-cumulative number.
        "tokenUsage": item["usage"],
    }


def turn_views(entry: dict) -> list[dict]:
    """Turn index, newest first, like ``workflow.compact``."""
    return [turn_view(entry, item) for item in reversed(entry["attempts"])]


def turn_summary(item: dict) -> str:
    return ((item["result"] or {}).get("result") or {}).get("finalText", "")


def request_records(entry: dict) -> list[dict]:
    """Every request of the run as the audit lists it, oldest first; the open one is the active request."""
    records = []
    for span_item in entry["spans"]:
        if span_item["kind"] != "host":
            continue
        active = entry["activeRequest"] if entry["activeRequest"] and entry["activeRequest"]["requestId"] == span_item["requestId"] else None
        records.append(active or {
            "requestId": span_item["requestId"], "kind": span_item["requestKind"], "routing": span_item["requestKind"] == "routing",
            "state": span_item["state"], "summary": span_item.get("summary") or "", "attempted": "合成预览：已尝试的步骤",
            "reason": reason("router-out-of-bounds", bound="constraints") if span_item["requestKind"] == "routing" else None,
            "neededWork": ["合成预览：需要完成的事"], "expectedArtifacts": [], "acceptance": "合成预览：验收条件",
            "childTaskId": None, "createdAt": span_item["startAt"], "decidedAt": span_item["endAt"],
        })
    return records


#: The state the board keeps of a helper, by the state of its micro task.
CHILD_STATE = {"executing": "active", "waiting-helpers": "active", "waiting-owned": "active", "awaiting-host": "attention",
               "delivered": "succeeded", "accepted": "succeeded", "concluded": "failed", "failed": "failed", "cancelled": "cancelled"}


def workflow_view(entry: dict) -> dict:
    """The micro task view consumed by ``TaskDetails``/``WorkflowPanel``."""
    turns = turn_views(entry)
    children = [{"taskId": child["runId"], "role": "helper", "state": CHILD_STATE[child["state"]],
                 "integrator": False, "autoContinue": True, "requestId": child["requestId"]}
                for child in FIXTURES["runs"].values()
                if child["kind"] in ("micro-task", "helper") and child["parentRunId"] == entry["runId"]]
    routing = ({key: entry["routing"][key] for key in
                ("status", "decisionId", "taskId", "attemptId", "generation", "selectedBuddy", "reason", "note",
                 "constraints", "requiredCapabilities", "source", "routingBasis", *ROUTING_FACT_KEYS)}
               if entry["routing"] else {"status": "explicit" if entry["buddy"] else "needs-host",
                                         "decisionId": None, "taskId": None, "constraints": {},
                                         "source": "original-explicit" if entry["buddy"] else None})
    return {
        "runId": entry["runId"], "taskId": entry["runId"],
        "requestId": entry["requestId"], "title": entry["title"], "macroTaskId": entry["group"],
        "hostId": entry["currentHostId"], "ownerGeneration": 1, "revision": entry["revision"],
        "state": entry["state"], "status": entry["status"],
        "queueReason": "awaiting-owned" if entry["state"] == "waiting-owned" else None,
        "awaitingHost": entry["state"] == "awaiting-host",
        "continuationCount": entry["continuationCount"],
        # ADR-018: whether the Host may still re-choose the buddy, and
        # the Host's own recorded conclusion for a failed or cancelled micro task.
        "buddyLocked": entry["buddyLocked"],
        "hostConclusion": entry["hostConclusion"],
        "microTask": {"task": entry["task"], "taskTruncated": False,
                 "taskBytes": len(entry["task"].encode("utf-8")),
                 "harness": entry["adapter"] or (entry["buddy"] or {}).get("harness", "dsh"),
                 "cwd": entry["cwd"], "fingerprint": digest(f"{entry['runId']}-micro-task")},
        "buddy": entry["buddy"],
        "buddyRevision": 2 if entry["buddy"] else None,
        "routing": routing, "turns": turns,
        "currentTurn": ({**turns[0], "summary": turn_summary(entry["attempts"][-1]), "summaryTruncated": False,
                         "remaining": []} if turns else None),
        "activeRequest": entry["activeRequest"],
        "requests": [{key: record[key] for key in ("requestId", "kind", "state", "createdAt", "decidedAt",
                                                       "childTaskId", "routing", "reason")}
                     for record in request_records(entry)],
        "delegationAuthority": entry["delegationAuthority"],
        "counts": {"turns": len(turns),
                                                            "openRequests": 1 if entry["activeRequest"] else 0,
                                                            "children": len(children),
                                                            "artifacts": len(entry["artifacts"])},
        "children": children, "artifacts": artifact_views(entry), "integrations": entry["integrations"],
        "shutdown": shutdown_view(entry),
        "truncated": {"turns": 0, "requests": 0, "children": 0, "artifacts": 0, "pendingRequests": 0},
        "workspace": {"workspaceId": f"ws-{entry['runId']}", "path": entry["cwd"], "kind": "worktree",
                      "access": "write", "inputCommit": digest(f"{entry['runId']}-input")[:40],
                      "manifestSha256": digest(f"{entry['runId']}-workspace")},
        "createdAt": entry["createdAt"], "updatedAt": entry["updatedAt"],
        # A sealed partial output is never the run's final artifact.
        "finalArtifactId": next((item["artifactId"] for item in reversed(entry["artifacts"])
                                 if item["final"]), None),
        "finalAttemptId": (entry["attempts"][-1]["attemptId"]
                           if entry["resultAvailable"] and entry["attempts"]
                           and entry["state"] != "cancelled"
                           and entry["attempts"][-1]["disposition"] == "completed" else None),
        "task": task_view(entry),
    }


def task_first_line(task_text: str) -> str | None:
    """The bounded first nonempty task line, like the 0.15.1 read projection."""
    for line in task_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return None


PREVIEW_FIXES = {"preview-run-a2": "preview-run-a1", "preview-run-a3": "preview-run-a1"}


def timeline_row(entry: dict, depth: int) -> dict:
    attempts = entry["attempts"]
    confirmed = sum(1 for item in attempts if item["state"] == "finished" and item["shutdownConfirmed"])
    buddy = entry["buddy"]
    row = {
        "runId": entry["runId"], "parentRunId": entry["parentRunId"], "rootRunId": entry["rootRunId"],
        "rootLeadRunId": entry["lineage"]["rootLeadRunId"],
        "leadOwnership": entry["lineage"]["leadOwnership"],
        "delegationAuthority": entry["delegationAuthority"],
        "title": entry["title"], "titleSource": entry["titleSource"],
        "taskSummary": task_first_line(entry["task"]),
        "summary": "合成记录：已提交输出，等待按记录核对。" if entry["status"] == "completed" else None,
        "createdAt": entry["createdAt"], "state": entry["state"], "status": entry["status"],
        "category": entry["category"], "shutdownConfirmed": len(attempts) == confirmed, "depth": depth,
        "kind": entry["kind"],
        "buddy": ({key: buddy.get(key) for key in ("harness", "provider", "model", "effort")}
                          if buddy else None),
        "acceptedAt": entry["acceptedAt"], "acceptanceVerdict": entry["acceptanceVerdict"],
        "reviewRejectedAt": None, "reviewRejectionNote": None,
    }
    if entry["runId"] in PREVIEW_FIXES:
        target = FIXTURES["runs"][PREVIEW_FIXES[entry["runId"]]]
        row["fixes"] = target["runId"]
        row["fixesTitle"] = target["title"]
        row["fixesTitleSource"] = target["titleSource"]
    fixed_by = [run_id for run_id, target in PREVIEW_FIXES.items() if target == entry["runId"]]
    if fixed_by:
        row["fixedBy"] = fixed_by
    return row


def macro_task_summary(group: str, matching: set[str]) -> dict:
    entries = [FIXTURES["runs"][run_id] for run_id in FIXTURES["groups"][group]]
    counts = {"roots": 0, "helpers": 0, "accepted": 0, "failed": 0, "active": 0, "host": 0, "review": 0, "ended": 0,
              "stoppableRoots": 0, "stoppableHelpers": 0, "delivered": 0, "awaitingHost": 0}
    for entry in entries:
        counts[entry["category"]] += 1
        counts["helpers" if entry["parentRunId"] else "roots"] += 1
        if entry["state"] in ("executing", "waiting-helpers", "waiting-owned"):
            counts["stoppableHelpers" if entry["kind"] == "helper" else "stoppableRoots"] += 1
        if entry["state"] == "delivered":
            counts["delivered"] += 1
        elif entry["state"] == "awaiting-host":
            counts["awaitingHost"] += 1
        # 0.15.1 Host correction: accepted counts accepted ROOTS only; helpers
        # count through their root's acceptance, and the denominator is roots.
        if entry["parentRunId"] is None and entry["acceptanceVerdict"] == "accepted":
            counts["accepted"] += 1
        if entry["parentRunId"] is None and (entry["state"] == "failed" or
                                              (entry["hostConclusion"] or {}).get("executionStatus") == "failed"):
            counts["failed"] += 1
    state = next((name for name in ("host", "active", "review") if counts[name]), "ended")
    if group.startswith("macro-"):
        meta = MACRO_TASKS[group]
        title, title_source, project = meta["title"], "macro-task", meta["project"]
        description = meta.get("description")
        source_host, created, kind = meta["host"], meta["createdAt"], "macro-task"
        roots = [entry["runId"] for entry in entries if entry["parentRunId"] is None]
    else:
        root = FIXTURES["runs"][group[len("run:"):]]
        title, title_source, project = root["title"], root["titleSource"], root["project"]
        description = None
        source_host, created, kind, roots = root["sourceHostId"], root["createdAt"], "standalone", [root["runId"]]
    seq, at = FIXTURES["group_activity"][group]
    return {
        "macroTaskId": group, "kind": kind, "title": title, "titleSource": title_source,
        "description": description,
        "summary": "合成记录：保留独立委派的结果摘要。" if kind == "standalone" else None,
        "project": PROJECTS[project], "sourceHostId": source_host,
        "currentHostIds": sorted({entry["currentHostId"] for entry in entries if entry["currentHostId"]}),
        "createdAt": created, "lastActivityAt": at, "lastActivitySeq": seq, "state": state,
        "counts": counts, "matchingRuns": len(matching), "rootRunIds": roots,
    }


def span_views(entry: dict) -> list[dict]:
    attempts = {item["attemptId"]: item for item in entry["attempts"]}
    selected = entry["attempts"][-1] if entry["attempts"] else None
    views = []
    for item in entry["spans"]:
        execution = item["kind"] == "execution"
        attempt = attempts.get(item["attemptId"]) if execution else None
        current = (attempt is not None and attempt is selected and entry["status"] == "running"
                   and attempt["state"] != "finished" and attempt["result"] is None)
        usage_attempt = (next(record for record in FIXTURES["runs"][item["decisionTaskId"]]["attempts"]
                              if record["attemptId"] == item["attemptId"]) if item["kind"] == "routing" else attempt)
        views.append({
            **item, "runId": entry["runId"],
            "expectedSeconds": attempt["expectedSeconds"]
            if attempt is not None else None,
            "reminders": attempt_view(entry, attempt)["reminders"] if current else None,
            "inquiries": [{key: message[key] for key in ("inquiryId", "createdAt", "author", "state")}
                          for message in entry["messages"] if message["attemptId"] == attempt["attemptId"]]
            if attempt is not None else None,
            **({"tokenUsage": usage_attempt["usage"]} if usage_attempt is not None else {}),
        })
    return views


def marker_views(run_ids: list[str]) -> list[dict]:
    wanted = set(run_ids)
    return [item for _run_id, item in FIXTURES["markers"] if item["runId"] in wanted]


# --------------------------------------------------------------------------- #
# Filters and one-page reads                                                    #
# --------------------------------------------------------------------------- #
def parse_query(query: str, allowed: frozenset[str]) -> dict:
    entries = urllib.parse.parse_qs(query, keep_blank_values=True)
    params: dict[str, Any] = {}
    for name in sorted(entries):
        if name not in allowed:
            raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {name}")
        if len(entries[name]) != 1 or not entries[name][0]:
            raise PreviewError("INVALID_ARGUMENT", f"{name} must be a single nonempty value")
        params[name] = entries[name][0]
    return params


def parse_limit(params: dict, default: int, maximum: int) -> int:
    if "limit" not in params:
        return default
    if not INTEGER.match(params["limit"]):
        raise PreviewError("INVALID_ARGUMENT", "limit must be a nonnegative integer")
    value = int(params["limit"])
    if not 1 <= value <= maximum:
        raise PreviewError("INVALID_ARGUMENT", f"limit must be between 1 and {maximum}")
    return value


def applied_filters(params: dict) -> dict:
    applied: dict[str, Any] = {}
    for key, limit in (("query", 200), ("projectId", 4096), ("hostId", 256)):
        if params.get(key):
            if len(params[key]) > limit:
                raise PreviewError("INVALID_ARGUMENT", f"{key} is too long")
            applied[key] = params[key]
    name = params.get("filter") or "all"
    if name not in ("all", "active", "host", "review"):
        raise PreviewError("INVALID_ARGUMENT", "filter must be one of: all, active, host, review")
    if name != "all":
        applied["filter"] = name
    return applied


def matches(entry: dict, applied: dict) -> bool:
    """The fixture member predicate: project/host/query and the shared filters."""
    if applied.get("projectId") and (entry["project"] is None or
                                     PROJECTS[entry["project"]]["id"] != applied["projectId"]):
        return False
    if applied.get("hostId") and applied["hostId"] not in (entry["sourceHostId"], entry["currentHostId"]):
        return False
    if applied.get("query"):
        buddy = entry["buddy"] or {}
        project_path = PROJECTS[entry["project"]]["path"] if entry["project"] else ""
        haystack = " ".join((entry["runId"], entry["title"] or "", project_path,
                             entry["sourceHostId"] or "", entry["currentHostId"] or "",
                             buddy.get("harness", ""), buddy.get("model", ""))).casefold()
        if applied["query"].casefold() not in haystack:
            return False
    name = applied.get("filter", "all")
    if name == "all":
        return True
    if name == "host":
        return entry["state"] == "awaiting-host"
    if name == "active":
        return entry["status"] in ("queued", "running", "cancelling", "reconciliation-needed") or \
            entry["state"] in ("executing", "awaiting-host", "waiting-helpers")
    return entry["category"] == "review"


def group_matching(group: str, applied: dict) -> set[str]:
    return {run_id for run_id in FIXTURES["groups"][group]
            if matches(FIXTURES["runs"][run_id], applied)}


def macro_task_page(params: dict, state: PreviewState | None = None) -> dict:
    limit = parse_limit(params, 50, 100)
    applied = applied_filters(params)
    base_scope = {key: value for key, value in applied.items() if key != "filter"}
    groups = [group for group in FIXTURES["groups"] if state is None or group not in state.deleted]
    totals = {name: sum(bool(group_matching(group, {**base_scope, "filter": name}))
                        for group in groups)
              for name in ("all", "active", "host", "review")}
    rows = [(FIXTURES["group_activity"][group][0], group)
            for group in groups if group_matching(group, applied)]
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return {
        "macroTasks": [macro_task_summary(group, group_matching(group, applied))
                       for _seq, group in rows[:limit]],
        "totals": totals, "nextCursor": None, "cursor": state.cursor if state is not None else FIXTURES["head"], "changed": False,
    }


def tree_order(entries: list[dict]) -> list[tuple[dict, int]]:
    known = {entry["runId"] for entry in entries}
    children: dict[str | None, list[dict]] = {}
    for entry in entries:
        parent = entry["parentRunId"] if entry["parentRunId"] in known else None
        children.setdefault(parent, []).append(entry)
    for group in children.values():
        group.sort(key=lambda entry: (entry["createdAt"], entry["runId"]))
    ordered: list[tuple[dict, int]] = []

    def visit(entry: dict, depth: int) -> None:
        ordered.append((entry, depth))
        for child in children.get(entry["runId"], ()):
            visit(child, depth + 1)

    for root in children.get(None, ()):
        visit(root, 0)
    return ordered


CAPS = {"normal": (200, 800, 800), "readonly": (200, 800, 800), "error": (200, 800, 800),
        "truncated": (3, 3, 3)}


def macro_task_timeline(params: dict, scenario: str, state: PreviewState | None = None) -> dict:
    group = params.get("macroTaskId", "")
    if not MACRO_TASK_ID.match(group) or group not in FIXTURES["groups"] or state is not None and group in state.deleted:
        raise PreviewError("NOT_FOUND", "Unknown macroTaskId")
    limit = parse_limit(params, 100, 200)
    applied = applied_filters(params)
    entries = [FIXTURES["runs"][run_id] for run_id in FIXTURES["groups"][group]]
    matching = group_matching(group, applied)
    selected = [(entry, depth) for entry, depth in tree_order(entries) if entry["runId"] in matching]
    row_cap, span_cap, event_cap = CAPS[scenario]
    shown = selected[:min(limit, row_cap)]
    spans = sorted((item for entry, _depth in shown for item in span_views(entry)),
                   key=lambda item: (item["startAt"] or "", item["runId"], item["spanId"]))
    markers = marker_views([entry["runId"] for entry, _depth in shown])
    span_total, marker_total = len(spans), len(markers)
    spans, markers = spans[:span_cap], markers[:event_cap]
    truncated = {"rows": len(selected) > len(shown), "spans": span_total > len(spans),
                 "events": marker_total > len(markers)}
    return {
        "macroTask": macro_task_summary(group, matching), "observedAt": OBSERVED_AT,
        "cursor": state.cursor if state is not None else FIXTURES["head"], "rows": [timeline_row(entry, depth) for entry, depth in shown],
        "spans": spans, "events": markers,
        "totals": {"rows": len(selected), "spans": span_total, "events": marker_total,
                   "allRows": len(entries)},
        "truncated": truncated, "filtered": bool(applied),
        "scopeComplete": not applied and not any(truncated.values()),
    }



def buddy_views() -> list[dict]:
    # All buddy claims here are synthetic, including decision support. They
    # provide UI choices without asserting native verification of real models.
    views = []
    for harness, provider, model, efforts, enabled in CATALOG:
        for effort_rank, effort in enumerate(efforts):
            available = harness != LOGGED_OUT_HARNESS
            pending = (harness, provider, model) == PENDING_MODEL
            view = {"buddyId": f"{harness}:{provider}:{model}:{effort}",
                    "label": f"{model} · {effort} (synthetic preview)",
                    "harness": harness, "provider": provider, "model": model,
                    "effort": effort, "effortRank": effort_rank,
                    "available": available, "enabled": effort in enabled,
                    "capabilities": [f"execution:{harness}", "routing:fast"],
                    "contextWindow": 200000, "description": "synthetic preview model catalog entry",
                    "source": "synthetic-preview", "catalogState": "ready", "catalogReason": None,
                    "catalogStatus": "pending" if pending else "available",
                    "billing": {'kind': 'subscription' if harness in ('codex', 'claude') else 'metered',
                                'source': 'synthetic-preview', 'observedAt': OBSERVED_AT}, 'quotaExhausted': False}
            if not available:
                view["unavailableReason"] = reason("harness.login-required", operation="execution")
            if pending:
                view["pendingSince"] = "2026-09-26T03:00:00Z"
            views.append(view)
    # The four buddies the runs use come first, as the snapshot always listed them.
    used = [f"{cfg['harness']}:{cfg['provider']}:{cfg['model']}:{cfg['effort']}" for cfg in CONFIGS]
    return sorted(views, key=lambda view: used.index(view["buddyId"]) if view["buddyId"] in used else len(used))


def policy_views() -> dict:
    """Synthetic user policy: three buddy preferences and one model note."""
    def model_service(cfg: dict) -> dict:
        return {key: cfg[key] for key in ("harness", "provider", "model")}

    def buddy_id(cfg: dict) -> str:
        return f"{cfg['harness']}:{cfg['provider']}:{cfg['model']}:{cfg['effort']}"

    preferences = [{"buddyId": buddy_id(CFG_FLASH), "mode": "prefer", "reason": "synthetic preview choice"},
                   {"buddyId": buddy_id(CFG_CLAUDE), "mode": "exclude", "reason": "synthetic preview exclusion"},
                   {"buddyId": buddy_id(CFG_CODEX), "mode": "pin", "reason": ""}]
    return {
        "preferences": sorted(preferences, key=lambda entry: entry["buddyId"]),
        "modelServiceNotes": [{**model_service(CFG_CODEX), "text": "synthetic preview family note",
                               "updatedAt": "2026-09-26T00:00:00Z"}],
    }


def concurrency_rows() -> list[dict]:
    return [{"harness": harness, "provider": provider, "model": model,
             "limit": 1 if model == "deepseek-v4-pro" else 2,
             "active": 1 if (harness, model) in (("claude", CFG_CLAUDE["model"]), ("codex", CFG_CODEX["model"])) else 0}
            for harness, provider, model, _efforts, _enabled in CATALOG]


def harness_account(harness: str, status: str, *, account_type: str | None = None,
                    reason: dict | None = None) -> dict:
    """The native login facts displayed by the console."""
    return {"harness": harness, "status": status, "accountType": account_type, "checkedAt": instant(9, 55),
            "reason": reason}


def harness_rows() -> list[dict]:
    """The four ADR-018 console harness rows, in harness order, with account and quota.

    The accounts cover native login observations, a known account type,
    and a login that is missing. One provider carries the exhaustion
    record of the run that failed on quota, with its retry window open.

    Retained quota observations are display-only: an absent observation is
    ``null``, a stale one keeps its old ``observedAt``, and an unknown window is
    ``usedPercent: null`` — never 0.
    """
    return [
        {"harness": "dsh", "status": "ready", "available": True, "revision": 3, "manualPath": None,
         "executable": "/synthetic-preview/harnesses/dsh/dsh", "version": "synthetic-preview-1.0",
         "account": harness_account("dsh", "ready", account_type="apiKey"),
         "billingByProvider": {"deepseek-official": {"kind": "metered", "source": "synthetic-preview",
                                                     "observedAt": OBSERVED_AT}},
         "source": "synthetic-preview", "checkedAt": instant(9, 55),
         "quota": {"observedAt": instant(9, 50), "source": "dsh-native",
                   "provider": "deepseek-official", "stale": False, "balanceZero": True,
                   "windows": []}},
        {"harness": "zcode", "status": "ready", "available": True, "revision": 1, "manualPath": None,
         "executable": "/synthetic-preview/harnesses/zcode/zcode", "version": "synthetic-preview-0.4",
         "account": harness_account("zcode", "ready"),
         "quotaRouting": [{"provider": "zai", "limitId": None, "code": "HARNESS_QUOTA_EXHAUSTED",
                           "source": "zcode-native", "observedAt": instant(6, 26), "resetsAt": None, "blocked": True,
                           "retry": {"eligibleAt": instant(7, 26), "open": True, "pendingManual": False,
                                     "manualAt": None, "consumedAt": None, "consumedBy": None}}],
         "source": "synthetic-preview", "checkedAt": instant(9, 55),
         "quota": {"observedAt": instant(2, 30), "source": "zcode-native", "provider": "zai",
                   "stale": True,
                   "windows": [{"name": "5h", "usedPercent": None, "resetsAt": None},
                               {"name": "weekly", "usedPercent": 12, "resetsAt": instant(77, 0)}]}},
        {"harness": "codex", "status": "ready", "available": True, "revision": 1, "manualPath": None,
         'executable': '/synthetic-preview/codex', 'version': '0.159.0',
         "account": harness_account("codex", "ready", account_type="chatgpt"),
         'billingByProvider': {'openai': {'kind': 'subscription', 'source': 'synthetic-preview', 'observedAt': OBSERVED_AT}},
         'systemSandbox': True,
         'readOnlyStructured': {'eligible': True, 'reason': None, 'systemSandbox': True, 'sameAttemptContinuation': True},
         "source": "synthetic-preview", "checkedAt": instant(9, 55), "quota": None},
        {"harness": "claude", "status": "login-required", "available": False, "revision": 1, "manualPath": None,
         "executable": "/synthetic-preview/harnesses/claude/claude", "version": "synthetic-preview-2.1",
         "account": harness_account("claude", "logged-out", reason=reason("account.login-required")),
         "source": "synthetic-preview", "checkedAt": instant(9, 55),
         "reason": reason("harness.login-required", operation="execution"),
         "quota": {"observedAt": instant(9, 40), "source": "claude-native", "provider": "anthropic",
                   "stale": False,
                   "windows": [{"name": "5h", "usedPercent": 18, "resetsAt": instant(14, 30)},
                               {"name": "weekly", "usedPercent": 93, "resetsAt": instant(77, 0)}]}},
    ]


def pending_root_count(state: PreviewState | None = None) -> int:
    """The slim snapshot's board-wide awaiting-Host root count.

    Mirrors ``macro_tasks.pending_root_count`` over the synthetic runs: one
    distinct lineage root per micro task run whose state awaits its Host,
    deduplicated by ``rootRunId`` exactly like the service computes it.
    """
    roots = {entry["lineage"]["rootRunId"] for entry in FIXTURES["runs"].values()
             if entry["kind"] in ("micro-task", "helper") and entry["state"] == "awaiting-host"
             and (state is None or entry["runId"] not in state.removed_runs)}
    return len(roots)


def runtime_version_report() -> dict:
    """The version facts the settings page reads once: one synthetic installed runtime, in use."""
    facts = {"softwareVersion": "0.0.0", "contractVersion": "0.0.0", "schemaVersion": 0,
             "sourceCommit": "0" * 40, "installedAt": "2026-09-26T00:00:00Z"}
    return {"running": {"mode": "runtime", **facts}, "installed": dict(facts)}


def router_records(buddies: list[dict]) -> list[dict]:
    """The Router list with one record each: one serving, one waiting to be tried again, one that cannot serve."""
    def record(buddy_id: str, index: int, **facts) -> dict:
        buddy = next(item for item in buddies if item["buddyId"] == buddy_id)
        return {"buddyId": buddy_id, "index": index,
                "identity": {key: buddy[key] for key in ("harness", "provider", "model", "effort")},
                "eligible": True, "code": None, "inSkipWindow": False, "skipUntil": None, "retryAt": None,
                "retryInProgress": False, "answeredCount": 0, "noAnswerCount": 0, "consecutiveNoAnswers": 0,
                "lastAnsweredAt": None, "lastNoAnswerAt": None, "lastError": None, **facts}
    return [
        record(buddies[0]["buddyId"], 0, answeredCount=3, noAnswerCount=1, lastAnsweredAt=instant(6, 5),
               lastNoAnswerAt=instant(4, 12), lastError={"code": "routing-bounds-rejected", "at": instant(4, 12), "phase": "answer"}),
        record("codex:openai:gpt-5-mini:high", 1, inSkipWindow=True, skipUntil=instant(8, 46), retryAt=instant(8, 46),
               answeredCount=1, noAnswerCount=2, consecutiveNoAnswers=2, lastAnsweredAt=instant(3, 2),
               lastNoAnswerAt=instant(8, 36), lastError={"code": "router-no-answer", "at": instant(8, 36), "phase": "start"}),
        record("claude:anthropic:claude-opus-5-5:high", 2, eligible=False, code="harness-unavailable"),
    ]


def console_snapshot(scenario: str, assets_ready: bool, state: PreviewState | None = None) -> dict:
    buddies = buddy_views()
    routers = router_records(buddies)
    writable = scenario != "readonly"
    return {
        "csrfToken": SYNTHETIC_CSRF,
        "eventCursor": state.cursor if state is not None else FIXTURES["head"],
        "consoleAccess": {"requireLogin": False, "revision": 0, "sessions": []},
        "reminderSettings": {"silenceSeconds": 900, "revision": 1, "updatedAt": instant(0, 0)},
        "consoleSession": {"id": SYNTHETIC_SESSION_ID, "canWrite": writable,
                           "reason": None if writable else "superseded"},
        "configuration": {"routerBuddyIds": [entry["buddyId"] for entry in routers],
                          "routerRetryIntervalSeconds": 600,
                          "defaultRoutingMode": "fast", "routingBudget": "standard",
                          "routingBudgetLimits": {"preset": "standard", "timeoutSeconds": 300,
                                                  "toolCalls": 24, "bytesRead": 524288}},
        "configurationError": None,
        "buddies": buddies,
        "modelServiceConcurrency": concurrency_rows(),
        "harnesses": harness_rows(),
        "unavailableBuddyCount": sum(1 for buddy in buddies if not buddy["available"]),
        **policy_views(), "decisions": [],
        "routingHealth": {"windowSize": 20, "sampleCount": 4, "failureCount": 1,
                          "consecutiveFailures": 0, "abstentionCount": 1, "cancelledCount": 1,
                          "budgetExhaustedCount": 0, "boundsRejectedCount": 1, "inputChangedCount": 0,
                          "lastSuccessAt": instant(6, 5), "lastSuccessDecisionId": ROUTING_A1["decisionId"],
                          "available": True, "reasonCode": None, "currentRouterBuddyId": routers[0]["buddyId"],
                          "routers": routers,
                          "recentFailures": [{"decisionId": ROUTING_A2["decisionId"], "runId": "preview-run-a2",
                                              "at": instant(8, 36), "code": "routing-bounds-rejected"}]},
        "tasks": {"pendingCount": pending_root_count(state)},
        "capabilities": {"selection": True,
                         "decisionAdapter": True, "decisionAdapterReason": None,
                         "modelCatalogDiscovery": False, "taskControl": False,
                         "consoleAssets": assets_ready, "previewSynthetic": True},
    }


def decision_view(router: dict) -> dict:
    """One compact decision record from this exact internal router row."""
    routing = router["routing"]
    return {"decisionId": router["decisionId"], "requestId": router["requestId"], "kind": "select",
            "status": routing["status"], "task": router["task"], "runId": router["runId"],
            "buddyId": None, "selectedBuddy": routing["selectedBuddy"],
            "decisionModel": {"requested": ROUTER_BUDDY, "resolved": None, "observed": None},
            "reason": routing["reason"], "note": routing["note"], "evidence": [],
            "constraints": routing["constraints"], "requiredCapabilities": [], "routingBasis": routing.get("routingBasis"),
            "routerCalled": True,
            **{key: routing[key] for key in ROUTING_FACT_KEYS},
            "policyCheck": routing["policyCheck"], "usage": routing["usage"],
            "nativeIdentity": None, "stopEvidence": decision_result(routing)["stopEvidence"],
            "inputVerification": None, "routerTrials": [],
            "createdAt": router["createdAt"], "updatedAt": router["updatedAt"]}


def selection_get(params: dict, state: PreviewState | None = None) -> dict:
    unknown = sorted(set(params) - {"decisionId", "includeAudit"})
    if unknown:
        raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    decision_id = params.get("decisionId")
    if not isinstance(decision_id, str) or not decision_id:
        raise PreviewError("INVALID_ARGUMENT", "decisionId is required")
    router = next((item for item in FIXTURES["runs"].values()
                   if item["kind"] == "decision" and item.get("decisionId") == decision_id
                   and (state is None or item["runId"] not in state.removed_runs)), None)
    if router is None:
        raise PreviewError("NOT_FOUND", "Unknown decisionId")
    view = decision_view(router)
    if params.get("includeAudit"):
        routing = router["routing"]
        view.update({"attemptId": None, "generation": 1,
                                          "requested": {"constraints": routing["constraints"]}, "input": None,
                     "inputSha256": None, "output": decision_result(routing),
                     "requestedBuddy": ROUTER_BUDDY, "resolvedBuddy": None, "observedBuddy": None})
    return {"decision": view}


def audit_view(entry: dict) -> dict:
    """What only the audit read carries: each turn's full result, the Host's continuation inputs, every request."""
    return {
        "hostConclusions": [entry["hostConclusion"]] if entry["hostConclusion"] else [],
        "hostConclusionsTruncated": False,
        "turns": [{**turn_view(entry, item), "summary": turn_summary(item), "remaining": []}
                  for item in entry["attempts"]],
        "requests": [{**record, "attempted": record.get("attempted"), "turnId": None, "attemptId": None,
                      "decision": ({"decision": {"approved": "approve", "declined": "decline"}.get(record["state"]),
                                    "note": "合成预览：Host 的决定理由", "actor": host_actor(entry["currentHostId"] or HOST_A)}
                                   if record["state"] in ("approved", "declined") else None),
                      "payload": {"source": "routing", "decisionId": entry["routing"]["decisionId"]}
                      if record["routing"] and entry["routing"] else {}}
                     for record in request_records(entry)],
        "continuations": entry["continuations"],
        "reservations": [], "suggestions": [],
        "ownerGeneration": 1, "microTaskFingerprint": digest(f"{entry['runId']}-micro-task"),
    }


def message_list(params: dict, state: PreviewState | None = None) -> dict:
    """The inquiries of one run, newest first, as ``message_list`` returns them."""
    unknown = sorted(set(params) - {"runId", "taskId", "state", "limit"})
    if unknown:
        raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    run_id = params.get("runId") or params.get("taskId")
    limit = params.get("limit", 50)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise PreviewError("INVALID_ARGUMENT", "limit must be an integer from 1 to 200")
    if not isinstance(run_id, str):
        raise PreviewError("NOT_FOUND", "Unknown runId or requestId")
    entry = fixture_run(run_id, state)
    messages = sorted(entry["messages"], key=lambda item: item["createdAt"], reverse=True)
    return {"messages": messages[:limit], "cursor": state.cursor if state is not None else FIXTURES["head"]}


def workflow_get(params: dict, state: PreviewState | None = None) -> dict:
    unknown = sorted(set(params) - {"runId", "includeAudit", "routingHistory"})
    if unknown:
        raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    run_id = params.get("runId")
    if not isinstance(run_id, str) or not run_id:
        raise PreviewError("INVALID_ARGUMENT", "runId is required")
    if "includeAudit" in params and not isinstance(params["includeAudit"], bool):
        raise PreviewError("INVALID_ARGUMENT", "includeAudit must be a boolean")
    entry = fixture_run(run_id, state)
    if entry["kind"] == "decision":
        view = {"runId": run_id, "task": task_view(entry)}
        if params.get("routingHistory") is not None:
            view["routingHistory"] = {"entries": [], "nextCursor": None, "total": 0}
        return view
    view = workflow_view(entry)
    if params.get("includeAudit"):
        view["audit"] = audit_view(entry)
    history = params.get("routingHistory")
    if history is not None:
        if not isinstance(history, dict):
            raise PreviewError("INVALID_ARGUMENT", "routingHistory must be an object")
        records = sorted((router for router in FIXTURES["runs"].values()
                          if router["kind"] == "decision" and router["parentRunId"] == run_id),
                         key=lambda router: (router["createdAt"], router["decisionId"]), reverse=True)
        entries = []
        for router in records:
            routing = router["routing"]
            entries.append({"status": routing["status"], "decisionId": routing["decisionId"],
                     "taskId": routing["taskId"],
                     "selectedBuddy": routing["selectedBuddy"], "reason": routing["reason"], "note": routing["note"],
                     "createdAt": router["createdAt"],
                     **{key: routing[key] for key in ROUTING_FACT_KEYS},
                     "source": "model-selection",
                     "ownerGeneration": 1, "current": routing["decisionId"] == entry["routing"]["decisionId"]})
        view["routingHistory"] = {"entries": entries, "nextCursor": None, "total": len(entries)}
    return view


def command_result(scenario: str, operation: str, params: Any, state: PreviewState | None = None) -> dict:
    """Serve synthetic facts and in-memory deletion; never invoke product operations."""
    if not isinstance(params, dict):
        raise PreviewError("INVALID_ARGUMENT", "params must be an object")
    if operation in REMOVED_OPERATIONS:
        raise PreviewError("METHOD_NOT_FOUND",
                           f"Operation {operation!r} is not available through the console")
    if operation not in ALLOWED_OPERATIONS:
        raise PreviewError("METHOD_NOT_FOUND",
                           f"Operation {operation!r} is not available through the console")
    if operation in ("macro_task_deletion_preview", "macro_task_delete"):
        fields = {"macroTaskId"} | ({"microTaskCount", "confirm"} if operation == "macro_task_delete" else set())
        unknown = sorted(set(params) - fields)
        if unknown:
            raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
        group = params.get("macroTaskId")
        if not isinstance(group, str) or not MACRO_TASK_ID.fullmatch(group):
            raise PreviewError("INVALID_ARGUMENT", "macroTaskId must be a macro task or run:<rootRunId> identifier")
        if operation == "macro_task_delete":
            if params.get("confirm") is not True:
                raise PreviewError("INVALID_ARGUMENT", "confirm must be true")
            count = params.get("microTaskCount")
            if type(count) is not int or count < 0:
                raise PreviewError("INVALID_ARGUMENT", "microTaskCount must be a nonnegative integer")
        if scenario == "readonly":
            raise PreviewError("CONSOLE_READ_ONLY", "This synthetic preview session has no write authority")
        if scenario == "error" or operation == "macro_task_deletion_preview":
            preview = deletion_preview(group, state)
            if scenario == "error":
                raise PreviewError("SERVICE_UNAVAILABLE", ERROR_SCENARIO_MESSAGE)
            return preview
        assert state is not None, "a synthetic deletion requires an instance's PreviewState"
        return delete_macro_task(params, state)
    if operation == "macro_task_stop":
        unknown = sorted(set(params) - {"macroTaskId", "commandId", "note"})
        if unknown:
            raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
        if not isinstance(params.get("macroTaskId"), str) or not MACRO_TASK_ID.match(params.get("macroTaskId", "")):
            raise PreviewError("INVALID_ARGUMENT", "macroTaskId must be a macro task or run:<rootRunId> identifier")
        if not isinstance(params.get("commandId"), str) or not params.get("commandId"):
            raise PreviewError("INVALID_ARGUMENT", "commandId is required")
        if len(str(params.get("note") or "")) > 500:
            raise PreviewError("INVALID_ARGUMENT", "note is too long")
    # A check of the harnesses changes nothing here: it answers the same synthetic rows, and nothing is probed.
    checking = operation == "capabilities" and scenario != "readonly"
    if checking:
        unknown = sorted(set(params) - {"refresh", "harness"})
        if unknown:
            raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    if operation not in READ_OPERATIONS and not checking:
        # Only synthetic deletion changes this preview's in-memory records.
        # Other allowed product writes retain the preview's refusal.
        raise PreviewError("CONSOLE_READ_ONLY",
                           "This synthetic preview simulates deletion only; other writes are refused")
    if operation == "workflow_get":
        result = workflow_get(params, state)
    elif operation == "message_list":
        result = message_list(params, state)
    elif operation == "selection_get":
        result = selection_get(params, state)
    elif operation == "selection_list":
        decisions = [decision_view(item) for item in FIXTURES["runs"].values()
                     if item["kind"] == "decision" and (state is None or item["runId"] not in state.removed_runs)]
        result = {"decisions": decisions, "nextCursor": None, "total": len(decisions)}
    elif operation == "capabilities":
        rows = [row for row in harness_rows() if params.get("harness") in (None, row["harness"])]
        if not rows:
            raise PreviewError("INVALID_ARGUMENT", "harness names no harness of the preview")
        result = {"harnesses": rows}
    elif operation == "buddy_list":
        buddies = buddy_views()
        result = {"buddies": buddies, **policy_views(),
                  "modelServiceConcurrency": concurrency_rows(), "nextCursor": None}
    if scenario == "error":
        # Validate first, then simulate the read failure: a missing or unknown
        # identifier still answers 400/404 instead of being masked by the 503.
        raise PreviewError("SERVICE_UNAVAILABLE", ERROR_SCENARIO_MESSAGE)
    return result


# --------------------------------------------------------------------------- #
# HTTP                                                                          #
# --------------------------------------------------------------------------- #
def endpoint_lines(port: int) -> tuple[str, ...]:
    base = f"http://127.0.0.1:{port}"
    return (
        f"GET  {base}/api/console",
        f"GET  {base}/api/macro-tasks",
        f"GET  {base}/api/macro-tasks/{MACRO_A}/timeline",
        f"GET  {base}/api/macro-tasks/{urllib.parse.quote(STANDALONE_GROUP, safe='')}/timeline",
        f"GET  {base}/api/tasks/preview-run-a1",
        f"GET  {base}/api/runtime-version",
        f"POST {base}/api/command  workflow_get · message_list · selection_get · selection_list · "
        f"buddy_list · macro_task_deletion_preview · macro_task_delete "
        f"(deletion is in-memory only; other writes: 403; removed ops: 404)",
    )


class PreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    server_version = "hey-my-buddy-console-preview"
    sys_version = ""

    def __init__(self, address, *, assets: Path, scenario: str, verbose: bool):
        self.assets = assets
        self.started = time.monotonic()
        self.scenario = scenario
        self.state = PreviewState()
        self.verbose = verbose
        super().__init__(address, PreviewHandler)


class PreviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

    def date_time_string(self, timestamp=None):  # noqa: D401 - BaseHTTPRequestHandler API
        """The ``Date`` of every response runs on from the instant the fixtures were observed, by the time the
        service has been up, so that what waits is measured against the fixture clock, reads the same on any
        day, and the time of a check moves on."""
        observed = datetime.fromisoformat(OBSERVED_AT.replace("Z", "+00:00"))
        return format_datetime(observed + timedelta(seconds=int(time.monotonic() - self.server.started)), usegmt=True)

    def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler API
        if self.server.verbose:
            sys.stderr.write("preview %s\n" % (format % args))

    # -- responses ---------------------------------------------------------
    def _send(self, status: int, body: bytes, content_type: str, *, cache: str = "no-store") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header(PREVIEW_HEADER, PREVIEW_VALUE)
        self.send_header("X-Hey-My-Buddy-Preview-Scenario", self.server.scenario)
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Cross-Origin-Opener-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, status: int, value: Any) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"),
                   "application/json; charset=utf-8")

    def _error(self, status: int, code: str, message: str, **details: Any) -> None:
        self.close_connection = True
        self._json(status, error_envelope(code, message, **details))

    def _preview_error(self, error: PreviewError) -> None:
        self._error(STATUS_BY_CODE.get(error.code, 400), error.code, error.message, **error.details)

    def _path(self) -> str:
        path = urllib.parse.unquote(urllib.parse.urlsplit(self.path).path)
        parts = path.split("/")
        # Only the real console layout /console/<24-hex id>/<24-hex session>/ may be
        # stripped, so an asset path like /console/assets/x.js is never rewritten.
        if (len(parts) >= 4 and parts[1] == "console" and CONSOLE_ID.match(parts[2])
                and CONSOLE_ID.match(parts[3])):
            relative = "/" + "/".join(parts[4:])
            if relative in ("/", "") or relative.startswith(("/api/", "/assets/")):
                return relative
        return path

    def _trusted_host(self) -> bool:
        name, _, port = (self.headers.get("Host") or "").rpartition(":")
        if name not in ("127.0.0.1", "localhost") or port != str(self.server.server_address[1]):
            self._error(403, "FORBIDDEN", "Host is not the loopback preview origin")
            return False
        return True

    # -- read routes -------------------------------------------------------
    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
        with self.server.state.lock:
            self._read()

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler API
        with self.server.state.lock:
            self._read()

    def _read(self) -> None:
        if not self._trusted_host():
            return
        path = self._path()
        query = urllib.parse.urlsplit(self.path).query
        try:
            if path in ("", "/"):
                return self._page()
            if path == "/api/console":
                return self._json(200, console_snapshot(self.server.scenario, self._assets_ready(), self.server.state))
            if path == "/api/macro-tasks":
                params = parse_query(query, frozenset({"limit", "projectId", "hostId", "query", "filter"}))
                return self._json(200, macro_task_page(params, self.server.state))
            if path == "/api/runtime-version":
                return self._json(200, runtime_version_report())
            if path.startswith("/api/macro-tasks/") and path.endswith("/timeline"):
                identifier = path[len("/api/macro-tasks/"):-len("/timeline")]
                if not MACRO_TASK_ID.match(identifier) or identifier not in FIXTURES["groups"] or identifier in self.server.state.deleted:
                    return self._error(404, "NOT_FOUND", "Not found")
                params = parse_query(query, frozenset({"limit", "query", "filter"}))
                params["macroTaskId"] = identifier
                if self.server.scenario == "error":
                    # A known group read fails; an unknown group or a bad parameter
                    # above still answers 404/400 instead of being masked by the 503.
                    return self._error(503, "SERVICE_UNAVAILABLE", ERROR_SCENARIO_MESSAGE)
                return self._json(200, macro_task_timeline(params, self.server.scenario, self.server.state))
            if path.startswith("/api/tasks/"):
                run_id = path[len("/api/tasks/"):]
                if not RUN_ID.match(run_id) or run_id not in FIXTURES["runs"]:
                    return self._error(404, "NOT_FOUND", "Not found")
                return self._json(200, task_view(fixture_run(run_id, self.server.state)))
            if path.startswith("/api/"):
                return self._error(404, "NOT_FOUND", "Not found")
            return self._asset(path)
        except PreviewError as error:
            return self._preview_error(error)
        except Exception:  # noqa: BLE001 - no traceback crosses the boundary
            return self._error(500, "INTERNAL_ERROR", "The synthetic preview could not answer this read")

    # -- synthetic command route ------------------------------------------
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._trusted_host():
            return
        if self._path() != "/api/command":
            return self._error(404, "NOT_FOUND", "Not found")
        if (self.headers.get("X-Hey-My-Buddy-CSRF") or "") != SYNTHETIC_CSRF:
            return self._error(403, "FORBIDDEN", "The preview accepts only its fixed synthetic CSRF header")
        origin = self.headers.get("Origin")
        if origin is not None and origin != f"http://{self.headers.get('Host')}":
            return self._error(403, "FORBIDDEN", "Origin is not the preview origin")
        body = self._read_body()
        if body is None:
            return
        try:
            value = json.loads(body)
        except ValueError:
            return self._error(400, "INVALID_ARGUMENT", "The request body is not valid JSON")
        if not isinstance(value, dict):
            return self._error(400, "INVALID_ARGUMENT", "The request body must be a JSON object")
        unknown = sorted(set(value) - {"operation", "params"})
        if unknown:
            return self._error(400, "INVALID_ARGUMENT", f"Unknown command field: {unknown[0]}")
        operation = value.get("operation")
        if not isinstance(operation, str) or not operation:
            return self._error(400, "INVALID_ARGUMENT", "operation is required")
        try:
            with self.server.state.lock:
                result = command_result(self.server.scenario, operation, value.get("params", {}), self.server.state)
        except PreviewError as error:
            return self._preview_error(error)
        except Exception:  # noqa: BLE001 - no traceback crosses the boundary
            return self._error(500, "INTERNAL_ERROR", "The command failed inside the preview")
        return self._json(200, {"ok": True, "result": result})

    def do_PUT(self):  # noqa: N802 - BaseHTTPRequestHandler API
        self._error(405, "METHOD_NOT_FOUND", "Method not allowed")

    do_DELETE = do_PUT
    do_OPTIONS = do_PUT

    def _read_body(self) -> bytes | None:
        if (self.headers.get("Transfer-Encoding") or "").strip():
            self._error(411, "INVALID_ARGUMENT", "A bounded Content-Length body is required")
            return None
        try:
            length = int(self.headers.get("Content-Length") or -1)
        except ValueError:
            length = -1
        if length < 0:
            self._error(411, "INVALID_ARGUMENT", "A bounded Content-Length body is required")
            return None
        if length == 0 or length > MAX_BODY_BYTES:
            self._error(413, "MESSAGE_TOO_LARGE", f"The request body must be 1..{MAX_BODY_BYTES} bytes")
            return None
        return self.rfile.read(length)

    # -- static assets -----------------------------------------------------
    def _assets_ready(self) -> bool:
        return (self.server.assets / "index.html").is_file()

    def _page(self) -> None:
        index = self.server.assets / "index.html"
        if not index.is_file():
            body = SETUP_PAGE.format(assets=self.server.assets,
                                     endpoints="\n".join(endpoint_lines(self.server.server_address[1])))
            return self._send(503, body.encode("utf-8"), "text/html; charset=utf-8")
        try:
            body = index.read_bytes()
        except OSError:
            return self._error(500, "INTERNAL_ERROR", "The preview page could not be read")
        self._send(200, body, "text/html; charset=utf-8", cache="no-cache")

    def _asset(self, relative: str) -> None:
        target = resolve_asset(self.server.assets, relative)
        if target is None:
            first = Path(relative.lstrip("/")).parts[0] if relative.strip("/") else ""
            if (not relative.endswith("/") and "." not in Path(relative).name
                    and first not in ("assets", "api")):
                return self._page()
            return self._error(404, "NOT_FOUND", "Not found")
        try:
            body = target.read_bytes()
        except OSError:
            return self._error(500, "INTERNAL_ERROR", "The console asset could not be read")
        content_type = ASSET_TYPES[target.suffix.lower()]
        self._send(200, body, content_type)


def resolve_asset(root: Path, relative: str) -> Path | None:
    value = relative.lstrip("/")
    if not value or "\0" in value or "\\" in value:
        return None
    parts = value.split("/")
    if any(part in ("", ".", "..") or not SAFE_SEGMENT.match(part) for part in parts):
        return None
    if Path(parts[-1]).suffix.lower() not in ASSET_TYPES:
        return None
    base = root.resolve()
    try:
        resolved = base.joinpath(*parts).resolve()
        resolved.relative_to(base)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    try:
        return resolved if resolved.stat().st_size <= MAX_ASSET_BYTES else None
    except OSError:
        return None


# --------------------------------------------------------------------------- #
# Self checks                                                                   #
# --------------------------------------------------------------------------- #
REQUIRED_KEYS = {
    "MacroTaskPage": {"macroTasks", "totals", "nextCursor", "cursor", "changed"},
    "MacroTaskSummary": {"macroTaskId", "kind", "title", "titleSource", "description", "summary", "project",
                         "sourceHostId", "currentHostIds", "createdAt", "lastActivityAt", "lastActivitySeq",
                         "state", "counts", "matchingRuns", "rootRunIds"},
    "MacroTaskCounts": {"roots", "helpers", "accepted", "failed", "active", "host", "review", "ended",
                        "stoppableRoots", "stoppableHelpers", "delivered", "awaitingHost"},
    "TimelineSpan": {"spanId", "runId", "kind", "startAt", "endAt", "state", "attemptId", "turnId",
                     "turnIndex", "requestId", "buddy", "shutdownConfirmed", "uncertain",
                     "clockSkew", "expectedSeconds", "reminders", "inquiries"},
    "TimelineRow": {"runId", "parentRunId", "rootRunId", "rootLeadRunId", "leadOwnership", "delegationAuthority",
                    "title", "titleSource", "taskSummary", "summary",
                    "createdAt", "state", "status", "category", "shutdownConfirmed", "depth", "kind",
                    "buddy", "acceptedAt", "acceptanceVerdict", "reviewRejectedAt", "reviewRejectionNote"},
    "TimelineEvent": {"seq", "runId", "kind", "at", "decision", "verdict", "state", "strategy", "note", "actor", "attemptId",
                      "requestId", "artifactId"},
    "MacroTaskTimeline": {"macroTask", "observedAt", "cursor", "rows", "spans", "events", "totals",
                          "truncated", "filtered", "scopeComplete"},
}


def occupied_intervals(group: str) -> list[tuple[datetime, datetime]]:
    """Fixture occupancy, mirroring the layout's readSpan/marker rules."""
    observed = datetime.fromisoformat(OBSERVED_AT.replace("Z", "+00:00"))
    open_states = {"queued", "admitted", "pending", "starting", "executing", "finalizing", "uncertain",
                   "running", "active", "open", "in-progress", "waiting", "waiting-host", "awaiting-host",
                   "cancelling", "reconciliation-needed"}
    parse = lambda value: datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None
    intervals: list[tuple[datetime, datetime]] = []
    for run_id in FIXTURES["groups"][group]:
        entry = FIXTURES["runs"][run_id]
        for item in entry["spans"]:
            start, end = parse(item["startAt"]), parse(item["endAt"])
            if start is None:
                continue
            if item["uncertain"] and item["shutdownConfirmed"] is not True:
                end = observed
            if end is None:
                if item["shutdownConfirmed"] is True or not (item["uncertain"] or
                                                             item["state"].lower() in open_states):
                    continue
                end = observed
            intervals.append((start, end))
        for item in entry["markers"]:
            at = parse(item["at"])
            intervals.append((at - timedelta(minutes=1), at + timedelta(minutes=1)))
    merged: list[tuple[datetime, datetime]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def idle_gaps(group: str) -> list[tuple[datetime, datetime]]:
    merged = occupied_intervals(group)
    return [(merged[index][1], merged[index + 1][0])
            for index in range(len(merged) - 1) if merged[index + 1][0] > merged[index][1]]


def token_usage_problems(label: str, value: Any) -> list[str]:
    """ADR-018 per-attempt usage: ``null``, or a complete object scoped to one attempt."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [f"{label}: tokenUsage must be null or an object"]
    problems: list[str] = []
    if set(value) != TOKEN_USAGE_KEYS:
        problems.append(f"{label}: tokenUsage keys are {sorted(value)}")
    if value.get("scope") != "attempt":
        problems.append(f'{label}: tokenUsage.scope must be "attempt"')
    if not isinstance(value.get("source"), str) or not value.get("source"):
        problems.append(f"{label}: tokenUsage.source must be a nonempty string")
    for key in ("inputTokens", "cachedInputTokens", "outputTokens"):
        item = value.get(key, "absent")
        if item is not None and (type(item) is not int or item < 0):
            problems.append(f"{label}: tokenUsage.{key} must be a nonnegative integer or null")
    if isinstance(value.get("inputTokens"), int) and isinstance(value.get("cachedInputTokens"), int) \
            and value["cachedInputTokens"] > value["inputTokens"]:
        problems.append(f"{label}: cachedInputTokens must stay a subset of inputTokens")
    return problems


def reason_problems(value: Any, registry: dict, path: str = "reply") -> list[str]:
    """Check program causes against the current registry, leaving human preferences intact."""
    problems = []
    if isinstance(value, list):
        for index, item in enumerate(value):
            problems.extend(reason_problems(item, registry, f"{path}[{index}]"))
    elif isinstance(value, dict):
        for key, item in value.items():
            field = f"{path}.{key}"
            if key in ("reason", "releaseReason", "unavailableReason", "catalogReason", "decisionAdapterReason") and item is not None:
                if key == "reason" and ".preferences[" in path:
                    continue  # The user's preference text remains its current public field.
                if (not isinstance(item, dict) or set(item) != {"code", "params"}
                        or item.get("code") not in registry or not isinstance(item.get("params"), dict)
                        or set(item["params"]) - set(registry[item["code"]])):
                    problems.append(f"{field}: program reason must use the current code/params shape")
            problems.extend(reason_problems(item, registry, field))
    return problems


def validate_fixtures() -> list[str]:
    """Shape drift, determinism and the idle-gap invariants of the fixtures."""
    problems: list[str] = []
    page = macro_task_page({})
    timeline = macro_task_timeline({"macroTaskId": MACRO_A}, "normal")
    configuration = console_snapshot("normal", True)["configuration"]
    if not {"routerBuddyIds", "routerRetryIntervalSeconds", "defaultRoutingMode", "routingBudget",
            "routingBudgetLimits"} <= set(configuration) or "decisionBuddyId" in configuration:
        problems.append("snapshot must use the ADR-021 ordered Router configuration")
    for name, value, keys in (
            ("MacroTaskPage", page, REQUIRED_KEYS["MacroTaskPage"]),
            ("MacroTaskSummary", page["macroTasks"][0], REQUIRED_KEYS["MacroTaskSummary"]),
            ("MacroTaskCounts", page["macroTasks"][0]["counts"], REQUIRED_KEYS["MacroTaskCounts"]),
            ("MacroTaskTimeline", timeline, REQUIRED_KEYS["MacroTaskTimeline"]),
            ("TimelineRow", timeline["rows"][0], REQUIRED_KEYS["TimelineRow"]),
            ("TimelineSpan", timeline["spans"][0], REQUIRED_KEYS["TimelineSpan"]),
            ("TimelineEvent", timeline["events"][0], REQUIRED_KEYS["TimelineEvent"])):
        present = set(value)
        problems += [f"{name}: missing {key}" for key in sorted(keys - present)]
    for _run_id, event in FIXTURES["markers"]:
        if (REQUIRED_KEYS["TimelineEvent"] - set(event) or {"label", "summary"} & set(event)
                or any(event[key] is not None and not isinstance(event[key], str)
                       for key in ("decision", "verdict", "state", "strategy", "note"))):
            problems.append("TimelineEvent must carry facts and notes without localized labels or summaries")
        actor = event["actor"]
        if actor is not None and (not isinstance(actor, dict) or set(actor) != {"role", "id"}
                                  or actor["role"] not in {"host", "lead", "console"}
                                  or not isinstance(actor["id"], str) or not actor["id"]):
            problems.append("TimelineEvent.actor must be a structured role and id or null")
    owned = FIXTURES["runs"]["preview-run-b2"]
    if (owned["sourceHostId"] is not None or owned["currentHostId"] is not None
            or owned["lineage"]["leadOwnership"]["leadRunId"] != "preview-run-b1"
            or owned["markers"][0]["actor"] != {"role": "lead", "id": "preview-run-b1"}):
        problems.append("Lead-owned preview run must keep its nullable Host and explicit Lead actor")
    for group in FIXTURES["groups"]:
        group_timeline = macro_task_timeline({"macroTaskId": group}, "normal")
        for row in group_timeline["rows"]:
            lineage = FIXTURES["runs"][row["runId"]]["lineage"]
            missing = REQUIRED_KEYS["TimelineRow"] - set(row)
            if missing:
                problems.append(f"{row['runId']}: TimelineRow missing {sorted(missing)}")
            for key in ("rootLeadRunId", "leadOwnership", "delegationAuthority"):
                if key not in row or row[key] != lineage[key]:
                    problems.append(f"{row['runId']}: timeline {key} differs from task lineage")
            if type(row["delegationAuthority"]) is not bool:
                problems.append(f"{row['runId']}: delegationAuthority must be a boolean")
            if row["titleSource"] == "none" and row["title"] is not None:
                problems.append(f"{row['runId']}: absent author title must stay null")
        for span in group_timeline["spans"]:
            if REQUIRED_KEYS["TimelineSpan"] - set(span):
                problems.append(f"{span['spanId']}: timeline span missing F2 facts")
            if span["kind"] != "execution":
                if any(span[key] is not None for key in ("expectedSeconds", "reminders", "inquiries")):
                    problems.append(f"{span['spanId']}: non-execution span carries turn facts")
            elif span["expectedSeconds"] is None or span["expectedSeconds"] <= 0 or span["inquiries"] is None:
                problems.append(f"{span['spanId']}: execution span lacks turn facts")
    live = next(span for span in timeline["spans"] if span["spanId"] == "execution:att-a3-1")
    if (live["expectedSeconds"] != 600 or live["reminders"] is None
            or not live["reminders"]["overdue"] or not live["reminders"]["possiblyStuck"]
            or [item["inquiryId"] for item in live["inquiries"]] != ["inq-a3-1"]):
        problems.append("preview-run-a3 must show a live overrun, silence reminder and Host inquiry")
    if "测试跑了很久还没有结束" in json.dumps(live, ensure_ascii=False):
        problems.append("timeline inquiry must omit question body")
    historic = next(span for span in timeline["spans"] if span["spanId"] == "execution:att-a1-2")
    if historic["expectedSeconds"] != 900 or historic["reminders"] is not None:
        problems.append("preview second turn must retain its own duration without live reminders")
    fixes = {row["runId"]: row for row in timeline["rows"]}
    if (fixes["preview-run-a2"].get("fixes") != "preview-run-a1"
            or fixes["preview-run-a3"].get("fixes") != "preview-run-a1"
            or fixes["preview-run-a2"].get("fixesTitle") != FIXTURES["runs"]["preview-run-a1"]["title"]
            or fixes["preview-run-a2"].get("fixesTitleSource") != "title"
            or fixes["preview-run-a1"].get("fixedBy") != ["preview-run-a2", "preview-run-a3"]):
        problems.append("preview must show two fixes of the same accepted micro task")
    for run_id, fixed_run_id in PREVIEW_FIXES.items():
        target = FIXTURES["runs"][fixed_run_id]
        if target["state"] != "accepted" or target["acceptedAt"] > FIXTURES["runs"][run_id]["createdAt"]:
            problems.append(f"{run_id}: a fix must follow the target's acceptance")
    lead = workflow_get({"runId": "preview-run-b3"})
    waiting = workflow_get({"runId": "preview-run-b4"})
    active = workflow_get({"runId": "preview-run-b5"})
    turn = lead["currentTurn"]
    dispatch_turn = lead["turns"][-1]
    if (lead["state"] != "waiting-owned" or lead["status"] != "completed"
            or not lead["task"]["lineage"]["delegationAuthority"]
            or lead["task"]["activeAttemptId"] is not None
            or lead["task"]["queueReason"] != "awaiting-owned" or lead["queueReason"] != "awaiting-owned"
            or turn["state"] != "returned" or turn["disposition"] != "waiting" or turn["turnIndex"] != 2
            or lead["continuationCount"] != 1 or len(lead["turns"]) != 2
            or not dispatch_turn["updatedAt"] < waiting["updatedAt"] < turn["createdAt"]
            or not lead["shutdown"]["selfConfirmed"]
            or lead["finalAttemptId"] is not None or lead["finalArtifactId"] is not None):
        problems.append("preview-run-b3 must be a stopped, returned waiting Lead without a final delivery")
    for view in (waiting, active):
        lineage = view["task"]["lineage"]
        ownership = lineage["leadOwnership"]
        if (lineage["rootLeadRunId"] != lead["runId"] or ownership["leadRunId"] != lead["runId"]
                or ownership["leadTurnId"] != dispatch_turn["turnId"]
                or lineage["sourceHostId"] is not None or lineage["currentHostId"] is not None
                or lineage["parentRunId"] is not None or lineage["rootRunId"] != view["runId"]
                or not dispatch_turn["createdAt"] <= ownership["createdAt"] <= dispatch_turn["updatedAt"]
                or view["updatedAt"] >= turn["createdAt"]):
            problems.append(f"{view['runId']}: owned work must retain its creation turn and predate the parked Lead's input")
    if (waiting["state"] != "awaiting-host" or not waiting["awaitingHost"]
            or waiting["activeRequest"] != LEAD_REQUEST
            or waiting["currentTurn"]["disposition"] != "assistance"
            or not waiting["shutdown"]["selfConfirmed"]):
        problems.append("preview-run-b4 must keep its open assistance request waiting for Lead")
    if (active["state"] != "executing" or active["status"] != "queued"
            or active["task"]["activeAttemptId"] is not None or active["currentTurn"] is not None
            or active["task"]["selectedAttemptId"] is not None or active["task"]["resultAvailable"]):
        problems.append("preview-run-b5 must keep the unclaimed owned micro task that allows the Lead to wait")
    beta_rows = macro_task_timeline({"macroTaskId": MACRO_B}, "normal")["rows"]
    if sum(row["rootLeadRunId"] == "preview-run-b3" for row in beta_rows) != 4:
        problems.append("preview-run-b3 must own four independent micro tasks")
    if not any(not row["delegationAuthority"] and row["rootLeadRunId"] is None and row["kind"] == "micro-task"
               for row in beta_rows):
        problems.append("Lead lanes must include an ordinary independent micro task")
    no_children = timeline_row(FIXTURES["runs"]["preview-run-d1"], 0)
    if not no_children["delegationAuthority"] or any(entry["leadOwner"] and entry["leadOwner"][0] == no_children["runId"]
                                                    for entry in FIXTURES["runs"].values()):
        problems.append("a Lead without owned micro tasks must retain its explicit authority")
    if set(page) != REQUIRED_KEYS["MacroTaskPage"]:
        problems.append(f"MacroTaskPage: keys {sorted(set(page))}")
    expected_totals = {name: sum(bool(group_matching(group, {"filter": name}))
                                 for group in FIXTURES["groups"])
                       for name in ("all", "active", "host", "review")}
    if page["totals"] != expected_totals or macro_task_page({"filter": "host"})["totals"] != expected_totals:
        problems.append("macro task totals must count each category across the same scope regardless of selected filter")
    if json.dumps(macro_task_page({}), sort_keys=True) != json.dumps(macro_task_page({}), sort_keys=True):
        problems.append("macro task list is not stable across reads")
    if json.dumps(macro_task_timeline({"macroTaskId": MACRO_B}, "normal"), sort_keys=True) != json.dumps(
            macro_task_timeline({"macroTaskId": MACRO_B}, "normal"), sort_keys=True):
        problems.append("macro task timeline is not stable across reads")
    if any(end - start > timedelta(minutes=30) for start, end in idle_gaps(MACRO_A)):
        problems.append("macro task A's unconfirmed tail must prevent false idle folding")
    biggest = max((gap[1] - gap[0] for gap in idle_gaps(MACRO_B)), default=timedelta())
    if biggest <= timedelta(minutes=30):
        problems.append("macro task B has no strictly-greater-than-30-minute idle gap")
    if not any(gap[1] - gap[0] == timedelta(minutes=30) for gap in idle_gaps(MACRO_B)):
        problems.append("macro task B does not carry the exact 30-minute non-folding boundary")
    if not macro_task_timeline({"macroTaskId": MACRO_A}, "normal")["scopeComplete"]:
        problems.append("normal scope must be complete")
    if not macro_task_timeline({"macroTaskId": MACRO_A}, "truncated")["truncated"]["rows"]:
        problems.append("truncated scenario must report row truncation")
    summary_a = page["macroTasks"][0] if page["macroTasks"][0]["macroTaskId"] == MACRO_A else next(
        item for item in page["macroTasks"] if item["macroTaskId"] == MACRO_A)
    if not summary_a["description"] or len(summary_a["description"]) > 300:
        problems.append("macro task A must carry a bounded description")
    if any(item["description"] is not None and item["kind"] == "standalone"
           for item in page["macroTasks"]):
        problems.append("standalone groups must not carry a description")
    if not all(isinstance(item["counts"][key], int) for item in page["macroTasks"]
               for key in ("accepted", "failed")):
        problems.append("root outcome counts must be present on every summary")
    accepted_a = sum(1 for run_id in FIXTURES["groups"][MACRO_A]
                     if FIXTURES["runs"][run_id]["parentRunId"] is None
                     and FIXTURES["runs"][run_id]["acceptanceVerdict"] == "accepted")
    for item in page["macroTasks"]:
        if item["counts"]["accepted"] > item["counts"]["roots"]:
            problems.append(f"{item['macroTaskId']}: accepted exceeds roots")
        expected_failed = sum(1 for run_id in FIXTURES["groups"][item["macroTaskId"]]
                              if FIXTURES["runs"][run_id]["parentRunId"] is None
                              and (FIXTURES["runs"][run_id]["state"] == "failed" or
                                   (FIXTURES["runs"][run_id]["hostConclusion"] or {}).get("executionStatus") == "failed"))
        if item["counts"]["failed"] != expected_failed:
            problems.append(f"{item['macroTaskId']}: failed does not match failed root turns")
    if summary_a["counts"]["accepted"] != accepted_a:
        problems.append("counts.accepted does not match the accepted members")
    if not all(row["taskSummary"] == task_first_line(FIXTURES["runs"][row["runId"]]["task"])
               for row in timeline["rows"]):
        problems.append("taskSummary must be the first nonempty task line")
    standalone_rows = macro_task_timeline({"macroTaskId": STANDALONE_GROUP}, "normal")["rows"]
    if not any(row["taskSummary"] is None for row in standalone_rows):
        problems.append("the blank-task helper must keep a null taskSummary")
    categories = {entry["category"] for entry in FIXTURES["runs"].values() if entry["kind"] in ("micro-task", "helper")}
    if categories != {"host", "active", "review", "ended"}:
        problems.append(f"fixture categories are {sorted(categories)}")
    kinds = {item["kind"] for _run_id, item in FIXTURES["markers"]}
    if set(MARKER_EVENTS) - kinds:
        problems.append(f"missing marker kinds: {sorted(set(MARKER_EVENTS) - kinds)}")
    routing_spans = [item for item in timeline["spans"] if item["kind"] == "routing"]
    statuses: set[str] = set()
    for item in routing_spans:
        decision = selection_get({"decisionId": item["decisionId"], "includeAudit": True})["decision"]
        statuses.add(decision["status"])
        router = FIXTURES["runs"][item["decisionTaskId"]]
        if router["parentRunId"] != item["runId"] or router["decisionId"] != item["decisionId"]:
            problems.append(f"{item['spanId']}: routing decision attributed to another run")
        projection = item["routing"]
        if projection.get("routingMode") not in {"fast", "review"} or {"requestedRoutingMode", "fallback"} & projection.keys():
            problems.append(f"{item['spanId']}: routing mode must follow the current frozen Router facts")
        if any(projection[key] != decision[key] for key in projection):
            problems.append(f"{item['spanId']}: decision detail differs from span projection")
        expected_result = {"completed": "ok", "failed": "failed", "cancelled": "cancelled", "needs-host": "ok"}
        if item.get("resultStatus") != expected_result[decision["status"]]:
            problems.append(f"{item['spanId']}: routing result status is inconsistent")
        if decision["reason"] == reason("router-abstained") and (item.get("error") or item.get("disposition") != "abstention"):
            problems.append("routing abstention must not project as a failure")
        policy = projection["policyCheck"]
        if set(policy) != {"hardConstraints", "userPreference"}:
            problems.append(f"{item['spanId']}: policyCheck differs from Python selection policy shape")
        limits = {**projection["budget"], "elapsedMs": projection["budget"]["timeoutSeconds"] * 1000}
        if any(value is not None and (type(value) is not int or value < 0 or
               key in limits and value > limits[key]) for key, value in projection["usage"].items()):
            problems.append(f"{item['spanId']}: synthetic usage exceeds its budget")
        if projection["routingMode"] == "fast" and (projection["usage"]["toolCalls"] != 0 or
                                                    set(projection["budget"]) != {"timeoutSeconds"}):
            problems.append(f"{item['spanId']}: fast routing must have zero tool calls and only a deadline")
        entries = workflow_get({"runId": item["runId"], "routingHistory": {}})["routingHistory"]["entries"]
        if not any(entry["decisionId"] == item["decisionId"] and entry["status"] == decision["status"]
                   for entry in entries):
            problems.append(f"{item['spanId']}: precise decision missing from owner routing history")
    if len(routing_spans) != 4 or statuses != {"completed", "failed", "cancelled", "needs-host"}:
        problems.append(f"routing fixtures must cover four outcomes: {sorted(statuses)}")
    history_a2 = workflow_get({"runId": "preview-run-a2", "routingHistory": {}})["routingHistory"]
    if history_a2["total"] != 2 or sum(entry["current"] for entry in history_a2["entries"]) != 1:
        problems.append("rerouting history must preserve both decisions with one current decision")
    if json.dumps(FIXTURES, sort_keys=True) != json.dumps(build_runs(), sort_keys=True):
        problems.append("routing fixture construction is not deterministic")
    snapshot = console_snapshot("normal", False)
    registry = json.loads((Path(__file__).resolve().parents[2] / "src/hey_my_buddy/protocol/reason-codes.json").read_text(encoding="utf-8"))
    problems.extend(reason_problems(snapshot, registry))
    if {"limitations"} & snapshot["capabilities"].keys():
        problems.append("capabilities must not carry the retired fixed limitations text")
    for entry in FIXTURES["runs"].values():
        view = workflow_get({"runId": entry["runId"], "includeAudit": True, "routingHistory": {}})
        problems.extend(reason_problems(view, registry))
        problems.extend(reason_problems(message_list({"runId": entry["runId"]}), registry))
        if "waitReason" in view:
            problems.append(f"{entry['runId']}: retired waitReason must be absent")
        if entry["kind"] == "decision":
            problems.extend(reason_problems(decision_view(entry), registry))
    for harness, provider, model, efforts, _enabled in CATALOG:
        for buddy in snapshot["buddies"]:
            if (buddy["harness"], buddy["provider"], buddy["model"]) == (harness, provider, model) \
                    and buddy["effortRank"] != efforts.index(buddy["effort"]):
                problems.append(f"{buddy['buddyId']}: effortRank differs from the model catalog order")
    for group in FIXTURES["groups"]:
        preview = deletion_preview(group)
        problems.extend(reason_problems(preview, registry))
        values = [*preview["scope"]["artifacts"].values(), *preview["scope"]["sessions"].values(),
                  preview["scope"]["checkouts"], preview["scope"]["gitRefs"]]
        if any(value is not None and (type(value) is not int or value < 0) for value in values):
            problems.append(f"{group}: deletion scope must carry nonnegative integers or null")
        if preview["blockers"] != sorted(preview["blockers"], key=lambda item: (item["taskId"], item["reason"]["code"])):
            problems.append(f"{group}: deletion blocker ordering drifted")
        for blocker in preview["blockers"]:
            if blocker["titleSource"] == "none" and blocker["title"] is not None:
                problems.append(f"{group}: an untitled blocker must carry null")
    health = snapshot["routingHealth"]
    for status, field in (("failed", "failureCount"), ("cancelled", "cancelledCount"), ("needs-host", "abstentionCount")):
        if health[field] != sum(selection_get({"decisionId": item["decisionId"]})["decision"]["status"] == status
                                for item in routing_spans):
            problems.append(f"routingHealth.{field} differs from synthetic decisions")
    if any(not isinstance(health.get(key), int) or health[key] < 0 for key in
           ("budgetExhaustedCount", "boundsRejectedCount", "inputChangedCount")):
        problems.append("routing health must expose nonnegative synthetic budget/bounds/input statistics")

    # -- ADR-018 schema-15 fixture data ------------------------------------- #
    conclusions, locked, partials, patched, host_paths_seen = 0, 0, 0, False, False
    usage_sources: set[str] = set()
    unknown_usage = False
    for entry in FIXTURES["runs"].values():
        if entry["kind"] == "decision":
            continue
        view = workflow_get({"runId": entry["runId"]})
        if not isinstance(view.get("buddyLocked"), bool):
            problems.append(f"{entry['runId']}: buddyLocked must be a boolean")
        if "hostConclusion" not in view:
            problems.append(f"{entry['runId']}: micro task view must carry hostConclusion")
        if view.get("buddyLocked"):
            locked += 1
        conclusion = view.get("hostConclusion")
        if conclusion is not None:
            conclusions += 1
            if not isinstance(conclusion, dict) or set(conclusion) != HOST_CONCLUSION_KEYS:
                problems.append(f"{entry['runId']}: hostConclusion keys drifted")
            elif conclusion["executionStatus"] not in ("failed", "cancelled"):
                problems.append(f"{entry['runId']}: hostConclusion must record a failed/cancelled outcome")
            elif not isinstance(conclusion["evidence"], list) or not 1 <= len(conclusion["evidence"]) <= 8 \
                    or not all(isinstance(item, str) and 0 < len(item) <= 200
                               for item in conclusion["evidence"]):
                problems.append(f"{entry['runId']}: hostConclusion.evidence must be a bounded short-string list")
            elif type(conclusion["ownerGeneration"]) is not int or type(conclusion["runRevision"]) is not int:
                problems.append(f"{entry['runId']}: hostConclusion must carry ownerGeneration and runRevision")
        turns = view["turns"]
        if len(turns) != len(entry["attempts"]):
            problems.append(f"{entry['runId']}: every attempt must keep its own turn entry")
        for item, recorded in zip(turns, reversed(entry["attempts"])):
            if item.get("tokenUsage") != recorded["usage"]:
                problems.append(f"{entry['runId']}: turn {item['turnId']} usage differs from its attempt")
            problems += token_usage_problems(f"{entry['runId']} turn {item['turnId']}",
                                             item.get("tokenUsage"))
            value = item.get("tokenUsage")
            if isinstance(value, dict) and isinstance(value.get("source"), str):
                usage_sources.add(value["source"])
            if recorded["state"] == "finished" and recorded["usage"] is None:
                unknown_usage = True
        if turns:
            problems += token_usage_problems(f"{entry['runId']} currentTurn",
                                             view["currentTurn"].get("tokenUsage"))
        problems += token_usage_problems(f"{entry['runId']} task", view["task"].get("tokenUsage"))
        selected = view["task"].get("selectedAttempt") or {}
        if turns and selected.get("tokenUsage") != view["currentTurn"].get("tokenUsage"):
            problems.append(f"{entry['runId']}: selectedAttempt usage differs from currentTurn")
        for item in view["artifacts"]:
            if item["kind"] not in ("output", "resolved-output", "partial-output"):
                continue
            if item["kind"] == "partial-output":
                partials += 1
                if (item["partial"], item["verified"], item["final"]) != (True, False, False):
                    problems.append(f"{entry['runId']}: {item['artifactId']} must be unverified and non-final")
                if view["finalArtifactId"] == item["artifactId"]:
                    problems.append(f"{entry['runId']}: a partial output must never be the final artifact")
            patch = item.get("cumulativePatch")
            if patch is None:
                continue
            if not isinstance(patch, dict) or set(patch) != {"baseCommit", "outputCommit", "path", "sha256",
                                                             "changedPaths"}:
                problems.append(f"{entry['runId']}: {item['artifactId']} cumulativePatch shape drifted")
            elif patch["baseCommit"] != view["workspace"]["inputCommit"]:
                problems.append(f"{entry['runId']}: {item['artifactId']} patch base is not the workspace input")
            elif patch["changedPaths"] != item["changedPaths"] or not patch["path"] or not patch["sha256"]:
                problems.append(f"{entry['runId']}: {item['artifactId']} patch disagrees with the artifact")
            elif item["kind"] != "partial-output":
                patched = True
        for item in view["integrations"]:
            paths = item["verification"].get("hostPaths")
            if not isinstance(paths, list) or not all(isinstance(path, str) and path for path in paths):
                problems.append(f"{entry['runId']}: integration verification.hostPaths must be a string list")
            elif paths:
                host_paths_seen = True
    for label, covered in (("a Host conclusion", conclusions), ("a locked buddy", locked),
                           ("a sealed partial output", partials), ("a patched final artifact", patched),
                           ("a non-empty verification.hostPaths", host_paths_seen),
                           ("a finished attempt with unknown usage", unknown_usage)):
        if not covered:
            problems.append(f"the fixtures must cover {label}")
    if not {"dsh-native", "codex-native", "zcode-native"} <= usage_sources:
        problems.append(f"token usage sources are {sorted(usage_sources)}")
    for run_id, expected in (("preview-run-b1-h1", "cancelled"), ("preview-run-b2", "failed")):
        conclusion = workflow_get({"runId": run_id})["hostConclusion"]
        if not conclusion or conclusion["executionStatus"] != expected:
            problems.append(f"{run_id}: missing the {expected} Host conclusion")
    quota_failed = workflow_get({"runId": "preview-run-b2"})
    attempt_record = quota_failed["task"].get("selectedAttempt") or {}
    if quota_failed["status"] != "failed" or QUOTA_ERROR not in (attempt_record.get("error") or ""):
        problems.append("preview-run-b2 must stay failed and record the synthetic quota exhaustion")
    if not any(item["kind"] == "partial-output" for item in quota_failed["artifacts"]):
        problems.append("preview-run-b2 must carry its sealed partial output")
    multi_turn = workflow_get({"runId": "preview-run-a1"})
    if len({json.dumps(turn["tokenUsage"], sort_keys=True) for turn in multi_turn["turns"]}) != len(
            multi_turn["turns"]):
        problems.append("each turn must keep its own usage, never one session-cumulative number")

    # -- console snapshot harness rows -------------------------------------- #
    harnesses = console_snapshot("normal", False).get("harnesses")
    if not isinstance(harnesses, list) or [row.get("harness") for row in harnesses] != ["dsh", "zcode",
                                                                                        "codex", "claude"]:
        problems.append("harnesses must list dsh, zcode, codex and claude in that order")
        harnesses = []
    stale_unknown = False
    for row in harnesses:
        if (row.get("available") is True) != (row.get("status") == "ready"):
            problems.append(f"harness {row.get('harness')}: available must mirror status")
        if row.get("manualPath", "absent") is not None:
            problems.append(f"harness {row.get('harness')}: manualPath must be null")
        if type(row.get("revision")) is not int or row["revision"] < 0:
            problems.append(f"harness {row.get('harness')}: revision must be a nonnegative integer")
        quota = row.get("quota")
        if quota is None:
            continue
        if not isinstance(quota, dict) or not isinstance(quota.get("observedAt"), str) \
                or not isinstance(quota.get("source"), str) or not isinstance(quota.get("windows"), list):
            problems.append(f"harness {row.get('harness')}: malformed quota observation")
            continue
        if row["harness"] == "dsh" and (row["billingByProvider"]["deepseek-official"]["kind"] != "metered"
                                         or quota.get("balanceZero") is not True or quota["windows"]):
            problems.append("DSH must be metered with a zero balance and no quota windows")
        if quota.get("stale") is True and any(window.get("usedPercent") is None
                                              for window in quota["windows"]):
            stale_unknown = True
        for window in quota["windows"]:
            used = window.get("usedPercent")
            if used is None:
                continue
            if type(used) is not int or not 0 <= used <= 100:
                problems.append(f"harness {row.get('harness')}: window usage must be 0..100 or null")
    if not stale_unknown:
        problems.append("one harness quota observation must be stale with an unknown window")
    if not any(row.get("quota") is None for row in harnesses):
        problems.append("one harness row must carry quota: null")

    # -- the emitted fixture tree -------------------------------------------- #
    plan = emit_fixture_files()
    payloads = fixture_payloads()
    if payloads != fixture_payloads():
        problems.append("the emitted fixture tree is not byte-identical across builds")
    for scenario in EMIT_SCENARIOS + ("incompatible",):
        written = tuple(item["path"].split("/", 1)[1] for item in plan if item["scenario"] == scenario)
        if written != EMIT_LAYOUT[scenario]:
            problems.append(f"emitted {scenario} files are {written}")
    manifest = json.loads(payloads["manifest.json"])
    if set(manifest) != {"fixtureVersion", "source", "scenarios", "files"} \
            or manifest["fixtureVersion"] != 1 or manifest["source"] != EMIT_SOURCE \
            or manifest["scenarios"] != list(EMIT_SCENARIOS) or len(manifest["files"]) != len(plan):
        problems.append("manifest.json header differs from the documented shape")
    rejected = {item["path"]: "INVALID_RESPONSE" for item in plan if item["scenario"] == "incompatible"
                and item["manifest"]["expected"] == "rejected"}
    rejected["readonly/console.json"] = "INVALID_RESPONSE"
    rejected["error/timeline-macro-a.json"] = "SERVICE_UNAVAILABLE"
    rejected["error/workflow-preview-run-a1.json"] = "SERVICE_UNAVAILABLE"
    for entry in manifest["files"]:
        code = rejected.get(entry["path"])
        if code is not None:
            if entry["expected"] != "rejected" or entry.get("expectedCode") != code:
                problems.append(f"{entry['path']}: must be rejected with {code}")
        elif entry["expected"] != "accepted" or "expectedCode" in entry:
            problems.append(f"{entry['path']}: must be an accepted fixture")
        if (entry["httpStatus"] == 503) != (entry.get("expectedCode") == "SERVICE_UNAVAILABLE"):
            problems.append(f"{entry['path']}: HTTP status and expected ApiError code disagree")
        if entry["scenario"] == "incompatible" and not entry.get("mutation"):
            problems.append(f"{entry['path']}: an incompatible fixture must document its mutation")
        request = entry.get("request")
        if not isinstance(request, dict):
            problems.append(f"{entry['path']}: every manifest entry must name its request")
        elif entry["kind"] == "macro-task-timeline":
            want = ({"macroTaskId": STANDALONE_GROUP}
                    if entry["path"].endswith("timeline-standalone.json") else
                    {"macroTaskId": {"timeline-macro-b.json": MACRO_B, "timeline-macro-c.json": MACRO_C}.get(
                        entry["path"].split("/", 1)[1], MACRO_A)})
            if request != want:
                problems.append(f"{entry['path']}: request must be {want}")
        elif entry["kind"] == "workflow-get":
            if set(request) != {"runId"} or not isinstance(request.get("runId"), str):
                problems.append(f"{entry['path']}: a workflow-get request must name exactly one runId")
            elif entry["scenario"] == "incompatible":
                if request["runId"] != "preview-run-a1":
                    problems.append(f"{entry['path']}: the mutated workflow view is preview-run-a1")
            elif f"workflow-{request['runId']}.json" != entry["path"].split("/", 1)[1]:
                problems.append(f"{entry['path']}: request runId differs from the file name")
        elif entry["kind"] in ("workflow-audit", "message-list"):
            prefix = "workflow-audit" if entry["kind"] == "workflow-audit" else "messages"
            if set(request) != {"runId"} or f"{prefix}-{request.get('runId')}.json" != entry["path"].split("/", 1)[1]:
                problems.append(f"{entry['path']}: a {entry['kind']} request must name the runId of its file")
        elif request != {}:
            problems.append(f"{entry['path']}: {entry['kind']} takes no request parameters")
    if not json.loads(payloads["truncated/timeline-macro-a.json"])["truncated"]["rows"]:
        problems.append("the emitted truncated timeline must report its truncation flags")
    if payloads["normal/console.json"] != encode_json(console_snapshot("normal", assets_ready=False)):
        problems.append("emitted console.json must be the exact console_snapshot body")
    if payloads["normal/workflow-preview-run-a1.json"] != encode_json(workflow_get({"runId": "preview-run-a1"})):
        problems.append("an emitted workflow payload must be the exact workflow_get result")
    return problems


# --------------------------------------------------------------------------- #
# Emitted fixture tree (--emit-fixtures)                                        #
# --------------------------------------------------------------------------- #
def clone(value: Any) -> Any:
    """A JSON round-trip copy, so an incompatible mutation never touches the source fixture."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True))


def encode_json(value: Any) -> bytes:
    """The one emitted byte shape: UTF-8, sorted keys, two-space indent, trailing newline."""
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def emit_fixture_files() -> list[dict]:
    """Every emitted file in manifest order: scenario, path, payload and manifest row."""
    files: list[dict] = []

    def add(scenario: str, name: str, kind: str, endpoint: str, status: int, expected: str,
            value: Any, *, request: dict, expected_code: str | None = None,
            mutation: str | None = None, note: str | None = None) -> None:
        row: dict[str, Any] = {"scenario": scenario, "path": f"{scenario}/{name}", "kind": kind,
                               "endpoint": endpoint, "request": request, "httpStatus": status,
                               "expected": expected}
        if expected_code is not None:
            row["expectedCode"] = expected_code
        if mutation is not None:
            row["mutation"] = mutation
        if note is not None:
            row["note"] = note
        files.append({"scenario": scenario, "path": row["path"], "value": value, "manifest": row})

    failure = error_envelope("SERVICE_UNAVAILABLE", ERROR_SCENARIO_MESSAGE)
    timeline_endpoint = "GET /api/macro-tasks/<macroTaskId>/timeline"
    workflow_endpoint = "POST /api/command workflow_get"
    workflows = {"normal": ("preview-run-a1", "preview-run-a2", "preview-run-b1", "preview-run-b1-h1",
                            "preview-run-b2", "preview-run-b3", "preview-run-b4", "preview-run-b5",
                            "preview-run-b6", "preview-run-b7", "preview-run-b8", "preview-run-b9", "preview-run-b10",
                            "preview-run-d1", "preview-run-d2"),
                 "readonly": ("preview-run-a1",), "truncated": ("preview-run-a1",),
                 "error": ("preview-run-a1",)}
    for scenario in EMIT_SCENARIOS:
        if scenario == "readonly":
            # 0.18 removed read-only console sessions, so the superseded-session
            # snapshot shape is deliberately no longer valid for the parser.
            add(scenario, "console.json", "console-snapshot", "GET /api/console", 200, "rejected",
                console_snapshot(scenario, assets_ready=False), request={},
                expected_code="INVALID_RESPONSE")
        else:
            add(scenario, "console.json", "console-snapshot", "GET /api/console", 200, "accepted",
                console_snapshot(scenario, assets_ready=False), request={})
        if scenario == "normal":
            # Settings reads version facts separately from the slim snapshot.
            add(scenario, "runtime-version.json", "runtime-version", "GET /api/runtime-version",
                200, "accepted", runtime_version_report(), request={})
            # Task views let the parser check that timeline lineage matches its
            # full task records. This is internal evidence, not an HTTP route.
            records = [task_view(entry) for entry in FIXTURES["runs"].values()]
            add(scenario, "tasks.json", "lineage-records", "fixture:lineage-records",
                200, "accepted", {"runs": records, "total": len(records),
                                   "cursor": FIXTURES["head"], "nextCursor": None}, request={})
        add(scenario, "macro-tasks.json", "macro-tasks-page", "GET /api/macro-tasks", 200, "accepted",
            macro_task_page({}), request={})
        if scenario == "error":
            add(scenario, "timeline-macro-a.json", "macro-task-timeline", timeline_endpoint, 503, "rejected",
                failure, request={"macroTaskId": MACRO_A}, expected_code="SERVICE_UNAVAILABLE")
        else:
            add(scenario, "timeline-macro-a.json", "macro-task-timeline", timeline_endpoint, 200, "accepted",
                macro_task_timeline({"macroTaskId": MACRO_A}, scenario), request={"macroTaskId": MACRO_A})
        if scenario == "normal":
            add(scenario, "timeline-macro-b.json", "macro-task-timeline", timeline_endpoint, 200,
                "accepted", macro_task_timeline({"macroTaskId": MACRO_B}, scenario),
                request={"macroTaskId": MACRO_B})
            add(scenario, "timeline-macro-c.json", "macro-task-timeline", timeline_endpoint, 200,
                "accepted", macro_task_timeline({"macroTaskId": MACRO_C}, scenario),
                request={"macroTaskId": MACRO_C},
                note="Every row requires delegationAuthority, including a Lead without owned micro tasks")
            add(scenario, "timeline-standalone.json", "macro-task-timeline", timeline_endpoint, 200,
                "accepted", macro_task_timeline({"macroTaskId": STANDALONE_GROUP}, scenario),
                request={"macroTaskId": STANDALONE_GROUP})
        if scenario == "normal":
            # The audit read and the inquiries are read separately from the compact record.
            for run_id in ("preview-run-a1", "preview-run-a2"):
                add(scenario, f"workflow-audit-{run_id}.json", "workflow-audit",
                    "POST /api/command workflow_get includeAudit", 200, "accepted",
                    workflow_get({"runId": run_id, "includeAudit": True, "routingHistory": {"limit": 100}}),
                    request={"runId": run_id})
            for run_id in ("preview-run-a1", "preview-run-a3"):
                add(scenario, f"messages-{run_id}.json", "message-list", "POST /api/command message_list",
                    200, "accepted", message_list({"runId": run_id, "limit": 200}), request={"runId": run_id})
        for run_id in workflows[scenario]:
            name = f"workflow-{run_id}.json"
            if scenario == "error":
                add(scenario, name, "workflow-get", workflow_endpoint, 503, "rejected", failure,
                    request={"runId": run_id}, expected_code="SERVICE_UNAVAILABLE")
            else:
                add(scenario, name, "workflow-get", workflow_endpoint, 200, "accepted",
                    workflow_get({"runId": run_id}), request={"runId": run_id})

    # Deliberately wrong shapes for the strict frontend parser. The readonly
    # snapshot is the real fixture for the retired superseded-session shape; the
    # malformed quota stays readable because a bad optional observation must be
    # dropped as unknown rather than blanking the page or reading as 0%.
    normal = console_snapshot("normal", assets_ready=False)
    legacy = clone(normal)
    for key in ("routerBuddyIds", "routerRetryIntervalSeconds", "defaultRoutingMode"):
        legacy["configuration"].pop(key, None)
    legacy["configuration"]["decisionBuddyId"] = buddy_views()[0]["buddyId"]
    add("incompatible", "snapshot-legacy-single-router.json", "console-snapshot", "GET /api/console", 200,
        "rejected", legacy, request={}, expected_code="INVALID_RESPONSE",
        mutation="configuration reverted to the retired schema-13 single-Router shape: decisionBuddyId "
                 "added while routerBuddyIds, routerRetryIntervalSeconds and defaultRoutingMode were removed")
    missing_session = clone(normal)
    missing_session.pop("consoleSession", None)
    add("incompatible", "snapshot-missing-session.json", "console-snapshot", "GET /api/console", 200,
        "rejected", missing_session, request={}, expected_code="INVALID_RESPONSE",
        mutation="consoleSession removed, so the snapshot cannot describe the console session")
    missing_tasks = clone(normal)
    missing_tasks.pop("tasks", None)
    add("incompatible", "snapshot-missing-tasks.json", "console-snapshot", "GET /api/console", 200,
        "rejected", missing_tasks, request={}, expected_code="INVALID_RESPONSE",
        mutation="tasks removed, leaving the slim snapshot without the server's board-wide pending count")
    missing_revision = clone(workflow_get({"runId": "preview-run-a1"}))
    missing_revision.pop("revision", None)
    add("incompatible", "workflow-missing-revision.json", "workflow-get", workflow_endpoint, 200,
        "rejected", missing_revision, request={"runId": "preview-run-a1"}, expected_code="INVALID_RESPONSE",
        mutation="revision removed from the micro task workflow view, so owner-generation fencing is unprovable")
    malformed_quota = clone(normal)
    malformed_quota["harnesses"][0]["quota"]["windows"] = "near-limit"
    add("incompatible", "harness-quota-malformed.json", "console-snapshot", "GET /api/console", 200,
        "accepted", malformed_quota, request={},
        mutation="harnesses[0].quota.windows replaced by the string \"near-limit\" while every other "
                 "harness field stays valid",
        note="malformed quota must be dropped as unknown, never displayed as 0")

    order = {scenario: index for index, scenario in enumerate(EMIT_SCENARIOS + ("incompatible",))}
    files.sort(key=lambda item: (order[item["scenario"]], item["path"]))
    return files


def fixture_payloads() -> dict[str, bytes]:
    """The complete emitted tree as relative path -> exact bytes, ``manifest.json`` included."""
    files = emit_fixture_files()
    payloads = {item["path"]: encode_json(item["value"]) for item in files}
    payloads["manifest.json"] = encode_json({"fixtureVersion": 1, "source": EMIT_SOURCE,
                                             "scenarios": list(EMIT_SCENARIOS),
                                             "files": [item["manifest"] for item in files]})
    return payloads


def emit_fixtures(directory: Path) -> int:
    """Write the deterministic fixture tree; nonzero only on an OS error."""
    root = Path(directory)
    payloads = fixture_payloads()
    try:
        for name, body in payloads.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
    except OSError as error:
        print(f"cannot write the fixture tree under {root}: {error}", file=sys.stderr)
        return 1
    print(f"emitted {len(payloads)} fixture files under {root}")
    return 0


# --------------------------------------------------------------------------- #
# Modes                                                                         #
# --------------------------------------------------------------------------- #
def print_banner(server: PreviewHTTPServer, assets: Path) -> None:
    print("=" * 78)
    print("SYNTHETIC PREVIEW ONLY — not a hey-my-buddy board; no daemon, Worker, model call or")
    print("production connection. Every /api/ response is fixture data labelled by the")
    print(f"{PREVIEW_HEADER}: {PREVIEW_VALUE} response header.")
    print("=" * 78)
    print(f"scenario:  {server.scenario}")
    print(f"assets:    {assets} (index.html: {'found' if (assets / 'index.html').is_file() else 'MISSING'})")
    print(f"base URL:  http://127.0.0.1:{server.server_address[1]}/")
    print("endpoints:")
    for line in endpoint_lines(server.server_address[1]):
        print(f"  {line}")
    print("fixture groups:")
    for group in (MACRO_A, MACRO_B, MACRO_C, STANDALONE_GROUP):
        summary = macro_task_summary(group, set(FIXTURES["groups"][group]))
        members = summary["counts"]["roots"] + summary["counts"]["helpers"]
        print(f"  {group}  state={summary['state']} members={members} project={summary['project']['label']}")
    print("deletion changes synthetic memory only; other writes are refused; Ctrl-C stops the preview.")


def run_server(assets: Path, scenario: str, port: int, verbose: bool) -> int:
    try:
        server = PreviewHTTPServer(("127.0.0.1", port), assets=assets, scenario=scenario,
                                   verbose=verbose)
    except OSError as error:
        print(f"cannot bind 127.0.0.1:{port}: {error}", file=sys.stderr)
        return 1
    print_banner(server, assets)

    def stop(_signum, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\npreview stopped by interrupt (Ctrl-C/SIGTERM)")
    finally:
        server.shutdown()
        server.server_close()
    print(f"preview stopped; 127.0.0.1:{server.server_address[1]} released")
    return 0


def smoke(assets: Path, scenario: str) -> int:
    """Start on an ephemeral port, read the surface back with stdlib urllib, stop."""
    server = PreviewHTTPServer(("127.0.0.1", 0), assets=assets, scenario=scenario, verbose=False)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    failures: list[str] = []

    def call(method: str, path: str, *, payload: dict | None = None) -> tuple[int, dict, dict]:
        headers = {"X-Hey-My-Buddy-CSRF": SYNTHETIC_CSRF, "Origin": base, "Content-Type": "application/json"}
        request = urllib.request.Request(base + path, method=method, headers=headers)
        if payload is not None:
            request.data = json.dumps(payload).encode()
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                raw, status, response_headers = response.read(), response.status, dict(response.headers)
        except urllib.error.HTTPError as error:
            raw, status, response_headers = error.read(), error.code, dict(error.headers)
        try:
            return status, json.loads(raw), response_headers
        except ValueError:
            return status, {}, response_headers

    def check(name: str, condition: bool, detail: str = "") -> None:
        print(f"  {'PASS' if condition else 'FAIL'}  {name}{'' if condition else ' · ' + detail}")
        if not condition:
            failures.append(name)

    try:
        status, _body, headers = call("GET", "/")
        check("GET / serves an index or labeled setup page", status in (200, 503), f"status={status}")
        check("synthetic label header", headers.get(PREVIEW_HEADER) == PREVIEW_VALUE)
        status, snapshot, _headers = call("GET", "/api/console")
        check("GET /api/console", status == 200 and snapshot.get("csrfToken") == SYNTHETIC_CSRF
              and snapshot["consoleSession"]["id"] == SYNTHETIC_SESSION_ID, f"status={status}")
        check("slim snapshot carries the board-wide pending count",
              snapshot.get("tasks") == {"pendingCount": pending_root_count()},
              str(snapshot.get("tasks")))
        status, _records, _headers = call("GET", "/api/tasks")
        check("removed GET /api/tasks returns 404", status == 404, f"status={status}")
        status, version, _headers = call("GET", "/api/runtime-version")
        check("GET /api/runtime-version serves the version facts", status == 200
              and version.get("running", {}).get("mode") == "runtime"
              and version.get("installed", {}).get("softwareVersion") == "0.0.0", f"status={status}")
        status, page, _headers = call("GET", "/api/macro-tasks")
        check("GET /api/macro-tasks", status == 200 and len(page["macroTasks"]) == len(FIXTURES["groups"])
              and page["cursor"] == FIXTURES["head"] and page["changed"] is False, f"status={status}")
        status, timeline, _headers = call("GET", f"/api/macro-tasks/{MACRO_A}/timeline")
        if scenario == "error":
            check("timeline fails closed in error scenario", status == 503
                  and timeline.get("error", {}).get("code") == "SERVICE_UNAVAILABLE", f"status={status}")
        else:
            check("GET /api/macro-tasks/<id>/timeline", status == 200 and timeline["observedAt"] == OBSERVED_AT,
                  f"status={status}")
            check("truncated scenario reports truncation",
                  (scenario == "truncated") == bool(timeline["truncated"]["rows"]),
                  str(timeline["truncated"]))
        biggest = max((gap[1] - gap[0] for gap in idle_gaps(MACRO_B)), default=timedelta())
        check("truly idle gap > 30 min", biggest > timedelta(minutes=30), f"biggest={biggest}")
        status, task, _headers = call("GET", "/api/tasks/preview-run-a1")
        check("GET /api/tasks/<id>", status == 200 and task.get("runId") == "preview-run-a1",
              f"status={status}")
        status, _, _headers = call(
            "GET", f"/api/macro-tasks/{urllib.parse.quote(STANDALONE_GROUP, safe='')}/timeline")
        expected = 503 if scenario == "error" else 200
        check("encoded standalone timeline", status == expected, f"status={status}")
        status, result, _headers = call("POST", "/api/command", payload={"operation": "workflow_get",
                                                               "params": {"runId": "preview-run-a1"}})
        if scenario == "error":
            check("workflow_get fails closed", status == 503, f"status={status}")
        else:
            check("POST workflow_get is read-only", status == 200 and result["result"]["microTask"]["task"] == FIXTURES["runs"]["preview-run-a1"]["task"],
                  f"status={status}")
        status, denied, _headers = call("POST", "/api/command", payload={"operation": "workflow_cancel",
                                                               "params": {"runId": "preview-run-a1"}})
        check("removed Host operation is METHOD_NOT_FOUND", status == 404
              and denied["error"]["code"] == "METHOD_NOT_FOUND", f"status={status}")
        status, stop, _headers = call("POST", "/api/command", payload={
            "operation": "macro_task_stop",
            "params": {"macroTaskId": MACRO_A, "commandId": "synthetic-stop-command-1"}})
        check("macro_task_stop is allowed-but-refused by the preview", status == 403
              and stop["error"]["code"] == "CONSOLE_READ_ONLY", f"status={status}")
        status, _, _headers = call("POST", "/api/command", payload={
            "operation": "macro_task_stop", "params": {"macroTaskId": "macro-not-a-real-group",
                                                     "commandId": "synthetic-stop-command-2"}})
        check("macro_task_stop validates its identifiers first", status in (400, 403), f"status={status}")
        if scenario != "error":
            check("timeline rows carry the 0.15.1 taskSummary", any(
                row.get("taskSummary") for row in timeline.get("rows", [])))
        status, unknown, _headers = call("POST", "/api/command", payload={"operation": "nope", "params": {}})
        check("unknown operation is METHOD_NOT_FOUND", status == 404
              and unknown["error"]["code"] == "METHOD_NOT_FOUND", f"status={status}")
        status, _missing, _headers = call(
            "GET", "/api/macro-tasks/macro-ffffffff-ffff-4fff-8fff-ffffffffffff/timeline")
        check("unknown macro task is 404", status == 404, f"status={status}")
        if scenario == "readonly":
            check("readonly scenario reports a superseded session",
                  snapshot["consoleSession"]["canWrite"] is False)
    finally:
        server.shutdown()
        server.server_close()
    print(f"preview smoke: {len(failures)} failure(s) with scenario={scenario}")
    return 1 if failures else 0


def prepare_assets(value: str | None) -> Path:
    if not value:
        raise SystemExit("--assets ABS_PATH is required (for example src/hey_my_buddy/console/assets)")
    assets = Path(value).resolve()
    if not assets.is_dir():
        raise SystemExit(f"assets directory does not exist: {assets}")
    return assets


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(line_buffering=True)  # the banner must reach a log immediately
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(
        description="Deterministic synthetic preview server for the work-macro-task timeline UI.",
        epilog="All data is synthetic; no board, daemon, Worker, model call or production connection is used.")
    parser.add_argument("--assets", help="absolute path of the built console assets directory")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--port", type=int, default=0, help="0 binds an ephemeral loopback port (default)")
    parser.add_argument("--verbose", action="store_true", help="enable access logging (off by default)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="run the fixture self-checks and exit")
    modes.add_argument("--smoke", action="store_true", help="start, read back with urllib, and exit")
    modes.add_argument("--emit-fixtures", metavar="DIR",
                       help="write the deterministic fixture tree under DIR and exit (no --assets needed)")
    args = parser.parse_args(argv)

    if args.check:
        problems = validate_fixtures()
        for problem in problems:
            print(f"CHECK FAIL  {problem}")
        if problems:
            return 1
        biggest = max((gap[1] - gap[0] for gap in idle_gaps(MACRO_B)), default=timedelta())
        print(f"CHECK OK  shapes stable · idle gap {biggest} · fixtures deterministic · "
              f"{len(FIXTURES['runs'])} runs / {len(FIXTURES['groups'])} groups · "
              f"{len(harness_rows())} harnesses")
        return 0

    if args.emit_fixtures is not None:
        if not args.emit_fixtures.strip():
            print("--emit-fixtures requires a nonempty DIR", file=sys.stderr)
            return 2
        return emit_fixtures(Path(args.emit_fixtures))

    assets = prepare_assets(args.assets)
    if args.smoke:
        return smoke(assets, args.scenario)
    return run_server(assets, args.scenario, args.port, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
