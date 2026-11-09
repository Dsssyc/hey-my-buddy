import { useState, type ReactNode } from "react";
import { ChevronRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { StateMark } from "@/components/ui/state-mark";
import { Fold } from "../task-detail/parts";
import { formatCompact, formatDuration, formatNumber, formatDateTime } from "../i18n/format";
import { locale } from "../i18n";
import { TASK_TYPES } from "../types";
import type { DomainNode, EvaluationStatistics, KindEvaluation, Model } from "../types";
import { QuantBarDisclosure } from "./QuantBar";

function sampledDomains(nodes: DomainNode[], sampled: ReadonlySet<string>, collapsed: ReadonlySet<string>) {
  const active = nodes.filter(node => node.mergedIntoId === null);
  const byId = new Map(active.map(node => [node.nodeId, node]));
  const included = new Set<string>();
  for (const id of sampled) {
    let node = byId.get(id);
    while (node && !included.has(node.nodeId)) {
      included.add(node.nodeId);
      node = node.parentId === null ? undefined : byId.get(node.parentId);
    }
  }
  const children = new Map<string | null, DomainNode[]>();
  for (const node of active) if (included.has(node.nodeId))
    children.set(node.parentId, [...(children.get(node.parentId) ?? []), node]);
  const rows: { node: DomainNode; depth: number; hasChildren: boolean }[] = [];
  const visit = (node: DomainNode, depth: number) => {
    const descendants = children.get(node.nodeId) ?? [];
    rows.push({ node, depth, hasChildren: descendants.length > 0 });
    if (!collapsed.has(node.nodeId)) descendants.forEach(child => visit(child, depth + 1));
  };
  (children.get(null) ?? []).forEach(node => visit(node, 0));
  return rows;
}

function Tally({ statistics, preferenceKey, subdued = false }: { statistics: EvaluationStatistics; preferenceKey: string; subdued?: boolean }) {
  const { t } = useTranslation("buddies");
  const reworked = statistics.successes - statistics.firstPasses;
  const values = [
    { label: t("evaluation.firstPasses"), value: statistics.firstPasses, tone: "green" as const },
    { label: t("evaluation.reworkedSuccesses"), value: reworked, tone: "amber" as const },
    { label: t("evaluation.failures"), value: statistics.failures, tone: "sample-pink" as const },
  ];
  return <div data-slot="evaluation-tally" className={subdued ? "opacity-70" : undefined}>
    <div className="flex items-baseline gap-2"><span className="text-xs text-ink3">{t("evaluation.samples")}</span><strong className="text-lg font-semibold tabular-nums">{formatNumber(statistics.samples)}</strong></div>
    <div className="mt-2"><QuantBarDisclosure width={300} maximum={statistics.samples} parts={values.map(({ value, tone }) => ({ value, tone }))}
      label={`${t("evaluation.samples")} ${formatNumber(statistics.samples)}; ${t("evaluation.failures")} ${formatNumber(statistics.failures)}`} preferenceKey={`evaluation.${preferenceKey}`}>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {values.map(({ label, value }, index) => <span key={label} className={`inline-flex items-center gap-1.5 ${index === 2 ? "text-fail" : ""}`}>
          {index === 2 && <StateMark kind="fail" />}{label} {formatNumber(value)}
        </span>)}
        <span>{t("evaluation.reworkRounds")} {formatNumber(statistics.reworkRounds)}</span>
      </div>
      <div className="mt-3"><Medians statistics={statistics} /></div>
    </QuantBarDisclosure></div>
  </div>;
}

function Medians({ statistics }: { statistics: EvaluationStatistics }) {
  const { t } = useTranslation("buddies");
  const tokens = (value: number | null) => value === null ? t("evaluation.noMedian") : t("evaluation.tokens", { n: formatCompact(Math.round(value)) });
  const entries = [
    [t("evaluation.medianDeliveryShort"), statistics.medianDeliverySeconds === null ? t("evaluation.noMedian") : formatDuration(statistics.medianDeliverySeconds * 1000)],
    [t("evaluation.medianInputShort"), tokens(statistics.usage.medianInputTokens)],
    [t("evaluation.medianCachedShort"), tokens(statistics.usage.medianCachedInputTokens)],
    [t("evaluation.medianOutputShort"), tokens(statistics.usage.medianOutputTokens)],
  ];
  return <div className="flex items-baseline gap-5 overflow-x-auto whitespace-nowrap border-t border-line pt-3 text-xs">
    <span className="font-medium text-ink2">{t("evaluation.medians")}</span>
    {entries.map(([label, value]) => <span key={label} className="whitespace-nowrap text-ink3">{label} <span className="tabular-nums text-foreground">{value}</span></span>)}
  </div>;
}

function DomainTable({ evaluation, domains }: { evaluation: KindEvaluation; domains: DomainNode[] }) {
  const { t } = useTranslation("buddies");
  const [collapsed, setCollapsed] = useState<ReadonlySet<string>>(() => new Set());
  const sampled = new Set(Object.entries(evaluation.statistics.byDomain).filter(([, value]) => value.samples > 0).map(([id]) => id));
  const rows = sampledDomains(domains, sampled, collapsed);
  const rate = (value: number | null) => value === null ? "—" : new Intl.NumberFormat(locale, { style: "percent", maximumFractionDigits: 0 }).format(value);
  return <section aria-label={t("evaluation.domainTable")} className="border-t border-line pt-5">
    <h3 className="mb-3 text-sm font-medium">{t("evaluation.domainTable")}</h3>
    {!rows.length ? <p className="text-xs text-ink3">{t(domains.some(node => node.mergedIntoId === null) ? "evaluation.noSample" : "evaluation.noDomains")}</p>
      : <div className="max-w-full overflow-x-auto"><table className="w-full min-w-[900px] border-collapse text-xs">
        <thead><tr className="border-y border-line text-ink3"><th scope="col" className="sticky left-0 z-10 min-w-40 bg-card py-2 pr-3 text-left font-medium">{t("evaluation.domain")}</th>
          {TASK_TYPES.map(type => <th key={type} scope="col" className="min-w-[102px] px-2 py-2 text-left font-medium">{t(`evaluation.taskTypes.${type}`)}</th>)}
        </tr></thead>
        <tbody>{rows.map(({ node, depth, hasChildren }) => <tr key={node.nodeId} className="border-b border-line/70 align-middle">
          <th scope="row" className="sticky left-0 z-10 bg-card py-2 pr-3 text-left font-normal"><div className="flex items-center gap-1" style={{ paddingInlineStart: `${depth * 1.1}rem` }}>
            {hasChildren ? <button type="button" aria-label={node.name} aria-expanded={!collapsed.has(node.nodeId)}
              onClick={() => setCollapsed(previous => { const next = new Set(previous); if (next.has(node.nodeId)) next.delete(node.nodeId); else next.add(node.nodeId); return next; })}
              className="flex size-7 shrink-0 items-center justify-center text-ink2 outline-none transition-colors duration-150 motion-reduce:transition-none hover:bg-sunk focus-visible:ring-2 focus-visible:ring-ring">
              <ChevronRight aria-hidden="true" className={`size-3.5 transition-transform ${collapsed.has(node.nodeId) ? "" : "rotate-90"}`} />
            </button> : <span className="w-7 shrink-0" />}
            <span className="break-words">{node.name}</span>
            {!!evaluation.statistics.byDomain[node.nodeId]?.samples && <span className="ml-auto text-ink3 tabular-nums">{formatNumber(evaluation.statistics.byDomain[node.nodeId].samples)}</span>}
          </div></th>
          {TASK_TYPES.map(type => {
            const cell = evaluation.statistics.byDomainTaskType[node.nodeId]?.[type];
            return <td key={type} className="px-2 py-2" aria-label={!cell?.samples ? t("evaluation.noSample") : undefined}>
              {!!cell?.samples && <Hint text={t("evaluation.cell", { samples: formatNumber(cell.samples), rate: rate(cell.firstPassRate), rework: formatNumber(cell.reworkRounds) })}><span tabIndex={0} aria-label={t("evaluation.cell", { samples: formatNumber(cell.samples), rate: rate(cell.firstPassRate), rework: formatNumber(cell.reworkRounds) })} className="inline-flex items-center gap-1.5 tabular-nums">
                <span className="block h-1.5 w-11 bg-line" aria-hidden="true"><span className="quant-domain-rate block h-full" style={{ width: `${(cell.firstPassRate ?? 0) * 100}%` }} /></span>
                <span className="text-ink3">{formatNumber(cell.samples)}</span>
              </span></Hint>}
            </td>;
          })}
        </tr>)}</tbody>
      </table></div>}
  </section>;
}

/** Current kind evidence, followed by the names menu and retained older evidence. */
export function DomainEvaluation({ evaluation, evaluations, domains, children }: {
  evaluation?: KindEvaluation; evaluations: KindEvaluation[]; domains: DomainNode[]; children?: ReactNode;
}) {
  const { t } = useTranslation("buddies");
  if (!evaluation) return <p className="text-sm text-ink3">{t("evaluation.noEvidence")}</p>;
  const previous = evaluation.previousGroups.map(group => ({ group,
    evidence: evaluations.find(item => item.harness === evaluation.harness && item.effort === evaluation.effort && item.modelId === group.modelId),
  })).filter(({ evidence }) => !!evidence?.statistics.overall.samples);
  return <div data-slot="domain-evaluation" className="space-y-5">
    <Tally statistics={evaluation.statistics.overall} preferenceKey={`${evaluation.harness}.${evaluation.modelId}.${evaluation.effort}`} />
    <section aria-label={t("evaluation.summary")} className="space-y-1"><h3 className="text-sm font-medium">{t("evaluation.summary")}</h3>
      {evaluation.summary?.text ? <Fold text={evaluation.summary.text} /> : <p className="text-xs text-ink3">{t("evaluation.noSummary")}</p>}
    </section>
    <DomainTable evaluation={evaluation} domains={domains} />
    {children}
    {!!previous.length && <section aria-label={t("evaluation.previousSamples")} className="space-y-4 border-t border-line pt-5">
      <h3 className="text-sm font-medium text-ink2">{t("evaluation.previousSamples")}</h3>
      {previous.map(({ group, evidence }) => <div key={group.modelId} className="space-y-2 border-t border-line/70 pt-3">
        <p className="text-xs text-ink2">{group.modelDisplayName}</p>
        <Tally statistics={evidence!.statistics.overall} preferenceKey={`${evaluation.harness}.${evaluation.modelId}.${evaluation.effort}.previous.${group.modelId}`} subdued />
        <p className="text-xs text-ink3">{group.names.map(name => <span key={`${name.harness}\0${name.provider}\0${name.model}`} className="mr-4 inline-block">
          {name.model} · {t("evaluation.since", { date: formatDateTime(Date.parse(name.since)) })}
        </span>)}</p>
      </div>)}
    </section>}
  </div>;
}

/** The profile belongs to the model, so multiple buddy kinds display this same content. */
export function ModelPortrait({ model, children }: { model: Model; children?: ReactNode }) {
  const { t } = useTranslation("buddies");
  const profile = model.profile;
  const sources = new Map<string, { number: number; url: string; date: string; kind: "vendor" | "third-party" }>();
  for (const claim of profile?.claims ?? []) {
    const key = `${claim.sourceUrl}\0${claim.sourceDate}\0${claim.sourceKind}`;
    if (!sources.has(key)) sources.set(key, { number: sources.size + 1, url: claim.sourceUrl, date: claim.sourceDate, kind: claim.sourceKind });
  }
  return <section data-slot="model-portrait" aria-label={`${model.displayName} · ${t("modelProfile.title")}`} className="space-y-5">
    <div className="flex flex-wrap items-center gap-3"><h2 className="text-lg font-medium">{model.displayName} · {t("modelProfile.title")}</h2><span className="text-xs text-ink3">{t("modelProfile.shared")}</span></div>
    <div className="flex flex-wrap items-center gap-3 border-y border-line py-3 text-xs">
      {profile ? <><StateMark kind="done" /><span>{t("modelProfile.state.completed")}</span><time className="text-ink3" dateTime={profile.builtAt}>{formatDateTime(Date.parse(profile.builtAt))}</time></>
        : <span className="text-ink3">{t("modelProfile.notBuilt")}</span>}
      <span className="ml-auto">{children}</span>
    </div>
    {profile && <>
      <div><h3 className="mb-1 text-xs text-ink3">{t("modelProfile.positioning")}</h3><p className="text-base">{profile.positioning}</p></div>
      <div className="grid gap-6 border-t border-line pt-4 min-[700px]:grid-cols-2">
        {(["strength", "weakness"] as const).map(polarity => <section key={polarity} aria-label={t(`modelProfile.${polarity}`)}>
          <h3 className="mb-3 text-sm font-medium"><span className="mr-2 text-lg" aria-hidden="true">{polarity === "strength" ? "+" : "−"}</span>{t(`modelProfile.${polarity}`)}</h3>
          <ul className="space-y-3 text-sm">{profile.claims.filter(claim => claim.polarity === polarity).map((claim, index) => {
            const source = sources.get(`${claim.sourceUrl}\0${claim.sourceDate}\0${claim.sourceKind}`)!;
            return <li key={`${source.number}-${index}`} className="wrap-anywhere">{claim.claim} <sup aria-label={`S${source.number}`} className="text-xs text-ink2">S{source.number}</sup></li>;
          })}</ul>
        </section>)}
      </div>
      {!!sources.size && <div className="flex flex-wrap gap-x-5 gap-y-1 border-t border-line pt-3 text-xs text-ink3"><span>{t("modelProfile.sources")}</span>
        {[...sources.values()].map(source => <a key={source.number} href={source.url} target="_blank" rel="noreferrer" className="underline underline-offset-2">
          S{source.number} · {t(`modelProfile.claimSource.${source.kind}`)} · {source.date}
        </a>)}
      </div>}
    </>}
  </section>;
}
