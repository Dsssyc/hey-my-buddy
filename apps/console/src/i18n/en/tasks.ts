import type { tasks as zh } from "../zh/tasks";
export const tasks = {
  recordsList: "Record list", recordsEntries: "Record entries", recordsCollapse: "Collapse record list", recordsExpand: "Expand record list", recordsChoose: "Choose a record.", recordsEmpty: "No records yet.", recordsNoMatch: "No matching records.", recordsFailed: "Read failed", recordsNew: "New records <count>{{n}}</count>", recordsLatest: "Back to latest", today: "Today", yesterday: "Yesterday", helper: "Assistance task", calculation: "Routing calculation", command: "Command execution",
  waiting: "Waiting for a hand", failed: "Failed",
  unknownProject: "Unrecorded project", macro: "Macro tasks", records: "Records", list: "Macro task list", entries: "Macro task entries", collapse: "Collapse macro task list", expand: "Expand macro task list",
  search: "Search title, project, Host or ID", all: "All", active: "Running", host: "Waiting for Host", review: "Awaiting acceptance", accepted: "Accepted", ended: "Ended",
  filterHost: "Host", filterReview: "Acceptance", standalone: "Standalone micro tasks", source: "From the first line", unnamed: "Unnamed", update: "New activity, sort by latest activity", earlier: "Earlier", loading: "Reading", retry: "Reconnect",
  offline: "Disconnected", lastRead: "Showing content read at {{time}}", noMatch: "No matching macro tasks", noTasks: "No macro tasks", emptyTitle: "No macro tasks yet", emptyBody: "Delegate a task in a Host session and it will appear here.", choose: "Choose a macro task", harnesses: "Available harnesses {{n}}", buddies: "Enabled buddies {{n}}", buddy: "Buddy", stop: "Stop", more: "More", detail: "Macro task detail", unknown: "Not recorded", readonly: "This session is read only", stopping: "Stopping",
} satisfies typeof zh;
export default tasks;
