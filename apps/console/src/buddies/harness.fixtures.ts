import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { vi } from "vitest";
import { App } from "../App";
import { ApiError } from "../api";
import type { ConsoleApi, HarnessHealth } from "../api";
import type { Buddy, Snapshot } from "../types";

/**
 * The snapshot and the client the harness view's tests share: four harnesses
 * that are found, missing, not logged in and not passing; a client whose
 * `capabilities` and `harness_set` behave as the board does; and a way to
 * answer any other command.
 */

export const CHECKED_AT = "2026-09-28T09:30:00.000Z";
const CODEX_EXECUTABLE = "/Users/fixture/.nvm/versions/node/v24/bin/codex";
const sonnet = { harness: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const mediumId = "claude:anthropic:claude-sonnet-5:medium";

export function harness(entry: Partial<HarnessHealth> & Pick<HarnessHealth, "harness" | "status">): HarnessHealth {
  return { revision: 1, manualPath: null, ...entry };
}

/** One harness that is found, one missing, one needing a login and one that does not pass. */
export function harnesses(): HarnessHealth[] {
  return [
    harness({ harness: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh",
      command: ["/usr/local/bin/dsh", "--version"], version: "0.4.2", source: "版本管理器（nvm 默认版本）",
      checkedAt: CHECKED_AT }),
    harness({ harness: "zcode", status: "missing", revision: 1, checkedAt: CHECKED_AT,
      reason: { code: "harness.not-found", params: { operation: "probe" } },
      candidates: [
        { path: "/Applications/ZCode.app/Contents/MacOS/zcode", source: "应用包", status: "missing", reason: { code: "harness.not-found", params: { operation: "probe" } } },
        { path: "/opt/homebrew/bin/zcode", source: "Homebrew", status: "missing" },
      ] }),
    harness({ harness: "codex", status: "login-required", revision: 2, executable: CODEX_EXECUTABLE,
      command: [CODEX_EXECUTABLE], version: "0.157.0", source: "PATH",
      reason: { code: "harness.login-required", params: { operation: "probe" } }, checkedAt: CHECKED_AT }),
    harness({ harness: "claude", status: "unhealthy", revision: 5, executable: "/Users/fixture/.claude/local/claude",
      version: "2.0.1", source: "手动路径", manualPath: "/Users/fixture/.claude/local/claude",
      reason: { code: "harness.probe-failed", params: { operation: "probe" } }, checkedAt: CHECKED_AT,
      candidates: [{ path: "/Users/fixture/.claude/local/claude", source: "手动路径", status: "failed", reason: { code: "harness.probe-failed", params: { operation: "probe" } } }] }),
  ];
}

type HarnessSnapshot = Snapshot & { harnesses: HarnessHealth[] };

export function snapshot(rows: HarnessHealth[] = harnesses()): HarnessSnapshot {
  const buddy: Buddy = { effortRank: null, buddyId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium", available: true,
    enabled: true, capabilities: ["execution:claude", "decision"], contextWindow: 200_000, description: "", source: "catalog:fixture" };
  return {
    csrfToken: "csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session" },

    configuration: { routerBuddyIds: [mediumId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard" },
    buddies: [buddy],
    preferences: [], modelServiceNotes: [], decisions: [],

    modelServiceConcurrency: [{ ...sonnet, limit: 2, active: 0 }],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
    harnesses: rows,
  };
}

type Reply = (params: Record<string, any>) => unknown;
type FixtureOptions = {
  harnesses?: HarnessHealth[];
  /** No browser session was supplied to the view. */
  missingSession?: boolean;
  failFirstCheck?: boolean;
  failFirstSet?: boolean;
  /** Answers for any other command, by operation. */
  replies?: Record<string, Reply>;
};

export function fixture(options: FixtureOptions = {}) {
  let state = snapshot(options.harnesses);
  if (options.missingSession) state = { ...state, consoleSession: undefined as unknown as Snapshot["consoleSession"] };
  const calls: { operation: string; params: Record<string, any> }[] = [];
  let checksToFail = options.failFirstCheck ? 1 : 0;
  let setsToFail = options.failFirstSet ? 1 : 0;
  /** One cheap check: a selected harness becomes found with a fresh reading. */
  function checked(harness?: string): HarnessHealth[] {
    return state.harnesses.map(row => harness === undefined || row.harness === harness
      ? { ...row, status: "ready" as const, revision: row.revision + 1,
          executable: `/opt/bin/${row.harness}`, version: "9.9.9", source: "PATH",
          manualPath: null, reason: null, candidates: [],
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
      state = { ...state, harnesses: checked(params.harness) };
      return { ...state.capabilities, harnesses: state.harnesses };
    }
    if (operation === "harness_set") {
      const harness = params.harness as string;
      if (setsToFail > 0) {
        setsToFail -= 1;
        throw new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。");
      }
      const path = params.path as string | null;
      state = { ...state, harnesses: state.harnesses.map(row => row.harness !== harness ? row : path
        ? { ...row, revision: row.revision + 1, status: "ready" as const, manualPath: path,
            executable: path, version: "1.0.0", source: "手动路径", reason: null,
            candidates: [], checkedAt: new Date().toISOString() }
        : { ...row, revision: row.revision + 1, status: "missing" as const, manualPath: null,
            executable: undefined, version: undefined, source: undefined, reason: { code: "harness.not-found", params: { operation: "probe" } },
            candidates: [],
            checkedAt: new Date().toISOString() }) };
      return { harness: state.harnesses.find(row => row.harness === harness)! };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const snapshotMock = vi.fn(async () => structuredClone(state));
  const api = {
    snapshot: snapshotMock,
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })),
    // Mirrors the real api helpers: the existing command route, no new path.
    harnessRefresh: vi.fn(async (csrfToken: string, harness?: string) => {
      const reply = await command("capabilities",
        harness === undefined ? { refresh: true } : { refresh: true, harness }, csrfToken) as { harnesses: HarnessHealth[] };
      return reply.harnesses;
    }),
    harnessSet: vi.fn(async (harness: string, path: string | null, csrfToken: string) =>
      (await command("harness_set", { harness, path }, csrfToken) as { harness: HarnessHealth }).harness),
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
      const name = { dsh: "DSH", codex: "Codex", claude: "Claude Code", zcode: "ZCode" }[first.harness] ?? first.harness;
      const summary = await screen.findByRole("button", { name: `${name} 账户` });
      if (summary.getAttribute("aria-expanded") !== "true") fireEvent.click(summary);
      await waitFor(() => screen.getByRole("group", { name: `${name} 设置` }));
    }
  }
}
