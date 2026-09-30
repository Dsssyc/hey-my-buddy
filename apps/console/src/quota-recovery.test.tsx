import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi, HarnessHealth } from "./api";
import { ApiError } from "./api";
import type { Profile, Snapshot } from "./types";

/**
 * ADR-019 second stage, the minimal quota recovery entry on the Buddy 配置
 * harness detail: one persisted exhaustion without a reset time explains its
 * single-use retry window, and 重新检测 opens that window honestly — no
 * balance query, no model call, never a claim that the quota recovered.
 * A read-only session never fires the command, and a failure stays visible.
 */

const CHECKED_AT = "2026-09-29T09:30:00.000Z";
const ELIGIBLE_AT = "2026-09-29T10:30:00.000Z";
const flash = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const flashId = "dsh:deepseek-official:deepseek-flash:max";

function harness(entry: Partial<HarnessHealth> & Pick<HarnessHealth, "adapter" | "status">): HarnessHealth {
  return { available: entry.status === "ready", revision: 1, manualPath: null, ...entry };
}

function exhaustedRow(): HarnessHealth {
  return harness({ adapter: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh",
    version: "0.4.2", source: "PATH", checkedAt: CHECKED_AT, quotaRouting: [
      { provider: "deepseek-official", limitId: null, code: "HARNESS_BALANCE_ZERO", source: "dsh-native",
        observedAt: "2026-09-29T09:00:00.000Z", resetsAt: null, blocked: true,
        retry: { eligibleAt: ELIGIBLE_AT, open: false, pendingManual: false, manualAt: null, consumedAt: null, consumedBy: null } },
    ] });
}

function harnesses(rows: HarnessHealth[]): HarnessHealth[] {
  const base = harness({ adapter: "codex", status: "ready", revision: 2, executable: "/opt/bin/codex",
    version: "0.157.0", source: "PATH", checkedAt: CHECKED_AT });
  return [base, ...rows];
}

function profile(): Profile {
  return { profileId: flashId, label: "DeepSeek Flash · max", adapter: "dsh", provider: "deepseek-official",
    model: "deepseek-flash", effort: "max", available: true, enabled: true,
    capabilities: ["execution:dsh"], contextWindow: 200_000, description: "", source: "catalog:fixture",
    quotaExhausted: true };
}

function snapshot(rows: HarnessHealth[]): Snapshot & { harnesses: HarnessHealth[] } {
  return {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, fastRouterProfileId: null, reviewRouterProfileId: null,
      defaultRoutingMode: "fast", routingBudget: "standard" },
    profiles: [profile()],
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [],
    cards: [], evidence: [], decisions: [], sampleCounts: {},
    modelConcurrency: [{ ...flash, limit: 2, active: 0 }],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
    harnesses: harnesses(rows),
  };
}

