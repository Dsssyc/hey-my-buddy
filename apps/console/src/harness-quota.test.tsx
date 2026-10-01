import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { snapshotHarnesses } from "./api";
import type { ConsoleApi, HarnessHealth } from "./api";
import type { Profile, Snapshot } from "./types";

/**
 * ADR-018 §23 on the Buddy 配置 harness strip: the latest recorded quota
 * observation names its source, windows and reset times; a window at or above
 * 90% raises a reminder; a stale or unknown observation is never displayed as
 * 0% or as available; and a malformed observation is dropped as unknown
 * instead of blanking the page.
 */

const CHECKED_AT = "2026-09-29T09:30:00.000Z";
const flash = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const flashId = "dsh:deepseek-official:deepseek-flash:max";

function harness(entry: Partial<HarnessHealth> & Pick<HarnessHealth, "adapter" | "status">): HarnessHealth {
  return { available: entry.status === "ready", revision: 1, manualPath: null, ...entry };
}

function harnesses(): HarnessHealth[] {
  return [
    harness({ adapter: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh",
      version: "0.4.2", source: "PATH", checkedAt: CHECKED_AT, quota: {
        observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official", stale: false,
        windows: [
          { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" },
          { name: "weekly", usedPercent: 41, resetsAt: null },
        ],
      } }),
    harness({ adapter: "zcode", status: "missing", revision: 1, checkedAt: CHECKED_AT,
      reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI。", quota: {
        observedAt: "2026-09-20T09:00:00.000Z", source: "zcode-native", stale: true,
        windows: [{ name: "5h", usedPercent: null, resetsAt: null }],
      } }),
    harness({ adapter: "codex", status: "ready", revision: 2, executable: "/opt/bin/codex",
      version: "0.157.0", source: "PATH", checkedAt: CHECKED_AT, quota: null }),
    harness({ adapter: "claude", status: "unhealthy", revision: 5, reasonCode: "HARNESS_HANDSHAKE_FAILED",
      remedy: "检查该 CLI 能否独立运行。", checkedAt: CHECKED_AT }),
  ];
}

function profile(): Profile {
  return { profileId: flashId, label: "DeepSeek Flash · max", adapter: "dsh", provider: "deepseek-official",
    model: "deepseek-flash", effort: "max", available: true, enabled: true,
    capabilities: ["execution:dsh"], contextWindow: 200_000, description: "", source: "catalog:fixture" };
}

function snapshot(): Snapshot & { harnesses: HarnessHealth[] } {
  return {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileId: null,
      defaultRoutingMode: "fast", routingBudget: "standard" },
    profiles: [profile()],
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [],
    cards: [], evidence: [], decisions: [], sampleCounts: {},
    modelConcurrency: [{ ...flash, limit: 2, active: 0 }],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
    harnesses: harnesses(),
  };
}

function fixture() {
  const state = snapshot();
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command: vi.fn(async () => ({})),
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api };
}

async function openBuddy() {
  const user = userEvent.setup();
  window.location.hash = "#buddy/harness";
  render(<App suppliedApi={fixture().api} />);
  await screen.findByRole("region", { name: "Harness 状态" });
  return user;
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("harness quota observations", () => {
  it("warns about the near-limit window and names source, window and reset time", async () => {
    const user = await openBuddy();
    expect(screen.getByText("额度接近上限")).toBeTruthy();
    // The status line carries the reminder without expanding a row, and states
    // that it is the latest recorded observation.
    expect(screen.getByText(/额度提醒：DSH/)).toBeTruthy();
    const dsh = screen.getByText(/^找到：路径 \/usr\/local\/bin\/dsh/).closest("li")!;
    await user.click(screen.getByRole("button", { name: "DSH 检测详情" }));
    const facts = within(dsh).getByText("额度观测").closest("div")!;
    expect(within(facts).getByText(/最近一次观测 · 来源 dsh-native · deepseek-official/)).toBeTruthy();
    expect(within(facts).getByText("使用率 93%")).toBeTruthy();
    expect(within(facts).getByText("接近上限")).toBeTruthy();
    expect(within(facts).getByText("使用率 41%")).toBeTruthy();
    expect(within(facts).getByText(/重置 \d{2}-\d{2} \d{2}:\d{2}/)).toBeTruthy();
    expect(within(facts).getByText(/非实时/)).toBeTruthy();
  });

  it("never shows a stale unknown observation as 0% or as available", async () => {
    const user = await openBuddy();
    const zcode = screen.getByText(/未找到可执行文件/).closest("li")!;
    await user.click(screen.getByRole("button", { name: "ZCode 检测详情" }));
    expect(within(zcode).getByText("使用率未知")).toBeTruthy();
    expect(within(zcode).queryByText(/使用率 0%/)).toBeNull();
    expect(within(zcode).getByText(/最近一次观测，可能已过期 · 来源 zcode-native/)).toBeTruthy();
    expect(within(zcode).getAllByText(/已过期/).length).toBeGreaterThan(0);
    // The stale observation raises no near-limit reminder of its own: the only
    // reminder is DSH's row badge plus the status line naming DSH.
    expect(within(zcode).queryByText(/接近上限/)).toBeNull();
    expect(screen.getAllByText(/额度接近上限/)).toHaveLength(1);
    expect(screen.getByText(/额度提醒：DSH$/)).toBeTruthy();
  });

  it("reports a harness with no recorded observation as unknown", async () => {
    const user = await openBuddy();
    const codex = screen.getByText(/^找到：路径 \/opt\/bin\/codex/).closest("li")!;
    await user.click(screen.getByRole("button", { name: "Codex 检测详情" }));
    expect(within(codex).getByText("未记录观测（未知）")).toBeTruthy();
  });

  it("drops a malformed observation as unknown instead of failing the row", () => {
    const rows = snapshotHarnesses({
      harnesses: [harness({ adapter: "dsh", status: "ready",
        quota: { observedAt: CHECKED_AT, source: "dsh-native", windows: "near-limit" } as unknown as HarnessHealth["quota"] })],
    } as unknown as Snapshot);
    expect(rows).toHaveLength(1);
    expect(rows[0].quota ?? null).toBeNull();
    expect(snapshotHarnesses({ harnesses: [harness({ adapter: "dsh", status: "ready", quota: null })] } as unknown as Snapshot)[0].quota ?? null).toBeNull();
  });
});
