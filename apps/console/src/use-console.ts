import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { Snapshot } from "./types";

export function useConsole(api: ConsoleApi) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const mounted = useRef(false),
    sequence = useRef(0);
  const initialRead = useRef(true);
  const visible = useDocumentVisible();
  const refresh = useCallback(
    async (signal?: AbortSignal, strict = false) => {
      const request = ++sequence.current;
      try {
        const next = await api.snapshot(signal);
        if (mounted.current && request === sequence.current) {
          setSnapshot(next);
          setError("");
          setUpdatedAt(Date.now());
        }
        return next;
      } catch (failure) {
        if (
          mounted.current &&
          request === sequence.current &&
          !isAbortError(failure)
        )
          setError(errorText(failure));
        if (strict && !isAbortError(failure)) throw failure;
        return null;
      }
    },
    [api],
  );
  // Each mount (including StrictMode effect replay) gets its bootstrap read.
  // Visibility changes only restart the scheduling effect below.
  useEffect(() => {
    mounted.current = true;
    initialRead.current = true;
    return () => { mounted.current = false; };
  }, [refresh]);
  // Abort + sequence cleanup prevents stale reads after unmount/StrictMode replay.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  // The first snapshot reads even on a hidden page. Subsequent polling gates
  // on Page Visibility: a hidden page stops reading and stops scheduling;
  // returning to the foreground re-runs this effect, which reads immediately.
  // The invocation-time document check closes the cleanup gap: a timer that
  // fires while the document is already hidden but the visibilitychange state
  // update has not landed yet must not start a GET. Explicit actions
  // (重新连接, header refresh) never gate on visibility.
  useEffect(() => {
    const first = initialRead.current;
    initialRead.current = false;
    if (!first && !visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async (bootstrap = false) => {
      if (!bootstrap && !documentVisibleNow()) return;
      await refresh(controller.signal);
      if (!controller.signal.aborted && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll(first);
    return () => {
      ++sequence.current;
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, visible]);
  return { snapshot, error, updatedAt, refresh };
}
