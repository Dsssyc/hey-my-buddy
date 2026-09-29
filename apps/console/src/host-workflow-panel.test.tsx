import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WorkflowPanel } from "./WorkflowPanel";
import type { Workflow } from "./workflow-types";
import type { ConsoleApi } from "./api";
import type { Snapshot, Task } from "./types";

afterEach(cleanup);

/**
 * ADR-018 Host workflow in the read-only delegation detail: the independent
 * conclusion of a failed goal, the partial-output marks, the cumulative patch,
 * the Host's own supplementary paths and the per-execution token usage. A
 * failed goal keeps its failure label and is never presented as accepted.
 */

function task(): Task {
  return {
    runId: "parent", task: "完成状态内核", status: "failed", owner: "host-a", cwd: "/repo",
    revision: 4, createdAt: "2026-09-22", acceptedAt: null, acceptanceVerdict: null,
    spec: { workspace: false }, shutdownConfirmed: true,
    workflow: { state: "failed", awaitingHost: false, hostId: "host-a", ownerGeneration: 2, revision: 7 },
  };
}

function workflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    governed: true, runId: "parent", hostId: "host-a", ownerGeneration: 2, revision: 7,
    state: "failed", awaitingHost: false, waitReason: "the run was cancelled", continuationCount: 0,
    configurationLocked: false,
    hostConclusion: {
      conclusionId: "concl-1", attemptId: "attempt-2", executionStatus: "failed",
      note: "额度耗尽前已把改动提取为部分成果", evidence: ["部分成果已封存", "检出内容已保留"],
      artifactId: "art-partial", integrationId: null, actor: "host-a",
      createdAt: "2026-09-29T09:00:00.000Z", ownerGeneration: 2, runRevision: 7,
    },
    executionConfiguration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max" },
    executionConfigurationRevision: 2,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: "/repo", kind: "existing", access: "write", inputCommit: "input-commit", manifestSha256: "manifest-a" },
    currentTurn: { turnId: "turn-2", turnIndex: 2, attemptId: "attempt-2", resumeMode: "initial", summary: "额度耗尽", tokenUsage: null },
    turns: [
      { turnId: "turn-2", turnIndex: 2, attemptId: "attempt-2", executionConfiguration: { adapter: "codex", provider: "openai", model: "gpt-5-codex", effort: "high" }, tokenUsage: {
        inputTokens: 20000, cachedInputTokens: 15000, outputTokens: 800, source: "codex-native", scope: "attempt" } },
      { turnId: "turn-1", turnIndex: 1, attemptId: "attempt-1", executionConfiguration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max" }, tokenUsage: null },
    ],
    activeRequest: null,
    children: [], finalArtifactId: null, finalAttemptId: null,
    artifacts: [{
      artifactId: "art-partial", attemptId: "attempt-1", sourceTaskId: "parent", kind: "partial-output",
      manifestSha256: "manifest-partial", partial: true, verified: false, final: false,
      cumulativePatch: { baseCommit: "input-commit", outputCommit: "out-commit", path: "/diffs/cumulative.diff",
        sha256: "b".repeat(64), changedPaths: ["src/a.py"] },
    }],
    integrations: [], task: task(), ...overrides,
  };
}

function fixture(value: Workflow = workflow()) {
  const snapshot = {
    csrfToken: "fixture-csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    profiles: [],
  } as unknown as Snapshot;
  const command = vi.fn(async (op: string) => {
    if (op !== "workflow_get") throw new Error(`Unexpected mutation: ${op}`);
    return structuredClone(value);
  });
  const props = { task: task(), snapshot, api: { command } as unknown as ConsoleApi, refresh: vi.fn(), selectTask: vi.fn() };
  return { props, value };
}

/** The tab controls are real buttons; the panels stay mounted and hidden. */
async function openTab(name: string) {
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name }));
}

