import type zh from "../zh/shell";

export default {
  maintenanceValidation: {
    invalidJson: {
      single: "invalid JSON in the maintenance answer",
      multiple: "invalid JSON in {{total}} places in the maintenance answer",
    },
    citationOutsideBatch: {
      single: "a summary cites a micro task outside this batch",
      multiple: "{{total}} citations point to micro tasks outside this batch",
    },
    citationOutsideEvidence: {
      single: "a summary cites a micro task outside its frozen evidence",
      multiple: "{{total}} summaries cite micro tasks outside their frozen evidence",
    },
    unknown: {
      single: "the maintenance answer failed validation",
      multiple: "unrecognized validation problems at {{total}} targets",
    },
    wrongType: {
      single: "a field in the answer has the wrong type",
      multiple: "wrong field types at {{total}} locations in the answer",
    },
    invalidValue: {
      single: "the answer contains a disallowed value",
      multiple: "disallowed values at {{total}} locations in the answer",
    },
    requiredField: {
      single: "the answer is missing a required field",
      multiple: "{{total}} required fields are missing from the answer",
    },
    unexpectedField: {
      single: "the answer contains an unexpected field",
      multiple: "the answer contains {{total}} unexpected fields",
    },
    textTooShort: {
      single: "a text field in the answer is empty",
      multiple: "{{total}} text fields in the answer are empty",
    },
    textTooLong: {
      single: "a text field in the answer is too long",
      multiple: "{{total}} text fields in the answer are too long",
    },
    tooManyItems: {
      single: "the answer contains too many items",
      multiple: "too many items at {{total}} locations in the answer",
    },
    invalidUnicode: {
      single: "the answer contains invalid Unicode text",
      multiple: "{{total}} entries contain invalid Unicode text",
    },
    stagedConflict: {
      single: "an entry conflicts with staged content",
      multiple: "{{total}} entries conflict with staged content",
    },
    duplicateNode: {
      single: "a new domain node ID is duplicated",
      multiple: "{{total}} new domain node IDs are duplicated",
    },
    blankNode: {
      single: "a new domain node has a blank name or definition",
      multiple: "{{total}} new domain nodes have a blank name or definition",
    },
    changedNode: {
      single: "a correction attempts to change an existing domain node",
      multiple: "corrections attempt to change {{total}} existing domain nodes",
    },
    nulNode: {
      single: "a domain node field contains a null character",
      multiple: "{{total}} domain nodes have fields that contain null characters",
    },
    inactiveParent: {
      single: "a domain node has an inactive parent",
      multiple: "{{total}} tree edits involve inactive parent nodes",
    },
    parentCycle: {
      single: "domain node parents form a cycle",
      multiple: "{{total}} tree edits would create cycles in the parent relationships",
    },
    duplicateName: {
      single: "active domain node names are duplicated",
      multiple: "{{total}} tree edits would duplicate active domain node names",
    },
    duplicateMerge: {
      single: "a domain node is merged more than once",
      multiple: "{{total}} domain nodes are merged more than once",
    },
    inactiveMergeSource: {
      single: "the source domain node for a merge is inactive",
      multiple: "{{total}} merges have inactive source nodes",
    },
    inactiveMergeTarget: {
      single: "the target domain node for a merge is inactive",
      multiple: "{{total}} merges have inactive target nodes",
    },
    duplicateClassification: {
      single: "a micro task is classified more than once",
      multiple: "{{total}} micro tasks are classified more than once",
    },
    inactiveClassificationNode: {
      single: "a classification points to an inactive domain node",
      multiple: "classifications for {{total}} micro tasks point to inactive domain nodes",
    },
    unknownRun: {
      single: "a classification points to an unknown micro task",
      multiple: "classifications point to {{total}} unknown micro tasks",
    },
    missingClassification: {
      single: "a micro task is missing a classification",
      multiple: "{{total}} micro tasks are missing classifications",
    },
    unreadTaskSection: {
      single: "a task section was not read in full",
      multiple: "task sections were not read in full for {{total}} micro tasks",
    },
    duplicateSummary: {
      single: "a buddy kind has more than one summary",
      multiple: "{{total}} buddy kinds have more than one summary each",
    },
    unknownKind: {
      single: "a summary refers to an unknown buddy kind",
      multiple: "{{total}} summaries refer to unknown buddy kinds",
    },
    missingSummary: {
      single: "a summary is missing",
      multiple: "{{total}} summaries are missing",
    },
    unreadSummarySections: {
      single: "a summary's required micro task sections were not read in full",
      multiple: "required micro task reading was incomplete for {{total}} summaries",
    },
    summaryTooLong: {
      single: "a summary exceeds the byte limit",
      multiple: "{{total}} summaries exceed the byte limit",
    },
    duplicateCitation: {
      single: "a summary cites the same micro task more than once",
      multiple: "{{total}} summaries contain duplicate citations",
    },
    deletedCitation: {
      single: "a summary cites a deleted micro task",
      multiple: "{{total}} summaries cite deleted micro tasks",
    },
  },
  reasonNoProcesses: "none",
  reasonProcess: "{{executable}} ({{pid}})",
  reasonFact: "{{name}}: {{value}}",
  reasonParam: {
    "errors": "Problems",
    "access": "Access",
    "adapter": "Execution adapter",
    "bound": "Bound",
    "buddy": "Buddy",
    "buddyId": "Buddy ID",
    "bytes": "Bytes",
    "catalogReadAt": "Catalog read",
    "cause": "Cause",
    "collection": "Collection",
    "conflictIds": "Conflicts",
    "continuationId": "Continuation",
    "count": "Count",
    "disposition": "Disposition",
    "enabled": "Enabled",
    "errorCode": "Error code",
    "harness": "Harness",
    "kind": "Kind",
    "leadRunId": "Lead micro task",
    "legalEfforts": "Legal reasoning efforts",
    "limit": "Limit",
    "missingCapabilities": "Missing capabilities",
    "operation": "Operation",
    "paths": "Paths",
    "recordId": "Record",
    "requiredCapability": "Required capability",
    "retryAt": "Retry time",
    "shape": "Shape",
    "total": "Total",
    "unfinished": "Unfinished micro tasks"
  },
  reasonValue: {
    "violation": {
      "type": "must be text",
      "empty": "cannot be empty",
      "length": "can have at most 64 characters",
      "format": "cannot contain “ / ”, square brackets or control characters"
    },
    "yes": "Yes",
    "no": "No",
    "operation": {
      "probe": "Check",
      "account-read": "Account read"
    },
    "bound": {
      "disabled": "Disabled",
      "unavailable": "Unavailable",
      "excluded": "Excluded",
      "capability-missing": "Capability missing",
      "identity-changed": "Identity changed",
      "constraints": "Constraints not met",
      "not-pinned": "Not pinned",
      "harness-unavailable": "Harness unavailable",
      "quota-exhausted": "Quota exhausted",
      "not-a-candidate": "Not a candidate"
    },
    "disposition": {
      "completed": "Completed",
      "waiting": "Waiting",
      "assistance": "Assistance",
      "attention": "Attention"
    },
    "state": {
      "queued": "Queued",
      "executing": "Executing",
      "waiting-owned": "Waiting for owned micro tasks",
      "awaiting-host": "Awaiting Host",
      "delivered": "Awaiting acceptance",
      "accepted": "Accepted",
      "concluded": "Concluded",
      "cancelled": "Cancelled",
      "failed": "Failed"
    }
  },
  identifier: { copy: "Copy identifier {{value}}", copied: "Copied", failed: "Copy failed; try again" },
  actor: {
    unknownOrigin: "Unrecorded initiator", microTask: "Micro task {{id}}",
    events: {
      dispatch: "{{time}} {{role}} submitted “{{title}}”",
      decide: "{{time}} {{role}} resolved a workspace conflict for “{{title}}”",
      continue: "{{time}} {{role}} continued “{{title}}”", integrate: "{{time}} {{role}} integrated the output of “{{title}}”",
      accept: "{{time}} {{role}} accepted “{{title}}”", reject: "{{time}} {{role}} requested acceptance changes for “{{title}}”",
      stop: "{{time}} {{role}} stopped “{{title}}”", console: "{{time}} Console stopped “{{title}}”",
      service: "{{time}} Service stopped “{{title}}”", unknown: "{{time}} Stopped “{{title}}” (initiator unknown)",
      takeover: "{{time}} {{role}} took over “{{title}}”", other: "{{time}} {{role}} {{action}} “{{title}}”",
    },
    unrecorded: "Not recorded", identity: "{{role}} · {{id}}", recordedBy: "Recorded by",
    markerIdentity: "{{sentence}}, recorded by {{actor}}", origin: "Initiating {{role}}", reason: "Stop reason",
    roles: { host: "Host", lead: "Lead", console: "Console", service: "Service", workflow: "Blackboard", bridge: "Bridge" },
    stop: { host: "Stopped by Host", lead: "Stopped by Lead", console: "Stopped from the console", service: "Stopped by the service", workflow: "Stopped by Blackboard", bridge: "Stopped by Bridge", unknown: "Stopped (initiator unknown)" },
  },
  duration: { hour: "{{n}} h", minute: "{{n}} min", second: "{{n}} s" },
  relative: { second: "{{n}} s ago", minute: "{{n}} min ago", hour: "{{n}} h ago", day: "{{n}} d ago" },
  skipToContent: "Skip to main content",
  timelineFix: {
    fixes: "Fixes {{title}}",
    fixedBy: "Fixes: {{count}}",
    target: "Micro task being fixed",
  },
  nav: {
    label: "Main navigation",
    tasks: "Tasks",
    buddies: "Buddies",
    settings: "Settings",
    pendingHost: "Micro tasks waiting for their Host's decision",
    unsaved: "Unsaved changes",
  },
  connection: {
    lost: "Connection lost",
    retry: "Retry",
  },
  runtimeUpdated: {
    message: "The console has been updated. Refresh to use the new version.",
    refresh: "Refresh",
  },
  reminders: {
    overdue: "Overdue",
    possiblyStuck: "Possibly stalled",
  },
  macroWork: {
    pause: "Pause", resume: "Resume", end: "End",
    pausing: "Pausing", paused: "Paused",
    title: "End macro task", cancel: "Cancel", confirm: "End",
    scope: "Unfinished micro tasks stop and are concluded as ended by you from the console, and their checkouts are reclaimed. Delivered ones are left for the Host to accept. Ending cannot be undone.",
    scopeUnfinished: "End {{count}} micro tasks", scopeDelivered: "Keep {{count}} delivered micro tasks",
    pauseUnknown: "The pause result is unknown and may have taken effect; retry the same request or refresh to check.",
    resumeUnknown: "The resume result is unknown and may have taken effect; retry the same request or refresh to check.",
    endUnknown: "The end result is unknown and may have taken effect; retry the same request or refresh to check.",
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
      login: "Browser authorization has expired; run hey-my-buddy console to authorize this browser again. The draft is kept.",
    },
    reload: "Reread settings",
  },
  startup: {
    entryUnavailable: "The console entry has expired or is unavailable", reopenCommand: "hey-my-buddy console",
    connecting: "Connecting to the local blackboard",
    waiting: "Connecting…",
    failed: "Cannot reach the blackboard right now",
    reconnect: "Reconnect",
  },
} satisfies typeof zh & { macroWork: Record<string, string> };
