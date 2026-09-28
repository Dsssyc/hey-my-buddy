import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, createApi, snapshotHarnesses } from "./api";
import type { ConsoleApi, HarnessHealth } from "./api";
import type { Profile, Snapshot } from "./types";

/**
 * ADR-017 §15 on the Buddy 配置 page: each harness shows its found path,
 * version and source — or its attempted locations and remedy — with the last
 * check time and an explicit 重新检测. Automatic detection failure offers an
 * advanced manual absolute path (and clearing it back to automatic via null).
 * Re-checking goes through the existing `capabilities` operation with
 * `refresh: true` and never calls a model; saving a path goes through
 * `harness_set` with the recorded revision, so a concurrent change is a
 * reported conflict rather than an overwrite.
 */

const CHECKED_AT = "2026-09-28T09:30:00.000Z";
const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const glm = { adapter: "zcode", provider: "zhipu", model: "glm-5" };
const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const glmId = "zcode:zhipu:glm-5:default";
const codexExecutable = "/Users/fixture/.nvm/versions/node/v24/bin/codex";

function harness(entry: Partial<HarnessHealth> & Pick<HarnessHealth, "adapter" | "status">): HarnessHealth {
  return { available: entry.status === "ready", revision: 1, manualPath: null, ...entry };
}

/** One ready harness, one missing, one needing login and one unhealthy. */
function harnesses(): HarnessHealth[] {
  return [
    harness({ adapter: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh",
      command: ["/usr/local/bin/dsh", "--version"], version: "0.4.2", source: "版本管理器（nvm 默认版本）",
      checkedAt: CHECKED_AT }),
    harness({ adapter: "zcode", status: "missing", revision: 1, checkedAt: CHECKED_AT,
      reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI，或填写可执行文件的绝对路径。",
      candidates: [
        { path: "/Applications/ZCode.app/Contents/MacOS/zcode", source: "应用包", status: "missing", reasonCode: "not_found" },
        { path: "/opt/homebrew/bin/zcode", source: "Homebrew", status: "missing" },
      ] }),
    harness({ adapter: "codex", status: "login-required", revision: 2, executable: codexExecutable,
      command: [codexExecutable], version: "0.157.0", source: "PATH",
      reasonCode: "HARNESS_LOGIN_REQUIRED", remedy: "先运行 codex login 完成登录，再重新检测。", checkedAt: CHECKED_AT }),
    harness({ adapter: "claude", status: "unhealthy", revision: 5, executable: "/Users/fixture/.claude/local/claude",
      version: "2.0.1", source: "手动路径", manualPath: "/Users/fixture/.claude/local/claude",
      reasonCode: "HARNESS_HANDSHAKE_FAILED", remedy: "检查该 CLI 能否独立运行。", checkedAt: CHECKED_AT,
      candidates: [{ path: "/Users/fixture/.claude/local/claude", source: "手动路径", status: "failed", reasonCode: "handshake_failed" }] }),
  ];
}

type HarnessSnapshot = Snapshot & { harnesses: HarnessHealth[] };

function profile(entry: Partial<Profile> & Pick<Profile, "profileId" | "label" | "adapter" | "provider" | "model" | "effort">): Profile {
  return { available: true, enabled: false, capabilities: [], contextWindow: null, description: "", source: "catalog:fixture", ...entry };
}

function snapshot(options: { harnesses?: HarnessHealth[] } = {}): HarnessSnapshot {
  return {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: mediumId, routingBudget: "standard" },
    profiles: [
      profile({ profileId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium",
        enabled: true, capabilities: ["execution:claude", "decision"], contextWindow: 200_000 }),
      profile({ profileId: glmId, label: "GLM-5 · default", ...glm, effort: "default", available: false,
        unavailableReason: "本机未检测到 zcode CLI" }),
    ],
    preferences: [],
    familyPreferences: [],
    preferenceOverrides: [],
    cards: [],
    familyAnnotations: [],
    evidence: [],
    decisions: [],
    sampleCounts: {},
    modelConcurrency: [{ ...sonnet, limit: 2, active: 0 }, { ...glm, limit: 2, active: 0 }],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
    harnesses: options.harnesses ?? harnesses(),
  };
}

type FixtureOptions = { harnesses?: HarnessHealth[]; failFirstCheck?: boolean; conflictOnce?: boolean };

