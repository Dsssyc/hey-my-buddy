import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { formatCompact, formatNumber } from "../i18n/format";
import type { TokenUsage } from "../types";

/**
 * One execution's own usage, never a session's. What was not recorded is
 * unknown, never nought. The cache is counted as the usage says: inside the
 * input when its basis includes it, beside the input when it does not.
 */
export function usageLabel(t: TFunction<"taskDetail">, usage: TokenUsage | null): string {
  if (!usage) return t("usage.unrecorded");
  const count = (value: number | null) => (value === null ? t("unknown") : formatNumber(value));
  const apart = usage.inputBasis === "excludes-cached";
  return [
    usage.cachedInputTokens === null ? t("usage.input", { n: count(usage.inputTokens) })
      : t(apart ? "usage.inputBesideCache" : "usage.inputWithCache", { n: count(usage.inputTokens), cached: count(usage.cachedInputTokens) }),
    t("usage.output", { n: count(usage.outputTokens) }),
    t("usage.source", { source: usage.source }),
    t(apart ? "usage.scopeApart" : "usage.scope"),
    ...(usage.completeness === "partial" ? [t("usage.partial")] : []),
  ].join(" · ");
}

export function Usage({ usage }: { usage: TokenUsage | null }) {
  const { t } = useTranslation("taskDetail");
  const count = (value: number | null | undefined) => (value === null || value === undefined ? t("unknown") : formatCompact(value));
  return <Hint text={usageLabel(t, usage)}>
    <span tabIndex={0} className="inline-flex flex-wrap gap-x-3.5 rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
      <span>{t("usage.inputShort")} {count(usage?.inputTokens)}</span>
      <span>{t("usage.outputShort")} {count(usage?.outputTokens)}</span>
      <span>{t("usage.cacheShort")} {count(usage?.cachedInputTokens)}</span>
    </span>
  </Hint>;
}
