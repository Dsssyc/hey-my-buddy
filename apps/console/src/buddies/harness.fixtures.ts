import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { vi } from "vitest";
import { App } from "../App";
import { ApiError } from "../api";
import type { ConsoleApi, HarnessHealth } from "../api";
import type { Profile, Snapshot } from "../types";

/**
 * The snapshot and the client the harness view's tests share: four harnesses
 * that are found, missing, not logged in and not passing; a client whose
 * `capabilities` and `harness_set` behave as the board does; and a way to
 * answer any other command.
 */

export const CHECKED_AT = "2026-09-28T09:30:00.000Z";
export const CODEX_EXECUTABLE = "/Users/fixture/.nvm/versions/node/v24/bin/codex";
const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const mediumId = "claude:anthropic:claude-sonnet-5:medium";

export function harness(entry: Partial<HarnessHealth> & Pick<HarnessHealth, "adapter" | "status">): HarnessHealth {
  return { available: entry.status === "ready", revision: 1, manualPath: null, ...entry };
}

/** One harness that is found, one missing, one needing a login and one that does not pass. */
export function harnesses(): HarnessHealth[] {
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
    harness({ adapter: "codex", status: "login-required", revision: 2, executable: CODEX_EXECUTABLE,
      command: [CODEX_EXECUTABLE], version: "0.157.0", source: "PATH",
      reasonCode: "HARNESS_LOGIN_REQUIRED", remedy: "先运行 codex login 完成登录，再检测。", checkedAt: CHECKED_AT }),
    harness({ adapter: "claude", status: "unhealthy", revision: 5, executable: "/Users/fixture/.claude/local/claude",
      version: "2.0.1", source: "手动路径", manualPath: "/Users/fixture/.claude/local/claude",
      reasonCode: "HARNESS_HANDSHAKE_FAILED", remedy: "检查该 CLI 能否独立运行。", checkedAt: CHECKED_AT,
      candidates: [{ path: "/Users/fixture/.claude/local/claude", source: "手动路径", status: "failed", reasonCode: "handshake_failed" }] }),
  ];
}

export type HarnessSnapshot = Snapshot & { harnesses: HarnessHealth[] };

export function snapshot(rows: HarnessHealth[] = harnesses()): HarnessSnapshot {
  const profile: Profile = { profileId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium", available: true,
    enabled: true, capabilities: ["execution:claude", "decision"], contextWindow: 200_000, description: "", source: "catalog:fixture" };
  return {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [mediumId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard" },
    profiles: [profile],
    preferences: [], familyPreferences: [], preferenceOverrides: [], cards: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {},
    modelConcurrency: [{ ...sonnet, limit: 2, active: 0 }],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
    harnesses: rows,
  };
}

type Reply = (params: Record<string, any>) => unknown;
export type FixtureOptions = {
  harnesses?: HarnessHealth[];
  /** The session cannot write. */
  readOnly?: boolean;
  failFirstCheck?: boolean;
  conflictOnce?: boolean;
  /** Answers for any other command, by operation. */
  replies?: Record<string, Reply>;
};

export function fixture(options: FixtureOptions = {}) {
  let state = snapshot(options.harnesses);
  if (options.readOnly) state = { ...state, consoleSession: { id: "expired", canWrite: false, reason: null } };
  const calls: { operation: string; params: Record<string, any> }[] = [];
  let checksToFail = options.failFirstCheck ? 1 : 0;
  let setsToConflict = options.conflictOnce ? 1 : 0;
  /** One cheap check: a selected harness becomes found with a fresh reading. */
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
    if (options.replies?.[operation]) return options.replies[operation](params);
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

/** Opens the page at an address and waits for the harness view. */
export async function openHarness(f: ReturnType<typeof fixture>, hash = "#buddies", expand = true) {
  window.location.hash = hash;
  render(createElement(App, { suppliedApi: f.api }));
  await screen.findByRole("region", { name: "目录" });
  if (expand && !hash.startsWith("#buddies/catalog/")) {
    const first = f.state().harnesses[0];
    if (first) {
      const name = { dsh: "DSH", codex: "Codex", claude: "Claude Code", zcode: "ZCode" }[first.adapter] ?? first.adapter;
      const summary = await screen.findByRole("button", { name: `${name} 账户` });
      if (summary.getAttribute("aria-expanded") !== "true") fireEvent.click(summary);
      await waitFor(() => screen.getByRole("group", { name: `${name} 设置` }));
    }
  }
}