describe("ADR-018 Host workflow in the delegation detail", () => {
  it("shows the Host conclusion of a failed goal as an independent record, not an acceptance", async () => {
    render(<WorkflowPanel {...fixture().props} />);
    const block = await screen.findByRole("region", { name: "Host 结论" });
    expect(within(block).getByText("Host 结论")).toBeTruthy();
    expect(within(block).getByText(/^失败 · 不计入验收/)).toBeTruthy();
    expect(within(block).getByText("额度耗尽前已把改动提取为部分成果")).toBeTruthy();
    expect(within(block).getByText("attempt-2")).toBeTruthy();
    expect(within(block).getByText("art-partial")).toBeTruthy();
    expect(within(block).getByText(/host-a · 第 2 代/)).toBeTruthy();
    // The execution outcome label never becomes accepted or successful.
    expect(screen.queryByText("已验收")).toBeNull();
    expect(screen.getByText("执行失败")).toBeTruthy();
  });

  it("marks the partial output and shows the cumulative patch and Host paths", async () => {
    const value = workflow({
      integrations: [{
        integrationId: "int-partial", runId: "parent", artifactId: "art-partial", attemptId: "attempt-1",
        state: "not-required", strategy: "none", target: null, sourceCommit: null, sourceTree: null,
        beforeCommit: null, afterCommit: null, beforeTree: null, afterTree: null,
        verification: { notRequired: true, reason: "未整合", hostPaths: ["docs/reference/console.md"] },
        notRequired: true, reason: "Host 已提取部分成果", actor: "host-a", createdAt: "2026-09-29T09:00:00.000Z",
      }],
    });
    render(<WorkflowPanel {...fixture(value).props} />);
    await screen.findByRole("region", { name: "Host 结论" });
    await openTab("产物与验收");
    const list = screen.getByText("固定产物引用").closest("section")!;
    expect(within(list).getByText(/部分成果（未验证、非最终）/)).toBeTruthy();
    expect(within(list).getByText(/部分、未验证、非最终/)).toBeTruthy();
    expect(within(list).getByText(/累计补丁/)).toBeTruthy();
    expect(within(list).getByText("base input-commit")).toBeTruthy();
    expect(within(list).getByText("output out-commit")).toBeTruthy();
    expect(within(list).getByText("SHA-256 " + "b".repeat(64))).toBeTruthy();
    expect(within(list).getByText(/src\/a\.py/)).toBeTruthy();
    expect(within(list).getByText(/Host 补充改动/)).toBeTruthy();
    expect(within(list).getByText(/docs\/reference\/console\.md/)).toBeTruthy();
    // A partial output is never presented as the final artifact.
    expect(within(list).queryByText("最终产物与整合证据")).toBeNull();
  });

  it("shows each execution's usage separately, keeps unknown unknown and never adds the cache again", async () => {
    render(<WorkflowPanel {...fixture().props} />);
    await screen.findByRole("region", { name: "Host 结论" });
    await openTab("执行记录");
    const usage = screen.getByRole("region", { name: "每次执行的用量" });
    const rows = within(usage).getAllByRole("listitem");
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByText("第 2 回合")).toBeTruthy();
    expect(within(rows[0]).getByText("输入 20,000（含缓存 15,000） · 输出 800")).toBeTruthy();
    expect(within(rows[0]).getByText("来源 codex-native")).toBeTruthy();
    expect(within(usage).getByText("第 1 回合")).toBeTruthy();
    expect(within(usage).getByText("未记录（未知）")).toBeTruthy();
    expect(within(usage).queryByText(/35,000/)).toBeNull();
  });

  it("states whether an explicit configuration is a user lock or a Host choice", async () => {
    render(<WorkflowPanel {...fixture(workflow({ configurationLocked: true })).props} />);
    await screen.findByRole("region", { name: "Host 结论" });
    expect(screen.getByText(/用户要求锁定，续做时不能更换/)).toBeTruthy();
  });
});
