import { useTranslation } from "react-i18next";
import { formatDuration } from "../i18n/format";
import { acceptanceInterval } from "../timeline/acceptance";
import { DetailItem } from "./linking";
import { Moment } from "./parts";
import type { Step, Steps } from "./steps";
import type { Context } from "./types";

/** A wait belongs to the step that ends it; an unfinished tail belongs to the last step. */
export function StepWaits({ step, steps, ctx }: { step: Step; steps: Steps; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const spans = ctx.timeline!.spans.filter(span => span.runId === ctx.view.task.runId);
  const waits = [...step.waits, ...(step === steps.steps.at(-1) ? steps.tail : [])];
  return waits.map(wait => {
    const itemKey = wait.spanId === null ? `settle:${ctx.view.task.runId}` : `span:${wait.spanId}`;
    const start = wait.spanId === null ? acceptanceInterval(ctx.row!, spans, ctx.nowMs)?.startMs
      : spans.find(span => span.spanId === wait.spanId)!.startAt;
    const label = t(`wait.${wait.kind}`);
    return <DetailItem key={itemKey} itemKey={itemKey} label={label} className="flex shrink-0 flex-wrap items-baseline gap-x-2 gap-y-1 px-6 pb-2 text-xs text-ink3">
      <i aria-hidden="true" className={wait.kind === "queue" ? "w-4 border-t border-dashed border-ink3" : "w-4 border-t border-dashed border-hand"}/>
      <span>{label} {wait.ms !== null && <span className="tabular-nums">{formatDuration(wait.ms, "fine")}</span>}</span>
      <span>· <Moment at={start} itemKey={itemKey} label={label}/> {t("wait.since")}</span>
    </DetailItem>;
  });
}
