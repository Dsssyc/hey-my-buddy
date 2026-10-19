import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { ArrowDown, ArrowLeft } from "lucide-react";
import { ApiError } from "../api";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "../components/ui/dialog";
import { ScrollArea } from "../components/ui/scroll-area";
import { reasonText } from "../i18n/reason";
import { sessionBlocks } from "./blocks";
import { SessionBlocks } from "./SessionBlocks";
import { useSession } from "./use-session";
import type { SessionClient } from "./use-session";
import type { SessionPosition } from "./types";

export type SessionDialogProps = {
  client: SessionClient; taskId: string; attemptId: string; label: string; harness: string;
  active: boolean; position?: SessionPosition; onPosition: (position: SessionPosition) => void; onClose: () => void; onReturnFocus?: () => boolean;
};

/** One read-only attempt. Navigation and persistence are owned by the enclosing shell. */
export default function SessionDialog({ client, taskId, attemptId, label, harness, active, position, onPosition, onClose, onReturnFocus }: SessionDialogProps) {
  const { t } = useTranslation("taskDetail");
  const { data, error, earlierBusy, earlier, retry } = useSession(client, taskId, attemptId, active, position?.before);
  const viewport = useRef<HTMLDivElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const closeButton = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | SVGElement | null>(null);
  const atBottom = useRef(position?.atBottom ?? true);
  const anchor = useRef<{ height: number; top: number } | null>(null);
  const [newContent, setNewContent] = useState(false);
  const [expanded, setExpanded] = useState(() => new Set(position?.expanded ?? []));
  const expandedRef = useRef(expanded);
  const focus = useRef<HTMLElement | null>(null);
  const initial = useRef(false);
  const previousAfter = useRef<number | null>(null);
  const blocks = useMemo(() => sessionBlocks(data?.events ?? []), [data?.events]);
  const persist = () => {
    if (!data || !viewport.current) return;
    const node = viewport.current;
    // A page can begin between native items. The loaded suffix has stable
    // control order even when its read-page boundaries change on restoration.
    const targets = [...(panel.current?.querySelectorAll<HTMLElement>("[data-session-focus]") ?? [])];
    const focused = focus.current === null ? -1 : targets.indexOf(focus.current);
    onPosition({ before: data.before, scrollTop: node.scrollTop, atBottom: node.scrollHeight - node.scrollTop - node.clientHeight < 48, focus: focused < 0 ? null : focused, expanded: [...expandedRef.current] });
  };
  useEffect(() => {
    window.addEventListener("pagehide", persist);
    return () => window.removeEventListener("pagehide", persist);
  }, [data, onPosition]);
  useLayoutEffect(() => {
    const node = viewport.current;
    if (!node || !data) return;
    if (!initial.current) {
      initial.current = true;
      node.scrollTop = atBottom.current ? node.scrollHeight : position?.scrollTop ?? 0;
      const target = position?.focus == null ? null : panel.current?.querySelectorAll<HTMLElement>("[data-session-focus]")[position.focus];
      target?.focus({ preventScroll: true });
    } else if (data.change === "earlier" && anchor.current) {
      node.scrollTop = anchor.current.top + node.scrollHeight - anchor.current.height;
      anchor.current = null;
    } else if (previousAfter.current !== null && data.after > previousAfter.current) {
      if (atBottom.current) node.scrollTop = node.scrollHeight;
      else setNewContent(true);
    }
    previousAfter.current = data.after;
    persist();
  }, [data]);
  useEffect(() => {
    const node = content.current;
    if (!node) return;
    const observer = new ResizeObserver(() => {
      if (atBottom.current && viewport.current) viewport.current.scrollTop = viewport.current.scrollHeight;
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [data !== null && !error]);

  const errors = ["SESSION_NOT_INTEGRATED", "SESSION_NOT_RECORDED", "SESSION_DELETED", "SESSION_INVALID_RECORD", "SESSION_UNREADABLE"] as const;
  const failure = errors.find(code => error instanceof ApiError && code === error.code) ?? "SESSION_UNREADABLE";
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent ref={panel} data-slot="session-view" aria-describedby="session-description" className="flex h-dvh max-h-dvh w-full max-w-full flex-col gap-0 border-0 p-0 sm:h-[min(90dvh,960px)] sm:max-w-[min(1100px,calc(100%-3rem))] sm:border"
      onFocusCapture={event => {
        const target = (event.target as HTMLElement).closest<HTMLElement>("[data-session-focus]");
        // Closing is navigation; keep the last reading control for a return.
        if (target === closeButton.current) return;
        focus.current = target;
        persist();
      }}
      onOpenAutoFocus={event => { event.preventDefault(); const current = document.activeElement; returnFocus.current = current instanceof HTMLElement || current instanceof SVGElement ? current : null; closeButton.current?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); if (!onReturnFocus?.() && returnFocus.current?.isConnected) returnFocus.current.focus({ preventScroll: true }); }}>
      <header className="shrink-0 border-b bg-card px-4 py-3 sm:px-6">
        <div className="flex items-start gap-3">
          <Button ref={closeButton} size="sm" variant="ghost" data-session-focus="close" aria-label={t("sessionView.back")} onClick={onClose}><ArrowLeft size={16} aria-hidden/>{t("sessionView.back")}</Button>
          <div className="min-w-0 flex-1"><DialogTitle className="text-base leading-7">{label} · {t("sessionView.title")}</DialogTitle>
            <DialogDescription id="session-description" className="flex flex-wrap items-baseline gap-x-3 text-xs text-ink2"><span className="font-mono wrap-anywhere">{harness}</span>{data && <span className={data.state === "running" ? "text-live" : "text-ink2"}>{t(`sessionView.state.${data.state}`)}</span>}</DialogDescription>
          </div>
        </div>
        {data && data.omitted.length > 0 && <p className="mt-2 text-xs leading-relaxed text-ink2">{t("sessionView.omitted", { content: data.omitted.map(reasonText).join(t("sessionView.separator")) })}</p>}
      </header>
      {error ? <div className="flex min-h-0 flex-1 flex-col items-start gap-4 p-6" role="alert"><p>{t(`sessionView.errors.${failure}`)}</p><Button variant="outline" onClick={retry}>{t("retry")}</Button></div>
        : !data ? <p className="p-6 text-sm text-ink2" role="status">{t("sessionView.loading")}</p>
          : <div className="relative flex min-h-0 flex-1 flex-col">
            <ScrollArea className="min-h-0 flex-1" viewportProps={{ ref: viewport, tabIndex: 0, "data-session-focus": "viewport", "aria-label": t("sessionView.content"), className: "[overflow-anchor:none]", onScroll: () => {
              const node = viewport.current!;
              atBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
              if (atBottom.current) setNewContent(false);
              persist();
            } }}>
              <div ref={content} className="mx-auto w-full max-w-[880px] px-4 pt-4 pb-8 sm:px-8">
                <div className="mb-6 flex justify-center">
                  {data.hasEarlier ? <Button variant="ghost" size="sm" data-session-focus="earlier" disabled={earlierBusy} onClick={() => {
                    const node = viewport.current!;
                    anchor.current = { height: node.scrollHeight, top: node.scrollTop };
                    void earlier();
                  }}>{t(earlierBusy ? "sessionView.loadingEarlier" : "sessionView.earlier")}</Button> : <p className="py-2 text-xs text-ink3">{t("sessionView.start")}</p>}
                </div>
                {blocks.length ? <SessionBlocks blocks={blocks} expansion={{ expanded, toggle: (key, open) => {
                  setExpanded(previous => { const next = new Set(previous); if (open) next.add(key); else next.delete(key); expandedRef.current = next; return next; });
                  // The disclosure keeps the viewport where the reader is interacting.
                  atBottom.current = false;
                  queueMicrotask(persist);
                } }}/> : <p className="text-sm text-ink2">{t("sessionView.empty")}</p>}
              </div>
            </ScrollArea>
            {newContent && <Button data-slot="session-new-content" aria-live="polite" className="absolute bottom-4 left-1/2 -translate-x-1/2 shadow-sm" onClick={() => {
              atBottom.current = true; viewport.current!.scrollTop = viewport.current!.scrollHeight; setNewContent(false); persist(); viewport.current?.focus({ preventScroll: true });
            }}><ArrowDown size={14} aria-hidden/>{t("sessionView.newContent")}</Button>}
          </div>}
    </DialogContent>
  </Dialog>;
}
