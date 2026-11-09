export default {
  jobs: "Router work", routing: "Routing", maintenance: "Maintenance", profiles: "Profiles",
  who: "Who serves", settings: "Settings", notSet: "This work does not run. Set a buddy.",
  description: { routing: "Choose a Worker buddy for a micro task within the routing bounds.", maintenance: "Classify completed micro tasks by domain and task type, and update buddy evaluations.", profiles: "Research public sources online to build model profiles." },
  status: { unset: "Not set", unknown: "No records yet", running: "Latest run · running", completed: "Latest run · succeeded", completed_maintenance: "Latest run · completed", failed: "Latest run · failed", failed_maintenance: "Latest run · failed" },
  sharedWait: "Profile building uses this same wait for the model to start responding.", everyBefore: "1 in every", everyAfter: "micro tasks",
  first: "Already first", last: "Already last", answers: "Answered / No answer", answerHint: "Answered {{answered}} times, no answer {{missing}} times; {{consecutive}} consecutive no answers.",
  copyId: "Copy ID", copied: "ID copied", failed: "Copy failed", timeRange: "Start–end", usage: "Usage · cached / other input / output",
};
