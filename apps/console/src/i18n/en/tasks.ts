import type { tasks as zh } from "../zh/tasks";
export const tasks = {
  zoomOut: "Zoom out (-)", zoomIn: "Zoom in (+)", zoomFit: "Fit to window (0)",
  drawing: {
    acceptedLegend: "Accepted", failedConclusionLegend: "Failed conclusion",
    eventLegend: "Event", clusterLegend: "Clustered markers", idleLegend: "Idle",
    eventCluster: "{{count}} markers",
    stopUnconfirmed: "Stop unconfirmed",
    running: "Running {{duration}}", failed: "{{duration}} failed", queue: "Queued {{duration}}",
    expected: "Expected {{duration}}", silent: "Possibly stuck · silent {{duration}}", silence: "Silent {{duration}}", silentLegend: "Possibly stuck",
    waiting: "{{kind}} {{duration}}", inquiry: "Inquiry", inquiryReadout: "Inquiry · {{author}} · {{at}} · {{state}}",
    gap: "Idle {{start}}–{{end}} · {{duration}}", routingMode: "Routing · {{mode}}",
    events: { dispatch: "Dispatched", decide: "Decision", continue: "Continuation", integrate: "Integration", accept: "Accepted", reject: "Rejected", conclude: "Conclusion", takeover: "Takeover" },
  },

  waiting: "Waiting on someone", failed: "Failed",
  unknownProject: "Unrecorded project", macro: "Macro tasks", list: "Macro task list", entries: "Macro task entries", collapse: "Collapse macro task list", expand: "Expand macro task list",
  search: "Search title, project, Host or ID", all: "All", active: "Running", host: "Waiting for Host", review: "Awaiting acceptance", accepted: "Accepted", ended: "Ended",
  filterHost: "Host", filterReview: "Acceptance", standalone: "Standalone micro tasks", source: "From the first line", unnamed: "Unnamed", update: "New activity, sort by latest activity", earlier: "Earlier", loading: "Reading", retry: "Reconnect",
  offline: "Disconnected", lastRead: "Showing content read at {{time}}", noMatch: "No matching macro tasks", noTasks: "No macro tasks", emptyTitle: "No macro tasks yet", emptyBody: "Delegate a task in a Host session and it will appear here.", choose: "Choose a macro task", harnesses: "Available harnesses {{n}}", buddies: "Enabled buddies {{n}}", buddy: "Buddy", stop: "Stop", more: "More", detail: "Macro task detail", unknown: "Not recorded", readonly: "This session is read only", stopping: "Stopping",
  turn: "Turn {{n}}", kind: { execution: "Execution", routing: "Route", queue: "Queue" },
  microTasks: "Micro tasks",
  laneRegion: "This macro task's micro tasks",
  execution: "Execution", wait: "Wait", elapsed: "Elapsed", now: "Now", leadBadge: "Lead", otherMicroTasks: "Other micro tasks",
  laneMicroTasks: "{{n}} micro tasks", laneToggle: "Collapse or expand {{title}}'s micro tasks",
  fixes: "Repairs {{title}}", fixedBy: "Repaired by {{n}} micro tasks", newMicroTask: "New micro task", track: "Tracks for {{title}}", segment: "{{label}}", openWait: "Waiting {{duration}}", turnCount: "{{n}} turns", range: "{{start}}–{{end}} · {{n}} micro tasks", noTime: "Time not recorded", noMicroTasks: "No micro tasks", uncompressed: "This macro task's record is incomplete; time is not compressed", timelineReadFailed: "Read failed; time frozen at {{at}}", staleTimeline: "Showing the last read", closeDetail: "Close detail",
} satisfies typeof zh;
export default tasks;
