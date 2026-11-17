import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { StateMark } from "@/components/ui/state-mark";
import { harnessName } from "../buddy-display";
import { harnessTime } from "./harness";
import type { BuddyLocation } from "./navigation";
import { HarnessDetails } from "./HarnessDetails";
import type { useHarnessActions } from "./use-harness-actions";

/** A dedicated, persistent view for checking and configuring harnesses. */
export function HarnessView({ actions, active, hidden, location }: {
  actions: ReturnType<typeof useHarnessActions>; active: boolean; hidden: boolean; location: BuddyLocation;
}) {
  const { t } = useTranslation("buddies");
  const handledTarget = useRef<string | null>(null);
  const rows = [...actions.rows].sort((a, b) => Number(b.status === "ready") - Number(a.status === "ready"));
  const checkedAt = rows.map(row => row.checkedAt).filter((value): value is string => !!value).sort().at(-1);
  const checkedTime = harnessTime(checkedAt);
  useEffect(() => {
    if (!active || location.view !== "harness" || !location.target) {
      handledTarget.current = null;
      return;
    }
    if (handledTarget.current === location.target) return;
    const target = document.getElementById(`harness-title-${location.target}`);
    if (!target) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView?.({ block: "nearest" });
    handledTarget.current = location.target;
  }, [active, location, rows]);
  const refusal = actions.refusal || (actions.busy ? t("harness.checkAllBusy") : undefined);
  return <section hidden={hidden} aria-label={t("sections.harness")} className="view-presence flex min-h-0 flex-1 flex-col bg-background">
    <div data-slot="harness-toolbar" className="min-h-12 shrink-0 border-b bg-card px-6 py-2 text-ink2">
      <div data-slot="harness-controls" className="mx-auto flex max-w-[1040px] min-w-0 flex-wrap items-center justify-between gap-x-6 gap-y-2">
        {rows.length > 0 ? <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
          <span className="inline-flex items-center gap-2"><StateMark kind={rows.filter(row => row.status === "ready").length === rows.length ? "live" : "idle"} />{t("harness.found", { found: rows.filter(row => row.status === "ready").length, total: rows.length })}</span>
          {checkedTime && <span className="text-xs text-ink3 tabular-nums">{t("harness.checkedAt", { time: checkedTime })}</span>}
        </div> : <p className="text-sm text-ink3">{t("harness.empty")}</p>}
        <div className="ml-auto flex items-center gap-2">
          <Hint text={refusal}><Button variant="outline" className="h-7 px-2 text-xs sm:h-8 sm:px-3 sm:text-sm" aria-disabled={!!refusal || undefined} onClick={() => void actions.run({ kind: "check" })}>{t("harness.checkAll")}</Button></Hint>
        </div>
      </div>
    </div>
    {active && actions.scope === "harness" && (actions.guard || actions.error || actions.note) && <div data-slot="harness-feedback" className="shrink-0 border-b bg-card px-3 py-2 text-xs sm:px-6">
      <div className="mx-auto flex max-w-[1040px] flex-wrap items-center gap-x-5 gap-y-1">
        {actions.guard && <p role="status">{actions.guard}</p>}
        {actions.error && <p className="flex flex-wrap items-center gap-2 text-fail" role="alert">{actions.error}{actions.retry && <Button variant="outline" size="sm" onClick={() => void actions.run(actions.retry!)}>{t("harness.retry")}</Button>}</p>}
        {actions.note && <p role="status">{actions.note}</p>}
      </div>
    </div>}
    <ScrollArea data-slot="harness-list" className="min-h-0 flex-1" viewportProps={{ className: "[&>div]:block!" }}>
      <div className="mx-auto max-w-[1040px] space-y-3 p-3 sm:p-6">
        {rows.map(row => <div key={row.harness} data-slot="harness-card" className="rounded-(--radius-card) border bg-card">
          <HarnessDetails row={row} name={harnessName(row.harness)} canWrite={!actions.refusal} refusal={actions.refusal}
            busy={actions.busy} onCheck={() => void actions.run({ kind: "check", harness: row.harness })}
            onSavePath={path => void actions.run({ kind: "set", harness: row.harness, path })} />
        </div>)}
      </div>
    </ScrollArea>
  </section>;
}
