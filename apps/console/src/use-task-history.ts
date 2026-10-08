import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { useGlobalRefresh, waitForRead } from "./global-refresh";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { Task, TaskPage, TaskQuery } from "./types";

/** The poll window of the records view's own first page; never a snapshot page. */
const POLL_LIMIT = 50;
/**
 * The view's memory of rows it has itself read, bounded like the old snapshot
 * window. Only the filter dropdowns read it, so choices survive a scope
 * change while the displayed rows reload; the list itself never renders it.
 */
const KNOWN_LIMIT = 100;

/**
 * True when `row` sits at or past `floor` in the records route's own keyset
 * order (createdAt descending, then task id descending — the cursor's
 * documented ordering), so a walk page ending here has enumerated every row
 * of the loaded window down to its oldest member.
 */
function atOrPastFloor(row: Task, floor: Task): boolean {
  if (row.createdAt !== floor.createdAt) return row.createdAt < floor.createdAt;
  const rowId = typeof row.taskId === "string" ? row.taskId : row.runId;
  const floorId = typeof floor.taskId === "string" ? floor.taskId : floor.runId;
  return rowId <= floorId;
}

function mergeKnown(previous: Task[], rows: Task[]): Task[] {
  if (!rows.length) return previous;
  // Newest reads first: once the bounded memory is full, the facts a fresh
  // page brought must not be the entries that get dropped — otherwise new
  // projects and hosts would never reach the filter choices.
  const byId = new Map<string, Task>();
  for (const row of rows) byId.set(row.runId, row);
  for (const row of previous) if (!byId.has(row.runId)) byId.set(row.runId, row);
  return [...byId.values()].slice(0, KNOWN_LIMIT);
}

