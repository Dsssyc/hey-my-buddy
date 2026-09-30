import { describe, expect, it } from "vitest";
import { finalArtifact, finalIntegration } from "./integration";
import type { IntegrationRecord, Workflow } from "./workflow-types";

/** Only the fields these helpers read, so the envelope stays explicit. */
function workflow(partial: Partial<Workflow>): Workflow {
  return {
    governed: true, runId: "run", hostId: "host", ownerGeneration: 1, revision: 1,
    state: "delivered", awaitingHost: false, waitReason: "", continuationCount: 0,
    workspace: null, currentTurn: null, activeRequest: null, children: [],
    artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null,
    task: { runId: "run", revision: 1, status: "completed" },
    ...partial,
  } as Workflow;
}

function verified(partial: Partial<IntegrationRecord>): IntegrationRecord {
  return {
    integrationId: "int", runId: "run", artifactId: "artifact", attemptId: "attempt",
    state: "verified", strategy: "patch", target: null, sourceCommit: null, sourceTree: null,
    beforeCommit: null, afterCommit: null, beforeTree: null, afterTree: null,
    verification: {}, notRequired: false, reason: "", actor: "host", createdAt: "2026-09-25",
    ...partial,
  };
}

const output = { artifactId: "final-output", attemptId: "attempt", sourceTaskId: "run", kind: "output", manifestSha256: "h1" };
const resolved = { artifactId: "final-resolved", attemptId: "attempt", sourceTaskId: "run", kind: "resolved-output", manifestSha256: "h2" };
const earlier = { artifactId: "earlier-output", attemptId: "attempt", sourceTaskId: "helper", kind: "output", manifestSha256: "h3" };

describe("the recorded final artifact", () => {
  it("is matched by finalArtifactId and accepts a resolved output", () => {
    const resolvedWorkflow = workflow({ finalArtifactId: resolved.artifactId, artifacts: [earlier, resolved] });
    expect(finalArtifact(resolvedWorkflow)?.artifactId).toBe("final-resolved");
    const outputWorkflow = workflow({ finalArtifactId: output.artifactId, artifacts: [output, earlier] });
    expect(finalArtifact(outputWorkflow)?.artifactId).toBe("final-output");
  });

  it("never falls back to another attempt's output when nothing is recorded", () => {
    expect(finalArtifact(workflow({ finalAttemptId: "attempt", artifacts: [output, earlier] }))).toBeNull();
    expect(finalArtifact(workflow({ finalArtifactId: "gone", artifacts: [output] }))).toBeNull();
    expect(finalArtifact(null)).toBeNull();
  });
});

describe("the integration record that satisfies acceptance", () => {
  it("requires the record to name exactly the final artifact", () => {
    const value = workflow({
      finalArtifactId: resolved.artifactId, artifacts: [earlier, resolved],
      integrations: [verified({ integrationId: "int-other", artifactId: earlier.artifactId })],
    });
    expect(finalIntegration(value)).toBeNull();
    expect(finalIntegration(value, finalArtifact(value))).toBeNull();
  });

  it("accepts a verified or an explicit not-required record and keeps the newest", () => {
    const notRequired = verified({
      integrationId: "int-ok", artifactId: resolved.artifactId, state: "not-required",
      notRequired: true, strategy: "not-required", reason: "只读任务无需整合",
    });
    const newest = verified({ integrationId: "int-new", artifactId: resolved.artifactId, strategy: "cherry-pick" });
    const value = workflow({
      finalArtifactId: resolved.artifactId, artifacts: [resolved],
      // `compact` lists integrations newest first.
      integrations: [newest, notRequired],
    });
    expect(finalIntegration(value)?.integrationId).toBe("int-new");
    expect(finalIntegration(value)?.state).toBe("verified");
  });

  it("ignores a record in any other state", () => {
    const value = workflow({
      finalArtifactId: output.artifactId, artifacts: [output],
      integrations: [verified({ artifactId: output.artifactId, state: "pending" })],
    });
    expect(finalIntegration(value)).toBeNull();
  });
});
