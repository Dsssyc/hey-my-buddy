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
  const snapshot: Snapshot = { csrfToken: "csrf", tableRevision: 2,
    gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
    configuration: { revision: 1, decisionProfileId: profiles[0].profileId },
    profiles, cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, origin: "maintenance",
      summary: `原评价 ${p.model} ${p.effort}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    preferences: [], annotations: [], evidence: [], decisions: [], sampleCounts: { [profiles[0].profileId]: 4 },
    modelConcurrency: [],
    tasks: { runs: records, total: records.length },
    capabilities: { selection: false, maintenance: false, evaluationWriteGate: true } };
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing", tableRevision: 2,
    expiresAt: new Date(Date.now() + 120000).toISOString() };
  const workflows = new Map<string, Workflow>();
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    if (operation === "evaluation_write_begin") {
      snapshot.gate = { phase: "writing", readers: 0, waitingWriters: 0, writer: { ...grant, kind: "human" } };
      return grant;
    }
    if (operation === "workflow_get") return workflows.get(String(params.runId)) || workflow(snapshot.tasks.runs.find(t => t.runId === params.runId)!);
    throw new Error(`Unexpected write: ${operation}`);
  });
  const tasks = vi.fn(async (query: TaskQuery) => {
    const runs = snapshot.tasks.runs.filter(t => (!query.rootsOnly || t.delegation?.kind === "goal")
      && (!query.projectId || t.delegation?.project.id === query.projectId)
      && (!query.hostId || t.delegation?.sourceHostId === query.hostId)
      && (!query.query || t.task.includes(query.query)));
    return { runs: structuredClone(runs), total: runs.length, nextCursor: null };
  });
  const api = { snapshot: vi.fn(async () => structuredClone(snapshot)), tasks, command,
    task: vi.fn(async (runId: string) => snapshot.tasks.runs.find(t => t.runId === runId)) } as unknown as ConsoleApi;
  return { snapshot, api, tasks, command, workflows };
}

afterEach(() => { cleanup(); window.location.hash = ""; });

describe("desktop console", () => {
  it("uses the exact top navigation and presents the 22 native configurations as six read-only model families", async () => {
    const f = fixture();
    const user = userEvent.setup();
    const { container } = render(<App suppliedApi={f.api} />);
    const navigation = await screen.findByRole("navigation", { name: "主要导航" });
    expect(within(navigation).getAllByRole("link").map(link => link.textContent)).toEqual(["委派记录", "模型卡片", "路由配置"]);
    expect(navigation.closest("header")).not.toBeNull();
    expect(container.querySelector(".sidebar")).toBeNull();
    expect(screen.getAllByRole("navigation")).toHaveLength(1);
    await user.click(within(navigation).getByRole("link", { name: "模型卡片" }));
    expect(await screen.findByText("6 个模型 · 22 个执行配置")).toBeTruthy();
    const list = screen.getByRole("region", { name: "模型目录" });
    expect(list.querySelectorAll(".profile-row")).toHaveLength(6);
    await user.click(within(list).getByRole("button", { name: /^deepseek-flash/ }));
    const detail = screen.getByRole("complementary", { name: "评价卡片详情" });
    expect(within(within(detail).getByRole("tabpanel")).getByText("原评价 deepseek-flash off")).toBeTruthy();
    expect(within(detail).queryByRole("textbox", { name: "当前评价" })).toBeNull();
    expect(within(detail).getByRole("button", { name: /非思考，已启用，正在查看/ })).toBeTruthy();
    expect(within(detail).getByRole("button", { name: /low，未启用/ })).toBeTruthy();
    await user.click(within(detail).getByRole("tab", { name: "评价与意见" }));
    // The automatic assessment stays read-only; only the separate opinion is editable.
    expect(within(detail).getByText("自动评价（只读）")).toBeTruthy();
    expect(within(detail).getByText("由维护 Harness 依据证据发布")).toBeTruthy();
    expect(within(detail).queryByRole("textbox", { name: "当前评价" })).toBeNull();
    expect(within(detail).getAllByText("原评价 deepseek-flash off").length).toBeGreaterThan(0);
    await user.click(within(detail).getByRole("tab", { name: "证据" }));
    expect(within(detail).queryByRole("textbox", { name: "补充观察" })).toBeNull();
    expect(within(detail).queryByRole("checkbox")).toBeNull();
    expect(within(detail).getByText(/暂无评价证据/)).toBeTruthy();
    await user.click(within(detail).getByRole("tab", { name: "偏好与启用" }));
    expect(within(detail).queryByRole("textbox", { name: "补充观察" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps one opinion draft across efforts, models, detail tabs and main pages without a lease", async () => {
    const f = fixture();
    window.location.hash = "#models";
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("switch", { name: "编辑模式" }));
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.click(screen.getByRole("tab", { name: "评价与意见" }));
    await user.type(await screen.findByLabelText("我的意见"), "off 人工意见");
    await user.click(screen.getByRole("button", { name: /^max，已启用/ }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "");
    await user.type(screen.getByLabelText("我的意见"), "max 人工意见");
    await user.click(screen.getByRole("button", { name: /^GLM-5\.3(?!-Flash)/ }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "");
    await user.type(screen.getByLabelText("我的意见"), "ZCode 人工意见");
    await user.click(screen.getByRole("tab", { name: "偏好与启用" }));
    await user.selectOptions(screen.getByLabelText("用户偏好"), "prefer");
    await user.click(screen.getByRole("link", { name: "路由配置" }));
    expect(screen.queryByRole("textbox", { name: "我的意见" })).toBeNull();
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "ZCode 人工意见");
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "off 人工意见");
    await user.click(screen.getByRole("button", { name: /^max/ }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "max 人工意见");
    // A read-only refresh keeps the draft; only Save would take a lease.
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(screen.getByLabelText("我的意见")).toHaveProperty("value", "max 人工意见");
    // The automatic card stays program-owned in edit mode and cannot be typed into.
    expect(screen.getAllByText("原评价 deepseek-flash max").length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("当前评价")).toBeNull();
    await user.click(screen.getByRole("tab", { name: "证据" }));
    expect(screen.queryByRole("textbox", { name: "补充观察" })).toBeNull();
    expect(screen.queryByLabelText("作为卡片依据")).toBeNull();
    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await user.click(await screen.findByRole("button", { name: "放弃修改" }));
    await screen.findByText("已放弃未发布的修改。");
    expect(f.command).not.toHaveBeenCalled();
  });

  it("labels an unattributed historical card honestly instead of as an automatic assessment", async () => {
    const f = fixture();
    f.snapshot.cards = f.snapshot.cards.map(card => ({ ...card, origin: "unattributed" }));
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: /^deepseek-flash/ }));
    const detail = screen.getByRole("complementary", { name: "评价卡片详情" });
    await user.click(within(detail).getByRole("tab", { name: "评价与意见" }));
    // The card was not recorded as maintenance-published, so it is not called
    // an automatic assessment; it stays read-only either way.
    expect(within(detail).queryByText("自动评价（只读）")).toBeNull();
    expect(within(detail).getAllByText(/发布者未记录/).length).toBeGreaterThan(0);
    expect(within(detail).getAllByText("原评价 deepseek-flash off").length).toBeGreaterThan(0);
    expect(within(detail).queryByRole("textbox", { name: "当前评价" })).toBeNull();
    expect(screen.queryByLabelText("我的意见")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps history filters and scroll across main pages and uses backend source attribution", async () => {
    const root = goal("alpha"), other = goal("beta", "other-host", "other-project");
    const helper = { ...goal("helper"), delegation: { ...root.delegation!, kind: "helper" as const, parentRunId: "alpha", rootRunId: "alpha" } };
    const f = fixture([root, other, helper]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
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
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
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
    await screen.findByRole("button", { name: /目标 alpine/ });
    expect(screen.getByText("已加载 2 个委派目标")).toBeTruthy();
    await user.click(screen.getByLabelText("显示协助任务与内部执行"));
    expect(await screen.findByText("已加载 2 条执行记录")).toBeTruthy();
    expect(screen.queryByText("已加载 2 个委派目标")).toBeNull();
  });

  it("announces new records without inserting them or changing scroll until the user refreshes history", async () => {
    const f = fixture([goal("old")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("button", { name: /目标 old/ });
    await user.selectOptions(screen.getByLabelText("项目筛选"), "source-project");
    await screen.findByRole("button", { name: /目标 old/ });
    const scroll = screen.getByLabelText("委派条目");
    scroll.scrollTop = 240;
    f.snapshot.tasks.runs.unshift({ ...goal("outside", "another-host", "other-project"), createdAt: "2026-09-24T11:00:00Z" });
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(screen.queryByRole("button", { name: /条新记录/ })).toBeNull();
    f.snapshot.tasks.runs.unshift({ ...goal("new"), createdAt: "2026-09-24T11:00:00Z" });
    f.snapshot.tasks.total = 2;
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    const notice = await screen.findByRole("button", { name: "有 1 条新记录 · 回到最新" });
    expect(screen.queryByRole("button", { name: /目标 new/ })).toBeNull();
    expect(scroll.scrollTop).toBe(240);
    await user.click(notice);
    expect(await screen.findByRole("button", { name: /目标 new/ })).toBeTruthy();
    expect(scroll.scrollTop).toBe(0);
    expect(screen.queryByRole("button", { name: /目标 outside/ })).toBeNull();
  });

  it("announces the first matching record after an empty history read", async () => {
    const f = fixture();
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await waitFor(() => expect(f.tasks).toHaveBeenCalledTimes(1));
    await screen.findByRole("heading", { name: "没有匹配的委派" });
    f.snapshot.tasks.runs.push(goal("first"));
    f.snapshot.tasks.total = 1;
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    const notice = await screen.findByRole("button", { name: "有 1 条新记录 · 回到最新" });
    expect(screen.queryByRole("button", { name: /目标 first/ })).toBeNull();
    await user.click(notice);
    expect(await screen.findByRole("button", { name: /目标 first/ })).toBeTruthy();
  });

  it("retains each delegation's unsubmitted inputs when switching records and hiding the page", async () => {
    const f = fixture([goal("one"), goal("two")]);
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: /目标 one/ }));
    await user.type(await screen.findByLabelText("决定理由"), "one 的决定草稿");
    await user.type(screen.getByLabelText("交给下一回合的输入"), "one 的接续草稿");
    await user.click(screen.getByRole("button", { name: /目标 two/ }));
    expect(await screen.findByLabelText("决定理由")).toHaveProperty("value", "");
    await user.type(screen.getByLabelText("决定理由"), "two 的决定草稿");
    await user.click(screen.getByRole("link", { name: "模型卡片" }));
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    expect(screen.getByLabelText("决定理由")).toHaveProperty("value", "two 的决定草稿");
    await user.click(screen.getByRole("button", { name: /目标 one/ }));
    expect(await screen.findByLabelText("决定理由")).toHaveProperty("value", "one 的决定草稿");
    expect(screen.getByLabelText("交给下一回合的输入")).toHaveProperty("value", "one 的接续草稿");
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
    await user.click(await screen.findByRole("button", { name: /目标 historical/ }));
    const detail = screen.getByRole("complementary", { name: "任务详情" });
    await waitFor(() => expect(within(detail).getByRole("tab", { name: "产物与验收" }).getAttribute("aria-selected")).toBe("true"));
    expect(within(detail).getByRole("heading", { name: "目标 historical" })).toBeTruthy();
    expect(within(detail).getByText("委派方：codex-source")).toBeTruthy();
    await user.type(screen.getByLabelText("搜索委派"), "无匹配记录");
    await screen.findByRole("heading", { name: "没有匹配的委派" });
    expect(within(detail).getByRole("heading", { name: "目标 historical" })).toBeTruthy();
    expect(within(detail).getByText("委派方：codex-source")).toBeTruthy();
  });
});
