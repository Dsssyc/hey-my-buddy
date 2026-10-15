import type zh from "../zh/shell";

export default {
  actor: {
    currentOwner: "Current owner",
    unknownOrigin: "Unrecorded initiator", microTask: "Micro task {{id}}",
    events: {
      dispatch: "{{time}} {{role}} submitted “{{title}}”", approved: "{{time}} {{role}} approved the request for “{{title}}”",
      declined: "{{time}} {{role}} declined the request for “{{title}}”", decide: "{{time}} {{role}} decided the request for “{{title}}”",
      continue: "{{time}} {{role}} continued “{{title}}”", integrate: "{{time}} {{role}} integrated the output of “{{title}}”",
      accept: "{{time}} {{role}} accepted “{{title}}”", reject: "{{time}} {{role}} requested acceptance changes for “{{title}}”",
      stop: "{{time}} {{role}} stopped “{{title}}”", console: "{{time}} Stopped “{{title}}” in console",
      service: "{{time}} Service stopped “{{title}}”", unknown: "{{time}} Stopped “{{title}}” (initiator unknown)",
      takeover: "{{time}} {{role}} took over “{{title}}”", other: "{{time}} {{role}} {{action}} “{{title}}”",
    },
    unrecorded: "Not recorded", identity: "{{role}} · {{id}}", recordedBy: "Recorded by",
    markerIdentity: "{{sentence}}, recorded by {{actor}}", origin: "Initiating {{role}}", reason: "Stop reason",
    roles: { host: "Host", lead: "Lead", console: "Console", service: "Service", workflow: "Blackboard", bridge: "Bridge" },
    stop: { host: "Stopped by Host", lead: "Stopped by Lead", console: "Stopped in console", service: "Service stopped", unknown: "Stopped (initiator unknown)" },
  },
  skipToContent: "Skip to main content",
  nav: {
    label: "Main navigation",
    tasks: "Tasks",
    buddies: "Buddies",
    settings: "Settings",
    pendingHost: "Micro tasks waiting for their Host's decision",
    unsaved: "Unsaved changes",
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
    retry: "Retry the same save",
    discard: "Discard",
    unresolvedNote: "The result of the save is unknown and it may have taken effect; check before retrying.",
    unavailable: {
      connection: "The connection is lost; retry the connection. The draft is kept.",
      login: "The login has expired; run hey-my-buddy console to log in again. The draft is kept.",
    },
    reload: "Reread settings",
  },
  startup: {
    connecting: "Connecting to the local blackboard",
    waiting: "Connecting…",
    failed: "Cannot reach the blackboard right now",
    reconnect: "Reconnect",
  },
} satisfies typeof zh;
