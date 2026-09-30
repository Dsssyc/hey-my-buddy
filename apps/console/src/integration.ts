import type { IntegrationRecord, Workflow } from "./workflow-types";

/** Sealed output kinds the board can record as the final artifact. */
const FINAL_OUTPUT_KINDS = new Set(["output", "resolved-output"]);

type Artifact = Workflow["artifacts"][number];

/**
 * The one recorded final artifact, matched strictly by `finalArtifactId`; the
 * Host-resolution `resolved-output` kind is a legal final delivery. There is no
 * fallback to another attempt's output: acceptance must bind the artifact the
 * board itself recorded as final.
 */
export function finalArtifact(value: Workflow | null | undefined): Artifact | null {
  if (!value?.finalArtifactId) return null;
  return value.artifacts.find(
    (artifact) => artifact.artifactId === value.finalArtifactId && FINAL_OUTPUT_KINDS.has(artifact.kind),
  ) ?? null;
}

/**
 * The newest integration record that satisfies acceptance for exactly this
 * artifact: a verified integration or an explicit not-required decision. A
 * record for any other artifact, or in any other state, never counts. `compact`
 * lists integrations newest first, so the first match is the newest one.
 */
export function finalIntegration(
  value: Workflow | null | undefined,
  artifact: Artifact | null = finalArtifact(value),
): IntegrationRecord | null {
  if (!value || !artifact) return null;
  return (value.integrations ?? []).find(
    (record) => record.artifactId === artifact.artifactId
      && (record.state === "verified" || record.state === "not-required"),
  ) ?? null;
}

/** Integration records that count as evidence, newest first. */
export function recordedIntegrations(value: Workflow | null | undefined): IntegrationRecord[] {
  return (value?.integrations ?? []).filter(
    (record) => record.state === "verified" || record.state === "not-required",
  );
}
