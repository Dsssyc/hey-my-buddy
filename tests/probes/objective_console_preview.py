#!/usr/bin/env python3
"""Deterministic, read-only preview server for the work-objective timeline UI.

Safety boundary:

* Every ``/api/`` byte is a synthetic fixture declared in this file. Nothing is
  read from a board, state directory, credential or running service, and no
  daemon, Worker, model call or production connection is started. Responses are
  labelled by the ``X-Buddy-Preview: synthetic-fixture-data`` header.
* The server binds ``127.0.0.1`` only (ephemeral port by default).
* It never reads or sets a browser cookie. ``buddy_console_session`` is scoped
  to host ``127.0.0.1`` without a port, so emitting one here could clobber a
  real private console on another port; the fixed synthetic session and CSRF
  strings exist only inside the JSON snapshot.
* All mutation POSTs are refused. ``POST /api/command`` serves the same five
  read-only operations the real console allows (``workflow_get``,
  ``selection_get``, ``selection_list``, ``model_profiles``,
  ``evaluation_history``) with fixed-shape fixture payloads.
* Fixture records reuse the same field names as the console DTOs
  (``apps/console/src/objective-types.ts`` and the console snapshot/task views).
  Read pages are one fixed page: ``cursor`` is a constant event head and
  ``nextCursor`` is always null, so repeated polling returns identical bytes.

Usage::

    python tests/probes/objective_console_preview.py --assets ABS_PATH
    python tests/probes/objective_console_preview.py --assets ABS_PATH --scenario truncated
    python tests/probes/objective_console_preview.py --assets ABS_PATH --smoke
    python tests/probes/objective_console_preview.py --check

Scenarios: ``normal`` (complete, writable-seeming), ``readonly`` (superseded
session), ``truncated`` (explicit row/span/event truncation), ``error``
(timeline reads fail with a 503 envelope while the list stays readable).

The asset directory is Host-supplied; the repository's current bundle lives at
``src/buddy/console_assets``. The new objective UI ships later.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import signal
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

PREVIEW_HEADER = "X-Buddy-Preview"
PREVIEW_VALUE = "synthetic-fixture-data"
SCENARIOS = ("normal", "readonly", "truncated", "error")
SYNTHETIC_CSRF = "synthetic-preview-csrf-not-a-credential"
SYNTHETIC_SESSION_ID = "synthetic-preview-session-not-a-credential"
HOST_A = "synthetic-host-alpha"
HOST_B = "synthetic-host-beta"
HOST_B2 = "synthetic-host-beta-2"

FIXTURE_DAY = datetime(2026, 9, 27, tzinfo=timezone.utc)
OBSERVED_AT = "2026-09-27T10:00:00.000Z"
HEAD = 21
UNTITLED = "未命名委派"
MAX_BODY_BYTES = 1024 * 1024
MAX_ASSET_BYTES = 8 * 1024 * 1024

OBJECTIVE_ID = re.compile(r"^(obj-[A-Za-z0-9-]{1,120}|run:[A-Za-z0-9._:-]{1,128})$")
RUN_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")
INTEGER = re.compile(r"^[0-9]{1,7}$")
CONSOLE_ID = re.compile(r"^[0-9a-f]{24}$")

OBJ_A = "obj-11111111-1111-4111-8111-111111111111"
OBJ_B = "obj-22222222-2222-4222-8222-222222222222"
STANDALONE = "preview-run-c1"
STANDALONE_GROUP = f"run:{STANDALONE}"

PROJECTS = {
    "alpha": {"id": "synthetic-preview-alpha", "path": "/synthetic-preview/hey-my-buddy",
              "label": "hey-my-buddy"},
    "beta": {"id": "synthetic-preview-beta", "path": "/synthetic-preview/dsh-harness",
             "label": "dsh-harness"},
}
CFG_FLASH = {"adapter": "dsh", "provider": "deepseek-official", "model": "deepseek-flash", "effort": "max"}
CFG_CODEX = {"adapter": "codex", "provider": "openai", "model": "gpt-5-codex", "effort": "high"}
CFG_GLM = {"adapter": "zcode", "provider": "zai", "model": "glm-5", "effort": "high"}
CFG_CLAUDE = {"adapter": "claude", "provider": "anthropic", "model": "claude-opus-5-5", "effort": "high"}
CONFIGS = (CFG_FLASH, CFG_CODEX, CFG_GLM, CFG_CLAUDE)

MARKER_LABELS = {"dispatch": "派发", "decide": "决定", "continue": "续接", "integrate": "整合",
                 "accept": "验收", "reject": "验收问题", "cancel": "取消", "takeover": "接管"}
MARKER_EVENTS = {"dispatch": "task.submitted", "decide": "workflow.request_approved",
                 "continue": "workflow.continued", "integrate": "workflow.integration_recorded",
                 "accept": "workflow.acknowledged", "reject": "workflow.acknowledged",
                 "cancel": "workflow.cancelled", "takeover": "workflow.takeover"}
MARKER_SUMMARY = {"dispatch": "synthetic dispatch · preview fixture",
                  "decide": "approve · synthetic Host decision for preview only",
                  "continue": "manual continuation · synthetic preview input",
                  "integrate": "merge · synthetic verified integration",
                  "accept": "accepted · synthetic preview acknowledgement",
                  "reject": "rejected · synthetic preview review problem",
                  "cancel": "cancelled · synthetic Host cancellation",
                  "takeover": "takeover · synthetic host handover"}

CONSOLE_OPERATIONS = (
    "evaluation_history", "selection_get", "selection_list", "model_profiles", "workflow_get",
    "evaluation_write_begin", "evaluation_write_renew", "user_policy_publish", "evaluation_write_abort",
    "model_catalog_refresh", "task_cancel", "task_retry", "task_acknowledge", "workflow_submit",
    "workflow_decide", "workflow_continue", "workflow_takeover", "workflow_cancel",
    "workflow_acknowledge", "workflow_scope_amend", "workflow_workspace_resolve",
    "workflow_integration_record", "workspace_cleanup_plan", "workspace_cleanup_apply", "workflow_suggest",
)
#: The exact read-only operation set of the real console (console_sessions.py).
#: A 403 CONSOLE_READ_ONLY would latch the browser session as read-only, so every
#: one of them must be answered instead of refused.
READ_OPERATIONS = ("workflow_get", "selection_get", "selection_list", "model_profiles",
                   "evaluation_history")

STATUS_BY_CODE = {"INVALID_ARGUMENT": 400, "METHOD_NOT_FOUND": 404, "NOT_FOUND": 404, "FORBIDDEN": 403,
                  "CONSOLE_READ_ONLY": 403, "UNSUPPORTED": 501, "SERVICE_UNAVAILABLE": 503,
                  "INTERNAL_ERROR": 500, "MESSAGE_TOO_LARGE": 413}

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
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


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


# --------------------------------------------------------------------------- #
# Fixture records (the payloads themselves, not a board simulation)            #
# --------------------------------------------------------------------------- #
def attempt(attempt_id: str, turn_index: int, state: str, confirmed: bool, started: str,
            finished: str | None, configuration: dict, *, error: str | None = None,
            result: str | None = None, disposition: str = "completed") -> dict:
    # `result` is the stored worker receipt: {"status", "result", "shutdownConfirmed"},
    # exactly what store.task_result unpacks; the adapter payload nests under `result`.
    receipt = None if result is None else {"status": "failed" if error else "ok",
                                           "result": {"finalText": result}, "shutdownConfirmed": confirmed}
    return {"attemptId": attempt_id, "turnId": f"turn-{attempt_id}", "turnIndex": turn_index,
            "generation": turn_index, "state": state, "shutdownConfirmed": confirmed,
            "startedAt": started, "finishedAt": finished, "configuration": configuration,
            "error": error, "result": receipt, "disposition": disposition,
            "uncertain": state == "uncertain" or (state == "finished" and not confirmed)}


def span(span_id: str, kind: str, start: str, end: str | None, state: str, *, attempt_id: str | None = None,
         request_id: str | None = None, configuration: dict | None = None,
         confirmed: bool | None = None, uncertain: bool = False, generation: int | None = None,
         disposition: str | None = None, error: str | None = None, request_kind: str | None = None,
         summary: str | None = None, decision_task: str | None = None,
         turn_index: int | None = None, result_status: str | None = None) -> dict:
    view = {"spanId": span_id, "kind": kind, "startAt": start, "endAt": end, "state": state,
            "attemptId": attempt_id, "turnId": f"turn-{attempt_id}" if attempt_id else None,
            "turnIndex": turn_index, "requestId": request_id, "configuration": configuration,
            "shutdownConfirmed": confirmed, "uncertain": uncertain,
            "clockSkew": bool(start and end and end < start)}
    extras = {"generation": generation, "disposition": disposition, "error": error,
              "requestKind": request_kind, "summary": summary, "decisionTaskId": decision_task}
    view.update({key: value for key, value in extras.items() if value is not None})
    if kind in ("execution", "routing") and state == "finished":
        view["resultStatus"] = result_status or ("failed" if error else "ok")
    return view


def marker(at: str, kind: str, *, actor: str | None = None, summary: str | None = None,
           **extra: Any) -> dict:
    return {"seq": 0, "runId": None, "kind": kind, "at": at, "label": MARKER_LABELS[kind],
            "summary": summary or MARKER_SUMMARY[kind], "actor": actor, "attemptId": None,
            "requestId": None, "artifactId": None, "integrationId": None, "continuationId": None,
            "eventKind": MARKER_EVENTS[kind], **extra}


ACTIVE_REQUEST = {
    "requestId": "req-a2-2", "kind": "assistance", "routing": False, "state": "open",
    "summary": "需要 Host 决定是否允许在合成预览中访问外部样例仓库（等待中）",
    "attempted": "合成预览请求已执行到需要 Host 决定的位置",
    "neededWork": ["由 Host 确认合成预览范围"], "expectedArtifacts": [],
    "acceptance": "合成预览：仅检查渲染分支，不产生真实验收", "childTaskId": None,
}
def routing(decision_id: str, task_id: str, status: str, selected: dict | None, reason: str,
            constraints: dict, configuration_revision: int | None) -> dict:
    """One recorded routing decision, shaped like ``workflow._routing_view``."""
    return {"status": status, "decisionId": decision_id, "taskId": task_id, "attemptId": f"att-{task_id}",
            "generation": 1, "tableRevision": 7 if decision_id else None,
            "configurationRevision": configuration_revision, "selectedProfile": selected, "reason": reason,
            "constraints": constraints, "routingPreferences": [],
            "source": "model-selection" if decision_id else None, "preferenceOutcome": None}


ROUTING_A1 = routing("preview-decision-a1", "preview-run-a1-router", "completed", CFG_FLASH,
                     "synthetic preview routing decision: dsh/deepseek-flash chosen", {"adapter": "dsh"}, 2)
ROUTING_A2 = routing("preview-decision-a2", "preview-run-a2-router", "needs-host", None,
                     "synthetic preview: routing needs Host configuration", {"adapter": "claude"}, 1)
ROUTING_A3 = routing("preview-decision-a3", "preview-run-a3-router", "completed", CFG_CLAUDE,
                     "synthetic preview routing decision: claude/claude-opus-5-5 chosen",
                     {"adapter": "claude"}, 2)


def run(run_id: str, *, project: str, title: str, title_source: str, task_text: str, created: str,
        updated: str, state: str, status: str, category: str, configuration: dict | None,
        hosts: tuple[str, str] = (HOST_A, HOST_A), parent: str | None = None, group: str | None = None,
        accepted: str | None = None, verdict: str | None = None, result: bool = False,
        result_summary: str | None = None, artifacts: list[dict] | None = None,
        integrations: list[dict] | None = None, routing: dict | None = None,
        active_request: dict | None = None, spans: list[dict] | None = None,
        markers: list[dict] | None = None, attempts: list[dict] | None = None,
        shutdown_confirmed: bool = True, governed: bool = True, adapter: str | None = None,
        continuation_count: int = 0, timeout_seconds: int = 0, revision: int = 3) -> dict:
    return {
        "runId": run_id, "parentRunId": parent, "group": group, "project": project, "title": title,
        "titleSource": title_source, "task": task_text, "createdAt": created, "updatedAt": updated,
        "state": state, "status": status, "category": category, "configuration": configuration,
        "sourceHostId": hosts[0], "currentHostId": hosts[1], "acceptedAt": accepted,
        "acceptanceVerdict": verdict, "resultAvailable": result, "resultSummary": result_summary,
        "artifacts": artifacts or [], "integrations": integrations or [], "routing": routing,
        "activeRequest": active_request, "spans": spans or [], "markers": markers or [],
        "attempts": attempts or [], "shutdownConfirmed": shutdown_confirmed, "governed": governed,
        "adapter": adapter, "continuationCount": continuation_count, "timeoutSeconds": timeout_seconds,
        "revision": revision,
        "kind": "decision" if not governed else ("helper" if parent is not None else "goal"),
    }


def artifact(artifact_id: str, attempt_id: str, commit: str, diff_path: str,
             paths: list[str]) -> dict:
    return {"artifactId": artifact_id, "kind": "output", "attemptId": attempt_id,
            "turnId": f"turn-{attempt_id}", "manifestSha256": digest(f"{artifact_id}-manifest"),
            "outputCommit": commit, "diffPath": diff_path, "diffSha256": digest(f"{artifact_id}-diff"),
            "changedPaths": paths}


def integration(integration_id: str, artifact_id: str, attempt_id: str, host: str) -> dict:
    return {"integrationId": integration_id, "artifactId": artifact_id, "attemptId": attempt_id,
            "state": "verified", "strategy": "cherry-pick",
            "target": {"kind": "git", "path": PROJECTS["alpha"]["path"],
                       "ref": "refs/heads/synthetic-preview", "repositoryId": digest("repo")[:40],
                       "checkoutId": digest("checkout")[:40]},
            "sourceCommit": digest("source")[:40], "sourceTree": digest("source-tree")[:40],
            "beforeCommit": digest("before")[:40], "afterCommit": digest("after")[:40],
            "beforeTree": digest("before-tree")[:40], "afterTree": digest("after-tree")[:40],
            "verification": {"verified": True, "notRequired": False,
                             "summary": "synthetic preview verification: fixture-only, no real repository"},
            "notRequired": False, "reason": "synthetic preview integration record", "actor": host,
            "createdAt": None}


def build_runs() -> dict[str, dict]:
    """The nine governed delegations and three internal routing rows."""
    runs = [
        run("preview-run-a1", project="alpha", group=OBJ_A,
            title=long_title("工作目标时间轴：预览服务器合成夹具与只读接口验证 —— 这是一个刻意加长的委派标题，"
                             "用来检查列表两行截断、时间轴标签单行省略、详情头部两行截断与 title 完整文本；"),
            title_source="title",
            task_text="设计并实现工作目标时间轴的只读预览夹具，核对列表、时间轴与详情在长标题下的截断。",
            created=instant(6, 0), updated=instant(7, 5), state="accepted", status="completed",
            category="ended", configuration=CFG_CODEX, accepted=instant(7, 5), verdict="accepted",
            result=True, result_summary="合成预览：工作目标时间轴夹具的第一轮交付摘要",
            spans=[
                span("queue:att-a1-1", "queue", instant(6, 0), instant(6, 2), "claimed",
                     attempt_id="att-a1-1"),
                span("routing:att-router-a1", "routing", instant(6, 2), instant(6, 5), "finished",
                     attempt_id="att-router-a1", confirmed=True, decision_task="preview-run-a1-router"),
                span("execution:att-a1-1", "execution", instant(6, 5), instant(6, 40), "finished",
                     attempt_id="att-a1-1", configuration=CFG_FLASH, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("host:req-a1-1", "host", instant(6, 40), instant(6, 44), "approved",
                     request_id="req-a1-1", request_kind="assistance",
                     summary="需要 Host 批准在隔离工作树外读取合成样例目录"),
                span("queue:att-a1-2", "queue", instant(6, 45), instant(6, 46), "claimed",
                     attempt_id="att-a1-2"),
                span("execution:att-a1-2", "execution", instant(6, 46), instant(7, 0), "finished",
                     attempt_id="att-a1-2", configuration=CFG_CODEX, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-a1-1", 1, "finished", True, instant(6, 5), instant(6, 40), CFG_FLASH,
                        result="合成预览：第一轮完成并确认停止。"),
                attempt("att-a1-2", 2, "finished", True, instant(6, 46), instant(7, 0), CFG_CODEX,
                        result="合成预览：第二轮完成并确认停止。"),
            ],
            markers=[
                marker(instant(6, 0), "dispatch", actor=HOST_A),
                marker(instant(6, 44), "decide", actor=HOST_A, request_id="req-a1-1", summary="approve"),
                marker(instant(6, 45), "continue", actor=HOST_A, continuation_id="cont-a1-1"),
                marker(instant(7, 3), "integrate", actor=HOST_A, artifact_id="art-a1",
                       integration_id="int-a1", summary="merge"),
                marker(instant(7, 5), "accept", actor=HOST_A, artifact_id="art-a1", summary="accepted"),
            ],
            artifacts=[artifact("art-a1", "att-a1-2", digest("a1-output")[:40], "/synthetic-preview/diffs/a1.diff",
                                ["tests/probes/objective_console_preview.py"])],
            integrations=[integration("int-a1", "art-a1", "att-a1-2", HOST_A)],
            routing=ROUTING_A1),
        run("preview-run-a1-h1", project="alpha", group=OBJ_A, parent="preview-run-a1",
            title="补充 schema 升级离线副本的验证测试", title_source="title",
            task_text="为 board_prepare 的离线副本补充验证测试，覆盖行数不变与来源文件哈希不变。",
            created=instant(6, 20), updated=instant(6, 55), state="delivered", status="completed",
            category="review", configuration=CFG_GLM, result=True,
            result_summary="补充 schema 升级离线副本的验证测试",
            spans=[
                span("queue:att-h1-1", "queue", instant(6, 20), instant(6, 24), "claimed",
                     attempt_id="att-h1-1"),
                span("execution:att-h1-1", "execution", instant(6, 24), instant(6, 36), "finished",
                     attempt_id="att-h1-1", configuration=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1,
                     error="synthetic preview failure: test command timed out"),
                span("queue:att-h1-2", "queue", instant(6, 36), instant(6, 38), "claimed",
                     attempt_id="att-h1-2"),
                span("execution:att-h1-2", "execution", instant(6, 38), instant(6, 55), "finished",
                     attempt_id="att-h1-2", configuration=CFG_GLM, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-h1-1", 1, "finished", True, instant(6, 24), instant(6, 36), CFG_GLM,
                        error="synthetic preview failure: test command timed out",
                        result="synthetic preview failure: test command timed out"),
                attempt("att-h1-2", 2, "finished", True, instant(6, 38), instant(6, 55), CFG_GLM,
                        result="合成预览：重试后通过。"),
            ],
            markers=[marker(instant(6, 20), "dispatch", actor=HOST_A)],
            artifacts=[artifact("art-a1h", "att-h1-2", digest("a1h-output")[:40],
                                "/synthetic-preview/diffs/a1h.diff", ["tests/python/test_board_prepare.py"])]),
        run("preview-run-a1-h1-h1", project="alpha", group=OBJ_A, parent="preview-run-a1-h1",
            title="核对离线副本的跨平台路径边界", title_source="task",
            task_text="核对 Windows 与 macOS 下离线副本路径大小写与分隔符的边界记录。",
            created=instant(6, 56), updated=instant(7, 2), state="executing", status="reconciliation-needed",
            category="active", configuration=CFG_CLAUDE, result=True, shutdown_confirmed=False,
            result_summary="核对离线副本的跨平台路径边界",
            spans=[
                span("queue:att-h2-1", "queue", instant(6, 56), instant(6, 57), "claimed",
                     attempt_id="att-h2-1"),
                span("execution:att-h2-1", "execution", instant(6, 57), instant(7, 2), "finished",
                     attempt_id="att-h2-1", configuration=CFG_CLAUDE, confirmed=False, uncertain=True,
                     generation=1, disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-h2-1", 1, "finished", False, instant(6, 57), instant(7, 2), CFG_CLAUDE,
                              result="合成预览：结果已收到，但停止未确认。")],
            markers=[marker(instant(6, 56), "dispatch", actor=HOST_A)]),
        run("preview-run-a2", project="alpha", group=OBJ_A,
            title="核对 Host 等待片段与决定标记的渲染", title_source="title",
            task_text="核对等待 Host 的开放片段、决定标记与协助请求在时间轴和详情中的呈现。",
            created=instant(8, 30), updated=instant(9, 20), state="awaiting-host", status="completed",
            category="host", configuration=CFG_CLAUDE, result=True, continuation_count=1,
            result_summary="核对 Host 等待片段与决定标记的渲染", routing=ROUTING_A2,
            active_request=ACTIVE_REQUEST,
            spans=[
                span("queue:att-a2-1", "queue", instant(8, 30), instant(8, 33), "claimed",
                     attempt_id="att-a2-1"),
                span("routing:att-router-a2", "routing", instant(8, 33), instant(8, 36), "finished",
                     attempt_id="att-router-a2", confirmed=True, decision_task="preview-run-a2-router"),
                span("execution:att-a2-1", "execution", instant(8, 36), instant(8, 55), "finished",
                     attempt_id="att-a2-1", configuration=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("host:req-a2-1", "host", instant(8, 55), instant(9, 0), "declined",
                     request_id="req-a2-1", request_kind="routing",
                     summary="合成预览：路由需要 Host 决定，已按预览策略拒绝"),
                span("queue:att-a2-2", "queue", instant(9, 1), instant(9, 2), "claimed",
                     attempt_id="att-a2-2"),
                span("execution:att-a2-2", "execution", instant(9, 2), instant(9, 20), "finished",
                     attempt_id="att-a2-2", configuration=CFG_CLAUDE, confirmed=True, generation=2,
                     disposition="assistance", turn_index=2),
                span("host:req-a2-2", "host", instant(9, 20), None, "open", request_id="req-a2-2",
                     request_kind="assistance",
                     summary="需要 Host 决定是否允许在合成预览中访问外部样例仓库（等待中）"),
            ],
            attempts=[
                attempt("att-a2-1", 1, "finished", True, instant(8, 36), instant(8, 55), CFG_GLM,
                        result="合成预览：第一轮结束，交给 Host 决定。"),
                attempt("att-a2-2", 2, "finished", True, instant(9, 2), instant(9, 20), CFG_CLAUDE,
                        result="合成预览：第二轮结束，需要 Host 协助。"),
            ],
            markers=[
                marker(instant(8, 30), "dispatch", actor=HOST_A),
                marker(instant(9, 0), "decide", actor=HOST_A, request_id="req-a2-1", summary="decline"),
                marker(instant(9, 1), "continue", actor=HOST_A, continuation_id="cont-a2-1"),
            ]),
        run("preview-run-a3", project="alpha", group=OBJ_A,
            title="实现折叠区间的键盘焦点顺序", title_source="title",
            task_text="实现折叠空闲区间展开后的键盘焦点顺序，并补充可访问性检查。",
            created=instant(9, 35), updated=instant(9, 39), state="executing", status="running",
            category="active", configuration=CFG_CLAUDE, shutdown_confirmed=False, routing=ROUTING_A3,
            spans=[
                span("queue:att-a3-1", "queue", instant(9, 35), instant(9, 36), "claimed",
                     attempt_id="att-a3-1"),
                span("routing:att-router-a3", "routing", instant(9, 36), instant(9, 39), "finished",
                     attempt_id="att-router-a3", confirmed=True, decision_task="preview-run-a3-router"),
                span("execution:att-a3-1", "execution", instant(9, 39), None, "executing",
                     attempt_id="att-a3-1", configuration=CFG_CLAUDE, confirmed=False, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-a3-1", 1, "executing", False, instant(9, 39), None, CFG_CLAUDE)],
            markers=[marker(instant(9, 35), "dispatch", actor=HOST_A)]),
        run("preview-run-b1", project="beta", group=OBJ_B, hosts=(HOST_B, HOST_B2),
            title=long_title("DSH 插件在 Windows 路径下的沙箱回归排查与修复记录，包含长标题截断检查："),
            title_source="title",
            task_text="排查 DSH 插件在 Windows 路径下的沙箱回归，修复后等待最终验收。",
            created=instant(3, 0), updated=instant(5, 35), state="delivered", status="completed",
            category="review", configuration=CFG_GLM, result=True, continuation_count=1,
            result_summary="合成预览：沙箱回归修复完成，等待验收",
            spans=[
                span("queue:att-b1-1", "queue", instant(3, 0), instant(3, 4), "claimed",
                     attempt_id="att-b1-1"),
                span("execution:att-b1-1", "execution", instant(3, 4), instant(3, 30), "finished",
                     attempt_id="att-b1-1", configuration=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
                span("queue:att-b1-2", "queue", instant(5, 1), instant(5, 2), "claimed",
                     attempt_id="att-b1-2"),
                span("execution:att-b1-2", "execution", instant(5, 2), instant(5, 30), "finished",
                     attempt_id="att-b1-2", configuration=CFG_GLM, confirmed=True, generation=2,
                     disposition="completed", turn_index=2),
            ],
            attempts=[
                attempt("att-b1-1", 1, "finished", True, instant(3, 4), instant(3, 30), CFG_GLM,
                        result="合成预览：第一轮探测完成。"),
                attempt("att-b1-2", 2, "finished", True, instant(5, 2), instant(5, 30), CFG_GLM,
                        result="合成预览：修复完成，等待验收。"),
            ],
            markers=[
                marker(instant(3, 0), "dispatch", actor=HOST_B),
                marker(instant(4, 1), "takeover", actor=HOST_B2),
                marker(instant(5, 1), "continue", actor=HOST_B2, continuation_id="cont-b1-1"),
                marker(instant(5, 35), "integrate", actor=HOST_B2, artifact_id="art-b1",
                       integration_id="int-b1", summary="merge"),
            ],
            artifacts=[artifact("art-b1", "att-b1-2", digest("b1-output")[:40], "/synthetic-preview/diffs/b1.diff",
                                ["harnesses/dsh/plugins/sandbox.js"])],
            integrations=[integration("int-b1", "art-b1", "att-b1-2", HOST_B2)]),
        run("preview-run-b1-h1", project="beta", group=OBJ_B, parent="preview-run-b1",
            hosts=(HOST_B, HOST_B),
            title="整理 macOS 与 Windows 沙箱规则差异清单", title_source="title",
            task_text="整理两个平台的沙箱规则差异清单。",
            created=instant(3, 10), updated=instant(3, 25), state="cancelled", status="cancelled",
            category="ended", configuration=CFG_GLM, result=True,
            result_summary="整理 macOS 与 Windows 沙箱规则差异清单",
            spans=[
                span("queue:att-b1h-1", "queue", instant(3, 10), instant(3, 11), "claimed",
                     attempt_id="att-b1h-1"),
                span("execution:att-b1h-1", "execution", instant(3, 11), instant(3, 25), "finished",
                     attempt_id="att-b1h-1", configuration=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1,
                     error="synthetic preview cancellation: Host cancelled the helper", result_status="cancelled"),
            ],
            attempts=[attempt("att-b1h-1", 1, "finished", True, instant(3, 11), instant(3, 25), CFG_GLM,
                              error="synthetic preview cancellation: Host cancelled the helper",
                              result="synthetic preview cancellation: Host cancelled the helper")],
            markers=[marker(instant(3, 10), "dispatch", actor=HOST_B),
                     marker(instant(3, 25), "cancel", actor=HOST_B, summary="cancelled")]),
        run(STANDALONE, project="beta", hosts=(HOST_B, HOST_B2),
            title="修复标题回退在 CRLF 输入下的显示", title_source="task",
            task_text="修复标题回退在 CRLF 输入下的显示，并补充回归测试。",
            created=instant(5, 0), updated=instant(5, 35), state="accepted", status="completed",
            category="ended", configuration=CFG_FLASH, accepted=instant(5, 35), verdict="accepted",
            result=True, result_summary="修复标题回退在 CRLF 输入下的显示",
            spans=[
                span("queue:att-c1-1", "queue", instant(5, 0), instant(5, 4), "claimed",
                     attempt_id="att-c1-1"),
                span("execution:att-c1-1", "execution", instant(5, 4), instant(5, 20), "finished",
                     attempt_id="att-c1-1", configuration=CFG_FLASH, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-c1-1", 1, "finished", True, instant(5, 4), instant(5, 20), CFG_FLASH,
                              result="合成预览：CRLF 标题回退修复完成。")],
            markers=[marker(instant(5, 0), "dispatch", actor=HOST_B),
                     marker(instant(5, 30), "takeover", actor=HOST_B2, summary="takeover"),
                     marker(instant(5, 35), "accept", actor=HOST_B2, artifact_id="art-c1", summary="accepted")],
            artifacts=[artifact("art-c1", "att-c1-1", digest("c1-output")[:40], "/synthetic-preview/diffs/c1.diff",
                                ["apps/console/src/task-title.test.tsx"])],
            integrations=[integration("int-c1", "art-c1", "att-c1-1", HOST_B2)]),
        run("preview-run-c1-h1", project="beta", group=None, parent=STANDALONE, hosts=(HOST_B, HOST_B),
            title=UNTITLED, title_source="none", task_text="   ",
            created=instant(5, 6), updated=instant(5, 25), state="delivered", status="completed",
            category="review", configuration=CFG_GLM, verdict="rejected", result=True,
            result_summary="合成预览：交付被记录为验收问题。",
            spans=[
                span("queue:att-c1h-1", "queue", instant(5, 6), instant(5, 8), "claimed",
                     attempt_id="att-c1h-1"),
                span("execution:att-c1h-1", "execution", instant(5, 8), instant(5, 18), "finished",
                     attempt_id="att-c1h-1", configuration=CFG_GLM, confirmed=True, generation=1,
                     disposition="completed", turn_index=1),
            ],
            attempts=[attempt("att-c1h-1", 1, "finished", True, instant(5, 8), instant(5, 18), CFG_GLM,
                              result="合成预览：交付被记录为验收问题。")],
            markers=[marker(instant(5, 6), "dispatch", actor=HOST_B),
                     marker(instant(5, 25), "reject", actor=HOST_B, artifact_id="art-c1h", summary="rejected")],
            artifacts=[artifact("art-c1h", "att-c1h-1", digest("c1h-output")[:40],
                                "/synthetic-preview/diffs/c1h.diff", ["apps/console/src/task-title.ts"])]),
    ]
    # Internal routing rows: real task history, never objective rows.
    for router, owner, decision, label in (
            ("preview-run-a1-router", "preview-run-a1", "preview-decision-a1", "dsh/deepseek-flash"),
            ("preview-run-a2-router", "preview-run-a2", "preview-decision-a2", "Host boundary"),
            ("preview-run-a3-router", "preview-run-a3", "preview-decision-a3", "claude/claude-opus-5-5")):
        runs.append(run(router, project="alpha", parent=owner, title=f"内部路由计算：选择 {label}",
                        title_source="task", task_text="为合成预览夹具选择执行配置。",
                        created=instant(0, 0), updated=instant(0, 0), state="accepted",
                        status="completed", category="ended", configuration=None, governed=False,
                        adapter="decision", revision=1))
        runs[-1]["decisionId"] = decision
        runs[-1]["requestId"] = f"request-{router}"
    by_id = {entry["runId"]: entry for entry in runs}
    for entry in runs:
        root = entry["runId"]
        while by_id[root]["parentRunId"] is not None:
            root = by_id[root]["parentRunId"]
        entry["rootRunId"] = root
        entry["requestId"] = entry.get("requestId") or f"request-{entry['runId']}"
        entry["cwd"] = f"/synthetic-preview/checkouts/{entry['runId']}"
        project = PROJECTS[entry["project"]]
        entry["delegation"] = {
            "kind": entry["kind"], "sourceHostId": entry["sourceHostId"],
            "currentHostId": entry["currentHostId"], "parentRunId": entry["parentRunId"],
            "rootRunId": root,
            "project": {"id": project["id"], "path": project["path"], "label": project["label"]},
            "configuration": entry["configuration"] or (
                {"adapter": "decision", "provider": "decision", "model": "decision", "effort": "default"}
                if entry["adapter"] == "decision" else None),
        }
        for item in entry["artifacts"]:
            item["sourceTaskId"] = entry["runId"]
            item["createdAt"] = entry["updatedAt"]
        for item in entry["integrations"]:
            item["runId"] = entry["runId"]
            item["createdAt"] = entry["updatedAt"]
        for item in entry["spans"]:
            item["runId"] = entry["runId"]
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
        if not entry["governed"]:
            continue
        group = entry["group"] or (f"run:{by_id[entry['parentRunId']]['rootRunId']}"
                                   if entry["parentRunId"] else f"run:{entry['runId']}")
        groups.setdefault(group, []).append(entry["runId"])
    group_activity = {group: max((activity[run_id] for run_id in members), key=lambda item: item[0])
                      for group, members in groups.items()}
    return {"runs": by_id, "order": order, "groups": groups, "activity": activity,
            "group_activity": group_activity, "head": len(sequence), "markers": sequence}


FIXTURES = build_runs()
OBJECTIVES = {
    OBJ_A: {"title": long_title("工作目标时间轴：设计、接口与合成预览夹具的实现与核对（含超长标题与折叠区间）："),
            "project": "alpha", "host": HOST_A, "createdAt": instant(6, 0)},
    OBJ_B: {"title": "0.13 控制台入口候选版收尾与文档", "project": "beta", "host": HOST_B,
            "createdAt": instant(3, 0)},
}


# --------------------------------------------------------------------------- #
# Thin projections: fixture record -> console DTO                              #
# --------------------------------------------------------------------------- #
def shutdown_view(entry: dict) -> dict:
    """Recorded stop evidence over the governed helper lineage.

    Routing rows are internal decision records with no attempt of their own in
    this fixture, so only governed helpers are aggregated.
    """
    unconfirmed: list[str] = []
    stack = [entry]
    while stack:
        current = stack.pop()
        if not current["shutdownConfirmed"]:
            unconfirmed.append(current["runId"])
        stack.extend(child for child in FIXTURES["runs"].values()
                     if child["governed"] and child["parentRunId"] == current["runId"])
    own = entry["shutdownConfirmed"]
    return {"selfConfirmed": own, "descendantsConfirmed": unconfirmed == ([] if own else [entry["runId"]]),
            "unconfirmedRunIds": unconfirmed[:32], "unconfirmedCount": len(unconfirmed), "truncated": False}


def attempt_view(entry: dict, item: dict) -> dict:
    configuration = item["configuration"]
    cancelled = entry["status"] == "cancelled"
    return {
        "attemptId": item["attemptId"], "taskId": entry["runId"], "generation": item["generation"],
        "workerId": "synthetic-worker-1", "workerIdentity": "synthetic preview worker (fixture)",
        "workerInstance": "synthetic-instance-1", "adapter": entry["adapter"] or configuration["adapter"],
        "executionState": item["state"], "ownership": "owned",
        "modelFamily": {key: configuration[key] for key in ("adapter", "provider", "model")},
        "runtimeIdentity": None, "leaseExpiresAt": None, "leaseSeconds": 900, "logPaths": {},
        "resultAvailable": item["result"] is not None, "shutdownConfirmed": item["shutdownConfirmed"],
        "cancelRequested": cancelled, "cancelRequestedAt": entry["updatedAt"] if cancelled else None,
        "exitCode": 0 if item["state"] == "finished" else None, "signal": None, "error": item["error"],
        "startedAt": item["startedAt"], "finishedAt": item["finishedAt"], "createdAt": item["startedAt"],
        "updatedAt": item["finishedAt"] or item["startedAt"], "revision": 1,
        **({"result": item["result"]} if item["result"] is not None else {}),
    }


def task_view(entry: dict) -> dict:
    selected = entry["attempts"][-1] if entry["attempts"] else None
    receipt = selected["result"] if selected is not None and entry["resultAvailable"] else None
    adapter = entry["adapter"] or (entry["configuration"] or {}).get("adapter", "dsh")
    spec: dict[str, Any] = {"task": entry["task"], "adapter": adapter, "cwd": entry["cwd"],
                            "workspace": True, "timeoutSeconds": entry["timeoutSeconds"]}
    if entry["group"]:
        spec["objectiveId"] = entry["group"]
    if entry["adapter"] == "decision" and entry.get("decisionId"):
        spec["decision"] = {"decisionId": entry["decisionId"]}
    view: dict[str, Any] = {
        "runId": entry["runId"], "taskId": entry["runId"], "requestId": entry["requestId"],
        "owner": "synthetic-worker-1", "adapter": adapter, "cwd": entry["cwd"], "task": entry["task"],
        "spec": spec, "inputFingerprint": digest(f"{entry['runId']}-fingerprint"), "fingerprintVersion": 3,
        "requiredCapabilities": [], "exclusiveResources": [], "timeoutSeconds": entry["timeoutSeconds"],
        "status": entry["status"], "state": entry["status"], "queueReason": None,
        "revision": entry["revision"],
        "selectedAttemptId": selected["attemptId"] if selected else None,
        "activeAttemptId": (selected["attemptId"] if selected and selected["state"] == "executing" else None),
        "createdAt": entry["createdAt"], "updatedAt": entry["updatedAt"], "acceptedAt": entry["acceptedAt"],
        "acceptanceNote": None, "acceptanceVerdict": entry["acceptanceVerdict"],
        "resultAvailable": receipt is not None, "shutdownConfirmed": entry["shutdownConfirmed"],
        "activity": None, "delegation": entry["delegation"], "artifacts": entry["artifacts"],
        "artifactCount": len(entry["artifacts"]), "inquiries": {},
    }
    if entry["governed"]:
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
        view["cancelRequested"] = entry["status"] == "cancelled"
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
                 "objectiveId": entry["group"]}
    if entry["activeRequest"]:
        extension["requestKind"] = entry["activeRequest"]["kind"]
        extension["requestSummary"] = entry["activeRequest"]["summary"]
        extension["requestRouting"] = entry["activeRequest"]["routing"]
    return extension


def wait_reason(entry: dict) -> str:
    if entry["state"] == "awaiting-host":
        return ("the current turn requested Host assistance"
                if entry["activeRequest"] and entry["activeRequest"]["kind"] == "assistance"
                else "the current turn concluded and the Host decides the next turn")
    if entry["state"] == "accepted":
        return "the final artifact was acknowledged"
    if entry["state"] == "cancelled":
        return "the run was cancelled"
    return "a turn is executing" if entry["status"] == "running" else "the run is queued for its next turn"


def turn_views(entry: dict) -> list[dict]:
    """Turn index, newest first, like ``workflow.compact``."""
    views = []
    for item in reversed(entry["attempts"]):
        views.append({
            "turnId": item["turnId"], "turnIndex": item["turnIndex"], "attemptId": item["attemptId"],
            "generation": item["generation"], "resumeMode": "reconstructed-new-session",
            "sessionId": None, "state": "concluded" if item["state"] == "finished" else "open",
            "disposition": item["disposition"], "createdAt": item["startedAt"],
            "updatedAt": item["finishedAt"] or item["startedAt"],
            "executionConfiguration": item["configuration"],
            "routing": ({"decisionId": entry["routing"]["decisionId"], "executionConfigurationRevision": 2}
                        if entry["routing"] and entry["routing"]["decisionId"] and item["turnIndex"] == 1
                        else None),
            "summary": ((item["result"] or {}).get("result") or {}).get("finalText", ""),
            "summaryTruncated": False, "remaining": [],
        })
    return views


def workflow_view(entry: dict) -> dict:
    """The governed view consumed by ``TaskDetails``/``WorkflowPanel``."""
    turns = turn_views(entry)
    children = [{"taskId": child["runId"], "role": "helper", "state": child["state"],
                 "integrator": False, "autoContinue": True, "requestId": child["requestId"]}
                for child in FIXTURES["runs"].values()
                if child["governed"] and child["parentRunId"] == entry["runId"]]
    routing = entry["routing"] or {"status": "explicit" if entry["configuration"] else "needs-host",
                                   "decisionId": None, "taskId": None, "constraints": {},
                                   "routingPreferences": [], "source": None}
    return {
        "governed": True, "runId": entry["runId"], "taskId": entry["runId"],
        "requestId": entry["requestId"], "title": entry["title"], "objectiveId": entry["group"],
        "hostId": entry["currentHostId"], "ownerGeneration": 1, "revision": entry["revision"],
        "state": entry["state"], "status": entry["status"], "queueReason": None,
        "awaitingHost": entry["state"] == "awaiting-host", "waitReason": wait_reason(entry),
        "continuationCount": entry["continuationCount"],
        "goal": {"task": entry["task"], "taskTruncated": False,
                 "adapter": entry["adapter"] or (entry["configuration"] or {}).get("adapter", "dsh"),
                 "cwd": entry["cwd"], "fingerprint": digest(f"{entry['runId']}-goal")},
        "executionConfiguration": entry["configuration"],
        "executionConfigurationRevision": 2 if entry["configuration"] else None,
        "routing": routing, "turns": turns, "currentTurn": turns[0] if turns else None,
        "activeRequest": entry["activeRequest"], "counts": {"turns": len(turns),
                                                            "openRequests": 1 if entry["activeRequest"] else 0,
                                                            "children": len(children),
                                                            "artifacts": len(entry["artifacts"])},
        "children": children, "artifacts": entry["artifacts"], "integrations": entry["integrations"],
        "shutdown": shutdown_view(entry),
        "truncated": {"turns": 0, "requests": 0, "children": 0, "artifacts": 0, "pendingRequests": 0},
        "workspace": {"workspaceId": f"ws-{entry['runId']}", "path": entry["cwd"], "kind": "worktree",
                      "access": "write", "inputCommit": digest(f"{entry['runId']}-input")[:40],
                      "manifestSha256": digest(f"{entry['runId']}-workspace")},
        "createdAt": entry["createdAt"], "updatedAt": entry["updatedAt"],
        "finalArtifactId": entry["artifacts"][-1]["artifactId"] if entry["artifacts"] else None,
        "finalAttemptId": entry["attempts"][-1]["attemptId"] if entry["attempts"] else None,
        "task": task_view(entry),
    }


def timeline_row(entry: dict, depth: int) -> dict:
    attempts = entry["attempts"]
    confirmed = sum(1 for item in attempts if item["state"] == "finished" and item["shutdownConfirmed"])
    configuration = entry["configuration"]
    return {
        "runId": entry["runId"], "parentRunId": entry["parentRunId"], "rootRunId": entry["rootRunId"],
        "title": entry["title"] or UNTITLED, "titleSource": entry["titleSource"],
        "summary": "合成记录：已提交输出，等待按记录核对。" if entry["status"] == "completed" else None,
        "createdAt": entry["createdAt"], "state": entry["state"], "status": entry["status"],
        "category": entry["category"], "shutdownConfirmed": len(attempts) == confirmed, "depth": depth,
        "kind": entry["kind"],
        "configuration": ({key: configuration.get(key) for key in ("adapter", "provider", "model", "effort")}
                          if configuration else None),
        "acceptedAt": entry["acceptedAt"], "acceptanceVerdict": entry["acceptanceVerdict"],
    }


def objective_summary(group: str, matching: set[str]) -> dict:
    entries = [FIXTURES["runs"][run_id] for run_id in FIXTURES["groups"][group]]
    counts = {"roots": 0, "helpers": 0, "active": 0, "host": 0, "review": 0, "ended": 0}
    for entry in entries:
        counts[entry["category"]] += 1
        counts["helpers" if entry["parentRunId"] else "roots"] += 1
    state = next((name for name in ("host", "active", "review") if counts[name]), "ended")
    if group.startswith("obj-"):
        meta = OBJECTIVES[group]
        title, title_source, project = meta["title"], "objective", meta["project"]
        source_host, created, kind = meta["host"], meta["createdAt"], "objective"
        roots = [entry["runId"] for entry in entries if entry["parentRunId"] is None]
    else:
        root = FIXTURES["runs"][group[len("run:"):]]
        title, title_source, project = root["title"], root["titleSource"], root["project"]
        source_host, created, kind, roots = root["sourceHostId"], root["createdAt"], "standalone", [root["runId"]]
    seq, at = FIXTURES["group_activity"][group]
    return {
        "objectiveId": group, "kind": kind, "title": title or UNTITLED, "titleSource": title_source,
        "summary": "合成记录：保留独立委派的结果摘要。" if kind == "standalone" else None,
        "project": PROJECTS[project], "sourceHostId": source_host,
        "currentHostIds": sorted({entry["currentHostId"] for entry in entries}),
        "createdAt": created, "lastActivityAt": at, "lastActivitySeq": seq, "state": state,
        "counts": counts, "matchingRuns": len(matching), "rootRunIds": roots,
    }


def span_views(entry: dict) -> list[dict]:
    return [{**item, "runId": entry["runId"]} for item in entry["spans"]]


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
    if applied.get("projectId") and PROJECTS[entry["project"]]["id"] != applied["projectId"]:
        return False
    if applied.get("hostId") and applied["hostId"] not in (entry["sourceHostId"], entry["currentHostId"]):
        return False
    if applied.get("query"):
        configuration = entry["configuration"] or {}
        haystack = " ".join((entry["runId"], entry["title"], PROJECTS[entry["project"]]["path"],
                             entry["sourceHostId"], entry["currentHostId"],
                             configuration.get("adapter", ""), configuration.get("model", ""))).casefold()
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


def objective_page(params: dict) -> dict:
    limit = parse_limit(params, 50, 100)
    applied = applied_filters(params)
    rows = [(FIXTURES["group_activity"][group][0], group)
            for group in FIXTURES["groups"] if group_matching(group, applied)]
    rows.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return {
        "objectives": [objective_summary(group, group_matching(group, applied))
                       for _seq, group in rows[:limit]],
        "total": len(rows), "nextCursor": None, "cursor": FIXTURES["head"], "changed": False,
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


def objective_timeline(params: dict, scenario: str) -> dict:
    group = params.get("objectiveId", "")
    if not OBJECTIVE_ID.match(group) or group not in FIXTURES["groups"]:
        raise PreviewError("NOT_FOUND", "Unknown objectiveId")
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
        "objective": objective_summary(group, matching), "observedAt": OBSERVED_AT,
        "cursor": FIXTURES["head"], "rows": [timeline_row(entry, depth) for entry, depth in shown],
        "spans": spans, "events": markers,
        "totals": {"rows": len(selected), "spans": span_total, "events": marker_total,
                   "allRows": len(entries)},
        "truncated": truncated, "filtered": bool(applied),
        "scopeComplete": not applied and not any(truncated.values()),
    }


def task_page(params: dict) -> dict:
    if params.get("before"):
        raise PreviewError("UNSUPPORTED", "The synthetic preview serves one task page; omit before")
    limit = parse_limit(params, 20, 100)
    applied = applied_filters(params)
    entries = [entry for entry in FIXTURES["runs"].values() if matches(entry, applied)]
    if params.get("state"):
        entries = [entry for entry in entries if entry["status"] == params["state"]]
    if params.get("adapter"):
        entries = [entry for entry in entries
                   if (entry["adapter"] or (entry["configuration"] or {}).get("adapter")) == params["adapter"]]
    if (params.get("rootsOnly") or "false").lower() in ("true", "1"):
        entries = [entry for entry in entries if entry["parentRunId"] is None]
    entries.sort(key=lambda entry: (entry["createdAt"], entry["runId"]), reverse=True)
    views = [task_view(entry) for entry in entries[:limit]]
    return {"runs": views, "tasks": views, "total": len(entries), "cursor": FIXTURES["head"],
            "nextCursor": None}


def profile_views() -> list[dict]:
    return [{"profileId": f"{cfg['adapter']}:{cfg['provider']}:{cfg['model']}:{cfg['effort']}",
             "label": f"{cfg['model']} · {cfg['effort']} (synthetic preview)",
             "adapter": cfg["adapter"], "provider": cfg["provider"], "model": cfg["model"],
             "effort": cfg["effort"], "available": True, "enabled": True, "capabilities": ["execution"],
             "contextWindow": 200000, "description": "synthetic preview profile",
             "source": "synthetic-preview", "catalogState": "ready", "catalogReason": None}
            for cfg in CONFIGS]


def concurrency_rows() -> list[dict]:
    return [{key: cfg[key] for key in ("adapter", "provider", "model")}
            | {"limit": 2, "active": 1 if cfg["adapter"] == "claude" else 0} for cfg in CONFIGS]


def console_snapshot(scenario: str, assets_ready: bool) -> dict:
    profiles = profile_views()
    entries = sorted(FIXTURES["runs"].values(),
                     key=lambda entry: (entry["createdAt"], entry["runId"]), reverse=True)
    tasks = [task_view(entry) for entry in entries]
    writable = scenario != "readonly"
    return {
        "csrfToken": SYNTHETIC_CSRF,
        "consoleSession": {"id": SYNTHETIC_SESSION_ID, "canWrite": writable,
                           "reason": None if writable else "superseded"},
        "tableRevision": 7,
        "gate": {"phase": "open", "readers": 0, "waitingWriters": 0, "writer": None},
        "configuration": {"revision": 1, "decisionProfileId": profiles[0]["profileId"]},
        "profiles": profiles,
        "modelConcurrency": concurrency_rows(),
        "unavailableProfileCount": 0,
        "preferences": [], "annotations": [], "cards": [], "evidence": [], "decisions": [],
        "routingHealth": {"windowSize": 20, "sampleCount": 5, "failureCount": 3,
                          "consecutiveFailures": 3, "abstentionCount": 1, "cancelledCount": 0, "staleCount": 0,
                          "lastSuccessAt": instant(11, 15), "lastSuccessDecisionId": "synthetic-success",
                          "recentFailures": [{"decisionId": "synthetic-routing-refusal", "runId": "preview-run-a1",
                                              "at": instant(14, 5), "code": "policy-check-shape"}]},
        "pendingEvidence": 0, "sampleCounts": {profile["profileId"]: 0 for profile in profiles},
        "tasks": {"runs": tasks, "total": len(tasks), "nextCursor": None},
        "capabilities": {"selection": True, "maintenance": False, "maintenanceMode": "harness-owned",
                         "decisionAdapter": True, "decisionAdapterReason": "synthetic preview only",
                         "evaluationWriteGate": True, "readerAdmission": True, "evidenceRecord": True,
                         "modelCatalogDiscovery": False, "taskControl": False,
                         "consoleAssets": assets_ready, "previewSynthetic": True},
    }


def decision_view(router: dict) -> dict:
    """One compact decision record, built from the router row and its owner's routing."""
    routing = FIXTURES["runs"][router["parentRunId"]]["routing"]
    return {"decisionId": router["decisionId"], "requestId": router["requestId"], "kind": "select",
            "status": routing["status"], "task": router["task"], "runId": router["runId"],
            "profileId": None, "selectedProfile": routing["selectedProfile"],
            "decisionModel": {"requested": None, "resolved": None, "observed": None},
            "tableRevision": routing["tableRevision"] or 7, "expectedRevision": 7,
            "publishedRevision": None, "noOp": False, "reason": routing["reason"], "evidenceIds": [],
            "createdAt": router["createdAt"], "updatedAt": router["updatedAt"],
            "pendingEvidenceRemaining": 0}


