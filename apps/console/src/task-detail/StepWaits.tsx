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
    return <DetailItem key={itemKey} itemKey={itemKey} label={label} className="flex flex-wrap items-baseline gap-x-3.5 gap-y-1 border-b px-6 py-3 text-xs text-ink2">
      <span>{label}</span>
      <Moment at={start} itemKey={itemKey} label={label}/>
      {wait.ms !== null && <span className="ml-auto tabular-nums">{formatDuration(wait.ms, "fine")}</span>}
    </DetailItem>;
  });
}
