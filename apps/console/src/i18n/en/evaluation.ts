import type zh from "../zh/evaluation";
export default {
  noEvidence: "No evaluation data yet.", summary: "Summary", noSummary: "No summary yet.", samples: "Samples", firstPasses: "First pass", reworkedSuccesses: "Success after rework", reworkRounds: "Rework rounds", failures: "Failed",
  medians: "Medians", medianDeliveryShort: "Delivery", medianInputShort: "Input", medianCachedShort: "Cached", medianOutputShort: "Output", noMedian: "Not recorded", tokens: "{{n}} tokens",
  domainTable: "Domains and task types", domain: "Domain", noDomains: "No domain nodes yet.", noSample: "No samples",
  taskTypes: { create: "Create", change: "Change", debug: "Debug", verify: "Verify", review: "Review", research: "Research", write: "Write" },
  cell: "{{samples}} samples · first pass {{rate}} · {{rework}} rework rounds",
  previousSamples: "Previous samples", since: "Points to the current model since {{date}}", names: "Names:", modelActions: "Model actions", chooseName: "Choose a name", targetModel: "Target model", chooseModel: "Choose a model", cancel: "Cancel", continue: "Continue", confirm: "Confirm build",
  explain: {
    generation: "New results for {{name}} will count toward a new generation. Its previous samples remain as secondary evidence. The displayed name and catalog stay unchanged. Confirmation stages a draft; saving publishes it.",
    map: "Map {{name}} to the selected model {{target}}. New results will count toward that model. Previous samples remain as secondary evidence. The displayed name and catalog stay unchanged. Confirmation stages a draft; saving publishes it.",
    undo: "Undo the latest correspondence for {{name}} changed by a profile or the user, restoring the previous mapping. Accumulated samples, the displayed name and catalog stay unchanged. Confirmation stages a draft; saving publishes it.",
  },
  profileCount: "Profiles built {{built}} / {{total}}", batchBuild: "Build profiles", pendingMaintenance: "{{n}} micro tasks pending", noMaintenanceBuddy: "Choose a buddy in Router Maintenance first.", search: "Search models or reasoning effort", showAll: "All", list: "Evaluation list", noMatches: "No matching buddies.", providers: "Shared by {{names}}", detailTabs: "Evaluation details", domains: "Domain evaluation", buildFailedShort: "Profile build failed", confirmBuild: "Confirm profile build", buildCount: "One run for each model listed, {{n}} runs in total.", viewRun: "View run record",
} satisfies typeof zh;
