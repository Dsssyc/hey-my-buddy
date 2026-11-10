import { useTranslation } from "react-i18next";
import { CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import type { HarnessHealth } from "../types";
import { HarnessLogo } from "./HarnessLogo";
import { CoolingHeaderProviders } from "./cooling";
import { harnessStateText } from "./models";
import type { HarnessBlock } from "./models";

const row = "flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2 text-left sm:px-4";

/**
 * The whole head folds models; provider retries sit above its trigger.
 */
export function HarnessHeader({ block, health, state, gate, onRetry }: {
  block: HarnessBlock & { modelCount: number }; health: HarnessHealth | undefined; state: string | null;
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
  return <div data-slot="harness-header" className="group/header relative flex min-h-12 items-stretch">
    <CollapsibleTrigger type="button" data-slot="fold-handle" aria-label={t(block.folded ? "models.unfold" : "models.fold", { name: block.name })}
      className="absolute inset-0 w-full cursor-pointer outline-none transition-colors duration-150 hover:bg-sunk focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring motion-reduce:transition-none" />
    <div className="pointer-events-none relative z-10 flex min-w-0 flex-1 items-stretch">
      <span className="ml-3 flex size-7 shrink-0 self-center items-center justify-center text-ink3 transition-colors duration-150 group-hover/header:text-ink motion-reduce:transition-none sm:ml-4">
        <Disclosure open={!block.folded} />
      </span>
      <div className={row}>{content}</div>
    </div>
  </div>;
}
