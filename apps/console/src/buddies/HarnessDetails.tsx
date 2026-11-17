import { cn } from "cn";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import type { HarnessHealth } from "../types";
import { harnessDetailsView, statusText } from "./harness";
import type { HarnessDetailsView } from "./harness";
import { HarnessLogo } from "./HarnessLogo";
import { HarnessPathDialog } from "./HarnessPathDialog";

const hintTarget = "rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";
const label = "text-xs text-ink3";
const fields = "grid min-w-0 grid-cols-[max-content_minmax(0,1fr)] content-start items-baseline gap-x-3 gap-y-2";

function PathText({ path }: { path: string }) {
  return <>{path.split(/(?<=[\\/])/).map((part, index) => <span key={index} className="inline-block max-w-full align-bottom wrap-anywhere">{part}<wbr /></span>)}</>;
}

function Exception({ exception }: { exception: NonNullable<HarnessDetailsView["location"]["exception"]> }) {
  const { t } = useTranslation("buddies");
  return <div className={cn(fields, "col-span-2 mt-1")}>
    {exception.reason && <><span className={label}>{t("harness.pane.reason")}</span><span className="wrap-anywhere">{exception.reason}</span></>}
    {exception.tried.length > 0 && <>
      <span className={cn(label, "col-span-2")}>{t("harness.pane.tried")}</span>
      {exception.tried.map((tried, index) => <div key={`${tried.path ?? ""}:${index}`} className="col-span-2 flex min-w-0 flex-col gap-0.5">
        <code data-slot="harness-path" className="font-mono text-xs wrap-anywhere">{tried.path === null ? t("harness.pane.noPath") : <PathText path={tried.path} />}</code>
        <span className="text-xs text-ink3">{[tried.source, tried.result].filter(Boolean).join(" · ")}</span>
      </div>)}
    </>}
  </div>;
}

/** One wide harness card with location and account facts in adjacent columns. */
export function HarnessDetails({ row, name, canWrite, refusal, busy, onCheck, onSavePath }: {
  row: HarnessHealth; name: string; canWrite: boolean; refusal: string; busy: boolean;
  onCheck: () => void; onSavePath: (path: string | null) => void;
}) {
  const { t } = useTranslation("buddies");
  const view = harnessDetailsView(row, t);
  const { location, account } = view;
  const [pathOpen, setPathOpen] = useState(false);
  const inert = busy || !canWrite;
  const reason = refusal || (busy ? t("harness.running") : undefined);
  return <div role="group" aria-label={t("harness.detailsLabel", { name })} data-slot="harness-details" className="grid grid-cols-1 gap-x-8 gap-y-3 px-4 pb-4 text-sm sm:px-5 md:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
    <div className="col-span-full flex min-h-12 flex-wrap items-center justify-between gap-x-4 gap-y-2 py-2.5">
      <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
        <h3 id={`harness-title-${row.harness}`} tabIndex={-1} className="flex items-center gap-2 text-base font-semibold outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">
          <HarnessLogo harness={row.harness} />{name}
        </h3>
        {row.version && <span data-slot="harness-version" className="text-xs text-ink3 tabular-nums">{row.version}</span>}
        <span className="text-xs text-ink3" aria-hidden="true">·</span>
        <span className="inline-flex items-center gap-1.5 text-xs text-ink2"><StateMark kind={row.status === "ready" ? "live" : "idle"} />
          <Hint text={location.exception?.reason ?? undefined}><span tabIndex={0} className={hintTarget}>{statusText(row.status, t)}</span></Hint>
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Hint text={reason}><Button variant="outline" className="h-7 px-2 text-xs sm:h-8 sm:px-3 sm:text-sm" aria-disabled={inert || undefined} onClick={onCheck}>{t("harness.check")}</Button></Hint>
        <Button variant="outline" className="h-7 px-2 text-xs sm:h-8 sm:px-3 sm:text-sm" onClick={() => setPathOpen(true)}>{t("harness.pane.pickPath")}</Button>
      </div>
    </div>
      <div role="group" aria-label={t("harness.pane.location")} className={cn(fields, "text-xs")}>
        <span className={label}>{t("harness.pane.path")}</span>
        <span className="min-w-0"><span data-slot="harness-path" className="font-mono text-xs text-foreground wrap-anywhere">{location.path === null ? t("harness.unrecorded") : <PathText path={location.path} />}</span>
          {(location.path !== null || row.manualPath !== null) && <span data-slot="harness-source" className="whitespace-nowrap text-ink3"> · {location.source}</span>}</span>
        <span className={label}>{t("harness.pane.permissions")}</span>
        <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1">
          <span>{t("harness.pane.review")} <Hint text={[location.review.note, location.review.reason].filter(Boolean).join(" ")}><span tabIndex={0} className={cn(hintTarget, "text-foreground")}>{location.review.text}</span></Hint></span>
          <span className="text-ink3" aria-hidden="true">/</span>
          <Hint text={t(row.permissions?.web === "enforced" ? "harness.web.enforcedDescription" : "harness.web.unenforcedDescription")}><span tabIndex={0} className={hintTarget}>{t(row.permissions?.web === "enforced" ? "harness.web.summaryEnforced" : "harness.web.summaryUnenforced")}</span></Hint>
        </span>
        {location.exception && <Exception exception={location.exception} />}
      </div>
      <div role="group" aria-label={t("harness.account.name")} className={cn(fields, "text-xs")}>
        <span className={label}>{t("harness.account.state")}</span>
        <Hint text={account.reason ?? undefined}><span tabIndex={0} className={cn(hintTarget, "text-foreground")}>{account.state}</span></Hint>
        <span className={label}>{t("harness.account.billing")}</span>
        <span className="flex flex-wrap gap-x-3 gap-y-0.5 text-foreground">{account.billing.length ? account.billing.map(entry => <span key={entry.provider} className="whitespace-nowrap">{entry.provider} <span className="text-ink3">{entry.plan}</span></span>) : <span className="text-ink3">{t("harness.account.plan.unknown")}</span>}</span>
      </div>
    {pathOpen && <HarnessPathDialog harness={row.harness} name={name} manualPath={row.manualPath} disabled={inert}
      reason={reason} onSave={path => { setPathOpen(false); onSavePath(path); }} onClose={() => setPathOpen(false)} />}
  </div>;
}
