import { cn } from "cn";
import { useContext, useId, useLayoutEffect, useRef, useState } from "react";
import type { Root, PhrasingContent } from "mdast";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { MessageResponse } from "../session-view/Markdown";
import { formatClock, formatMoment } from "../i18n/format";
import { DetailItem, DetailLinks, ItemTime } from "./linking";

/** A recorded instant in the reader's clock; one that was not recorded is said so. */
export function Moment({ at, className, itemKey, label = "" }: { at: string | number | null | undefined; className?: string; itemKey?: string; label?: string }) {
  const { t } = useTranslation("taskDetail");
  const ms = typeof at === "number" ? at : at ? Date.parse(at) : NaN;
  if (!Number.isFinite(ms)) return <span className={cn("text-ink3", className)}>{t("unrecorded")}</span>;
  const time = formatMoment(ms, Date.now());
  const content = <time dateTime={new Date(ms).toISOString()} className={cn("tabular-nums", className)}>{time}</time>;
  return itemKey ? <ItemTime itemKey={itemKey} label={label} time={time}>{content}</ItemTime> : content;
}

/** A recorded interval names its local date once when both ends share that date. */
export function MomentRange({ start, end, itemKey, label = "" }: { start: number | null; end: string | null; itemKey?: string; label?: string }) {
  const { t } = useTranslation("taskDetail");
  const endMs = end === null ? NaN : Date.parse(end);
  const sameDay = start !== null && Number.isFinite(start) && Number.isFinite(endMs)
    && new Date(start).toDateString() === new Date(endMs).toDateString();
  const content = <span className="font-mono tabular-nums">
    {start !== null ? <Moment at={start} /> : t("unrecorded")} – {end !== null
      ? sameDay ? <time dateTime={new Date(endMs).toISOString()}>{formatClock(endMs)}</time> : <Moment at={end} />
      : null}
  </span>;
  return itemKey ? <ItemTime itemKey={itemKey} label={label} time={start === null ? t("unrecorded") : formatMoment(start, Date.now())}>{content}</ItemTime> : content;
}

/** The name of a pane, its state in words, and its moment on the right. */
export function PaneTitle({ title, state, tone = "past", warnings = [], note, children }: {
  title: string; state?: string; tone?: "past" | "live" | "hand" | "fail"; warnings?: string[]; note?: ReactNode; children?: ReactNode;
}) {
  return <div data-slot="pane-title" className="flex shrink-0 flex-wrap items-baseline gap-x-2.5 gap-y-1 px-6 pt-4 pb-2">
    <h3 className="text-base font-semibold">{title}</h3>
    {state && <span className={cn("text-xs", tone === "past" ? "text-ink2" : tone === "live" ? "text-ink state-breathe" : tone === "hand" ? "text-hand" : "text-fail")}>{state}</span>}
    {warnings.map(word => <span key={word} className="text-xs text-hand">{word}</span>)}
    {children && <span className="ml-auto flex flex-wrap items-baseline gap-x-3.5 gap-y-1 text-xs text-ink2">{children}</span>}
    {note && <p className="basis-full text-xs text-ink2 wrap-anywhere">{note}</p>}
  </div>;
}

/** Header and waits stay above the execution tabs; only the chosen content scrolls. */
export type ContentTabs = (content: ReactNode) => ReactNode;
export function Pane({ title, children, footer, tabs }: { title: ReactNode; children: ReactNode; footer?: ReactNode; tabs?: ContentTabs }) {
  const links = useContext(DetailLinks);
  const content = <ScrollArea className="min-h-0 flex-1"><div className="w-0 min-w-full py-3">{children}</div></ScrollArea>;
  return <section data-slot="detail-pane" className="flex min-h-0 min-w-0 flex-1 flex-col bg-card">
    <DetailItem itemKey={links?.stepKey || undefined} className="flex min-h-0 min-w-0 flex-1 flex-col">
      {title}
      {links?.waits}
      {tabs ? tabs(content) : content}
    </DetailItem>
    {footer}
  </section>;
}

/** Step facts share one label column, without nesting panels. */
export function Section({ name, children, className }: { name: string; children: ReactNode; className?: string }) {
  const id = useId();
  return <div data-slot="section" role="group" aria-labelledby={id} className={cn("grid grid-cols-[84px_minmax(0,1fr)] items-baseline gap-x-4 gap-y-2 px-6 py-1.5 text-sm leading-[1.7] max-sm:grid-cols-[64px_minmax(0,1fr)]", className)}>
    <span id={id} className="text-xs text-ink3">{name}</span><div className="min-w-0 wrap-anywhere">{children}</div>
  </div>;
}

