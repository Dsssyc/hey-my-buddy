import type zh from "../zh/common";

export default {
  concurrencyNotice: {
    title: "Before running several sessions at once",
    body: "Running more sessions at once uses more of your quota and requests at the same time. Some subscriptions are meant for one person's ordinary use; going beyond their terms or suggested concurrency can lead to rate limits, suspension or effects on your account. Check the terms of the harnesses and subscriptions you use before raising the limit.",
  },
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
    submit: "The result of the submission is unknown; check it, then retry.",
    macroTasks: "The macro task list is incomplete; check the service version.", timeline: "This macro task's record is incomplete; check the service version.",
    cleanup: "The result of the cleanup is unknown; retry the same request.", recheck: "The result of the check is incomplete; check the service version.",
    pathSaved: "The result of saving the path is unknown; check the service version.", refused: "The request did not succeed.", failed: "The request failed; try again later.",
  },
  snapshot: {
    verified: "Verified",
    unverified: "Verification time not recorded",
  },
} satisfies typeof zh;
