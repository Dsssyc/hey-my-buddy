import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Profile, Snapshot, Task, TaskQuery, WriterGrant } from "./types";
import type { Workflow } from "./workflow-types";

// The installed catalog contains four DSH families with four efforts each and
// two ZCode families with three each. Only the four recorded choices are enabled.
function catalog(): Profile[] {
  const enabled = new Set(["dsh:deepseek-flash:off", "dsh:deepseek-flash:max", "zcode:GLM-5.3:high", "zcode:GLM-5.3-Flash:max"]);
  return [
    ...["deepseek-flash", "deepseek-v4-flash", "deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]
      .flatMap(model => ["off", "low", "high", "max"].map(effort => ({ adapter: "dsh", provider: "deepseek-official", model, effort }))),
    ...["GLM-5.3", "GLM-5.3-Flash"].flatMap(model => ["low", "high", "max"]
      .map(effort => ({ adapter: "zcode", provider: "bigmodel-api", model, effort }))),
  ].map(p => ({ ...p, profileId: [p.adapter, p.provider, p.model, p.effort].join(":"), label: `${p.model} · ${p.effort}`,
    available: true, enabled: enabled.has([p.adapter, p.model, p.effort].join(":")), capabilities: [],
    contextWindow: null, source: "catalog:fixture", description: "" }));
}
function goal(runId: string, sourceHostId = "codex-source", projectId = "source-project"): Task {
  return { runId, task: `目标 ${runId}`, status: "queued", owner: "worker:unrelated-label", cwd: `/runtime/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-24T10:00:00Z", acceptedAt: null, acceptanceVerdict: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 1 },
    delegation: { kind: "goal", sourceHostId, currentHostId: "current-host", parentRunId: null, rootRunId: runId,
      project: { id: projectId, label: projectId, path: `/source/${projectId}` },
      configuration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" } } };
}
function workflow(task: Task): Workflow {
  return { governed: true, runId: task.runId, hostId: "current-host", ownerGeneration: 2, revision: 1,
    state: "awaiting-host", awaitingHost: true, waitReason: "assistance", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: task.cwd, kind: "worktree", access: "write", inputCommit: "input", manifestSha256: "hash" },
    currentTurn: null, activeRequest: { requestId: `request-${task.runId}`, kind: "assistance", state: "open",
      summary: `协助 ${task.runId}`, attempted: "已有检查", neededWork: ["补充测试"], expectedArtifacts: [], acceptance: "检查通过" },
    children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null, task };
}
function fixture(records: Task[] = []) {
  const profiles = catalog();
  // The board-wide awaiting-Host root count the slim snapshot carries; the
  // fixture computes it with the server's own dedup semantics.
  const pendingCount = new Set(records.filter(t => t.workflow?.awaitingHost)
    .map(t => t.delegation?.rootRunId || t.runId)).size;
  const snapshot: Snapshot = { csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
    configuration: { revision: 1, routerProfileIds: [profiles[0].profileId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles, cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, origin: "maintenance",
      summary: `原评价 ${p.model} ${p.effort}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], evidence: [], decisions: [], sampleCounts: { [profiles[0].profileId]: 4 },
    modelConcurrency: [],
    tasks: { pendingCount },
    capabilities: { selection: false, maintenance: false, evaluationWriteGate: true } };
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing", tableRevision: 2,
    expiresAt: new Date(Date.now() + 120000).toISOString() };
  const workflows = new Map<string, Workflow>();
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "evaluation_write_begin") {
      snapshot.gate = { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } };
      return grant;
    }
    if (operation === "workflow_get") return workflows.get(String(params.runId)) || workflow(records.find(t => t.runId === params.runId)!);
    throw new Error(`Unexpected write: ${operation}`);
  });
  const tasks = vi.fn(async (query: TaskQuery) => {
    const runs = records.filter(t => (!query.rootsOnly || t.delegation?.kind === "goal")
      && (!query.projectId || t.delegation?.project.id === query.projectId)
      && (!query.hostId || t.delegation?.sourceHostId === query.hostId)
      && (!query.query || t.task.includes(query.query)));
    return { runs: structuredClone(runs), total: runs.length, nextCursor: null };
  });
  const api = { snapshot: vi.fn(async () => structuredClone(snapshot)), tasks, command,
    task: vi.fn(async (runId: string) => records.find(t => t.runId === runId)),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })) } as unknown as ConsoleApi;
  return { snapshot, records, api, tasks, command, workflows };
}

afterEach(() => { cleanup(); window.location.hash = ""; });

