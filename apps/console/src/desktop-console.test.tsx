import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { formatDateTime } from "./i18n/format";
import type { Buddy, Snapshot, Task, TaskQuery } from "./types";
import { at, timelineOfRecords, turn as detailTurn, span as detailSpan } from "./task-detail/task-detail.fixtures";
import type { MacroTaskTimeline } from "./macro-task-types";
import { catalogue, effortCell, levelSwitch, listed, modelButton, note, pane, selectModel } from "./buddies/page.testing";

const dsh = (model: string) => `dsh / deepseek-official / ${model}`;
const GLM = "zcode / bigmodel-api / GLM-5.3";

// The installed catalog contains four DSH model services with four efforts each and
// two ZCode model services with three each. Only the four recorded choices are enabled.
function catalog(): Buddy[] {
  const enabled = new Set(["dsh:deepseek-flash:off", "dsh:deepseek-flash:max", "zcode:GLM-5.3:high", "zcode:GLM-5.3-Flash:max"]);
  return [
    ...["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]
      .flatMap(model => ["off", "low", "high", "max"].map(effort => ({ harness: "dsh", provider: "deepseek-official", model, effort }))),
    ...["GLM-5.3", "GLM-5.3-Flash"].flatMap(model => ["low", "high", "max"]
      .map(effort => ({ harness: "zcode", provider: "bigmodel-api", model, effort }))),
  ].map(p => ({ ...p, buddyId: [p.harness, p.provider, p.model, p.effort].join(":"), label: `${p.model} · ${p.effort}`,
    available: true, enabled: enabled.has([p.harness, p.model, p.effort].join(":")), capabilities: [],
    contextWindow: null, source: "catalog:fixture", description: "" }));
}
function microTask(runId: string, sourceHostId = "codex-source", projectId = "source-project"): Task {
  return { runId, task: `目标 ${runId}`, status: "queued", owner: "worker:unrelated-label", cwd: `/runtime/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-24T10:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null, reviewRejectionNote: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 1 },
    lineage: { kind: "micro-task", sourceHostId, currentHostId: "current-host", parentRunId: null, rootRunId: runId, rootLeadRunId: null,
      project: { id: projectId, label: projectId, path: `/source/${projectId}` },
      buddy: { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" } } };
}
/** The compact collaboration record of a micro task, as `workflow_get` sends it. */
type Workflow = Record<string, unknown>;
function workflow(task: Task): Workflow {
  return { runId: task.runId, hostId: "current-host", revision: 1, state: "awaiting-host", buddy: null, buddyLocked: false, delegationAuthority: false,
    routing: null, turns: [], currentTurn: null, requests: [], counts: { openRequests: 1 }, children: [], artifacts: [], integrations: [],
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
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },

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
  const tasks = vi.fn(async (query: TaskQuery) => {
    const runs = records.filter(t => (!query.rootsOnly || t.lineage?.kind === "micro-task")
      && (!query.projectId || t.lineage?.project.id === query.projectId)
      && (!query.hostId || t.lineage?.sourceHostId === query.hostId)
      && (!query.query || t.task.includes(query.query)));
    return { runs: structuredClone(runs), total: runs.length, nextCursor: null };
  });
  const api = { runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(snapshot)), tasks, command,
    task: vi.fn(async (runId: string) => records.find(t => t.runId === runId)),
    macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    macroTaskTimeline: vi.fn(async (macroTaskId: string) => ({ timeline: timelineOfRecords(macroTaskId, records), verifiedAtMs: null })) } as unknown as ConsoleApi;
  return { snapshot, records, api, tasks, command, workflows };
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
    expect(read.parentElement!.textContent).toBe(`核对时间 ${read.textContent}`);
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
      const taskCount = f.tasks.mock.calls.length;
      await act(async () => { screen.getByRole("button", { name: "重试连接" }).click(); });
      expect((f.api.snapshot as ReturnType<typeof vi.fn>).mock.calls.length).toBe(snapshotCount + 1);
      expect((f.api.macroTasks as ReturnType<typeof vi.fn>).mock.calls.length).toBe(macroTaskCount);
      expect(f.tasks.mock.calls.length).toBe(taskCount);
      expect(f.command).not.toHaveBeenCalled();
      expect(screen.queryByRole("button", { name: "重试连接" })).toBeNull();
    } finally { vi.useRealTimers(); }
  });

  it("shows the server's board-wide pending count in the top bar, never a count inferred from rows", async () => {
    const f = fixture([microTask("alpha")]);
    // The fixture's rows contain no awaiting-Host micro task at all; the count only
    // comes from the slim snapshot's server-side field.
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
    await user.click(screen.getByRole("link", { name: /Buddy/ }));
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

  it("keeps history filters and scroll across main pages and uses backend source attribution", async () => {
    const root = microTask("alpha"), other = microTask("beta", "other-host", "other-project");
    const helper = { ...microTask("helper"), lineage: { ...root.lineage!, kind: "helper" as const, parentRunId: "alpha", rootRunId: "alpha" } };
    const f = fixture([root, other, helper]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await screen.findByRole("button", { name: /目标 alpha/ });
    expect(f.tasks.mock.calls[0][0]).toMatchObject({ rootsOnly: true });
    expect(screen.queryByRole("button", { name: /目标 helper/ })).toBeNull();
    expect(screen.getAllByText(/codex-source → dsh/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/worker:unrelated-label/)).toBeNull();
    await user.type(screen.getByLabelText("搜索微任务"), "目标");
    await user.selectOptions(screen.getByLabelText("项目筛选"), "source-project");
    await user.selectOptions(screen.getByLabelText("委派方筛选"), "codex-source");
    await waitFor(() => expect(f.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ rootsOnly: true,
      query: "目标", projectId: "source-project", hostId: "codex-source" }), expect.any(AbortSignal)));
    const scroll = screen.getByLabelText("微任务条目");
    scroll.scrollTop = 280;
    await user.click(screen.getByRole("link", { name: /Buddy/ }));
    await user.click(screen.getByRole("link", { name: "任务" }));
    expect(screen.getByLabelText("搜索微任务")).toHaveProperty("value", "目标");
    expect(screen.getByLabelText("项目筛选")).toHaveProperty("value", "source-project");
    expect(screen.getByLabelText("委派方筛选")).toHaveProperty("value", "codex-source");
    expect(screen.getByLabelText("微任务条目")).toHaveProperty("scrollTop", 280);
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    await user.click(await screen.findByRole("button", { name: /目标 helper/ }));
    expect(f.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ rootsOnly: false, projectId: "source-project", hostId: "codex-source" }), expect.any(AbortSignal));
    // A helper has a collaboration record, so it opens in its macro task; its source and its parent are on the delegation step.
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    expect(window.location.hash).toMatch(/^#tasks\/run%3Aalpha\/helper(\/\d+)?$/);
    await within(detail).findByRole("navigation", { name: "步骤" });
    expect(within(detail).getByText("source-project").getAttribute("title")).toBe("/source/source-project");
    expect(within(detail).getByTitle("alpha").tagName).toBe("BUTTON");
  });

  it("labels each project group count explicitly for micro tasks and internal records", async () => {
    const f = fixture([microTask("alpha"), microTask("alpine")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await screen.findByRole("button", { name: /目标 alpine/ });
    expect(screen.getByText("已加载 2 个微任务")).toBeTruthy();
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByText("已加载 2 条执行记录")).toBeTruthy();
    expect(screen.queryByText("已加载 2 个微任务")).toBeNull();
  });

  it("reloads the visible filtered history when the local history refresh is pressed", async () => {
    const f = fixture([microTask("old")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await screen.findByRole("button", { name: /目标 old/ });
    await user.selectOptions(screen.getByLabelText("项目筛选"), "source-project");
    await screen.findByRole("button", { name: /目标 old/ });
    const scroll = screen.getByLabelText("微任务条目");
    scroll.scrollTop = 240;
    f.records.unshift({ ...microTask("outside", "another-host", "other-project"), createdAt: "2026-09-24T11:00:00Z" });
    await user.click(screen.getByRole("button", { name: "刷新全部执行记录" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "刷新全部执行记录" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("button", { name: /条新记录/ })).toBeNull();
    f.records.unshift({ ...microTask("new"), createdAt: "2026-09-24T11:00:00Z" });
    await user.click(screen.getByRole("button", { name: "刷新全部执行记录" }));
    expect(await screen.findByRole("button", { name: /条新记录 · 回到最新/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /目标 new/ })).toBeNull();
    expect(await screen.findByRole("button", { name: /目标 old/ })).toBeTruthy();
    expect(scroll.scrollTop).toBe(240);
    expect(screen.queryByRole("button", { name: /目标 outside/ })).toBeNull();
  });

  it("loads the first matching record from the local history refresh after an empty history read", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await waitFor(() => expect(f.tasks).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: "没有匹配的微任务" });
    f.records.push(microTask("first"));
    await user.click(screen.getByRole("button", { name: "刷新全部执行记录" }));
    expect(await screen.findByRole("button", { name: /目标 first/ })).toBeTruthy();
  });

  it("keeps record details read-only: no microTask drafts, no writes when switching records or pages", async () => {
    const waiting = (runId: string) => { const record = microTask(runId); record.workflow = { ...record.workflow!, state: "awaiting-host", awaitingHost: true }; return record; };
    const f = fixture([waiting("one"), waiting("two")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 one/ }));
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    await within(detail).findByRole("navigation", { name: "步骤" });
    // The micro task detail exposes no Host input or write action (0.15.1 U4).
    for (const label of ["决定理由", "交给下一回合的输入", "实际检查依据"]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    expect(detail.querySelectorAll("input, textarea, select, form")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "提交接续输入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准所列协助" })).toBeNull();
    await user.click(screen.getAllByRole("button", { name: "切换到全部执行记录" })[0]!);
    await user.click(screen.getByRole("button", { name: /目标 two/ }));
    expect((await screen.findAllByText(/协助 two/)).length).toBeGreaterThan(0);
    // Leaving the page and coming back leads to the same micro task, not to the first screen.
    await user.click(screen.getByRole("link", { name: /Buddy/ }));
    await user.click(screen.getByRole("link", { name: /^任务/ }));
    expect((await screen.findAllByText(/协助 two/)).length).toBeGreaterThan(0);
    expect(window.location.hash).toMatch(/^#tasks\/run%3Atwo\/two/);
    expect(f.command.mock.calls.every(([operation]) => ["workflow_get", "message_list"].includes(operation as string))).toBe(true);
  });

  it("refreshes an older record from a bounded workflow task summary without dropping its title or attribution", async () => {
    const record = microTask("historical");
    const f = fixture([record]);
    f.tasks.mockImplementation(async query => ({ runs: query.query ? [] : [structuredClone(record)],
      total: query.query ? 0 : 1, nextCursor: null }));
    const value = workflow(record);
    Object.assign(value, { revision: 7, state: "delivered", awaitingHost: false, activeRequest: null });
    value.task = { runId: record.runId, revision: 7, status: "completed", shutdownConfirmed: true, lineage: record.lineage };
    f.workflows.set(record.runId, value);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 historical/ }));
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    await within(detail).findByRole("navigation", { name: "步骤" });
    expect(within(detail).getByRole("heading", { name: /目标 historical/ })).toBeTruthy();
    expect(within(detail).getByText("原始委派方", { selector: "dt" }).nextElementSibling!.textContent).toBe("codex-source");
    // The list that found the record can lose it; the detail it opened stays, with its title and its source.
    await user.click(screen.getAllByRole("button", { name: "切换到全部执行记录" })[0]!);
    await user.type(screen.getByLabelText("搜索微任务"), "无匹配记录");
    await screen.findByRole("heading", { name: "没有匹配的微任务" });
    await user.click(screen.getAllByRole("button", { name: "切换到宏任务" })[0]!);
    const kept = await screen.findByRole("region", { name: "微任务详情" });
    expect(within(kept).getByRole("heading", { name: /目标 historical/ })).toBeTruthy();
    expect(within(kept).getByText("原始委派方", { selector: "dt" }).nextElementSibling!.textContent).toBe("codex-source");
  });

  it("renders projected summaries without fetching a workflow for each list row", async () => {
    const first = microTask("first-summary"), second = microTask("second-summary");
    first.workflow!.resultSummary = "第一项的现有结果";
    second.workflow!.resultSummary = "第二项的现有结果";
    const f = fixture([first, second]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    // 0.16 T1: rows show the task intent, never the Worker summaries.
    expect(await screen.findByRole("button", { name: /目标 first-summary/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /目标 second-summary/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /第一项的现有结果/ })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
    expect(f.api.task).not.toHaveBeenCalled();
  });

  it("keeps the list row and detail title in step when the newest returned result arrives", async () => {
    const record = microTask("older");
    record.task = "目标 older\n不该成为标题";
    // The record exists only in paged history, outside the snapshot window.
    const f = fixture([record]);
    f.tasks.mockImplementation(async query => ({ runs: query.query ? [] : [structuredClone(record)],
      total: query.query ? 0 : 1, nextCursor: null }));
    const value = workflow(record);
    const returned = { ...detailTurn(1, { attemptId: "att-older", turnId: "turn-older" }), summary: "历史记录的最新结论结果", summaryTruncated: false, remaining: [] };
    Object.assign(value, { revision: 7, state: "delivered", activeRequest: null, counts: { openRequests: 0 }, turns: [returned], currentTurn: returned });
    value.task = { runId: record.runId, revision: 7, status: "completed", shutdownConfirmed: true, lineage: record.lineage,
      task: record.task,
      workflow: { state: "delivered", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 7,
        resultSummary: "历史记录的最新结论结果" } };
    f.workflows.set(record.runId, value);
    // The timeline holds the turn the result belongs to.
    (f.api.macroTaskTimeline as ReturnType<typeof vi.fn>).mockImplementation(async (macroTaskId: string): Promise<{ timeline: MacroTaskTimeline; verifiedAtMs: null }> => {
      const base = timelineOfRecords(macroTaskId, [record]);
      return { timeline: { ...base, spans: [detailSpan("e1", "execution", at("10:02"), at("10:30"), { runId: record.runId, turnIndex: 1, attemptId: "att-older", turnId: "turn-older",
        shutdownConfirmed: true, resultStatus: "ok" })] }, verifiedAtMs: null };
    });
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "切换到全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 older/ }));
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    // 0.16 T1: the newest returned summary never becomes the heading or the row title; it is the result of the turn.
    expect(await within(detail).findByRole("heading", { name: /目标 older/ })).toBeTruthy();
    expect(within(detail).getByRole("heading", { name: /目标 older/ }).textContent).not.toContain("历史记录的最新结论结果");
    await waitFor(() => expect(detail.textContent).toContain("历史记录的最新结论结果"));
    await user.click(screen.getAllByRole("button", { name: "切换到全部执行记录" })[0]!);
    const row = screen.getByRole("button", { name: /目标 older/ });
    expect(row.querySelector("strong.task-title")!.textContent).toContain("目标 older");
    // The raw task text keeps its place on the delegation step.
    await user.click(screen.getAllByRole("button", { name: "切换到宏任务" })[0]!);
    await user.click(within(await screen.findByRole("navigation", { name: "步骤" })).getByRole("button", { name: /^委派/ }));
    expect((await screen.findAllByText(/不该成为标题/)).length).toBeGreaterThan(0);
  });
});
