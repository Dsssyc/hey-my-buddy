import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { reasonText } from "../i18n/reason";
import { Hint } from "@/components/hint";
import { Fold, Moment } from "./parts";
import { Node, Quote } from "./thread";
import type { Inquiry } from "./types";

/** One inquiry put to the running execution, and the answer it got. */
export function Ask({ inquiry, highlighted = false }: { inquiry: Inquiry; highlighted?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (highlighted) ref.current!.scrollIntoView({ block: "center", behavior: window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ? "instant" : "smooth" });
  }, [highlighted]);
  const { t } = useTranslation("taskDetail");
  const explanation = [inquiry.reason ? reasonText(inquiry.reason) : null, inquiry.note].filter(Boolean).join("\n");
  const endedReason = inquiry.state === "unavailable" && inquiry.reason &&
    ["inquiry-root-turn-ended", "inquiry-delivered-at-attempt-end"].includes(inquiry.reason.code)
    ? reasonText(inquiry.reason) : null;
  const word = <span>{endedReason ?? t(inquiry.state === "queued" && inquiry.deliveryMode === "cooperative-checkpoint"
    ? "inquiry.queuedCheckpoint" : `inquiry.${inquiry.state}`)}</span>;
  return <div ref={ref} data-inquiry-id={inquiry.inquiryId} data-highlighted={highlighted} className="transition-colors duration-150 data-[highlighted=true]:bg-selected-row data-[highlighted=true]:ring-1 data-[highlighted=true]:ring-ring motion-reduce:transition-none">
    <Node mark="ask">
      <Quote>
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-ink3">
          <span className="shrink-0 whitespace-nowrap">{t("inquiry.head")} · <Moment at={inquiry.createdAt} /></span>
          <span className="min-w-0 flex-auto">{explanation && !endedReason && (inquiry.state === "discarded" || inquiry.state === "unavailable")
            ? <Hint text={explanation}><span tabIndex={0} className="rounded-sm underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{word}</span></Hint>
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
  </div>;
}
