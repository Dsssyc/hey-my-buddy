import { useTranslation } from "react-i18next";
import { reasonText } from "../i18n/reason";
import { formatDuration } from "../i18n/format";
import { BasisFold } from "./facts";
import { Band, Fold, Section, MomentRange, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import { parseTokenUsage } from "./parse";
import type { ContentTabs } from "./parts";
import type { Step } from "./steps";
import type { Context } from "./types";
import { Usage } from "./usage";

/**
 * One route: the Router that was asked, how, what it chose and why. The facts
 * are the span's own, so that two routes of one decision do not read alike; the
 * decision's record adds only which Router was asked and how the choice was made.
 * The list of candidates is not here: knowing who chose and why is what the
 * reader needs, and the list says nothing more. What the record lacks is said to
 * be unrecorded.
 */
export function RoutingPane({ step, ctx, tabs }: { step: Step; ctx: Context; tabs?: ContentTabs }) {
  const { t } = useTranslation("taskDetail");
  const span = step.span!;
  const entry = ctx.routing.find(item => item.decisionId === span.decisionId) ?? null;
  const basis = entry ? ctx.bases[entry.decisionId] ?? null : null;
  const unrecorded = t("unrecorded");
  // The program chose without asking a Router: there is no Router to name and no mode to speak of.
  const notCalled = entry?.source === "single-candidate" || entry?.source === "no-candidate";
  const mode = span.routing?.routingMode ?? null;
  const usage = span.routing?.usage ?? null;
  const running = step.outcome === "running";
  const failed = step.outcome === "failed";
  const state = running ? t("state.running") : failed ? t("state.failed") : step.outcome === "unknown" ? t("state.unknown")
    : step.outcome === "abstention" ? t("routing.abstained") : t("state.ended");
  return <Pane tabs={tabs} title={
    <PaneTitle title={t("step.routing")} state={state} tone={running ? "live" : failed ? "fail" : step.outcome === "unknown" ? "hand" : "past"} note={failed && span.error ? span.error : undefined}>
      <MomentRange start={step.at} end={span.endAt} itemKey={step.key} label={t("step.routing")}/>
      {step.ms !== null && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(step.ms, "fine")}</b>}
    </PaneTitle>}>
    <Band role="Router" identity={notCalled ? undefined : quad(entry?.routerBuddy) ?? <span className="font-sans text-ink3">{unrecorded}</span>}
      end={notCalled ? t("routing.notCalled") : mode ? t(`routing.mode.${mode}`) : unrecorded}
      second={<span className="inline-flex flex-wrap gap-x-3.5">
        <Usage usage={parseTokenUsage(span.tokenUsage)} />
        <span>{t("routing.elapsed", { time: usage?.elapsedMs != null ? formatDuration(usage.elapsedMs, "fine") : t("unknown") })}</span>
        <span>{t("routing.toolCalls", { n: usage?.toolCalls ?? t("unknown") })}</span>
      </span>} />
    <Section name={t("routing.chosen")}><span className="font-mono text-xs">{quad(span.routing?.selectedBuddy) ?? unrecorded}</span></Section>
    <Section name={t("routing.reason")}>
      <Fold text={[span.routing?.reason ? reasonText(span.routing.reason) : null, span.routing?.note].filter(Boolean).join("\n") || unrecorded} />
      {basis && <BasisFold basis={basis} />}
    </Section>
    {entry?.taskType !== undefined && <>
      <Section name={t("routing.domainNode")}>{entry.domainNodeId === null ? t("routing.unclassified") : entry.domainNodeName ?? entry.domainNodeId ?? unrecorded}</Section>
      <Section name={t("routing.taskType")}>{t(`routing.taskTypes.${entry.taskType}`)}</Section>
    </>}
    {entry?.source && entry.source !== "model-selection" && <Section name={t("routing.source")}>{t(`routing.sources.${entry.source}`, { defaultValue: entry.source })}</Section>}
  </Pane>;
}
