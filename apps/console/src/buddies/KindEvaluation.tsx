import { useState } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Hint } from "@/components/hint";
import { formatCompact, formatDuration, formatNumber } from "../i18n/format";
import { locale } from "../i18n";
import { tasksHash } from "../task-detail/location";
import { TASK_TYPES } from "../types";
import type { DomainNode, EvaluationStatistics, KindEvaluation as KindEvaluationData } from "../types";

type OpenTask = (macroTaskId: string, runId: string) => void;

/**
 * The rows of the user's domain tree that carry this buddy kind's evidence: the
 * sampled nodes and the ancestors that place them. A branch without a sample
 * anywhere below it is left out.
 */
function visibleDomains(nodes: DomainNode[], sampled: ReadonlySet<string>,
                        collapsed: ReadonlySet<string>): { node: DomainNode; depth: number; children: boolean }[] {
  const active = nodes.filter(node => node.mergedIntoId === null);
  const byId = new Map(active.map(node => [node.nodeId, node]));
  const included = new Set<string>();
  for (const nodeId of sampled) {
    let node = byId.get(nodeId);
    while (node && !included.has(node.nodeId)) {
      included.add(node.nodeId);
      node = node.parentId === null ? undefined : byId.get(node.parentId);
    }
  }
  const children = new Map<string | null, DomainNode[]>();
  for (const node of active) {
    if (!included.has(node.nodeId)) continue;
    const parent = node.parentId;
    children.set(parent, [...(children.get(parent) ?? []), node]);
  }
  const result: { node: DomainNode; depth: number; children: boolean }[] = [];
  const visit = (node: DomainNode, depth: number) => {
    const descendants = children.get(node.nodeId) ?? [];
    result.push({ node, depth, children: descendants.length > 0 });
    if (!collapsed.has(node.nodeId)) descendants.forEach(child => visit(child, depth + 1));
  };
  (children.get(null) ?? []).forEach(node => visit(node, 0));
  return result;
}

function Overall({ statistics }: { statistics: EvaluationStatistics }) {
  const { t } = useTranslation("buddies");
  const rate = (value: number | null) => value === null ? t("evaluation.noMedian")
    : new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(value);
  const count = (value: number) => formatNumber(value);
  const median = (value: number | null, duration = false) => value === null ? t("evaluation.noMedian")
    : duration ? formatDuration(value * 1000) : t("evaluation.tokens", { n: formatCompact(value) });
  const outcomes = [
    [t("evaluation.samples"), count(statistics.samples)],
    [t("evaluation.successes"), count(statistics.successes)],
    [t("evaluation.firstPasses"), `${count(statistics.firstPasses)} · ${rate(statistics.firstPassRate)}`],
    [t("evaluation.reworkRounds"), count(statistics.reworkRounds)],
    [t("evaluation.failures"), count(statistics.failures)],
  ];
  const medians = [
    [t("evaluation.medianDelivery"), median(statistics.medianDeliverySeconds, true)],
    [t("evaluation.medianInput"), median(statistics.usage.medianInputTokens)],
    [t("evaluation.medianCached"), median(statistics.usage.medianCachedInputTokens)],
    [t("evaluation.medianOutput"), median(statistics.usage.medianOutputTokens)],
  ];
  return <section aria-label={t("evaluation.statistics")} className="border-t border-line pt-4 min-[900px]:border-t-0 min-[900px]:border-l min-[900px]:pt-0 min-[900px]:pl-5">
    <h4 className="mb-2 text-xs font-medium text-ink2">{t("evaluation.statistics")}</h4>
    <dl className="grid grid-cols-2 gap-x-4 gap-y-2 sm:grid-cols-5">
      {outcomes.map(([label, value]) => <div key={label} className="min-w-0">
        <dt className="text-xs text-ink3">{label}</dt><dd className="text-sm font-medium tabular-nums">{value}</dd>
      </div>)}
    </dl>
    <dl className="mt-4 grid grid-cols-1 gap-x-5 gap-y-2 border-t border-line pt-3 text-xs sm:grid-cols-2">
      {medians.map(([label, value]) => <div key={label} className="flex flex-wrap justify-between gap-x-2">
        <dt className="text-ink3">{label}</dt><dd className="tabular-nums">{value}</dd>
      </div>)}
    </dl>
  </section>;
}

