import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import type { ObjectivePage, ObjectiveQuery, ObjectiveSummary } from "./objective-types";

type PageState = { rows: ObjectiveSummary[]; total: number; nextCursor: string | null };

/** Sort exactly like the service's default order: activity sequence, then identifier descending. */
function byActivity(left: ObjectiveSummary, right: ObjectiveSummary): number {
  return right.lastActivitySeq - left.lastActivitySeq || right.objectiveId.localeCompare(left.objectiveId);
}

/**
 * Keyset-paged work-objective list. Polls the first page on the console's
 * three-second cadence and merges in place, but never reorders silently: when
 * the newest read would change the display order (or add unseen groups), the
 * caller gets a `reorder` notice and applies it only on demand, so a reader's
 * selection never jumps.
 */
export function useObjectiveList(api: ConsoleApi, query: ObjectiveQuery, active: boolean) {
  const [page, setPage] = useState<PageState>({ rows: [], total: 0, nextCursor: null });
  const [latest, setLatest] = useState<Map<string, ObjectiveSummary>>(() => new Map());
  const [loading, setLoading] = useState(active), [error, setError] = useState("");
  const [reorder, setReorder] = useState<{ count: number | null } | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const pageRef = useRef(page), latestRef = useRef(latest);
  pageRef.current = page;
  latestRef.current = latest;
  const key = JSON.stringify(query);
  const current = useRef({ query, active, cursor: page.nextCursor });
  current.current = { query, active, cursor: page.nextCursor };

  // Applying the notice also rebuilds paging from a fresh first-page read, so
  // the stale keyset cursor never serves outdated pages afterwards.
  const applyReorder = useCallback(() => {
    setPage(previous => {
      const known = new Set(previous.rows.map(row => row.objectiveId));
      const merged = previous.rows.map(row => latestRef.current.get(row.objectiveId) ?? row);
      for (const [id, summary] of latestRef.current) if (!known.has(id)) merged.push(summary);
      merged.sort(byActivity);
      return { rows: merged, total: previous.total, nextCursor: previous.nextCursor };
    });
    setReorder(null);
    setRevision(value => value + 1);
  }, []);

  const fetchPage = useCallback(async (mode: "first" | "more" | "poll") => {
    if (!current.current.active || pending.current) return;
    if (mode === "more" && !current.current.cursor) return;
    const version = generation.current, controller = new AbortController();
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
        setLatest(mode === "first" ? new Map(next.objectives.map(row => [row.objectiveId, row])) : fresh);
        // Loading an unchanged older page never discards a pending reorder
        // notice; a paged read that reports `changed` still offers one because
        // activity above the first page is not visible in this reply.
        if (mode === "more") setReorder(existing => existing ?? (next.changed ? { count: null } : null));
        else setReorder(null);
      }
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
    } finally {
      if (version === generation.current) { pending.current = false; if (mode !== "poll") setLoading(false); }
    }
  }, [api]);

  const loadedKey = useRef<string | null>(null);
  useEffect(() => {
    ++generation.current; request.current?.abort(); pending.current = false; setLoading(false);
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    if (!active) return cleanup;
    if (loadedKey.current === key + revision && pageRef.current.rows.length) return cleanup;
    loadedKey.current = key + revision;
    setPage({ rows: [], total: 0, nextCursor: null });
    setLatest(new Map());
    setReorder(null);
    setLoading(true);
    const timer = setTimeout(() => void fetchPage("first"), 180);
    return () => { clearTimeout(timer); cleanup(); };
  }, [active, key, revision, fetchPage]);

  // First-page poll on the shared read-only cadence; merges stay in place.
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => void fetchPage("poll"), 3000);
    return () => clearInterval(timer);
  }, [active, fetchPage]);

  // Freshest summaries win for display; committed order stays until applied.
  const rows = page.rows.map(row => latest.get(row.objectiveId) ?? row);
  return {
    rows, total: page.total, nextCursor: page.nextCursor, loading, error, reorder,
    more: () => void fetchPage("more"),
    retry: () => void fetchPage(pageRef.current.rows.length > 0 ? "poll" : "first"),
    reset: () => setRevision(value => value + 1),
    applyReorder,
  };
}