function fixture(options: FixtureOptions = {}) {
  let state = snapshot(options);
  const calls: { operation: string; params: Record<string, any> }[] = [];
  let checksToFail = options.failFirstCheck ? 1 : 0;
  let setsToConflict = options.conflictOnce ? 1 : 0;
  /** One cheap re-check: a selected harness becomes ready with a fresh reading. */
  function checked(adapter?: string): HarnessHealth[] {
    return state.harnesses.map(row => adapter === undefined || row.adapter === adapter
      ? { ...row, status: "ready" as const, available: true, revision: row.revision + 1,
          executable: `/opt/bin/${row.adapter}`, version: "9.9.9", source: "PATH",
          manualPath: null, reasonCode: undefined, remedy: undefined, candidates: [],
          checkedAt: new Date().toISOString() }
      : row);
  }
  const command = vi.fn(async (operation: string, params: Record<string, any>, _csrfToken?: string) => {
    calls.push({ operation, params });
    if (operation === "capabilities") {
      if (checksToFail > 0) {
        checksToFail -= 1;
        throw new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。");
      }
      state = { ...state, harnesses: checked(params.adapter) };
      return { ...state.capabilities, harnesses: state.harnesses };
    }
    if (operation === "harness_set") {
      const adapter = params.adapter as string;
      if (setsToConflict > 0) {
        // Another writer advanced the record while this save was in flight.
        setsToConflict -= 1;
        state = { ...state, harnesses: state.harnesses.map(row =>
          row.adapter === adapter ? { ...row, revision: row.revision + 3 } : row) };
        throw new ApiError("REVISION_CONFLICT", "Harness settings changed; reread before saving");
      }
      const path = params.path as string | null;
      state = { ...state, harnesses: state.harnesses.map(row => row.adapter !== adapter ? row : path
        ? { ...row, revision: row.revision + 1, status: "ready" as const, available: true, manualPath: path,
            executable: path, version: "1.0.0", source: "手动路径", reasonCode: undefined, remedy: undefined,
            candidates: [], checkedAt: new Date().toISOString() }
        : { ...row, revision: row.revision + 1, status: "missing" as const, available: false, manualPath: null,
            executable: undefined, version: undefined, source: undefined, reasonCode: "HARNESS_NOT_FOUND",
            remedy: "安装 ZCode CLI，或填写可执行文件的绝对路径。", candidates: [],
            checkedAt: new Date().toISOString() }) };
      return { harness: state.harnesses.find(row => row.adapter === adapter)! };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const snapshotMock = vi.fn(async () => structuredClone(state));
  const api = {
    snapshot: snapshotMock,
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    // Mirrors the real api helpers: the existing command route, no new path.
    harnessRefresh: vi.fn(async (csrfToken: string, adapter?: string) => {
      const reply = await command("capabilities",
        adapter === undefined ? { refresh: true } : { refresh: true, adapter }, csrfToken) as { harnesses: HarnessHealth[] };
      return reply.harnesses;
    }),
    harnessSet: vi.fn(async (adapter: string, path: string | null, expectedRevision: number, csrfToken: string) =>
      (await command("harness_set", { adapter, path, expectedRevision }, csrfToken) as { harness: HarnessHealth }).harness),
  } as unknown as ConsoleApi;
  return { api, snapshotMock, command, calls, state: () => state };
}

async function openBuddy(f: ReturnType<typeof fixture>) {
  window.location.hash = "#buddy";
  render(<App suppliedApi={f.api} />);
  await screen.findByRole("region", { name: "Harness 状态" });
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("the harness status strip", () => {
  it("shows each harness's recorded state, attempted locations and remedy without calling anything", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    expect(screen.getByText("可用 1/4")).toBeTruthy();
    expect(screen.getByText("找到：路径 /usr/local/bin/dsh · 版本 0.4.2 · 来源 版本管理器（nvm 默认版本）")).toBeTruthy();
    expect(screen.getByText(`已找到 ${codexExecutable}，但需要先登录`)).toBeTruthy();
    expect(screen.getByText("未找到可执行文件（已尝试 2 处）")).toBeTruthy();
    // Reading the page is not an action: no command runs and no draft is created.
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();

    // A ready harness names its path, version, source and last check.
    const dsh = screen.getByText(/^找到：路径 \/usr\/local\/bin\/dsh/).closest("li")!;
    await user.click(screen.getByRole("button", { name: "DSH 检测详情" }));
    expect(within(dsh).getByText("/usr/local/bin/dsh")).toBeTruthy();
    expect(within(dsh).getByText("0.4.2")).toBeTruthy();
    expect(within(dsh).getByText("版本管理器（nvm 默认版本）")).toBeTruthy();
    expect(within(dsh).getByText(/^\d{2}-\d{2} \d{2}:\d{2}$/)).toBeTruthy();
    // Automatic detection succeeded, so no manual path is offered.
    expect(within(dsh).queryByLabelText("DSH 手动路径")).toBeNull();

    // A missing harness lists every attempted location and the remedy, plus the advanced path.
    const zcode = screen.getByText("未找到可执行文件（已尝试 2 处）").closest("li")!;
    await user.click(screen.getByRole("button", { name: "ZCode 检测详情" }));
    expect(within(zcode).getByText("已尝试的位置（2）")).toBeTruthy();
    expect(within(zcode).getByText("/Applications/ZCode.app/Contents/MacOS/zcode")).toBeTruthy();
    expect(within(zcode).getByText("/opt/homebrew/bin/zcode")).toBeTruthy();
    expect(within(zcode).getByText("未找到（未找到可执行文件）")).toBeTruthy();
    expect(within(zcode).getByText("修复办法：安装 ZCode CLI，或填写可执行文件的绝对路径。")).toBeTruthy();
    expect(within(zcode).getByLabelText("ZCode 手动路径")).toBeTruthy();
  });

  it("keeps a stored manual path visible and restorable", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    const claude = screen.getByText("已找到 /Users/fixture/.claude/local/claude，但检测未通过").closest("li")!;
    await user.click(screen.getByRole("button", { name: "Claude Code 检测详情" }));
    expect(within(claude).getAllByText("/Users/fixture/.claude/local/claude").length).toBeGreaterThan(0);
    expect(within(claude).getAllByText("手动路径").length).toBeGreaterThan(0);
    expect(within(claude).getByRole("button", { name: "恢复自动检测" })).toBeTruthy();
  });

  it("shows nothing rather than invented health when the snapshot carries no harness rows", async () => {
    const f = fixture({ harnesses: [] });
    window.location.hash = "#buddy";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("heading", { name: "模型 2" });
    expect(screen.queryByRole("region", { name: "Harness 状态" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("explicit re-detection", () => {
  it("re-checks one harness through the existing capabilities operation and reloads the snapshot", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    const reads = f.snapshotMock.mock.calls.length;
    await user.click(screen.getByRole("button", { name: "重新检测 Codex" }));
    await screen.findByText("已重新检测 Codex：已找到。");
    // No model call and no new path: one named adapter through /api/command.
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true, adapter: "codex" } }]);
    await waitFor(() => expect(f.snapshotMock.mock.calls.length).toBeGreaterThan(reads));
    await screen.findByText("找到：路径 /opt/bin/codex · 版本 9.9.9 · 来源 PATH");
    expect(screen.getByText("可用 2/4")).toBeTruthy();
  });

  it("re-checks every harness when no adapter is named", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "重新检测全部" }));
    await screen.findByText("已重新检测 4 个 harness：可用 4 个，其余 0 个。");
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true } }]);
    expect(screen.getByText("可用 4/4")).toBeTruthy();
  });

  it("keeps a failed re-check retryable and only reports success after a real reply", async () => {
    const f = fixture({ failFirstCheck: true });
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "重新检测全部" }));
    await screen.findByRole("alert");
    expect(screen.getByText("无法连接本地黑板。已有任务仍由后台管理。")).toBeTruthy();
    // The recorded state is untouched by the failure.
    expect(screen.getByText("可用 1/4")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已重新检测 4 个 harness：可用 4 个，其余 0 个。");
    expect(f.calls.map(call => call.operation)).toEqual(["capabilities", "capabilities"]);
  });
});

