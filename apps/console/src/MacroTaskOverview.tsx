import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import type { MacroTaskSummary, MacroTaskTimeline } from "./macro-task-types";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Button } from "./components/ui/button";
import { Hint } from "./components/hint";
import { displayTitle } from "./macro-task-display";
import { macroTaskMetrics } from "./macro-task-metrics";
import { usageTotalsLabel } from "./usage-totals";
import { formatDuration, formatList, formatDateTime, formatMoment, formatMomentMinutes, formatNumber } from "./i18n/format";
import { StateMark } from "./components/ui/state-mark";
import { CopyableIdentifier } from "./components/CopyableIdentifier";

type Count = { key: "host" | "review" | "active" | "accepted" | "failed" | "ended"; mark: "hand" | "live" | "done" | "fail" | "idle"; value: number };

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
}) {
  const { t, i18n: { language } } = useTranslation("macroHeader");
  const [statsOpen, setStatsOpen] = useState(false);
  const statsContent = useRef<HTMLDivElement>(null);
  const shown = timeline?.macroTask ?? summary;
  useEffect(() => setStatsOpen(false), [active, shown?.macroTaskId]);
  const metrics = useMemo(() => timeline ? macroTaskMetrics(timeline) : null, [timeline, language]);
  if (!shown) return null;

  const title = displayTitle(shown.titleSource, shown.title);
  const titleText = shown.titleSource === "none" ? t("unnamed") : title.text;
  const titleHint = shown.titleSource === "task" ? t("titleFromTask", { title: title.fullText }) : shown.titleSource === "none" ? undefined : title.fullText;
  const created = shown.createdAt === null ? null : Date.parse(shown.createdAt);
  const observed = nowMs ?? (timeline ? Date.parse(timeline.observedAt) : Date.now());
  const lastActivity = shown.lastActivityAt === null ? null : Date.parse(shown.lastActivityAt);
  const clock = observed;
  const end = shown.counts.active || shown.counts.host || shown.counts.review ? clock : lastActivity;
  const elapsed = created === null || end === null ? t("unrecorded") : formatDuration(end - created, "minutes");
  const cumulative = metrics && metrics.sumMs !== null && metrics.unionMs !== null && metrics.sumMs !== metrics.unionMs
    ? metrics.sumMs : null;
  const counts: Count[] = [
    { key: "host", mark: "hand", value: shown.counts.host },
    { key: "review", mark: "hand", value: shown.counts.review },
    { key: "active", mark: "live", value: shown.counts.active },
    { key: "accepted", mark: "done", value: shown.counts.accepted },
    { key: "failed", mark: "fail", value: shown.counts.failed },
    { key: "ended", mark: "idle", value: shown.counts.ended - shown.counts.accepted - shown.counts.failed },
  ];
  const startText = created === null ? t("unrecorded") : formatMomentMinutes(created, clock);

  return <header data-slot="macro-overview-header" className="flex shrink-0 flex-col gap-2.5 border-b bg-card px-4 py-3 sm:px-6 @min-[700px]:grid @min-[700px]:grid-cols-[minmax(0,1fr)_auto] @min-[700px]:items-start @min-[700px]:gap-x-6 @min-[700px]:gap-y-1.5 @min-[700px]:pt-4.5 @min-[700px]:pb-4">
    <div className="min-w-0 flex-1 @min-[700px]:contents">
      <Button type="button" variant="ghost" size="sm" onClick={onBackToList} className="mb-1 -ml-2 hidden @max-[700px]:inline-flex">
        {t("back")}
      </Button>
      <div className="flex min-w-0 items-start gap-3 @min-[700px]:col-start-1 @min-[700px]:row-start-1">
        {listToggle}
        <div className="min-w-0 flex-1">
          <Hint text={titleHint}>
            <h2 tabIndex={0} className="min-w-0 truncate rounded-sm text-lg leading-[1.45] font-semibold tracking-[-0.01em] outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{titleText}</h2>
          </Hint>
        </div>
      </div>
        <dl data-slot="macro-overview-facts" className="mt-1.5 flex flex-wrap items-center @min-[700px]:col-span-2 @min-[700px]:row-start-2 @min-[700px]:mt-0 gap-x-5 gap-y-0.5 text-xs leading-5 tabular-nums">
          <div className="flex min-w-0 gap-1.5"><dt className="shrink-0 text-ink3">{t("project")}</dt><dd className="min-w-0 wrap-anywhere text-ink">{shown.project?.label ?? t("unrecorded")}</dd></div>
          <div className="flex min-w-0 gap-1.5"><dt className="shrink-0 text-ink3">{t("host")}</dt><dd className="min-w-0 wrap-anywhere text-ink">{shown.sourceHostId ?? t("unrecorded")}</dd></div>
          <div className="flex gap-1.5"><dt className="shrink-0 text-ink3">{t("started")}</dt><dd className="text-ink">{startText}</dd></div>
          <div className="flex gap-1.5"><dt className="shrink-0 text-ink3">{t("elapsed")}</dt><dd><Popover key={shown.macroTaskId} open={active && statsOpen} onOpenChange={setStatsOpen}>
            <PopoverTrigger asChild><button type="button" className="inline-flex items-center border-b border-dotted border-ink3 text-left text-ink transition-colors duration-150 hover:border-ink hover:text-ink focus-visible:rounded-sm focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none" aria-label={t("statsTitle")}>{elapsed}</button></PopoverTrigger>
            <PopoverContent ref={statsContent} align="start" aria-label={t("statsTitle")} className="w-[34rem] max-w-[calc(100vw-2rem)] rounded-none"
              onOpenAutoFocus={event => { event.preventDefault(); statsContent.current?.focus(); }}>
              <div data-slot="time-stats-body" className="grid gap-2 wrap-anywhere text-sm">
                <dl data-slot="time-stats-instants" className="flex flex-wrap gap-x-4 gap-y-1 text-xs tabular-nums">
                  <div className="flex gap-1.5"><dt className="text-ink3">{t("started")}</dt><dd>{created === null ? t("unrecorded") : <time dateTime={shown.createdAt!}>{formatDateTime(created)}</time>}</dd></div>
                  <div className="flex gap-1.5"><dt className="text-ink3">{t("lastActivity")}</dt><dd>{lastActivity === null ? t("unrecorded") : <time dateTime={shown.lastActivityAt!}>{formatDateTime(lastActivity)}</time>}</dd></div>
                </dl>
                {shown.kind === "standalone" && <p>{t("standalone")}</p>}
                {shown.description && <p>{shown.description}</p>}
                <h3 className="font-semibold">{t("statsTitle")}</h3>
                {!timeline
                  ? <p className="text-xs text-ink2">{loading ? t("statsLoading") : t("statsMissing")}</p>
                  : <>
                    <dl data-slot="time-stats-values" className="grid grid-cols-[max-content_minmax(0,1fr)] items-baseline gap-x-4 gap-y-1 text-xs tabular-nums">
                      <dt><Hint text={t("span")}><span tabIndex={0} className="rounded-sm text-ink3 outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("spanLabel")}</span></Hint></dt>
                      <dd>{metrics?.totalSpanMs != null ? formatDuration(metrics.totalSpanMs) : t("unrecorded")}</dd>
                      <dt><Hint text={t("execution")}><span tabIndex={0} className="rounded-sm text-ink3 outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("executionLabel")}</span></Hint></dt>
                      <dd>{metrics?.unionMs != null ? formatDuration(metrics.unionMs) : t("unrecorded")}</dd>
                      {cumulative !== null && <><dt><Hint text={t("cumulative")}><span tabIndex={0} className="rounded-sm text-ink3 outline-none focus-visible:ring-2 focus-visible:ring-ring">{t("cumulativeLabel")}</span></Hint></dt><dd>{formatDuration(cumulative)}</dd></>}
                    </dl>
                    <p className="text-xs">{t("usage")}{usageTotalsLabel(metrics!.usage)}</p>
                    {metrics!.runningCount > 0 && <p className="text-xs text-ink2">{t("running", { count: metrics!.runningCount, time: formatMoment(Date.parse(timeline.observedAt), clock) })}</p>}
                    {metrics!.unknownEndCount > 0 && <p className="text-xs text-ink2">{t("unknownEnd", { count: metrics!.unknownEndCount })}</p>}
                    {metrics!.limitations.length > 0 && <p data-slot="time-stats-limitations" className="text-xs text-ink2">{t("limitations")}{formatList(metrics!.limitations.map(entry => t(`limitation.${entry.key}`)))}</p>}
                    {stale && <p className="text-xs text-ink2">{t("stale", { time: formatMoment(Date.parse(timeline.observedAt), clock) })}</p>}
                  </>}
                <p className="mt-2 text-xs text-ink2">{t("sourceHost", { host: shown.sourceHostId || t("unrecorded") })} · {t("currentHost", { host: shown.currentHostIds.length ? formatList(shown.currentHostIds) : t("unrecorded") })}</p>
              </div>
            </PopoverContent>
          </Popover></dd></div>
          <div className="min-w-0"><dd><CopyableIdentifier value={shown.macroTaskId}/></dd></div>
        </dl>
    </div>
    <div className="flex min-w-0 shrink-0 items-start gap-4 @min-[700px]:col-start-2 @min-[700px]:row-start-1 @max-[700px]:w-full @max-[700px]:items-center @min-[700px]:gap-10">
      <dl data-slot="macro-overview-counts" className="flex flex-wrap gap-x-4 gap-y-1.5 @max-[700px]:min-w-0 @max-[700px]:flex-1 @min-[700px]:gap-x-5">
        {counts.filter(count => count.value > 0).map(count => <div key={count.key} data-slot={`macro-overview-count-${count.key}`} className="flex flex-col items-end gap-0.5 whitespace-nowrap @max-[700px]:flex-row @max-[700px]:items-center @max-[700px]:gap-1.5">
          <dd className={`text-lg font-semibold leading-tight tabular-nums @max-[700px]:text-sm ${count.mark === "hand" ? "text-hand" : count.mark === "live" ? "text-foreground state-breathe" : count.mark === "fail" ? "text-fail" : "text-ink"}`}>{formatNumber(count.value)}</dd>
          <dt className="flex items-center gap-1.5 text-xs text-ink2"><StateMark kind={count.mark} />{t(count.key === "host" ? "hostWait" : count.key)}</dt>
        </div>)}
      </dl>
      {headerActions}
    </div>
  </header>;
}