def selection_get(params: dict) -> dict:
    unknown = sorted(set(params) - {"decisionId", "includeAudit"})
    if unknown:
        raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    decision_id = params.get("decisionId")
    if not isinstance(decision_id, str) or not decision_id:
        raise PreviewError("INVALID_ARGUMENT", "decisionId is required")
    router = next((item for item in FIXTURES["runs"].values()
                   if item["kind"] == "decision" and item.get("decisionId") == decision_id), None)
    if router is None:
        raise PreviewError("NOT_FOUND", "Unknown decisionId")
    view = decision_view(router)
    if params.get("includeAudit"):
        routing = FIXTURES["runs"][router["parentRunId"]]["routing"]
        view.update({"attemptId": None, "generation": 1,
                     "configurationRevision": routing["configurationRevision"], "readerId": None,
                     "writerId": None, "writerGeneration": None, "autoPublish": False,
                     "requested": {"constraints": routing["constraints"]}, "input": None,
                     "inputSha256": None, "output": None, "proposal": None,
                     "requestedProfile": routing["selectedProfile"]})
    return {"decision": view}


def workflow_get(params: dict) -> dict:
    unknown = sorted(set(params) - {"runId", "includeAudit", "routingHistory"})
    if unknown:
        raise PreviewError("INVALID_ARGUMENT", f"Unknown parameter: {unknown[0]}")
    run_id = params.get("runId")
    if not isinstance(run_id, str) or not run_id:
        raise PreviewError("INVALID_ARGUMENT", "runId is required")
    if params.get("includeAudit"):
        raise PreviewError("UNSUPPORTED", "The synthetic preview serves the compact view only")
    entry = FIXTURES["runs"].get(run_id)
    if entry is None:
        raise PreviewError("NOT_FOUND", "Unknown runId")
    if not entry["governed"]:
        view = {"governed": False, "runId": run_id, "task": task_view(entry)}
        if params.get("routingHistory") is not None:
            view["routingHistory"] = {"entries": [], "nextCursor": None, "total": 0}
        return view
    view = workflow_view(entry)
    history = params.get("routingHistory")
    if history is not None:
        if not isinstance(history, dict):
            raise PreviewError("INVALID_ARGUMENT", "routingHistory must be an object")
        routing = entry["routing"]
        entries = ([{"status": routing["status"], "decisionId": routing["decisionId"],
                     "taskId": routing["taskId"], "tableRevision": routing["tableRevision"],
                     "configurationRevision": routing["configurationRevision"],
                     "selectedProfile": routing["selectedProfile"], "reason": routing["reason"],
                     "constraints": routing["constraints"], "createdAt": entry["createdAt"],
                     "ownerGeneration": 1, "current": True}]
                   if routing and routing["decisionId"] else [])
        view["routingHistory"] = {"entries": entries, "nextCursor": None, "total": len(entries)}
    return view


