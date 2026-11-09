import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Hint } from "@/components/hint";
import { formatCoolingTime } from "../i18n/format";
import type { ProviderCooling } from "../types";

export function activeCooling(providers: ProviderCooling[], now = Date.now()): ProviderCooling[] {
  return providers.filter(record => record.coolingUntil !== null && Date.parse(record.coolingUntil) > now);
}

/** Wake at the next deadline, including when a retained record arrives after mount. */
function useCooling(providers: ProviderCooling[]) {
  const [now, setNow] = useState(Date.now);
  const deadlines = providers.map(record => record.coolingUntil).join(",");
  useEffect(() => {
    const current = Date.now();
    const next = Math.min(...activeCooling(providers, current).map(record => Date.parse(record.coolingUntil!)));
    if (!Number.isFinite(next)) return;
    const timer = window.setTimeout(() => setNow(Date.now()), Math.max(1, next - current));
    return () => window.clearTimeout(timer);
  }, [deadlines, now]);
  return activeCooling(providers);
}

type Gate = { canWrite: boolean; refusal: string; busy: boolean };

function RetryButton({ provider, gate, onRetry }: { provider: string; gate: Gate; onRetry: (provider: string) => void }) {
  const { t } = useTranslation("buddies");
  const inert = !gate.canWrite || gate.busy;
  const reason = inert ? gate.refusal || t("harness.running") : t("harness.cooling.retryHint");
  return <Hint text={reason}><Button type="button" variant="outline" size="sm" aria-disabled={inert || undefined}
    onClick={() => { if (!inert) onRetry(provider); }}>{t("harness.cooling.clear")}</Button></Hint>;
}

/** Provider labels and their own active cooldowns stay together in the section head. */
export function CoolingHeaderProviders({ names, providers, gate, onRetry }: {
  names: string[]; providers: ProviderCooling[]; gate: Gate; onRetry: (provider: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const cooling = useCooling(providers);
  const displayed = [...new Set([...names, ...cooling.map(record => record.provider)])];
  return <>{displayed.map(name => {
    const record = cooling.find(entry => entry.provider === name);
    return <span key={name} data-slot="header-provider" className="relative z-10 inline-flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-ink2">
      <span>{name}</span>
      {record && <>
        <span data-slot="cooling-status" className="text-ink3 tabular-nums">{t("harness.cooling.status", { time: formatCoolingTime(Date.parse(record.coolingUntil!)) })}</span>
        <span className="pointer-events-auto"><RetryButton provider={name} gate={gate} onRetry={onRetry} /></span>
      </>}
    </span>;
  })}</>;
}

export function CoolingDetails({ providers, gate, onClear }: {
  providers: ProviderCooling[]; gate: Gate; onClear: (provider: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const cooling = useCooling(providers);
  if (!cooling.length) return <p data-slot="cooling-details" className="text-xs text-ink3">{t("harness.cooling.none")}</p>;
  return <div data-slot="cooling-details" className="min-w-0">
      {cooling.map(record => <div key={record.provider} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 pb-3">
        <span className="font-mono text-xs wrap-anywhere">{record.provider}</span>
        <span className="col-start-1 text-xs text-ink3">{t("harness.cooling.status", { time: formatCoolingTime(Date.parse(record.coolingUntil!)) })}</span>
        <span className="col-start-2 row-start-1 row-span-2"><RetryButton provider={record.provider} gate={gate} onRetry={onClear} /></span>
      </div>)}
  </div>;
}
