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
  startup: {
    connecting: "Connecting to the local blackboard",
    waiting: "Connecting…",
    failed: "Cannot reach the blackboard right now",
    reconnect: "Reconnect",
  },
} satisfies typeof zh;