def command_result(scenario: str, operation: str, params: Any) -> dict:
    """Only reads are served; every known mutation is refused and nothing is written."""
    if not isinstance(params, dict):
        raise PreviewError("INVALID_ARGUMENT", "params must be an object")
    if operation not in CONSOLE_OPERATIONS:
        raise PreviewError("METHOD_NOT_FOUND",
                           f"Operation {operation!r} is not available through the console")
    if operation not in READ_OPERATIONS:
        raise PreviewError("CONSOLE_READ_ONLY",
                           "This synthetic preview never performs mutations; only read-only operations are served")
    if operation == "workflow_get":
        result = workflow_get(params)
    elif operation == "selection_get":
        result = selection_get(params)
    elif operation == "selection_list":
        decisions = [decision_view(item) for item in FIXTURES["runs"].values()
                     if item["kind"] == "decision"]
        result = {"decisions": decisions, "nextCursor": None, "total": len(decisions)}
    elif operation == "model_profiles":
        profiles = profile_views()
        result = {"profiles": profiles, "cards": [], "annotations": [], "preferences": [],
                  "sampleCounts": {profile["profileId"]: 0 for profile in profiles},
                  "modelConcurrency": concurrency_rows(), "tableRevision": 7, "nextCursor": None}
    else:
        result = {"revisions": [], "nextCursor": None, "total": 0}
    if scenario == "error":
        # Validate first, then simulate the read failure: a missing or unknown
        # identifier still answers 400/404 instead of being masked by the 503.
        raise PreviewError("SERVICE_UNAVAILABLE", "synthetic preview: simulated read failure (--scenario error)")
    return result


