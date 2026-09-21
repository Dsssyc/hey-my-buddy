import { useCallback, useEffect, useRef, useState } from 'react';
import type { ConsoleApi } from './api';
import { errorText } from './api';
import type { Snapshot } from './types';

export function useConsole(api: ConsoleApi) {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const mounted = useRef(false), sequence = useRef(0);
  const refresh = useCallback(async (signal?: AbortSignal) => {
    const request = ++sequence.current;
    try {
      const next = await api.snapshot(signal);
      if (mounted.current && request === sequence.current) {
        setSnapshot(next); setError(''); setUpdatedAt(Date.now());
      }
      return next;
    } catch (failure) {
      if (mounted.current && request === sequence.current && !(failure instanceof Error && failure.name === 'AbortError')) setError(errorText(failure));
      return null;
    }
  }, [api]);
  // Abort + cleanup prevents stale reads after unmount/StrictMode remount.
  // https://react.dev/reference/react/useEffect#fetching-data-with-effects
  useEffect(() => {
    mounted.current = true;
    const controller = new AbortController(); let timer: ReturnType<typeof setTimeout>;
    const poll = async () => { await refresh(controller.signal); if (!controller.signal.aborted) timer = setTimeout(poll, 3000); };
    void poll();
    return () => { mounted.current = false; controller.abort(); clearTimeout(timer); };
  }, [refresh]);
  return { snapshot, error, updatedAt, refresh };
}
