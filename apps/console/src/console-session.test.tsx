import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, createApi, errorText, isSessionExpiredRefusal } from "./api";
import type { ConsoleApi } from "./api";
import { createAuthorityLatch, sessionCanWrite } from "./console-session";
import { QUEUE_POLL_MS } from "./use-editor";
import type { ConsoleSession, Profile, Snapshot, Task, TaskQuery, WriterGrant } from "./types";
import type { Workflow } from "./workflow-types";

const bannerNotice = /新窗口已取得写权限，此页面现在是只读/;
const actionRefusal = /登录已失效/;
const saveRefusal = /登录已失效/;

/** Two families and three execution configurations, all available. */
function catalog(): Profile[] {
  const entries = [
    { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off", enabled: true },
    { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max", enabled: true },
    { adapter: "zcode", provider: "bigmodel-api", model: "GLM-5.3", effort: "high", enabled: false },
  ];
  return entries.map(p => ({ ...p, profileId: [p.adapter, p.provider, p.model, p.effort].join(":"),
    label: `${p.model} · ${p.effort}`, available: true, capabilities: [], contextWindow: null,
    source: "catalog:fixture", description: "" }));
}
const flashOff = "dsh:deepseek-official:deepseek-flash:off";

function goal(runId: string, task: string, extra: Partial<Task> = {}): Task {
  return { runId, task, status: "queued", owner: "worker:fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-24T10:00:00Z", acceptedAt: null, acceptanceVerdict: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 1 },
    delegation: { kind: "goal", sourceHostId: "codex-source", currentHostId: "current-host", parentRunId: null, rootRunId: runId,
      project: { id: "fixture-project", label: "fixture-project", path: "/source/fixture-project" },
      configuration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" } },
    ...extra };
}

/** A finished ungoverned task with no claimed attempt: retry is offered. */
function retryable(runId: string, task: string): Task {
  return goal(runId, task, { status: "cancelled", revision: 2, workflow: undefined,
    selectedAttemptId: null, activeAttemptId: null, shutdownConfirmed: false });
}

function workflow(task: Task, overrides: Partial<Workflow> = {}): Workflow {
  return { governed: true, runId: task.runId, hostId: "current-host", ownerGeneration: 2, revision: 1,
    state: "awaiting-host", awaitingHost: true, waitReason: "assistance", continuationCount: 0,
    shutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false },
    workspace: { path: task.cwd, kind: "worktree", access: "write", inputCommit: "input", manifestSha256: "hash" },
    currentTurn: null,
    activeRequest: { requestId: `request-${task.runId}`, kind: "assistance", state: "open",
      summary: `协助 ${task.runId}`, attempted: "已有检查", neededWork: ["补充测试"], expectedArtifacts: [], acceptance: "检查通过" },
    children: [], artifacts: [], integrations: [], finalArtifactId: null, finalAttemptId: null,
    task: { runId: task.runId, revision: task.revision, status: task.status },
    ...overrides };
}

const session = (): ConsoleSession => ({ id: "session-writer", canWrite: true, reason: null });

type IdentityOutcome = "expired";
type PublishOutcome = "ok" | "network" | IdentityOutcome;
type Script = {
  begin?: "active" | "queued" | IdentityOutcome;
  renew?: "active" | "queued" | IdentityOutcome;
  publish?: PublishOutcome | PublishOutcome[];
};

function fixture(options: {
  script?: Script; records?: Task[]; workflows?: Map<string, Workflow>; failAfter?: number;
  failWith?: unknown; queuedForever?: boolean;
} = {}) {
  const profiles = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", consoleSession: session(), tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: flashOff },
    profiles,
    unavailableProfileCount: 1,
    cards: profiles.map(p => ({ profileId: p.profileId, revision: 2, summary: `原评价 ${p.model} ${p.effort}`,
      strengths: [], limitations: [], risks: [], evidenceIds: [], updatedAt: null })),
    familyAnnotations: [{ adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "原人工意见", revision: 1, updatedAt: null }],
    preferences: [], familyPreferences: [], preferenceOverrides: [], evidence: [], decisions: [], sampleCounts: { [flashOff]: 4 },
    modelConcurrency: [{ adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 }],
    tasks: { runs: options.records ?? [], total: (options.records ?? []).length },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  const workflows = options.workflows ?? new Map<string, Workflow>();
  const grant: WriterGrant = { writerId: "writer", generation: 1, writerToken: "private", phase: "writing",
    state: "active", tableRevision: 2, expiresAt: new Date(Date.now() + 120000).toISOString() };
  const operations: string[] = [];
  const published: Record<string, unknown>[] = [];
  let snapshots = 0;
  const publishQueue: PublishOutcome[] | null = Array.isArray(options.script?.publish)
    ? [...options.script!.publish!]
    : null;
  const nextPublish = (): PublishOutcome | undefined => publishQueue
    ? publishQueue.shift()
    : options.script?.publish as PublishOutcome | undefined;
  const command = vi.fn(async (operation: string, params: Record<string, unknown>) => {
    operations.push(operation);
    const scripted = options.script ?? {};
    if (operation === "evaluation_write_begin") {
      if (scripted.begin === "expired") throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      if (scripted.begin === "queued") return { ...grant, state: "waiting", phase: "draining", queuePosition: 2 };
      return grant;
    }
    if (operation === "evaluation_write_renew") {
      if (scripted.renew === "expired") throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      if (options.queuedForever || scripted.renew === "queued")
        return { ...grant, state: "waiting", phase: "draining", queuePosition: 2 };
      return { ...grant, state: "active", phase: "writing" };
    }
    if (operation === "user_policy_publish") {
      published.push(params);
      const outcome = nextPublish();
      if (outcome === "network") throw new ApiError("NETWORK", "lost publish reply");
      if (outcome === "expired") throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      return { published: true, tableRevision: 3 };
    }
    if (operation === "evaluation_write_abort") return { aborted: true };
    if (operation === "workflow_get") {
      const value = workflows.get(String(params.runId));
      if (!value) throw new Error(`Unknown run: ${String(params.runId)}`);
      return structuredClone(value);
    }
    if (operation === "evaluation_history") {
      return { revisions: [{ revision: 12, kind: "human", actor: "console",
        counts: { annotationChanges: 1 }, createdAt: "2026-09-25T10:00:00Z" }], nextCursor: null, total: 1 };
    }
    if (operation === "model_profiles") {
      return { profiles: [], cards: [], familyAnnotations: [], preferences: [], preferenceOverrides: [], familyPreferences: [], modelConcurrency: [],
        sampleCounts: {}, tableRevision: state.tableRevision, nextCursor: null };
    }
    if (operation === "selection_get") {
      return { decision: { decisionId: String(params.decisionId), kind: "select", status: "completed",
        task: "路由计算", profileId: flashOff, tableRevision: 1, reason: "记录的选择依据",
        evidence: [], createdAt: "2026-09-25T10:00:00Z" } };
    }
    throw new ApiError("FORBIDDEN", "board refused a mutation");
  });
  const tasks = vi.fn(async (query: TaskQuery) => {
    const runs = state.tasks.runs.filter(row => (!query.rootsOnly || row.delegation?.kind === "goal")
      && (!query.query || row.task.includes(query.query)));
    return { runs: structuredClone(runs), total: runs.length, nextCursor: null };
  });
  let loginExpired = false;
  const snapshot = vi.fn(async () => {
    snapshots += 1;
    if (loginExpired) throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
    if (options.failAfter !== undefined && snapshots > options.failAfter) {
      throw options.failWith ?? new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。");
    }
    return structuredClone(state);
  });
  const api = { snapshot, tasks, command, task: vi.fn(async (runId: string) => state.tasks.runs.find(t => t.runId === runId)),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })) } as unknown as ConsoleApi;
  return { api, command, operations, published, snapshot, tasks,
    state: () => state,
    expireSession: () => { loginExpired = true; } };
}