# --------------------------------------------------------------------------- #
# HTTP                                                                          #
# --------------------------------------------------------------------------- #
def endpoint_lines(port: int) -> tuple[str, ...]:
    base = f"http://127.0.0.1:{port}"
    return (
        f"GET  {base}/api/console",
        f"GET  {base}/api/objectives",
        f"GET  {base}/api/objectives/{OBJ_A}/timeline",
        f"GET  {base}/api/objectives/{urllib.parse.quote(STANDALONE_GROUP, safe='')}/timeline",
        f"GET  {base}/api/tasks/preview-run-a1",
        f"POST {base}/api/command  workflow_get · selection_get · selection_list · "
        f"model_profiles · evaluation_history   (all mutations refused)",
    )


class PreviewHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    server_version = "buddy-console-preview"
    sys_version = ""

    def __init__(self, address, *, assets: Path, scenario: str, verbose: bool):
        self.assets = assets
        self.scenario = scenario
        self.verbose = verbose
        super().__init__(address, PreviewHandler)


class PreviewHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    timeout = 30

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
        self.send_header("X-Buddy-Preview-Scenario", self.server.scenario)
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

    def _error(self, status: int, code: str, message: str) -> None:
        self.close_connection = True
        self._json(status, {"ok": False, "error": {"code": code, "message": message}})

    def _preview_error(self, error: PreviewError) -> None:
        self._error(STATUS_BY_CODE.get(error.code, 400), error.code, error.message)

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
        self._read()

    def do_HEAD(self):  # noqa: N802 - BaseHTTPRequestHandler API
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
                return self._json(200, console_snapshot(self.server.scenario, self._assets_ready()))
            if path == "/api/objectives":
                params = parse_query(query, frozenset({"limit", "projectId", "hostId", "query", "filter"}))
                return self._json(200, objective_page(params))
            if path == "/api/tasks":
                params = parse_query(query, frozenset({"limit", "state", "adapter", "before", "rootsOnly",
                                                       "query", "projectId", "hostId", "filter"}))
                return self._json(200, task_page(params))
            if path.startswith("/api/objectives/") and path.endswith("/timeline"):
                identifier = path[len("/api/objectives/"):-len("/timeline")]
                if not OBJECTIVE_ID.match(identifier) or identifier not in FIXTURES["groups"]:
                    return self._error(404, "NOT_FOUND", "Not found")
                params = parse_query(query, frozenset({"limit", "query", "filter"}))
                params["objectiveId"] = identifier
                if self.server.scenario == "error":
                    # A known group read fails; an unknown group or a bad parameter
                    # above still answers 404/400 instead of being masked by the 503.
                    return self._error(503, "SERVICE_UNAVAILABLE",
                                       "synthetic preview: simulated read failure (--scenario error)")
                return self._json(200, objective_timeline(params, self.server.scenario))
            if path.startswith("/api/tasks/"):
                run_id = path[len("/api/tasks/"):]
                if not RUN_ID.match(run_id) or run_id not in FIXTURES["runs"]:
                    return self._error(404, "NOT_FOUND", "Not found")
                return self._json(200, task_view(FIXTURES["runs"][run_id]))
            if path.startswith("/api/"):
                return self._error(404, "NOT_FOUND", "Not found")
            return self._asset(path)
        except PreviewError as error:
            return self._preview_error(error)
        except Exception:  # noqa: BLE001 - no traceback crosses the boundary
            return self._error(500, "INTERNAL_ERROR", "The synthetic preview could not answer this read")

    # -- the one read-only command route -----------------------------------
    def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
        if not self._trusted_host():
            return
        if self._path() != "/api/command":
            return self._error(404, "NOT_FOUND", "Not found")
        if (self.headers.get("X-Buddy-CSRF") or "") != SYNTHETIC_CSRF:
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
            result = command_result(self.server.scenario, operation, value.get("params", {}))
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
    "ObjectivePage": {"objectives", "total", "nextCursor", "cursor", "changed"},
    "ObjectiveSummary": {"objectiveId", "kind", "title", "titleSource", "project", "sourceHostId",
                         "currentHostIds", "createdAt", "lastActivityAt", "lastActivitySeq", "state",
                         "counts", "matchingRuns", "rootRunIds"},
    "ObjectiveCounts": {"roots", "helpers", "active", "host", "review", "ended"},
    "TimelineSpan": {"spanId", "runId", "kind", "startAt", "endAt", "state", "attemptId", "turnId",
                     "turnIndex", "requestId", "configuration", "shutdownConfirmed", "uncertain",
                     "clockSkew"},
    "TimelineRow": {"runId", "parentRunId", "rootRunId", "title", "titleSource", "createdAt", "state",
                    "status", "category", "shutdownConfirmed", "depth", "kind", "configuration",
                    "acceptedAt", "acceptanceVerdict"},
    "TimelineEvent": {"seq", "runId", "kind", "at", "label", "summary", "actor", "attemptId",
                      "requestId", "artifactId"},
    "ObjectiveTimeline": {"objective", "observedAt", "cursor", "rows", "spans", "events", "totals",
                          "truncated", "filtered", "scopeComplete"},
}


