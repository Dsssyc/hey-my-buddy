import { cleanup, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import type { HarnessHealth, QuotaRoutingRecord } from "../types";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import { CHECKED_AT, fixture, harness, openHarness } from "./harness.fixtures";
import { fieldValue, harnessButton, harnessPane, harnessSection, paneBlock, selectHarness, toolbarAlert } from "./harness.testing";

/**
 * ADR-018 §23 on the harness table: the latest recorded quota observation names
 * its source, windows and reset times; a window at or above 90% is near its
 * limit; a stale or unknown observation is never shown as 0% or as available.
 * ADR-019, second stage: one persisted exhaustion without a recorded reset has a
 * single-use retry window, which "开放重试" opens honestly: no balance query, no
 * model call, never a claim that the quota recovered.
 */

const ELIGIBLE_AT = "2026-09-29T10:30:00.000Z";
const clock = (iso: string) => {
  const date = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  sessionStorage.clear();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

function observed(): HarnessHealth[] {
  return [
    harness({ harness: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH", checkedAt: CHECKED_AT,
      quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official", stale: false, windows: [
        { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" },
        { name: "weekly", usedPercent: 41, resetsAt: null },
      ] } }),
    harness({ harness: "zcode", status: "missing", revision: 1, checkedAt: CHECKED_AT, reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI。",
      quota: { observedAt: "2026-09-20T09:00:00.000Z", source: "zcode-native", stale: true, windows: [{ name: "5h", usedPercent: null, resetsAt: null }] } }),
    harness({ harness: "codex", status: "ready", revision: 2, executable: "/opt/bin/codex", version: "0.157.0", source: "PATH", checkedAt: CHECKED_AT, quota: null }),
    harness({ harness: "claude", status: "unhealthy", revision: 5, reasonCode: "HARNESS_HANDSHAKE_FAILED", remedy: "检查该 CLI 能否独立运行。", checkedAt: CHECKED_AT }),
  ];
}

describe("quota observations", () => {
  it("warns about the near-limit window in the table and names source, window and reset time in the side column", async () => {
    await openHarness(fixture({ harnesses: observed() }));
    const cell = harnessButton("DSH");
    expect(cell.textContent).toContain(`93%重置 ${clock("2026-09-29T13:00:00.000Z")}`);
    // The nearest window is drawn hot: its bar and its percentage.
    expect(within(cell).getByText("93%").className).toContain("text-hand");

    // The Harness table carries the mark without a row being opened, and names the harness.
    expect(within(harnessButton("DSH")).getByText("93%").className).toContain("text-hand");

    const quota = paneBlock("额度");
    const windows = ["5h", "weekly"].map(name => fieldValue(quota, name));
    expect(windows[0].textContent).toBe(`93%重置 ${clock("2026-09-29T13:00:00.000Z")}接近上限`);
    expect(within(windows[0]).getByText("接近上限").className).toContain("text-hand");
    expect(within(windows[0]).getByText("93%").className).toContain("text-hand");
    expect(windows[1].textContent).toBe("41%重置 未记录");
    expect(within(windows[1]).getByText("41%").className).not.toContain("text-hand");
    expect(fieldValue(quota, "来源").textContent).toBe("dsh-native · deepseek-official");
    expect(within(quota).getByText("观测").nextElementSibling!.textContent).toBe(clock("2026-09-29T09:00:00.000Z"));
    // The board's note that this is an observation, not a live reading, is on the word 观测.
    expect(within(quota).getByText("观测").getAttribute("aria-description")).toBe("最近额度观测 · 非实时");
    expect(within(quota).queryByText("过期")).toBeNull();
  });

  it.each([false, true])("uses balance facts for a metered account with balanceZero=%s", async balanceZero => {
    const rows = [harness({ harness: "dsh", status: "ready", billingByProvider: {
      "deepseek-official": { kind: "metered", source: "native", observedAt: CHECKED_AT },
    }, quota: { source: "dsh-native", observedAt: CHECKED_AT, balanceZero, windows: [] } })];
    await openHarness(fixture({ harnesses: rows }));
    expect(harnessButton("DSH").textContent).toContain("余额");
    expect(harnessButton("DSH").textContent).toContain(balanceZero ? "余额为零" : "未知");
    expect(harnessButton("DSH").textContent).not.toMatch(/\d+%|¥|\$/);
  });

  it("keeps a stale metered balance factual without near-limit colour", async () => {
    const rows = [harness({ harness: "dsh", status: "ready", billingByProvider: {
      "deepseek-official": { kind: "metered", source: "native", observedAt: CHECKED_AT },
    }, quota: { source: "dsh-native", observedAt: CHECKED_AT, stale: true, balanceZero: true, windows: [] } })];
    await openHarness(fixture({ harnesses: rows }));
    const summary = harnessButton("DSH");
    const value = within(summary).getByText("余额为零");
    expect(value.className).not.toContain("text-hand");
    expect(within(summary).getByText("过期")).toBeTruthy();
    expect(summary.textContent).not.toContain("余额 余额");
  });

  it("says a window at its limit and a limit the native record reports", async () => {
    const rows = [harness({ harness: "dsh", status: "ready", quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", reachedType: "usage_limit_reached",
      windows: [{ name: "5h", usedPercent: 100, resetsAt: null }, { name: "weekly", usedPercent: 12, resetsAt: null }] } })];
    await openHarness(fixture({ harnesses: rows }));
    const quota = paneBlock("额度");
    expect(within(fieldValue(quota, "5h")).getByText("已到上限")).toBeTruthy();
    expect(within(fieldValue(quota, "weekly")).queryByText(/上限/)).toBeNull();
    expect(within(quota).getByText("已报告限额").className).toContain("text-hand");
    expect(within(quota).getByText("观测").getAttribute("aria-description")).toContain("原生记录报告额度限制");
    // The table shows the window nearest the limit, hot.
    expect(within(harnessButton("DSH")).getByText("100%").className).toContain("text-hand");
  });

  it("never shows a stale unknown observation as 0% or as available", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: observed() }));
    const cell = harnessButton("ZCode");
    expect(cell.textContent).toContain("未知过期");
    expect(within(cell).queryByText("接近上限")).toBeNull();
    expect(cell.textContent).not.toMatch(/0%/);
    expect(within(cell).getByText("未知").className).not.toContain("text-hand");
    await user.click(harnessButton("DSH"));
    await selectHarness(user, "ZCode");
    const quota = paneBlock("额度");
    expect(fieldValue(quota, "5h").textContent).toBe("未知重置 未记录");
    expect(within(quota).getByText("观测").nextElementSibling!.textContent).toBe(clock("2026-09-20T09:00:00.000Z"));
    expect(within(quota).getByText("过期")).toBeTruthy();
    expect(within(quota).getByText("观测").getAttribute("aria-description")).toBe("额度观测已过期");
    expect(quota.textContent).not.toMatch(/0%|接近上限|已到上限/);
    // The stale observation raises no reminder of its own: the only one is DSH's.
    expect(within(harnessButton("DSH")).getByText("93%").className).toContain("text-hand");
  });

  it("does not draw a stale reading as near its limit", async () => {
    const rows = [harness({ harness: "dsh", status: "ready", quota: { observedAt: "2026-09-20T09:00:00.000Z", source: "dsh-native", stale: true,
      windows: [{ name: "5h", usedPercent: 97, resetsAt: null }] } })];
    await openHarness(fixture({ harnesses: rows }));
    expect(harnessButton("DSH").textContent).toContain("97%过期");
    expect(within(harnessButton("DSH")).getByText("97%").className).not.toContain("text-hand");
    expect(within(harnessButton("DSH")).getByText("97%").className).not.toContain("text-hand");
  });

  it("reports a harness with no recorded observation as not recorded, never as 0", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: observed() }));
    expect(harnessButton("Codex").textContent).toContain("—");
    await user.click(harnessButton("DSH"));
    await selectHarness(user, "Codex");
    expect(paneBlock("额度").textContent).toBe("额度未记录");
    expect(paneBlock("额度").textContent).not.toMatch(/0%/);
  });
});

