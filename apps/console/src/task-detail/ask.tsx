import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Fold, Moment } from "./parts";
import { Node, Quote } from "./thread";
import type { Inquiry } from "./types";

/** One inquiry put to the running execution, and the answer it got. */
export function Ask({ inquiry }: { inquiry: Inquiry }) {
  const { t } = useTranslation("taskDetail");
  const word = <span className="whitespace-nowrap">{t(`inquiry.${inquiry.state}`)}</span>;
  return <>
    <Node mark="ask">
      <Quote>
        <div className="flex items-baseline gap-2 text-xs text-ink3">
          <span className="min-w-0">{t("inquiry.head", { who: inquiry.author })} · <Moment at={inquiry.createdAt} /></span>
          <span className="ml-auto">{inquiry.reason && (inquiry.state === "discarded" || inquiry.state === "unavailable")
            ? <Hint text={inquiry.reason}><span tabIndex={0} className="rounded-sm underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{word}</span></Hint>
            : word}</span>
        </div>
        <Fold text={inquiry.question} />
      </Quote>
    </Node>
    {inquiry.answer && <Node mark="message">
      <div className="min-w-0 flex-1">
        <Fold text={inquiry.answer.text} className="text-foreground" />
        {inquiry.answer.truncated && <p className="text-xs text-ink3">{t("inquiry.truncated")}</p>}
      </div>
      <Moment at={inquiry.answer.at} className="text-xs text-ink3" />
    </Node>}
  </>;
}
