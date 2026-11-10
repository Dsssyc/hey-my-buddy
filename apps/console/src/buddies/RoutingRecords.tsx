import { useState } from "react";
import { Copy, Check, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatShortDateTime } from "../i18n/format";
import type { RoutingHealth } from "../types";
import { tasksHash } from "../task-detail/location";
import { recordCounts } from "./roles";
import { RecordRegion } from "./RecordRegion";
import { RouterFailure } from "./RouterFailure";


const cell = "pr-8 text-xs whitespace-nowrap";

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
  const failures = [...health.recentFailures].sort((a, b) => Date.parse(b.at) - Date.parse(a.at));
  return <div className="space-y-5">
    {health.available === false && <p className="text-xs text-ink2">{t("roles.records.unavailable")} · {health.reason ? <RouterFailure failure={health.reason} /> : t("roles.records.unknown")}</p>}
    {health.sampleCount === 0 ? <p className="text-xs text-ink2">{t("roles.records.empty")}</p> : <div className="routing-readings" data-slot="routing-summary">
      <div><div className="mb-1 text-xs text-ink3">{t("roles.records.failed")}</div><div className={`flex items-center gap-2 text-xl font-semibold tabular-nums ${health.failureCount ? "text-fail" : ""}`}>
        {!!health.failureCount && <StateMark kind="fail" />}{health.failureCount} / {health.sampleCount}</div></div>
      <div><div className="mb-1 text-xs text-ink3">{t("roles.records.consecutiveFailures")}</div><div className="text-xl font-semibold tabular-nums">{health.consecutiveFailures}</div></div>
      {recordCounts(health).filter(item => item.name !== "consecutiveFailures").map(item => <div key={item.name}><div className="mb-1 text-xs text-ink3">{t(`roles.records.${item.name}`)}</div><div className="text-xl font-semibold tabular-nums">{item.n}</div></div>)}
      <div><div className="mb-1 text-xs text-ink3">{t("roles.records.lastSuccess")}</div><div className="text-xl font-semibold"><Moment at={health.lastSuccessAt} /></div></div>
    </div>}
    {!!failures.length && <div>
      <h4 className="pb-2 text-sm font-medium">{t("roles.records.failures.label")}</h4>
      <RecordRegion header={false} aria-label={t("roles.records.failures.label")} data-slot="routing-failures-region">
      <table className="routing-failures w-full min-w-[620px]" aria-label={t("roles.records.failures.label")}>
      <thead className="sr-only"><tr><th>{t("roles.records.failures.at")}</th><th>{t("roles.records.failures.code")}</th><th>{t("roles.records.failures.run")}</th></tr></thead>
      <tbody>{failures.map(row => <tr key={row.decisionId}>
        <td className={cell}><Moment at={row.at} /></td>
        <td className={cell}><span className="inline-flex max-w-full items-center gap-2"><StateMark kind="fail" /><RouterFailure failure={row.code} /></span></td>
        <td className={cell}>{row.runId && row.macroTaskId ? <span className="inline-flex max-w-full items-center gap-1"><a className="min-w-0 truncate underline decoration-line2 underline-offset-2 transition-colors duration-150 motion-reduce:transition-none hover:decoration-foreground" href={tasksHash({ macroTaskId: row.macroTaskId, runId: row.runId, step: null })}>{row.title}</a><CopyId value={row.runId} /></span> : t("roles.records.failures.unrecorded")}</td>
      </tr>)}</tbody>
      </table></RecordRegion>
    </div>}
  </div>;
}
