import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown } from "lucide-react";
import { ApiError } from "../api";
import { Button } from "../components/ui/button";
import { MessageScrollerProvider, MessageScroller, MessageScrollerViewport, MessageScrollerContent } from "../components/ui/message-scroller";
import { sessionRows } from "./rows";
import { reasonText } from "../i18n/reason";
import { sessionBlocks } from "./blocks";
import { SessionBlocks } from "./SessionBlocks";
import { useSession } from "./use-session";
import type { SessionClient } from "./use-session";

export type SessionPanelProps = {
  routingMode?: "fast" | "review" | null; taskText: string; checkoutRoot: string; client: SessionClient; taskId: string; attemptId: string; harness: string;
  active: boolean;
};

/** One read-only attempt. Navigation and persistence are owned by the enclosing shell. */
export default function SessionPanel({ routingMode, taskText, checkoutRoot, client, taskId, attemptId, harness, active }: SessionPanelProps) {
  const { t } = useTranslation("taskDetail");
  const { data, error, earlierBusy, earlier, retry } = useSession(client, taskId, attemptId, active);
  const viewport = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const sentinel = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const anchor = useRef<{ bottom: number } | null>(null);
  const [newContent, setNewContent] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set<string>());
  const initial = useRef(false);
  const previousAfter = useRef<number | null>(null);
  const rows = useMemo(() => sessionRows(sessionBlocks(data?.events ?? [])), [data?.events]);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!active || !node || !data) return;
    if (!initial.current) {
      initial.current = true;
      node.scrollTop = node.scrollHeight;
    } else if (data.change === "earlier" && anchor.current) {
      node.scrollTop += content.current!.querySelector<HTMLElement>('[data-slot="session-blocks"]')!.getBoundingClientRect().bottom - anchor.current.bottom;
      anchor.current = null;
    } else if (previousAfter.current !== null && data.after > previousAfter.current) {
      if (atBottom.current) node.scrollTop = node.scrollHeight;
      else setNewContent(true);
    }
    previousAfter.current = data.after;
  }, [data, active]);
  useEffect(() => {
    const node = content.current;
    if (!node || !active) return;
    const observer = new ResizeObserver(() => {
      if (atBottom.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [data !== null && !error, active]);
  // Reaching the top of the transcript reads the next earlier page. The observer is
  // rebuilt once per prepend, so a sentinel that stays in view keeps reading.
  useEffect(() => {
    const trigger = sentinel.current;
    const node = viewport.current;
    if (!trigger || !node) return;
    const observer = new IntersectionObserver(entries => {
      if (!entries.some(entry => entry.isIntersecting)) return;
      // Group IDs change as history merges. Anchor the transcript end,
      // excluding the viewport's unused space when the latest page is short.
      const body = content.current?.querySelector<HTMLElement>('[data-slot="session-blocks"]');
      anchor.current = body ? { bottom: body.getBoundingClientRect().bottom } : null;
      atBottom.current = false;
      void earlier();
    }, { root: node, rootMargin: "320px 0px 0px 0px" });
    observer.observe(trigger);
    return () => observer.disconnect();
  }, [earlier, data?.before]);

  const errors = ["SESSION_NOT_INTEGRATED", "SESSION_NOT_RECORDED", "SESSION_DELETED", "SESSION_INVALID_RECORD", "SESSION_UNREADABLE"] as const;
  const failure = errors.find(code => error instanceof ApiError && code === error.code) ?? "SESSION_UNREADABLE";
  return <section ref={panel} data-slot="session-view" aria-label={t("sessionView.title")} className="flex min-h-0 min-w-0 flex-1 flex-col">
      <header className="shrink-0 border-b bg-card px-6 py-3">
        <p className="flex flex-wrap items-baseline gap-x-3 text-xs text-ink2"><span className="font-mono wrap-anywhere">{harness}</span>{data && <span className={data.state === "running" ? "text-ink state-breathe" : "text-ink2"}>{t(`sessionView.state.${data.state}`)}</span>}</p>
        {data && data.omitted.length > 0 && <p className="mt-2 text-xs leading-relaxed text-ink2">{t("sessionView.omitted", { content: data.omitted.map(reasonText).join(t("sessionView.separator")) })}</p>}
      </header>
      {error ? <div className="flex min-h-0 flex-1 flex-col items-start gap-4 p-6" role="alert"><p>{t(`sessionView.errors.${failure}`)}</p><Button variant="outline" onClick={retry}>{t("retry")}</Button></div>
        : !data ? <p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>
          : <div className="relative flex min-h-0 flex-1 flex-col">
            <MessageScrollerProvider defaultScrollPosition="start"><MessageScroller><MessageScrollerViewport preserveScrollOnPrepend={false} ref={viewport} tabIndex={0} data-session-focus="viewport" aria-label={t("sessionView.content")} className="[overflow-anchor:none]" onScroll={() => {
              if (!active) return;
              const node = viewport.current!;
              atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
              if (atBottom.current) setNewContent(false);
            }}>
              <MessageScrollerContent ref={content} className="mx-auto w-full max-w-[868px] gap-0 px-4 pt-4 pb-8 sm:px-6">
                <div ref={sentinel} aria-hidden="true" className="h-0 w-full" />
                {rows.length ? <SessionBlocks routingMode={routingMode} rows={rows} taskText={taskText} checkoutRoot={checkoutRoot} expansion={{ expanded, toggle: (key, open) => {
                  setExpanded(previous => { const next = new Set(previous); if (open) next.add(key); else next.delete(key); return next; });
                  // The disclosure keeps the viewport where the reader is interacting.
                  atBottom.current = false;
                } }}/> : <p className="text-sm text-ink2">{t("sessionView.empty")}</p>}
                {data.stopped && <p data-slot="session-stopped" role="status" className="mt-6 border-t pt-3 text-xs leading-relaxed text-fail">{t("sessionView.recordingStopped", { reason: reasonText(data.stopped) })}</p>}
              </MessageScrollerContent>
            </MessageScrollerViewport></MessageScroller></MessageScrollerProvider>
            {earlierBusy && <p data-slot="session-loading-earlier" role="status" className="pointer-events-none absolute left-1/2 top-3 -translate-x-1/2 bg-card px-3 py-1 text-xs text-ink3">{t("sessionView.loadingEarlier")}</p>}
            {newContent && <Button data-slot="session-new-content" aria-live="polite" className="absolute bottom-4 left-1/2 -translate-x-1/2 shadow-sm" onClick={() => {
              atBottom.current = true; viewport.current!.scrollTop = viewport.current!.scrollHeight; setNewContent(false); viewport.current?.focus({ preventScroll: true });
            }}><ArrowDown size={14} aria-hidden/>{t("sessionView.newContent")}</Button>}
          </div>}
  </section>;
}