def occupied_intervals(group: str) -> list[tuple[datetime, datetime]]:
    """Fixture occupancy, mirroring the layout's readSpan/marker rules."""
    observed = datetime.fromisoformat(OBSERVED_AT.replace("Z", "+00:00"))
    open_states = {"queued", "claimed", "pending", "starting", "executing", "finalizing", "uncertain",
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


def validate_fixtures() -> list[str]:
    """Shape drift, determinism and the idle-gap invariants of the fixtures."""
    problems: list[str] = []
    page = objective_page({})
    timeline = objective_timeline({"objectiveId": OBJ_A}, "normal")
    for name, value, keys in (
            ("ObjectivePage", page, REQUIRED_KEYS["ObjectivePage"]),
            ("ObjectiveSummary", page["objectives"][0], REQUIRED_KEYS["ObjectiveSummary"]),
            ("ObjectiveCounts", page["objectives"][0]["counts"], REQUIRED_KEYS["ObjectiveCounts"]),
            ("ObjectiveTimeline", timeline, REQUIRED_KEYS["ObjectiveTimeline"]),
            ("TimelineRow", timeline["rows"][0], REQUIRED_KEYS["TimelineRow"]),
            ("TimelineSpan", timeline["spans"][0], REQUIRED_KEYS["TimelineSpan"]),
            ("TimelineEvent", timeline["events"][0], REQUIRED_KEYS["TimelineEvent"])):
        present = set(value)
        problems += [f"{name}: missing {key}" for key in sorted(keys - present)]
    if set(page) != REQUIRED_KEYS["ObjectivePage"]:
        problems.append(f"ObjectivePage: keys {sorted(set(page))}")
    if json.dumps(objective_page({}), sort_keys=True) != json.dumps(objective_page({}), sort_keys=True):
        problems.append("objective list is not stable across reads")
    if json.dumps(objective_timeline({"objectiveId": OBJ_B}, "normal"), sort_keys=True) != json.dumps(
            objective_timeline({"objectiveId": OBJ_B}, "normal"), sort_keys=True):
        problems.append("objective timeline is not stable across reads")
    if any(end - start > timedelta(minutes=30) for start, end in idle_gaps(OBJ_A)):
        problems.append("objective A's unconfirmed tail must prevent false idle folding")
    biggest = max((gap[1] - gap[0] for gap in idle_gaps(OBJ_B)), default=timedelta())
    if biggest <= timedelta(minutes=30):
        problems.append("objective B has no strictly-greater-than-30-minute idle gap")
    if not any(gap[1] - gap[0] == timedelta(minutes=30) for gap in idle_gaps(OBJ_B)):
        problems.append("objective B does not carry the exact 30-minute non-folding boundary")
    if not objective_timeline({"objectiveId": OBJ_A}, "normal")["scopeComplete"]:
        problems.append("normal scope must be complete")
    if not objective_timeline({"objectiveId": OBJ_A}, "truncated")["truncated"]["rows"]:
        problems.append("truncated scenario must report row truncation")
    categories = {entry["category"] for entry in FIXTURES["runs"].values() if entry["governed"]}
    if categories != {"host", "active", "review", "ended"}:
        problems.append(f"fixture categories are {sorted(categories)}")
    kinds = {item["kind"] for _run_id, item in FIXTURES["markers"]}
    if set(MARKER_LABELS) - kinds:
        problems.append(f"missing marker kinds: {sorted(set(MARKER_LABELS) - kinds)}")
    return problems


# --------------------------------------------------------------------------- #
# Modes                                                                         #
# --------------------------------------------------------------------------- #
def print_banner(server: PreviewHTTPServer, assets: Path) -> None:
    print("=" * 78)
    print("SYNTHETIC PREVIEW ONLY — not a Buddy board; no daemon, Worker, model call or")
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
    for group in (OBJ_A, OBJ_B, STANDALONE_GROUP):
        summary = objective_summary(group, set(FIXTURES["groups"][group]))
        members = summary["counts"]["roots"] + summary["counts"]["helpers"]
        print(f"  {group}  state={summary['state']} members={members} project={summary['project']['label']}")
    print("mutation POSTs are refused; Ctrl-C stops the preview cleanly.")


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
        headers = {"X-Buddy-CSRF": SYNTHETIC_CSRF, "Origin": base, "Content-Type": "application/json"}
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
        check("snapshot task views", len(snapshot.get("tasks", {}).get("runs", [])) == len(FIXTURES["runs"]))
        status, page, _headers = call("GET", "/api/objectives")
        check("GET /api/objectives", status == 200 and len(page["objectives"]) == 3
              and page["cursor"] == FIXTURES["head"] and page["changed"] is False, f"status={status}")
        status, timeline, _headers = call("GET", f"/api/objectives/{OBJ_A}/timeline")
        if scenario == "error":
            check("timeline fails closed in error scenario", status == 503
                  and timeline.get("error", {}).get("code") == "SERVICE_UNAVAILABLE", f"status={status}")
        else:
            check("GET /api/objectives/<id>/timeline", status == 200 and timeline["observedAt"] == OBSERVED_AT,
                  f"status={status}")
            check("truncated scenario reports truncation",
                  (scenario == "truncated") == bool(timeline["truncated"]["rows"]),
                  str(timeline["truncated"]))
        biggest = max((gap[1] - gap[0] for gap in idle_gaps(OBJ_B)), default=timedelta())
        check("truly idle gap > 30 min", biggest > timedelta(minutes=30), f"biggest={biggest}")
        status, task, _headers = call("GET", "/api/tasks/preview-run-a1")
        check("GET /api/tasks/<id>", status == 200 and task.get("runId") == "preview-run-a1",
              f"status={status}")
        status, standalone, _headers = call(
            "GET", f"/api/objectives/{urllib.parse.quote(STANDALONE_GROUP, safe='')}/timeline")
        expected = 503 if scenario == "error" else 200
        check("encoded standalone timeline", status == expected, f"status={status}")
        status, result, _headers = call("POST", "/api/command", payload={"operation": "workflow_get",
                                                               "params": {"runId": "preview-run-a1"}})
        if scenario == "error":
            check("workflow_get fails closed", status == 503, f"status={status}")
        else:
            check("POST workflow_get is read-only", status == 200 and result["result"]["governed"] is True,
                  f"status={status}")
        status, denied, _headers = call("POST", "/api/command", payload={"operation": "workflow_cancel",
                                                               "params": {"runId": "preview-run-a1"}})
        check("mutation POST is refused", status == 403
              and denied["error"]["code"] == "CONSOLE_READ_ONLY", f"status={status}")
        status, unknown, _headers = call("POST", "/api/command", payload={"operation": "nope", "params": {}})
        check("unknown operation is METHOD_NOT_FOUND", status == 404
              and unknown["error"]["code"] == "METHOD_NOT_FOUND", f"status={status}")
        status, _missing, _headers = call(
            "GET", "/api/objectives/obj-ffffffff-ffff-4fff-8fff-ffffffffffff/timeline")
        check("unknown objective is 404", status == 404, f"status={status}")
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
        raise SystemExit("--assets ABS_PATH is required (for example src/buddy/console_assets)")
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
        description="Deterministic synthetic preview server for the work-objective timeline UI.",
        epilog="All data is synthetic; no board, daemon, Worker, model call or production connection is used.")
    parser.add_argument("--assets", help="absolute path of the built console assets directory")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--port", type=int, default=0, help="0 binds an ephemeral loopback port (default)")
    parser.add_argument("--verbose", action="store_true", help="enable access logging (off by default)")
    parser.add_argument("--check", action="store_true", help="run the fixture self-checks and exit")
    parser.add_argument("--smoke", action="store_true", help="start, read back with urllib, and exit")
    args = parser.parse_args(argv)

    if args.check:
        problems = validate_fixtures()
        for problem in problems:
            print(f"CHECK FAIL  {problem}")
        if problems:
            return 1
        biggest = max((gap[1] - gap[0] for gap in idle_gaps(OBJ_B)), default=timedelta())
        print(f"CHECK OK  shapes stable · idle gap {biggest} · fixtures deterministic · "
              f"{len(FIXTURES['runs'])} runs / {len(FIXTURES['groups'])} groups")
        return 0

    assets = prepare_assets(args.assets)
    if args.smoke:
        return smoke(assets, args.scenario)
    return run_server(assets, args.scenario, args.port, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
