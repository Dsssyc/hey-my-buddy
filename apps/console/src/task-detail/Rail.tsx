import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { StateMark } from "@/components/ui/state-mark";
import { actorRoleText } from "../actor";
import { formatClockMinutes, formatDuration } from "../i18n/format";
import type { Step, Steps, Wait } from "./steps";
import { modelEffort } from "./names";

/** The shape of a step's mark: what kind of step it is, and who it waits for. */
function Mark({ step }: { step: Step }) {
  const tone = step.outcome === "running" ? "bg-live" : step.outcome === "failed" ? "bg-fail" : "bg-past";
  const ring = "shadow-[0_0_0_4px_var(--rail-ring)]";
  if (step.kind === "acceptance" && !step.pending && step.events.some(event => event.kind === "accept")) {
    return <StateMark kind="done" className={cn("relative z-10 self-center justify-self-center row-span-2 bg-[var(--rail-ring)]", ring)} />;
  }
  if (step.kind === "turn" || step.kind === "record") return <i className={cn("relative z-10 row-span-2 size-2.5 self-center justify-self-center", tone, ring)} />;
  if (step.kind === "routing" || step.kind === "calculation") {
    return <i className={cn("relative z-10 row-span-2 size-2.5 self-center justify-self-center border-[1.5px] border-past bg-[var(--rail-ring)]", ring)} />;
  }
  if (step.kind === "delegation" || step.kind === "handoff") return <i className={cn("relative z-10 row-span-2 size-2 self-center justify-self-center rounded-full bg-past", ring)} />;
  return <i className={cn("relative z-10 row-span-2 size-2 rotate-45 self-center justify-self-center", step.pending ? "bg-hand-mark" : "bg-past", ring)} />;
}

function Line({ wait }: { wait: Wait }) {
  const { t } = useTranslation("taskDetail");
  return <li className={cn("relative py-1 pr-4 pl-[52px] text-xs", wait.open ? "text-hand" : "text-ink3",
    "before:absolute before:inset-y-0 before:left-[21px] before:border-l-[1.5px]",
    wait.open ? "before:border-dashed before:border-hand-mark" : "before:border-ink3")}>
    {t(`wait.${wait.kind}`)}{wait.ms !== null && <> <span className="tabular-nums">{formatDuration(wait.ms)}</span></>}
  </li>;
}

/** The words a step carries: its name, then what it says of itself in a line or two. */
function Entry({ step, selected, index, last, onSelect, buddyName, lead }: {
  step: Step; selected: boolean; index: number; last: boolean; onSelect: () => void; buddyName: (step: Step) => string | null; lead: boolean;
}) {
  const { t } = useTranslation("taskDetail");
  const name = step.kind === "turn" ? t("step.turn", { n: step.turnIndex }) : t(`step.${step.kind}`);
  const owner = step.events[0]?.actor;
  const small = step.kind === "handoff" ? [modelEffort(step.from), modelEffort(step.to)].filter(Boolean).join(" → ")
    : buddyName(step) ?? (owner ? `${actorRoleText(owner.role)} · ${owner.id}` : null);
  const mark = step.kind === "turn" || step.kind === "record" ? step.outcome : null;
  const word = mark === "running" ? t("state.running") : mark === "failed" ? t("state.failed") : step.pending ? t(lead ? "wait.lead" : "wait.host") : null;
  const right = step.kind === "turn" || step.kind === "routing" || step.kind === "record" || step.kind === "calculation"
    ? (step.ms !== null ? formatDuration(step.ms) : null)
    : step.at !== null ? formatClockMinutes(step.at) : null;
  return <li className={cn("relative before:absolute before:inset-y-0 before:left-[21px] before:border-l-[1.5px] before:border-ink3",
    index === 0 && "before:top-1/2", last && "before:bottom-1/2", step.pending && "before:left-[20.5px] before:border-l-2 before:border-dashed before:border-hand-mark")}>
    <button type="button" aria-current={selected ? "step" : undefined} data-step={index + 1}
      onClick={onSelect}
      className="relative grid w-full grid-cols-[44px_minmax(0,1fr)_auto] items-baseline gap-x-2 gap-y-px py-[9px] pr-4 text-left outline-none transition-colors duration-150 motion-reduce:transition-none [--rail-ring:var(--paper)] hover:bg-sunk focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset aria-[current=step]:bg-live-wash aria-[current=step]:[--rail-ring:var(--live-wash)] aria-[current=step]:hover:bg-live-wash aria-[current=step]:after:absolute aria-[current=step]:after:inset-y-0 aria-[current=step]:after:left-0 aria-[current=step]:after:w-0.5 aria-[current=step]:after:bg-live">
      <Mark step={step} />
      <span className="font-medium">{name}</span>
      <span className="text-xs text-ink2 tabular-nums">{right}</span>
      {small && <span className="col-start-2 min-w-0 text-xs wrap-anywhere text-ink3">{small}</span>}
      {word && <span className={cn("col-start-3 text-xs", step.pending ? "text-hand" : mark === "running" ? "text-live" : "text-fail")}>{word}</span>}
    </button>
  </li>;
}

/**
 * The micro task's row of the timeline, stood on end: a step is something that
 * happened, a line between two steps is the wait. A thin line is a wait that
 * ended; a dashed orange one is waiting still for someone to take it up.
 */
export function Rail({ result, selected, lead, onSelect, buddyName }: {
  result: Steps; selected: number; lead: boolean; onSelect: (index: number) => void; buddyName: (step: Step) => string | null;
}) {
  const { t } = useTranslation("taskDetail");
  return <nav data-slot="detail-rail" aria-label={t("rail.label")} className="py-3.5">
    <ol>
      {result.steps.flatMap((step, index) => [
        ...step.waits.map((wait, position) => <Line key={`${step.key}:${position}`} wait={wait} />),
        <Entry key={step.key} step={step} index={index} last={index === result.steps.length - 1 && result.tail.length === 0}
          selected={index === selected} lead={lead} onSelect={() => onSelect(index)} buddyName={buddyName} />,
      ])}
      {result.tail.map((wait, position) => <Line key={`tail:${position}`} wait={wait} />)}
    </ol>
    {!result.complete && <p className="px-4 pt-3 text-xs text-ink3">{t("rail.truncated")}</p>}
  </nav>;
}

