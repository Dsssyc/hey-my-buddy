import i18n from "i18next";
import type { Snapshot } from "./types";

/**
 * Whether this snapshot carries a session. The API parser validates its id
 * when it crosses the HTTP boundary.
 */
export function sessionCanWrite(snapshot: Snapshot): boolean {
  return Boolean(snapshot?.consoleSession);
}

/**
 * Session authority for one mounted page.
 *
 * A definite `CONSOLE_SESSION_EXPIRED` (HTTP 401) refusal latches the loss
 * for that public session id: the refusal proves the board no longer accepts
 * this session's writes, and a later render that still carries an older
 * valid snapshot must not silently restore write access. Only a genuinely
 * different session id (a fresh login, normally a page reload) can be
 * writable again. The latch is deliberately not a security boundary; the
 * board enforces authority regardless of UI state.
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

/* Every authenticated window may edit. */

/** Copy for a mutation control an unauthenticated page cannot use. */
export const LOGIN_EXPIRED_ACTION_REFUSAL = () => i18n.t("console:yourLoginExpiredTheActionWasNot");

/** Copy for the save path; the draft stays local and is never discarded. */
export const LOGIN_EXPIRED_SAVE_REFUSAL = () => i18n.t("console:yourLoginExpiredLogInAgainBefore");

/**
 * Copy when a save reply was lost: the result is unknown — it may have
 * committed — so the draft and its submission identity both stay retained for
 * inspection/replay. Never described as failure or rollback.
 */
export const UNRESOLVED_SAVE_NOTE = () => i18n.t("console:theSaveResultIsUnknownAndMay");
