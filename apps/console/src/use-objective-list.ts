import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { waitForRead } from "./global-refresh";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { ObjectivePage, ObjectiveQuery, ObjectiveSummary } from "./objective-types";

type PageState = { rows: ObjectiveSummary[]; total: number; nextCursor: string | null };

/** Sort exactly like the service's default order: activity sequence, then identifier descending. */
function byActivity(left: ObjectiveSummary, right: ObjectiveSummary): number {
  return right.lastActivitySeq - left.lastActivitySeq || right.objectiveId.localeCompare(left.objectiveId);
}

/**
 * Keyset-paged work-objective list. Polls the first page on the console's
 * three-second cadence and merges in place. A changed order waits for the
 * visible list to decide whether the reader is idle or explicitly applies it.
 */
export function useObjectiveList(api: ConsoleApi, query: ObjectiveQuery, active: boolean) {
  const [page, setPage] = useState<PageState>({ rows: [], total: 0, nextCursor: null });
  const [latest, setLatest] = useState<Map<string, ObjectiveSummary>>(() => new Map());
  const [loading, setLoading] = useState(active), [error, setError] = useState("");
  const [reorder, setReorder] = useState<{ count: number | null } | null>(null);
  const [verifiedAtMs, setVerifiedAtMs] = useState<number | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible();
  const previousVisible = useRef(visible);
  const initialReadStarted = useRef(false);
  const pageRef = useRef(page), latestRef = useRef(latest);
  pageRef.current = page;
  latestRef.current = latest;
  const key = JSON.stringify(query);
  const current = useRef({ query, active, cursor: page.nextCursor });
  current.current = { query, active, cursor: page.nextCursor };

  const pendingNew = useRef<Map<string, ObjectiveSummary>>(new Map());
  const needsFirst = useRef(false);

  const fetchPage = useCallback(async (mode: "first" | "more" | "poll", strict = false) => {
    if (!current.current.active) return;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
      if (!current.current.active) return;
    }
    // A failed reorder refresh invalidated its cursor. The next automatic read
    // must rebuild the first page before normal polling/paging can resume.
    if (mode === "poll" && needsFirst.current) mode = "first";
    if (mode === "more" && !current.current.cursor) return;
    const version = generation.current, controller = new AbortController();
    initialReadStarted.current = true;
    request.current = controller; pending.current = true;
    if (mode !== "poll") { setLoading(true); setError(""); }
    try {
      const limit = mode === "poll"
        ? Math.max(50, Math.min(100, pageRef.current.rows.length || 50))
        : 50;
      const next: ObjectivePage = await api.objectives({
        ...current.current.query, limit,
        ...(mode === "more" ? { before: current.current.cursor! } : {}),
      }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      const checked = api.readVerifiedAt?.(next) ?? null;
      setVerifiedAtMs(previous => mode === "first" || mode === "poll" && next.nextCursor === null
        ? checked : previous === null || checked === null ? null : Math.min(previous, checked));
      setError("");
      // Both load modes merge into the existing freshest map, so a page load
      // never discards newer poll data for already-known rows.
      const fresh = new Map(latestRef.current);
      for (const summary of next.objectives) fresh.set(summary.objectiveId, summary);
      if (mode === "poll") {
        const previous = pageRef.current;
        // An empty display adopts the read directly (including its cursor);
        // nothing was on screen to reorder. Otherwise the freshest summaries
        // render in place while only a real order change — new activity moving
        // a group, or an unseen group appearing — defers behind the notice.
        if (!previous.rows.length) {
          pendingNew.current.clear();
          setLatest(fresh);
          setPage({ rows: [...next.objectives].sort(byActivity), total: next.total, nextCursor: next.nextCursor });
          return;
        }
        const known = new Set(previous.rows.map(row => row.objectiveId));
        const hasNew = next.objectives.some(row => !known.has(row.objectiveId));
        // A complete filtered first page proves the whole matching scope: rows
        // it no longer returns have stopped matching and leave the list (an
        // open detail stays mounted on the workspace's summary fallbacks). An
        // incomplete page cannot prove absence, so it keeps the refresh notice
        // instead of guessing.
        const complete = next.nextCursor === null;
        const returned = new Set(next.objectives.map(row => row.objectiveId));
        const droppedAmbiguous = complete ? [] : previous.rows.filter(row => {
          if (returned.has(row.objectiveId)) return false;
          const floor = next.objectives.length ? next.objectives[next.objectives.length - 1]!.lastActivitySeq : -Infinity;
          return row.lastActivitySeq >= floor;
        });
        // Completeness proves membership, not permission to reorder. Preserve
        // the surviving committed order; newcomers wait behind the notice.
        const retained = previous.rows
          .filter(row => !complete || returned.has(row.objectiveId))
          .map(row => fresh.get(row.objectiveId) ?? row);
        const merged = [...retained, ...next.objectives.filter(row => !known.has(row.objectiveId))];
        if (complete) pendingNew.current.clear();
        for (const row of next.objectives) if (!known.has(row.objectiveId)) pendingNew.current.set(row.objectiveId, row);
        const sorted = [...merged].sort(byActivity);
        const orderChanged = hasNew || sorted.some((row, index) => merged[index]!.objectiveId !== row.objectiveId);
        const changedCount = merged.filter(row => {
          const committed = previous.rows.find(candidate => candidate.objectiveId === row.objectiveId);
          return committed === undefined || row.lastActivitySeq > committed.lastActivitySeq;
        }).length;
        setLatest(fresh);
        // The committed order stays behind the notice, but the reported total
        // always reflects the newest read.
        setPage(previous => ({ rows: retained, total: next.total, nextCursor: complete ? null : previous.nextCursor }));
        if (orderChanged) setReorder(existing => changedCount > 0 ? { count: changedCount } : existing ?? { count: null });
        else if (next.changed || droppedAmbiguous.length > 0) setReorder(existing => existing ?? { count: null });
      } else {
        const known = new Set(mode === "more" ? pageRef.current.rows.map(row => row.objectiveId) : []);
        const rows = mode === "more"
          ? [...pageRef.current.rows.map(row => fresh.get(row.objectiveId) ?? row),
            ...next.objectives.filter(row => !known.has(row.objectiveId)).map(row => fresh.get(row.objectiveId) ?? row)]
          : [...next.objectives].sort(byActivity);
        setPage({ rows, total: next.total, nextCursor: next.nextCursor });
        if (mode === "first") { pendingNew.current.clear(); needsFirst.current = false; }
        setLatest(mode === "first" ? new Map(next.objectives.map(row => [row.objectiveId, row])) : fresh);
        // Loading an unchanged older page never discards a pending reorder
        // notice; a paged read that reports `changed` still offers one because
        // activity above the first page is not visible in this reply.
        if (mode === "more") setReorder(existing => existing ?? (next.changed ? { count: null } : null));
        else setReorder(null);
      }
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) { pending.current = false; if (mode !== "poll") setLoading(false); }
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
      const known = new Set(previous.rows.map(row => row.objectiveId));
      const merged = previous.rows.map(row => latestRef.current.get(row.objectiveId) ?? row);
      for (const [id, summary] of pendingNew.current) if (!known.has(id)) merged.push(summary);
      return { rows: merged.sort(byActivity), total: previous.total, nextCursor: null };
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
    setPage({ rows: [], total: 0, nextCursor: null });
    pendingNew.current.clear();
    needsFirst.current = false;
    setLatest(new Map());
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
        await fetchPage(mode);
      }
      if (!stopped && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, visible, fetchPage]);

  // Freshest summaries win for display; committed order stays until applied.
  const rows = page.rows.map(row => latest.get(row.objectiveId) ?? row);
  return {
    rows, verifiedAtMs, total: page.total, nextCursor: page.nextCursor, loading, error, reorder,
    more: () => void fetchPage("more"),
    retry: () => void fetchPage(needsFirst.current || pageRef.current.rows.length === 0 ? "first" : "poll"),
    reset: () => setRevision(value => value + 1),
    applyReorder,
  };
}
