import { useCopyState } from "./i18n/use-copy-state";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { retainEqual } from "./retain-equal";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { MacroTaskFilter, MacroTaskPage, MacroTaskQuery, MacroTaskSort, MacroTaskSummary } from "./macro-task-types";

type PageState = { rows: MacroTaskSummary[]; totals: Record<MacroTaskFilter, number>; nextCursor: string | null };
const emptyTotals = (): PageState["totals"] => ({ all: 0, active: 0, host: 0, review: 0 });

/** The service order, also used when polling merges fresh summaries into loaded pages. */
function compareRows(left: MacroTaskSummary, right: MacroTaskSummary, sort: MacroTaskSort): number {
  const difference = sort.startsWith("activity-") ? left.lastActivitySeq - right.lastActivitySeq
    : (left.createdAt ?? "") < (right.createdAt ?? "") ? -1 : (left.createdAt ?? "") > (right.createdAt ?? "") ? 1 : 0;
  const order = difference || (left.macroTaskId < right.macroTaskId ? -1 : left.macroTaskId > right.macroTaskId ? 1 : 0);
  return sort.endsWith("asc") ? order : -order;
}

/**
 * Keyset-paged work macro task list. Polls the first page on the console's
 * three-second cadence and merges in place. A changed order waits for the
 * visible list to decide whether the reader is idle or explicitly applies it.
 */
