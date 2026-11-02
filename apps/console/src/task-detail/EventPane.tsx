import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Section } from "./parts";
import { actorText, stopActorText } from "../actor";
import { eventDetails, eventName } from "../macro-task-display";
import { Facts, Fold, Moment, Pane, PaneTitle } from "./parts";
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
  const stopped = event?.kind === "cancel" ? event.actor?.role ?? null : null;
  const note = event ? eventDetails(event) : null;
  const title = step.kind === "cancel" ? eventName(event!) : t(`step.${step.kind}`);
  return <Pane title={
    <PaneTitle title={title}>{event?.actor && <span>{actorText(event.actor)}</span>}<Moment at={step.at} itemKey={step.key} label={title} /></PaneTitle>}>
    <Section name={t("event.facts")}>
      {step.kind === "handoff"
        ? <Facts items={[[t("event.from"), quad(step.from) ?? unrecorded, true], [t("event.to"), quad(step.to) ?? unrecorded, true]]} />
        : <Facts items={[
          ...(stopped ? [[t("event.stoppedBy"), stopActorText(stopped)] as [string, string]] : []),
          ...(note ? [[t("event.details"), <Fold key="note" text={note} />] as [string, ReactNode]] : []),
        ]} />}
    </Section>
  </Pane>;
}
