import { useTranslation } from "react-i18next";
import { formatClockMinutes, formatNumber } from "../i18n/format";
import type { TokenUsage } from "../types";
import { QuantBarDisclosure } from "./QuantBar";

type AttemptUsage = { tokenUsage: TokenUsage | null };

/** Add only values recorded for every attempt; an absent field remains unknown. */
export function sumRouterUsage(attempts: AttemptUsage[]): TokenUsage | null {
  if (attempts.length === 0) return null;
  const sum = (part: (usage: TokenUsage) => number | null) => attempts.every(attempt => attempt.tokenUsage && part(attempt.tokenUsage) !== null)
    ? attempts.reduce((total, attempt) => total + part(attempt.tokenUsage!)!, 0) : null;
  const input = sum(value => value.inputTokens === null ? null
    : value.inputBasis === "excludes-cached" ? value.cachedInputTokens === null ? null : value.inputTokens + value.cachedInputTokens : value.inputTokens);
  const cached = sum(value => value.cachedInputTokens);
  const partial = attempts.some(attempt => !attempt.tokenUsage || attempt.tokenUsage.completeness === "partial");
  const unknown = attempts.some(attempt => attempt.tokenUsage?.completeness === "unknown");
  return {
    inputTokens: input, cachedInputTokens: cached, outputTokens: sum(value => value.outputTokens),
    source: "profile-runs", scope: "attempt", inputBasis: "includes-cached",
    ...(partial || unknown ? { completeness: partial ? "partial" as const : "unknown" as const } : {}),
  };
}

export function routerUsageTotal(usage: TokenUsage | null): number | null {
  if (!usage || usage.inputTokens === null || usage.outputTokens === null) return null;
  if (usage.inputBasis === "excludes-cached") return usage.cachedInputTokens === null ? null : usage.inputTokens + usage.cachedInputTokens + usage.outputTokens;
  return usage.inputTokens + usage.outputTokens;
}

/** A token track; the caller supplies one scale for the whole comparison group. */
export function RouterUsage({ usage, maximum, preferenceKey, label }: { usage: TokenUsage | null; maximum?: number; preferenceKey: string; label: string }) {
  const { t } = useTranslation("buddies");
  const cached = usage?.cachedInputTokens ?? null;
  const input = usage?.inputTokens ?? null;
  const output = usage?.outputTokens ?? null;
  const other = input === null ? null : usage?.inputBasis === "excludes-cached" ? input
    : cached === null ? null : Math.max(0, input - cached);
  const parts = [cached, other, output];
  const labels = [t("routerProfiles.cached"), t("routerProfiles.otherInput"), t("routerProfiles.output")];
  const known = parts.reduce<number>((total, value) => total + (value ?? 0), 0);
  const scale = Math.max(maximum ?? known, known, 1);
  const detail = parts.map((value, index) => `${labels[index]} ${value === null ? t("routerProfiles.unknown") : formatNumber(value)}`).join("; ");
  const total = routerUsageTotal(usage);
  const title = `${detail}; ${t("routerProfiles.total")} ${total === null ? t("routerProfiles.unknown") : formatNumber(total)}${usage?.completeness === "partial" || usage?.completeness === "unknown" ? `; ${t("routerProfiles.partial")}` : ""}`;
  return <div data-slot="router-usage"><QuantBarDisclosure preferenceKey={`usage.${preferenceKey}`}
    label={`${label}: ${title}`} maximum={scale} width={112}
    parts={[{ value: cached, tone: "green-dark" }, { value: other, tone: "green" }, { value: output, tone: "orange" }]}>
    <span aria-label={title}>{title}</span>
  </QuantBarDisclosure></div>;
}

export function RouterTimeRange({ startedAt, finishedAt }: { startedAt: string; finishedAt: string | null }) {
  const { t } = useTranslation("buddies");
  const start = Date.parse(startedAt);
  const end = finishedAt === null ? null : Date.parse(finishedAt);
  const sameDay = end !== null && new Date(start).toDateString() === new Date(end).toDateString();
  const short = (ms: number) => { const date = new Date(ms); const pad = (n: number) => String(n).padStart(2, "0");
    return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${formatClockMinutes(ms)}`; };
  return <span className="inline-flex whitespace-nowrap gap-1 tabular-nums text-xs text-ink2">
    <time dateTime={startedAt}>{short(start)}</time>
    <span aria-hidden="true">–</span>
    {finishedAt ? <time dateTime={finishedAt}>{sameDay ? formatClockMinutes(end!) : short(end!)}</time> : <span aria-label={t("routerProfiles.notEnded")}>…</span>}
  </span>;
}