describe("the advanced manual path", () => {
  it("refuses a relative path locally and sends nothing", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "ZCode 检测详情" }));
    await user.type(screen.getByLabelText("ZCode 手动路径"), "zcode");
    await user.click(screen.getByRole("button", { name: "保存并检测" }));
    expect(await screen.findByText("请输入可执行文件的绝对路径（以 / 开头，或 Windows 盘符路径）。")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("saves an absolute path with the recorded revision and applies the returned row", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "ZCode 检测详情" }));
    await user.type(screen.getByLabelText("ZCode 手动路径"), "/opt/tools/zcode");
    await user.click(screen.getByRole("button", { name: "保存并检测" }));
    await screen.findByText("已保存手动路径：/opt/tools/zcode；ZCode 已找到。");
    expect(f.calls).toEqual([{
      operation: "harness_set",
      params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 1 },
    }]);
    await screen.findByText("找到：路径 /opt/tools/zcode · 版本 1.0.0 · 来源 手动路径");
  });

  it("clears the manual path back to automatic detection with null", async () => {
    const f = fixture();
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "Claude Code 检测详情" }));
    await user.click(screen.getByRole("button", { name: "恢复自动检测" }));
    await screen.findByText("已恢复自动检测；Claude Code 未找到。");
    expect(f.calls).toEqual([{
      operation: "harness_set",
      params: { adapter: "claude", path: null, expectedRevision: 5 },
    }]);
    // Automatic detection still fails here; the row reports that, not a stale path.
    await screen.findByText("未找到可执行文件（已尝试 0 处）");
    expect(screen.getByLabelText("Claude Code 手动路径")).toHaveProperty("value", "");
  });

  it("reports a revision conflict, rereads the record and retries against the new revision", async () => {
    const f = fixture({ conflictOnce: true });
    const user = userEvent.setup();
    await openBuddy(f);
    await user.click(screen.getByRole("button", { name: "ZCode 检测详情" }));
    await user.type(screen.getByLabelText("ZCode 手动路径"), "/opt/tools/zcode");
    await user.click(screen.getByRole("button", { name: "保存并检测" }));
    await screen.findByText("记录已更新，此操作未提交。请刷新并核对最新版本后再操作。");
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已保存手动路径：/opt/tools/zcode；ZCode 已找到。");
    expect(f.calls).toEqual([
      { operation: "harness_set", params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 1 } },
      { operation: "harness_set", params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 4 } },
    ]);
  });
});

