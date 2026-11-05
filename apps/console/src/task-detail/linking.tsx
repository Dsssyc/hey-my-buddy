import { createContext, useContext, useEffect, useRef } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskTimeline } from "../macro-task-types";
import { eventActions } from "../timeline/events";

/** The keys that share one drawn mark: a span, each of its inquiries, each event cluster, and the settling mark. */
export function linkedItems(timeline: MacroTaskTimeline, runId: string) {
  const items = new Set<string>();
  for (const span of timeline.spans.filter(span => span.runId === runId)) {
    items.add(`span:${span.spanId}`);
    for (const inquiry of span.inquiries ?? []) items.add(`inquiry:${inquiry.inquiryId}`);
  }
  const events = timeline.events.filter(event => event.runId === runId);
  for (const action of eventActions(events)) for (const event of [action.event, ...(action.relatedEvents ?? [])]) items.add(`event:${event.seq}`);
  items.add(`settle:${runId}`);
  return items;
}

type Links = {
  origin: string | null;
  items: ReadonlySet<string>;
  onItem: (key: string) => void;
  stepKey: string;
  waits: ReactNode;
  elements: Map<string, HTMLDivElement>;
};
export const DetailLinks = createContext<Links | null>(null);

export function scrollToItem(node: Element) {
  node.scrollIntoView({ block: "center", inline: "nearest",
    behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
}

/** One entry of the overview: selecting it, by click or by keyboard, marks the same item in the step strip. */
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
  // The highlight frame is an overlay above the content, so no child background can cover it or break it.
  return <div ref={ref} data-item-key={itemKey} data-highlighted={highlighted}
    tabIndex={linked ? 0 : undefined} role={linked ? "article" : undefined} aria-label={linked ? label ?? t("label") : undefined}
    onClick={linked ? event => {
      // Controls inside an entry keep their own behaviour.
      if ((event.target as Element).closest("a, button")) return;
      links!.onItem(itemKey!);
    } : undefined}
    onKeyDown={linked ? event => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      links!.onItem(itemKey!);
    } : undefined}
    className={`relative outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring ${className}`}>
    {children}
    {linked && <span data-slot="item-highlight" aria-hidden="true"
      className={`pointer-events-none absolute inset-0 z-10 ring-1 ring-inset ring-ring transition-opacity duration-150 motion-reduce:transition-none ${highlighted ? "opacity-100" : "opacity-0"}`}/>}
  </div>;
}

export function ItemTime({ itemKey, label, time, children }: { itemKey: string; label: string; time: string; children: ReactNode }) {
  const links = useContext(DetailLinks);
  const { t } = useTranslation("taskDetail");
  return links?.items.has(itemKey) ? <button type="button" aria-label={t("strip.focusItem", { item: label, time })}
    className="text-left underline decoration-dotted underline-offset-2 outline-none hover:decoration-foreground focus-visible:ring-1 focus-visible:ring-ring"
    onClick={() => links.onItem(itemKey)}>{children}</button> : children;
}
