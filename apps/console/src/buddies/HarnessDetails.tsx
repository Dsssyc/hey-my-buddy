import { cn } from "cn";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import type { HarnessHealth } from "../types";
import { harnessDetailsView, statusText } from "./harness";
import type { HarnessDetailsView } from "./harness";
import { CoolingDetails } from "./cooling";
import { HarnessPathDialog } from "./HarnessPathDialog";

const hintTarget = "rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";
const label = "text-xs text-ink3";
const fields = "grid grid-cols-[max-content_minmax(0,1fr)] items-baseline gap-x-3 gap-y-2";
const buttons = "mt-4 flex flex-wrap gap-2";

type Gate = { canWrite: boolean; refusal: string; busy: boolean };

function Block({ title, head, children }: { title: string; head?: ReactNode; children: ReactNode }) {
  return <div role="group" aria-label={title} className="min-w-0 px-5 py-4">
    <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
      <h3 className="text-xs font-medium text-ink2">{title}</h3>
      <span className="flex flex-wrap items-center gap-x-1.5 text-xs text-ink3">{head}</span>
    </div>
    {children}
  </div>;
}

function Exception({ exception }: { exception: NonNullable<HarnessDetailsView["location"]["exception"]> }) {
  const { t } = useTranslation("buddies");
  return <div className={cn(fields, "mt-3")}>
    {exception.reason && <><span className={label}>{t("harness.pane.reason")}</span>
      <span className="wrap-anywhere">{exception.reason}</span></>}
    {exception.tried.length > 0 && <>
      <span className={cn(label, "col-span-2")}>{t("harness.pane.tried")}</span>
      {exception.tried.map((tried, index) => <div key={`${tried.path ?? ""}:${index}`} className="col-span-2 flex flex-col gap-0.5">
        <code className="font-mono text-xs break-all">{tried.path ?? t("harness.pane.noPath")}</code>
        <span className="text-xs text-ink3">{[tried.source, tried.result].filter(Boolean).join(" · ")}</span>
      </div>)}
    </>}
  </div>;
}

function LocationBlock({ row, name, view, gate, onCheck, onSavePath }: {
  row: HarnessHealth; name: string; view: HarnessDetailsView; gate: Gate;
  onCheck: () => void; onSavePath: (path: string | null) => void;
}) {
  const { t } = useTranslation("buddies");
  const [pathOpen, setPathOpen] = useState(false);
  const { location } = view;
  const inert = gate.busy || !gate.canWrite;
  const refusal = gate.refusal || (gate.busy ? t("harness.running") : undefined);
  return <Block title={t("harness.pane.location")} head={<>
    <StateMark kind={row.status === "ready" ? "live" : "idle"} />
    <Hint text={location.exception?.reason ?? undefined}><span tabIndex={0} className={hintTarget}>{statusText(row.status, t)}</span></Hint>
  </>}>
    <div className={fields}>
      {row.version && <><span className={label}>{t("harness.pane.version")}</span>
        <span className="tabular-nums">{row.version}</span></>}
      <span className={label}>{t("harness.pane.path")}</span>
      <span className="font-mono text-xs break-all">{location.path ?? t("harness.unrecorded")}</span>
      <span className={label}>{t("harness.pane.source")}</span><span>{location.source}</span>
      <span className={label}>{t("harness.pane.checked")}</span>
      <span className="tabular-nums">{location.checked ?? t("harness.unrecorded")}</span>
      <span className={label}>{t("harness.pane.review")}</span>
      <Hint text={[location.review.note, location.review.reason].filter(Boolean).join(" ")}>
        <span tabIndex={0} className={hintTarget}>{location.review.text}</span>
      </Hint>
    </div>
    {location.exception && <Exception exception={location.exception} />}
    <div className={buttons}>
      <Hint text={refusal}><Button variant="outline" aria-disabled={inert || undefined}
        onClick={onCheck}>{t("harness.check")}</Button></Hint>
      <Button variant="outline" onClick={() => setPathOpen(true)}>{t("harness.pane.pickPath")}</Button>
    </div>
    {pathOpen && <HarnessPathDialog harness={row.harness} name={name} manualPath={row.manualPath} disabled={inert}
      reason={refusal} onSave={path => { setPathOpen(false); onSavePath(path); }} onClose={() => setPathOpen(false)} />}
  </Block>;
}

function AccountBlock({ view, row, gate, onClear }: { view: HarnessDetailsView; row: HarnessHealth; gate: Gate; onClear: (provider: string) => void }) {
  const { t } = useTranslation("buddies");
  const { account } = view;
  return <Block title={t("harness.account.name")}>
    {account ? <div className={fields}>
      <span className={label}>{t("harness.account.state")}</span>
      <Hint text={account.reason ?? undefined}><span tabIndex={0} className={hintTarget}>{account.state}</span></Hint>
      <span className={label}>{t("harness.account.billing")}</span>
      <span className="flex flex-col">{account.billing.length
        ? account.billing.map(entry => <span key={entry.provider}>{entry.provider} <span className="text-ink3">{entry.plan}</span></span>)
        : t("harness.account.plan.unknown")}</span>
    </div> : <span className="text-xs text-ink3">{t("harness.unrecorded")}</span>}
    <CoolingDetails providers={row.providers ?? []} gate={gate} onClear={onClear} />
  </Block>;
}

/** The account expansion belongs to its harness header, independent of model drawers. */
export function HarnessDetails({ row, name, canWrite, refusal, busy, onCheck, onSavePath, onRetry }: {
  row: HarnessHealth; name: string;
  onCheck: () => void; onSavePath: (path: string | null) => void; onRetry: (provider: string) => void;
} & Gate) {
  const { t } = useTranslation("buddies");
  const view = harnessDetailsView(row, t);
  const gate = { canWrite, refusal, busy };
  return <div role="group" aria-label={t("harness.detailsLabel", { name })} data-slot="harness-details"
    className="grid border-b-2 border-ink3 bg-card text-sm min-[900px]:pl-[38px] min-[900px]:grid-cols-2 min-[900px]:divide-x max-[899px]:divide-y">
    <LocationBlock row={row} name={name} view={view} gate={gate} onCheck={onCheck} onSavePath={onSavePath} />
    <AccountBlock view={view} row={row} gate={gate} onClear={onRetry} />
  </div>;
}
