import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { useGlobalRefresh, waitForRead } from "./global-refresh";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { ObjectiveTimeline } from "./objective-types";

/**
 * Reads one work objective's timeline on the console's three-second cadence.
 * A failed read keeps the last good data (open tails stop extending because
 * the kept `observedAt` no longer advances) and surfaces an error with the
 * age of what is displayed; row order is preserved across merges and new
 * delegations are appended at the end, exactly as the read contract requires.
 */
export function useObjectiveTimeline(api: ConsoleApi, objectiveId: string | null, active: boolean) {
  const [data, setData] = useState<ObjectiveTimeline | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [newRunIds, setNewRunIds] = useState<ReadonlySet<string>>(() => new Set<string>());
  /**
   * The display clock: the server's verification time of the most recent
   * successful read. Every 200 or 304 names when the server last verified
   * this state (its HTTP `Date`, surfaced by the api as `verifiedAtMs`) — a
   * warm cached body may carry an older `observedAt`, so the clock must never
   * pretend the read created that old anchor. When a response carries no
   * date, the local clock at read completion stands in (the console is a
   * local surface). The value updates only on a successful read and freezes
   * on a failed one (the displayed data really is that old); the response's
   * own `observedAt` is never rewritten.
   */
  const [displayObservedAt, setDisplayObservedAt] = useState<string | null>(null);
  const anchor = useRef<{ verifiedAtMs: number; readAt: number } | null>(null);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible();
  const dataRef = useRef<ObjectiveTimeline | null>(null);
  dataRef.current = data;

  const read = useCallback(async (strict = false) => {
    if (!objectiveId) return;
    if (pending.current) {
      if (!strict) return;
      await waitForRead(() => pending.current);
    }
    const version = generation.current, controller = new AbortController();
    request.current = controller; pending.current = true;
    try {
      const { timeline: next, verifiedAtMs } = await api.objectiveTimeline(objectiveId, { limit: 200 }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      if (next.objective.objectiveId !== objectiveId) {
        setError("返回的工作目标标识与请求不符，未采用该数据。");
        return;
      }
      setError("");
      // Anchor on when the server last VERIFIED this state, not on when the
      // body's projection was generated: a warm cached 200 or a same-body 304
      // names a fresh verification instant even though `observedAt` still
      // carries the older source fact. Without a response date the local
      // clock at read completion stands in (justified: the console is local).
      const readAt = Date.now();
      const verifiedAt = verifiedAtMs ?? readAt;
      anchor.current = { verifiedAtMs: verifiedAt, readAt };
      setDisplayObservedAt(new Date(verifiedAt).toISOString());
      // Merge from the last accepted data before setData: the state updater may
      // be deferred by React, so new-row labels cannot depend on its output.
      const previous = dataRef.current;
      let rows = next.rows;
      let appended: string[] = [];
      if (previous && previous.objective.objectiveId === next.objective.objectiveId) {
        // The service sends tree order; keep committed row order and append new
        // delegations at the end so a refresh never moves a row under the reader.
        const byId = new Map(next.rows.map(row => [row.runId, row]));
        const known = new Set(previous.rows.map(row => row.runId));
        appended = next.rows.filter(row => !known.has(row.runId)).map(row => row.runId);
        rows = [
          ...previous.rows.map(row => byId.get(row.runId)).filter((row): row is NonNullable<typeof row> => row !== undefined),
          ...next.rows.filter(row => !known.has(row.runId)),
        ];
      }
      // A fresh selection marks nothing as new: the 新 label is only for rows
      // appended to an already displayed timeline.
      const merged = { ...next, rows };
      // Keep the ref in step at write time so a same-tick follow-up read merges
      // against this data rather than treating it as a selection change.
      dataRef.current = merged;
      setData(merged);
      setNewRunIds(new Set(appended));
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) pending.current = false;
    }
  }, [api, objectiveId]);
  useGlobalRefresh(() => read(true), active && !!objectiveId);

  // A new selection resets; a visibility toggle keeps the last good data and
  // just re-reads once on return, the same retention the task history keeps.
  // A hidden page starts no read: the first read of a selection waits for the
  // foreground, and returning re-runs this effect, which reads immediately.
  // A selection made while hidden still clears at once — the previous
  // objective's data must never pose as the new one's, so a slow or failed
  // read cannot leave the wrong timeline standing.
  const selectedRef = useRef(objectiveId);
  useEffect(() => {
    const changed = selectedRef.current !== objectiveId;
    selectedRef.current = objectiveId;
    ++generation.current; request.current?.abort(); pending.current = false;
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    if (!objectiveId) {
      setData(null); setError(""); setNewRunIds(new Set());
      anchor.current = null;
      setDisplayObservedAt(null);
      setLoading(false);
      return cleanup;
    }
    if (changed || dataRef.current === null) {
      setData(null); setError(""); setNewRunIds(new Set());
      anchor.current = null;
      setDisplayObservedAt(null);
      if (active && visible) setLoading(true);
    }
    if (!active || !visible) return cleanup;
    void read();
    return cleanup;
  }, [objectiveId, active, visible, read]);

  useEffect(() => {
    if (!objectiveId || !active || !visible) return;
    const timer = setInterval(() => {
      // Invocation-time document check: the visibilitychange cleanup can lag
      // the actual hide, and an interval firing inside that gap must not
      // start a GET.
      if (documentVisibleNow()) void read();
    }, 3000);
    return () => clearInterval(timer);
  }, [objectiveId, active, visible, read]);

  // "Loading" describes only the first read of a selection; polling is silent.
  useEffect(() => { if (!loading) return; if (data || error) setLoading(false); }, [data, error, loading]);

  return {
    timeline: data,
    observedAt: data?.observedAt ?? null,
    displayObservedAt,
    loading, error,
    stale: !!error && !!data,
    newRunIds,
    retry: read,
  };
}
