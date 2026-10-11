import type zh from "../zh/shell";

export default {
  skipToContent: "Skip to main content",
  nav: {
    label: "Main navigation",
    tasks: "Tasks",
    buddies: "Buddies",
    settings: "Settings",
    pendingHost: "Micro tasks waiting for their Host's decision",
    unsaved: "Unsaved changes",
  },
  gate: {
    draining: "Writes queued",
    exclusive: "Exclusive edit in progress",
  },
  snapshot: {
    verified: "Verified",
    unverified: "Verification time not recorded",
  },
  connection: {
    lost: "Connection lost",
    retry: "Retry",
  },
  draft: {
    label: "Unsaved changes",
    unsaved: "Unsaved: {{n}}",
    unresolved: "The result of the save awaits checking",
    save: "Save",
    saving: "Saving…",
    waiting: "Waiting for another save…",
    retry: "Retry the same save",
    discard: "Discard",
    cancelWait: "Stop waiting",
    waitingRefusal: "Waiting for another save to finish; you can stop waiting first.",
    conflict: "The settings have changed; check the draft.",
    reload: "Load the latest version",
    discardChanges: "Discard changes",
  },
  startup: {
    connecting: "Connecting to the local blackboard",
    waiting: "Connecting…",
    failed: "Cannot reach the blackboard right now",
    reconnect: "Reconnect",
  },
} satisfies typeof zh;
