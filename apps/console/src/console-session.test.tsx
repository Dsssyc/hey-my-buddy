import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, createApi, errorText, isSessionExpiredRefusal } from "./api";
import type { ConsoleApi } from "./api";
import { createAuthorityLatch, sessionCanWrite } from "./console-session";
import { FILTER_DEBOUNCE_MS } from "./use-buddy-history";
import { VIEW_PREFERENCE_PREFIX } from "./view-preferences";
import type { ConsoleSession, Buddy, Snapshot, Task } from "./types";
import { listed, note, selectModel } from "./buddies/page.testing";
import { timelineOfRecords } from "./task-detail/task-detail.fixtures";
import { macroTaskSummary } from "./macro-task-fixtures";

const bannerNotice = /新窗口已取得写权限，此页面现在是只读/;
const actionRefusal = /浏览器授权已失效/;
const saveRefusal = /浏览器授权已失效/;

/** Two model services and three execution buddies, all available. */
function catalog(): Buddy[] {
  const entries = [
    { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off", enabled: true },
    { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "max", enabled: true },
    { harness: "zcode", provider: "bigmodel-api", model: "GLM-5.3", effort: "high", enabled: false },
  ];
  return entries.map(p => ({ ...p, effortRank: null, buddyId: [p.harness, p.provider, p.model, p.effort].join(":"),
    label: `${p.model} · ${p.effort}`, available: true, capabilities: [], contextWindow: null,
    source: "catalog:fixture", description: "" }));
}
const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const FLASH = "dsh / deepseek-official / deepseek-flash";

function microTask(runId: string, task: string, extra: Partial<Task> = {}): Task {
  return { runId, task, state: "queued", owner: "worker:fixture", cwd: `/worktrees/${runId}`,
    revision: 1, createdAt: "2026-09-24T10:00:00Z", updatedAt: "2026-09-24T10:00:00Z", acceptedAt: null, reviewRejectedAt: null, reviewRejectionNote: null,
    workflow: { state: "executing", awaitingHost: false, hostId: "current-host", ownerGeneration: 2, revision: 1 },
    lineage: { kind: "micro-task", sourceHostId: "codex-source", currentHostId: "current-host", parentRunId: null, rootRunId: runId, rootLeadRunId: null, routingTask: null,
      project: { id: "fixture-project", label: "fixture-project", path: "/source/fixture-project" },
      buddy: { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" } },
    ...extra };
}

/** The compact collaboration record of a micro task, as `workflow_get` sends it. */
type Workflow = Record<string, unknown>;
function workflow(task: Task, overrides: Workflow = {}): Workflow {
  return { runId: task.runId, hostId: "current-host", revision: 1, state: "awaiting-host", buddy: null, buddyLocked: false, delegationAuthority: false,
    routing: null, turns: [], currentTurn: null, requests: [], counts: { turns: 0, openRequests: 1 }, children: [], artifacts: [], integrations: [],
    finalArtifactId: null, hostConclusion: null, shutdown: null, workspace: { path: task.cwd },
    activeRequest: { requestId: `request-${task.runId}`, kind: "assistance", state: "open", createdAt: "2026-09-25T10:00:00Z", decidedAt: null, childTaskId: null, routing: false,
      summary: `协助 ${task.runId}`, attempted: "已有检查", neededWork: ["补充测试"], expectedArtifacts: [], acceptance: "检查通过" },
    task: { runId: task.runId, revision: task.revision, state: task.state, lineage: task.lineage },
    ...overrides };
}

const session = (): ConsoleSession => ({ id: "session-writer" });

type IdentityOutcome = "expired";
type PublishOutcome = "ok" | "network" | IdentityOutcome;
type Script = { publish?: PublishOutcome | PublishOutcome[] };

function fixture(options: {
  script?: Script; records?: Task[]; workflows?: Map<string, Workflow>; failAfter?: number;
  failWith?: unknown;
} = {}) {
  const buddies = catalog();
  let state: Snapshot = {
    csrfToken: "csrf", eventCursor: 0, consoleSession: session(),

    configuration: { routerBuddyIds: [flashOff], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies,
    unavailableBuddyCount: 1,

    modelServiceNotes: [{ harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "原人工意见", updatedAt: null }],
    preferences: [], decisions: [],
    modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 }],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };
  const workflows = options.workflows ?? new Map<string, Workflow>();
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
    if (operation === "user_policy_publish") {
      published.push(params);
      const outcome = nextPublish();
      if (outcome === "network") throw new ApiError("NETWORK", "lost publish reply");
      if (outcome === "expired") throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
      return { published: true,};
    }
    if (operation === "workflow_get") {
      const value = workflows.get(String(params.runId));
      if (!value) throw new Error(`Unknown run: ${String(params.runId)}`);
      return params.includeAudit
        ? { ...structuredClone(value), audit: { turns: [], continuations: [], requests: [], hostConclusions: [] }, routingHistory: { entries: [], nextCursor: null, total: 0 } }
        : structuredClone(value);
    }
    if (operation === "message_list") return { messages: [], cursor: 0 };
    if (operation === "buddy_list") {
      return { buddies: [], modelServiceNotes: [], preferences: [], modelServiceConcurrency: [], nextCursor: null };
    }
    if (operation === "selection_get") {
      return { decision: { decisionId: String(params.decisionId), status: "completed",
        task: "路由计算", buddyId: flashOff, reason: "记录的选择依据",
        evidence: [], createdAt: "2026-09-25T10:00:00Z" } };
    }
    throw new ApiError("FORBIDDEN", "board refused a mutation");
  });
  const records = options.records ?? [];
  const roots = records.filter(row => row.lineage?.kind === "micro-task");
  let loginExpired = false;
  const snapshot = vi.fn(async () => {
    snapshots += 1;
    if (loginExpired) throw new ApiError("CONSOLE_SESSION_EXPIRED", "login expired");
    if (options.failAfter !== undefined && snapshots > options.failAfter) {
      throw options.failWith ?? new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。");
    }
    return structuredClone(state);
  });
  const api = { runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
    snapshot, command, task: vi.fn(async (runId: string) => records.find(t => t.runId === runId)),
    macroTasks: vi.fn(async () => ({ macroTasks: roots.map(row => macroTaskSummary({ macroTaskId: `run:${row.runId}`, title: row.task, titleSource: "task", project: row.lineage?.project ?? null })),
      totals: { all: roots.length, active: roots.length, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })),
    macroTaskTimeline: vi.fn(async (macroTaskId: string) => ({ timeline: timelineOfRecords(macroTaskId, records), verifiedAtMs: null })) } as unknown as ConsoleApi;
  return { api, command, operations, published, snapshot,
    state: () => state,
    expireSession: () => { loginExpired = true; } };
}

/** Foregrounding uses the existing visibility-gated snapshot read, without a global refresh. */
async function readSnapshotOnForeground(snapshot: ConsoleApi["snapshot"]) {
  const calls = vi.mocked(snapshot).mock.calls.length;
  const visibility = vi.spyOn(document, "visibilityState", "get");
  try {
    visibility.mockReturnValue("hidden");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    visibility.mockReturnValue("visible");
    await act(async () => { fireEvent(document, new Event("visibilitychange")); });
    expect(vi.mocked(snapshot).mock.calls.length).toBeGreaterThan(calls);
  } finally {
    visibility.mockRestore();
  }
}

// These cases start with default model filters and restore only the keys they own.
const modelViewKeys = ["models.enabledOnly", "models.showUnavailable"]
  .map(name => VIEW_PREFERENCE_PREFIX + name);
let savedModelViews: Array<string | null>;
beforeEach(() => {
  savedModelViews = modelViewKeys.map(key => window.localStorage.getItem(key));
  for (const key of modelViewKeys) window.localStorage.removeItem(key);
});

afterEach(() => {
  cleanup();
  modelViewKeys.forEach((key, index) => {
    const saved = savedModelViews[index];
    if (saved === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, saved);
  });
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});
/** Builds one authenticated-snapshot envelope; `null` leaves the field out. */
function envelope(consoleSession: unknown, include = true) {
  return {
    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "fast", routingBudget: "standard" },

    buddies: [], modelServiceNotes: [], preferences: [], modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    csrfToken: "csrf", eventCursor: 0,
    ...(include ? { consoleSession } : {}),
  };
}
function apiFor(payload: unknown) {
  return createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
}

