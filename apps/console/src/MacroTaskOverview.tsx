import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskSummary, MacroTaskTimeline } from "./macro-task-types";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Hint } from "./components/hint";
import { displayTitle } from "./macro-task-display";
import { macroTaskMetrics } from "./macro-task-metrics";
import { formatDuration, formatDurationMinutes, formatList, formatMoment, formatMomentMinutes, formatNumber } from "./i18n/format";
import { StateMark } from "./components/ui/state-mark";
import { CopyableIdentifier } from "./components/CopyableIdentifier";
import "./macro-header.css";

type Count = { key: "host" | "review" | "active" | "accepted" | "ended"; mark: "hand" | "live" | "done" | "idle"; value: number };

/** One macro task's title, context, state counts and expandable recorded facts. */
export function MacroTaskOverview({ summary, timeline, loading, stale, active = true, nowMs, onBackToList, listToggle, headerActions }: {
  summary: MacroTaskSummary | null;
  timeline: MacroTaskTimeline | null;
  loading: boolean;
  stale: boolean;
  active?: boolean;
  nowMs?: number;
  onBackToList: () => void;
  listToggle?: ReactNode;
  headerActions?: ReactNode;
  compact?: boolean;
}) {
  const { t } = useTranslation("macroHeader");
  const [statsOpen, setStatsOpen] = useState(false);
  const statsContent = useRef<HTMLDivElement>(null);
  const shown = timeline?.macroTask ?? summary;
  useEffect(() => setStatsOpen(false), [active, shown?.macroTaskId]);
  const metrics = useMemo(() => timeline ? macroTaskMetrics(timeline) : null, [timeline]);
  if (!shown) return null;

  const title = displayTitle(shown.titleSource, shown.title);
  const titleText = shown.titleSource === "none" ? t("unnamed") : title.text;
  const created = shown.createdAt === null ? null : Date.parse(shown.createdAt);
  const observed = nowMs ?? (timeline ? Date.parse(timeline.observedAt) : Date.now());
  const lastActivity = shown.lastActivityAt === null ? null : Date.parse(shown.lastActivityAt);
  const clock = observed;
  const end = shown.counts.active || shown.counts.host || shown.counts.review ? clock : lastActivity;
  const elapsed = created === null || end === null ? t("unrecorded") : formatDurationMinutes(end - created);
  const cumulative = metrics && metrics.sumMs !== null && metrics.unionMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;
  const counts: Count[] = [
    { key: "host", mark: "hand", value: shown.counts.host },
    { key: "review", mark: "hand", value: shown.counts.review },
    { key: "active", mark: "live", value: shown.counts.active },
    { key: "accepted", mark: "done", value: shown.counts.accepted },
    { key: "ended", mark: "idle", value: shown.counts.ended - shown.counts.accepted },
  ];
  const startText = created === null ? t("unrecorded") : formatMomentMinutes(created, clock);

  return <header className="tl-head macro-overview-header" data-slot="macro-overview-header">
    <div className="macro-overview-leading">
      {listToggle}
      <button type="button" className="macro-overview-back" onClick={onBackToList}>{t("back")}</button>
      <div className="macro-overview-identity">
        {title.fromTask
          ? <Hint text={t("titleFromTask", { title: title.fullText })}><h2 tabIndex={0}>{title.text}</h2></Hint>
          : <h2>{titleText}</h2>}
        <dl className="macro-overview-facts">
          <div><dt>{t("project")}</dt><dd>{shown.project?.label ?? t("unrecorded")}</dd></div>
          <div><dt>{t("host")}</dt><dd>{shown.sourceHostId ?? t("unrecorded")}</dd></div>
          <div><dt>{t("started")}</dt><dd>{startText}</dd></div>
          <div><dt>{t("elapsed")}</dt><dd><Popover key={shown.macroTaskId} open={active && statsOpen} onOpenChange={setStatsOpen}>
            <PopoverTrigger asChild><button type="button" className="time-stats" aria-label={t("statsTitle")}>{elapsed}</button></PopoverTrigger>
            <PopoverContent ref={statsContent} align="start" aria-label={t("statsTitle")} className="time-stats-popover w-[34rem] max-w-[min(34rem,calc(100vw-2rem))] rounded-none"
              onOpenAutoFocus={event => { event.preventDefault(); statsContent.current?.focus(); }}>
              <div className="time-stats-body">
                {shown.kind === "standalone" && <p>{t("standalone")}</p>}
                {shown.description && <p>{shown.description}</p>}
                <CopyableIdentifier value={shown.macroTaskId} />
                <h3>{t("statsTitle")}</h3>
                {!timeline
                  ? <p className="small muted">{loading ? t("statsLoading") : t("statsMissing")}</p>
                  : <>
                    <p className="time-stat">{t("span")}{metrics?.totalSpanMs != null ? formatDuration(metrics.totalSpanMs) : t("unrecorded")}</p>
                    <p className="time-stat">{t("execution")}{metrics?.unionMs != null ? formatDuration(metrics.unionMs) : t("unrecorded")}</p>
                    {cumulative !== null && <p className="time-stat">{t("cumulative")}{formatDuration(cumulative)}</p>}
                    {metrics!.runningCount > 0 && <p className="small muted">{t("running", { count: metrics!.runningCount, time: formatMoment(Date.parse(timeline.observedAt), clock) })}</p>}
                    {metrics!.unknownEndCount > 0 && <p className="small muted">{t("unknownEnd", { count: metrics!.unknownEndCount })}</p>}
                    {metrics!.limitations.length > 0 && <p className="small metric-warn">{t("limitations")}{formatList(metrics!.limitations.map(entry => t(`limitation.${entry.key}`)))}</p>}
                    {stale && <p className="small muted">{t("stale", { time: formatMoment(Date.parse(timeline.observedAt), clock) })}</p>}
                  </>}
                <p className="small muted record-source">{t("sourceHost", { host: shown.sourceHostId || t("unrecorded") })} · {t("currentHost", { host: shown.currentHostIds.length ? formatList(shown.currentHostIds) : t("unrecorded") })}</p>
              </div>
            </PopoverContent>
          </Popover></dd></div>
        </dl>
      </div>
    </div>
    <div className="macro-overview-trailing">
      <dl className="macro-overview-counts">
        {counts.filter(count => count.value > 0).map(count => <div key={count.key} className={`macro-overview-count macro-overview-count-${count.key}`}>
          <dd>{formatNumber(count.value)}</dd><dt><StateMark kind={count.mark} />{t(count.key === "host" ? "hostWait" : count.key)}</dt>
        </div>)}
      </dl>
      {headerActions}
    </div>
  </header>;
}
