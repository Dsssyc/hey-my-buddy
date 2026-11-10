import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { useTranslation } from "react-i18next";
import { ArrowDown } from "lucide-react";
import { useStickToBottom } from "use-stick-to-bottom";
import { ApiError } from "../api";
import { Button } from "../components/ui/button";
import { ScrollArea } from "../components/ui/scroll-area";
import { sessionRows } from "./rows";
import { ReasonMessage } from "../i18n/ReasonMessage";
import { Message } from "../i18n/Message";
import { sessionBlocks } from "./blocks";
import { SessionBlocks } from "./SessionBlocks";
import { useSession } from "./use-session";
import { SessionCoverage } from "./SessionCoverage";
import type { SessionClient } from "./use-session";

export type SessionPanelProps = {
  routingMode?: "fast" | "review" | null; taskText: string; checkoutRoot: string; client: SessionClient; taskId: string; attemptId: string; harness: string;
  active: boolean;
  /** The right end of the turn tab row, where the session coverage control sits. */
  tabsSide?: HTMLSpanElement | null;
};

/** One read-only attempt. Navigation and persistence are owned by the enclosing shell. */
export default function SessionPanel({ routingMode, taskText, checkoutRoot, client, taskId, attemptId, harness, active, tabsSide }: SessionPanelProps) {
  const { t } = useTranslation("taskDetail");
  const { data, error, earlierBusy, earlier, retry } = useSession(client, taskId, attemptId, active);
  // Keep the existing immediate motion for initial positioning, streaming and
  // explicit return to the end, including prefers-reduced-motion.
  const { scrollRef, contentRef, scrollToBottom, stopScroll, isAtBottom, state } = useStickToBottom({ initial: "instant", resize: "instant" });
  // Wait for the library's initial scroll to finish before observing history.
  // A transient near-bottom signal can be missed if the reader immediately
  // scrolls up, so readiness follows the scroll promise, not the bottom state.
  const [positioned, setPositioned] = useState(false);
  useEffect(() => {
    if (!data || error) return;
    let mounted = true;
    void Promise.resolve(scrollToBottom("instant")).then(() => { if (mounted) setPositioned(true); });
    return () => { mounted = false; };
  }, [data !== null && !error, scrollToBottom]);
  const sentinel = useRef<HTMLDivElement>(null);
  const prependEnd = useRef<number | null>(null);
  const [newContent, setNewContent] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const previousAfter = useRef<number | null>(null);
  const rows = useMemo(() => sessionRows(sessionBlocks(data?.events ?? [])), [data?.events]);
  useLayoutEffect(() => {
    if (!active || !data) return;
    if (data.change === "earlier" && prependEnd.current !== null) {
      const body = contentRef.current!.querySelector<HTMLElement>('[data-slot="session-blocks"]')!;
      scrollRef.current!.scrollTop += body.getBoundingClientRect().bottom - prependEnd.current;
      prependEnd.current = null;
    }
    if (previousAfter.current !== null && data.after > previousAfter.current && !isAtBottom) setNewContent(true);
    previousAfter.current = data.after;
  }, [data, active, isAtBottom, contentRef, scrollRef]);
  useEffect(() => { if (isAtBottom) setNewContent(false); }, [isAtBottom]);
  // Reaching the top of the transcript reads the next earlier page. The sentinel
  // exists only while an earlier page can be read, so a session with none never
  // touches the reading position; the observer is rebuilt once per prepend, so a
  // sentinel that stays in view keeps reading.
  useEffect(() => {
    const trigger = sentinel.current;
    const node = scrollRef.current;
    if (!trigger || !node) return;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      // Native anchoring does not preserve a prepend at scrollTop 0, and
      // merging earlier calls can replace the visible group's DOM identity.
      // Anchor only the transcript end; the library owns following additions.
      const body = contentRef.current!.querySelector<HTMLElement>('[data-slot="session-blocks"]');
      prependEnd.current = body?.getBoundingClientRect().bottom ?? null;
      if (!state.isNearBottom) stopScroll();
      void earlier();
    }, { root: node, rootMargin: "320px 0px 0px 0px" });
    observer.observe(trigger);
    return () => observer.disconnect();
  }, [earlier, data?.before, scrollRef, contentRef, stopScroll, state, positioned]);

  const errors = ["SESSION_NOT_INTEGRATED", "SESSION_NOT_RECORDED", "SESSION_DELETED", "SESSION_INVALID_RECORD", "SESSION_UNREADABLE"] as const;
  const failure = errors.find(code => error instanceof ApiError && code === error.code) ?? "SESSION_UNREADABLE";
  return <section data-slot="session-view" aria-label={t("sessionView.title")} className="flex min-h-0 min-w-0 flex-1 flex-col">
      {tabsSide && !error && data && createPortal(<SessionCoverage harness={harness} coverage={data.coverage}/>, tabsSide)}
      {error ? <div className="flex min-h-0 flex-1 flex-col items-start gap-4 p-6" role="alert"><p>{t(`sessionView.errors.${failure}`)}</p><Button variant="outline" onClick={retry}>{t("retry")}</Button></div>
        : !data ? <p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>
          : <div className="relative flex min-h-0 flex-1 flex-col">
            {/* A uniform overflow value lets the hook recognize wheel events on the Radix viewport. */}
            <ScrollArea className="min-h-0 min-w-0 flex-1" viewportProps={{ ref: scrollRef, tabIndex: 0, "aria-label": t("sessionView.content"), style: { overflow: "auto" }, className: "overscroll-contain [overflow-anchor:none] [&>div]:block!" }}>
              <div ref={contentRef} className="mx-auto flex min-h-full w-full max-w-[868px] flex-col px-4 pt-4 pb-8 sm:px-6">
                {data.hasEarlier && positioned && <div ref={sentinel} aria-hidden="true" className="h-0 w-full" />}
                {rows.length ? <SessionBlocks routingMode={routingMode} rows={rows} taskText={taskText} checkoutRoot={checkoutRoot} expansion={{ expanded, toggle: (key, open) => {
                  setExpanded(previous => { const next = new Set(previous); if (open) next.add(key); else next.delete(key); return next; });
                  // The disclosure keeps the viewport where the reader is interacting.
                  stopScroll();
                } }}/> : <p className="text-sm text-ink2">{t("sessionView.empty")}</p>}
                {data.stopped && <p data-slot="session-stopped" role="status" className="mt-6 border-t pt-3 text-xs leading-relaxed text-fail">{<Message ns="taskDetail" id="sessionView.recordingStopped" values={{ reason: <ReasonMessage reason={data.stopped} /> }} />}</p>}
              </div>
            </ScrollArea>
            {earlierBusy && <p data-slot="session-loading-earlier" role="status" className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 bg-card px-3 py-1 text-xs text-ink3">{t("sessionView.loadingEarlier")}</p>}
            {newContent && <Button data-slot="session-new-content" aria-live="polite" className="absolute bottom-4 left-1/2 -translate-x-1/2 shadow-sm" onClick={() => {
              void scrollToBottom("instant"); scrollRef.current?.focus({ preventScroll: true });
            }}><ArrowDown size={14} aria-hidden/>{t("sessionView.newContent")}</Button>}
          </div>}
  </section>;
}