describe("console session descriptor validation", () => {
  it("accepts a session identity from the live service", async () => {
    await expect(apiFor(envelope({ id: "session-a" })).snapshot())
      .resolves.toMatchObject({ consoleSession: { id: "session-a" } });
  });

  it("fails closed for a missing or malformed descriptor", async () => {
    const invalid: [string, unknown, boolean][] = [
      ["missing", undefined, false],
      ["null", null, true],
      ["string", "session", true],
      ["array", [], true],
      ["empty object", {}, true],
      ["empty id", { id: "" }, true],
      ["blank id", { id: "   " }, true],
      ["numeric id", { id: 7 }, true],
    ];
    for (const [label, value, include] of invalid) {
      const failure = await apiFor(envelope(value, include)).snapshot()
        .then(() => null, (error: unknown) => error);
      expect(failure, label).toBeInstanceOf(ApiError);
      expect((failure as ApiError).code, label).toBe("INVALID_RESPONSE");
      // The refusal copy names the version/entry recovery, never a credential.
      expect((failure as ApiError).message, label).toContain("会话信息无法识别");
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
    const writer = { consoleSession: { id: "session-a" } } as Snapshot;
    const staleCopy = { consoleSession: { id: "session-a" } } as Snapshot;
    const missing = { consoleSession: undefined } as unknown as Snapshot;
    const fresh = { consoleSession: { id: "session-b" } } as Snapshot;
    expect(latch.writable(writer)).toBe(true);
    latch.lose("session-a");
    // An older success snapshot for the same session must not re-arm writes.
    expect(latch.writable(writer)).toBe(false);
    expect(latch.writable(staleCopy)).toBe(false);
    expect(latch.writable(missing)).toBe(false);
    // A genuinely different launch-created session is new authority.
    expect(latch.writable(fresh)).toBe(true);
    // Losing the latch twice (for example both a refusal and a poll) is stable.
    latch.lose("session-a");
    expect(latch.writable(writer)).toBe(false);
  });

  it("maps the 401 CONSOLE_SESSION_EXPIRED refusal to login-expired copy (0.16)", () => {
    const expired = errorText(new ApiError("CONSOLE_SESSION_EXPIRED", "server raw text"));
    expect(expired).toContain("浏览器授权已失效");
    expect(expired).toContain("请运行 hey-my-buddy console 重新授权");
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
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    // No single-writer badge or banner exists anywhere (0.16 multi-window).
    expect(screen.queryByText("只读会话")).toBeNull();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(document.querySelector(".readonly-banner")).toBeNull();
    await selectModel(user, FLASH, "DSH");
    const opinion = note();
    await user.clear(opinion);
    await user.type(opinion, "未保存的本地草稿");
    expect(screen.getByText(/1 项未保存/)).toBeTruthy();

    // The cookie expires: the next authenticated poll reports a session that
    // can no longer write. This is the security bottom line, not a handoff.
    f.expireSession();
    await readSnapshotOnForeground(f.api.snapshot);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true"));
    expect(screen.queryByText("只读会话")).toBeNull();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(document.querySelector(".readonly-banner")).toBeNull();
    // Draft value survives and stays copyable.
    const retained = note() as HTMLTextAreaElement;
    expect(retained.value).toBe("未保存的本地草稿");
    expect(retained.disabled).toBe(false);
    // Save is refused locally without a publish, a begin or a renew.
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    // The refusal is said above the page, next to what the page already says about the login.
    const noticesBefore = screen.queryAllByRole("status").length;
    await user.click(save);
    await waitFor(() => expect(screen.queryAllByRole("status").length).toBeGreaterThan(noticesBefore));
    expect((await screen.findAllByText(saveRefusal)).length).toBeGreaterThan(0);
    expect(f.operations).toEqual([]);
    expect(f.published).toHaveLength(0);
    // Navigation keeps working and no control reacquires authority.
    await user.click(screen.getByRole("link", { name: "任务" }));
    expect(await screen.findByRole("heading", { name: "还没有宏任务" })).toBeTruthy();
    expect(f.operations).toEqual([]);
    // Refreshing again never regains rights without a new login.
    // The edit cluster only exists on the settings tabs (P1.2), so return there.
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
    await listed(2, 3);
    await readSnapshotOnForeground(f.api.snapshot);
    await waitFor(() => expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true"));
    expect(f.operations).toEqual([]);
  });

  it("pauses discovery and saving once the login lapses (401), without any takeover copy", async () => {
    const record = microTask("readonly-task", "只读微任务");
    const f = fixture({ records: [record] });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    expect(screen.queryByText(bannerNotice)).toBeNull();
    // A local draft may open before the session lapses.
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "登录失效前的草稿");
    // The next poll is a 401: the page stays browsable with the login-expired
    // banner and no single-writer copy anywhere.
    f.expireSession();
    await readSnapshotOnForeground(f.api.snapshot);
    expect(await screen.findByText(/浏览器授权已失效/)).toBeTruthy();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    // Discovery and saving stay disabled with no edit switch to gate them.
    const discover = screen.getByRole("button", { name: "发现模型" });
    expect(discover.getAttribute("aria-disabled")).toBe("true");
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await user.click(discover);
    expect((await screen.findAllByText(/暂时不能发现模型/)).length).toBeGreaterThan(0);
    // Browsing and the read-only detail keep working.
    await user.click(screen.getByRole("link", { name: "任务" }));
    await user.click(await screen.findByRole("button", { name: /只读微任务/ }));
    await waitFor(() => expect(document.querySelector('[data-key="row:readonly-task"]')).toBeTruthy());
    await user.dblClick(document.querySelector('[data-key="row:readonly-task"]') as HTMLElement);
    await screen.findByRole("heading", { name: /只读微任务/ });
    expect(screen.queryByRole("button", { name: "取消任务" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重新尝试" })).toBeNull();
    // Discovery and task control never reached the board; detail reads remain available.
    expect(f.operations.every(operation => ["workflow_get", "message_list"].includes(operation))).toBe(true);
  });

  it("keeps an unconfirmed publication after the login lapses and never replays it", async () => {
    const f = fixture({ script: { publish: "network" } });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "结果不明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    // The lost publish reply is staged for a deliberate retry with the same id.
    expect(await screen.findByText(/保存结果未知/)).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "重试同一保存" }).length).toBeGreaterThan(0);
    expect(f.operations).toEqual(["user_policy_publish"]);
    const commandId = f.published[0].commandId;

    // The next poll reports the invalid login. The staged payload must not be
    // replayed or unfrozen: its command ID and unknown-result status persist.
    f.expireSession();
    await readSnapshotOnForeground(f.api.snapshot);
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(await screen.findByText(/保存结果未知/)).toBeTruthy();
    const retained = note() as HTMLTextAreaElement;
    expect(retained.value).toBe("原人工意见结果不明");
    expect(retained.disabled).toBe(false);
    expect(retained.readOnly).toBe(true);
    expect(retained.getAttribute("aria-disabled")).toBe("true");
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
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "提交结果不明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect(await screen.findByText(/保存结果未知/)).toBeTruthy();
    const commandId = f.published[0].commandId;

    // The retry reaches the board before any poll reports the invalid login.
    // The refusal proves only that this request was denied: the earlier same-ID
    // attempt may still have committed.
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    expect((await screen.findAllByText(/保存结果未知/)).length).toBeGreaterThan(0);
    expect(f.published).toHaveLength(2);
    expect(f.published[1].commandId).toBe(commandId);
    expect(screen.getAllByRole("button", { name: "重试同一保存" }).length).toBeGreaterThan(0);
    // The snapshot still carries this session id, but the refusal latched this
    // session: no takeover copy appears and a later retry is never sent.
    expect(screen.queryByText(bannerNotice)).toBeNull();
    const operations = [...f.operations];
    await user.click(screen.getAllByRole("button", { name: "重试同一保存" })[0]!);
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(operations);
    expect(f.published).toHaveLength(2);
    expect(note()).toHaveProperty("value", "原人工意见提交结果不明");
  });

  it("treats a first publish that is definitely refused as a resolved rejection", async () => {
    const f = fixture({ script: { publish: "expired" } });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "首次被拒");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(actionRefusal)).length).toBeGreaterThan(0);
    // No earlier attempt exists, so this is not an unknown result and the
    // retained draft is not frozen behind a confirmation state.
    expect(screen.queryByText(/保存结果未知/)).toBeNull();
    expect(screen.getByRole("button", { name: "保存" })).toBeTruthy();
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(["user_policy_publish"]);
    expect(f.published).toHaveLength(1);
  });

  it("preserves the draft when an in-flight save is refused with the 401 login-expired code", async () => {
    const f = fixture({ script: { publish: "expired" } });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "被拒绝的保存");
    await user.click(screen.getByRole("button", { name: "保存" }));
    expect((await screen.findAllByText(actionRefusal)).length).toBeGreaterThan(0);
    expect(note()).toHaveProperty("value", "原人工意见被拒绝的保存");
    // A definite refusal is not retried: no begin replay, no renew, no abort.
    await new Promise(resolve => setTimeout(resolve, 900));
    expect(f.operations).toEqual(["user_policy_publish"]);
    expect(f.published).toHaveLength(1);
  });

  it("keeps micro task details read-only across sessions and leaves routing readable", async () => {
    const awaiting = microTask("browse-task", "可浏览任务", { workflow: { state: "awaiting-host", awaitingHost: true, hostId: "current-host", ownerGeneration: 2, revision: 1 } });
    const workflows = new Map<string, Workflow>([["browse-task", workflow(awaiting, {
      buddy: { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", effort: "off" },
      routing: { status: "completed", decisionId: "decision-1", source: "model-selection" },
    })]]);
    for (const sessionId of ["session-first", "session-second"]) {
      const f = fixture({ records: [awaiting], workflows });
      f.state().consoleSession.id = sessionId;
      const user = userEvent.setup();
      window.location.hash = "#tasks/run%3Abrowse-task/browse-task";
      render(<App suppliedApi={f.api} />);
      const detail = await screen.findByRole("region", { name: "微任务详情" });
      await screen.findByRole("group", { name: "这个微任务的各步" });
      expect(await within(detail).findByText("协助 browse-task")).toBeTruthy();
      expect(within(detail).queryByRole("group", { name: "用户决定" })).toBeNull();
      expect(within(detail).queryByRole("group", { name: "手工接续" })).toBeNull();
      expect(detail.querySelectorAll("input, textarea, select, form, fieldset.workflow-controls")).toHaveLength(0);
      await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
      await user.click(await screen.findByRole("checkbox", { name: "显示不可用 1" }));
      expect(f.operations).toContain("workflow_get");
      expect(f.operations.every(operation => ["selection_get", "selection_list", "buddy_list", "workflow_get", "message_list"].includes(operation))).toBe(true);
      cleanup();
    }
  });

  const failedPolls: [string, ApiError][] = [
    ["network", new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。")],
    ["expired session", new ApiError("CONSOLE_SESSION_EXPIRED", "session expired")],
    ["unreadable snapshot", new ApiError("INVALID_RESPONSE", "console descriptor missing")],
  ];

  it.each(failedPolls)("keeps micro task details readable with no writes after a failed poll (%s)", async (_label, failure) => {
    const awaiting = microTask("gated-micro-task", "断线待协助目标", { workflow: { state: "awaiting-host", awaitingHost: true, hostId: "current-host", ownerGeneration: 2, revision: 1 } });
    const workflows = new Map<string, Workflow>([["gated-micro-task", workflow(awaiting)]]);
    const f = fixture({ records: [awaiting], workflows, failAfter: 1, failWith: failure });
    const user = userEvent.setup();
    window.location.hash = "#tasks/run%3Agated-micro-task/gated-micro-task";
    render(<App suppliedApi={f.api} />);
    const detail = await screen.findByRole("region", { name: "微任务详情" });
    await screen.findByRole("group", { name: "这个微任务的各步" });

    await readSnapshotOnForeground(f.api.snapshot);
    expect(await screen.findByText("连接中断")).toBeTruthy();
    expect(within(detail).queryByRole("group", { name: "用户决定" })).toBeNull();
    expect(within(detail).queryByRole("group", { name: "手工接续" })).toBeNull();
    expect(screen.queryByText(bannerNotice)).toBeNull();
    expect(f.operations.every(operation => ["selection_get", "selection_list", "buddy_list", "workflow_get", "message_list"].includes(operation))).toBe(true);
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
    await listed(2, 3);
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "断线草稿");
    expect(note()).toHaveProperty("value", "原人工意见断线草稿");
    expect(screen.getByRole("button", { name: "保存" }).getAttribute("aria-disabled")).toBe("true");
  });

  it("keeps the connection-loss gate: a failed poll disables saving and retains the draft", async () => {
    const f = fixture({ failAfter: 1 });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(2, 3);
    // Let restored-history reads become reachable before checking the default fixture's gate.
    await act(async () => { await new Promise(resolve => setTimeout(resolve, FILTER_DEBOUNCE_MS + 50)); });
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "断线草稿");
    await readSnapshotOnForeground(f.api.snapshot);
    expect(await screen.findByText(/无法连接本地黑板/)).toBeTruthy();
    const save = screen.getByRole("button", { name: "保存" });
    expect(save.getAttribute("aria-disabled")).toBe("true");
    await user.click(save);
    expect(note()).toHaveProperty("value", "原人工意见断线草稿");
    expect(f.operations).toEqual([]);
  });
});
