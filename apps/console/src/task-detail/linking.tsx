import { createContext, useContext, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskTimeline } from "../macro-task-types";
import { eventActions } from "../timeline/events";

/** Keys that share one drawn mark share its CSS token; no record text enters a selector. */
export function linkedItems(timeline: MacroTaskTimeline, runId: string) {
  const items = new Map<string, string>();
  const add = (keys: string[]) => {
    const token = String(items.size);
    for (const key of keys) items.set(key, token);
  };
  for (const span of timeline.spans.filter(span => span.runId === runId)) {
    add([`span:${span.spanId}`]);
    for (const inquiry of span.inquiries ?? []) add([`inquiry:${inquiry.inquiryId}`]);
  }
  const events = timeline.events.filter(event => event.runId === runId);
  for (const action of eventActions(events)) add([action.event, ...(action.relatedEvents ?? [])].map(event => `event:${event.seq}`));
  add([`settle:${runId}`]);
  return items;
}

type Links = {
  origin: string | null;
  items: ReadonlyMap<string, string>;
  onItem: (key: string) => void;
  stepKey: string;
  waits: ReactNode;
  elements: Map<string, HTMLDivElement>;
};
export const DetailLinks = createContext<Links | null>(null);

/** Native hover and focus-within provide the transient feedback, without a second selection state. */
export function LinkStyles({ items }: { items: ReadonlyMap<string, string> }) {
  return <style>{[...new Set(items.values())].map(token =>
    `[data-slot=task-detail]:has([data-overview-item="${token}"]:is(:hover,:focus-within)):not(:has([data-overview-item="${token}"] [data-overview-item]:is(:hover,:focus-within))) [data-linked-item~="${token}"] [data-slot=fragment-focus]{opacity:1;stroke:var(--color-focus)}`
  ).join("\n")}</style>;
}

export function scrollToItem(node: Element) {
  node.scrollIntoView({ block: "center", inline: "nearest",
    behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}

export function DetailItem({ itemKey, label, children, className = "" }: {
  itemKey?: string; label?: string; children: ReactNode; className?: string;
}) {
  const links = useContext(DetailLinks);
  const { t } = useTranslation("taskDetail");
  const ref = useRef<HTMLDivElement>(null);
  const linked = itemKey !== undefined && (links?.items.has(itemKey) ?? false);
  const highlighted = linked && links!.origin === itemKey;
  useEffect(() => {
    if (!linked) return;
    links!.elements.set(itemKey!, ref.current!);
    return () => { links!.elements.delete(itemKey!); };
  }, [linked, itemKey, links?.elements]);
  useEffect(() => {
    if (highlighted) scrollToItem(ref.current!);
  }, [highlighted]);
  return <div ref={ref} data-item-key={itemKey} data-overview-item={itemKey === undefined ? undefined : links?.items.get(itemKey)} data-highlighted={highlighted}
    tabIndex={linked ? 0 : undefined} role={linked ? "article" : undefined} aria-label={linked ? label ?? t("label") : undefined}
    className={`outline-none transition-colors duration-150 data-[highlighted=true]:bg-selected-row data-[highlighted=true]:ring-1 data-[highlighted=true]:ring-inset data-[highlighted=true]:ring-ring focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none ${className}`}>
    {children}
  </div>;
}

export function ItemTime({ itemKey, label, time, children }: { itemKey: string; label: string; time: string; children: ReactNode }) {
  const links = useContext(DetailLinks);
  const { t } = useTranslation("taskDetail");
  return links?.items.has(itemKey) ? <button type="button" aria-label={t("strip.focusItem", { item: label, time })}
    className="text-left underline decoration-dotted underline-offset-2 outline-none hover:decoration-foreground focus-visible:ring-1 focus-visible:ring-ring"
    onClick={() => links.onItem(itemKey)}>{children}</button> : children;
}
