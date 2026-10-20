import type { tasks as zh } from "../zh/tasks";
export const tasks = {
  sort: { by: "Sort by", direction: "Sort direction", activity: "Last updated", created: "Created", asc: "Ascending", desc: "Descending" },
  legendLabel: "Legend",
  resizeNames: "Drag to resize names; use arrow keys to adjust, or double-click to reset.",
  zoomFit: "Fit width",
  zoomRulerHint: "Scroll to zoom · Shift+scroll to pan · Fit width: double-click · Keys + − 0",
  drawing: {
    acceptedLegend: "Accepted", failedConclusionLegend: "Failed",
    eventLegend: "Event", clusterLegend: "Clustered markers", idleLegend: "Idle",
    eventCluster: "{{count}} markers",
    stopUnconfirmed: "Stop unconfirmed",
    queue: "Queued {{duration}}",
    expected: "Expected {{duration}}", silent: "Possibly stuck · silent {{duration}}", silentLegend: "Possibly stuck",
    waiting: "{{kind}} {{duration}}", inquiry: "Inquiry", inquiryReadout: "Inquiry · {{author}} · {{at}} · {{state}}",
    gap: "Idle {{start}}–{{end}} · {{duration}}", routingMode: "Routing · {{mode}}",
    events: { dispatch: "Dispatched", decide: "Decision", continue: "Continuation", integrate: "Integration", accept: "Accepted", reject: "Rejected", conclude: "Conclusion", takeover: "Takeover" },
  },
  legendMeaning: {
    execution: "Time a buddy spent working; colours distinguish buddies.", wait: "Time between two activities.", waiting: "Time waiting for the Host or Lead to decide or accept the work.",
    silent: "Execution continues, but no native activity was seen for a while.", inquiry: "A question from the Host to a running turn.", event: "A recorded decision or other action.",
    accepted: "This micro task passed acceptance.", failed: "This micro task ended with a failed conclusion.", cluster: "Nearby marks are merged; the number counts them. Click to list each.", idle: "A long inactive stretch folded on the timeline.",
  },

  waiting: "Waiting on someone", failed: "Failed",
  unknownProject: "Unrecorded project", macro: "Macro tasks", list: "Macro task list", entries: "Macro task entries", collapse: "Collapse macro task list", expand: "Expand macro task list",
  search: "Search title, project, Host or ID", all: "All", active: "Running", host: "Waiting for Host", review: "Awaiting acceptance", accepted: "Accepted", ended: "Ended",
  filterHost: "Host", filterReview: "Acceptance", standalone: "Standalone micro tasks", source: "From the first line", unnamed: "Unnamed", update: "New activity; update order", earlier: "Earlier", loading: "Reading", retry: "Reconnect",
  offline: "Disconnected", lastRead: "Showing content read at {{time}}", noMatch: "No matching macro tasks", noTasks: "No macro tasks", emptyTitle: "No macro tasks yet", emptyBody: "Delegate a task in a Host session and it will appear here.", choose: "Choose a macro task", harnesses: "Available harnesses {{n}}", buddies: "Enabled buddies {{n}}", buddy: "Buddy", stop: "Stop", more: "More", detail: "Macro task detail", unknown: "Not recorded", readonly: "This session is read only", stopping: "Stopping",
  turn: "Turn {{n}}", kind: { execution: "Execution", routing: "Route", queue: "Queue" },
  microTasks: "Micro tasks",
  laneRegion: "This macro task's micro tasks",
  execution: "Execution", wait: "Wait", elapsed: "Elapsed", now: "Now", leadBadge: "Lead", otherMicroTasks: "Other micro tasks",
  laneMicroTasks: "{{n}} micro tasks", laneToggle: "Collapse or expand {{title}}'s micro tasks",
  fixes: "Repairs {{title}}", fixedBy: "Repaired by {{n}} micro tasks", newMicroTask: "New micro task", track: "Tracks for {{title}}", segment: "{{label}}", openWait: "Waiting {{duration}}", turnCount: "{{n}} turns", range: "{{start}}–{{end}} · {{n}} micro tasks", noTime: "Time not recorded", noMicroTasks: "No micro tasks", uncompressed: "This macro task's record is incomplete; time is not compressed", timelineReadFailed: "Read failed; time frozen at {{at}}", staleTimeline: "Showing the last read", closeDetail: "Close detail",
} satisfies typeof zh;
export default tasks;
