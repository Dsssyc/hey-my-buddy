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
  // Abort + cleanup prevents stale reads after unmount/StrictMode remount.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  // The poll also gates on Page Visibility: a hidden page stops reading and
  // stops scheduling (an in-flight read is left to finish once, bounded);
  // returning to the foreground re-runs this effect, which reads immediately.
  // The invocation-time document check closes the cleanup gap: a timer that
  // fires while the document is already hidden but the visibilitychange state
  // update has not landed yet must not start a GET. Explicit actions
  // (重新连接, header refresh) never gate on visibility.
  useEffect(() => {
    mounted.current = true;
    if (!visible) {
      return () => { mounted.current = false; };
    }
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      if (!documentVisibleNow()) return;
      await refresh(controller.signal);
      if (!controller.signal.aborted && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => {
      mounted.current = false;
      controller.abort();
      clearTimeout(timer);
    };
  }, [refresh, visible]);
  return { snapshot, error, updatedAt, refresh };
}
