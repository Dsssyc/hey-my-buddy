import { cn } from "cn";
import { useId, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import type { ConsoleApi } from "../api";
import type { HarnessHealth } from "../types";
import { harnessPaneView, statusText } from "./harness";
import type { HarnessPaneView, WindowRow } from "./harness";
import { HarnessAccountDialog } from "./HarnessAccountDialog";
import { HarnessPathDialog } from "./HarnessPathDialog";
import { paneBlockHead, paneFields } from "./styles";

/** A word that carries a hint can be reached with the keyboard. */
const hintTarget = "rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";
const label = "min-w-0 text-xs break-words text-ink3";
/** Where the buttons of a block stand: under the values of its fields. */
const buttons = "flex flex-wrap gap-2 pr-5 pb-4 pl-[108px]";

/** A block of the column: its name and its state or time on the head, then its fields. */
function Block({ title, head, children }: { title: string; head?: ReactNode; children: ReactNode }) {
  return <div role="group" aria-label={title}>
    <div className={paneBlockHead}>
      <b className="font-medium text-foreground">{title}</b>
      <span className="flex items-center gap-1.5">{head}</span>
    </div>
    {children}
  </div>;
}

/** What the actions of the column need to know: whether one can start, and why not. */
type Gate = {
  /** The session can write and the connection is up. */
  canWrite: boolean;
  /** Why it cannot, when it cannot; empty otherwise. */
  refusal: string;
  /** Another action is under way. */
  busy: boolean;
};

/** The extra of a harness that is not found: why, what to do about it, and where it was looked for. */
function Exception({ exception }: { exception: NonNullable<HarnessPaneView["location"]["exception"]> }) {
  const { t } = useTranslation("buddies");
  return <div className={cn(paneFields, "pt-0 pb-3")}>
    {exception.reason && <><span className={label}>{t("harness.pane.reason")}</span><div>{exception.reason}</div></>}
    {exception.remedy && <div className="col-span-2 text-xs text-ink2">{exception.remedy}</div>}
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
  row: HarnessHealth; name: string; view: HarnessPaneView; gate: Gate; onCheck: () => void; onSavePath: (path: string | null) => void;
}) {
  const { t } = useTranslation("buddies");
  const [pathOpen, setPathOpen] = useState(false);
  const { location } = view;
  const inert = gate.busy || !gate.canWrite || undefined;
  return <Block title={t("harness.pane.location")}
    head={<><StateMark kind={row.status === "ready" ? "live" : "idle"} />{statusText(row.status, t)}</>}>
    <div className={cn(paneFields, "pb-3")}>
      <span className={label}>{t("harness.pane.path")}</span>
      <div className="font-mono text-xs break-all">{location.path ?? t("harness.unrecorded")}</div>
      <span className={label}>{t("harness.pane.source")}</span>
      <div>{location.source}</div>
      <span className={label}>{t("harness.pane.checked")}</span>
      <div className="tabular-nums">{location.checked ?? t("harness.unrecorded")}</div>
      <span className={label}>{t("harness.pane.review")}</span>
      <div>
        <Hint text={location.review.note}><span tabIndex={0} className={hintTarget}>{location.review.text}</span></Hint>
        {location.review.reason && <p className="mt-1 text-xs text-ink3" role="status">{location.review.reason}</p>}
      </div>
    </div>
    {location.exception && <Exception exception={location.exception} />}
    <div className={buttons}>
      <Hint text={gate.refusal || undefined}>
        <Button variant="outline" aria-disabled={inert} onClick={onCheck}>{t("harness.check")}</Button>
      </Hint>
      <Button variant="outline" onClick={() => setPathOpen(true)}>{t("harness.pane.pickPath")}</Button>
    </div>
    {pathOpen && <HarnessPathDialog adapter={row.adapter} name={name} manualPath={row.manualPath} disabled={!!inert}
      reason={gate.refusal || undefined} onSave={path => { setPathOpen(false); onSavePath(path); }} onClose={() => setPathOpen(false)} />}
  </Block>;
}

function AccountBlock({ name, view, api, csrfToken, gate, onRefreshed }: {
  name: string; view: HarnessPaneView; api: ConsoleApi; csrfToken: string; gate: Gate; onRefreshed: () => Promise<unknown>;
}) {
  const { t } = useTranslation("buddies");
  const [open, setOpen] = useState(false);
  const { account } = view;
  return <Block title={t("harness.account.name")} head={account?.source}>
    {account ? <>
      <div className={cn(paneFields, "pb-3")}>
        <span className={label}>{t("harness.account.state")}</span>
        <div>{account.state}</div>
        <span className={label}>{t("harness.account.billing")}</span>
        <div className="flex flex-col">
          {account.billing.length
            ? account.billing.map(entry => <span key={entry.provider}>{entry.provider} <span className="text-ink3">{entry.plan}</span></span>)
            : t("harness.account.plan.unknown")}
        </div>
        {account.guidance && <div className="col-span-2 text-xs text-ink3">{account.guidance}</div>}
      </div>
      <div className={buttons}>
        <Button variant="outline" onClick={() => setOpen(true)}>{t("harness.account.manage")}</Button>
      </div>
      {open && <HarnessAccountDialog account={account.parsed} name={name} api={api} csrfToken={csrfToken}
        canWrite={gate.canWrite} reason={gate.refusal || undefined} onRefreshed={onRefreshed} onClose={() => setOpen(false)} />}
    </> : <p className="px-5 pt-1 pb-4 text-xs text-ink3">{t("harness.unrecorded")}</p>}
  </Block>;
}

