import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import type { Dispatch, SetStateAction } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, uncertainResponse } from "./api";
import {
  changedProfileIds,
  configurationChanged as configurationDiffers,
  draftDiffers,
  historyView,
  makeDraft,
  publication,
  rebaseDraft,
  retainedAdditions,
  withHistory,
} from "./draft";
import type { HistoryEntries, RebaseConflict } from "./draft";
import { attentionIssues, blockingIssues } from "./policy";
import type { Draft, Snapshot, WriterGrant } from "./types";

/** Queue poll while another writer holds the table; each renew also extends the lease. */
export const QUEUE_POLL_MS = 750;
/** Lease keep-alive while this page still holds a known writer intent. */
export const LEASE_KEEPALIVE_MS = 10000;
/** A bounded wait: after this the draft and intent stay, and the user decides. */
const QUEUE_LIMIT_MS = 300000;
/**
 * Codes the board uses for a writer identity that is already terminal or gone.
 * They prove a renewal cannot continue and an abort has nothing left to release;
 * an authorization or validation refusal proves neither.
 */
const TERMINAL_GRANT_CODES = [
  "WRITER_NOT_ACTIVE",
  "WRITER_EXPIRED",
  "STALE_GENERATION",
  "NOT_FOUND",
];

type BeginIntent = { requestId: string; expectedRevision: number; kind: "human" };
/** One abort attempt whose exact command identity is reused on every retry. */
type AbortIntent = { grant: WriterGrant; commandId: string; createdAt: number };

const delay = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));
const isActive = (grant: WriterGrant) => grant.state === undefined || grant.state === "active";
const isTerminalGrant = (error: unknown) =>
  error instanceof ApiError && TERMINAL_GRANT_CODES.includes(error.code);
const isRevisionConflict = (error: unknown) =>
  error instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT"].includes(error.code);

export type EditorConflict = { basedOn: number; latest: number };

/**
 * One edit-mode switch for the model and routing pages.
 *
 * Turning the switch on is entirely local: it clones the published table into a
 * draft and never asks the board for a lease. Only "保存更改" takes the existing
 * evaluation_write_begin grant, waits for it if another writer is ahead, renews
 * it, and publishes the dirty human patches through `user_policy_publish`. A lost
 * reply keeps the same command/request identity so a retry can never republish a
 * different payload.
 *
 * Every intent that could still exist on the board is either resolved or
 * retained for retry: an unresolved begin is re-sent with its original request
 * ID before the page exits, discards or reloads, and an abort whose reply was
 * lost is never reported as a confirmed release.
 */
