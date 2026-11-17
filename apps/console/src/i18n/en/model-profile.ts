import type zh from "../zh/model-profile";
export default {
  unknownTool: "Unknown tool",
  title: "Model profile", strength: "Strengths", weakness: "Weaknesses", notBuilt: "No profile built yet", build: "Build profile", rebuild: "Rebuild profile",
  quota: "{{name}} will browse the web to build this profile, using its quota.", noBuddy: "Choose a buddy in Router Profiles first.", saveFirst: "Save or discard changes before building a profile.",
  invalidReply: "The profile operation returned an invalid response", readFailed: "The operation replied, but reading failed. Refresh to see the run record.",
  shared: "Shared by buddies using this model", positioning: "Positioning", sources: "Sources",
  claimSource: { vendor: "Vendor claim", "third-party": "Third party" },
  state: { running: "Running", completed: "Completed", failed: "Failed" },
  map: "Map to another model", generation: "Mark a new generation", apply: "Stage change", undo: "Undo",
  pendingMap: "Will map to {{name}} when saved", pendingGeneration: "Will create a new generation named {{name}} when saved", pendingUndo: "Will restore the previous correspondence when saved",
} satisfies typeof zh;
