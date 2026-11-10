import { useTranslation } from "react-i18next";
import { ReasonMessage } from "../i18n/ReasonMessage";
import { Fold, Moment } from "./parts";
import { Quote } from "./thread";
import type { Inquiry } from "./types";
import { DetailItem } from "./linking";

/** One inquiry put to the running execution, and the answer it got. */
export function Ask({ inquiry }: { inquiry: Inquiry }) {
  const { t } = useTranslation("taskDetail");
  const explanation = inquiry.reason || inquiry.note;
  const endedReason = inquiry.state === "unavailable" && inquiry.reason &&
    ["inquiry-root-turn-ended", "inquiry-delivered-at-attempt-end"].includes(inquiry.reason.code)
    ? <ReasonMessage reason={inquiry.reason} /> : null;
  const word = <span>{endedReason ?? t(inquiry.state === "queued" && inquiry.deliveryMode === "cooperative-checkpoint"
    ? "inquiry.queuedCheckpoint" : `inquiry.${inquiry.state}`)}</span>;
  const itemKey = `inquiry:${inquiry.inquiryId}`;
  return <DetailItem itemKey={itemKey} label={`${t("inquiry.head")} · ${inquiry.question}`}>
      <Quote>
        <div data-slot="request-kind" className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-ink3">
          <span className="shrink-0 whitespace-nowrap">{t("inquiry.head")} · <Moment at={inquiry.createdAt} itemKey={itemKey} label={t("inquiry.head")} /></span>
          <span className="min-w-0 flex-auto text-balance">{word}</span>
        </div>
        <Fold text={inquiry.question} />
        {explanation && !endedReason && ["discarded", "unavailable"].includes(inquiry.state) && <Fold text={inquiry.note ?? ""} className="mt-1 text-ink3">{inquiry.reason && <div><ReasonMessage reason={inquiry.reason} /></div>}</Fold>}
      </Quote>
    {inquiry.answer && <div data-slot="host-response" className="mt-3 space-y-1">
      <div className="min-w-0 flex-1">
        <Fold text={inquiry.answer.text} className="text-foreground" />
        {inquiry.answer.truncated && <p className="text-xs text-ink3">{t("inquiry.truncated")}</p>}
      </div>
      <Moment at={inquiry.answer.at} className="text-xs text-ink3" />
    </div>}
  </DetailItem>;
}
