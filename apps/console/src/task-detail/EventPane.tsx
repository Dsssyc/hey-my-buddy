import { useTranslation } from "react-i18next";
import { Section } from "@/components/section";
import { actorText } from "../actor";
import { Facts, Moment, Pane, PaneTitle } from "./parts";
import { quad } from "./names";
import type { Step } from "./steps";
import type { Context } from "./types";

/**
 * The steps that are one fact each: a cancel, a takeover (the Host changing the
 * credential that controls the micro task) and a handoff (a different buddy
 * taking the next turn). A takeover and a handoff are not the same thing.
 */
export function EventPane({ step, ctx }: { step: Step; ctx: Context }) {
  const { t } = useTranslation("taskDetail");
  const event = step.events[0] ?? null;
  const unrecorded = t("unrecorded");
  const stopped = event?.kind === "cancel" ? event.stopInitiator ?? event.actor?.role ?? null : null;
  const reason = event?.kind === "cancel" ? ctx.task.cancellation?.reason ?? null : null;
  return <Pane>
    <PaneTitle title={t(`step.${step.kind}`)}><Moment at={step.at} seconds /></PaneTitle>
    <Section name={t("event.facts")} className="border-t">
      {step.kind === "handoff"
        ? <Facts items={[[t("event.from"), quad(step.from) ?? unrecorded, true], [t("event.to"), quad(step.to) ?? unrecorded, true]]} />
        : <Facts items={[
          [t("decision.who"), event?.actor ? actorText(event.actor) : unrecorded],
          [t("decision.when"), <Moment key="when" at={step.at} seconds />],
          ...(stopped ? [[t("event.stoppedBy"), t(`stop.${stopped}`, { defaultValue: stopped })] as [string, string]] : []),
          ...(reason ? [[t("event.reason"), reason] as [string, string]] : []),
          ...(event?.summary ? [[t("event.summary"), event.summary] as [string, string]] : []),
        ]} />}
    </Section>
  </Pane>;
}
