import { useLayoutEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, X, File, Search, Terminal, Pencil, Globe, Wrench, LoaderCircle } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { TraceCollapsible, TraceCollapsibleTrigger, TraceCollapsibleContent, TraceLine, TraceIcon, TraceTitle } from "../components/ui/trace";
import { CodeBlockActions, CodeBlockCopyButton } from "../components/ui/code-block";
import { Hint } from "../components/hint";
import { HighlightedCode } from "./MarkdownCode";
import { displayPath, toolInput, toolObject } from "./rows";
import type { ToolBlock, ToolKind } from "./rows";
import type { Expansion } from "./SessionBlocks";

export const kindIcon = (kind: ToolKind | undefined): LucideIcon => ({ read: File, edit: Pencil, delete: Pencil, move: Pencil, search: Search, execute: Terminal, fetch: Globe, think: Wrench, switch_mode: Wrench, other: Wrench })[kind ?? "other"];
const jsonText = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value, null, 2);

export function Output({ text, id, expansion, language = "text", preview = true }: { text: string; id: string; expansion: Expansion; language?: string; preview?: boolean }) {
  const { t } = useTranslation("taskDetail");
  const lines = text.split("\n");
  const remaining = lines.length - 14;
  const open = expansion.expanded.has(id);
  return <div className="group/output relative min-w-0" data-slot="session-output">
    <HighlightedCode code={!preview || open ? text : lines.slice(0, 14).join("\n")} language={language}/>
    <CodeBlockActions className="absolute top-1 right-1 opacity-0 group-hover/output:opacity-100 group-focus-within/output:opacity-100">
      <Hint text={t("sessionView.copy")}><CodeBlockCopyButton value={text} data-session-focus={`${id}:copy`}/></Hint>
    </CodeBlockActions>
    {preview && remaining > 0 && <button type="button" className="disclosure-trigger py-1 text-xs text-ink2" data-session-focus={id} aria-expanded={open} onClick={() => expansion.toggle(id, !open)}>{t(open ? "sessionView.collapseOutput" : "sessionView.moreLines", { count: remaining })}</button>}
  </div>;
}
export function ToolRow({ block, expansion, checkoutRoot, markers = [] }: { block: ToolBlock; expansion: Expansion; checkoutRoot: string; markers?: Extract<import("./blocks").SessionBlock, { type: "message" }>[] }) {
  const { t } = useTranslation("taskDetail");
  const { update: tool, key } = block;
  const title = toolObject(tool, checkoutRoot) ?? t(`sessionView.kind.${tool.kind ?? "other"}`);
  const Icon = kindIcon(tool.kind);
  const open = expansion.expanded.has(key);
  const titleRef = useRef<HTMLSpanElement>(null);
  const [truncated, setTruncated] = useState(false);
  useLayoutEffect(() => {
    const node = titleRef.current!;
    const measure = () => setTruncated(node.scrollWidth > node.clientWidth || String(toolInput(tool).command ?? "").includes("\n"));
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [title, tool.rawInput]);
  const input = toolInput(tool);
  const fields = Object.entries(input).filter(([name]) => tool.kind !== "execute" || name !== "command");
  const output = [...block.segments.values()].join("");
  const status = tool.status;
  return <TraceCollapsible open={open} onOpenChange={value => expansion.toggle(key, value)} data-session-tool={key}>
    <TraceCollapsibleTrigger className="h-[30px] w-full rounded-none" data-session-focus={key}>
      <TraceLine className="w-full gap-2 text-[13px]">
        <TraceIcon><Hint text={t(`sessionView.kind.${tool.kind ?? "other"}`)}><Icon size={13}/></Hint></TraceIcon>
        <TraceTitle ref={titleRef} className="flex-1 text-left font-mono text-ink">{title}</TraceTitle>
        <Hint text={t(`sessionView.toolStatus.${status ?? "unrecorded"}`)}><span className={status === "failed" ? "text-fail" : "text-ink3"} aria-label={t(`sessionView.toolStatus.${status ?? "unrecorded"}`)}>
          {status === "failed" ? <X size={13} aria-hidden/> : status === "completed" ? <Check size={13} aria-hidden/> : status === "in_progress" ? <LoaderCircle size={13} aria-hidden className="motion-safe:animate-spin"/> : <span className="inline-block size-1 rounded-full bg-current" aria-hidden/>}
        </span></Hint>
      </TraceLine>
    </TraceCollapsibleTrigger>
    <TraceCollapsibleContent className="mt-0 mb-2 ml-6 border-0 pr-0 pl-0 text-[12px]">
      {tool.kind === "execute" && truncated && typeof input.command === "string" && <div className="mb-2 whitespace-pre-wrap wrap-anywhere font-mono text-ink">$ {input.command}</div>}
      {fields.length > 0 && <dl className="mb-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-5 gap-y-1 font-mono leading-relaxed">{fields.map(([name, value]) => <div key={name} className="contents"><dt className="text-ink3">{name}</dt><dd className="min-w-0 whitespace-pre-wrap wrap-anywhere text-ink">{typeof value === "string" && /(?:path|directory|cwd)$/.test(name) ? displayPath(value, checkoutRoot) : jsonText(value)}</dd></div>)}</dl>}
      {output && <Output text={output} id={`${key}:output`} expansion={expansion} language={tool.kind === "execute" ? "ansi" : "text"}/>}
      {tool.rawOutput !== undefined && <Output text={jsonText(tool.rawOutput)} id={`${key}:result`} expansion={expansion} language={typeof tool.rawOutput === "string" ? "text" : "json"}/>}
      {markers.map(marker => <p key={marker.key} className="mt-2 text-xs text-ink3">{marker.update.content.text || marker.update._meta?.nativeType}</p>)}
      {tool.locations && <ul className="mt-2 font-mono text-ink2">{tool.locations.map((location, index) => <li key={index}>{displayPath(location.path, checkoutRoot)}</li>)}</ul>}
    </TraceCollapsibleContent>
  </TraceCollapsible>;
}