export function useEditor(
  api: ConsoleApi,
  snapshot: Snapshot,
  refresh: () => Promise<Snapshot | null>,
  mutationsAvailable: boolean,
  unavailableReason: string,
) {
  const [mode, setMode] = useState(false);
  const [draft, setDraftState] = useState<Draft | null>(null);
  const [baseline, setBaseline] = useState<Draft | null>(null);
  const [historyEntries, setHistoryEntries] = useState<HistoryEntries | null>(null);
  const [rebaseConflicts, setRebaseConflicts] = useState<RebaseConflict[]>([]);
  const [grant, setGrantState] = useState<WriterGrant | null>(null);
  const [saving, setSaving] = useState(false);
  const [auxiliaryBusy, setAuxiliaryBusy] = useState(false);
  const [waiting, setWaiting] = useState(false);
  const [error, setError] = useState("");
  const [blocked, setBlocked] = useState("");
  const [notice, setNotice] = useState("");
  const [uncertain, setUncertain] = useState(false);
  const [confirming, setConfirming] = useState(false);
  const [exitPrompt, setExitPrompt] = useState(false);

  const csrf = useRef(snapshot.csrfToken);
  csrf.current = snapshot.csrfToken;
  const alive = useRef(true);
  const grantRef = useRef<WriterGrant | null>(null);
  const baselineRef = useRef<Draft | null>(null);
  /** Retained profiles loaded from `model_profiles`; merged into the local view. */
  const historyRef = useRef<HistoryEntries | null>(null);
  /** A program publication whose merge waits for fresh retained-row data. */
  const pendingRebase = useRef<Snapshot | null>(null);
  const draftRef = useRef<Draft | null>(null);
  /** A begin whose reply was lost; kept so a retry reuses the same request ID. */
  const pendingBegin = useRef<BeginIntent | null>(null);
  const pendingSave = useRef<ReturnType<typeof publication> | null>(null);
  /** An abort whose reply was lost or refused; its identity stays retryable. */
  const unresolvedAbort = useRef<AbortIntent | null>(null);
  const cancelRequested = useRef(false);

  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const setGrant = useCallback((value: WriterGrant | null) => {
    grantRef.current = value;
    setGrantState(value);
  }, []);
  const setBaselineValue = useCallback((value: Draft | null) => {
    baselineRef.current = value;
    setBaseline(value);
  }, []);
  const setDraft: Dispatch<SetStateAction<Draft | null>> = useCallback((update) => {
    setDraftState((previous) => {
      const next = typeof update === "function"
        ? (update as (value: Draft | null) => Draft | null)(previous)
        : update;
      draftRef.current = next;
      return next;
    });
  }, []);

  const dirty = useMemo(
    () => (baseline && draft ? draftDiffers(baseline, draft) : false),
    [baseline, draft],
  );
  const changedProfiles = useMemo(
    () => (baseline && draft ? changedProfileIds(baseline, draft) : []),
    [baseline, draft],
  );
  const configurationDirty = useMemo(
    () => (baseline && draft ? configurationDiffers(baseline, draft) : false),
    [baseline, draft],
  );
  // One console view: published/loaded program facts, retained history rows and
  // the local human draft when editing. Cards, samples and evidence stay taken
  // from the recorded snapshot/history, never from the draft.
  const view: Snapshot = useMemo(() => {
    const recorded = historyView(snapshot, historyEntries);
    return draft
      ? {
          ...recorded,
          profiles: draft.profiles,
          preferences: draft.preferences,
          annotations: draft.annotations,
          configuration: draft.configuration,
        }
      : recorded;
  }, [snapshot, draft, historyEntries]);
  // A new enable/pin/selector needs a currently legal model; unrelated patches
  // never wait for an old stale pin or decision setting to be repaired.
  const blocking = useMemo(
    () => (baseline && draft ? blockingIssues(baseline, draft) : []),
    [baseline, draft],
  );
  const attention = useMemo(
    () => attentionIssues(view),
    [view],
  );
  // A newer publication is only a conflict once no reply is still pending: after a
  // lost publish reply the revision may have advanced because of *this* draft.
  const conflict: EditorConflict | null = useMemo(
    () => (draft && !confirming && snapshot.tableRevision !== draft.tableRevision
      ? { basedOn: draft.tableRevision, latest: snapshot.tableRevision }
      : null),
    [draft, confirming, snapshot.tableRevision],
  );

  // The draft is frozen while a save, discovery, discard or reload is in flight
  // and while a staged publication waits for confirmation, so a payload can
  // never be edited out from under its own retry.
  const busy = saving || auxiliaryBusy;
  const editing = mode && !busy && !confirming;
  const saveBlockedReason = !mutationsAvailable && mode
    ? unavailableReason || "当前无法提交保存，草稿会保留在本页。"
    : "";

  // Keep a *known* writer intent alive. Once an outcome is unknown the page stops
  // renewing an idle writer, so an ambiguous operation cannot hold the table
  // indefinitely; the short lease expires and the user resolves it deliberately.
  useEffect(() => {
    if (!grant || busy || uncertain) return;
    const timer = setInterval(async () => {
      const owner = grantRef.current;
      if (!owner || !alive.current) return;
      try {
        const next = await api.command<Partial<WriterGrant>>(
          "evaluation_write_renew",
          { writerId: owner.writerId, generation: owner.generation, writerToken: owner.writerToken },
          csrf.current,
        );
        if (!alive.current || grantRef.current?.writerId !== owner.writerId) return;
        const merged = { ...owner, ...next };
        grantRef.current = merged;
        setGrantState(merged);
        setUncertain(false);
      } catch (failure) {
        if (!alive.current) return;
        if (isTerminalGrant(failure)) {
          setGrant(null);
          setUncertain(false);
          setNotice("编辑资格已过期；再次保存会重新申请，不会重复发布。");
        } else if (uncertainResponse(failure)) {
          setUncertain(true);
          setError("尚未确认编辑资格状态；再次保存会复用同一编辑资格。");
        } else {
          // A refused renewal says nothing about the lease: release it, and keep
          // the identity when even the release cannot be confirmed.
          const released = await release(owner);
          if (!alive.current) return;
          setGrant(null);
          setUncertain(!released);
          setError(released
            ? errorText(failure)
            : `${errorText(failure)} 未能确认释放结果，下次保存前会重试。`);
        }
      }
    }, LEASE_KEEPALIVE_MS);
    return () => clearInterval(timer);
  }, [api, grant?.writerId, busy, uncertain, setGrant]);

  /**
   * Drops the local draft. `preserveIntent` keeps a possible server-side grant
   * and its recovery state when a release outcome is still unknown.
   */
  function closeDraft(preserveIntent = false) {
    setMode(false);
    setDraft(null);
    setBaselineValue(null);
    draftRef.current = null;
    setConfirming(false);
    setWaiting(false);
    setBlocked("");
    setExitPrompt(false);
    cancelRequested.current = false;
    pendingSave.current = null;
    pendingRebase.current = null;
    setRebaseConflicts([]);
    if (!preserveIntent) {
      pendingBegin.current = null;
      unresolvedAbort.current = null;
      setGrant(null);
      setUncertain(false);
    }
  }

  /** Local only: no begin, no renew, no model request. */
  function enter() {
    setBlocked("");
    if (busy) {
      setBlocked("正在保存或读取目录。请等待结束后再进入编辑模式。");
      return;
    }
    setNotice("");
    if (!draftRef.current) {
      // Retained history rows join the draft from the start, so disabling a
      // retired configuration is an ordinary local edit with a recorded baseline.
      const next = withHistory(makeDraft(snapshot), historyRef.current);
      draftRef.current = next;
      setDraft(next);
      setBaselineValue(structuredClone(next));
    }
    setMode(true);
  }

  /**
   * Sends one begin intent. A definite reply clears the uncertainty; an unknown
   * one keeps the request ID so a retry cannot take a second writer intent.
   */
  async function begin(intent: BeginIntent): Promise<WriterGrant | null> {
    try {
      const next = await api.command<WriterGrant>("evaluation_write_begin", intent, csrf.current);
      if (!alive.current) return null;
      pendingBegin.current = null;
      setGrant(next);
      setUncertain(false);
      setError("");
      return next;
    } catch (failure) {
      if (!alive.current) return null;
      if (uncertainResponse(failure)) {
        // The board may have created a grant for this exact request. Keep the
        // request ID and recovery state instead of pretending it never happened.
        setUncertain(true);
        setError("尚未确认编辑资格请求的结果；再次保存会复用同一请求 ID，不会重复占用编辑资格。");
        return null;
      }
      pendingBegin.current = null;
      setUncertain(false);
      setError(errorText(failure));
      if (isRevisionConflict(failure)) await refresh();
      return null;
    }
  }

  /** True once the last known lease window passed: no grant can still be live. */
  function leaseExpired(intent: AbortIntent): boolean {
    const expiry = Date.parse(intent.grant.expiresAt);
    if (Number.isFinite(expiry)) return Date.now() > expiry;
    // A payload-only identity carries no expiry; the longest bounded queue wait
    // is still a conservative ceiling for a short writer lease.
    return Date.now() > intent.createdAt + QUEUE_LIMIT_MS;
  }

  /**
   * Sends one abort intent. Only a success or a terminal-writer code proves the
   * release; a lost *or refused* request proves nothing and is retained.
   */
  async function sendAbort(intent: AbortIntent): Promise<boolean> {
    try {
      await api.command(
        "evaluation_write_abort",
        {
          commandId: intent.commandId,
          writerId: intent.grant.writerId,
          generation: intent.grant.generation,
          writerToken: intent.grant.writerToken,
        },
        csrf.current,
      );
      return true;
    } catch (failure) {
      return isTerminalGrant(failure);
    }
  }

  /**
   * Releases one grant. False means the outcome is unknown: the exact intent,
   * including its command ID, stays in recovery state for a later retry.
   */
  async function release(owner: WriterGrant | null): Promise<boolean> {
    if (!owner) return true;
    const intent: AbortIntent = { grant: owner, commandId: crypto.randomUUID(), createdAt: Date.now() };
    const released = await sendAbort(intent);
    if (!alive.current) return false;
    if (released) {
      if (unresolvedAbort.current?.commandId === intent.commandId) unresolvedAbort.current = null;
      return true;
    }
    unresolvedAbort.current = intent;
    return false;
  }

  /** Retries a retained abort with its original command ID. */
  async function flushUnresolvedAbort(): Promise<boolean> {
    const intent = unresolvedAbort.current;
    if (!intent) return true;
    if (leaseExpired(intent)) {
      // The short lease passed its own expiry, so the intent cannot still exist;
      // dropping it is bounded recovery, not a confirmed release claim.
      unresolvedAbort.current = null;
      return true;
    }
    const released = await sendAbort(intent);
    if (!alive.current) return false;
    if (!released) return false;
    unresolvedAbort.current = null;
    return true;
  }

  /**
   * Resolves every writer intent this page may still hold: a lost abort, an
   * unresolved begin (re-sent with its original request ID) and a known grant.
   * False means at least one outcome is unknown and its recovery state is kept.
   */
  async function releaseIntents(): Promise<boolean> {
    if (!(await flushUnresolvedAbort())) return false;
    if (!grantRef.current && pendingBegin.current) {
      await begin(pendingBegin.current);
      if (!alive.current) return false;
      if (pendingBegin.current) return false;
    }
    const owner = grantRef.current;
    if (!owner) return true;
    const released = await release(owner);
    if (!alive.current) return false;
    setGrant(null);
    return released;
  }

  async function requestExit() {
    setBlocked("");
    if (busy) {
      setBlocked(waiting
        ? "正在等待编辑资格。请先取消等待，或等待保存结束。"
        : "正在保存。请等待保存结束；结果未确认前不会丢弃草稿。");
      return;
    }
    if (confirming) {
      setBlocked("保存结果尚未确认。请先点击“确认保存结果”，再决定是否退出编辑模式。");
      return;
    }
    if (!dirty) {
      // Nothing to lose, but every intent this page may hold is resolved first.
      let released = true;
      if (grantRef.current || pendingBegin.current || unresolvedAbort.current) {
        setSaving(true);
        try {
          released = await releaseIntents();
        } finally {
          if (alive.current) setSaving(false);
        }
        if (!alive.current) return;
      }
      closeDraft(!released);
      if (!released) {
        setUncertain(true);
        setError("退出前未能确认编辑资格已释放；该资格会自动过期，下次保存前会重试释放。");
      }
      return;
    }
    setExitPrompt(true);
  }

  function keepEditing() {
    setExitPrompt(false);
  }

  async function acquire(current: Draft): Promise<WriterGrant | null> {
    // A release whose reply was lost must resolve before a second writer
    // identity is requested, so one page can never hold two intents.
    if (!(await flushUnresolvedAbort())) {
      if (!alive.current) return null;
      setUncertain(true);
      setError("尚未确认上一次编辑资格的释放结果；请稍后重试保存。");
      return null;
    }
    const existing = grantRef.current;
    if (existing && !pendingSave.current && existing.tableRevision === current.tableRevision) {
      return existing;
    }
    // A superseded intent (for example after a renewal reported a newer table
    // revision) must not stay queued or active behind the new one.
    if (existing) {
      const released = await release(existing);
      if (!alive.current) return null;
      if (!released) {
        setGrant(null);
        setUncertain(true);
        setError("尚未确认编辑资格的释放结果；请稍后重试保存。");
        return null;
      }
      setGrant(null);
    }
    const intent = pendingBegin.current ?? {
      requestId: crypto.randomUUID(),
      expectedRevision: current.tableRevision,
      kind: "human" as const,
    };
    pendingBegin.current = intent;
    return begin(intent);
  }

  /** Waits behind admitted readers, renewing the queued intent until it may write. */
  async function ensureActive(owner: WriterGrant): Promise<WriterGrant | null> {
    if (isActive(owner)) return owner;
    cancelRequested.current = false;
    setWaiting(true);
    setNotice(owner.queuePosition && owner.queuePosition > 1
      ? `正在等待编辑资格（前面还有 ${owner.queuePosition - 1} 位）…`
      : "正在等待编辑资格…");
    const deadline = Date.now() + QUEUE_LIMIT_MS;
    let current = owner;
    try {
      for (;;) {
        // A cancellation or deadline reached while a renew was in flight still
        // releases the intent before anything can be published.
        if (cancelRequested.current) return await cancelQueuedWrite(current);
        if (isActive(current)) break;
        if (Date.now() > deadline) {
          const released = await release(current);
          if (!alive.current) return null;
          setGrant(null);
          if (released) {
            setUncertain(false);
            setError("等待编辑资格超时，已释放排队中的编辑资格。草稿保持不变，可以再次保存。");
          } else {
            setUncertain(true);
            setError("等待编辑资格超时，且未能确认释放结果；草稿保持不变，可以再次保存。");
          }
          return null;
        }
        await delay(QUEUE_POLL_MS);
        if (!alive.current) return null;
        if (cancelRequested.current) return await cancelQueuedWrite(current);
        let next: Partial<WriterGrant>;
        try {
          next = await api.command<Partial<WriterGrant>>(
            "evaluation_write_renew",
            { writerId: current.writerId, generation: current.generation, writerToken: current.writerToken },
            csrf.current,
          );
        } catch (failure) {
          if (!alive.current) return null;
          if (uncertainResponse(failure)) {
            // Keep the identity for a retry and stop renewing: recovery is bounded
            // instead of holding a writer this page can no longer confirm.
            setUncertain(true);
            setError("尚未确认编辑资格状态；再次保存会复用同一编辑资格。");
            return null;
          }
          const released = await release(current);
          if (!alive.current) return null;
          setGrant(null);
          if (released) {
            setUncertain(false);
            setError(errorText(failure));
          } else {
            setUncertain(true);
            setError(`${errorText(failure)} 未能确认释放结果，下次保存前会重试。`);
          }
          return null;
        }
        if (!alive.current) return null;
        current = { ...current, ...next };
        grantRef.current = current;
        setGrantState(current);
        setUncertain(false);
        setError("");
      }
      setNotice("");
      return current;
    } finally {
      if (alive.current) setWaiting(false);
    }
  }

  async function cancelQueuedWrite(owner: WriterGrant): Promise<null> {
    if (!alive.current) return null;
    const released = await release(owner);
    if (!alive.current) return null;
    if (released) {
      setGrant(null);
      setUncertain(false);
      setNotice("已取消等待，编辑资格已释放；草稿保持不变。");
    } else {
      // Never claim a release the board did not confirm.
      setGrant(null);
      setUncertain(true);
      setError("未能确认编辑资格已释放；该资格会自动过期，草稿保持不变。");
    }
    return null;
  }

  async function publish(payload: ReturnType<typeof publication>) {
    try {
      await api.command("user_policy_publish", payload, csrf.current);
      if (!alive.current) return;
      pendingSave.current = null;
      pendingBegin.current = null;
      unresolvedAbort.current = null;
      setGrant(null);
      setUncertain(false);
      setConfirming(false);
      setNotice("已发布新版本。正在执行的任务继续使用原配置。");
      closeDraft();
      await refresh();
    } catch (failure) {
      if (!alive.current) return;
      if (uncertainResponse(failure)) {
        // The payload and its commandId stay staged: a retry resolves the same
        // command instead of publishing a second revision. Idle renewals stop so
        // an unknown outcome cannot hold the table indefinitely.
        setUncertain(true);
        setConfirming(true);
        setError("尚未确认保存结果。再次点击“确认保存结果”会复用同一请求，不会重复发布；草稿保持原样。");
        return;
      }
      pendingSave.current = null;
      setConfirming(false);
      setUncertain(false);
      // Release the intent the payload names even if the local mirror was dropped:
      // a definitive failure must not leave a queued or active lease behind.
      const staged = grantRef.current ?? {
        writerId: payload.writerId,
        generation: payload.generation,
        writerToken: payload.writerToken,
      } as WriterGrant;
      const released = await release(staged);
      if (!alive.current) return;
      setGrant(null);
      if (!released) setUncertain(true);
      if (isRevisionConflict(failure)) {
        await refresh();
        setError("共享评价表已发布新版本，你的草稿仍保留。请选择重新加载最新版本，或放弃修改。");
        return;
      }
      setError(errorText(failure));
    }
  }

  async function save() {
    const current = draftRef.current;
    const base = baseline;
    if (!current || !base || busy) {
      if (busy) setBlocked("正在读取模型目录或保存中，请稍后再试；草稿不会丢失。");
      return;
    }
    if (!mutationsAvailable) {
      setBlocked(unavailableReason || "当前无法提交保存；草稿会保留在本页。");
      return;
    }
    setBlocked("");
    setError("");
    setNotice("");
    // A blocked patch (a new enable, pin or selector for an unavailable model)
    // is refused locally with the exact reason, so no other patch is published
    // against a payload the board would reject as a whole. The check reads the
    // live draft instead of a possibly older render memo.
    const blocked = blockingIssues(base, current);
    if (blocked.length) {
      setError(`有 ${blocked.length} 项修改当前不能提交：${blocked.map((issue) => issue.message).join(" ")}`);
      return;
    }
    setSaving(true);
    try {
      if (pendingSave.current) {
        // The staged payload carries its own writer identity, so a lost reply can
        // still be resolved even if the local lease mirror expired.
        await publish(pendingSave.current);
        return;
      }
      if (!draftDiffers(base, current)) {
        // Publishing an empty patch would advance the revision for nothing.
        setNotice("没有需要保存的用户修改。");
        return;
      }
      if (snapshot.tableRevision !== current.tableRevision) {
        setError(`共享评价表已发布 V${snapshot.tableRevision}，你的草稿基于 V${current.tableRevision}。草稿仍保留：请选择重新加载最新版本，或放弃修改。`);
        return;
      }
      const owner = await acquire(current);
      if (!owner) return;
      const ready = await ensureActive(owner);
      if (!ready) return;
      const payload = publication(base, current, ready, crypto.randomUUID());
      pendingSave.current = payload;
      await publish(payload);
    } finally {
      if (alive.current) setSaving(false);
    }
  }

  async function saveAndExit() {
    setExitPrompt(false);
    await save();
  }

  async function discard() {
    if (busy) {
      setBlocked(waiting ? "正在等待编辑资格，请先取消等待。" : "正在保存，请等待结果；草稿不会被静默丢弃。");
      return;
    }
    if (confirming) {
      setBlocked("保存结果尚未确认。请先确认保存结果，再决定是否放弃草稿。");
      return;
    }
    setExitPrompt(false);
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const released = await releaseIntents();
      if (!alive.current) return;
      closeDraft(!released);
      if (released) setNotice("已放弃未发布的修改。");
      else {
        setUncertain(true);
        setError("草稿已放弃；编辑资格的释放结果尚未确认，会自动过期。");
      }
      await refresh();
    } finally {
      if (alive.current) setSaving(false);
    }
  }

  /** Deliberate reload: the draft is replaced by the latest published revision. */
  async function reloadLatest() {
    if (busy || confirming) {
      setBlocked("保存结果尚未确认，或保存仍在进行；暂不能重新加载。");
      return;
    }
    setSaving(true);
    setError("");
    setNotice("");
    try {
      if (grantRef.current || pendingBegin.current || unresolvedAbort.current) {
        const released = await releaseIntents();
        if (!alive.current) return;
        if (!released) {
          setUncertain(true);
          setError("尚未确认编辑资格已释放，暂不能重新加载最新版本；请稍后重试。");
          return;
        }
      }
      const next = withHistory(makeDraft(snapshot), historyRef.current);
      draftRef.current = next;
      setDraft(next);
      setBaselineValue(structuredClone(next));
      pendingBegin.current = null;
      pendingSave.current = null;
      unresolvedAbort.current = null;
      pendingRebase.current = null;
      setRebaseConflicts([]);
      setGrant(null);
      setUncertain(false);
      setNotice(`已加载评价表 V${next.tableRevision}。请核对后重新保存。`);
    } finally {
      if (alive.current) setSaving(false);
    }
  }

  /** Cancels a queued wait; the loop itself releases the intent exactly once. */
  function cancelSave() {
    if (!busy || !waiting) return;
    cancelRequested.current = true;
    setNotice("正在取消等待…");
  }

  /**
   * Adopts retained profile rows loaded from `model_profiles`. They become part
   * of the local view immediately, and of the draft and its baseline when a
   * draft exists, so a disable or an opinion on a retired configuration stays a
   * normal diffed patch instead of an untracked insertion.
   *
   * Rows are only trusted at the revision they were read at: a page that no
   * longer matches the published table is dropped before it can seed a view or
   * a baseline. A deferred rebase retries here, because this is the moment the
   * missing fresh row data actually arrives.
   */
  function adoptHistory(entries: HistoryEntries | null) {
    const usable = entries && entries.tableRevision === snapshot.tableRevision ? entries : null;
    historyRef.current = usable;
    setHistoryEntries(usable);
    const current = draftRef.current;
    const base = baselineRef.current;
    if (!current || !base || saving || confirming || uncertain) return;
    if (grantRef.current || pendingBegin.current || unresolvedAbort.current) return;
    const pending = pendingRebase.current;
    if (pending && pending.tableRevision === snapshot.tableRevision) {
      const retried = rebaseDraft(current, base, pending, usable);
      if (retried.conflicts.length) {
        // A missing-row disagreement can still be resolved by fresh data; a real
        // competing value needs the user's deliberate reload or discard.
        pendingRebase.current = retried.conflicts.some((issue) => issue.kind === "unread")
          ? pending
          : null;
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

  /**
   * Adopts a freshly published snapshot (this page's own program directory
   * discovery, or another program publication). Directory facts come only from
   * the board; dirty human fields are replayed field by field. A field another
   * writer changed, or one whose fresh row is not readable yet, keeps the
   * original draft and its expectedRevision and is reported as a conflict.
   */
  function rebase(next: Snapshot) {
    const current = draftRef.current;
    // The ref is authoritative: a retained page adopted while `discover` was
    // awaiting the refresh must not be compared away by an older render value.
    const base = baselineRef.current;
    if (!current || !base) return;
    if (saving || confirming || uncertain) return;
    if (grantRef.current || pendingBegin.current || unresolvedAbort.current) return;
    const adopted = rebaseDraft(current, base, next, historyRef.current);
    if (adopted.conflicts.length) {
      pendingRebase.current = adopted.conflicts.some((issue) => issue.kind === "unread") ? next : null;
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
    mode,
    editing,
    draft,
    view,
    setDraft,
    baseline,
    grant,
    busy,
    setAuxiliaryBusy,
    waiting,
    uncertain,
    confirming,
    dirty,
    changedProfiles,
    configurationDirty,
    blocking,
    attention,
    conflict,
    rebaseConflicts,
    error,
    notice,
    blocked,
    exitPrompt,
    saveBlockedReason,
    enter,
    requestExit,
    keepEditing,
    save,
    saveAndExit,
    discard,
    reloadLatest,
    rebase,
    adoptHistory,
    cancelSave,
  };
}
export type Editor = ReturnType<typeof useEditor>;
