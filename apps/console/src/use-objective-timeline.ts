import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
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
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const dataRef = useRef<ObjectiveTimeline | null>(null);
  dataRef.current = data;

  const read = useCallback(async () => {
    if (!objectiveId || pending.current) return;
    const version = generation.current, controller = new AbortController();
    request.current = controller; pending.current = true;
    try {
      const next = await api.objectiveTimeline(objectiveId, { limit: 200 }, controller.signal);
      if (version !== generation.current || controller.signal.aborted) return;
      setError("");
      let appended: string[] = [];
      setData(previous => {
        if (!previous || previous.objective.objectiveId !== next.objective.objectiveId) {
          appended = next.rows.map(row => row.runId);
          return next;
        }
        // The service sends tree order; keep committed row order and append new
        // delegations at the end so a refresh never moves a row under the reader.
        const byId = new Map(next.rows.map(row => [row.runId, row]));
        const known = new Set(previous.rows.map(row => row.runId));
        appended = next.rows.filter(row => !known.has(row.runId)).map(row => row.runId);
        const rows = [
          ...previous.rows.map(row => byId.get(row.runId)).filter((row): row is NonNullable<typeof row> => row !== undefined),
          ...next.rows.filter(row => !known.has(row.runId)),
        ];
        return { ...next, rows };
      });
      setNewRunIds(new Set(appended));
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
    } finally {
      if (version === generation.current) pending.current = false;
    }
  }, [api, objectiveId]);

  // A new selection resets; a visibility toggle keeps the last good data and
  // just re-reads once on return, the same retention the task history keeps.
  const selectedRef = useRef(objectiveId);
  useEffect(() => {
    const changed = selectedRef.current !== objectiveId;
    selectedRef.current = objectiveId;
    ++generation.current; request.current?.abort(); pending.current = false;
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    if (!objectiveId) {
      setData(null); setError(""); setNewRunIds(new Set());
      setLoading(false);
      return cleanup;
    }
    if (!active) return cleanup;
    if (changed || dataRef.current === null) {
      setData(null); setError(""); setNewRunIds(new Set());
      setLoading(true);
    }
    void read();
    return cleanup;
  }, [objectiveId, active, read]);

  useEffect(() => {
    if (!objectiveId || !active) return;
    const timer = setInterval(() => void read(), 3000);
    return () => clearInterval(timer);
  }, [objectiveId, active, read]);

  // "Loading" describes only the first read of a selection; polling is silent.
  useEffect(() => { if (!loading) return; if (data || error) setLoading(false); }, [data, error, loading]);

  return {
    timeline: data,
    observedAt: data?.observedAt ?? null,
    loading, error,
    stale: !!error && !!data,
    newRunIds,
    retry: read,
  };
}
