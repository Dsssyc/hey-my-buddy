import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { DomainNode, EvaluationStatistics, KindEvaluation, Model } from "../types";
import { DomainEvaluation, ModelPortrait } from "./EvaluationContent";

const stats: EvaluationStatistics = {
  samples: 8, successes: 6, failures: 2, firstPasses: 4, reworkRounds: 3,
  firstPassRate: 0.5, failureRate: 0.25, medianDeliverySeconds: 240,
  usage: { samples: 8, medianInputTokens: 1200.5, medianCachedInputTokens: 300, medianOutputTokens: 500 },
};
const cell: EvaluationStatistics = { ...stats, samples: 2, successes: 2, failures: 0, firstPasses: 1, reworkRounds: 1 };
const evaluation: KindEvaluation = {
  harness: "codex", modelId: "new", modelDisplayName: "Sol", effort: "high",
  modelNames: [{ harness: "codex", provider: "openai", model: "sol-latest" }],
  previousGroups: [{ modelId: "old", modelDisplayName: "Earlier Sol", names: [
    { harness: "codex", provider: "openai", model: "sol-previous", since: "2026-11-01T00:00:00Z" },
  ] }],
  summary: { text: "Useful for **debugging**." } as KindEvaluation["summary"],
  statistics: { overall: stats, byDomain: { software: stats, api: cell }, byDomainTaskType: { api: { debug: cell } }, infrastructureFailures: [] },
};
const oldEvaluation: KindEvaluation = {
  ...evaluation, modelId: "old", modelDisplayName: "Earlier Sol", previousGroups: [],
  statistics: { overall: { ...stats, samples: 3, successes: 2, firstPasses: 1, failures: 1, reworkRounds: 2 }, byDomain: {}, byDomainTaskType: {}, infrastructureFailures: [] },
};
const domains: DomainNode[] = [
  { nodeId: "software", name: "Software", definition: "", parentId: null, mergedIntoId: null, directCount: 0 },
  { nodeId: "api", name: "API", definition: "", parentId: "software", mergedIntoId: null, directCount: 0 },
  { nodeId: "other", name: "Other", definition: "", parentId: null, mergedIntoId: null, directCount: 0 },
];

afterEach(() => { cleanup(); localStorage.clear(); });
const show = (content: React.ReactNode) => render(<TooltipProvider>{content}</TooltipProvider>);

describe("domain evaluation", () => {
  it("opens the sample graphic to reveal outcomes and medians, then remembers its state", async () => {
    const user = userEvent.setup();
    const view = show(<DomainEvaluation evaluation={evaluation} evaluations={[evaluation, oldEvaluation]} domains={domains} />);
    const tally = view.container.querySelector('[data-slot="evaluation-tally"]')!;
    expect(tally.querySelectorAll('[data-slot="quant-bar"] [data-tone]')).toHaveLength(3);
    expect([...tally.querySelectorAll('[data-tone]')].map(part => part.getAttribute("data-tone"))).toEqual(["green", "amber", "sample-pink"]);
    const button = within(tally as HTMLElement).getByRole("button", { name: /样本 8|Samples 8/ });
    expect(button.getAttribute("aria-expanded")).toBe("false");
    expect(within(tally as HTMLElement).queryByText(/First pass|一次通过/)).toBeNull();
    await user.click(button);
    expect(button.getAttribute("aria-expanded")).toBe("true");
    expect(within(tally as HTMLElement).getByText(/First pass|一次通过/)).toBeTruthy();
    expect(tally.querySelector('[data-kind="fail"]')).toBeTruthy();
    expect(screen.getByText("debugging")).toBeTruthy();
    expect(view.container.querySelector('a[href^="#tasks/"]')).toBeNull();
    expect(screen.getAllByText(/median|中位数/i).length).toBeGreaterThan(0);
    expect(within(tally as HTMLElement).getByText(/1,200\.5 tokens/)).toBeTruthy();
    view.unmount();
    const revisited = show(<DomainEvaluation evaluation={evaluation} evaluations={[evaluation, oldEvaluation]} domains={domains} />);
    expect(within(revisited.container.querySelector('[data-slot="evaluation-tally"]') as HTMLElement).getByRole("button", { name: /样本 8|Samples 8/ }).getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps unsampled cells blank and folds sampled descendants", async () => {
    const user = userEvent.setup();
    show(<DomainEvaluation evaluation={evaluation} evaluations={[evaluation]} domains={domains} />);
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(within(table).queryByText("Other")).toBeNull();
    expect(within(table).getAllByLabelText(/No samples|无样本/).every(node => node.textContent === "")).toBe(true);
    expect(within(table).queryByText(/Rework rounds 1|返工轮数 1/)).toBeNull();
    const fold = within(table).getByRole("button", { name: "Software" });
    expect(fold.getAttribute("aria-expanded")).toBe("true");
    await user.click(fold);
    expect(fold.getAttribute("aria-expanded")).toBe("false");
    expect(within(table).queryByRole("rowheader", { name: /API/ })).toBeNull();
  });

  it("puts names before subdued old groups and takes old counts from the matching kind", async () => {
    const user = userEvent.setup();
    const view = show(<DomainEvaluation evaluation={evaluation} evaluations={[evaluation, oldEvaluation]} domains={domains}><div data-testid="names">Names menu</div></DomainEvaluation>);
    const names = screen.getByTestId("names"), previous = screen.getByText("Earlier Sol");
    expect(names.compareDocumentPosition(previous) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(previous.closest("section")?.textContent).toContain("3");
    expect(previous.closest("section")?.textContent).toContain("sol-previous");
    expect(view.container.querySelectorAll('[data-slot="evaluation-tally"]')).toHaveLength(2);
    const oldTally = view.container.querySelectorAll('[data-slot="evaluation-tally"]')[1] as HTMLElement;
    expect(oldTally.className).toContain("opacity-70");
    expect(within(oldTally).queryByText(/First pass|一次通过/)).toBeNull();
    await user.click(within(oldTally).getByRole("button", { name: /样本 3|Samples 3/ }));
    expect(within(oldTally).getByText(/First pass|一次通过/)).toBeTruthy();
  });
});

describe("model portrait", () => {
  const model: Model = {
    modelId: "new", displayName: "Sol", names: [], latestProfileRun: null,
    profile: { positioning: "Engineering model", builtAt: "2026-11-07T00:00:00Z", profileRunId: "run-1", claims: [
      { polarity: "strength", claim: "Tracks call chains", sourceUrl: "https://example.org/one", sourceDate: "2026-11-06", sourceKind: "vendor" },
      { polarity: "strength", claim: "Debugs with tests", sourceUrl: "https://example.org/one", sourceDate: "2026-11-06", sourceKind: "vendor" },
      { polarity: "weakness", claim: "Needs context", sourceUrl: "https://example.org/two", sourceDate: "2026-11-05", sourceKind: "third-party" },
    ] },
  };
  it("shares one model profile, numbers claims, and lists each source once", () => {
    const view = show(<ModelPortrait model={model}><button>Rebuild</button></ModelPortrait>);
    expect(screen.getByText("Engineering model")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Rebuild" })).toBeTruthy();
    expect(view.container.querySelectorAll('sup[aria-label="S1"]')).toHaveLength(2);
    expect(view.container.querySelectorAll('a[href="https://example.org/one"]')).toHaveLength(1);
    expect(view.container.querySelectorAll('a[href="https://example.org/two"]')).toHaveLength(1);
    expect(view.container.querySelector('[data-slot="profile-usage"]')).toBeNull();
  });
});
