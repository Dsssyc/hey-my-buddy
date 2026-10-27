import i18n from "i18next";
import { parseTokenUsage } from "./task-detail/parse";
import { formatCompact } from "./i18n/format";
import type { TimelineSpan } from "./macro-task-types";

/**
 * Several executions' recorded token usage on the one display basis every
 * level shares: the cache sits inside the input. A count no record provided
 * is null, never nought; a missing or partial record marks the whole total
 * partial instead of feeding a number that looks complete.
 */
export type UsageTotals = {
  input: number | null;
  cached: number | null;
  output: number | null;
  partial: boolean;
};

/** Execution and routing are the spans that consume a model; waits never do. */
export function usageTotals(spans: readonly TimelineSpan[]): UsageTotals {
  let input = 0, cached = 0, output = 0;
  let inputKnown = false, cachedKnown = false, outputKnown = false;
  let known = 0, unknown = 0, partial = false;
  for (const span of spans) {
    if (span.kind !== "execution" && span.kind !== "routing") continue;
    const usage = parseTokenUsage(span.tokenUsage);
    if (!usage || usage.inputTokens === null && usage.cachedInputTokens === null && usage.outputTokens === null) {
      unknown += 1;
      continue;
    }
    known += 1;
    if (usage.inputTokens !== null) {
      // On the basis that counts the cache apart, the input that includes it is
      // the only number all records can be added on; a missing cache there
      // leaves that sum understated, which the partial note says out loud.
      input += usage.inputBasis === "excludes-cached" ? usage.inputTokens + (usage.cachedInputTokens ?? 0) : usage.inputTokens;
      inputKnown = true;
      if (usage.inputBasis === "excludes-cached" && usage.cachedInputTokens === null) partial = true;
    }
    if (usage.cachedInputTokens !== null) { cached += usage.cachedInputTokens; cachedKnown = true; }
    if (usage.outputTokens !== null) { output += usage.outputTokens; outputKnown = true; }
    if (usage.inputTokens === null || usage.outputTokens === null
      || usage.completeness === "partial" || usage.completeness === "unknown") partial = true;
  }
  return {
    input: inputKnown ? input : null,
    cached: cachedKnown ? cached : null,
    output: outputKnown ? output : null,
    partial: partial || known > 0 && unknown > 0,
  };
}

/** The one writing the turn's usage hint, the Lead's lane and the macro task's totals share. */
export function usageTotalsLabel(totals: UsageTotals): string {
  if (totals.input === null && totals.cached === null && totals.output === null) return i18n.t("taskDetail:usage.unrecorded");
  const count = (value: number | null) => (value === null ? i18n.t("taskDetail:unknown") : formatCompact(value));
  return [
    totals.cached === null
      ? i18n.t("taskDetail:usage.input", { n: count(totals.input) })
      : i18n.t("taskDetail:usage.inputWithCache", { n: count(totals.input), cached: count(totals.cached) }),
    i18n.t("taskDetail:usage.output", { n: count(totals.output) }),
    ...(totals.partial ? [i18n.t("taskDetail:usage.partialRecord")] : []),
  ].filter(Boolean).join(" · ");
}
