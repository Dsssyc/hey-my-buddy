import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { formatDateTime } from "./i18n/format";
import type { Buddy, Snapshot, Task } from "./types";
import { timelineOfRecords } from "./task-detail/task-detail.fixtures";
import { macroTaskSummary } from "./macro-task-fixtures";
import { catalogue, effortCell, levelSwitch, listed, modelButton, note, pane, selectModel } from "./buddies/page.testing";

const dsh = (model: string) => `dsh / deepseek-official / ${model}`;
const GLM = "zcode / bigmodel-api / GLM-5.3";

// The installed catalog contains four DSH model services with four efforts each and
// two ZCode model services with three each. Only the four recorded choices are enabled.
function catalog(): Buddy[] {
  const enabled = new Set(["dsh:deepseek-flash:off", "dsh:deepseek-flash:max", "zcode:GLM-5.3:high", "zcode:GLM-5.3-Flash:max"]);
  return [
    ...["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]
      .flatMap(model => ["off", "low", "high", "max"].map((effort, effortRank) => ({ harness: "dsh", provider: "deepseek-official", model, effort, effortRank }))),
    ...["GLM-5.3", "GLM-5.3-Flash"].flatMap(model => ["low", "high", "max"]
      .map((effort, effortRank) => ({ harness: "zcode", provider: "bigmodel-api", model, effort, effortRank }))),
  ].map(p => ({ ...p, buddyId: [p.harness, p.provider, p.model, p.effort].join(":"), label: `${p.model} · ${p.effort}`,
    available: true, enabled: enabled.has([p.harness, p.model, p.effort].join(":")), capabilities: [],
    contextWindow: null, source: "catalog:fixture", description: "" }));
}
function microTask(runId: string, sourceHostId = "codex-source", projectId = "source-project"): Task {
  return { runId, task: `目标 ${runId}`, status: "queued", owner: "worker:unrelated-label", cwd: `/runtime/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-24T10:00:00Z", updatedAt: "2026-09-24T10:00:00Z", acceptedAt: null, reviewRejectedAt: null, reviewRejectionNote: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 1 },
    lineage: { kind: "micro-task", sourceHostId, currentHostId: "current-host", parentRunId: null, rootRunId: runId, rootLeadRunId: null, routingTask: null,
      project: { id: projectId, label: projectId, path: `/source/${projectId}` },
      buddy: { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" } } };
}
/** The compact collaboration record of a micro task, as `workflow_get` sends it. */
type Workflow = Record<string, unknown>;
function workflow(task: Task): Workflow {
  return { runId: task.runId, hostId: "current-host", revision: 1, state: "awaiting-host", buddy: null, buddyLocked: false, delegationAuthority: false,
    routing: null, turns: [], currentTurn: null, requests: [], counts: { turns: 0, openRequests: 1 }, children: [], artifacts: [], integrations: [],
    finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: { path: task.cwd },
    activeRequest: { requestId: `request-${task.runId}`, kind: "assistance", state: "open", createdAt: "2026-09-24T10:00:00Z", decidedAt: null, childTaskId: null, routing: false,
      summary: `协助 ${task.runId}`, attempted: "已有检查", neededWork: ["补充测试"], expectedArtifacts: [], acceptance: "检查通过" },
    task: { runId: task.runId, revision: task.revision, status: task.status, lineage: task.lineage } };
}
function fixture(records: Task[] = []) {
  const buddies = catalog();
  // The board-wide awaiting-Host root count the slim snapshot carries; the
  // fixture computes it with the server's own dedup semantics.
  const pendingCount = new Set(records.filter(t => t.workflow?.awaitingHost)
    .map(t => t.lineage?.rootRunId || t.runId)).size;
  const snapshot: Snapshot = { csrfToken: "csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session" },

    configuration: { routerBuddyIds: [buddies[0].buddyId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies,
    preferences: [], modelServiceNotes: [], decisions: [],
    modelServiceConcurrency: [],
    tasks: { pendingCount },
    capabilities: { selection: false } };

  const workflows = new Map<string, Workflow>();
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {

    if (operation === "workflow_get") {
      const value = workflows.get(String(params.runId)) || workflow(records.find(t => t.runId === params.runId)!);
      return params.includeAudit
        ? { ...value, audit: { turns: [], continuations: [], requests: [], hostConclusions: [] }, routingHistory: { entries: [], nextCursor: null, total: 0 } }
        : value;
    }
    if (operation === "message_list") return { messages: [], cursor: 0 };
    throw new Error(`Unexpected write: ${operation}`);
  });
  const roots = records.filter(t => t.lineage?.kind === "micro-task");
  const api = { runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(snapshot)), command,
    task: vi.fn(async (runId: string) => records.find(t => t.runId === runId)),
    macroTasks: vi.fn(async () => ({ macroTasks: roots.map(t => macroTaskSummary({ macroTaskId: `run:${t.runId}`, title: t.task, titleSource: "task", project: t.lineage?.project ?? null })),
      totals: { all: roots.length, active: roots.length, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })),
    macroTaskTimeline: vi.fn(async (macroTaskId: string) => ({ timeline: timelineOfRecords(macroTaskId, records), verifiedAtMs: null })) } as unknown as ConsoleApi;
  return { snapshot, records, api, command, workflows };
}

afterEach(() => { cleanup(); window.history.replaceState(null, "", "#"); });

describe("desktop console", () => {
  it("removes the header refresh and displays the actual snapshot verification time", async () => {
    const f = fixture();
    f.api.readVerifiedAt = () => Date.parse("2026-01-02T03:04:05Z");
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "还没有宏任务" });
    expect(screen.queryByRole("button", { name: "刷新工作台" })).toBeNull();
    // A read from an earlier day is written with its date; the top bar holds the only copy.
    const read = within(screen.getByRole("banner")).getByText(formatDateTime(Date.parse("2026-01-02T03:04:05Z")));
    expect(read.getAttribute("datetime")).toBe("2026-01-02T03:04:05.000Z");
    expect(read.parentElement!.textContent).toContain(`核对时间 ${read.textContent}`);
    expect(within(screen.getByRole("main")).queryByText(/黑板快照/)).toBeNull();
    expect(screen.queryByText("已连接")).toBeNull();
  });

  it("retries a disconnected snapshot immediately without broadcasting other reads", async () => {
    const f = fixture();
    vi.useFakeTimers();
    try {
      render(<App suppliedApi={f.api} />);
      await act(async () => { await vi.advanceTimersByTimeAsync(200); });
      expect(within(screen.getByRole("banner")).getByText("核对时间未记录")).toBeTruthy();
      (f.api.snapshot as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("合成连接中断"));
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(screen.getByRole("button", { name: "重试连接" })).toBeTruthy();
      const snapshotCount = (f.api.snapshot as ReturnType<typeof vi.fn>).mock.calls.length;
      const macroTaskCount = (f.api.macroTasks as ReturnType<typeof vi.fn>).mock.calls.length;
      await act(async () => { screen.getByRole("button", { name: "重试连接" }).click(); });
      expect((f.api.snapshot as ReturnType<typeof vi.fn>).mock.calls.length).toBe(snapshotCount + 1);
      expect((f.api.macroTasks as ReturnType<typeof vi.fn>).mock.calls.length).toBe(macroTaskCount);
      expect(f.command).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "重试连接" })).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it("shows the server's board-wide pending count in the top bar, never a count inferred from rows", async () => {
    const f = fixture();
    // The count comes from the slim snapshot's server-side field.
    f.snapshot.tasks.pendingCount = 5;
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "还没有宏任务" });
    // The count and what it counts are both part of the tab's name.
    expect(screen.getByRole("link", { name: /^任务\s*5\s*等待 Host 决定的微任务$/ })).toBeTruthy();
    f.snapshot.tasks.pendingCount = 0;
    await waitFor(() => expect(screen.getByRole("link", { name: "任务" })).toBeTruthy(), { timeout: 5000 });
  });

  it("uses the exact top navigation and presents the 22 native buddies as six rows of models", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    const navigation = await screen.findByRole("navigation", { name: "主要导航" });
    // A mark on a tab adds words for a screen reader after its name; the name itself is the first text.
    expect(within(navigation).getAllByRole("link").map(link => link.firstChild?.textContent)).toEqual(["任务", "Buddy", "设置"]);
    expect(navigation.closest("header")).not.toBeNull();
    expect(document.querySelector(".sidebar")).toBeNull();
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    await user.click(within(navigation).getByRole("link", { name: /^Buddy/ }));
    await listed(4, 22);
    expect(within(catalogue()).getAllByRole("button").filter(button => button.closest("[data-slot=model-row]") && button.hasAttribute("aria-expanded"))).toHaveLength(6);
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    await selectModel(user, dsh("deepseek-flash"), "DSH");
    const detail = pane(dsh("deepseek-flash"));
    // Each effort selects its buddy; only that buddy has an editable panel.
    expect(levelSwitch("off")).toBeTruthy();
    await user.click(effortCell("low"));
    expect(levelSwitch("low")).toBeTruthy();
    expect(within(detail).queryByRole("switch", { name: "启用 off" })).toBeNull();
    expect(within(detail).queryByRole("checkbox")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps one note draft per model across rows and main pages without a lease", async () => {
    const f = fixture();
    window.location.hash = "#buddies";
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await listed(4, 22);
    await selectModel(user, dsh("deepseek-flash"), "DSH");
    await user.type(note(), "flash 的备注");
    await user.click(modelButton(dsh("deepseek-v4-pro")));
    expect(note()).toHaveProperty("value", "");
    await user.type(note(), "pro 的备注");
    await user.click(modelButton(GLM));
    expect(note()).toHaveProperty("value", "");
    await user.type(note(), "ZCode 的备注");
    await user.click(screen.getByRole("link", { name: "设置" }));
    expect(screen.queryByRole("textbox", { name: "备注" })).toBeNull();
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
    expect(note()).toHaveProperty("value", "ZCode 的备注");
    await user.click(modelButton(dsh("deepseek-flash")));
    expect(note()).toHaveProperty("value", "flash 的备注");
    await user.click(modelButton(dsh("deepseek-v4-pro")));
    expect(note()).toHaveProperty("value", "pro 的备注");
    // Snapshot polling keeps the draft without publishing it.
    await waitFor(() => expect((f.api.snapshot as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(1), { timeout: 4500 });
    expect(note()).toHaveProperty("value", "pro 的备注");
    await user.click(await screen.findByRole("button", { name: "放弃" }));
    expect(f.command).not.toHaveBeenCalled();
  });

  it("opens a micro task from its macro task and keeps its detail read-only across pages", async () => {
    const record = microTask("one");
    record.workflow = { ...record.workflow!, state: "awaiting-host", awaitingHost: true };
    const f = fixture([record]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: /目标 one/ }));
    await waitFor(() => expect(window.location.hash).toBe("#tasks/run%3Aone"));
    await waitFor(() => expect(document.querySelector('[data-key="row:one"]')).toBeTruthy());
    await user.dblClick(document.querySelector('[data-key="row:one"]') as HTMLElement);
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    await screen.findByRole("group", { name: "这个微任务的各步" });
    expect(within(detail).getByRole("heading", { name: /目标 one/ })).toBeTruthy();
    expect(detail.querySelectorAll("input, textarea, select, form")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "批准所列协助" })).toBeNull();
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
    await user.click(screen.getByRole("link", { name: /^任务/ }));
    expect(await screen.findByRole("region", { name: "微任务详情" })).toBeTruthy();
    expect(f.command.mock.calls.every(([operation]) => ["workflow_get", "message_list"].includes(operation as string))).toBe(true);
  });
});
