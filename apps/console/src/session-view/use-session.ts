import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "../api";
import { isAbortError } from "../api";
import { parseSession, records } from "./types";
import type { RecordedEvent, SessionPage } from "./types";

export const SESSION_PAGE_SIZE = 256;
export type SessionClient = Pick<ConsoleApi, "session">;
export type SessionData = Omit<SessionPage, "events"> & { events: RecordedEvent[]; change: "initial" | "earlier" | "later" };

/** Owns all in-flight reads of a visible panel; hiding or disposing it cancels them. */
export function useSession(client: SessionClient, taskId: string, attemptId: string, active: boolean) {
  const [data, setData] = useState<SessionData | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [earlierError, setEarlierError] = useState<unknown>(null);
  const [earlierBusy, setEarlierBusy] = useState(false);
  const [generation, setGeneration] = useState(0);
  const latest = useRef<SessionData | null>(null);
  const requests = useRef(new Set<AbortController>());
  const visible = useRef(false);
  const source = useRef<typeof read | null>(null);
  const earlierPending = useRef(false);
  const update = (transform: (previous: SessionData | null) => SessionData) => {
    latest.current = transform(latest.current);
    setData(latest.current);
  };
  const read = useCallback(async (params: { before?: number; after?: number }, controller: AbortController) =>
    parseSession(await client.session(taskId, attemptId, { ...params, limit: SESSION_PAGE_SIZE }, controller.signal)), [client, taskId, attemptId]);

  useEffect(() => {
    if (source.current !== read) {
      source.current = read;
      latest.current = null;
      setData(null);
      setError(null);
      setEarlierError(null);
    }
    if (!active) return;
    earlierPending.current = false;
    setEarlierBusy(false);
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;
    let delay = 250;
    let forward: AbortController | null = null;
    async function poll() {
      if (latest.current?.state === "ended") return;
      const controller = new AbortController();
      let failed = false;
      let received = false;
      let caughtUp = false;
      let ended = false;
      forward = controller;
      requests.current.add(controller);
      try {
        let cursor = latest.current?.after;
        if (cursor === undefined) {
          const page = await read({}, controller);
          if (controller.signal.aborted) return;
          ended = page.state === "ended";
          const startsMidCall = page.events[0]?.update.sessionUpdate === "tool_call_update";
          const earlier = startsMidCall && page.hasEarlier ? await read({ before: page.before }, controller).catch(() => null) : null;
          if (controller.signal.aborted) return;
          update(() => ({ ...page, before: earlier?.before ?? page.before, hasEarlier: earlier?.hasEarlier ?? page.hasEarlier,
            events: earlier ? [...records(earlier), ...records(page)] : records(page), change: "initial" }));
          return;
        }
        while (!controller.signal.aborted) {
          const page = await read({ after: cursor }, controller);
          if (controller.signal.aborted) return;
          update(previous => ({ ...previous!, state: page.events.length < SESSION_PAGE_SIZE ? page.state : "running", stopped: page.stopped, coverage: page.coverage, after: page.after,
            events: page.events.length ? [...previous!.events, ...records(page)] : previous!.events, change: "later" }));
          received ||= page.events.length > 0;
          cursor = page.after;
          if (page.events.length < SESSION_PAGE_SIZE) {
            caughtUp = true;
            ended = page.state === "ended";
            break;
          }
        }
      } catch (failure) {
        if (!controller.signal.aborted && !isAbortError(failure)) { failed = true; setError(failure); }
      } finally {
        requests.current.delete(controller);
        if (forward === controller) forward = null;
        if (caughtUp) delay = received ? 250 : Math.min(1000, delay + 250);
        if (!stopped && !failed && !controller.signal.aborted && visible.current && !ended) {
          timer = setTimeout(() => void poll(), delay);
        }
      }
    }
    function visibility() {
      visible.current = document.visibilityState === "visible";
      clearTimeout(timer);
      if (!visible.current) {
        for (const controller of requests.current) controller.abort();
        forward = null;
        earlierPending.current = false;
        setEarlierBusy(false);
      } else if (!forward) void poll();
    }
    visibility();
    document.addEventListener("visibilitychange", visibility);
    return () => {
      stopped = true;
      visible.current = false;
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibility);
      for (const controller of requests.current) controller.abort();
      requests.current.clear();
    };
  }, [active, read, generation]);

  const earlier = useCallback(async () => {
    if (!visible.current || earlierPending.current || !latest.current?.hasEarlier) return;
    earlierPending.current = true;
    setEarlierError(null);
    setEarlierBusy(true);
    const controller = new AbortController();
    requests.current.add(controller);
    try {
      const page = await read({ before: latest.current.before }, controller);
      if (!controller.signal.aborted) update(previous => ({ ...previous!, before: page.before, hasEarlier: page.hasEarlier,
        events: [...records(page), ...previous!.events], change: "earlier" }));
    } catch (failure) {
      if (!controller.signal.aborted && !isAbortError(failure)) setEarlierError(failure);
    } finally {
      requests.current.delete(controller);
      if (!controller.signal.aborted) { earlierPending.current = false; setEarlierBusy(false); }
    }
  }, [read]);
  const retryEarlier = useCallback(() => { void earlier(); }, [earlier]);
  const retry = () => { setError(null); setGeneration(value => value + 1); };
  return { data, error, earlierError, earlierBusy, earlier, retryEarlier, retry };
}
