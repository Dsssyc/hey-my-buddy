import { useCopyState } from "../i18n/use-copy-state";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi, StorageApplyResult, StoragePlan } from "../api";
import { ApiError, errorText, uncertainResponse } from "../api";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle, DialogTrigger } from "../components/ui/dialog";
import { StateMark } from "../components/ui/state-mark";
import { formatDateTime, formatDuration } from "../i18n/format";
import { StorageOrphans, StorageOutcome, StorageTable } from "./StorageDetails";
import { Section } from "./Section";
import { CATEGORY_ORDER, byteHint, byteText, categoryText } from "./storage-display";

type Phase = "idle" | "planning" | "planned" | "applying" | "applied" | "unknown" | "incomplete";

/** Only an explicit check scans storage. Unresolved deletion keeps one replayable identity. */
export function StorageSection({ api, csrfToken, connectionError }: {
  api: ConsoleApi; csrfToken: string; connectionError: string;
}) {
  const { t } = useTranslation("settingsStorage");
  const [phase, setPhase] = useState<Phase>("idle");
  const [plan, setPlan] = useState<StoragePlan | null>(null);
  const [error, setError] = useCopyState();
  const [result, setResult] = useState<StorageApplyResult | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [commandId, setCommandId] = useState("");
  const [now, setNow] = useState(Date.now);
  const alive = useRef(true);
  const inFlight = useRef(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  // Refresh the display at the exact expiry, including while a confirmation is open.
  useEffect(() => {
    if (!plan) return;
    const remaining = Date.parse(plan.expiresAt) - Date.now();
    if (remaining <= 0) { if (now < Date.parse(plan.expiresAt)) setNow(Date.now()); return; }
    const timer = setTimeout(() => setNow(Date.now()), Math.min(remaining, 30_000));
    return () => clearTimeout(timer);
  }, [plan, now]);
  const expired = !!plan && now >= Date.parse(plan.expiresAt);
  const unresolved = phase === "unknown" || phase === "incomplete";
  const busy = phase === "planning" || phase === "applying";
  const categories = useMemo(() => plan ? [...plan.categories].sort((a, b) => {
    const rank = (id: string) => { const n = (CATEGORY_ORDER as readonly string[]).indexOf(id); return n < 0 ? CATEGORY_ORDER.length : n; };
    return rank(a.id) - rank(b.id);
  }) : [], [plan]);
  const totalBytes = categories.reduce((sum, category) => sum + category.bytes, 0);
  const totalReclaimable = categories.reduce((sum, category) => sum + category.reclaimableBytes, 0);
  const checkReason = connectionError || (busy ? t(phase === "planning" ? "checking" : "applying") : unresolved ? t("replayFirst") : "");
  const reclaimReason = connectionError || (!plan ? t("unchecked") : busy ? t(phase === "planning" ? "checking" : "applying") : expired ? t("expiredHint") : totalReclaimable <= 0 ? t("none") : "");

  const requestPlan = useCallback(async () => {
    if (connectionError || inFlight.current || unresolved) return;
    inFlight.current = true;
    setPhase("planning"); setError(""); setConfirmOpen(false);
    try {
      const next = await api.storagePlan(csrfToken);
      if (!alive.current) return;
      setPlan(next); setNow(Date.now()); setResult(null); setCommandId(""); setPhase("planned");
    } catch (failure) {
      if (!alive.current) return;
      setError(() => errorText(failure)); setPhase(plan ? "planned" : "idle");
    } finally { inFlight.current = false; }
  }, [api, csrfToken, plan, connectionError, unresolved]);

  const applyPlan = useCallback(async (replay: boolean) => {
    if (!plan || connectionError || inFlight.current) return;
    // Dispatch guards use the current clock, not the most recent rendered clock.
    if (!replay && Date.now() >= Date.parse(plan.expiresAt)) { setNow(Date.now()); return; }
    inFlight.current = true;
    setPhase("applying"); setError(""); setConfirmOpen(false);
    try {
      const reply = await api.storageApply(plan.planId, commandId, csrfToken);
      if (!alive.current) return;
      setResult(reply); setPhase("applied");
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiError && failure.code === "STORAGE_INCOMPLETE") { setPhase("incomplete"); return; }
      if (uncertainResponse(failure)) { setPhase("unknown"); return; }
      setError(() => errorText(failure)); setPhase("planned");
    } finally { inFlight.current = false; }
  }, [api, csrfToken, plan, commandId, connectionError]);

  function prepareConfirmation() {
    if (!plan || reclaimReason || unresolved || phase === "applied") return false;
    if (Date.now() >= Date.parse(plan.expiresAt)) { setNow(Date.now()); return false; }
    if (!commandId) setCommandId(crypto.randomUUID());
    return true;
  }

  return <Dialog open={confirmOpen} onOpenChange={setConfirmOpen}><Section title={t("heading")}><div data-slot="storage-section" className="max-w-[760px] space-y-4 text-sm" aria-busy={busy}>
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3">
        <div role="group" aria-label={t("overview")} className="flex flex-wrap items-center gap-6">
          <span className="flex items-baseline gap-2"><span className="text-ink3">{t("used")}</span><Hint text={plan ? byteHint(totalBytes) : undefined}><span tabIndex={plan ? 0 : undefined} className="tabular-nums">{plan ? byteText(totalBytes) : t("unchecked")}</span></Hint></span>
          <span className="flex items-baseline gap-2"><span className="text-ink3">{t("reclaimable")}</span><Hint text={plan ? byteHint(totalReclaimable) : undefined}><span tabIndex={plan ? 0 : undefined} className={`tabular-nums ${plan && totalReclaimable > 0 ? "text-hand" : "text-ink3"}`}>{plan ? byteText(totalReclaimable) : t("unchecked")}</span></Hint></span>
        </div>
        <Hint text={checkReason || (expired || phase === "applied" ? t("expiredHint") : plan ? t("checked", { time: formatDateTime(Date.parse(plan.createdAt)), duration: formatDuration(Date.parse(plan.expiresAt) - now) }) : undefined)}><Button variant="outline" aria-disabled={!!checkReason} onClick={() => { if (!checkReason) void requestPlan(); }}>{t("check")}</Button></Hint>
        {!unresolved && phase !== "applied" && <Hint text={reclaimReason || t("reclaimHint", { bytes: byteText(totalReclaimable) })}><DialogTrigger asChild><Button variant="outline" aria-disabled={!!reclaimReason} onClick={event => { if (!prepareConfirmation()) event.preventDefault(); }}>{t("reclaim")}</Button></DialogTrigger></Hint>}
      </div>
      {phase === "planning" && <p role="status" className="flex items-center gap-2 text-xs text-ink2"><StateMark kind="live" />{t("checking")}</p>}
      {error && <div className="flex items-center gap-3"><p role="alert" className="text-xs text-fail">{error}</p><Hint text={checkReason || (expired || phase === "applied" ? t("expiredHint") : plan ? t("checked", { time: formatDateTime(Date.parse(plan.createdAt)), duration: formatDuration(Date.parse(plan.expiresAt) - now) }) : undefined)}><Button variant="outline" aria-disabled={!!checkReason} onClick={() => { if (!checkReason) void requestPlan(); }}>{t("retry")}</Button></Hint></div>}
      {plan && <>
        {(expired || phase === "applied") && <p className="text-xs text-ink3">{t(phase === "applied" ? "applied" : "expired")}</p>}
        <StorageTable categories={categories} />
        {plan.orphanProcesses.length > 0 && <StorageOrphans processes={plan.orphanProcesses} />}
        {phase === "applied" && result && <StorageOutcome key={commandId} result={result} />}
        {unresolved && <div className="flex flex-wrap items-center gap-3"><p role="status" className="flex items-center gap-2 text-xs text-hand"><StateMark kind="hand" />{t(phase === "unknown" ? "unknown" : "incomplete")}</p><Hint text={connectionError || t("replayHint")}><Button variant="outline" size="sm" aria-disabled={!!connectionError} onClick={() => { if (!connectionError) void applyPlan(true); }}>{t("replay")}</Button></Hint></div>}
        {phase === "applying" && <p role="status" className="flex items-center gap-2 text-xs text-ink2"><StateMark kind="live" />{t("applying")}</p>}
      </>}
    </div>
      <DialogContent aria-modal="true" onOpenAutoFocus={event => { event.preventDefault(); cancelRef.current?.focus(); }}>
        <DialogTitle>{t("confirmTitle")}</DialogTitle>
        <DialogDescription>{t("confirmDetail")}</DialogDescription>
        {plan && <ul className="space-y-2 text-sm">{categories.filter(category => category.reclaimableBytes > 0).map(category => <li key={category.id}>{t("confirmRow", { category: categoryText(category.id), bytes: byteText(category.reclaimableBytes) })}</li>)}
          <li className="font-medium">{t("confirmTotal", { bytes: byteText(totalReclaimable) })}</li><li className="text-xs text-ink3">{t("confirmChecked", { time: formatDateTime(Date.parse(plan.createdAt)) })}</li>
        </ul>}
        {expired && <p role="alert" className="text-sm text-fail">{t("confirmExpired")}</p>}
        {connectionError && <p role="alert" className="text-sm text-fail">{connectionError}</p>}
        <DialogFooter><Button ref={cancelRef} variant="outline" onClick={() => setConfirmOpen(false)}>{t("cancel")}</Button><Hint text={reclaimReason || undefined}><Button variant="default" aria-disabled={!!reclaimReason} onClick={() => { if (!reclaimReason) void applyPlan(false); }}>{t("confirm")}</Button></Hint></DialogFooter>
      </DialogContent>
  </Section></Dialog>;
}
