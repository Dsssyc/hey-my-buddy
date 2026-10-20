import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";
import { Button } from "../components/ui/button";
import { Hint } from "../components/hint";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "../components/ui/collapsible";
import { Message, MessageContent, MessageResponse } from "../components/ai-elements/message";
import { Reasoning, ReasoningTrigger, ReasoningContent } from "../components/ai-elements/reasoning";
import { Tool, ToolHeader, ToolContent } from "../components/ai-elements/tool";
import { Terminal } from "../components/ai-elements/terminal";
import { Plan, PlanHeader, PlanContent } from "../components/ai-elements/plan";
import { CodeBlock } from "../components/ai-elements/code-block";
import type { SessionBlock } from "./blocks";

export type Expansion = { expanded: Set<string>; toggle: (key: string, open: boolean) => void };
function CopyText({ text, id, revealOn }: { text: string; id: string; revealOn: "message" | "fold" }) {
  const { t } = useTranslation("taskDetail");
  const [copied, setCopied] = useState(false);
  const [failed, setFailed] = useState(false);
  useEffect(() => { setCopied(false); setFailed(false); }, [text]);
  return <span className={`absolute z-10 inline-flex items-center gap-1 ${revealOn === "message" ? "right-1 top-1" : "right-0 top-0"}`}>
    <Hint text={t(copied ? "sessionView.copied" : "sessionView.copy")}><Button size="icon-sm" variant="ghost" data-session-focus={id} aria-label={t("sessionView.copy")}
      className={revealOn === "message" ? "opacity-0 transition-opacity group-hover/message:opacity-100 group-focus-within/message:opacity-100 focus-visible:opacity-100 motion-reduce:transition-none" : "opacity-0 transition-opacity group-hover/fold:opacity-100 group-focus-within/fold:opacity-100 focus-visible:opacity-100 motion-reduce:transition-none"} onClick={async () => {
      try { await navigator.clipboard.writeText(text); setCopied(true); setFailed(false); }
      catch { setFailed(true); }
    }}>{copied ? <Check size={14} aria-hidden/> : <Copy size={14} aria-hidden/>}</Button></Hint>
    {failed && <span role="alert" className="text-xs text-fail">{t("sessionView.copyFailed")}</span>}
  </span>;
}
function FoldBody({ id, title, text, children, expansion }: { id: string; title: string; text: string; children: ReactNode; expansion: Expansion }) {
  const open = expansion.expanded.has(id);
  return <Collapsible open={open} onOpenChange={next => expansion.toggle(id, next)} className="group/fold min-w-0 border-t border-line2 py-2">
    <div className="relative">
      <CollapsibleTrigger data-session-focus={id} className="flex w-full min-w-0 items-center gap-2 py-1 pr-8 text-sm"><Disclosure open={open}/>{title}</CollapsibleTrigger>
      <CopyText text={text} id={`${id}:copy`} revealOn="fold"/>
    </div>
    <CollapsibleContent>{children}</CollapsibleContent>
  </Collapsible>;
}
const jsonText = (value: unknown): string => typeof value === "string" ? value : JSON.stringify(value, null, 2);

export function SessionBlocks({ blocks, expansion }: { blocks: SessionBlock[]; expansion: Expansion }) {
  const { t } = useTranslation("taskDetail");
  return <div data-slot="session-blocks" className="space-y-7">
    {blocks.map(block => {
      const { key } = block;
      if (block.type === "plan") return <article key={key} data-session-block={key} className="min-w-0">
        <Plan><PlanHeader><h3 className="text-sm font-medium">{t("sessionView.plan")}</h3></PlanHeader><PlanContent>
          <ol className="space-y-2">{block.update.entries.map((entry, index) => <li key={index} className="flex items-start gap-3 text-sm">
            <span className="shrink-0 text-xs text-ink3">{t(`sessionView.planStatus.${entry.status}`)} · {t(`sessionView.priority.${entry.priority}`)}</span>
            <span className="min-w-0 whitespace-pre-wrap wrap-anywhere">{entry.content}</span>
          </li>)}</ol>
        </PlanContent></Plan>
      </article>;
      if (block.type === "tool") {
        const tool = block.update;
        const output = [...block.segments.values()].join("");
        return <article key={key} data-session-block={key} className="min-w-0 border-y border-line2">
          <Tool open={!expansion.expanded.has(`collapsed:${key}`)} onOpenChange={open => expansion.toggle(`collapsed:${key}`, !open)}><ToolHeader data-session-focus={`${key}:header`} title={tool.title ?? tool.toolCallId} category={tool.kind ? t(`sessionView.kind.${tool.kind}`) : t("unrecorded")} status={tool.status ? t(`sessionView.toolStatus.${tool.status}`) : t("unrecorded")}/><ToolContent>
            {tool.rawInput !== undefined && <FoldBody id={`${key}:input`} title={t("sessionView.input")} text={jsonText(tool.rawInput)} expansion={expansion}><CodeBlock code={jsonText(tool.rawInput)} language={typeof tool.rawInput === "string" ? "text" : "json"}/></FoldBody>}
            {tool.rawOutput !== undefined && <FoldBody id={`${key}:raw-output`} title={t("sessionView.result")} text={jsonText(tool.rawOutput)} expansion={expansion}><CodeBlock code={jsonText(tool.rawOutput)} language={typeof tool.rawOutput === "string" ? "text" : "json"}/></FoldBody>}
            {output && <FoldBody id={`${key}:output`} title={t("sessionView.output")} text={output} expansion={expansion}><Terminal output={output}/></FoldBody>}
            {tool.locations && <ul className="space-y-1 py-2 font-mono text-xs text-ink2 wrap-anywhere">{tool.locations.map((location, index) => <li key={index}>{location.path}</li>)}</ul>}
          </ToolContent></Tool>
        </article>;
      }
      const message = block.update;
      const text = message.content.text;
      if (message._meta?.marker) return <article key={key} data-session-block={key} className="border-l-2 border-line2 pl-4 text-sm text-ink2">
        <h3 className="mb-2 text-xs text-ink3">{t("sessionView.marker", { type: message._meta.nativeType ?? t("unknown") })}</h3><MessageResponse>{text}</MessageResponse>
      </article>;
      if (message.sessionUpdate === "agent_thought_chunk") return <article key={key} data-session-block={key} className="min-w-0">
        <Reasoning open={expansion.expanded.has(key)} onOpenChange={open => expansion.toggle(key, open)}><ReasoningTrigger data-session-focus={key}>{t("sessionView.reasoning")}</ReasoningTrigger><ReasoningContent>{text}</ReasoningContent></Reasoning>
      </article>;
      return <article key={key} data-session-block={key} className="group/message min-w-0">
        <Message from={message.sessionUpdate === "user_message_chunk" ? "user" : "assistant"}><MessageContent>
          <h3 className="sr-only">{t(message.sessionUpdate === "user_message_chunk" ? "sessionView.user" : "sessionView.assistant")}</h3>
          <CopyText text={text} id={`${key}:copy`} revealOn="message"/>
          <div className="pr-8"><MessageResponse>{text}</MessageResponse></div>
        </MessageContent></Message>
      </article>;
    })}
  </div>;
}
