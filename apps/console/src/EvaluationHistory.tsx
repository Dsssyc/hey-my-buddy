import { useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot } from "./types";
import { formatDate } from "./ui";

/** Bounded page size: the console never asks for an unbounded publication range. */
export const HISTORY_PAGE_SIZE = 20;

export type EvaluationRevision = {
  revision: number;
  kind: string;
  actor: string | null;
  counts: {
    profiles: number;
    cards: number;
    preferences: number;
    provided?: string[];
  };
  createdAt: string;
};
export type EvaluationHistoryPage = {
  revisions: EvaluationRevision[];
  nextCursor: number | null;
  total: number;
};

const kindText: Record<string, string> = {
  human: "控制台发布",
  maintenance: "维护发布",
  catalog: "目录更新",
  initial: "初始版本",
};

function count(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : Number.NaN;
}

/** Rejects an incomplete envelope instead of rendering a fabricated history. */
export function parseHistoryPage(value: unknown): EvaluationHistoryPage {
  const page = value as EvaluationHistoryPage | null;
  const valid = !!page && Array.isArray(page.revisions)
    && page.revisions.every(item => !!item
      && Number.isInteger(item.revision)
      && typeof item.kind === "string"
      && (item.actor === null || typeof item.actor === "string")
      && typeof item.createdAt === "string"
      && !!item.counts
      && !Number.isNaN(count(item.counts.profiles))
      && !Number.isNaN(count(item.counts.cards))
      && !Number.isNaN(count(item.counts.preferences))
      && (item.counts.provided === undefined || (Array.isArray(item.counts.provided) && item.counts.provided.every(entry => typeof entry === "string"))))
    && (page.nextCursor === null || (Number.isInteger(page.nextCursor) && page.nextCursor > 0))
    && Number.isInteger(page.total);
  if (!valid) throw new Error("更新记录响应不完整，请检查服务版本。");
  return page;
}

function countsText(item: EvaluationRevision): string {
  const base = `配置 ${item.counts.profiles} · 卡片 ${item.counts.cards} · 偏好 ${item.counts.preferences}`;
  return item.counts.provided?.length ? `${base} · 提供：${item.counts.provided.join("、")}` : base;
}

/**
 * Read-only publication history. Viewing it never starts a model request and
 * never mutates the table; internal maintain decisions stay out of this page.
 */
export function EvaluationHistory({ snapshot, api, active, onBack }: {
  snapshot: Snapshot; api: ConsoleApi; active: boolean; onBack: () => void;
}) {
  const [page, setPage] = useState<EvaluationHistoryPage | null>(null);
  const [before, setBefore] = useState<number | undefined>();
  const [cursors, setCursors] = useState<(number | undefined)[]>([]);
  const [error, setError] = useState(""), [busy, setBusy] = useState(true), [retry, setRetry] = useState(0);
  const csrf = useRef(snapshot.csrfToken);
  csrf.current = snapshot.csrfToken;
  useEffect(() => {
    if (!active) return;
    // A late page from a previously inspected cursor must not replace this one.
    let current = true;
    setBusy(true); setError("");
    api.command<EvaluationHistoryPage>(
      "evaluation_history",
      { limit: HISTORY_PAGE_SIZE, ...(before === undefined ? {} : { before }) },
      csrf.current,
    ).then(value => {
      if (current) setPage(parseHistoryPage(value));
    }).catch(reason => { if (current) setError(errorText(reason)); })
      .finally(() => { if (current) setBusy(false); });
    return () => { current = false; };
  }, [active, api, before, retry]);
  return <>
    <header className="detail-header"><div className="row-between"><h2>更新记录</h2>
      <button className="button small-button" onClick={onBack}>返回模型卡片</button></div>
      <p className="small muted">只读取已发布版本的元数据；查看记录不会调用模型，也不会修改评价表。</p></header>
    <div className="detail-body">
      <div className="row-between"><h3>已发布版本{page ? ` · ${page.total}` : ""}</h3>
        <button className="button small-button" disabled={busy} onClick={() => setRetry(n => n + 1)}>刷新记录</button></div>
      {error && <p role="alert" className="error-message">{error}
        <button className="button small-button" disabled={busy} onClick={() => setRetry(n => n + 1)}>重试读取</button></p>}
      {busy && <p role="status" className="small muted">正在读取更新记录…</p>}
      {!busy && !error && page?.revisions.length === 0 && <p className="muted">还没有已发布的评价版本。</p>}
      <ul className="history-list" aria-busy={busy}>{page?.revisions.map(item => <li className="history-item" key={item.revision}>
        <span className="history-version">V{item.revision}</span>
        <span className="small muted">{kindText[item.kind] || item.kind}{item.actor ? ` · ${item.actor}` : ""} · {formatDate(item.createdAt)}</span>
        <span className="small muted">{countsText(item)}</span>
      </li>)}</ul>
      <div className="actions history-pagination">
        {cursors.length > 0 && <button className="button small-button" disabled={busy} onClick={() => {
          setBefore(cursors[cursors.length - 1]); setCursors(all => all.slice(0, -1)); setPage(null);
        }}>较新记录</button>}
        {page?.nextCursor != null && <button className="button small-button" disabled={busy} onClick={() => {
          setCursors(all => [...all, before]); setBefore(page.nextCursor!); setPage(null);
        }}>加载更早记录</button>}
      </div>
    </div>
  </>;
}
