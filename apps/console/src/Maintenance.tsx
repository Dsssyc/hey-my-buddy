import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot } from "./types";
import type { DecisionPage } from "./decision-types";
import { decisionStatus } from "./decision-types";
import { DecisionDetails } from "./DecisionDetails";
import { useDecisionRequest } from "./use-decision-request";
import { formatDate } from "./ui";

export function Maintenance({ snapshot, api, refresh, active, onBack }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>; active: boolean; onBack: () => void;
}) {
  const request = useDecisionRequest(api, snapshot, refresh);
  const [page, setPage] = useState<DecisionPage | null>(null);
  const [before, setBefore] = useState<number | undefined>();
  const [cursors, setCursors] = useState<(number | undefined)[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState(""), [busy, setBusy] = useState(true), [retry, setRetry] = useState(0);
  // The general snapshot is a wake-up hint, never the maintenance history source.
  const version = snapshot.decisions.filter(d => d.kind === "maintain").map(d => `${d.decisionId}:${d.status}:${d.updatedAt || ""}`).join("|");
  useEffect(() => {
    if (!active) return;
    let current = true;
    setBusy(true); setError("");
    api.command<DecisionPage>("selection_list", { kind: "maintain", limit: 20, ...(before === undefined ? {} : { before }) }, snapshot.csrfToken)
      .then(value => {
        if (!value || !Array.isArray(value.decisions) || value.decisions.some(d => d.kind !== "maintain")) throw new Error("评价维护记录不完整。");
        if (current) setPage(value);
      }).catch(reason => { if (current) setError(errorText(reason)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [active, api, before, snapshot.csrfToken, version, retry, request.message]);
  const hasPending = page?.decisions.some(d => ["queued", "running"].includes(d.status));
  useEffect(() => {
    if (!active || busy || !hasPending) return;
    const timer = setTimeout(() => setRetry(n => n + 1), 5000);
    return () => clearTimeout(timer);
  }, [active, busy, hasPending, retry]);
  const chosen = page?.decisions.find(d => d.decisionId === selected);
  return <>
    <header className="detail-header"><div className="row-between"><h2>评价维护</h2>
      <button className="button small-button" onClick={onBack}>返回模型卡片</button></div>
      <p className="small muted">整理共享评价表中的证据。查看记录不调用模型。</p></header>
    <div className="detail-body" hidden={!!selected}>
      <section aria-label="整理待处理证据">
        <h3>整理待处理证据</h3><p className="small muted">当前有 {snapshot.pendingEvidence} 条待处理。每次请求整理一批；未启用自动采纳时，只保留建议。</p>
        <button className="button maintenance-request" disabled={request.busy || snapshot.capabilities.maintenance !== true ||
          (!request.retryId && (!snapshot.configuration.decisionProfileId || snapshot.pendingEvidence === 0))}
          onClick={() => void request.submit("evaluation_maintain")}>
          {request.busy ? "正在提交…" : request.retryId ? "重试同一整理请求" : "请求整理"}</button>
        {request.message && <p className="inline-message" role="status">{request.message}</p>}
        {request.retryId && <p className="mono small wrap">请求 ID：{request.retryId}</p>}
        {snapshot.gate.phase !== "open" && <p className="small muted">评价表正在编辑，整理请求会排队，随后读取完整发布版本。</p>}
      </section>
      <section className="detail-section" aria-label="评价维护记录">
        <div className="row-between"><h3>整理记录{page ? ` · ${page.total}` : ""}</h3>
          <button className="button small-button" disabled={busy} onClick={() => setRetry(n => n + 1)}>刷新记录</button></div>
        {error && <p role="alert" className="error-message">{error}</p>}
        {busy && <p role="status" className="small muted">正在读取维护记录…</p>}
        {!busy && page?.decisions.length === 0 && <p className="muted">还没有评价整理记录。</p>}
        <ul className="maintenance-list" aria-busy={busy}>{page?.decisions.map(d => <li key={d.decisionId}>
          <button className="history-choice" disabled={busy} aria-pressed={d.decisionId === selected} onClick={() => setSelected(d.decisionId)}>
            <span>{formatDate(d.createdAt)} · 评价版本 V{d.tableRevision}</span>
            <span>{decisionStatus[d.status] || d.status}</span>
          </button></li>)}</ul>
        <div className="actions history-pagination">
          {cursors.length > 0 && <button className="button small-button" disabled={busy} onClick={() => {
            setBefore(cursors[cursors.length - 1]); setCursors(all => all.slice(0, -1)); setSelected(null); setPage(null);
          }}>较新记录</button>}
          {page?.nextCursor != null && <button className="button small-button" disabled={busy} onClick={() => {
            setCursors(all => [...all, before]); setBefore(page.nextCursor!); setSelected(null); setPage(null);
          }}>加载更早记录</button>}
        </div>
      </section>
    </div>
    {selected && <div className="detail-body">
      <button className="button small-button" onClick={() => setSelected(null)}>返回整理记录</button>
      <section className="detail-section">
        <DecisionDetails key={selected} decisionId={selected} api={api} csrfToken={snapshot.csrfToken} active={active}
          refreshKey={chosen ? `${chosen.status}:${chosen.updatedAt || ""}` : ""} />
      </section>
    </div>}
  </>;
}
