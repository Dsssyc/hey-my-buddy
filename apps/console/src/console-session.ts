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

/** Copy for a page whose session lost write authority to a newer window. */
export const READ_ONLY_BANNER =
  "新窗口已取得写权限，此页面现在是只读。你仍然可以浏览、筛选、查看任务详情、路由与评价历史；草稿和选择都保留在本页，不会被丢弃或重新加载。需要写入时，请从 Buddy 重新打开控制台入口。";

/**
 * Copy for a mutation control that a read-only session cannot use. It is also
 * the canonical `CONSOLE_READ_ONLY` error copy, so the local refusal and the
 * board's refusal read the same.
 */
export const READ_ONLY_ACTION_REFUSAL =
  "新窗口已取得写权限，此页面为只读：这项操作不会提交。草稿与浏览状态保留在本页。";

/** Copy for the save path; the draft stays local and is never discarded. */
export const READ_ONLY_SAVE_REFUSAL =
  "新窗口已取得写权限，此页面为只读：保存已停用。草稿仍保留在本页；请从 Buddy 重新打开控制台入口后再保存。";

/** In-panel reminder that a retained draft is visible but frozen. */
export const READ_ONLY_DRAFT_NOTE =
  "只读会话：草稿仍然可见、可复制，但不能修改或保存。";

/**
 * Copy for a page whose writes are paused because the authenticated poll failed
 * (network loss, expired session or an unreadable snapshot). This must never be
 * mixed up with the superseded/new-window message: a failed poll alone does not
 * prove another window took authority.
 */
export const CONNECTION_WRITE_PAUSED =
  "与本地黑板的连接已中断：任务与协作操作暂时不能提交；已输入的内容和草稿仍保留在本页。";

/**
 * Copy when a *definitely refused* read-only retry lands on a command whose
 * earlier attempt may still have committed. The refusal proves only that this
 * request was denied — not that the earlier same-ID attempt did not commit — so
 * the page keeps the command identity and the unknown result instead of
 * claiming success, failure or a fresh identity.
 */
export const UNRESOLVED_PUBLICATION_HANDOFF =
  "此前提交的保存结果尚未确认，该提交可能已经生效。本页为只读：不会重试、释放或覆盖它；草稿仍保留在本页。请从 Buddy 重新打开控制台入口，在新窗口中核对最新版本。";

/** The workflow-command counterpart of UNRESOLVED_PUBLICATION_HANDOFF. */
export const UNRESOLVED_COMMAND_HANDOFF =
  "此前提交的协作操作结果尚未确认，该操作可能已经生效。本页为只读：不会重试或覆盖它。请从 Buddy 重新打开控制台入口，在新窗口中核对最新记录。";

/** Compact banner reminder that a dispatched command still has an unknown result. */
export const UNRESOLVED_HANDOFF_NOTE =
  "本页有结果未确认的提交：它可能已经生效。请在新窗口取得写权限后先核对最新记录。";