export function useTaskHistory(api: ConsoleApi, query: TaskQuery, active: boolean) {
  const [page, setPage] = useState<TaskPage>({ runs: [], total: 0, nextCursor: null });
  const [newIds, setNewIds] = useState<ReadonlySet<string>>(() => new Set());
  const [known, setKnown] = useState<Task[]>([]);
  const [loading, setLoading] = useState(active), [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible();
  const pageRef = useRef(page);
  pageRef.current = page;
  const key = JSON.stringify(query);
  const current = useRef({ query, active, cursor: page.nextCursor });
  current.current = { query, active, cursor: page.nextCursor };
  // Ignore superseded responses when filters change; abort on unmount/hide.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  const fetchPage = useCallback(async (append: boolean, strict = false) => {
    if (!current.current.active || (append && !current.current.cursor)) return;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
      if (!current.current.active) return;
    }
    const version = generation.current, controller = new AbortController();
    request.current = controller; pending.current = true; setLoading(true); setError("");
    try {
      const next = await api.tasks({ ...current.current.query, limit: 50, ...(append ? { before: current.current.cursor! } : {}) }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      setKnown(previous => mergeKnown(previous, next.runs));
      setPage(previous => {
        const rows = append ? [...previous.runs] : [];
        const known = new Map(rows.map((t, i) => [t.runId, i]));
        for (const row of next.runs) {
          const index = known.get(row.runId);
          if (index === undefined) { known.set(row.runId, rows.length); rows.push(row); }
          else rows[index] = row;
        }
        return { ...next, runs: rows };
      });
      // The loaded pages are the whole truth here: a read that lands clears any
      // pending 回到最新 notice it has just overtaken.
      if (!append) setNewIds(new Set());
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) { pending.current = false; setLoading(false); }
    }
  }, [api]);
  /**
   * The records view's own bounded first-page poll: the newest matching page
   * read from the paginated records route alone (a conditional GET, so an
   * unchanged poll costs a 304). It refreshes loaded rows in place, carries
   * the live total and detects matching records the loaded history has not
   * seen yet for the 回到最新 notice. No other route's rows are ever merged.
   */
  const pollLatest = useCallback(async (strict = false) => {
    if (!current.current.active) return;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
      if (!current.current.active) return;
    }
    const version = generation.current, controller = new AbortController();
    request.current = controller; pending.current = true;
    try {
      const next = await api.tasks({ ...current.current.query, limit: POLL_LIMIT }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      setError("");
      const updates = new Map(next.runs.map(row => [row.runId, row]));
      const loaded = new Set(pageRef.current.runs.map(row => row.runId));
      const evictProven = () => setPage(previous => ({
        // A proven poll covers the whole loaded window: rows the matching
        // scope no longer returns have stopped matching (left the 等待 Host
        // or 进行中 filter, however deep they were paginated) and leave the
        // list in their committed order.
        runs: previous.runs.flatMap(row => updates.get(row.runId) ?? []),
        total: next.total,
        nextCursor: previous.nextCursor,
      }));
      // The first page publishes immediately — in-place refresh, live total,
      // choices memory and the 回到最新 notice — so a continuation walk can
      // never delay what the page shows.
      setKnown(previous => mergeKnown(previous, next.runs));
      const firstFresh = next.runs.filter(row => !loaded.has(row.runId));
      if (firstFresh.length) setNewIds(previous => new Set([...previous, ...firstFresh.map(row => row.runId)]));
      const floor = pageRef.current.runs[pageRef.current.runs.length - 1];
      const lastOfFirstPage = next.runs[next.runs.length - 1];
      let proven = next.nextCursor === null
        || (floor !== undefined && lastOfFirstPage !== undefined && atOrPastFloor(lastOfFirstPage, floor));
      if (proven) {
        // A complete page proves the whole matching scope of this view's own
        // records route: rows it no longer returns have stopped matching and
        // leave the list.
        evictProven();
        return;
      }
      setPage(previous => ({
        runs: previous.runs.map(row => updates.get(row.runId) ?? row),
        total: next.total,
        nextCursor: previous.nextCursor,
      }));
      // The first page ends short of the loaded window, so it cannot prove
      // absence by itself: walk this poll's own cursor chain — through the
      // same paginated records route, never a snapshot — until the walk
      // reaches the loaded window's floor: its oldest committed row in the
      // route's own keyset order (createdAt descending, then task id
      // descending — the cursor's documented ordering). Once a continuation's
      // last row sits at or past that floor, every still-matching loaded
      // member has been observed and absence is proven; a matching scope that
      // ends first proves it just the same. The walk is bounded by the loaded
      // window (ceil(loaded / 50) + 2 pages), never an entire-database read —
      // hundreds of unloaded matches below the window cost nothing. When the
      // bound runs out before the floor (a huge new-top flood), or the page
      // hides mid-walk, the just-refreshed loaded rows are kept: no proof, no
      // eviction.
      let covered = next.runs.length;
      let cursor = next.nextCursor;
      let guard = Math.ceil(pageRef.current.runs.length / POLL_LIMIT) + 2;
      while (!proven && cursor && floor && guard > 0) {
        // No continuation GET while the document is hidden: the first reply
        // may have arrived inside the visibility-event cleanup gap, and the
        // honest in-place refresh already on screen is preserved as-is.
        if (!documentVisibleNow()) break;
        const continuation = await api.tasks({ ...current.current.query, limit: POLL_LIMIT, before: cursor }, controller.signal);
        if (version !== generation.current || controller.signal.aborted) return;
        for (const row of continuation.runs) updates.set(row.runId, row);
        covered += continuation.runs.length;
        const last = continuation.runs[continuation.runs.length - 1];
        if (continuation.nextCursor === null || (last !== undefined && atOrPastFloor(last, floor))) proven = true;
        cursor = continuation.nextCursor;
        guard -= 1;
      }
      const walkedFresh = [...updates.values()].filter(row => !loaded.has(row.runId));
      if (walkedFresh.length) setNewIds(previous => new Set([...previous, ...walkedFresh.map(row => row.runId)]));
      setKnown(previous => mergeKnown(previous, [...updates.values()]));
      if (proven) evictProven();
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) { pending.current = false; }
    }
  }, [api]);
  useGlobalRefresh(() => fetchPage(false, true), active);
  const loadedKey = useRef<string | null>(null);
  // True while the load effect has an armed (debounced) first-page read; the
  // cadence effect stands down until it fires, so a mount or scope change
  // never performs the first read twice.
  const armed = useRef(false);
  const resetScope = () => {
    setPage({ runs: [], total: 0, nextCursor: null });
    setNewIds(new Set());
  };
  useEffect(() => {
    ++generation.current; request.current?.abort(); pending.current = false; setLoading(false);
    armed.current = false;
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    if (!active) return cleanup;
    const sameScope = loadedKey.current === key + revision;
    if (!sameScope) {
      // A scope change invalidates the previous answer no matter the
      // visibility: rows and cursor of the old range must never pose as the
      // new query's result while the page is hidden. Becoming visible re-runs
      // this effect and loads the new first page then.
      loadedKey.current = key + revision;
      resetScope();
      if (!visible) return cleanup;
      setLoading(true);
      armed.current = true;
      const timer = setTimeout(() => {
        armed.current = false;
        // First mount / scope change also debounces: re-check the document at
        // fire time — hiding before the visibilitychange cleanup lands must
        // not produce this automatic GET.
        if (documentVisibleNow()) void fetchPage(false);
      }, 180);
      return () => { clearTimeout(timer); armed.current = false; cleanup(); };
    }
    // Same scope: legitimately read rows and their pagination survive the
    // visibility toggle (a hidden page is not an unmount).
    if (pageRef.current.runs.length) return cleanup;
    // No rows yet (a mount while hidden, or a failed load): a hidden page
    // starts no read, and becoming visible re-runs this effect and loads then.
    if (!visible) return cleanup;
    resetScope();
    setLoading(true);
    armed.current = true;
    const timer = setTimeout(() => {
      armed.current = false;
      // The document may have hidden inside the debounce window (before the
      // visibilitychange state update landed): re-check at fire time.
      if (documentVisibleNow()) void fetchPage(false);
    }, 180);
    return () => { clearTimeout(timer); armed.current = false; cleanup(); };
  }, [active, visible, key, revision, fetchPage]);
  // The records first-page poll: the shared read-only cadence, gated by Page
  // Visibility like every scheduled read — a hidden page stops polling and the
  // effect re-run on return reads once immediately. An empty display reads as
  // a first page; an armed (debounced) load read takes precedence and the
  // tick only keeps the cadence. Every tick re-checks the document at its own
  // invocation: the visibilitychange cleanup can lag the actual hide, and a
  // timer firing inside that gap must not start a GET.
  useEffect(() => {
    if (!active || !visible) return;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      if (documentVisibleNow() && !armed.current) {
        if (pageRef.current.runs.length) await pollLatest();
        else await fetchPage(false);
      }
      if (!stopped && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, visible, pollLatest, fetchPage]);
  return { ...page, newIds, known, loading, error, more: () => fetchPage(true), retry: () => fetchPage(page.runs.length > 0),
    reset: () => setRevision(n => n + 1) };
}
