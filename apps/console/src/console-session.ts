import type { Snapshot } from "./types";

/**
 * The POST command routes the board still accepts from a read-only session.
 * This list mirrors the server contract so the UI can explain why a control is
 * unavailable; it is deliberately narrow and is never a security boundary —
 * the server rejects every other operation for a superseded session.
 */
export const READ_ONLY_SESSION_OPERATIONS: readonly string[] = [
  "evaluation_history",
  "selection_get",
  "selection_list",
  "model_profiles",
  "workflow_get",
];

/** Client-side mirror of the server's read-only allow list. */
export function readOnlySessionAllows(operation: string): boolean {
  return READ_ONLY_SESSION_OPERATIONS.includes(operation);
}

/**
 * Whether this snapshot grants write authority. A missing or non-boolean
 * descriptor never counts as write access, so an unrecognized response cannot
 * enable a mutation control.
 */
export function sessionCanWrite(snapshot: Snapshot): boolean {
  return snapshot?.consoleSession?.canWrite === true;
}

/**
 * Session authority for one mounted page.
 *
 * A definite CONSOLE_READ_ONLY/CONSOLE_SESSION_EXPIRED refusal latches the loss
 * for that public session id: the refusal proves the board no longer accepts
 * this session's writes, and a later render that still carries an older
 * `canWrite:true` snapshot must not silently restore write access. Only a
 * genuinely different session id (a fresh CLI entry, normally a page reload)
 * can be writable again. The latch is deliberately not a security boundary;
 * the board enforces authority regardless of UI state.
 */
export function createAuthorityLatch() {
  let lost: string | null = null;
  return {
    /** Records the public session id whose write authority the board refused. */
    lose(id: string | null | undefined): void {
      if (typeof id === "string" && id.trim().length > 0) lost = id;
    },
    /** Live descriptor authority, with a latched loss overriding older snapshots. */
    writable(snapshot: Snapshot): boolean {
      if (!sessionCanWrite(snapshot)) return false;
      return lost === null || snapshot.consoleSession.id !== lost;
    },
  };
}
export type AuthorityLatch = ReturnType<typeof createAuthorityLatch>;

/* ---- 0.16 multi-window copy: no single writer, no handoff, no read-only degradation ---- */

/**
 * Copy for an expired or invalid login (cookie). The cookie applies to the
 * whole origin, so re-logging-in from any window restores this page's polling
 * without a reload; drafts stay right here.
 */
export const LOGIN_EXPIRED_BANNER =
  "登录已失效：在终端运行 buddy console 重新登录后，本页会自动恢复。草稿保留在本页。";

/** Copy for a mutation control an unauthenticated page cannot use. */
export const LOGIN_EXPIRED_ACTION_REFUSAL =
  "登录已失效，这项操作不会提交。草稿与浏览状态保留在本页；重新登录后即可恢复。";

/** Copy for the save path; the draft stays local and is never discarded. */
export const LOGIN_EXPIRED_SAVE_REFUSAL =
  "登录已失效，保存已暂停。草稿仍保留在本页；重新登录后再保存即可。";

/**
 * Copy when a save reply was lost: the result is unknown — it may have
 * committed — so the draft and its submission identity both stay retained for
 * inspection/replay. Never described as failure or rollback.
 */
export const UNRESOLVED_SAVE_NOTE =
  "保存结果未确认：可能已经生效。草稿和提交标识都保留。";

/**
 * Copy for a page whose writes are paused because the authenticated poll failed
 * (network loss or an unreadable snapshot). A failed poll alone says nothing
 * about login validity, so it never borrows the login-expired wording.
 */
export const CONNECTION_WRITE_PAUSED =
  "与本地黑板的连接已中断：任务与协作操作暂时不能提交；已输入的内容和草稿仍保留在本页。";
