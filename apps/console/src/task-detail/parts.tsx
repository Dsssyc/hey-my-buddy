import { cn } from "cn";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { formatClock, formatShortDateTime } from "../i18n/format";

/** A recorded instant in the reader's clock; one that was not recorded is said so. */
export function Moment({ at, seconds = false, className }: { at: string | number | null | undefined; seconds?: boolean; className?: string }) {
  const { t } = useTranslation("taskDetail");
  const ms = typeof at === "number" ? at : at ? Date.parse(at) : NaN;
  if (!Number.isFinite(ms)) return <span className={cn("text-ink3", className)}>{t("unrecorded")}</span>;
  const today = new Date(ms).toDateString() === new Date().toDateString();
  return <time dateTime={new Date(ms).toISOString()} className={cn("tabular-nums", className)}>
    {seconds && today ? formatClock(ms) : formatShortDateTime(ms)}
  </time>;
}

/** The name of a pane, its state in words, and its moment on the right. */
export function PaneTitle({ title, state, tone = "past", warnings = [], note, children }: {
  title: string; state?: string; tone?: "past" | "live" | "hand" | "fail"; warnings?: string[]; note?: ReactNode; children?: ReactNode;
}) {
  return <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1 px-6 pt-4 pb-3.5">
    <h3 className="text-base font-semibold">{title}</h3>
    {state && <span className={cn("text-xs", tone === "past" ? "text-ink2" : tone === "live" ? "text-live" : tone === "hand" ? "text-hand" : "text-fail")}>{state}</span>}
    {warnings.map(word => <span key={word} className="text-xs text-hand">{word}</span>)}
    {children && <span className="ml-auto flex flex-wrap items-baseline gap-x-3.5 gap-y-1 text-xs text-ink2">{children}</span>}
    {note && <p className="basis-full text-xs text-ink2 wrap-anywhere">{note}</p>}
  </div>;
}

/** What one step has to say, scrolled; a footer stays under it. */
export function Pane({ children, footer }: { children: ReactNode; footer?: ReactNode }) {
  return <section data-slot="detail-pane" className="flex min-h-0 min-w-0 flex-1 flex-col bg-card">
    <ScrollArea className="min-h-0 flex-1"><div className="w-0 min-w-full pb-4">{children}</div></ScrollArea>
    {footer}
  </section>;
}

/** The pale band that opens a pane: who did it, then what it cost. */
export function Band({ role, plain = false, identity, end, second, secondEnd }: {
  role: string; plain?: boolean; identity?: ReactNode; end?: ReactNode; second?: ReactNode; secondEnd?: ReactNode;
}) {
  return <div data-slot="detail-band" className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-baseline gap-x-2.5 gap-y-1 border-y bg-sunk px-6 py-2.5 text-xs">
    <span className={cn("leading-[18px]", plain ? "px-0.5 text-ink2" : "rounded-sm border border-ink2 px-[7px] font-medium")}>{role}</span>
    <span className="min-w-0 font-mono wrap-anywhere">{identity}</span>
    <span className="text-right text-ink2">{end}</span>
    {(second || secondEnd) && <div className="col-span-2 col-start-2 flex flex-wrap items-baseline gap-x-4 gap-y-1 text-ink2">
      {second && <span className="min-w-0 tabular-nums">{second}</span>}
      {secondEnd && <span className="ml-auto text-ink3">{secondEnd}</span>}
    </div>}
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

/** Splits a long text where a line or a word ends, so that the head reads as a whole. */
function split(text: string, limit: number): [string, string] {
  if (text.length <= limit) return [text, ""];
  const cut = text.lastIndexOf("\n", limit) > limit / 2 ? text.lastIndexOf("\n", limit) : text.lastIndexOf(" ", limit) > limit / 2 ? text.lastIndexOf(" ", limit) : limit;
  return [text.slice(0, cut).trimEnd(), text.slice(cut).trimStart()];
}

/** A text that is long is folded after its head, and unfolds on a click. */
export function Fold({ text, limit = 280, className }: { text: string; limit?: number; className?: string }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const [head, tail] = split(text, limit);
  const paragraph = cn("whitespace-pre-wrap wrap-anywhere", className);
  if (!tail) return <p className={paragraph}>{text}</p>;
  return <Collapsible open={open} onOpenChange={setOpen}>
    <p className={paragraph}>{head}{!open && "…"}</p>
    <CollapsibleContent><p className={paragraph}>{tail}</p></CollapsibleContent>
    <CollapsibleTrigger className="mt-1 inline-flex items-center gap-1 rounded-sm text-xs text-ink2 outline-none transition-colors duration-150 hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none">
      <Disclosure open={open} />{open ? t("fold.less") : t("fold.more")}
    </CollapsibleTrigger>
  </Collapsible>;
}

/** One list of short lines under a name, as a request or a remainder lists them. */
export function Lines({ items, className }: { items: string[]; className?: string }) {
  return <ul className={cn("space-y-1", className)}>{items.map((item, index) => <li key={index} className="relative pl-3.5 wrap-anywhere before:absolute before:top-[0.7em] before:left-0.5 before:h-[1.5px] before:w-[5px] before:bg-ink3">{item}</li>)}</ul>;
}

/** A name that goes to the thing it names: another micro task's detail. */
export function LinkButton({ children, title, onClick }: { children: ReactNode; title?: string; onClick: () => void }) {
  return <button type="button" title={title} onClick={onClick}
    className="min-w-0 rounded-sm text-left underline decoration-line2 underline-offset-2 outline-none transition-colors duration-150 hover:decoration-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none">
    {children}
  </button>;
}