describe("the harness wire contract", () => {
  const codexRow: HarnessHealth = {
    adapter: "codex", status: "ready", available: true, revision: 4, manualPath: null,
    command: ["/opt/bin/codex"], executable: "/opt/bin/codex", version: "0.157.0", source: "PATH",
    candidates: [], checkedAt: CHECKED_AT,
  };

  function commandApi(result: unknown) {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true, result })));
    return { api: createApi("/private/", fetcher), fetcher };
  }
  const body = (fetcher: ReturnType<typeof commandApi>["fetcher"]) =>
    JSON.parse(String(fetcher.mock.calls[0][1]?.body));

  it("re-checks through the existing command route with the session CSRF header", async () => {
    const { api, fetcher } = commandApi({ harnesses: [codexRow] });
    await expect(api.harnessRefresh("csrf", "codex")).resolves.toEqual([codexRow]);
    expect(fetcher).toHaveBeenCalledWith("/private/api/command", expect.objectContaining({
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Buddy-CSRF": "csrf" },
      body: JSON.stringify({ operation: "capabilities", params: { refresh: true, adapter: "codex" } }),
    }));
  });

  it("omits the adapter when every harness is re-checked", async () => {
    const { api, fetcher } = commandApi({ harnesses: [codexRow] });
    await api.harnessRefresh("csrf");
    expect(body(fetcher)).toEqual({ operation: "capabilities", params: { refresh: true } });
  });

  it("refuses a re-check reply without harness rows", async () => {
    await expect(commandApi({ selection: true }).api.harnessRefresh("csrf", "codex"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(commandApi({ harnesses: [] }).api.harnessRefresh("csrf", "codex"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("saves a path or null with the expected revision and refuses a mismatched reply", async () => {
    const saved = commandApi({ harness: codexRow });
    await expect(saved.api.harnessSet("codex", "/opt/bin/codex", 3, "csrf")).resolves.toEqual(codexRow);
    expect(body(saved.fetcher)).toEqual({
      operation: "harness_set", params: { adapter: "codex", path: "/opt/bin/codex", expectedRevision: 3 },
    });
    const cleared = commandApi({ harness: codexRow });
    await cleared.api.harnessSet("codex", null, 4, "csrf");
    expect(body(cleared.fetcher).params).toEqual({ adapter: "codex", path: null, expectedRevision: 4 });
    await expect(commandApi({ harness: { ...codexRow, adapter: "claude" } }).api.harnessSet("codex", "/opt/bin/codex", 4, "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(commandApi({}).api.harnessSet("codex", "/opt/bin/codex", 4, "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("reads the snapshot's harness rows in the fixed order and treats an absent field as none", () => {
    const dsh = { ...codexRow, adapter: "dsh", executable: "/usr/local/bin/dsh" };
    const rows = snapshotHarnesses({ harnesses: [codexRow, dsh] } as unknown as Snapshot);
    expect(rows.map(row => row.adapter)).toEqual(["dsh", "codex"]);
    expect(snapshotHarnesses({} as unknown as Snapshot)).toEqual([]);
    // A malformed row is dropped instead of presented as health.
    expect(snapshotHarnesses({ harnesses: [codexRow, { adapter: "zcode" }] } as unknown as Snapshot)
      .map(row => row.adapter)).toEqual(["codex"]);
  });
});
