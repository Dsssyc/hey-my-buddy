import { reasonText } from "../i18n/reason";
import i18n from "i18next";
import { describe, expect, it } from "vitest";
import type { HarnessAccount } from "../harness-account";
import { quotaView } from "../quota";
import type { HarnessHealth, HarnessQuota, QuotaRoutingRecord } from "../types";
import {
  absolutePath, harnessDetailsView, harnessPath, harnessSummary, harnessTime, recoveryText, retryProviders, statusText,
} from "./harness";

/**
 * What the harness view is made of, apart from how it is drawn: how a time and a
 * state are worded, which quota window the table shows, what the side column
 * says of a harness that is found and of one that is not, and where each of the
 * six recovery states of an exhausted quota stands.
 */

const t = i18n.getFixedT(null, "buddies");
const clock = (iso: string) => {
  const date = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

function row(entry: Partial<HarnessHealth> = {}): HarnessHealth {
  return { harness: "dsh", status: "ready", available: true, revision: 1, manualPath: null, ...entry };
}
function quota(entry: Partial<HarnessQuota> = {}): HarnessQuota {
  return { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", windows: [], ...entry };
}
const account: HarnessAccount = { harness: "dsh", status: "ready",
  accountType: null, checkedAt: null, reason: null };

describe("times and words", () => {
  it("writes a time as month-day and hour-minute, and nothing for what is not a time", () => {
    expect(harnessTime("2026-09-28T09:30:00.000Z")).toBe(clock("2026-09-28T09:30:00.000Z"));
    expect(harnessTime(null)).toBeNull();
    expect(harnessTime("")).toBeNull();
    expect(harnessTime("not a time")).toBeNull();
  });

  it("takes a path to be absolute when it starts at the root or at a drive", () => {
    for (const path of ["/usr/bin/codex", "C:\\tools\\codex.exe", "d:/tools/codex.exe"]) expect(absolutePath(path)).toBe(true);
    for (const path of ["codex", "./codex", "bin/codex", "C:codex", ""]) expect(absolutePath(path)).toBe(false);
  });

  it("words the five states and leaves one it does not know as recorded", () => {
    expect(["ready", "missing", "login-required", "unhealthy", "unknown", "paused"].map(status => statusText(status, t)))
      .toEqual(["已找到", "未找到", "未登录", "未通过", "未检测", "paused"]);
  });

  it("names the chosen executable, else the launched command, else the manual path", () => {
    expect(harnessPath(row({ executable: " /opt/bin/dsh ", command: ["/other"] }))).toBe("/opt/bin/dsh");
    expect(harnessPath(row({ command: ["/other", "--version"] }))).toBe("/other");
    expect(harnessPath(row({ manualPath: "/manual/dsh" }))).toBe("/manual/dsh");
    expect(harnessPath(row())).toBeNull();
  });
});

describe("a harness summary", () => {
  it("writes each recorded harness state and reads its native login state in details", () => {
    expect(statusText("ready", t)).toBe("已找到");
    expect(statusText("login-required", t)).toBe("未登录");
    expect(statusText("paused", t)).toBe("paused");
    expect(harnessDetailsView(row(), t).account).toBeNull();
    expect(harnessDetailsView(row({ account }), t).account).toMatchObject({ state: "已配置" });
    expect(harnessDetailsView(row({ account: { ...account, status: "logged-out" } }), t).account)
      .toMatchObject({ state: "未登录" });
    expect(harnessDetailsView(row({ account: { ...account, status: "unsupported" } }), t).account?.state).toBe("未验证");
    expect(harnessDetailsView(row({ account: { ...account, status: "unknown" } }), t).account?.state).toBe("未知");
  });

  it("shows no quota for a harness without an observation", () => {
    expect(harnessSummary(row({ quota: null }), t)).toBeNull();
    expect(harnessSummary(row(), t)).toBeNull();
  });

  it("shows the window nearest its limit, hot from the near-limit threshold", () => {
    const cell = (windows: HarnessQuota["windows"], extra: Partial<HarnessQuota> = {}) =>
      harnessSummary(row({ quota: quota({ windows, ...extra }) }), t);
    expect(cell([{ name: "5h", usedPercent: 41, resetsAt: null }, { name: "weekly", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" }]))
      .toEqual({ used: 93, text: "93%", hot: true, stale: false, resets: clock("2026-09-29T13:00:00.000Z") });
    expect(cell([{ name: "5h", usedPercent: 89, resetsAt: null }])).toMatchObject({ used: 89, hot: false, resets: null });
    expect(cell([{ name: "5h", usedPercent: 90, resetsAt: null }])).toMatchObject({ hot: true });
    expect(cell([{ name: "5h", usedPercent: 100, resetsAt: null }])).toMatchObject({ used: 100, hot: true });
  });

  it("writes an unknown use as unknown, with no share that could read as 0", () => {
    const cell = harnessSummary(row({ quota: quota({ windows: [{ name: "5h", usedPercent: null, resetsAt: null }] }) }), t);
    expect(cell).toEqual({ used: null, text: "未知", hot: false, stale: false, resets: null });
    // An observation with no window says the same.
    expect(harnessSummary(row({ quota: quota() }), t)).toMatchObject({ used: null, text: "未知" });
    // A window with a known use outranks one without, whichever comes first.
    expect(harnessSummary(row({ quota: quota({ windows: [{ name: "a", usedPercent: null, resetsAt: null }, { name: "b", usedPercent: 12, resetsAt: null }] }) }), t))
      .toMatchObject({ used: 12, text: "12%" });
  });

  it("marks a stale observation and never lets it raise the near-limit colour", () => {
    expect(harnessSummary(row({ quota: quota({ stale: true, windows: [{ name: "5h", usedPercent: 97, resetsAt: null }] }) }), t))
      .toMatchObject({ used: 97, hot: false, stale: true });
    expect(harnessSummary(row({ quota: quota({ windows: [{ name: "5h", usedPercent: 97, resetsAt: null, stale: true }] }) }), t))
      .toMatchObject({ hot: false, stale: true });
  });

  it("makes the row hot when the native record reports a limit, even below the threshold", () => {
    expect(harnessSummary(row({ quota: quota({ reachedType: "usage_limit_reached", windows: [{ name: "5h", usedPercent: 10, resetsAt: null }] }) }), t))
      .toMatchObject({ hot: true });
  });
});

describe("harness details", () => {
  it("says where a harness is, how it was found and whether local review is possible", () => {
    const view = harnessDetailsView(row({ executable: "/usr/local/bin/dsh", source: "PATH", checkedAt: "2026-09-28T09:30:00.000Z",
      readOnlyStructured: { eligible: true, systemSandbox: true, reason: null, sameAttemptContinuation: true } }), t);
    expect(view.location).toMatchObject({ path: "/usr/local/bin/dsh", source: "PATH", checked: clock("2026-09-28T09:30:00.000Z"), exception: null });
    expect(view.location.review).toEqual({ text: "可用", reason: null,
      note: "本地资格检查不调用模型，不代表原生调用已验证。 具有原生系统沙盒；实际策略由每次运行核对。" });
  });

  it("calls a manually chosen path manual and keeps what is not recorded unrecorded", () => {
    const view = harnessDetailsView(row({ manualPath: "/manual/dsh", source: "手动路径" }), t);
    expect(view.location).toMatchObject({ path: "/manual/dsh", source: "手动", checked: null });
    expect(harnessDetailsView(row(), t).location).toMatchObject({ path: null, source: "未记录" });
  });

  it("words review as eligible, not eligible with its reason, or unknown, and keeps the sandbox boundary", () => {
    const eligibility = { eligible: false, systemSandbox: false, reason: { code: "harness.readonly-resource-missing", params: {} }, sameAttemptContinuation: true };
    const view = harnessDetailsView(row({ readOnlyStructured: eligibility }), t);
    expect(view.location.review).toMatchObject({ text: "不可用", reason: reasonText(eligibility.reason) });
    expect(view.location.review.note).toContain("本地资格检查不调用模型，不代表原生调用已验证。");
    expect(view.location.review.note).toContain("无系统沙盒，不能保证阻止副本外读取或外传；黑板的事后判定只覆盖上报的工具事件。");
    // Without a recorded eligibility nothing is said about the sandbox, and nothing reads as available.
    const unknown = harnessDetailsView(row(), t).location.review;
    expect(unknown).toEqual({ text: "未知", reason: null, note: "本地资格检查不调用模型，不代表原生调用已验证。" });
  });

  it("adds the reason, the remedy and the places tried only for a harness that is not found", () => {
    const view = harnessDetailsView(row({ status: "missing", available: false, reason: { code: "harness.not-found", params: { operation: "probe" } },
      candidates: [
        { path: "/a/zcode", source: "应用包", status: "missing", reason: { code: "harness.not-found", params: { operation: "probe" } } },
        { path: "/b/zcode", source: "Homebrew", status: "failed", reason: { code: "harness.probe-failed", params: { operation: "probe" } } },
        { status: "unknown" },
      ] }), t);
    expect(view.location.exception).toEqual({ reason: reasonText({ code: "harness.not-found", params: { operation: "probe" } }), tried: [
      { path: "/a/zcode", source: "应用包", result: reasonText({ code: "harness.not-found", params: { operation: "probe" } }) },
      { path: "/b/zcode", source: "Homebrew", result: reasonText({ code: "harness.probe-failed", params: { operation: "probe" } }) },
      { path: null, source: null, result: "未检测" },
    ] });
  });

  it("takes the program reason from the current reply without inventing a second cause", () => {
    const view = harnessDetailsView(row({ status: "unknown", available: false,
      reason: { code: "harness.not-checked", params: { operation: "probe" } } }), t).location.exception!;
    expect(view.reason).toBe(reasonText({ code: "harness.not-checked", params: { operation: "probe" } }));
    expect(harnessDetailsView(row({ status: "unhealthy", available: false }), t).location.exception!.reason).toBeNull();
  });

  it("writes the account's status, type and check, the billing of each provider and the guidance", () => {
    expect(harnessDetailsView(row(), t).account).toBeNull();
    const view = harnessDetailsView(row({ account: { ...account, status: "ready", accountType: "chatgpt", checkedAt: "2026-09-29T09:00:00.000Z" },
      billingByProvider: { openai: { kind: "subscription", source: "native", observedAt: "x" }, deepseek: { kind: "metered", source: "native", observedAt: "x" },
        other: { kind: "unknown", source: "native", observedAt: "x" } } }), t).account!;
    expect(view).toMatchObject({ state: `已配置 · ChatGPT · ${clock("2026-09-29T09:00:00.000Z")}`,
      billing: [{ provider: "openai", plan: "订阅" }, { provider: "deepseek", plan: "按量" }, { provider: "other", plan: "未知" }] });
    expect(harnessDetailsView(row({ account: { ...account, accountType: "mystery" } }), t).account?.state).toBe("已配置 · 未知");
    expect(harnessDetailsView(row({ account }), t).account?.billing).toEqual([]);
    const loggedOut = harnessDetailsView(row({ account: { ...account, status: "logged-out",
      reason: { code: "account.login-required", params: {} } } }), t).account!;
    expect(loggedOut.reason).toBe(reasonText({ code: "account.login-required", params: {} }));
  });

  it("lists each window with its use, its reset and whether it is at or near its limit", () => {
    const view = harnessDetailsView(row({ quota: quota({ provider: "deepseek-official", windows: [
      { name: "5h", usedPercent: 100, resetsAt: "2026-09-29T13:00:00.000Z" },
      { name: "weekly", usedPercent: 93.5, resetsAt: null },
      { name: "monthly", usedPercent: 41, resetsAt: null },
      { name: "daily", usedPercent: null, resetsAt: null },
    ] }) }), t).quota!;
    expect(view.source).toBe("dsh-native · deepseek-official");
    expect(view.windows).toEqual([
      { name: "5h", text: "100%", hot: true, resets: `重置 ${clock("2026-09-29T13:00:00.000Z")}`, flag: "atLimit" },
      { name: "weekly", text: "93.5%", hot: true, resets: "重置 未记录", flag: "nearLimit" },
      { name: "monthly", text: "41%", hot: false, resets: "重置 未记录", flag: null },
      { name: "daily", text: "未知", hot: false, resets: "重置 未记录", flag: null },
    ]);
    expect(view.limitReported).toBe(false);
    expect(view.note).toBe("最近额度观测 · 非实时");
  });

  it("says a stale observation is stale and has no window flagged near its limit", () => {
    const view = harnessDetailsView(row({ quota: quota({ stale: true, windows: [{ name: "5h", usedPercent: 99, resetsAt: null }] }) }), t).quota!;
    expect(view).toMatchObject({ stale: true, note: "额度观测已过期" });
    expect(view.windows[0]).toMatchObject({ hot: false, flag: null });
  });

  it("words the note of an observation from the page's own text, as the layer below words it", () => {
    for (const recorded of [quota(), quota({ stale: true }), quota({ reachedType: "usage_limit_reached" })])
      expect(harnessDetailsView(row({ quota: recorded }), t).quota!.note).toBe(quotaView(recorded)!.note);
  });
});

describe("an exhausted quota", () => {
  const retry = (extra: Partial<NonNullable<QuotaRoutingRecord["retry"]>> = {}) =>
    ({ eligibleAt: "2026-09-29T10:30:00.000Z", open: false, pendingManual: false, manualAt: null, consumedAt: null, consumedBy: null, ...extra });
  function record(extra: Partial<QuotaRoutingRecord> = {}): QuotaRoutingRecord {
    return { provider: "deepseek-official", limitId: null, code: "HARNESS_BALANCE_ZERO", source: "dsh-native",
      observedAt: "2026-09-29T09:00:00.000Z", resetsAt: null, blocked: true, retry: retry(), ...extra };
  }

  it("says where each of the six recovery states stands, from the recorded facts alone", () => {
    const at = (iso: string) => clock(iso);
    expect(recoveryText(record({ resetsAt: "2026-09-29T13:00:00.000Z", retry: null }), t)).toBe(`重置 ${at("2026-09-29T13:00:00.000Z")}`);
    expect(recoveryText(record({ retry: null }), t)).toBe("未知");
    expect(recoveryText(record({ retry: retry({ open: true }) }), t)).toBe("可重试");
    expect(recoveryText(record({ retry: retry({ open: true, consumedAt: "2026-09-29T09:40:00.000Z" }) }), t))
      .toBe(`已重试 ${at("2026-09-29T09:40:00.000Z")}，可再试`);
    expect(recoveryText(record(), t)).toBe(`${at("2026-09-29T10:30:00.000Z")} 后可重试`);
    expect(recoveryText(record({ retry: retry({ consumedAt: "2026-09-29T09:40:00.000Z" }) }), t))
      .toBe(`已重试 ${at("2026-09-29T09:40:00.000Z")}，${at("2026-09-29T10:30:00.000Z")} 后可再试`);
  });

  it("lists the blocked records with their word, their observation and their recovery, and leaves the others out", () => {
    const view = harnessDetailsView(row({ quotaRouting: [
      record(),
      record({ provider: "other", limitId: "tokens", code: "HARNESS_QUOTA_EXHAUSTED" }),
      record({ provider: "settled", blocked: false, code: null }),
    ] }), t);
    expect(view.exhausted.map(entry => [entry.provider, entry.limitId, entry.word])).toEqual([
      ["deepseek-official", null, "余额为零"], ["other", "tokens", "额度耗尽"]]);
    expect(view.exhausted[0].observation).toBe(`观测 ${clock("2026-09-29T09:00:00.000Z")} · dsh-native`);
  });

  it("offers a retry only for a blocked provider with no reset, once each", () => {
    expect(retryProviders([record(), record({ limitId: "b" }), record({ provider: "known", resetsAt: "2026-09-29T13:00:00.000Z", retry: null }),
      record({ provider: "free", blocked: false, code: null })])).toEqual(["deepseek-official"]);
    expect(retryProviders([])).toEqual([]);
  });
});
