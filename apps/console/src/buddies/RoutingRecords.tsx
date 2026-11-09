import { useState } from "react";
import { Copy, Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatShortDateTime } from "../i18n/format";
import { reasonText } from "../i18n/reason";
import type { RoutingHealth } from "../types";
import { tasksHash } from "../task-detail/location";
import { buddyQuad, recordCounts } from "./roles";
import { QuantBar } from "./QuantBar";
import { routingCodeText } from "./routing-code";

const head = "border-b py-3 pr-4 text-left text-xs font-normal text-ink3";
const cell = "border-b py-3 pr-4 align-top text-xs";

function Moment({ at }: { at?: string | null }) {
  return at ? <time dateTime={at} className="tabular-nums whitespace-nowrap">{formatShortDateTime(Date.parse(at))}</time> : <>—</>;
}

function CopyId({ value }: { value: string }) {
  const { t } = useTranslation("buddies");
  const [feedback, setFeedback] = useState<"copied" | "failed" | null>(null);
  async function copy() {
    try { await navigator.clipboard.writeText(value); setFeedback("copied"); }
    catch { setFeedback("failed"); }
  }
  return <><Hint text={t("routerWork.copyId")}><Button variant="ghost" size="icon-sm" aria-label={t("routerWork.copyId")} className="text-ink3" onClick={() => void copy()}>
    {feedback === "copied" ? <Check /> : feedback === "failed" ? <X className="text-fail" /> : <Copy />}
  </Button></Hint><span role={feedback ? "status" : undefined} className="sr-only">{feedback && t(`routerWork.${feedback}`)}</span></>;
}

/** Read-only routing outcomes, with task links bound to the service's own task grouping. */
export function RoutingRecords({ health }: { health?: RoutingHealth }) {
  const { t } = useTranslation("buddies");
  if (!health) return <p className="text-xs text-ink3">{t("roles.records.unknown")}</p>;
  const maximum = Math.max(0, ...(health.routers ?? []).map(row => (row.answeredCount ?? 0) + (row.noAnswerCount ?? 0)));
  return <div className="space-y-5">
    <div className="flex flex-wrap items-center gap-x-6 gap-y-2 text-xs text-ink2" data-slot="routing-summary">
      {health.available === false && <span>{t("roles.records.unavailable")} · {health.reason ? reasonText(health.reason) : t("roles.records.unknown")}</span>}
      {health.sampleCount === 0 ? <span>{t("roles.records.empty")}</span> : <>
        <span className="inline-flex items-center gap-1.5"><StateMark kind="fail" />{t("roles.records.failed")} <span className={health.failureCount ? "text-fail tabular-nums" : "tabular-nums"}>{health.failureCount} / {health.sampleCount}</span></span>
        <span>{t("roles.records.consecutiveFailures")} <b className="font-normal tabular-nums">{health.consecutiveFailures}</b></span>
        {recordCounts(health).filter(item => item.name !== "consecutiveFailures").map(item => <span key={item.name}>{t(`roles.records.${item.name}`)} {item.n}</span>)}
        <span>{t("roles.records.lastSuccess")} <Moment at={health.lastSuccessAt} /></span>
      </>}
    </div>
    {!!health.routers?.length && <table className="w-full table-fixed border-collapse" aria-label={t("roles.records.routers.label")}>
      <thead><tr><th className={`${head} w-[34%]`}>{t("roles.records.routers.buddy")}</th><th className={`${head} w-[30%]`}>{t("routerWork.answers")}</th><th className={`${head} w-[17%]`}>{t("roles.records.routers.lastAnswered")}</th><th className={head}>{t("roles.records.routers.lastError")}</th></tr></thead>
      <tbody>{health.routers.map(row => <tr key={row.buddyId}>
        <td className={`${cell} font-mono wrap-anywhere`}>{row.identity ? buddyQuad(row.identity) : row.buddyId}</td>
        <td className={cell}><Hint text={t("routerWork.answerHint", { answered: row.answeredCount ?? t("roles.records.unknown"), missing: row.noAnswerCount ?? t("roles.records.unknown"), consecutive: row.consecutiveNoAnswers ?? t("roles.records.unknown") })}>
          <span tabIndex={0} className="inline-flex flex-wrap items-center gap-2 tabular-nums">
            <QuantBar maximum={maximum} parts={[{ value: row.answeredCount ?? null, tone: "ink" }, { value: row.noAnswerCount ?? null, tone: "fail" }]} />
            <span>{row.answeredCount ?? "—"} / <span className={row.noAnswerCount ? "inline-flex items-center gap-1 text-fail" : ""}>{!!row.noAnswerCount && <StateMark kind="fail" />}{row.noAnswerCount ?? "—"}</span></span>
          </span>
        </Hint></td>
        <td className={cell}><Moment at={row.lastAnsweredAt} /></td>
        <td className={`${cell} wrap-anywhere`}>{row.lastError?.code ? routingCodeText(row.lastError.code) : t("roles.records.unknown")}</td>
      </tr>)}</tbody>
    </table>}
    {!!health.recentFailures.length && <table className="w-full table-fixed border-collapse" aria-label={t("roles.records.failures.label")}>
      <caption className="pb-2 text-left text-sm font-medium">{t("roles.records.failures.label")}</caption>
      <thead><tr><th className={`${head} w-[20%]`}>{t("roles.records.failures.at")}</th><th className={`${head} w-[30%]`}>{t("roles.records.failures.code")}</th><th className={head}>{t("roles.records.failures.run")}</th></tr></thead>
      <tbody>{health.recentFailures.map(row => <tr key={row.decisionId}>
        <td className={cell}><Moment at={row.at} /></td>
        <td className={cell}><span className="inline-flex items-start gap-2"><StateMark kind="fail" className="mt-0.5" />{routingCodeText(row.code)}</span></td>
        <td className={cell}>{row.runId && row.macroTaskId ? <span className="inline-flex max-w-full items-center gap-1"><a className="min-w-0 wrap-anywhere underline decoration-line2 underline-offset-2 transition-colors duration-150 motion-reduce:transition-none hover:decoration-foreground" href={tasksHash({ macroTaskId: row.macroTaskId, runId: row.runId, step: null })}>{row.title}</a><CopyId value={row.runId} /></span> : t("roles.records.failures.unrecorded")}</td>
      </tr>)}</tbody>
    </table>}
  </div>;
}
