import { useTranslation } from "react-i18next";
import { Message, MessageContent } from "../components/ui/message";
import { Marker, MarkerContent } from "../components/ui/marker";
import { CodeBlockCopyButton } from "../components/ui/code-block";
import { Hint } from "../components/hint";
import { MessageResponse } from "./Markdown";
import type { AnswerContent } from "./rows";
import type { JsonValue } from "./types";

function Value({ value }: { value: JsonValue }) {
  if (Array.isArray(value) && value.length === 0) return <span>[]</span>;
  if (Array.isArray(value)) return <ul className="min-w-0 list-disc space-y-1 pl-4">{value.map((item, index) => <li key={index} className="min-w-0"><Value value={item}/></li>)}</ul>;
  if (value !== null && typeof value === "object") return Object.keys(value).length ? <Fields entries={Object.entries(value)}/> : <span>{"{}"}</span>;
  return <span className="whitespace-pre-wrap wrap-anywhere">{String(value)}</span>;
}
function Fields({ entries, router = false }: { entries: [string, JsonValue][]; router?: boolean }) {
  const { t } = useTranslation("taskDetail");
  return <dl data-slot="session-answer-fields" className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-x-5 gap-y-2 sm:grid-cols-[auto_minmax(0,1fr)]">{entries.map(([key, value]) => <div key={key} className="contents"><dt className="text-xs text-ink3 wrap-anywhere">{router && ["buddyId", "note", "evidence"].includes(key) ? t(`sessionView.routerAnswer.${key as "buddyId" | "note" | "evidence"}`) : key}</dt><dd className="min-w-0 text-sm leading-relaxed"><Value value={value}/></dd></div>)}</dl>;
}
export function Answer({ content, copyText, id, routing }: { content: AnswerContent; copyText: string; id: string; routing: boolean }) {
  let entries: [string, JsonValue][] = [];
  if (content.format === "fields" && content.value !== null && typeof content.value === "object" && !Array.isArray(content.value)) {
    const all = Object.entries(content.value);
    const order = ["buddyId", "note", "evidence"];
    entries = routing ? [...order.flatMap(key => all.filter(([name]) => name === key)), ...all.filter(([key]) => !order.includes(key))] : all;
  }
  const { t } = useTranslation("taskDetail");
  return <div className="group/message relative max-w-[700px] min-w-0">
    <Marker className="mb-3 border-t border-line pt-4 text-xs text-ink3"><MarkerContent>{t("sessionView.answer")}</MarkerContent></Marker>
    <Message><MessageContent className="text-[14.5px] leading-[1.72]"><div className="pr-7">
      {content.format === "markdown" ? <MessageResponse>{content.text}</MessageResponse> : entries.length ? <Fields entries={entries} router={routing}/> : <Value value={content.value}/>}
    </div></MessageContent></Message>
    <div className="absolute top-12 right-0 opacity-0 group-hover/message:opacity-100 group-focus-within/message:opacity-100"><Hint text={t("sessionView.copy")}><CodeBlockCopyButton value={copyText}/></Hint></div>
  </div>;
}
