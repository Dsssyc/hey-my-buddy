import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { useGlobalRefresh, waitForRead } from "./global-refresh";
import type { Task, TaskPage, TaskQuery } from "./types";

export function useTaskHistory(api: ConsoleApi, query: TaskQuery, active: boolean) {
  const [page, setPage] = useState<TaskPage>({ runs: [], total: 0, nextCursor: null });
  const [loading, setLoading] = useState(active), [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
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
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(errorText(failure));
      if (strict && !isAbortError(failure)) throw failure;
    } finally {
      if (version === generation.current) { pending.current = false; setLoading(false); }
    }
  }, [api]);
  useGlobalRefresh(() => fetchPage(false, true), active);
  const loadedKey = useRef<string | null>(null);
  useEffect(() => {
    ++generation.current; request.current?.abort(); pending.current = false; setLoading(false);
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    if (!active) return cleanup;
    if (loadedKey.current === key + revision && page.runs.length) return cleanup;
    loadedKey.current = key + revision;
    setPage({ runs: [], total: 0, nextCursor: null });
    setLoading(true);
    const timer = setTimeout(() => void fetchPage(false), 180);
    return () => { clearTimeout(timer); cleanup(); };
  }, [active, key, revision, fetchPage]);
  return { ...page, loading, error, more: () => fetchPage(true), retry: () => fetchPage(page.runs.length > 0),
    reset: () => setRevision(n => n + 1) };
}
export function mergeLiveTasks(history: Task[], latest: Task[]) {
  const byId = new Map(latest.map(t => [t.runId, t]));
  return history.map(t => byId.get(t.runId) || t);
}