function fixture(state: Snapshot & { harnesses: HarnessHealth[] }, command: ConsoleApi["command"]) {
  return {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
}

async function openDetail(api: ConsoleApi) {
  const user = userEvent.setup();
  window.location.hash = "#buddy/harness";
  render(<App suppliedApi={api} />);
  await screen.findByRole("region", { name: "Harness 状态" });
  const dsh = screen.getByText(/^找到：路径 \/usr\/local\/bin\/dsh/).closest("li")!;
  await user.click(screen.getByRole("button", { name: "DSH 检测详情" }));
  return { user, dsh };
}

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("quota recovery entry", () => {
  it("reuses the request after a lost reply and malformed response", async () => {
    const command = vi.fn()
      .mockRejectedValueOnce(new ApiError("NETWORK_ERROR", "回复未知"))
      .mockResolvedValueOnce({})
      .mockResolvedValueOnce({ duplicate: true, quota: { opened: [], alreadyOpen: [] } });
    const { user, dsh } = await openDetail(fixture(snapshot([exhaustedRow()]), command as unknown as ConsoleApi["command"]));
    const button = within(dsh).getByRole("button", { name: "重新检测 deepseek-official 额度" });
    await user.click(button);
    await within(dsh).findByRole("alert");
    await user.click(button);
    await within(dsh).findByText("重新检测结果未知，请核对本次请求。");
    await user.click(button);
    expect(await within(dsh).findByText("本次重新检测已处理，以刷新后的状态为准。")).toBeTruthy();
    expect(command.mock.calls[0][1].requestId).toBe(command.mock.calls[1][1].requestId);
    expect(command.mock.calls[1][1].requestId).toBe(command.mock.calls[2][1].requestId);
  });
  it("explains the one-shot retry window from the recorded facts only", async () => {
    const { dsh } = await openDetail(fixture(snapshot([exhaustedRow()]), vi.fn(async () => ({})) as unknown as ConsoleApi["command"]));
    const panel = within(dsh).getByText("额度耗尽记录").closest("div")!;
    expect(within(panel).getByText(/余额为零/)).toBeTruthy();
    // dayClock renders the eligible time as "MM-DD HH:mm" in the local zone.
    const eligible = new Date(ELIGIBLE_AT);
    const pad = (part: number) => String(part).padStart(2, "0");
    const clock = `${pad(eligible.getMonth() + 1)}-${pad(eligible.getDate())} ${pad(eligible.getHours())}:${pad(eligible.getMinutes())}`;
    expect(within(panel).getByText(`${clock} 后允许一次自动路由重试`)).toBeTruthy();
    // The honesty line is always part of the entry.
    expect(within(panel).getByText(/不查询余额、不调用模型/)).toBeTruthy();
  });

  it("keeps a known reset on its original rule and never offers re-detection for it", async () => {
    const row = exhaustedRow();
    row.quotaRouting = [{ provider: "deepseek-official", limitId: null, code: "HARNESS_QUOTA_EXHAUSTED",
      source: "dsh-native", observedAt: "2026-09-29T09:00:00.000Z",
      resetsAt: "2026-09-29T13:00:00.000Z", blocked: true, retry: null }];
    const command = vi.fn(async () => ({}));
    const { dsh } = await openDetail(fixture(snapshot([row]), command as unknown as ConsoleApi["command"]));
    const panel = within(dsh).getByText("额度耗尽记录").closest("div")!;
    expect(within(panel).getByText(/已知重置时间/)).toBeTruthy();
    expect(within(panel).queryByRole("button", { name: "重新检测 deepseek-official 额度" })).toBeNull();
    expect(command).not.toHaveBeenCalled();
  });

  it("opens one window through quota_redetect and states it proves nothing", async () => {
    const command = vi.fn(async () => ({ quota: { adapter: "dsh", provider: "deepseek-official",
      records: [], opened: [null], alreadyOpen: [] } }));
    const { user, dsh } = await openDetail(fixture(snapshot([exhaustedRow()]), command as unknown as ConsoleApi["command"]));
    await user.click(within(dsh).getByRole("button", { name: "重新检测 deepseek-official 额度" }));
    expect(command).toHaveBeenCalledWith("quota_redetect",
      expect.objectContaining({ adapter: "dsh", provider: "deepseek-official", requestId: expect.any(String) }), "csrf");
    expect(await within(dsh).findByText(/已为 deepseek-official 开放一次重试机会/)).toBeTruthy();
    expect(within(dsh).getAllByText(/不代表额度已恢复/).length).toBeGreaterThan(0);
  });

  it("reports an already-open window without repeating the operation", async () => {
    const command = vi.fn(async () => ({ quota: { adapter: "dsh", provider: "deepseek-official",
      records: [], opened: [], alreadyOpen: [null] } }));
    const { user, dsh } = await openDetail(fixture(snapshot([exhaustedRow()]), command as unknown as ConsoleApi["command"]));
    await user.click(within(dsh).getByRole("button", { name: "重新检测 deepseek-official 额度" }));
    expect(await within(dsh).findByText(/重试窗口已是开放状态/)).toBeTruthy();
  });

  it("never fires the command from a read-only session and says why", async () => {
    const command = vi.fn(async () => ({}));
    const state = snapshot([exhaustedRow()]);
    state.consoleSession = { id: "expired", canWrite: false, reason: null };
    const { user, dsh } = await openDetail(fixture(state, command as unknown as ConsoleApi["command"]));
    const button = within(dsh).getByRole("button", { name: "重新检测 deepseek-official 额度" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    await user.click(button);
    expect(command).not.toHaveBeenCalled();
    expect(await within(dsh).findByRole("alert")).toBeTruthy();
  });

  it("keeps a failed re-detect visible instead of inventing a window", async () => {
    const command = vi.fn(async () => { throw new ApiError("CONFLICT", "服务拒绝"); });
    const { user, dsh } = await openDetail(fixture(snapshot([exhaustedRow()]), command as unknown as ConsoleApi["command"]));
    await user.click(within(dsh).getByRole("button", { name: "重新检测 deepseek-official 额度" }));
    expect((await within(dsh).findByRole("alert")).textContent).toBeTruthy();
    expect(within(dsh).queryByText(/已为 deepseek-official 开放一次重试机会/)).toBeNull();
  });

  it("drops malformed recovery facts instead of presenting them", async () => {
    const malformed = exhaustedRow();
    malformed.quotaRouting = [{ provider: "deepseek-official", limitId: null, code: "HARNESS_BALANCE_ZERO",
      source: "dsh-native", observedAt: CHECKED_AT, resetsAt: null, blocked: true,
      retry: { eligibleAt: 42, open: "yes" } as unknown as never }];
    const { dsh } = await openDetail(fixture(snapshot([malformed]), vi.fn(async () => ({})) as unknown as ConsoleApi["command"]));
    expect(within(dsh).queryByText("额度耗尽记录")).toBeNull();
    expect(within(dsh).queryByRole("button", { name: "重新检测 deepseek-official 额度" })).toBeNull();
  });
});
