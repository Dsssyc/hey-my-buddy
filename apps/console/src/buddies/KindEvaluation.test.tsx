import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import type { DomainNode, EvaluationStatistics, KindEvaluation as KindEvaluationData } from "../types";
import { KindEvaluation } from "./KindEvaluation";

const statistics: EvaluationStatistics = {
  samples: 8, successes: 6, failures: 2, firstPasses: 4, reworkRounds: 3,
  firstPassRate: 0.5, failureRate: 0.25, medianDeliverySeconds: 240,
  usage: { samples: 7, medianInputTokens: 1200, medianCachedInputTokens: 300, medianOutputTokens: 500 },
};
const domainCell: EvaluationStatistics = {
  ...statistics, samples: 2, successes: 2, failures: 0, firstPasses: 1, reworkRounds: 1,
};
const evaluation: KindEvaluationData = {
  harness: "codex", model: "gpt-6-sol", effort: "high",
  summary: { text: "善于边界明确的调试。", citations: [
    { runId: "micro-1", title: "修复解析错误", macroTaskId: "macro-1" },
    { runId: "root-2", title: null, macroTaskId: null },
  ] },
  statistics: { overall: statistics, byDomain: { backend: statistics, api: domainCell },
    byDomainTaskType: { backend: { debug: domainCell }, api: { debug: domainCell } }, infrastructureFailures: [] },
};
const domains: DomainNode[] = [
  { nodeId: "backend", name: "后端", definition: "", parentId: null, mergedIntoId: null },
  { nodeId: "api", name: "API", definition: "", parentId: "backend", mergedIntoId: null },
  { nodeId: "old", name: "已合并", definition: "", parentId: null, mergedIntoId: "backend" },
];

afterEach(cleanup);
const show = (props: Parameters<typeof KindEvaluation>[0]) => render(<TooltipProvider><KindEvaluation {...props} /></TooltipProvider>);

describe("buddy-kind evaluation", () => {
  it("opens an explicit empty state when a kind has no recorded evaluation", async () => {
    const user = userEvent.setup();
    show({ domains: [] });
    const toggle = screen.getByRole("button", { name: "评价与领域经验" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("还没有这个同型 buddy 的评价数据。")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
  });

  it("shows cited micro tasks as navigable links and the program's aggregate counts and medians", async () => {
    const user = userEvent.setup();
    const openTask = vi.fn();
    show({ evaluation, domains, onOpenTask: openTask });
    await user.click(screen.getByRole("button", { name: /评价与领域经验/ }));
    expect(screen.getByText("善于边界明确的调试。")).toBeTruthy();
    const link = screen.getByRole("link", { name: "修复解析错误" });
    expect(link.getAttribute("href")).toBe("#tasks/macro-1/micro-1");
    await user.click(link);
    expect(openTask).toHaveBeenCalledWith("macro-1", "micro-1");
    expect(screen.getByRole("link", { name: "root-2" }).getAttribute("href")).toBe("#tasks/run%3Aroot-2/root-2");
    const outcomes = screen.getByRole("region", { name: "累计结果" });
    expect(within(outcomes).getByText("4 · 50%")).toBeTruthy();
    expect(within(outcomes).getByText("1.2k tokens")).toBeTruthy();
    expect(within(outcomes).getByText("8")).toBeTruthy();
    expect(within(outcomes).getByText("6")).toBeTruthy();
  });

  it("shows seven task types, empty cells, and a collapsible domain tree", async () => {
    const user = userEvent.setup();
    show({ evaluation, domains });
    await user.click(screen.getByRole("button", { name: /评价与领域经验/ }));
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader")).toHaveLength(8);
    expect(within(table).getByRole("columnheader", { name: "排错" })).toBeTruthy();
    const emptyCells = within(table).getAllByLabelText("无样本");
    expect(emptyCells).toHaveLength(12);
    expect(emptyCells.every(cell => cell.textContent === "")).toBe(true);
    expect(within(table).getAllByText("返工轮数 1")).toHaveLength(2);
    expect(within(table).queryByText("已合并")).toBeNull();
    const toggle = within(table).getByRole("button", { name: "后端" });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(within(table).queryByRole("rowheader", { name: /API/ })).toBeNull();
  });

  it("lists only the sampled domains and the ancestors that place them", async () => {
    const user = userEvent.setup();
    const sparse: KindEvaluationData = {
      ...evaluation,
      statistics: {
        overall: statistics,
        byDomain: { leaf: domainCell },
        byDomainTaskType: { leaf: { change: domainCell } },
        infrastructureFailures: [],
      },
    };
    const tree: DomainNode[] = [
      { nodeId: "backend", name: "后端", definition: "", parentId: null, mergedIntoId: null },
      { nodeId: "api", name: "API", definition: "", parentId: "backend", mergedIntoId: null },
      { nodeId: "leaf", name: "解析", definition: "", parentId: "api", mergedIntoId: null },
      { nodeId: "other", name: "别的分支", definition: "", parentId: null, mergedIntoId: null },
    ];
    show({ evaluation: sparse, domains: tree });
    await user.click(screen.getByRole("button", { name: /评价与领域经验/ }));
    const table = screen.getByRole("table");
    const rows = within(table).getAllByRole("rowheader");
    expect(rows).toHaveLength(3);
    expect(within(table).getByRole("rowheader", { name: /后端/ })).toBeTruthy();
    expect(within(table).getByRole("rowheader", { name: /API/ })).toBeTruthy();
    expect(within(table).getByRole("rowheader", { name: /解析/ })).toBeTruthy();
    expect(within(table).queryByText("别的分支")).toBeNull();
    const emptyCells = within(table).getAllByLabelText("无样本");
    expect(emptyCells).toHaveLength(20);
    expect(emptyCells.every(cell => cell.textContent === "")).toBe(true);
  });

  it("says the kind has no domain samples without calling the domain tree empty", async () => {
    const user = userEvent.setup();
    const unclassified: KindEvaluationData = {
      ...evaluation,
      statistics: { overall: statistics, byDomain: {}, byDomainTaskType: {}, infrastructureFailures: [] },
    };
    show({ evaluation: unclassified, domains });
    await user.click(screen.getByRole("button", { name: /评价与领域经验/ }));
    expect(screen.getByText("无样本")).toBeTruthy();
    expect(screen.queryByText("还没有领域节点。")).toBeNull();
    expect(screen.queryByRole("table")).toBeNull();
  });
});
