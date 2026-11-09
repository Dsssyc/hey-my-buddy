import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import type { HarnessHealth } from "../types";
import { HarnessLogo } from "./HarnessLogo";
import { CoolingHeaderProviders } from "./cooling";
import { harnessStateText } from "./models";
import type { HarnessBlock } from "./models";

const row = "flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left sm:px-4";

/**
 * The chevron folds models. A separate button covers the rest of the row to open
 * its details; provider retry buttons sit above that button, never inside it.
 */
export function HarnessHeader({ block, health, state, accountOpen, onToggleAccount, gate, onRetry }: {
  block: HarnessBlock & { modelCount: number }; health: HarnessHealth | undefined; state: string | null;
  accountOpen: boolean; onToggleAccount: () => void;
  gate: { canWrite: boolean; refusal: string; busy: boolean }; onRetry: (provider: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const content = <>
    <HarnessLogo harness={block.harness} />
    <b className="text-base font-semibold">{block.name}</b>
    <CoolingHeaderProviders names={block.providers} providers={health?.providers ?? []} gate={gate} onRetry={onRetry} />
    {state && <span className="text-xs text-ink3">{harnessStateText(state, t)}</span>}
    <span className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-4 gap-y-1">
      {block.folded && <span className="text-xs text-ink3 tabular-nums">{t("models.modelCount", { n: block.modelCount })} · {t("models.enabled")} {block.enabled} / {block.total}</span>}
      {!block.folded && <span className="hidden w-16 text-right text-xs text-ink3 sm:inline">{t("models.columns.concurrency")}</span>}
    </span>
  </>;
  return <div data-slot="harness-header" className="flex min-h-12 items-stretch">
    <CollapsibleTrigger asChild>
      <Button type="button" variant="ghost" size="icon-sm" data-slot="fold-handle" aria-label={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}
        className="ml-3 self-center sm:ml-4">
        <Disclosure open={!block.folded} />
      </Button>
    </CollapsibleTrigger>
    <div className={cn(row, "relative")}>
      {health && <button id={`account-summary-${block.harness}`} type="button" data-slot="account-summary"
        aria-label={t("harness.summaryLabel", { name: block.name })} aria-expanded={accountOpen}
        onClick={onToggleAccount} className="disclosure-trigger absolute inset-0 z-0 w-full motion-reduce:transition-none" />}
      <span className="pointer-events-none relative z-10 flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1">{content}</span>
    </div>
  </div>;
}
