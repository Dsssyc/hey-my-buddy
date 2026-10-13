import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import type { HarnessHealth } from "../types";
import { HarnessLogo } from "./HarnessLogo";
import { harnessSummary, harnessTime, recoveryText } from "./harness";
import { harnessStateText, reasonTexts } from "./models";
import type { HarnessBlock } from "./models";

const row = "flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left sm:px-4";

/**
 * The head of one harness section, two buttons side by side and neither inside
 * the other: the chevron at the left folds the section's models, and the rest
 * of the row opens its location, account and quota. The quota summary is only
 * a part of that row. The chevron is the trigger of the Collapsible that holds
 * the section's models, so the head belongs inside it.
 */
export function HarnessHeader({ block, health, state, accountOpen, onToggleAccount }: {
  block: HarnessBlock & { modelCount: number }; health: HarnessHealth | undefined; state: string | null;
  accountOpen: boolean; onToggleAccount: () => void;
}) {
  const { t } = useTranslation("buddies");
  const summary = health && harnessSummary(health, t);
  const metered = health && Object.values(health.billingByProvider ?? {}).some(fact => fact.kind === "metered");
  const blocked = health?.quotaRouting?.find(record => record.blocked);
  const retryTime = blocked && harnessTime(blocked.resetsAt ?? blocked.retry?.eligibleAt);
  const said = blocked ? recoveryText(blocked, t) : state ? [health?.remedy, ...reasonTexts(block.reasons, t, true)].filter(Boolean).join(" · ") : "";
  const observed = health?.quota ? t("harness.quota.observation", { time: harnessTime(health.quota.observedAt) ?? t("harness.unrecorded"), source: health.quota.source }) : "";
  const note = [said, observed].filter(Boolean).join(" · ");
  const content = <>
    <HarnessLogo harness={block.harness} />
    <b className="text-base font-semibold">{block.name}</b>
    <span className="text-xs text-ink2">{block.providers.join(" / ")}</span>
    {state && !blocked && <span className="text-xs text-ink3">{harnessStateText(state, t)}</span>}
    {blocked && <span className="text-xs text-hand">{t(blocked.code === "HARNESS_BALANCE_ZERO" ? "harness.quota.balanceZero" : "harness.quota.exhausted")}{retryTime && ` · ${t(blocked.resetsAt ? "harness.quota.reset" : "harness.quota.retryTime", { time: retryTime })}`}</span>}
    <span className="ml-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-1">
      {block.folded && <span className="text-xs text-ink3 tabular-nums">{t("models.modelCount", { n: block.modelCount })} · {t("models.enabled")} {block.enabled} / {block.total}</span>}
      {health && <span data-slot="quota-summary" className="inline-flex max-w-full items-center gap-2 whitespace-nowrap text-xs tabular-nums">
        <span data-slot="summary-value" className={cn("whitespace-nowrap", (metered ? !summary?.stale && health.quota?.balanceZero : summary?.hot) && "text-hand")}>
          {metered ? health.quota?.balanceZero ? t("harness.quota.balanceZero") : `${t("harness.balance")} ${t("harness.quota.unknown")}`
            : summary ? summary.text : "—"}
        </span>
        {!metered && summary?.resets && <span className="whitespace-nowrap text-ink3">{t("harness.quota.reset", { time: summary.resets })}</span>}
        {summary?.stale && <span className="whitespace-nowrap text-ink3">{t("harness.quota.stale")}</span>}
      </span>}
      {!block.folded && <span className="hidden w-16 text-right text-xs text-ink3 sm:inline">{t("models.columns.concurrency")}</span>}
    </span>
  </>;
  return <div data-slot="harness-header" className="flex min-h-12 items-stretch">
    <CollapsibleTrigger asChild>
      <button type="button" data-slot="fold-handle" aria-label={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}
        className="ml-3 inline-flex size-8 shrink-0 items-center justify-center self-center rounded-md text-ink2 outline-none transition-colors duration-150 hover:bg-sunk hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none sm:ml-4">
        <Disclosure open={!block.folded} />
      </button>
    </CollapsibleTrigger>
    {health
      ? <Hint text={note || undefined}>
        <button id={`account-summary-${block.harness}`} type="button" data-slot="account-summary" aria-label={t("harness.summaryLabel", { name: block.name })}
          aria-expanded={accountOpen} onClick={onToggleAccount}
          className={cn(row, "outline-none transition-colors duration-150 hover:bg-sunk focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset motion-reduce:transition-none")}>
          {content}
        </button>
      </Hint>
      : <div className={row}>{content}</div>}
  </div>;
}
