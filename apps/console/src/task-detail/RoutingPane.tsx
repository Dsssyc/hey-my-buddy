import { useTranslation } from "react-i18next";
import { formatDuration } from "../i18n/format";
import { Band, Facts, Fold, Moment, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import { parseTokenUsage } from "./parse";
import type { Step } from "./steps";
import { Node, Thread } from "./thread";
import type { Context } from "./types";
import { Usage } from "./usage";

/**
 * One route: the Router that was asked, how, what it chose and why. The list of
 * candidates is not here: knowing who chose and why is what the reader needs,
 * and the list says nothing more. What the record lacks is said to be unrecorded.
 */
export function RoutingPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const span = step.span!;
  const entry = ctx.routing.find(item => item.decisionId === span.decisionId) ?? null;
  const unrecorded = t("unrecorded");
  const mode = entry?.routingMode ?? span.routing?.routingMode ?? null;
  const selected = entry?.selectedBuddy ?? span.routing?.selectedBuddy ?? null;
  const reason = entry?.reason ?? span.routing?.reason ?? null;
  const usage = span.routing?.usage ?? null;
  const running = step.outcome === "running";
  const failed = step.outcome === "failed";
  const state = running ? t("state.running") : failed ? t("state.failed") : step.outcome === "abstention" ? t("routing.abstained") : t("state.ended");
  return <Pane>
    <PaneTitle title={t("step.routing")} state={state} tone={running ? "live" : failed ? "fail" : "past"} note={failed && span.error ? span.error : undefined}>
      <span className="font-mono tabular-nums">{step.at !== null ? <Moment at={step.at} seconds /> : unrecorded} – {span.endAt ? <Moment at={span.endAt} seconds /> : null}</span>
      {step.ms !== null && <b className="text-sm font-normal text-foreground tabular-nums">{formatDuration(step.ms, true)}</b>}
    </PaneTitle>
    <Band role="Router" identity={quad(entry?.routerBuddy) ?? <span className="font-sans text-ink3">{unrecorded}</span>}
      end={mode ? t(`routing.mode.${mode}`) : unrecorded}
      second={<span className="inline-flex flex-wrap gap-x-3.5">
        <Usage usage={parseTokenUsage(span.tokenUsage)} />
        <span>{t("routing.elapsed", { time: usage?.elapsedMs != null ? formatDuration(usage.elapsedMs, true) : t("unknown") })}</span>
        <span>{t("routing.toolCalls", { n: usage?.toolCalls ?? t("unknown") })}</span>
      </span>} />
    <Thread key={span.spanId}>
      <Node mark="result">
        <div className="min-w-0 flex-1">
          <p className="text-xs text-ink3">{t("routing.chosen")}</p>
          <p className="font-mono text-xs wrap-anywhere">{quad(selected) ?? unrecorded}</p>
          <div className="mt-1"><Fold text={reason ?? unrecorded} className="text-foreground" limit={240} /></div>
          {entry?.source && entry.source !== "model-selection" && <div className="mt-2"><Facts items={[[t("routing.source"), t(`routing.sources.${entry.source}`, { defaultValue: entry.source })]]} /></div>}
        </div>
      </Node>
    </Thread>
  </Pane>;
}