export function useMacroTaskList(api: ConsoleApi, query: MacroTaskQuery, active: boolean) {
  const [page, setPage] = useState<PageState>({ rows: [], totals: emptyTotals(), nextCursor: null });
  const latestRef = useRef(new Map<string, MacroTaskSummary>());
  const [loading, setLoading] = useState(active), [error, setError] = useCopyState();
  const [reorder, setReorder] = useState<{ count: number | null } | null>(null);
  const [verifiedAtMs, setVerifiedAtMs] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible();
  const previousVisible = useRef(visible);
  const initialReadStarted = useRef(false);
  const pageRef = useRef(page);
  pageRef.current = page;
  const key = JSON.stringify(query);
  const current = useRef({ query, active, cursor: page.nextCursor });
  current.current = { query, active, cursor: page.nextCursor };

  const pendingNew = useRef<Map<string, MacroTaskSummary>>(new Map());
  const needsFirst = useRef(false);

  const fetchPage = useCallback(async (mode: "first" | "more" | "poll", foreground = mode !== "poll") => {
    if (!current.current.active || mode === "more" && !current.current.cursor) return;
    if (pending.current) {
      if (!foreground) return;
      ++generation.current;
      request.current?.abort();
    }
    // A failed reorder refresh invalidated its cursor. The next automatic read
    // must rebuild the first page before normal polling/paging can resume.
    if (mode === "poll" && needsFirst.current) mode = "first";
    const version = generation.current, controller = new AbortController();
    initialReadStarted.current = true;
    request.current = controller; pending.current = true;
    if (foreground || mode === "first") { setLoading(true); setError(""); }
    try {
      const limit = mode === "poll"
        ? Math.max(50, Math.min(100, pageRef.current.rows.length || 50))
        : 50;
      const next: MacroTaskPage = await api.macroTasks({
        ...current.current.query, limit,
        ...(mode === "more" ? { before: current.current.cursor! } : {}),
      }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      const byOrder = (left: MacroTaskSummary, right: MacroTaskSummary) => compareRows(left, right, current.current.query.sort ?? "activity-desc");
      const checked = api.readVerifiedAt?.(next) ?? null;
      setVerifiedAtMs(previous => mode === "first" || mode === "poll" && next.nextCursor === null
        ? checked : previous === null || checked === null ? null : Math.min(previous, checked));
      setError("");
      // Both load modes merge into the existing freshest map, so a page load
      // never discards newer poll data for already-known rows.
      const fresh = new Map(latestRef.current);
      for (const summary of next.macroTasks) fresh.set(summary.macroTaskId, retainEqual(fresh.get(summary.macroTaskId), summary)!);
      if (mode === "poll") {
        const previous = pageRef.current;
        // An empty display adopts the read directly (including its cursor);
        // nothing was on screen to reorder. Otherwise the freshest summaries
        // render in place while only a real order change — new activity moving
        // a group, or an unseen group appearing — defers behind the notice.
        if (!previous.rows.length) {
          pendingNew.current.clear();
          latestRef.current = fresh;
          setPage(previous => retainEqual(previous, { rows: next.macroTasks.map(row => fresh.get(row.macroTaskId)!).sort(byOrder), totals: next.totals, nextCursor: next.nextCursor }));
          return;
        }
        const previousById = new Map(previous.rows.map(row => [row.macroTaskId, row]));
        const known = new Set(previousById.keys());
        const hasNew = next.macroTasks.some(row => !known.has(row.macroTaskId));
        // A complete filtered first page proves the whole matching scope: rows
        // it no longer returns have stopped matching and leave the list (an
        // open detail stays mounted on the workspace's summary fallbacks). An
        // incomplete page cannot prove absence, so it keeps the refresh notice
        // instead of guessing.
        const complete = next.nextCursor === null;
        const returned = new Set(next.macroTasks.map(row => row.macroTaskId));
        const droppedAmbiguous = complete ? [] : previous.rows.filter(row => {
          if (returned.has(row.macroTaskId)) return false;
          const last = next.macroTasks.at(-1);
          return last === undefined || byOrder(row, last) <= 0;
        });
        // Completeness proves membership, not permission to reorder. Preserve
        // the surviving committed order; newcomers wait behind the notice.
        const retained = previous.rows
          .filter(row => !complete || returned.has(row.macroTaskId))
          .map(row => fresh.get(row.macroTaskId) ?? row);
        const merged = [...retained, ...next.macroTasks.filter(row => !known.has(row.macroTaskId))];
        if (complete) pendingNew.current.clear();
        for (const row of next.macroTasks) if (!known.has(row.macroTaskId)) pendingNew.current.set(row.macroTaskId, row);
        const sorted = [...merged].sort(byOrder);
        const orderChanged = hasNew || sorted.some((row, index) => merged[index]!.macroTaskId !== row.macroTaskId);
        const changedCount = merged.filter(row => {
          const committed = previousById.get(row.macroTaskId);
          return committed === undefined || row.lastActivitySeq > committed.lastActivitySeq;
        }).length;
        latestRef.current = fresh;
        // The committed order stays behind the notice, while reported totals
        // always reflect the newest read.
        setPage(previous => retainEqual(previous, { rows: retained, totals: next.totals, nextCursor: complete ? null : previous.nextCursor }));
        if (orderChanged) setReorder(existing => changedCount > 0 ? existing?.count === changedCount ? existing : { count: changedCount } : existing ?? { count: null });
        else if (next.changed || droppedAmbiguous.length > 0) setReorder(existing => existing ?? { count: null });
      } else {
        const known = new Set(mode === "more" ? pageRef.current.rows.map(row => row.macroTaskId) : []);
        const rows = mode === "more"
          ? [...pageRef.current.rows.map(row => fresh.get(row.macroTaskId) ?? row),
            ...next.macroTasks.filter(row => !known.has(row.macroTaskId)).map(row => fresh.get(row.macroTaskId) ?? row)]
          : next.macroTasks.map(row => fresh.get(row.macroTaskId)!).sort(byOrder);
        setPage(previous => retainEqual(previous, { rows, totals: next.totals, nextCursor: next.nextCursor }));
        if (mode === "first") { pendingNew.current.clear(); needsFirst.current = false; }
        latestRef.current = mode === "first" ? new Map(rows.map(row => [row.macroTaskId, row])) : fresh;
        // Loading an unchanged older page never discards a pending reorder
        // notice; a paged read that reports `changed` still offers one because
        // activity above the first page is not visible in this reply.
        if (mode === "more") setReorder(existing => existing ?? (next.changed ? { count: null } : null));
        else setReorder(null);
      }
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(() => errorText(failure));
    } finally {
      if (version === generation.current) { pending.current = false; if (foreground || mode === "first") setLoading(false); }
    }
  }, [api]);
  // Keep the committed rows on screen while the new first page replaces the
  // stale keyset cursor. A failed read leaves a retryable first-page refresh.
  const applyReorder = useCallback(() => {
    if (!reorder || !current.current.active) return;
    ++generation.current;
    request.current?.abort();
    pending.current = false;
    needsFirst.current = true;
    setPage(previous => {
      const known = new Set(previous.rows.map(row => row.macroTaskId));
      const merged = previous.rows.map(row => latestRef.current.get(row.macroTaskId) ?? row);
      for (const [id, summary] of pendingNew.current) if (!known.has(id)) merged.push(summary);
      return { rows: merged.sort((left, right) => compareRows(left, right, current.current.query.sort ?? "activity-desc")), totals: previous.totals, nextCursor: null };
    });
    setReorder(null);
    void fetchPage("first");
  }, [reorder, fetchPage]);

  const loadedKey = useRef<string | null>(null);
  // True while the load effect has an armed (debounced) first-page read; the
  // cadence effect stands down until it fires, so a mount or scope change
  // never performs the first read twice.
  const armed = useRef(false);
  const resetScope = () => {
    setPage({ rows: [], totals: emptyTotals(), nextCursor: null });
    pendingNew.current.clear();
    needsFirst.current = false;
    latestRef.current = new Map();
    setReorder(null);
    setVerifiedAtMs(null);
  };
  useEffect(() => {
    ++generation.current; request.current?.abort(); pending.current = false; setLoading(false);
    armed.current = false;
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    const becameVisible = visible && !previousVisible.current;
    previousVisible.current = visible;
    if (!active) return cleanup;
    const sameScope = loadedKey.current === key + revision;
    if (!sameScope) {
      // Invalidate old rows and paging even while hidden. Only the first
      // activation may read in the background; later queries wait for return.
      loadedKey.current = key + revision;
      resetScope();
    }
    // Loaded rows and paging survive a visibility toggle.
    if (sameScope && pageRef.current.rows.length && !needsFirst.current) return cleanup;
    // Consume the exception when a request starts, not when an effect arms:
    // StrictMode cleanup must leave the replacement debounce a first read.
    const initialRead = !initialReadStarted.current;
    if (!visible && !initialRead) return cleanup;
    if (sameScope && !needsFirst.current) resetScope();
    // The cadence effect supplies the single immediate read on return,
    // including an empty result or a query invalidated while hidden.
    if (becameVisible) return cleanup;
    setLoading(true);
    armed.current = true;
    const timer = setTimeout(() => {
      armed.current = false;
      // Initialization alone may ignore visibility. Later scope debounces
      // still re-check it if the document hides before cleanup lands.
      if (initialRead || documentVisibleNow()) void fetchPage("first");
    }, 180);
    return () => { clearTimeout(timer); armed.current = false; cleanup(); };
  }, [active, visible, key, revision, fetchPage]);

  // First-page poll on the shared read-only cadence; merges stay in place.
  // Page Visibility gates the schedule: a hidden page stops polling, and the
  // effect re-run on return reads once immediately. An empty display or a
  // pending first-page rebuild reads as "first"; an armed (debounced) load
  // read takes precedence and the tick only keeps the cadence. Every tick
  // re-checks the document at its own invocation — the visibilitychange
  // cleanup can lag the actual hide, and a timer firing inside that gap must
  // not start a GET.
  useEffect(() => {
    if (!active || !visible) return;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      if (documentVisibleNow() && !armed.current) {
        const mode = pageRef.current.rows.length && !needsFirst.current ? "poll" : "first";
        await fetchPage(mode, false);
      }
      if (!stopped && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, visible, fetchPage]);

  // Freshest summaries win for display; committed order stays until applied.
  const rows = page.rows;
  return {
    rows, verifiedAtMs, totals: page.totals, nextCursor: page.nextCursor, loading, error, reorder,
    more: () => void fetchPage("more"),
    retry: () => void fetchPage(needsFirst.current || pageRef.current.rows.length === 0 ? "first" : "poll", true),
    reset: () => setRevision(value => value + 1),
    applyReorder,
  };
}
