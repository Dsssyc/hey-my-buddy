import i18n from "i18next";
import { useCopyState } from "./i18n/use-copy-state";
import { useTranslation } from "react-i18next";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import { LOGIN_EXPIRED_ACTION_REFUSAL, LOGIN_EXPIRED_SAVE_REFUSAL, UNRESOLVED_SAVE_NOTE, createAuthorityLatch } from "./console-session";
import type { AuthorityLatch } from "./console-session";
import {
  changeCount as countChanges, configurationChanged as configurationDiffers,
  draftDiffers, historyView, makeDraft, publication,
  rebaseDraft, withHistory,
} from "./draft";
import type { HistoryEntries, UserPolicyPublication } from "./draft";
import { attentionIssues, blockingIssues } from "./policy";
import type { ConsoleView, Draft, Snapshot } from "./types";
import { historySourceKey } from "./use-buddy-history";

/** Local Buddy settings draft with a single idempotent policy publication. */
export function useEditor(
  api: ConsoleApi,
  snapshot: Snapshot,
  refresh: () => Promise<Snapshot | null>,
  mutationsAvailable: boolean,
  unavailableReason: string | (() => string),
  authority?: AuthorityLatch,
) {
  const unavailable = () => typeof unavailableReason === "function" ? unavailableReason() : unavailableReason;
  const { i18n: { language } } = useTranslation("console");
  const [draft, setDraftState] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [historyEntries, setHistoryEntries] = useState<{ key: string; entries: HistoryEntries | null } | null>(null);
  const [saving, setSaving] = useState(false);
  const [auxiliaryBusy, setAuxiliaryBusy] = useState(false);
  const [error, setError] = useCopyState();
  const [blocked, setBlocked] = useCopyState();
  const [notice, setNotice] = useCopyState();
  const [confirming, setConfirming] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const csrf = useRef(snapshot.csrfToken);
  csrf.current = snapshot.csrfToken;
  const localLatch = useRef<AuthorityLatch | null>(null);
  if (!localLatch.current) localLatch.current = createAuthorityLatch();
  const latch = authority ?? localLatch.current;
  const sessionId = useRef(snapshot.consoleSession?.id);
  sessionId.current = snapshot.consoleSession?.id;
  const sessionWritable = latch.writable(snapshot);
  const writable = useRef(sessionWritable);
  writable.current = sessionWritable;
  const alive = useRef(true);
  const draftRef = useRef<Draft | null>(null);
  const baselineRef = useRef<Draft | null>(null);
  const historyRef = useRef<{ key: string; entries: HistoryEntries | null } | null>(null);
  const pendingRebase = useRef<Snapshot | null>(null);
  const pendingSave = useRef<UserPolicyPublication | null>(null);
  const saveDispatched = useRef(false);
  const saveAmbiguous = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const setDraft: Dispatch<SetStateAction<Draft | null>> = useCallback(update => {
    setDraftState(previous => {
      const next = typeof update === "function" ? update(previous) : update;
      draftRef.current = next;
      return next;
    });
  }, []);
  const setBaselineValue = useCallback((value: Draft | null) => {
    baselineRef.current = value;
    setBaseline(value);
  }, []);
  const dirty = useMemo(() => !!baseline && !!draft && draftDiffers(baseline, draft), [baseline, draft]);
  const changeCount = useMemo(() => baseline && draft ? countChanges(baseline, draft) : 0, [baseline, draft]);
  const configurationDirty = useMemo(() => !!baseline && !!draft && configurationDiffers(baseline, draft), [baseline, draft]);
  const sourceKey = historySourceKey(snapshot);
  const lastSourceKey = useRef(sourceKey);
  const currentHistory = historyEntries?.key === sourceKey ? historyEntries.entries : null;
  const view: ConsoleView = useMemo(() => {
    const recorded = historyView(snapshot, currentHistory);
    if (!draft) return recorded;
    const edited = { ...recorded, buddies: draft.buddies,
      preferences: draft.preferences,
      modelServiceNotes: draft.modelServiceNotes, configuration: draft.configuration,
      modelServiceConcurrency: draft.modelServiceConcurrency };
    return edited;
  }, [snapshot, draft, currentHistory]);
  const blocking = useMemo(() => baseline && draft ? blockingIssues(baseline, draft) : [], [baseline, draft, language]);
  const attention = useMemo(() => attentionIssues(view), [view, language]);
  const busy = saving || auxiliaryBusy;
  const editing = !busy && !confirming && sessionWritable;
  const saveBlockedReason = !mutationsAvailable && draft
    ? unavailable() || i18n.t("console:cannotSaveRightNowYourDraftStays") : "";
  const mayWrite = () => writable.current && latch.writable(snapshot);
  const loseAuthority = () => { latch.lose(sessionId.current); writable.current = false; };

  function closeDraft() {
    draftRef.current = null;
    setDraft(null);
    setBaselineValue(null);
    pendingRebase.current = null;
    pendingSave.current = null;
    saveDispatched.current = false;
    saveAmbiguous.current = false;
    setConfirming(false);
    setUncertain(false);
    setBlocked("");
  }

  function update(change: (draft: Draft) => Draft) {
    setBlocked("");
    if (busy) { setBlocked(() => i18n.t("console:savingOrReadingTheCatalogWaitBefore")); return; }
    if (confirming) { setBlocked(() => i18n.t("console:theSaveResultIsUnknownCheckIt")); return; }
    if (!mayWrite()) { setBlocked(() => LOGIN_EXPIRED_ACTION_REFUSAL()); return; }
    if (!draftRef.current) {
      setNotice("");
      const next = withHistory(makeDraft(snapshot), historyRef.current?.key === sourceKey ? historyRef.current.entries : null);
      draftRef.current = next;
      setDraft(next);
      setBaselineValue(structuredClone(next));
    }
    setDraft(current => current ? change(current) : current);
  }

  useEffect(() => {
    if (!draft || dirty || busy || confirming || uncertain || pendingRebase.current || pendingSave.current) return;
    closeDraft();
  }, [draft, dirty, busy, confirming, uncertain]);

  async function publish(payload: UserPolicyPublication) {
    if (!mayWrite()) {
      if (saveDispatched.current) {
        setUncertain(true); setConfirming(true); setError(() => UNRESOLVED_SAVE_NOTE());
      } else {
        pendingSave.current = null; setError(() => LOGIN_EXPIRED_SAVE_REFUSAL());
      }
      return;
    }
    saveDispatched.current = true;
    try {
      await api.command("user_policy_publish", payload, csrf.current);
      if (!alive.current) return;
      const reread = await refresh();
      if (!alive.current) return;
      if (!reread) {
        saveAmbiguous.current = true;
        setUncertain(true); setConfirming(true);
        setError(() => i18n.t("console:settingsSavedButTheNewReadFailed"));
        return;
      }
      closeDraft();
      setNotice(() => i18n.t("console:settingsSaved"));
    } catch (failure) {
      if (!alive.current) return;
      if (isSessionExpiredRefusal(failure)) {
        loseAuthority();
        if (saveAmbiguous.current) {
          setUncertain(true); setConfirming(true); setError(() => UNRESOLVED_SAVE_NOTE());
        } else {
          pendingSave.current = null; saveDispatched.current = false;
          setUncertain(false); setConfirming(false); setError(() => errorText(failure));
        }
        return;
      }
      if (saveAmbiguous.current && failure instanceof ApiError
        && ["FORBIDDEN", "UNAUTHORIZED"].includes(failure.code)) {
        setUncertain(true); setConfirming(true); setError(() => UNRESOLVED_SAVE_NOTE());
        await refresh();
        return;
      }
      if (uncertainResponse(failure)) {
        saveAmbiguous.current = true;
        setUncertain(true); setConfirming(true);
        setError(() => i18n.t("console:theSaveResultIsUnknownAndMay2"));
        return;
      }
      pendingSave.current = null;
      saveDispatched.current = false;
      saveAmbiguous.current = false;
      setConfirming(false); setUncertain(false);
      setError(() => errorText(failure));
    }
  }

  async function save() {
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || busy) {
      if (busy) setBlocked(() => i18n.t("console:readingTheCatalogOrSavingTryAgain"));
      return;
    }
    if (!mayWrite()) {
      setBlocked(pendingSave.current && saveDispatched.current ? UNRESOLVED_SAVE_NOTE : LOGIN_EXPIRED_SAVE_REFUSAL);
      return;
    }
    if (!mutationsAvailable) {
      setBlocked(() => unavailable() || i18n.t("console:cannotSaveRightNowRetryShortlyYour"));
      return;
    }
    setBlocked(""); setError(""); setNotice("");
    const blockedIssues = blockingIssues(base, current);
    if (blockedIssues.length) {
      setError(() => i18n.t("console:cannotSaveItems", { v0: blockedIssues.length, v1: blockingIssues(base, current).map(issue => issue.message).join(" ") }));
      return;
    }
    setSaving(true);
    try {
      if (pendingSave.current) { await publish(pendingSave.current); return; }
      if (!draftDiffers(base, current)) return;
      const payload = publication(base, current, crypto.randomUUID());
      pendingSave.current = payload;
      saveDispatched.current = false;
      saveAmbiguous.current = false;
      await publish(payload);
    } finally { if (alive.current) setSaving(false); }
  }

  async function discard() {
    if (busy) { setBlocked(() => i18n.t("console:savingWaitForTheResult")); return; }
    if (confirming) { setBlocked(() => i18n.t("console:theSaveResultIsUnknownCheckIt")); return; }
    closeDraft();
    setError(""); setNotice(() => i18n.t("console:draftDiscarded"));
    await refresh();
  }

  async function reloadLatest() {
    if (busy || confirming) { setBlocked(() => i18n.t("console:saveUnconfirmedCheckItBeforeReloading")); return; }
    setError(""); setNotice("");
    const next = withHistory(makeDraft(snapshot), historyRef.current?.key === sourceKey ? historyRef.current.entries : null);
    draftRef.current = next;
    setDraft(next);
    setBaselineValue(structuredClone(next));
    pendingRebase.current = null;
    pendingSave.current = null;
    saveDispatched.current = false;
    saveAmbiguous.current = false;
    setUncertain(false);
    setNotice(() => i18n.t("console:settingsReloadedCheckThemBeforeSaving"));
  }

  function adoptHistory(entries: HistoryEntries | null, key: string) {
    if (key !== sourceKey) return;
    const usable = entries;
    historyRef.current = { key, entries: usable };
    setHistoryEntries({ key, entries: usable });
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || saving || confirming || uncertain) return;
    const pending = pendingRebase.current;
    if (usable) {
      const latest = pending && historySourceKey(pending) === key ? pending : snapshot;
      const adopted = rebaseDraft(current, base, latest, usable);
      if (adopted.conflicts.length) {
        pendingRebase.current = adopted.conflicts.some(issue => issue.kind === "unread") ? latest : null;
        return;
      }
      pendingRebase.current = null;
      draftRef.current = adopted.draft;
      setDraft(adopted.draft);
      setBaselineValue(adopted.baseline);
      return;
    }
    // The next retained page will be rebased against the current snapshot.
    return;
  }

  function rebase(next: Snapshot) {
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || saving || confirming || uncertain) return;
    const retained = historyRef.current?.key === historySourceKey(next) ? historyRef.current.entries : null;
    const adopted = rebaseDraft(current, base, next, retained);
    if (adopted.conflicts.length) {
      pendingRebase.current = adopted.conflicts.some(issue => issue.kind === "unread") ? next : null;
      return;
    }
    pendingRebase.current = null;
    draftRef.current = adopted.draft;
    setDraft(adopted.draft);
    setBaselineValue(adopted.baseline);
  }

  useEffect(() => {
    if (lastSourceKey.current === sourceKey) return;
    lastSourceKey.current = sourceKey;
    rebase(snapshot);
  }, [sourceKey, snapshot]);

  return {
    mode: draft !== null, editing, sessionWritable, draft, view, setDraft, baseline,
    busy, setAuxiliaryBusy, uncertain, confirming, dirty, changeCount,
    configurationDirty, blocking, attention,
    error, notice, blocked, saveBlockedReason,
    update, save, discard, reloadLatest, rebase, adoptHistory,
  };
}
export type Editor = ReturnType<typeof useEditor>;
