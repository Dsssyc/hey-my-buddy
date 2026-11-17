import { describe, expect, it } from "vitest";
import { parseSession } from "./types";
import { fixtureCoverage } from "./test-fixtures";

const response = (coverage: unknown) => ({ state: "ended", stopped: null, coverage, events: [], before: 0, after: 0, hasEarlier: false });

describe("session coverage response boundary", () => {
  it("keeps evidence, unavailability reasons and unverified categories distinct", () => {
    expect(parseSession(response(fixtureCoverage)).coverage).toEqual(fixtureCoverage);
  });
  it("rejects missing categories and evidence without a version", () => {
    const { plan: _plan, ...missing } = fixtureCoverage;
    for (const coverage of [missing,
      { ...fixtureCoverage, messages: { status: "evidenced" } },
      { ...fixtureCoverage, messages: { status: "evidenced", version: " " } },
      { ...fixtureCoverage, reasoning: { status: "unavailable" } },
      { ...fixtureCoverage, plan: { status: "supported" } },
    ]) expect(() => parseSession(response(coverage))).toThrow("Invalid session response");
  });
});
