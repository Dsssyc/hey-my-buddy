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

/** A stable single-line slot, silent while no provider is cooling. */
export function CoolingStatus({ providers }: { providers: ProviderCooling[] }) {
  const { t } = useTranslation("buddies");
  const cooling = useCooling(providers);
  // Earliest deadline answers when this harness has another provider to try.
  const next = cooling.length ? Math.min(...cooling.map(record => Date.parse(record.coolingUntil!))) : null;
  const text = next === null ? "" : t("harness.cooling.status", { time: formatCoolingTime(next) });
  return <span data-slot="cooling-status" className="block h-4 w-full max-w-full truncate text-xs text-ink3 tabular-nums sm:w-80">{text}</span>;
}

export function CoolingDetails({ providers, gate, onClear }: {
  providers: ProviderCooling[]; gate: { canWrite: boolean; refusal: string; busy: boolean }; onClear: (provider: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const cooling = useCooling(providers);
  const refusal = gate.refusal || (gate.busy ? t("harness.running") : undefined);
  return <div data-slot="cooling-details" className="mt-4 min-w-0">
    <div className="h-24 overflow-auto" role={cooling.length ? "group" : undefined} aria-label={cooling.length ? t("harness.cooling.name") : undefined}>
      {cooling.map(record => <div key={record.provider} className="grid min-w-0 grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 gap-y-1 pb-3">
        <span className="font-mono text-xs wrap-anywhere">{record.provider}</span>
        <span className="col-start-1 text-xs text-ink3">{t("harness.cooling.status", { time: formatCoolingTime(Date.parse(record.coolingUntil!)) })}</span>
        <Hint text={refusal}><Button className="col-start-2 row-start-1 row-span-2" variant="outline" aria-disabled={!gate.canWrite || gate.busy || undefined}
          onClick={() => onClear(record.provider)}>{t("harness.cooling.clear")}</Button></Hint>
      </div>)}
    </div>
  </div>;
}