/** The selected buddy's kind-wide summary and cumulative evidence, folded inside its settings. */
export function KindEvaluation({ evaluation, domains, onOpenTask }: {
  evaluation?: KindEvaluationData; domains: DomainNode[]; onOpenTask?: OpenTask;
}) {
  const { t } = useTranslation("buddies");
  const [open, setOpen] = useState(false);
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const sampled = new Set(Object.entries(evaluation?.statistics.byDomain ?? {})
    .filter(([, statistics]) => statistics.samples > 0).map(([nodeId]) => nodeId));
  const rows = open ? visibleDomains(domains, sampled, collapsed) : [];
  const rate = (value: number | null) => value === null ? "—"
    : new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(value);

  return <section data-slot="kind-evaluation" className="mt-4 border-t border-line pt-3">
    <button type="button" aria-expanded={open} onClick={() => setOpen(previous => !previous)}
      className="flex min-h-7 w-full items-center gap-2 rounded-sm py-1 text-left text-xs font-medium text-foreground outline-none hover:text-ink2 transition-colors duration-150 motion-reduce:transition-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <ChevronRight className={cn("size-3.5 shrink-0 transition-transform motion-reduce:transition-none", open && "rotate-90")} aria-hidden="true" />
      {t("evaluation.toggle")}
      {evaluation && <span className="ml-auto font-normal text-ink3 tabular-nums">{formatNumber(evaluation.statistics.overall.samples)}</span>}
    </button>
    {open && <div className="mt-3 space-y-4">
      {!evaluation ? <p className="text-xs text-ink3">{t("evaluation.noEvidence")}</p> : <>
        <div className="grid min-w-0 gap-5 min-[900px]:grid-cols-2">
        <section aria-label={t("evaluation.summary")} className="min-w-0">
          <h4 className="mb-1.5 text-xs font-medium text-ink2">{t("evaluation.summary")}</h4>
          {evaluation.summary?.text ? <p className="whitespace-pre-wrap text-sm leading-relaxed text-foreground">{evaluation.summary.text}</p>
            : <p className="text-xs text-ink3">{t("evaluation.noSummary")}</p>}
          {!!evaluation.summary?.citations.length && <div className="mt-2 flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs">
            <span className="text-ink3">{t("evaluation.citedTasks")}</span>
            {evaluation.summary.citations.map(citation => {
              const macroTaskId = citation.macroTaskId ?? `run:${citation.runId}`;
              return <a key={citation.runId} href={tasksHash({ macroTaskId, runId: citation.runId, step: null })}
                className="max-w-full truncate rounded-sm text-foreground underline decoration-line2 underline-offset-2 hover:decoration-foreground focus-visible:outline-2 focus-visible:outline-ring"
                onClick={event => { if (onOpenTask) { event.preventDefault(); onOpenTask(macroTaskId, citation.runId); } }}>
                {citation.title || citation.runId}
              </a>;
            })}
          </div>}
        </section>
        <Overall statistics={evaluation.statistics.overall} />
        </div>
        <section aria-label={t("evaluation.domainTable")} className="border-t border-line pt-4">
          <h4 className="mb-2 text-xs font-medium text-ink2">{t("evaluation.domainTable")}</h4>
          {rows.length === 0 ? <p className="text-xs text-ink3">
            {t(domains.some(node => node.mergedIntoId === null) ? "evaluation.noSample" : "evaluation.noDomains")}
          </p> :
            <div data-slot="evaluation-table-scroll" className="max-w-full overflow-x-auto">
              <table className="min-w-[960px] w-full border-collapse text-xs">
                <thead><tr className="border-y border-line text-ink3">
                  <th scope="col" className="sticky left-0 z-10 min-w-40 bg-card py-2 pr-3 text-left font-medium">{t("evaluation.domain")}</th>
                  {TASK_TYPES.map(type => <th key={type} scope="col" className="min-w-[112px] px-2 py-2 text-left font-medium">{t(`evaluation.taskTypes.${type}`)}</th>)}
                </tr></thead>
                <tbody>{rows.map(({ node, depth, children }) => <tr key={node.nodeId} className="border-b border-line/70 align-top">
                  <th scope="row" className="sticky left-0 z-10 bg-card py-2 pr-3 text-left font-normal">
                    <div className="flex items-start gap-1" style={{ paddingInlineStart: `${depth * 1.1}rem` }}>
                      {children ? <Hint text={t(collapsed.has(node.nodeId) ? "evaluation.unfoldDomain" : "evaluation.foldDomain", { name: node.name })}><button type="button" aria-expanded={!collapsed.has(node.nodeId)} aria-label={node.name}
                        onClick={() => setCollapsed(previous => {
                          const next = new Set(previous);
                          if (next.has(node.nodeId)) next.delete(node.nodeId); else next.add(node.nodeId);
                          return next;
                        })} className="flex size-7 shrink-0 items-center justify-center rounded-sm text-ink2 outline-none hover:bg-sunk focus-visible:ring-2 focus-visible:ring-ring transition-colors duration-150 motion-reduce:transition-none">
                        <ChevronRight className={cn("size-3.5 transition-transform motion-reduce:transition-none", !collapsed.has(node.nodeId) && "rotate-90")} aria-hidden="true" />
                      </button></Hint> : <span className="w-7 shrink-0" />}
                      <span className="min-w-0 break-words">{node.name}</span>
                      {evaluation.statistics.byDomain[node.nodeId]?.samples ? <span className="ml-auto text-ink3 tabular-nums">{formatNumber(evaluation.statistics.byDomain[node.nodeId].samples)}</span> : null}
                    </div>
                  </th>
                  {TASK_TYPES.map(type => {
                    const cell = evaluation.statistics.byDomainTaskType[node.nodeId]?.[type];
                    return <td key={type} className="px-2 py-2 tabular-nums" aria-label={!cell?.samples ? t("evaluation.noSample") : undefined}>
                      {cell?.samples ? <span aria-label={t("evaluation.cell", { samples: formatNumber(cell.samples), rate: rate(cell.firstPassRate), rework: formatNumber(cell.reworkRounds) })}>
                        <span className="block font-medium">{formatNumber(cell.samples)} · {rate(cell.firstPassRate)}</span>
                        <span className="block text-ink3">{t("evaluation.reworkRounds")} {formatNumber(cell.reworkRounds)}</span>
                      </span> : null}
                    </td>;
                  })}
                </tr>)}</tbody>
              </table>
            </div>}
        </section>
      </>}
    </div>}
  </section>;
}
