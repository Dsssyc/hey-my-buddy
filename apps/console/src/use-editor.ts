import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, isSessionExpiredRefusal, uncertainResponse } from "./api";
import { LOGIN_EXPIRED_ACTION_REFUSAL, LOGIN_EXPIRED_SAVE_REFUSAL, UNRESOLVED_SAVE_NOTE, createAuthorityLatch } from "./console-session";
import type { AuthorityLatch } from "./console-session";
import {
  changeCount as countChanges, configurationChanged as configurationDiffers,
  draftDiffers, effectivePreferences, historyView, makeDraft, publication,
  rebaseDraft, retainedAdditions, withHistory,
} from "./draft";
import type { HistoryEntries, RebaseConflict, UserPolicyPublication } from "./draft";
import { attentionIssues, blockingIssues } from "./policy";
import type { ConsoleView, Draft, Snapshot } from "./types";

const isRevisionConflict = (error: unknown) =>
  error instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT"].includes(error.code);
export type EditorConflict = { basedOn: number; latest: number };

/** Local Buddy settings draft with a single idempotent policy publication. */
export function useEditor(
  api: ConsoleApi,
  snapshot: Snapshot,
  refresh: () => Promise<Snapshot | null>,
  mutationsAvailable: boolean,
  unavailableReason: string,
  authority?: AuthorityLatch,
) {
  const [draft, setDraftState] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [historyEntries, setHistoryEntries] = useState<HistoryEntries | null>(null);
  const [rebaseConflicts, setRebaseConflicts] = useState<RebaseConflict[]>([]);
  const [saving, setSaving] = useState(false);
  const [auxiliaryBusy, setAuxiliaryBusy] = useState(false);
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState("");
  const [notice, setNotice] = useState("");
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
  const historyRef = useRef<HistoryEntries | null>(null);
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
  const view: ConsoleView = useMemo(() => {
    const recorded = historyView(snapshot, historyEntries);
    if (!draft) return recorded;
    const edited = { ...recorded, profiles: draft.profiles,
      familyPreferences: draft.familyPreferences, preferenceOverrides: draft.preferenceOverrides,
      familyAnnotations: draft.familyAnnotations, configuration: draft.configuration,
      modelConcurrency: draft.modelConcurrency };
    return { ...edited, preferences: effectivePreferences(edited) };
  }, [snapshot, draft, historyEntries]);
  const blocking = useMemo(() => baseline && draft ? blockingIssues(baseline, draft) : [], [baseline, draft]);
  const attention = useMemo(() => attentionIssues(view), [view]);
  const conflict: EditorConflict | null = useMemo(() =>
    draft && !confirming && snapshot.tableRevision !== draft.tableRevision
      ? { basedOn: draft.tableRevision, latest: snapshot.tableRevision } : null,
  [draft, confirming, snapshot.tableRevision]);
  const busy = saving || auxiliaryBusy;
  const editing = !busy && !confirming && sessionWritable;
  const saveBlockedReason = !mutationsAvailable && draft
    ? unavailableReason || "当前无法提交保存，草稿会保留在本页。" : "";
  const mayWrite = () => writable.current && latch.writable(snapshot);
  const loseAuthority = () => { latch.lose(sessionId.current); writable.current = false; };

  function closeDraft() {
    draftRef.current = null;
    setDraft(null);
    setBaselineValue(null);
    pendingRebase.current = null;
    setRebaseConflicts([]);
    pendingSave.current = null;
    saveDispatched.current = false;
    saveAmbiguous.current = false;
    setConfirming(false);
    setUncertain(false);
    setBlocked("");
  }

  function update(change: (draft: Draft) => Draft) {
    setBlocked("");
    if (busy) { setBlocked("正在保存或读取目录；请稍后修改。"); return; }
    if (confirming) { setBlocked("保存结果未知；请核对或重试同一保存。"); return; }
    if (!mayWrite()) { setBlocked(LOGIN_EXPIRED_ACTION_REFUSAL); return; }
    if (!draftRef.current) {
      setNotice("");
      const next = withHistory(makeDraft(snapshot), historyRef.current);
      draftRef.current = next;
      setDraft(next);
      setBaselineValue(structuredClone(next));
    }
    setDraft(current => current ? change(current) : current);
  }

  useEffect(() => {
    if (!draft || dirty || busy || confirming || uncertain || rebaseConflicts.length || pendingRebase.current || pendingSave.current) return;
    closeDraft();
  }, [draft, dirty, busy, confirming, uncertain, rebaseConflicts]);

  async function publish(payload: UserPolicyPublication) {
    if (!mayWrite()) {
      if (saveDispatched.current) {
        setUncertain(true); setConfirming(true); setError(UNRESOLVED_SAVE_NOTE);
      } else {
        pendingSave.current = null; setError(LOGIN_EXPIRED_SAVE_REFUSAL);
      }
      return;
    }
    saveDispatched.current = true;
    try {
      await api.command("user_policy_publish", payload, csrf.current);
      if (!alive.current) return;
      closeDraft();
      setNotice("已发布新版本");
      await refresh();
    } catch (failure) {
      if (!alive.current) return;
      if (isSessionExpiredRefusal(failure)) {
        loseAuthority();
        if (saveAmbiguous.current) {
          setUncertain(true); setConfirming(true); setError(UNRESOLVED_SAVE_NOTE);
        } else {
          pendingSave.current = null; saveDispatched.current = false;
          setUncertain(false); setConfirming(false); setError(errorText(failure));
        }
        return;
      }
      if (saveAmbiguous.current && failure instanceof ApiError
        && ["FORBIDDEN", "UNAUTHORIZED"].includes(failure.code)) {
        setUncertain(true); setConfirming(true); setError(UNRESOLVED_SAVE_NOTE);
        await refresh();
        return;
      }
      if (uncertainResponse(failure)) {
        saveAmbiguous.current = true;
        setUncertain(true); setConfirming(true);
        setError("保存结果未知，可能已生效；请重试同一保存。");
        return;
      }
      pendingSave.current = null;
      saveDispatched.current = false;
      saveAmbiguous.current = false;
      setConfirming(false); setUncertain(false);
      if (isRevisionConflict(failure)) {
        await refresh();
        setError("设置已更新；请重新加载核对或放弃草稿。");
      } else setError(errorText(failure));
    }
  }

  async function save() {
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || busy) {
      if (busy) setBlocked("正在读取目录或保存；请稍后重试。");
      return;
    }
    if (!mayWrite()) {
      setBlocked(pendingSave.current && saveDispatched.current ? UNRESOLVED_SAVE_NOTE : LOGIN_EXPIRED_SAVE_REFUSAL);
      return;
    }
    if (!mutationsAvailable) {
      setBlocked(unavailableReason || "暂无法保存；请稍后重试，草稿已保留。");
      return;
    }
    setBlocked(""); setError(""); setNotice("");
    const blockedIssues = blockingIssues(base, current);
    if (blockedIssues.length) {
      setError(`${blockedIssues.length} 项无法保存：${blockedIssues.map(issue => issue.message).join(" ")}`);
      return;
    }
    setSaving(true);
    try {
      if (pendingSave.current) { await publish(pendingSave.current); return; }
      if (!draftDiffers(base, current)) return;
      if (snapshot.tableRevision !== current.tableRevision) {
        setError("设置已更新；请重新加载核对或放弃草稿。");
        return;
      }
      const payload = publication(base, current, crypto.randomUUID());
      pendingSave.current = payload;
      saveDispatched.current = false;
      saveAmbiguous.current = false;
      await publish(payload);
    } finally { if (alive.current) setSaving(false); }
  }

  async function discard() {
    if (busy) { setBlocked("正在保存；请等待结果。"); return; }
    if (confirming) { setBlocked("保存结果未知；请核对或重试同一保存。"); return; }
    closeDraft();
    setError(""); setNotice("草稿已放弃");
    await refresh();
  }

  async function reloadLatest() {
    if (busy || confirming) { setBlocked("保存未确认；请核对后重新加载。"); return; }
    setError(""); setNotice("");
    const next = withHistory(makeDraft(snapshot), historyRef.current);
    draftRef.current = next;
    setDraft(next);
    setBaselineValue(structuredClone(next));
    pendingRebase.current = null;
    setRebaseConflicts([]);
    pendingSave.current = null;
    saveDispatched.current = false;
    saveAmbiguous.current = false;
    setUncertain(false);
    setNotice(`已加载 V${next.tableRevision}；请核对后保存。`);
  }

  function adoptHistory(entries: HistoryEntries | null) {
    const usable = entries && entries.tableRevision === snapshot.tableRevision ? entries : null;
    historyRef.current = usable;
    setHistoryEntries(usable);
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || saving || confirming || uncertain) return;
    const pending = pendingRebase.current;
    if (pending && pending.tableRevision === snapshot.tableRevision) {
      const retried = rebaseDraft(current, base, pending, usable);
      if (retried.conflicts.length) {
        pendingRebase.current = retried.conflicts.some(issue => issue.kind === "unread") ? pending : null;
        setRebaseConflicts(retried.conflicts);
        return;
      }
      pendingRebase.current = null;
      setRebaseConflicts([]);
      draftRef.current = retried.draft;
      setDraft(retried.draft);
      setBaselineValue(retried.baseline);
      return;
    }
    const nextDraft = withHistory(current, retainedAdditions(current, base, usable));
    draftRef.current = nextDraft;
    setDraft(nextDraft);
    setBaselineValue(withHistory(base, usable));
  }

  function rebase(next: Snapshot) {
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || saving || confirming || uncertain) return;
    const adopted = rebaseDraft(current, base, next, historyRef.current);
    if (adopted.conflicts.length) {
      pendingRebase.current = adopted.conflicts.some(issue => issue.kind === "unread") ? next : null;
      setRebaseConflicts(adopted.conflicts);
      return;
    }
    pendingRebase.current = null;
    setRebaseConflicts([]);
    draftRef.current = adopted.draft;
    setDraft(adopted.draft);
    setBaselineValue(adopted.baseline);
  }

  return {
    mode: draft !== null, editing, sessionWritable, draft, view, setDraft, baseline,
    busy, setAuxiliaryBusy, uncertain, confirming, dirty, changeCount,
    configurationDirty, blocking, attention, conflict, rebaseConflicts,
    error, notice, blocked, saveBlockedReason,
    update, save, discard, reloadLatest, rebase, adoptHistory,
  };
}
export type Editor = ReturnType<typeof useEditor>;
