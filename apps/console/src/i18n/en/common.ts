import type zh from "../zh/common";

export default {
  emptyMark: "+",
  error: {
    codes: {
      REVISION_CONFLICT: "The record was updated; refresh, check it and try again.", CONFLICT: "The records conflict; refresh, check them and try again.",
      FORBIDDEN: "You may not do this; reopen the console.", STALE_GENERATION: "The right to act is out of date; refresh and check who owns it.",
      SHUTDOWN_UNCONFIRMED: "The stop is not confirmed; check again later, then retry.", STORAGE_INCOMPLETE: "The cleanup is not complete; retry the same request.",
      PLAN_EXPIRED: "The plan has expired; check again.", CONSOLE_SESSION_EXPIRED: "Browser authorization has expired; run hey-my-buddy console to authorize this browser again.",
      CONSOLE_ENTRY_EXPIRED: "The entry has expired; reopen the console.",
    },
    session: "The session information is not recognised; check the service version or authorize this browser again.", access: "The console access settings are not recognised; refresh and retry.",
    reminders: "The reminder settings are not recognised; refresh and retry.", storagePlan: "The storage plan reply is incomplete; check the service version.",
    version: "The version information is not recognised; check the service version.", network: "Cannot reach the local blackboard; refresh and retry.",
    unparsable: "The local service sent a reply that cannot be read.", snapshot: "The console data is incomplete; check the service version.",
    submit: "The result of the submission is unknown; check it, then retry.", history: "The micro task history is incomplete; check the service version.",
    macroTasks: "The macro task list is incomplete; check the service version.", timeline: "The macro task timeline is incomplete; check the service version.",
    cleanup: "The result of the cleanup is unknown; retry the same request.", recheck: "The result of the check is incomplete; check the service version.",
    pathSaved: "The result of saving the path is unknown; check the service version.", refused: "The request did not succeed.", failed: "The request failed; try again later.",
  },
  snapshot: {
    verified: "Verified",
    unverified: "Verification time not recorded",
  },
  unrecorded: "Not recorded",
  status: {
    queued: "Queued", running: "Running", cancelling: "Cancelling",
    completed: "Completed", cancelled: "Cancelled", failed: "Failed",
    "reconciliation-needed": "Awaiting reconciliation", "waiting-assistance": "Waiting for assistance",
    "waiting-decision": "Waiting for decision", "waiting-host": "Waiting for Host",
    "awaiting-host": "Waiting for Host", "awaiting-lead": "Waiting for Lead",
    "waiting-owned": "Waiting for owned micro tasks", "waiting-helpers": "Helpers running",
    executing: "Executing", delivered: "Awaiting acceptance", accepted: "Accepted",
    concluded: "Ended", yielded: "Turn ended",
  },
} satisfies typeof zh;
