import { useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { RoutingHistory, Workflow } from "./workflow-types";
import { configurationText, DecisionDetails } from "./DecisionDetails";
import { decisionStatus } from "./decision-types";
import { formatDate } from "./ui";
import { fallbackDescription, recordedRoutingMode, routingBasisSummary, selectionSourceText } from "./routing-display";

export function RoutingDetails({ value, api, csrfToken, active, initialDecisionId }: {
  value: Workflow; api: ConsoleApi; csrfToken: string; active: boolean; initialDecisionId?: string | null;
}) {
  const [selected, setSelected] = useState<string | null>(initialDecisionId ?? null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [page, setPage] = useState<RoutingHistory | null>(null);
  const [before, setBefore] = useState<number | undefined>();
  const [cursors, setCursors] = useState<(number | undefined)[]>([]);
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [retry, setRetry] = useState(0);
  const currentId = value.routing?.decisionId || null;
  const sourceText = selectionSourceText(value.routing?.source);
  const basisLine = routingBasisSummary(value.routing?.routingBasis);
  // Explicit null comes from an old timeline span: its identity was not
  // recorded, so the current route cannot stand in for that historical fact.
  const chosenId = selected || (initialDecisionId === null ? null : currentId);
  const chosen = page?.entries.find(d => d.decisionId === chosenId);
  const rationale = useRef<HTMLElement>(null);
  const [focusRequest, setFocusRequest] = useState(0);
  useEffect(() => {
    if (initialDecisionId !== undefined) {
      setSelected(initialDecisionId); setHistoryOpen(false);
      if (initialDecisionId) setFocusRequest(n => n + 1);
    }
  }, [initialDecisionId]);
  function inspect(decisionId: string | null) {
    setSelected(decisionId); setHistoryOpen(false); setFocusRequest(n => n + 1);
  }
  useEffect(() => {
    if (focusRequest > 0) rationale.current?.focus();
  }, [focusRequest]);
  useEffect(() => {
    if (!active || !historyOpen) return;
    let current = true;
    setBusy(true); setError("");
    api.command<{ routingHistory: RoutingHistory }>("workflow_get", {
      runId: value.runId, routingHistory: { limit: 20, ...(before === undefined ? {} : { before }) },
    }, csrfToken).then(result => {
      if (!result?.routingHistory || !Array.isArray(result.routingHistory.entries)) throw new Error("路由历史响应不完整。");
      if (current) setPage(result.routingHistory);
    }).catch(reason => { if (current) setError(errorText(reason)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [api, csrfToken, value.runId, currentId, value.routing?.status, active, historyOpen, before, retry]);
  return <>
    {initialDecisionId === null && <p className="small muted" role="status">决策 ID 未记录；可查看当前配置或路由历史。</p>}
    <section aria-label="当前执行配置"><h3>当前执行配置</h3>
      <p className="read-text">{value.executionConfiguration ? configurationText(value.executionConfiguration) : "尚未确定执行配置。"}</p>
      <dl className="facts"><dt>配置版本</dt><dd>{value.executionConfigurationRevision == null ? "未记录" : `V${value.executionConfigurationRevision}`}</dd>
        <dt>路由状态</dt><dd>{value.routing ? decisionStatus[value.routing.status] || value.routing.status : "未记录"}</dd>
        {sourceText && <><dt>选择方式</dt><dd>{sourceText}</dd></>}
        {value.routing && (value.routing.status !== "explicit" || value.routing.routingMode || value.routing.requestedRoutingMode) && <><dt>请求模式</dt><dd>{recordedRoutingMode(value.routing.requestedRoutingMode)}</dd>
          <dt>实际模式</dt><dd>{value.routing.source === "single-candidate" ? "未调用 Router" : recordedRoutingMode(value.routing.routingMode)}</dd>
          <dt>模式降级</dt><dd>{fallbackDescription(value.routing.fallback)}</dd></>}</dl>
      {!currentId && <p className="read-text">{value.routing?.status === "explicit"
        ? "Host 指定" : value.routing?.reason || value.activeRequest?.summary || "路由决策未记录"}</p>}
      {currentId && value.routing?.reason && ["needs-host", "fenced", "failed"].includes(value.routing.status) &&
        <p className="error-message" role="status">本次路由未能用于执行：{value.routing.reason}</p>}
      {!!Object.keys(value.routing?.constraints || {}).length && <p className="small muted wrap">提交时硬约束：{configurationText(value.routing?.constraints)}</p>}
      {!!value.routing?.requiredCapabilities?.length && <p className="small muted wrap">所需能力：{value.routing.requiredCapabilities.join("、")}</p>}
      {basisLine && <p className="small muted wrap">路由依据：{basisLine}（冻结记录，不随当前配置变化）</p>}
    </section>
    {chosenId && <section className="detail-section" ref={rationale} tabIndex={-1} aria-label="所选路由决定" data-decision-id={chosenId}>
      {chosenId !== currentId && <div className="row-between"><h3>此前的路由依据</h3>
        <button className="button small-button" onClick={() => inspect(null)}>返回当前配置</button></div>}
      <DecisionDetails key={chosenId} decisionId={chosenId} api={api} csrfToken={csrfToken} active={active}
        refreshKey={chosenId === currentId ? value.routing?.status || "" : chosen?.status || ""} />
    </section>}
    <details className="detail-section" open={historyOpen} onToggle={event => setHistoryOpen(event.currentTarget.open)}>
      <summary>此前的路由决定</summary>
      {error && <p className="error-message" role="alert">{error}</p>}
      {busy && <p role="status" className="small muted">正在读取路由历史…</p>}
      {!busy && page?.entries.length === 0 && <p className="muted">没有智能路由历史。</p>}
      <ul className="history-list" aria-busy={busy}>{page?.entries.map(d => <li key={d.decisionId}>
        <button className="history-choice" aria-pressed={d.decisionId === chosenId} disabled={busy} onClick={() => inspect(d.decisionId)}>
          <span>{formatDate(d.createdAt)} · {recordedRoutingMode(d.routingMode)} · {d.fallback ? "已降级 · " : ""}{d.selectedProfile ? configurationText(d.selectedProfile) : "未选定配置"}</span>
          <span>{decisionStatus[d.status] || d.status}{d.current ? " · 当前" : ""}</span>
        </button></li>)}</ul>
      <div className="actions history-pagination">
        {error && <button className="button small-button" disabled={busy} onClick={() => setRetry(n => n + 1)}>重试读取历史</button>}
        {cursors.length > 0 && <button className="button small-button" disabled={busy} onClick={() => {
          setBefore(cursors[cursors.length - 1]); setCursors(all => all.slice(0, -1)); setPage(null);
        }}>较新决定</button>}
        {page?.nextCursor != null && <button className="button small-button" disabled={busy} onClick={() => {
          setCursors(all => [...all, before]); setBefore(page.nextCursor!); setPage(null);
        }}>加载更早决定</button>}
      </div>
    </details>
    {!!value.turns?.length && <details className="detail-section"><summary>执行回合与配置对应</summary>
      <ul className="route-turns">{value.turns.map(turn => <li key={turn.turnId}>
        <strong>第 {turn.turnIndex} 回合</strong><p className="small wrap">{configurationText(turn.executionConfiguration)}</p>
        {turn.routing ? <p className="small">执行配置 V{turn.routing.executionConfigurationRevision} · {turn.routing.decisionId
          ? <button className="routing-link" onClick={() => inspect(turn.routing!.decisionId)}>查看此回合的决定</button>
          : "Host 指定"}</p> : <p className="small muted">回合路由未记录</p>}
        <details><summary>回合标识</summary><p className="mono wrap">{turn.turnId} · {turn.attemptId}</p></details>
      </li>)}</ul>
      {!!value.truncated?.turns && <p className="small muted">已截断 · 另有 {value.truncated.turns} 回合</p>}
    </details>}
  </>;
}
