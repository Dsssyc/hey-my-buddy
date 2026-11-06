import { describe, expect, it } from "vitest";
import realSnapshot from "./fixtures/schema-37-snapshot.json";
import { snapshotReadSchema } from "./types";

// Reduced from the Host's private schema-37 service snapshot. Evaluation,
// model and correspondence rows retain their real wire shape; credentials and
// unrelated board facts are omitted. This fixture must not use preview builders.
describe("schema-37 service snapshot", () => {
  it("reads model-keyed kind evaluations without a legacy model name field", () => {
    expect(realSnapshot.kindEvaluations.length).toBeGreaterThan(0);
    expect(realSnapshot.kindEvaluations.every(row => !("model" in row))).toBe(true);
    const snapshot = snapshotReadSchema.parse(realSnapshot);
    expect(snapshot.kindEvaluations).toHaveLength(realSnapshot.kindEvaluations.length);
    for (const [index, row] of realSnapshot.kindEvaluations.entries()) {
      expect(snapshot.kindEvaluations![index]).toMatchObject({ harness: row.harness,
        modelId: row.modelId, modelDisplayName: row.modelDisplayName,
        modelNames: row.modelNames, effort: row.effort });
    }
  });
});