/** Who ran an execution and its usage, in the same label column as the other facts. */
export function Band({ role, identity, end, second, secondEnd }: {
  role: string; identity?: ReactNode; end?: ReactNode; second?: ReactNode; secondEnd?: ReactNode;
}) {
  const { t } = useTranslation("taskDetail");
  return <div data-slot="detail-band">
    <Section name={role}><span className="font-mono text-xs">{identity}</span>{end && <span className="ml-2 text-xs text-ink3">{end}</span>}</Section>
    {second && <Section name={t("usage.name")}>{second}</Section>}
    {secondEnd && <Section name={t("session.name")}>{secondEnd}</Section>}
  </div>;
}

/** A name and its value in one grid: the facts of a section. */
export function Facts({ items }: { items: [name: string, value: ReactNode, mono?: boolean][] }) {
  return <dl className="grid grid-cols-[auto_minmax(0,1fr)] items-baseline gap-x-3.5 gap-y-2">
    {items.map(([name, value, mono]) => <div key={name} className="contents">
      <dt className="text-xs text-ink3">{name}</dt>
      <dd className={cn("min-w-0 wrap-anywhere", mono && "font-mono text-xs")}>{value}</dd>
    </div>)}
  </dl>;
}

/** Keep the complete Markdown tree mounted; fold its rendered height after about eight lines. */
export function Fold({ text, className }: { text: string; className?: string }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const [long, setLong] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => { setOpen(false); }, [text]);
  useLayoutEffect(() => {
    const node = body.current!;
    const measure = () => setLong(node.scrollHeight > parseFloat(getComputedStyle(node).lineHeight) * 8 + 1);
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [text]);
  return <Collapsible data-slot="fold" open={open} onOpenChange={setOpen} className={cn("min-w-0 text-sm leading-[1.7]", className)}>
    <div data-slot="fold-body" data-folded={long && !open} ref={body}
      className={cn("min-w-0 wrap-anywhere", long && !open && "max-h-[13.6em] overflow-hidden [mask-image:linear-gradient(#000_70%,transparent)]")}>
      <MessageResponse className="[&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_h1]:text-base [&_h2]:text-sm [&_h3]:text-sm">{text}</MessageResponse>
    </div>
    {long && <CollapsibleTrigger className="mt-1 inline-flex items-center gap-1 rounded-sm text-xs text-ink2">
      <Disclosure open={open} />{open ? t("fold.less") : t("fold.more")}
    </CollapsibleTrigger>}
  </Collapsible>;
}

/** Keep only phrasing markup in a short list item; block syntax stays literal. */
function inlineMarkdown() {
  return (tree: Root) => {
    const phrasing = new Set(["text", "emphasis", "strong", "delete", "inlineCode", "link", "linkReference", "break", "image", "imageReference"]);
    const flatten = (nodes: Root["children"]): PhrasingContent[] => nodes.flatMap(node => {
      if (phrasing.has(node.type)) return [node as PhrasingContent];
      if ("children" in node) return flatten(node.children as Root["children"]);
      return "value" in node ? [{ type: "text" as const, value: node.value }] : [];
    });
    tree.children = [{ type: "paragraph", children: flatten(tree.children) }];
  };
}

/** One list of short lines under a name, as a request or a remainder lists them. */
export function Lines({ items, className }: { items: string[]; className?: string }) {
  return <ul className={cn("space-y-1", className)}>{items.map((item, index) => <li key={index} className="relative pl-3.5 wrap-anywhere before:absolute before:top-[0.7em] before:left-0.5 before:h-[1.5px] before:w-[5px] before:bg-ink3"><MessageResponse remarkPlugins={[inlineMarkdown]} className="[&_p]:inline [&_p]:leading-inherit">{item}</MessageResponse></li>)}</ul>;
}

/** Paths are product facts and never interpreted as Markdown. */
export function Paths({ items }: { items: string[] }) {
  return <ul className="space-y-1 font-mono text-xs">{items.map((item, index) => <li key={index} className="wrap-anywhere">{item}</li>)}</ul>;
}

/** A name that goes to the thing it names: another micro task's detail. */
export function LinkButton({ children, onClick }: { children: ReactNode; onClick: () => void }) {
  return <button type="button" onClick={onClick}
      className="min-w-0 rounded-sm text-left underline decoration-line2 underline-offset-2 outline-none transition-colors duration-150 hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none">
      {children}
    </button>;
}
