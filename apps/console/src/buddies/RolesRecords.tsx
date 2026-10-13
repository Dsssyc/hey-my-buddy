import { useTranslation } from "react-i18next";
import type { ReactNode } from "react";
import { Hint } from "@/components/hint";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import { StateMark } from "@/components/ui/state-mark";
import { formatShortDateTime } from "../i18n/format";
import type { RoutingHealth } from "../types";
import { buddyQuad, recordCounts } from "./roles";

const head = "border-b py-2 pr-6 text-left text-xs font-normal whitespace-nowrap text-ink3";
const cell = "border-b py-2 pr-6 align-middle whitespace-nowrap";

/** A name with its number, in one line of the facts. */
function Fact({ name, hint, children }: { name: string; hint?: string; children: ReactNode }) {
  return <dl className="flex items-baseline gap-1.5">
    <dt className="text-ink3">
      <Hint text={hint}><span tabIndex={hint ? 0 : undefined} className="rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{name}</span></Hint>
    </dt>
    <dd className="tabular-nums">{children}</dd>
  </dl>;
}

/** A recorded moment; a moment that was not recorded is a dash, never a zero. */
function Moment({ at }: { at: string | null | undefined }) {
  const { t } = useTranslation("buddies");
  return at ? <time dateTime={at} className="tabular-nums">{formatShortDateTime(Date.parse(at))}</time> : <>{t("roles.records.routers.none")}</>;
}

/**
 * What the snapshot records of routing, read only: the window's counts, one
 * line for each buddy of the list and the latest failures. It never calls a
 * model and reads the same in a session that cannot write. Absent data is said
 * to be not recorded, an empty window to have no records, and nothing is
 * counted from what is not there.
 */
export function RolesRecords({ health }: { health: RoutingHealth | undefined }) {
  const { t } = useTranslation("buddies");
  if (!health) return <p className="pt-[5px] text-ink3">{t("roles.records.unknown")}</p>;
  const none = t("roles.records.routers.none");
  const count = (value: number | undefined) => value ?? none;
  return <div className="flex flex-col gap-4 pt-[5px]">
    <div className="flex flex-wrap gap-x-[22px] gap-y-1 text-xs">
      {health.available === false && <Fact name={t("roles.records.unavailable")}><code className="font-mono text-xs">{health.reasonCode ?? t("roles.records.unknown")}</code></Fact>}
      {health.sampleCount === 0
        ? <p className="text-ink3">{t("roles.records.empty")}</p>
        : <>
          <Fact name={t("roles.records.failed")} hint={t("roles.records.failedHint")}>{health.failureCount} / {health.sampleCount}</Fact>
          {recordCounts(health).map(({ name, n }) => <Fact key={name} name={t(`roles.records.${name}`)}>{n}</Fact>)}
          <Fact name={t("roles.records.lastSuccess")}>{health.lastSuccessAt ? <Moment at={health.lastSuccessAt} /> : t("roles.records.never")}</Fact>
        </>}
    </div>
    {!!health.routers?.length && <ScrollArea className="max-w-[1100px]">
      <table className="w-full border-collapse">
        <caption className="pb-1.5 text-left text-xs text-ink3">{t("roles.records.routers.label")}</caption>
        <thead><tr>
          <th className={head}>{t("roles.records.routers.buddy")}</th>
          <th className={head}>{t("roles.records.routers.answered")}</th>
          <th className={head}>{t("roles.records.routers.noAnswer")}</th>
          <th className={head}>{t("roles.records.routers.consecutive")}</th>
          <th className={head}>{t("roles.records.routers.lastAnswered")}</th>
          <th className={head}>{t("roles.records.routers.lastError")}</th>
        </tr></thead>
        <tbody>{health.routers.map(entry => <tr key={entry.buddyId}>
          <td className={`${cell} font-mono text-xs`}>{entry.identity ? buddyQuad(entry.identity) : entry.buddyId}</td>
          <td className={`${cell} tabular-nums`}>{count(entry.answeredCount)}</td>
          <td className={`${cell} tabular-nums`}>{count(entry.noAnswerCount)}</td>
          <td className={`${cell} tabular-nums`}>{count(entry.consecutiveNoAnswers)}</td>
          <td className={cell}><Moment at={entry.lastAnsweredAt} /></td>
          <td className={cell}>{entry.lastError
            ? <span className="flex items-baseline gap-2"><code className="font-mono text-xs">{entry.lastError.code ?? t("roles.records.unknown")}</code><Moment at={entry.lastError.at} /></span>
            : none}</td>
        </tr>)}</tbody>
      </table>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>}
    {health.recentFailures.length > 0 && <ScrollArea className="max-w-[760px]">
      <table className="w-full border-collapse">
        <caption className="pb-1.5 text-left text-xs text-ink3">{t("roles.records.failures.label")}</caption>
        <thead><tr>
          <th className={head}>{t("roles.records.failures.at")}</th>
          <th className={head}>{t("roles.records.failures.code")}</th>
          <th className={head}>{t("roles.records.failures.run")}</th>
        </tr></thead>
        <tbody>{health.recentFailures.map(failure => <tr key={failure.decisionId}>
          <td className={cell}><Moment at={failure.at} /></td>
          <td className={cell}><span className="flex items-center gap-1.5 text-fail"><StateMark kind="fail" /><code className="font-mono text-xs">{failure.code}</code></span></td>
          <td className={`${cell} ${failure.runId ? "font-mono text-xs" : "text-ink3"}`}>{failure.runId ?? t("roles.records.failures.unrecorded")}</td>
        </tr>)}</tbody>
      </table>
      <ScrollBar orientation="horizontal" />
    </ScrollArea>}
  </div>;
}