describe("desktop console", () => {
  it("uses one header refresh for the visible objective list and reports completion", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    expect(await screen.findByRole("link", { name: "Hey my buddy" })).toHaveProperty("textContent", "Hey my buddy");
    expect(screen.queryByText("已连接")).toBeNull();
    await waitFor(() => expect(f.api.objectives).toHaveBeenCalled());
    const before = (f.api.objectives as ReturnType<typeof vi.fn>).mock.calls.length;
    expect(screen.queryByRole("button", { name: "刷新" })).toBeNull();
    const button = screen.getByRole("button", { name: "刷新工作台" });
    await user.click(button);
    await waitFor(() => expect((f.api.objectives as ReturnType<typeof vi.fn>).mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(button.getAttribute("title")).toContain("已刷新 ·"));
  });

  it("reports a visible objective read failure in the header refresh tooltip", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "还没有工作目标" });
    (f.api.objectives as ReturnType<typeof vi.fn>).mockRejectedValueOnce(new Error("合成读取失败"));
    const button = screen.getByRole("button", { name: "刷新工作台" });
    await user.click(button);
    await waitFor(() => expect(button.getAttribute("title")).toContain("刷新失败：合成读取失败"));
  });

  it("shows the server's board-wide pending count in the top bar, never a count inferred from rows", async () => {
    const f = fixture([goal("alpha")]);
    // The fixture's rows contain no awaiting-Host goal at all; the count only
    // comes from the slim snapshot's server-side field.
    f.snapshot.tasks.pendingCount = 5;
    const { container } = render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "还没有工作目标" });
    const badge = container.querySelector(".nav-count");
    expect(badge?.textContent).toBe("5");
    expect(badge?.getAttribute("title")).toBe("全部委派中等待 Host 决定的目标");
    f.snapshot.tasks.pendingCount = 0;
    await waitFor(() => expect(container.querySelector(".nav-count")).toBeNull(), { timeout: 5000 });
  });

  it("uses the exact top navigation and presents the 22 native configurations as six read-only model families", async () => {
    const f = fixture();
    const user = userEvent.setup();
    const { container } = render(<App suppliedApi={f.api} />);
    const navigation = await screen.findByRole("navigation", { name: "主要导航" });
    expect(within(navigation).getAllByRole("link").map(link => link.textContent)).toEqual(["委派记录", "Buddy 配置", "设置"]);
    expect(navigation.closest("header")).not.toBeNull();
    expect(container.querySelector(".sidebar")).toBeNull();
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    await user.click(within(navigation).getByRole("link", { name: "Buddy 配置" }));
    expect(await screen.findByRole("heading", { name: "模型 6" })).toBeTruthy();
    const list = screen.getByRole("region", { name: "模型家族" });
    expect(list.querySelectorAll(".family-row")).toHaveLength(6);
    await user.click(within(list).getByRole("button", { name: /^deepseek-flash/ }));
    const detail = screen.getByRole("complementary", { name: "模型家族详情" });
    // The automatic assessment stays read-only; there is no editable opinion field.
    expect(within(detail).getAllByText("原评价 deepseek-flash off").length).toBeGreaterThan(0);
    expect(within(detail).queryByRole("textbox", { name: "当前评价" })).toBeNull();
    expect(within(detail).getByRole("switch", { name: "启用 off" })).toBeTruthy();
    expect(within(detail).getByRole("switch", { name: "启用 low" })).toBeTruthy();
    expect(within(detail).getAllByText(/由维护 Harness 依据证据发布/).length).toBeGreaterThan(0);
    expect(within(detail).queryByRole("textbox", { name: "补充观察" })).toBeNull();
    expect(within(detail).queryByRole("checkbox")).toBeNull();
    expect(within(detail).getAllByText(/评价证据未记录/).length).toBeGreaterThan(0);
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps one note draft per family across models, models detail and main pages without a lease", async () => {
    const f = fixture();
    window.location.hash = "#models";
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "flash 家族备注");
    await user.click(screen.getByRole("button", { name: /^deepseek-v4-pro/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "");
    await user.type(screen.getByLabelText("家族备注"), "pro 家族备注");
    await user.click(screen.getByRole("button", { name: /^GLM-5\.3(?!-Flash)/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "");
    await user.type(screen.getByLabelText("家族备注"), "ZCode 家族备注");
    await user.click(screen.getByRole("link", { name: "设置" }));
    expect(screen.queryByRole("textbox", { name: "家族备注" })).toBeNull();
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "ZCode 家族备注");
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "flash 家族备注");
    await user.click(screen.getByRole("button", { name: /^deepseek-v4-pro/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "pro 家族备注");
    // A read-only refresh keeps the draft; only Save would take a lease.
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "pro 家族备注");
    // The automatic card stays program-owned and cannot be typed into.
    expect(screen.getAllByText(/原评价 deepseek-v4-pro/).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("当前评价")).toBeNull();
    expect(screen.queryByRole("textbox", { name: "补充观察" })).toBeNull();
    expect(screen.queryByLabelText("作为卡片依据")).toBeNull();
    await user.click(await screen.findByRole("button", { name: "放弃" }));
    expect(f.command).not.toHaveBeenCalled();
  });

  it("labels an unattributed historical card honestly instead of as an automatic assessment", async () => {
    const f = fixture();
    f.snapshot.cards = f.snapshot.cards.map(card => ({ ...card, origin: "unattributed" }));
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: /^deepseek-flash/ }));
    const detail = screen.getByRole("complementary", { name: "模型家族详情" });
    // The card was not recorded as maintenance-published, so it is not called
    // an automatic assessment; it stays read-only either way.
    expect(within(detail).getAllByText(/发布者未记录/).length).toBeGreaterThan(0);
    expect(within(detail).getAllByText("原评价 deepseek-flash off").length).toBeGreaterThan(0);
    expect(within(detail).queryByRole("textbox", { name: "当前评价" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps history filters and scroll across main pages and uses backend source attribution", async () => {
    const root = goal("alpha"), other = goal("beta", "other-host", "other-project");
    const helper = { ...goal("helper"), delegation: { ...root.delegation!, kind: "helper" as const, parentRunId: "alpha", rootRunId: "alpha" } };
    const f = fixture([root, other, helper]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await screen.findByRole("button", { name: /目标 alpha/ });
    expect(f.tasks.mock.calls[0][0]).toMatchObject({ rootsOnly: true });
    expect(screen.queryByRole("button", { name: /目标 helper/ })).toBeNull();
    expect(screen.getAllByText(/codex-source → dsh/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/worker:unrelated-label/)).toBeNull();
    await user.type(screen.getByLabelText("搜索委派"), "目标");
    await user.selectOptions(screen.getByLabelText("项目筛选"), "source-project");
    await user.selectOptions(screen.getByLabelText("委派方筛选"), "codex-source");
    await waitFor(() => expect(f.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ rootsOnly: true,
      query: "目标", projectId: "source-project", hostId: "codex-source" }), expect.any(AbortSignal)));
    const scroll = screen.getByLabelText("委派条目");
    scroll.scrollTop = 280;
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    expect(screen.getByLabelText("搜索委派")).toHaveProperty("value", "目标");
    expect(screen.getByLabelText("项目筛选")).toHaveProperty("value", "source-project");
    expect(screen.getByLabelText("委派方筛选")).toHaveProperty("value", "codex-source");
    expect(screen.getByLabelText("委派条目")).toHaveProperty("scrollTop", 280);
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    await user.click(await screen.findByRole("button", { name: /目标 helper/ }));
    expect(f.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ rootsOnly: false, projectId: "source-project", hostId: "codex-source" }), expect.any(AbortSignal));
    await user.click(await screen.findByRole("tab", { name: "执行记录" }));
    const detail = screen.getByRole("complementary", { name: "任务详情" });
    expect(within(detail).getByText("/source/source-project")).toBeTruthy();
    expect(within(detail).getByText("alpha")).toBeTruthy();
  });

  it("labels each project group count explicitly for goals and internal records", async () => {
    const f = fixture([goal("alpha"), goal("alpine")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await screen.findByRole("button", { name: /目标 alpine/ });
    expect(screen.getByText("已加载 2 个委派目标")).toBeTruthy();
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByText("已加载 2 条执行记录")).toBeTruthy();
    expect(screen.queryByText("已加载 2 个委派目标")).toBeNull();
  });

  it("reloads the visible filtered history when the global refresh is pressed", async () => {
    const f = fixture([goal("old")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await screen.findByRole("button", { name: /目标 old/ });
    await user.selectOptions(screen.getByLabelText("项目筛选"), "source-project");
    await screen.findByRole("button", { name: /目标 old/ });
    const scroll = screen.getByLabelText("委派条目");
    scroll.scrollTop = 240;
    f.records.unshift({ ...goal("outside", "another-host", "other-project"), createdAt: "2026-09-24T11:00:00Z" });
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    await waitFor(() => expect((screen.getByRole("button", { name: "刷新工作台" }) as HTMLButtonElement).disabled).toBe(false));
    expect(screen.queryByRole("button", { name: /条新记录/ })).toBeNull();
    f.records.unshift({ ...goal("new"), createdAt: "2026-09-24T11:00:00Z" });
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(await screen.findByRole("button", { name: /目标 new/ })).toBeTruthy();
    expect(scroll.scrollTop).toBe(240);
    expect(screen.queryByRole("button", { name: /目标 outside/ })).toBeNull();
  });

  it("loads the first matching record from the global refresh after an empty history read", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await waitFor(() => expect(f.tasks).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: "没有匹配的委派" });
    f.records.push(goal("first"));
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(await screen.findByRole("button", { name: /目标 first/ })).toBeTruthy();
  });

  it("keeps record details read-only: no delegation drafts, no writes when switching records or pages", async () => {
    const f = fixture([goal("one"), goal("two")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 one/ }));
    await screen.findByRole("tab", { name: "协作与待办" });
    // The delegation detail exposes no Host input or write action (0.15.1 U4).
    for (const label of ["决定理由", "交给下一回合的输入", "实际检查依据"]) {
      expect(screen.queryByLabelText(label)).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "提交接续输入" })).toBeNull();
    expect(screen.queryByRole("button", { name: "批准所列协助" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /目标 two/ }));
    expect((await screen.findAllByText(/协助 two/)).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    expect((await screen.findAllByText(/协助 two/)).length).toBeGreaterThan(0);
    expect(f.command.mock.calls.every(([operation]) => operation === "workflow_get")).toBe(true);
  });

  it("refreshes an older record from a bounded workflow task summary without dropping its title or attribution", async () => {
    const record = goal("historical");
    const f = fixture();
    f.tasks.mockImplementation(async query => ({ runs: query.query ? [] : [structuredClone(record)],
      total: query.query ? 0 : 1, nextCursor: null }));
    const value = workflow(record);
    Object.assign(value, { revision: 7, state: "delivered", awaitingHost: false, activeRequest: null });
    value.task = { runId: record.runId, revision: 7, status: "completed", shutdownConfirmed: true };
    f.workflows.set(record.runId, value);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 historical/ }));
    const detail = screen.getByRole("complementary", { name: "任务详情" });
    await waitFor(() => expect(within(detail).getByRole("tab", { name: "产物与验收" }).getAttribute("aria-selected")).toBe("true"));
    expect(within(detail).getByRole("heading", { name: /目标 historical/ })).toBeTruthy();
    expect(within(detail).getByText("委派方：codex-source")).toBeTruthy();
    await user.type(screen.getByLabelText("搜索委派"), "无匹配记录");
    await screen.findByRole("heading", { name: "没有匹配的委派" });
    expect(within(detail).getByRole("heading", { name: /目标 historical/ })).toBeTruthy();
    expect(within(detail).getByText("委派方：codex-source")).toBeTruthy();
  });

  it("renders projected summaries without fetching a workflow for each list row", async () => {
    const first = goal("first-summary"), second = goal("second-summary");
    first.workflow!.resultSummary = "第一项的现有结果";
    second.workflow!.resultSummary = "第二项的现有结果";
    const f = fixture([first, second]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    // 0.16 T1: rows show the task intent, never the Worker summaries.
    expect(await screen.findByRole("button", { name: /目标 first-summary/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /目标 second-summary/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /第一项的现有结果/ })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
    expect(f.api.task).not.toHaveBeenCalled();
  });

  it("keeps the list row and detail title in step when the newest concluded result arrives", async () => {
    const record = goal("older");
    record.task = "目标 older\n不该成为标题";
    // The record exists only in paged history, outside the snapshot window.
    const f = fixture();
    f.tasks.mockImplementation(async query => ({ runs: query.query ? [] : [structuredClone(record)],
      total: query.query ? 0 : 1, nextCursor: null }));
    const value = workflow(record);
    Object.assign(value, { revision: 7, state: "delivered", awaitingHost: false, activeRequest: null });
    value.task = { runId: record.runId, revision: 7, status: "completed", shutdownConfirmed: true,
      task: record.task,
      workflow: { state: "delivered", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 7,
        resultSummary: "历史记录的最新结论结果" } };
    f.workflows.set(record.runId, value);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /目标 older/ }));
    const detail = screen.getByRole("complementary", { name: "任务详情" });
    // 0.16 T1: the bounded refresh carries the newest concluded summary, but
    // it never becomes the heading or the row title; it appears as a 结果 line.
    expect(await within(detail).findByRole("heading", { name: /目标 older/ })).toBeTruthy();
    const row = screen.getByRole("button", { name: /目标 older/ });
    expect(row.querySelector("strong.task-title")!.textContent).toContain("目标 older");
    expect(within(detail).getAllByText(/结果：历史记录的最新结论结果/).length).toBeGreaterThan(0);
    // The raw task text keeps its detail access through the disclosure.
    expect((await within(detail).findAllByText(/不该成为标题/)).length).toBeGreaterThan(0);
  });
});