afterEach(() => { cleanup(); window.location.hash = ""; document.documentElement.dataset.theme = ""; });
/** Builds one authenticated-snapshot envelope; `null` leaves the field out. */
function envelope(consoleSession: unknown, include = true) {
  return {
    tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    profiles: [], cards: [], familyAnnotations: [], preferences: [], familyPreferences: [], preferenceOverrides: [], modelConcurrency: [], tasks: { runs: [] },
    csrfToken: "csrf",
    ...(include ? { consoleSession } : {}),
  };
}
function apiFor(payload: unknown) {
  return createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
}

describe("console session descriptor validation", () => {
  it("accepts only the live service descriptor: canWrite true with a null reason", async () => {
    await expect(apiFor(envelope({ id: "session-a", canWrite: true, reason: null })).snapshot())
      .resolves.toMatchObject({ consoleSession: { id: "session-a", canWrite: true, reason: null } });
    // 0.16: a non-writable or flagged descriptor is no longer a session state;
    // the board reports an expired login as a 401 refusal instead.
    await expect(apiFor(envelope({ id: "session-b", canWrite: false, reason: null })).snapshot())
      .rejects.toBeInstanceOf(ApiError);
    await expect(apiFor(envelope({ id: "session-c", canWrite: true, reason: "superseded" })).snapshot())
      .rejects.toBeInstanceOf(ApiError);
  });

  it("fails closed for a missing, malformed or contradictory descriptor", async () => {
    const invalid: [string, unknown, boolean][] = [
      ["missing", undefined, false],
      ["null", null, true],
      ["string", "session", true],
      ["array", [], true],
      ["empty object", {}, true],
      ["empty id", { id: "", canWrite: true, reason: null }, true],
      ["blank id", { id: "   ", canWrite: true, reason: null }, true],
      ["numeric id", { id: 7, canWrite: true, reason: null }, true],
      ["missing canWrite", { id: "session-a", reason: null }, true],
      ["string canWrite", { id: "session-a", canWrite: "true", reason: null }, true],
      ["writer with a stale reason", { id: "session-a", canWrite: true, reason: "superseded" }, true],
      ["non-writable session", { id: "session-a", canWrite: false, reason: null }, true],
      ["non-writable with a reason", { id: "session-a", canWrite: false, reason: "expired" }, true],
    ];
    for (const [label, value, include] of invalid) {
      const failure = await apiFor(envelope(value, include)).snapshot()
        .then(() => null, (error: unknown) => error);
      expect(failure, label).toBeInstanceOf(ApiError);
      expect((failure as ApiError).code, label).toBe("INVALID_RESPONSE");
      // The refusal copy names the version/entry recovery, never a credential.
      expect((failure as ApiError).message, label).toContain("不会授予写权限");
      expect((failure as ApiError).message, label).not.toContain("csrf");
    }
  });

  it("never treats a descriptor-less snapshot as write access when it reaches the UI", async () => {
    const api = apiFor(envelope(undefined, false));
    render(<App suppliedApi={api} />);
    expect(await screen.findByRole("button", { name: "重新连接" })).toBeTruthy();
    expect(screen.queryByRole("switch", { name: "编辑设置" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存" })).toBeNull();
  });

  it("treats a missing descriptor as no write authority", () => {
    expect(sessionCanWrite({} as Snapshot)).toBe(false);
  });

  it("latches a lost session so an older writable snapshot cannot restore writes", () => {
    const latch = createAuthorityLatch();
    const writer = { consoleSession: { id: "session-a", canWrite: true, reason: null } } as Snapshot;
    const staleCopy = { consoleSession: { id: "session-a", canWrite: true, reason: null } } as Snapshot;
    const readOnly = { consoleSession: { id: "session-a", canWrite: false, reason: null } } as Snapshot;
    const fresh = { consoleSession: { id: "session-b", canWrite: true, reason: null } } as Snapshot;
    expect(latch.writable(writer)).toBe(true);
    latch.lose("session-a");
    // An older success snapshot for the same session must not re-arm writes.
    expect(latch.writable(writer)).toBe(false);
    expect(latch.writable(staleCopy)).toBe(false);
    expect(latch.writable(readOnly)).toBe(false);
    // A genuinely different launch-created session is new authority.
    expect(latch.writable(fresh)).toBe(true);
    // Losing the latch twice (for example both a refusal and a poll) is stable.
    latch.lose("session-a");
    expect(latch.writable(writer)).toBe(false);
  });

  it("maps the 401 CONSOLE_SESSION_EXPIRED refusal to login-expired copy (0.16)", () => {
    const expired = errorText(new ApiError("CONSOLE_SESSION_EXPIRED", "server raw text"));
    expect(expired).toContain("登录已失效");
    expect(expired).toContain("本页会自动恢复");
    expect(expired).not.toContain("server raw text");
    expect(expired).not.toContain("新窗口");
    // The retired single-writer code no longer exists anywhere in the client.
    expect(isSessionExpiredRefusal(new ApiError("CONSOLE_SESSION_EXPIRED", ""))).toBe(true);
    expect(isSessionExpiredRefusal(new ApiError("FORBIDDEN", ""))).toBe(false);
    expect(isSessionExpiredRefusal(new ApiError("NETWORK", ""))).toBe(false);
    expect(isSessionExpiredRefusal(new Error("CONSOLE_SESSION_EXPIRED"))).toBe(false);
  });
});

describe("invalid login session (0.16 multi-window: no handoff UX)", () => {
  it("keeps a dirty draft local, stops publish and renew, and stays readable without any takeover copy", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    // No single-writer badge or banner exists anywhere (0.16 multi-window).
    expect(screen.queryByText("只读会话")).toBeNull();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(document.querySelector(".readonly-banner")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    const opinion = await screen.findByLabelText("家族备注");
    await user.clear(opinion);
    await user.type(opinion, "未保存的本地草稿");
    expect(screen.getByText(/有 1 项未保存修改/)).toBeTruthy();

    // The cookie expires: the next authenticated poll reports a session that
    // can no longer write. This is the security bottom line, not a handoff.
    f.expireSession();
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true"));
    expect(screen.queryByText("只读会话")).toBeNull();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(document.querySelector(".readonly-banner")).toBeNull();
    // Draft value survives and stays copyable.
    const retained = screen.getByLabelText("家族备注") as HTMLTextAreaElement;
    expect(retained.value).toBe("未保存的本地草稿");
    expect(retained.disabled).toBe(false);
    // Save is refused locally without a publish, a begin or a renew.
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    const guardsBefore = document.querySelectorAll(".guard-banner").length;
    await user.click(save);
    await waitFor(() => expect(document.querySelectorAll(".guard-banner").length).toBeGreaterThan(guardsBefore));
    expect((await screen.findAllByText(saveRefusal)).length).toBeGreaterThan(0);
    expect(f.operations).toEqual([]);
    expect(f.published).toHaveLength(0);
    // Navigation keeps working and no control reacquires authority.
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    expect(screen.getByRole("heading", { name: "选择一项委派" })).toBeTruthy();
    expect(f.operations).toEqual([]);
    // Refreshing again never regains rights without a new login.
    // The edit cluster only exists on the settings tabs (P1.2), so return there.
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true"));
    expect(f.operations).toEqual([]);
  });

  it("pauses discovery and saving once the login lapses (401), without any takeover copy", async () => {
    const record = goal("readonly-task", "只读普通任务", { workflow: undefined });
    const f = fixture({ records: [record] });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    expect(screen.queryByText(bannerNotice)).toBeNull();
    // A local draft may open before the session lapses.
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "登录失效前的草稿");
    // The next poll is a 401: the page stays browsable with the login-expired
    // banner and no single-writer copy anywhere.
    f.expireSession();
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(await screen.findByText(/登录已失效/)).toBeTruthy();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    // Discovery and saving stay disabled with no edit switch to gate them.
    const discover = screen.getByRole("button", { name: "发现模型" });
    expect(discover.getAttribute("aria-disabled")).toBe("true");
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await user.click(discover);
    expect((await screen.findAllByText(/暂时不能发现模型/)).length).toBeGreaterThan(0);
    // Browsing and the read-only detail keep working.
    await user.click(screen.getByRole("link", { name: "委派记录" }));
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /只读普通任务/ }));
    await screen.findByRole("heading", { name: /只读普通任务/ });
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    // Discovery and task control never reached the board.
    expect(f.operations).toEqual([]);
  });

  it("keeps an unconfirmed publication after the login lapses and never replays it", async () => {
    const f = fixture({ script: { publish: "network" } });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "结果不明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    // The lost publish reply is staged for a deliberate retry with the same id.
    expect(await screen.findByText(/保存结果未确认/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "重试同一保存" }).length).toBeGreaterThan(0);
    expect(f.operations).toEqual(["evaluation_write_begin", "user_policy_publish"]);
    const commandId = f.published[0].commandId;

    // The next poll reports the invalid login. The staged payload must not be
    // replayed or unfrozen: its command ID and unknown-result status persist.
    f.expireSession();
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(await screen.findByText(/保存结果未确认：可能已经生效/)).toBeTruthy();
    const retained = screen.getByLabelText("家族备注") as HTMLTextAreaElement;
    expect(retained.value).toBe("原人工意见结果不明");
    expect(retained.disabled).toBe(false);
    // A local retry is blocked and nothing new is dispatched.
    const operations = [...f.operations];
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(operations);
    expect(f.published).toHaveLength(1);
    expect(f.published[0].commandId).toBe(commandId);
  });

  it("keeps an ambiguous publish unknown when its retry is refused before the next poll", async () => {
    const f = fixture({ script: { publish: ["network", "expired"] } });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "提交结果不明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/保存结果未确认/)).toBeTruthy();
    const commandId = f.published[0].commandId;

    // The retry reaches the board before any poll reports the invalid login.
    // The refusal proves only that this request was denied: the earlier same-ID
    // attempt may still have committed.
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    expect((await screen.findAllByText(/保存结果未确认：可能已经生效/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(2);
    expect(f.published[1].commandId).toBe(commandId);
    expect(screen.getAllByRole("button", { name: "重试同一保存" }).length).toBeGreaterThan(0);
    // The snapshot still says canWrite: true, but the refusal latched this
    // session: no takeover copy appears and a later retry is never sent.
    expect(screen.queryByText(bannerNotice)).toBeNull();
    const operations = [...f.operations];
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(operations);
    expect(f.published).toHaveLength(2);
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见提交结果不明");
  });

  it("treats a first publish that is definitely refused as a resolved rejection", async () => {
    const f = fixture({ script: { publish: "expired" } });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "首次被拒");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(actionRefusal)).length).toBeGreaterThan(0);
    // No earlier attempt exists, so this is not an unknown result and the
    // retained draft is not frozen behind a confirmation state.
    expect(screen.queryByText(/保存结果未确认：可能已经生效/)).toBeNull();
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(["evaluation_write_begin", "user_policy_publish"]);
    expect(f.published).toHaveLength(1);
  });

  it("preserves the draft when an in-flight save is refused with the 401 login-expired code", async () => {
    const f = fixture({ script: { begin: "expired" } });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "被拒绝的保存");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(actionRefusal)).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见被拒绝的保存");
    // A definite refusal is not retried: no begin replay, no renew, no abort.
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(["evaluation_write_begin"]);
    expect(f.published).toHaveLength(0);
  });

  it("stops renewing a queued intent refused with the 401 login-expired code and releases nothing", async () => {
    const f = fixture({ script: { begin: "queued", renew: "expired" } });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "排队后被接管");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(saveRefusal, {}, { timeout: 4000 })).length).toBeGreaterThan(0);
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见排队后被接管");
    // The refused wait ends: exactly one renew, no release, no second renew loop.
    await new Promise(resolve => setTimeout(resolve, 1200));
    expect(f.operations.filter(operation => operation === "evaluation_write_renew")).toHaveLength(1);
    expect(f.operations).not.toContain("evaluation_write_abort");
    expect(f.published).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "取消等待" })).toBeNull();
  });

  it("stops a queued wait on a polled invalid snapshot without sending another renew", async () => {
    const f = fixture({ script: { begin: "queued", renew: "expired" }, queuedForever: true });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "排队后只读");
    vi.useFakeTimers();
    try {
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "保存" })); });
      for (let i = 0; i < 50 && !screen.queryByRole("button", { name: "取消等待" }); i++) {
        await act(async () => { await Promise.resolve(); });
      }
      expect(screen.getByRole("button", { name: "取消等待" })).toBeTruthy();
      // The queue poll's renew is answered with the 401 login-expired refusal.
      f.expireSession();
      await act(async () => { fireEvent.click(screen.getByRole("button", { name: "刷新工作台" })); });
      for (let i = 0; i < 50 && screen.queryByRole("button", { name: "取消等待" }); i++) {
        await act(async () => { await Promise.resolve(); });
      }
      await act(async () => { await vi.advanceTimersByTimeAsync(QUEUE_POLL_MS * 2); });
      for (let i = 0; i < 50 && screen.queryByRole("button", { name: "取消等待" }); i++) {
        await act(async () => { await Promise.resolve(); });
      }
      // The queue poll's renew is refused with the 401: the wait stops there,
      // nothing is released or retried, and the draft stays. No takeover copy.
      expect(f.operations.filter(operation => operation === "evaluation_write_renew")).toHaveLength(1);
      expect(f.operations).not.toContain("evaluation_write_abort");
      expect(f.operations).not.toContain("user_policy_publish");
      expect(screen.queryByRole("button", { name: "取消等待" })).toBeNull();
      expect(screen.queryByText(bannerNotice)).toBeNull();
      expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见排队后只读");
    } finally {
      vi.useRealTimers();
    }
  });

  it("keeps task details read-only for every record kind, whichever session renders them (U4)", async () => {
    const plain = goal("plain-task", "普通待执行任务", { workflow: undefined });
    const failed = retryable("failed-task", "可重试任务");
    const awaiting = goal("governed-task", "待协助目标", { workflow: { state: "awaiting-host", awaitingHost: true, hostId: "current-host", ownerGeneration: 2, revision: 1 } });
    const delivered = goal("delivered-task", "已交付目标", { status: "completed", shutdownConfirmed: true,
      workflow: { state: "delivered", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 3 },
      workflowShutdown: { selfConfirmed: true, descendantsConfirmed: true, unconfirmedRunIds: [], unconfirmedCount: 0, truncated: false } });
    const workflows = new Map<string, Workflow>([
      ["governed-task", workflow(awaiting)],
      ["delivered-task", workflow(delivered, { state: "delivered", awaitingHost: false, activeRequest: null, revision: 3,
        finalArtifactId: "artifact-1",
        artifacts: [{ artifactId: "artifact-1", attemptId: "attempt-1", sourceTaskId: "delivered-task", kind: "output",
          manifestSha256: "manifest", outputCommit: "commit" }],
        integrations: [{ integrationId: "integration-1", runId: "delivered-task", artifactId: "artifact-1", attemptId: "attempt-1",
          state: "verified", strategy: "merge", target: null, sourceCommit: null, sourceTree: null, beforeCommit: null,
          afterCommit: null, beforeTree: null, afterTree: null, verification: {}, notRequired: false, reason: null,
          actor: "Host", createdAt: "2026-09-25T10:00:00Z" }],
        task: { runId: "delivered-task", revision: 3, status: "completed", shutdownConfirmed: true } })],
    ]);
    const records = [plain, failed, awaiting, delivered];

    const first = fixture({ records, workflows });
    const user = userEvent.setup();
    render(<App suppliedApi={first.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await screen.findByRole("button", { name: /普通待执行任务/ });
    const detail = await screen.findByRole("complementary", { name: "任务详情" });
    await user.click(screen.getByRole("button", { name: /普通待执行任务/ }));
    await screen.findByRole("heading", { name: /普通待执行任务/ });
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /可重试任务/ }));
    await screen.findByRole("heading", { name: /可重试任务/ });
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /待协助目标/ }));
    await screen.findByRole("tab", { name: "协作与待办" });
    await waitFor(() => expect(within(detail).getByText("V1")).toBeTruthy());
    expect(within(detail).queryByRole("group", { name: "用户决定" })).toBeNull();
    expect(within(detail).queryByRole("group", { name: "手工接续" })).toBeNull();
    expect(detail.querySelectorAll("fieldset.workflow-controls").length).toBe(0);
    await user.click(screen.getByRole("button", { name: /已交付目标/ }));
    await user.click(await screen.findByRole("tab", { name: "产物与验收" }));
    expect(within(detail).queryByRole("group", { name: "最终验收" })).toBeNull();
    // Reading the governed record is allowed; no mutation reached the board.
    expect(first.operations.every(operation => operation === "workflow_get")).toBe(true);
    cleanup();

    // A second session renders the same read-only detail: no browser control
    // writes to a delegation (0.15.1 U4).
    const writer = fixture({ records, workflows });
    render(<App suppliedApi={writer.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    await user.click(await screen.findByRole("button", { name: /待协助目标/ }));
    await screen.findByRole("tab", { name: "协作与待办" });
    const writerDetail = screen.getByRole("complementary", { name: "任务详情" });
    await waitFor(() => expect(within(writerDetail).getByText("V1")).toBeTruthy());
    expect(within(writerDetail).queryByRole("group", { name: "用户决定" })).toBeNull();
    expect(within(writerDetail).queryByRole("group", { name: "手工接续" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /可重试任务/ }));
    await screen.findByRole("heading", { name: /可重试任务/ });
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    expect(writer.operations.every(operation => operation === "workflow_get")).toBe(true);
  });

  it("keeps browsing, task details, routing and evaluation history readable for any logged-in session", async () => {
    const awaiting = goal("browse-task", "可浏览任务", { workflow: { state: "awaiting-host", awaitingHost: true, hostId: "current-host", ownerGeneration: 2, revision: 1 } });
    const workflows = new Map<string, Workflow>([["browse-task", workflow(awaiting, {
      executionConfiguration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" },
      executionConfigurationRevision: 2,
      routing: { status: "selected", decisionId: "decision-1", reason: "记录的路由原因" },
    })]]);
    const f = fixture({ records: [awaiting], workflows });
    const user = userEvent.setup();
    render(<App suppliedApi={f.api} />);
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    const detail = await screen.findByRole("complementary", { name: "任务详情" });
    await user.click(await screen.findByRole("button", { name: /可浏览任务/ }));
    await waitFor(() => expect(within(detail).getByText("V1")).toBeTruthy());
    await user.click(await within(detail).findByRole("tab", { name: "路由依据" }));
    expect(await within(detail).findByText("记录的选择依据")).toBeTruthy();
    // Filters and searches stay usable without any write attempt.
    await user.type(screen.getByLabelText("搜索委派"), "可浏览");
    await waitFor(() => expect(f.tasks).toHaveBeenLastCalledWith(expect.objectContaining({ query: "可浏览" }), expect.any(AbortSignal)));
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    await user.click(await screen.findByRole("button", { name: "更新记录" }));
    expect(await screen.findByText("V12")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "查看历史配置" }));
    await waitFor(() => expect(f.operations).toContain("model_profiles"));
    expect(f.operations).toContain("selection_get");
    expect(f.operations).toContain("evaluation_history");
    // Only read operations were dispatched.
    expect(f.operations.every(operation => ["evaluation_history", "selection_get", "selection_list", "model_profiles", "workflow_get"].includes(operation))).toBe(true);
  });

  const failedPolls: [string, ApiError][] = [
    ["network", new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。")],
    ["expired session", new ApiError("CONSOLE_SESSION_EXPIRED", "session expired")],
    ["unreadable snapshot", new ApiError("INVALID_RESPONSE", "console descriptor missing")],
  ];

  it.each(failedPolls)("keeps details readable with no writes after a failed poll (%s)", async (_label, failure) => {
    const plain = goal("gated-plain", "断线可取消任务", { workflow: undefined });
    const awaiting = goal("gated-governed", "断线待协助目标", { workflow: { state: "awaiting-host", awaitingHost: true, hostId: "current-host", ownerGeneration: 2, revision: 1 } });
    const workflows = new Map<string, Workflow>([["gated-governed", workflow(awaiting)]]);
    const f = fixture({ records: [plain, awaiting], workflows, failAfter: 1, failWith: failure });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "断线草稿");

    await user.click(screen.getByRole("link", { name: /委派记录/ }));
    await user.click(await screen.findByRole("button", { name: "全部执行记录" }));
    const detail = await screen.findByRole("complementary", { name: "任务详情" });
    await user.click(await screen.findByRole("button", { name: /断线可取消任务/ }));
    await screen.findByRole("heading", { name: /断线可取消任务/ });
    // The browser never offered task control in the first place (0.15.1 U4).
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    await user.click(screen.getByRole("button", { name: /断线待协助目标/ }));
    await screen.findByRole("tab", { name: "协作与待办" });
    await waitFor(() => expect(within(detail).getByText("V1")).toBeTruthy());

    // The authenticated poll now fails; the detail stays readable and empty of
    // write controls, and no mutation is dispatched from either session state.
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(await screen.findByText(/连接中断 · 请检查本地服务并刷新/)).toBeTruthy();
    expect(within(detail).queryByRole("group", { name: "用户决定" })).toBeNull();
    expect(within(detail).queryByRole("group", { name: "手工接续" })).toBeNull();
    // A failed poll is not a new-window takeover.
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(f.operations.every(operation => ["evaluation_history", "selection_get", "selection_list", "model_profiles", "workflow_get"].includes(operation))).toBe(true);
    // The dirty model draft is preserved and its save stays disabled.
    await user.click(screen.getByRole("link", { name: /Buddy 配置/ }));
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见断线草稿");
    expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps the connection-loss gate: a failed poll disables saving and retains the draft", async () => {
    const f = fixture({ failAfter: 1 });
    const user = userEvent.setup();
    window.location.hash = "#models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    await user.click(screen.getByRole("button", { name: /^deepseek-flash/ }));
    await user.type(await screen.findByLabelText("家族备注"), "断线草稿");
    await user.click(screen.getByRole("button", { name: "刷新工作台" }));
    expect(await screen.findByText(/无法连接本地黑板/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await user.click(save);
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "原人工意见断线草稿");
    expect(f.operations).toEqual([]);
  });
});
