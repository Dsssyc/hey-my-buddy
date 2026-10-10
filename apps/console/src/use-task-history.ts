import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { waitForRead } from "./global-refresh";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { Task, TaskPage, TaskQuery } from "./types";

function oldestVerification(left: number | null, right: number | null): number | null {
  return left === null || right === null ? null : Math.min(left, right);
}

/** The poll window of the records view's own first page; never a snapshot page. */
const POLL_LIMIT = 50;
/**
 * The view's memory of rows it has itself read, bounded like the old snapshot
 * window. Only the filter dropdowns read it, so choices survive a scope
 * change while the displayed rows reload; the list itself never renders it.
 */
const KNOWN_LIMIT = 100;

type FailedRead = { kind: "first" | "append" | "poll"; scope: string; before?: string };

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
  const [verifiedAtMs, setVerifiedAtMs] = useState<number | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible();
  const previousVisible = useRef(visible);
  const initialReadStarted = useRef(false);
  const pageRef = useRef(page);
  pageRef.current = page;
  const key = JSON.stringify(query);
  const current = useRef({ query, active, cursor: page.nextCursor, scope: key + revision });
  current.current = { query, active, cursor: page.nextCursor, scope: key + revision };
  const failedRead = useRef<FailedRead | null>(null);
  const clearReadFailure = (kind: FailedRead["kind"]) => {
    // A first-page poll cannot prove that an unread append page succeeded.
    if (kind === "poll" && failedRead.current?.kind === "append") return;
    failedRead.current = null;
    setError("");
  };
  // Ignore superseded responses when filters change; abort on unmount/hide.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  const fetchPage = useCallback(async (append: boolean, strict = false, before?: string) => {
    const cursor = append ? before ?? current.current.cursor : null;
    if (!current.current.active || (append && !cursor)) return;
    const readScope = current.current.scope;
    const scope = generation.current;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
      if (!current.current.active || scope !== generation.current) return;
    }
    const version = generation.current, controller = new AbortController();
    initialReadStarted.current = true;
    request.current = controller; pending.current = true; setLoading(true); setError("");
    try {
      const next = await api.tasks({ ...current.current.query, limit: 50, ...(append ? { before: cursor! } : {}) }, controller.signal);
      if (version !== generation.current || readScope !== current.current.scope || controller.signal.aborted) return;
      clearReadFailure(append ? "append" : "first");
      const checked = api.readVerifiedAt?.(next) ?? null;
      setVerifiedAtMs(previous => append ? oldestVerification(previous, checked) : checked);
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
      if (version === generation.current && readScope === current.current.scope && !controller.signal.aborted && !isAbortError(failure)) {
        failedRead.current = { kind: append ? "append" : "first", scope: readScope, ...(append ? { before: cursor! } : {}) };
        setError(errorText(failure));
      }
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
    const readScope = current.current.scope;
    const scope = generation.current;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
      if (!current.current.active || scope !== generation.current) return;
    }
    const version = generation.current, controller = new AbortController();
    request.current = controller; pending.current = true;
    try {
      const next = await api.tasks({ ...current.current.query, limit: POLL_LIMIT }, controller.signal);
      if (version !== generation.current || readScope !== current.current.scope || controller.signal.aborted) return;
      clearReadFailure("poll");
      let checked = api.readVerifiedAt?.(next) ?? null;
      const updates = new Map(next.runs.map(row => [row.runId, row]));
      const loaded = new Set(pageRef.current.runs.map(row => row.runId));
      const evictProven = () => {
        setVerifiedAtMs(checked);
        setPage(previous => ({
        // A proven poll covers the whole loaded window: rows the matching
        // scope no longer returns have stopped matching (left the 等待 Host
        // or 进行中 filter, however deep they were paginated) and leave the
        // list in their committed order.
        runs: previous.runs.flatMap(row => updates.get(row.runId) ?? []),
        total: next.total,
        nextCursor: previous.nextCursor,
      }));
      };
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
      setVerifiedAtMs(previous => oldestVerification(previous, checked));
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
        if (version !== generation.current || readScope !== current.current.scope || controller.signal.aborted) return;
        checked = oldestVerification(checked, api.readVerifiedAt?.(continuation) ?? null);
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
      if (version === generation.current && readScope === current.current.scope && !controller.signal.aborted && !isAbortError(failure)) {
        failedRead.current = { kind: "poll", scope: readScope };
        setError(errorText(failure));
      }
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) { pending.current = false; }
    }
  }, [api]);
  const loadedKey = useRef<string | null>(null);
  // True while the load effect has an armed (debounced) first-page read; the
  // cadence effect stands down until it fires, so a mount or scope change
  // never performs the first read twice.
  const armed = useRef(false);
  const resetScope = () => {
    failedRead.current = null; setError("");
    setPage({ runs: [], total: 0, nextCursor: null });
    setNewIds(new Set());
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
    if (sameScope && pageRef.current.runs.length) return cleanup;
    // Consume the exception when a request starts, not when an effect arms:
    // StrictMode cleanup must leave the replacement debounce a first read.
    const initialRead = !initialReadStarted.current;
    if (!visible && !initialRead) return cleanup;
    if (sameScope) resetScope();
    // The cadence effect supplies the single immediate read on return,
    // including an empty result or a query invalidated while hidden.
    if (becameVisible) return cleanup;
    setLoading(true);
    armed.current = true;
    const timer = setTimeout(() => {
      armed.current = false;
      // Initialization alone may ignore visibility. Later scope debounces
      // still re-check it if the document hides before cleanup lands.
      if (initialRead || documentVisibleNow()) void fetchPage(false);
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
  const refresh = async () => {
    if (refreshing || !current.current.active) return;
    const scope = generation.current;
    const readScope = current.current.scope;
    const kind = pageRef.current.runs.length ? "poll" : "first";
    setRefreshing(true);
    try {
      if (pageRef.current.runs.length) await pollLatest(true);
      else await fetchPage(false, true);
    } catch (failure) {
      if (scope === generation.current && readScope === current.current.scope && !isAbortError(failure)) {
        failedRead.current = { kind, scope: readScope };
        setError(errorText(failure));
      }
    } finally { setRefreshing(false); }
  };
  const retry = () => {
    const failed = failedRead.current;
    if (!failed || failed.scope !== current.current.scope || !current.current.active) return;
    if (failed.kind === "poll") return pollLatest();
    return fetchPage(failed.kind === "append", false, failed.before);
  };
  return { ...page, newIds, known, loading, error, verifiedAtMs, refreshing, refresh, more: () => fetchPage(true), retry,
    reset: () => setRevision(n => n + 1) };
}
