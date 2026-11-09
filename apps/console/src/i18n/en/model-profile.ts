import type zh from "../zh/model-profile";
export default {
  toolFailed: "Failed", toolUnknown: "Unknown", unknownTool: "Unknown tool", toolOutcomesUnknown: "Tool call outcomes are unknown",
  duration: "Duration", toolCalls: "Tool calls",
  title: "Model profile", strength: "Strengths", weakness: "Weaknesses", notBuilt: "No profile built yet", build: "Build profile", rebuild: "Rebuild profile",
  quota: "{{name}} will browse the web to build this profile, using its quota.", noBuddy: "Choose a profile buddy on the Roles tab first.", saveFirst: "Save or discard changes before building a profile.",
  started: "The profile is running with {{name}}.", invalidReply: "The profile operation returned an invalid response", readFailed: "The operation replied, but reading failed. Refresh to see the run record.",
  latestRun: "Latest profile run", corrections: "Correction rounds: {{n}}", usage: "Usage", correspondences: "Model correspondences",
  source: { "same-name": "Same-name normalization", profile: "Profile plane suggestion", user: "User" }, claimSource: { vendor: "Vendor claim", "third-party": "Third party" },
  state: { running: "Running", completed: "Completed", failed: "Failed" },
  map: "Map to an existing model", generation: "Mark a new generation", apply: "Stage change", undo: "Undo latest change",
  actionFor: "Correspondence action for {{name}}", targetFor: "Model for {{name}}", newNameFor: "New generation display name for {{name}}",
  pendingMap: "Will map to {{name}} when saved", pendingGeneration: "Will create a new generation named {{name}} when saved", pendingUndo: "Will restore the previous correspondence when saved",
} satisfies typeof zh;
