import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import type { BackupPreflight } from "./types";

/**
 * The on-demand backup preflight. It reads the service's own
 * `/api/backup-preflight` only when the storage panel is open — one read per
 * open — and again on the explicit 重新读取 action, which omits the
 * conditional validator so the server's fresh walk is actually transferred.
 * A closed panel never reads and nothing here polls: the periodic snapshot
 * no longer carries the report, so the banner is always fed by the last read
 * that actually happened.
 *
 * Closing the panel (or unmounting) invalidates and aborts a read still in
 * the air, so a stale report can neither surface after the panel closed nor
 * survive a reopen that already started its own read. A development-mode
 * StrictMode double effect therefore stays bounded: the first read is
 * aborted before it can publish; React's production build never performs the
 * double invocation, and no production claim rests on these tests.
 */
export function useBackupPreflight(api: ConsoleApi, active: boolean) {
  const [report, setReport] = useState<BackupPreflight | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const run = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const read = useCallback(async (refresh = false) => {
    const id = ++run.current;
    controller.current?.abort();
    const local = new AbortController();
    controller.current = local;
    setLoading(true);
    try {
      const next = await api.backupPreflight(local.signal, { refresh });
      if (id !== run.current || local.signal.aborted) return null;
      setReport(next);
      setError("");
      return next;
    } catch (failure) {
      if (id !== run.current || local.signal.aborted || isAbortError(failure)) return null;
      setError(errorText(failure));
      return null;
    } finally {
      if (id === run.current && !local.signal.aborted) setLoading(false);
    }
  }, [api]);
  useEffect(() => {
    if (!active) return;
    void read(false);
    return () => {
      // A read overtaken by closing (or re-entering) the panel loses its
      // publication right before its reply could land.
      ++run.current;
      controller.current?.abort();
      setLoading(false);
    };
  }, [active, read]);
  return { report, error, loading, recheck: () => void read(true) };
}
