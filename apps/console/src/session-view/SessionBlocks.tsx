import { useTranslation } from "react-i18next";
import { ChevronRight, Brain, Minus } from "lucide-react";
import { Message, MessageContent } from "../components/ui/message";
import { Marker, MarkerContent, MarkerIcon } from "../components/ui/marker";
import { MessageScrollerItem } from "../components/ui/message-scroller";
import { Trace, TraceCollapsible, TraceCollapsibleContent, TraceCollapsibleTrigger, TraceLine, TraceIcon } from "../components/ui/trace";
import { CodeBlockCopyButton } from "../components/ui/code-block";
import { Hint } from "../components/hint";
import { MessageResponse } from "./Markdown";
import { ToolRow, Output, kindIcon } from "./ToolRow";
import { taskExcerpt } from "./rows";
import type { SessionRow, CallGroup, ToolKind } from "./rows";

export type Expansion = { expanded: Set<string>; toggle: (key: string, open: boolean) => void };
export function CallSummary({ group }: { group: CallGroup }) {
  const { t } = useTranslation("taskDetail");
  const counts = Object.entries(group.counts).map(([kind, count]) => t(`sessionView.counts.${kind as ToolKind}`, { count }));
  const markers = group.items.filter(item => item.type === "message").length;
  if (markers) counts.push(t("sessionView.counts.marker", { count: markers }));
  return <><span className="min-w-0 flex-1 text-left wrap-anywhere">{counts.join(t("sessionView.groupSeparator"))}</span>{group.failed > 0 && <span className="shrink-0 text-fail">{t("sessionView.failures", { count: group.failed })}</span>}</>;
}
export function SessionBlocks({ rows, expansion, taskText, checkoutRoot }: { rows: SessionRow[]; expansion: Expansion; taskText: string; checkoutRoot: string }) {
  const { t } = useTranslation("taskDetail");
  return <div data-slot="session-blocks" className="min-w-0 space-y-6">
    {rows.map(row => {
      const { key } = row;
      const open = expansion.expanded.has(key);
      let body;
      if (row.type === "calls") body = <Trace><TraceCollapsible open={open} onOpenChange={next => expansion.toggle(key, next)}>
        <TraceCollapsibleTrigger data-session-focus={key} className="min-h-[30px] w-full gap-2 rounded-none text-[13px] text-ink2">
          <ChevronRight size={13} aria-hidden className={open ? "shrink-0 rotate-90" : "shrink-0"}/>
          <span className="flex shrink-0 gap-1" aria-hidden>{Object.keys(row.counts).map(kind => { const Icon = kindIcon(kind as ToolKind); return <Icon key={kind} size={13}/>; })}</span>
          <CallSummary group={row}/>
        </TraceCollapsibleTrigger>
        <TraceCollapsibleContent className="mt-0 mb-0 ml-1.5 pl-4 pr-0"><div data-slot="session-call-group">{row.items.map(item => item.type === "tool" ? <ToolRow key={item.key} block={item} expansion={expansion} checkoutRoot={checkoutRoot}/> : <Marker key={item.key} className="py-1 text-xs"><MarkerContent>{item.update.content.text || item.update._meta?.nativeType}</MarkerContent></Marker>)}</div></TraceCollapsibleContent>
      </TraceCollapsible></Trace>;
      else if (row.type === "tool") body = <Trace><ToolRow block={row.tool} markers={row.markers} expansion={expansion} checkoutRoot={checkoutRoot}/></Trace>;
      else if (row.type === "blackboard") body = <div className="-my-3"><TraceCollapsible open={open} onOpenChange={next => expansion.toggle(key, next)}>
        <TraceCollapsibleTrigger className="min-h-[24px] rounded-none text-xs text-ink3" data-session-focus={key}><Minus size={13} aria-hidden/>{t(`sessionView.blackboard.${row.action}`)}</TraceCollapsibleTrigger>
        <TraceCollapsibleContent className="border-0"><Output text={JSON.stringify({ input: row.tool.update.rawInput, result: row.tool.update.rawOutput, output: [...row.tool.segments.values()].join("") }, null, 2)} id={`${key}:detail`} expansion={expansion} language="json"/></TraceCollapsibleContent>
      </TraceCollapsible></div>;
      else if (row.type === "plan") body = <Trace><TraceCollapsible open={open} onOpenChange={next => expansion.toggle(key, next)}><TraceCollapsibleTrigger data-session-focus={key}>{t("sessionView.plan")}</TraceCollapsibleTrigger><TraceCollapsibleContent><ol className="space-y-2">{row.update.entries.map((entry, index) => <li key={index} className="flex items-start gap-3 text-sm"><span className="shrink-0 text-xs text-ink3">{t(`sessionView.planStatus.${entry.status}`)} · {t(`sessionView.priority.${entry.priority}`)}</span><span className="min-w-0 whitespace-pre-wrap wrap-anywhere">{entry.content}</span></li>)}</ol></TraceCollapsibleContent></TraceCollapsible></Trace>;
      else {
        const text = row.message.update.content.text;
        if (row.type === "task") {
          const excerpt = taskExcerpt(taskText);
          body = <div data-slot="session-task" className="bg-sunk px-3.5 py-3">
            <p className="mb-1 text-xs text-ink3">{t("sessionView.task")}</p>
            <h3 className="text-[14px] font-semibold wrap-anywhere">{excerpt.title}</h3>
            <div className="mt-1 line-clamp-2 text-[13px] leading-[1.65] text-ink2 wrap-anywhere"><MessageResponse>{excerpt.opening}</MessageResponse></div>
            <TraceCollapsible open={open} onOpenChange={next => expansion.toggle(key, next)}><TraceCollapsibleTrigger data-session-focus={key} className="mt-2 rounded-none text-xs text-ink3">{t("sessionView.fullPrompt", { count: [...text].length })}</TraceCollapsibleTrigger><TraceCollapsibleContent className="ml-0 border-0 pl-0"><Output text={text} id={`${key}:prompt`} expansion={expansion} preview={false}/></TraceCollapsibleContent></TraceCollapsible>
          </div>;
        } else if (row.type === "reasoning") body = <Trace><TraceCollapsible open={open} onOpenChange={next => expansion.toggle(key, next)}><TraceCollapsibleTrigger data-session-focus={key} className="text-xs"><TraceLine><TraceIcon><Brain size={13}/></TraceIcon>{t("sessionView.reasoning")}</TraceLine></TraceCollapsibleTrigger><TraceCollapsibleContent className="text-sm"><MessageResponse>{text}</MessageResponse></TraceCollapsibleContent></TraceCollapsible></Trace>;
        else if (row.type === "marker") body = <Marker className="items-start text-xs"><MarkerIcon><Minus size={13}/></MarkerIcon><MarkerContent><MessageResponse>{text}</MessageResponse></MarkerContent></Marker>;
        else body = <div className="group/message relative max-w-[700px] min-w-0">
          {row.type === "answer" && <Marker className="mb-3 border-t border-line pt-4 text-xs text-ink3"><MarkerContent>{t("sessionView.answer")}</MarkerContent></Marker>}
          <Message><MessageContent className="text-[14.5px] leading-[1.72]"><div className="pr-7"><MessageResponse>{text}</MessageResponse></div></MessageContent></Message>
          <div className={`absolute right-0 ${row.type === "answer" ? "top-12" : "top-0"} opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100`}><Hint text={t("sessionView.copy")}><CodeBlockCopyButton value={text} data-session-focus={`${key}:copy`}/></Hint></div>
        </div>;
      }
      return <MessageScrollerItem key={key} messageId={key} data-session-block={key} data-session-row={row.type} className="[content-visibility:visible]">{body}</MessageScrollerItem>;
    })}
  </div>;
}