/** One window of the quota block: how much of it is used, when it resets, and whether it is near its limit. */
function QuotaWindow({ window }: { window: WindowRow }) {
  const { t } = useTranslation("buddies");
  return <>
    <span className={label}>{window.name}</span>
    <div className="flex flex-wrap items-baseline gap-x-2.5 tabular-nums">
      <span className={cn(window.hot && "text-hand")}>{window.text}</span>
      <span className="text-ink3">{window.resets}</span>
      {window.flag && <span className="text-hand">{t(`harness.quota.${window.flag}`)}</span>}
    </div>
  </>;
}

function QuotaBlock({ view, gate, onRetry }: { view: HarnessPaneView; gate: Gate; onRetry: (provider: string) => void }) {
  const { t } = useTranslation("buddies");
  const { quota, exhausted, retryProviders } = view;
  const inert = gate.busy || !gate.canWrite || undefined;
  return <Block title={t("harness.quota.name")} head={quota && <>
    <Hint text={quota.note}><span tabIndex={0} className={hintTarget}>{t("harness.quota.observed")}</span></Hint>
    <span className="tabular-nums">{quota.observed ?? t("harness.unrecorded")}</span>
    {quota.stale && <span>{t("harness.quota.stale")}</span>}
  </>}>
    {quota ? <div className={cn(paneFields, "pb-3")}>
      {quota.windows.map(window => <QuotaWindow key={window.name} window={window} />)}
      <span className={label}>{t("harness.quota.source")}</span>
      <div className="break-all">{quota.source}</div>
      {quota.limitReported && <div className="col-span-2 text-hand">{t("harness.quota.reported")}</div>}
    </div> : <p className="px-5 pt-1 pb-4 text-xs text-ink3">{t("harness.unrecorded")}</p>}
    {exhausted.length > 0 && <>
      <ul aria-label={t("harness.quota.exhaustedList")} className="mx-5 divide-y border-y">
        {exhausted.map(record => <li key={record.key} className="flex flex-col gap-0.5 py-2.5">
          <div className="flex items-baseline justify-between gap-3">
            <span className="font-mono text-xs break-all">{record.provider}{record.limitId ? ` · ${record.limitId}` : ""}</span>
            <span className="shrink-0 text-xs text-hand">{record.word}</span>
          </div>
          <span className="text-xs text-ink3">{record.observation}</span>
          <span className="text-xs">{record.recovery}</span>
        </li>)}
      </ul>
      {retryProviders.length > 0 && <div className="flex flex-wrap gap-2 px-5 pt-3 pb-4">
        {retryProviders.map(provider => <Hint key={provider} text={[gate.refusal, t("harness.quota.openHint")].filter(Boolean).join(" ")}>
          <Button variant="outline" aria-disabled={inert} onClick={() => onRetry(provider)}>
            {retryProviders.length > 1 ? t("harness.quota.openFor", { provider }) : t("harness.quota.open")}
          </Button>
        </Hint>)}
      </div>}
    </>}
  </Block>;
}

/**
 * The side column of the harness table: the selected harness's location, its
 * account and its quota. Every action here is an explicit click that the view
 * carries out; the column only offers it, or says why it cannot be done.
 */
export function HarnessPane({ row, name, api, csrfToken, canWrite, refusal, busy, onCheck, onSavePath, onRetry, onRefreshed, onBack, className }: {
  row: HarnessHealth; name: string; api: ConsoleApi; csrfToken: string;
  onCheck: () => void;
  onSavePath: (path: string | null) => void;
  onRetry: (provider: string) => void;
  onRefreshed: () => Promise<unknown>;
  onBack: () => void;
  className?: string;
} & Gate) {
  const { t } = useTranslation("buddies");
  const id = useId();
  const view = harnessPaneView(row, t);
  const gate = { canWrite, refusal, busy };
  return <aside aria-labelledby={`${id}-title`} className={className}>
    <div className="px-5 pt-[18px] pb-3.5">
      <Button variant="outline" size="sm" className="mb-3 xl:hidden" onClick={onBack}><span aria-hidden="true">‹</span>{t("harness.pane.back")}</Button>
      <h2 id={`${id}-title`} className="text-sm font-semibold">{name}</h2>
    </div>
    <LocationBlock row={row} name={name} view={view} gate={gate} onCheck={onCheck} onSavePath={onSavePath} />
    <AccountBlock name={name} view={view} api={api} csrfToken={csrfToken} gate={gate} onRefreshed={onRefreshed} />
    <QuotaBlock view={view} gate={gate} onRetry={onRetry} />
  </aside>;
}