function exhausted(extra: Partial<QuotaRoutingRecord> = {}): QuotaRoutingRecord {
  return { provider: "deepseek-official", limitId: null, code: "HARNESS_BALANCE_ZERO", source: "dsh-native",
    observedAt: "2026-09-29T09:00:00.000Z", resetsAt: null, blocked: true,
    retry: { eligibleAt: ELIGIBLE_AT, open: false, pendingManual: false, manualAt: null, consumedAt: null, consumedBy: null }, ...extra };
}
function withRecords(...records: QuotaRoutingRecord[]): HarnessHealth[] {
  return [
    harness({ harness: "dsh", status: "ready", revision: 3, executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH", checkedAt: CHECKED_AT, quotaRouting: records }),
    harness({ harness: "codex", status: "ready", revision: 2, executable: "/opt/bin/codex", version: "0.157.0", source: "PATH", checkedAt: CHECKED_AT }),
  ];
}
const list = () => within(paneBlock("额度")).getByRole("list", { name: "额度耗尽记录" });
const open = () => within(paneBlock("额度")).getByRole("button", { name: "开放重试" });

describe("an exhausted quota", () => {
  it("lists each record with its provider, its word, its observation and where its recovery stands", async () => {
    await openHarness(fixture({ harnesses: withRecords(exhausted(), exhausted({ provider: "other", limitId: "tokens", code: "HARNESS_QUOTA_EXHAUSTED" })) }));
    const [first, second] = within(list()).getAllByRole("listitem");
    expect(first.textContent).toBe(`deepseek-official余额为零观测 ${clock("2026-09-29T09:00:00.000Z")} · dsh-native${clock(ELIGIBLE_AT)} 后可重试`);
    expect(within(first).getByText("余额为零").className).toContain("text-hand");
    expect(second.textContent).toContain("other · tokens额度耗尽");
    expect(harnessSection("DSH").querySelector("[data-slot=harness-header]")!.textContent).toContain(clock(ELIGIBLE_AT));
  });

  it("explains the one-shot retry window from the recorded facts only, and states what opening it proves", async () => {
    await openHarness(fixture({ harnesses: withRecords(exhausted()) }));
    const hint = open().getAttribute("aria-description")!;
    expect(hint).toBe("只开放一次后续正常调用的重试机会：不查询余额、不调用模型，也不代表额度已恢复；只有更新的可用观测才会清除耗尽。");
    // No other sentence about it stands on the page.
    expect(within(harnessPane()).queryByText(/不查询余额/)).toBeNull();
  });

  it("names the provider on the button only when there is more than one", async () => {
    await openHarness(fixture({ harnesses: withRecords(exhausted(), exhausted({ provider: "other" })) }));
    expect(within(paneBlock("额度")).getAllByRole("button").map(button => button.textContent)).toEqual(["开放重试 deepseek-official", "开放重试 other"]);
  });

  it("keeps a known reset on its original rule and never offers a retry for it", async () => {
    const command = vi.fn(async () => ({}));
    await openHarness(fixture({ harnesses: withRecords(exhausted({ code: "HARNESS_QUOTA_EXHAUSTED", resetsAt: "2026-09-29T13:00:00.000Z", retry: null })), replies: { quota_redetect: command } }));
    expect(within(list()).getByText(`重置 ${clock("2026-09-29T13:00:00.000Z")}`)).toBeTruthy();
    expect(within(paneBlock("额度")).queryByRole("button", { name: /开放重试/ })).toBeNull();
    expect(command).not.toHaveBeenCalled();
  });

  it("opens one window through quota_redetect and says it proves nothing", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => ({ quota: { harness: "dsh", provider: "deepseek-official", records: [], opened: [null], alreadyOpen: [] } }) } });
    await openHarness(f);
    await user.click(open());
    expect(f.command).toHaveBeenCalledWith("quota_redetect",
      expect.objectContaining({ harness: "dsh", provider: "deepseek-official", requestId: expect.any(String) }), "csrf");
    expect((await screen.findByText(/已为 deepseek-official 开放一次重试机会/)).textContent).toContain("不代表额度已恢复");
    // The snapshot is read again, and the request number is let go.
    expect(f.snapshotMock.mock.calls.length).toBeGreaterThan(1);
    expect(sessionStorage.length).toBe(0);
  });

  it("reports an already-open window without repeating the operation", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => ({ quota: { opened: [], alreadyOpen: [null] } }) } });
    await openHarness(f);
    await user.click(open());
    expect(await screen.findByText("deepseek-official 的重试窗口已是开放状态，无需重复操作。")).toBeTruthy();
    expect(f.calls.filter(call => call.operation === "quota_redetect")).toHaveLength(1);
  });

  it("reports a record that needs no retry", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => ({ quota: { opened: [], alreadyOpen: [] } }) } });
    await openHarness(f);
    await user.click(open());
    expect(await screen.findByText("deepseek-official 当前没有需要开放重试的耗尽记录。")).toBeTruthy();
  });

  it("reuses the request after a lost reply and after a reply it cannot read, and lets it go once understood", async () => {
    const user = userEvent.setup();
    const replies = [
      () => { throw new ApiError("NETWORK_ERROR", "回复未知"); },
      () => ({}),
      () => ({ duplicate: true, quota: { opened: [], alreadyOpen: [] } }),
    ];
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => replies.shift()!() } });
    await openHarness(f);
    await user.click(open());
    expect((await toolbarAlert()).textContent).toContain("回复未知");
    expect(sessionStorage.length).toBe(1);
    await user.click(open());
    await screen.findByText("开放重试的结果未知，请核对本次请求。");
    expect(sessionStorage.length).toBe(1);
    await user.click(open());
    expect(await screen.findByText("本次请求已处理，以刷新后的状态为准。")).toBeTruthy();
    const ids = f.calls.filter(call => call.operation === "quota_redetect").map(call => call.params.requestId);
    expect(ids).toHaveLength(3);
    expect(new Set(ids).size).toBe(1);
    expect(sessionStorage.length).toBe(0);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("retries the same request from the notice under the toolbar", async () => {
    const user = userEvent.setup();
    const replies = [() => { throw new ApiError("NETWORK_ERROR", "回复未知"); }, () => ({ quota: { opened: [null], alreadyOpen: [] } })];
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => replies.shift()!() } });
    await openHarness(f);
    await user.click(open());
    await toolbarAlert();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText(/已为 deepseek-official 开放一次重试机会/);
    const calls = f.calls.filter(call => call.operation === "quota_redetect");
    expect(calls[0].params).toEqual(calls[1].params);
  });

  it("starts a new request when the one the session kept is not a request number", async () => {
    const user = userEvent.setup();
    sessionStorage.setItem("hey-my-buddy-quota-redetect:dsh:deepseek-official", "not a valid id!");
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => ({ quota: { opened: [null], alreadyOpen: [] } }) } });
    await openHarness(f);
    await user.click(open());
    await screen.findByText(/已为 deepseek-official/);
    expect(f.calls.find(call => call.operation === "quota_redetect")!.params.requestId).not.toBe("not a valid id!");
  });

  it("says it cannot keep the request number when the session will not store it, and sends nothing", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()) });
    await openHarness(f);
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    await user.click(open());
    expect((await toolbarAlert()).textContent).toContain("无法保留请求编号，请允许本页会话存储后重试。");
    expect(f.calls).toEqual([]);
  });

  it("never fires the command from a session that cannot write, and says why", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()), readOnly: true });
    await openHarness(f);
    expect(open().getAttribute("aria-disabled")).toBe("true");
    expect(open().getAttribute("aria-description")).toContain(LOGIN_EXPIRED_ACTION_REFUSAL());
    expect(open().getAttribute("aria-description")).toContain("不代表额度已恢复");
    await user.click(open());
    expect(await screen.findByText(LOGIN_EXPIRED_ACTION_REFUSAL())).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps a failed request visible instead of inventing a window", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => { throw new ApiError("CONFLICT", "服务拒绝"); } } });
    await openHarness(f);
    await user.click(open());
    expect((await toolbarAlert()).textContent).toContain("记录冲突；请刷新核对后重试。");
    expect(screen.queryByText(/已为 deepseek-official 开放一次重试机会/)).toBeNull();
  });

  it("drops malformed recovery facts instead of presenting them", async () => {
    const malformed = exhausted({ retry: { eligibleAt: 42, open: "yes" } as unknown as never });
    await openHarness(fixture({ harnesses: withRecords(malformed) }));
    expect(within(paneBlock("额度")).queryByRole("list", { name: "额度耗尽记录" })).toBeNull();
    expect(within(paneBlock("额度")).queryByRole("button", { name: /开放重试/ })).toBeNull();
  });

  it("lets no second action start while the request is under way", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    const f = fixture({ harnesses: withRecords(exhausted()), replies: { quota_redetect: () => new Promise(resolve => { finish = resolve; }) } });
    await openHarness(f);
    await user.click(open());
    await waitFor(() => expect(f.calls).toHaveLength(1));
    expect(open().getAttribute("aria-disabled")).toBe("true");
    await user.click(open());
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    expect(f.calls).toHaveLength(1);
    finish({ quota: { opened: [null], alreadyOpen: [] } });
    await screen.findByText(/已为 deepseek-official/);
  });
});
