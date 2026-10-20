import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { reasonText } from "../i18n/reason";
import type { HarnessHealth } from "../types";
import { HarnessLogo } from "./HarnessLogo";
import { CoolingStatus } from "./cooling";
import { harnessStateText, reasonTexts } from "./models";
import type { HarnessBlock } from "./models";

const row = "flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left sm:px-4";

/**
 * The head of one harness section, two buttons side by side and neither inside
 * the other: the chevron at the left folds the section's models, and the rest
 * of the row opens its location, account and cooldown. The cooldown summary is only
 * a part of that row. The chevron is the trigger of the Collapsible that holds
 * the section's models, so the head belongs inside it.
 */
export function HarnessHeader({ block, health, state, accountOpen, onToggleAccount }: {
  block: HarnessBlock & { modelCount: number }; health: HarnessHealth | undefined; state: string | null;
  accountOpen: boolean; onToggleAccount: () => void;
}) {
  const { t } = useTranslation("buddies");
  const said = state ? [health?.reason ? reasonText(health.reason) : null, ...reasonTexts(block.reasons, t, !!health?.reason)].filter(Boolean).join(" · ") : "";
  const content = <>
    <HarnessLogo harness={block.harness} />
    <b className="text-base font-semibold">{block.name}</b>
    <span className="text-xs text-ink2">{block.providers.join(" / ")}</span>
    {state && <span className="text-xs text-ink3">{harnessStateText(state, t)}</span>}
    <span className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-4 gap-y-1">
      {block.folded && <span className="text-xs text-ink3 tabular-nums">{t("models.modelCount", { n: block.modelCount })} · {t("models.enabled")} {block.enabled} / {block.total}</span>}
      <CoolingStatus providers={health?.providers ?? []} />
      {!block.folded && <span className="hidden w-16 text-right text-xs text-ink3 sm:inline">{t("models.columns.concurrency")}</span>}
    </span>
  </>;
  return <div data-slot="harness-header" className="flex min-h-12 items-stretch">
    <Hint text={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}><CollapsibleTrigger asChild>
      <button type="button" data-slot="fold-handle" aria-label={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}
        className="disclosure-trigger ml-3 inline-flex size-8 shrink-0 items-center justify-center self-center rounded-md sm:ml-4">
        <Disclosure open={!block.folded} />
      </button>
    </CollapsibleTrigger></Hint>
    {health
      ? <Hint text={said || undefined}>
        <button id={`account-summary-${block.harness}`} type="button" data-slot="account-summary" aria-label={t("harness.summaryLabel", { name: block.name })}
          aria-expanded={accountOpen} onClick={onToggleAccount}
          className={cn(row, "disclosure-trigger motion-reduce:transition-none")}>
          {content}
        </button>
      </Hint>
      : <div className={row}>{content}</div>}
  </div>;
}
