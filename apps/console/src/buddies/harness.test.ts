import i18n from "i18next";
import { describe, expect, it } from "vitest";
import type { HarnessAccount } from "../harness-account";
import { quotaView } from "../host-workflow";
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
  return { adapter: "dsh", status: "ready", available: true, revision: 1, manualPath: null, ...entry };
}
function quota(entry: Partial<HarnessQuota> = {}): HarnessQuota {
  return { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", windows: [], ...entry };
}
const account: HarnessAccount = { adapter: "dsh", source: "native", revision: 1, credentialRevision: 0, status: "ready",
  accountType: null, checkedAt: null, reasonCode: null, guidance: null,
  capabilities: { workerAccount: false, oauth: false, apiKey: false, logout: false, remove: false } };

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
  it("writes each recorded harness state and reads its account source and state in details", () => {
    expect(statusText("ready", t)).toBe("已找到");
    expect(statusText("login-required", t)).toBe("未登录");
    expect(statusText("paused", t)).toBe("paused");
    expect(harnessDetailsView(row(), t).account).toBeNull();
    expect(harnessDetailsView(row({ account }), t).account).toMatchObject({ source: "共用登录", state: "已配置" });
    expect(harnessDetailsView(row({ account: { ...account, source: "worker", status: "logged-out" } }), t).account)
      .toMatchObject({ source: "独立账户", state: "未登录" });
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
      readOnlyStructured: { eligible: true, systemSandbox: true, reason: null, reasonCode: null, sameAttemptContinuation: true } }), t);
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
    const eligibility = { eligible: false, systemSandbox: false, reasonCode: "readonly-resource-missing", reason: "The native read-only controller is missing", sameAttemptContinuation: true };
    const view = harnessDetailsView(row({ readOnlyStructured: eligibility }), t);
    expect(view.location.review).toMatchObject({ text: "不可用", reason: "The native read-only controller is missing" });
    expect(view.location.review.note).toContain("本地资格检查不调用模型，不代表原生调用已验证。");
    expect(view.location.review.note).toContain("无系统沙盒，不能保证阻止副本外读取或外传；黑板的事后判定只覆盖上报的工具事件。");
    // Without a recorded eligibility nothing is said about the sandbox, and nothing reads as available.
    const unknown = harnessDetailsView(row(), t).location.review;
    expect(unknown).toEqual({ text: "未知", reason: null, note: "本地资格检查不调用模型，不代表原生调用已验证。" });
  });

  it("adds the reason, the remedy and the places tried only for a harness that is not found", () => {
    const view = harnessDetailsView(row({ status: "missing", available: false, reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI。",
      candidates: [
        { path: "/a/zcode", source: "应用包", status: "missing", reasonCode: "not_found" },
        { path: "/b/zcode", source: "Homebrew", status: "failed", reasonCode: "handshake_failed" },
        { status: "paused", reasonCode: "odd_code" },
      ] }), t);
    expect(view.location.exception).toEqual({ reason: "未找到可执行文件", remedy: "安装 ZCode CLI。", tried: [
      { path: "/a/zcode", source: "应用包", result: "未找到" },
      { path: "/b/zcode", source: "Homebrew", result: "握手失败" },
      { path: null, source: null, result: "paused · odd_code" },
    ] });
  });

  it("offers a remedy of its own when none was recorded, and leaves an unknown reason as recorded", () => {
    const exception = (status: HarnessHealth["status"], extra: Partial<HarnessHealth> = {}) =>
      harnessDetailsView(row({ status, available: false, ...extra }), t).location.exception;
    expect(exception("unknown")?.remedy).toBe("点“检测”查看状态。");
    expect(exception("missing")?.remedy).toBe("安装 CLI，或指定路径。");
    expect(exception("login-required")?.remedy).toBe("在 Harness 中登录后检测。");
    expect(exception("unhealthy", { reasonCode: "SOMETHING_NEW" })).toMatchObject({ reason: "SOMETHING_NEW", remedy: "检查 CLI，或更换路径。" });
    expect(exception("unhealthy", { remedy: "  " })?.remedy).toBe("检查 CLI，或更换路径。");
    expect(exception("unhealthy")?.reason).toBeNull();
  });

  it("writes the account's source, status, type and check, the billing of each provider and the guidance", () => {
    expect(harnessDetailsView(row(), t).account).toBeNull();
    const view = harnessDetailsView(row({ account: { ...account, source: "worker", status: "ready", accountType: "chatgpt", checkedAt: "2026-09-29T09:00:00.000Z" },
      billingByProvider: { openai: { kind: "subscription", source: "native", observedAt: "x" }, deepseek: { kind: "metered", source: "native", observedAt: "x" },
        other: { kind: "unknown", source: "native", observedAt: "x" } } }), t).account!;
    expect(view).toMatchObject({ source: "独立账户", state: `已配置 · ChatGPT · ${clock("2026-09-29T09:00:00.000Z")}`,
      billing: [{ provider: "openai", plan: "订阅" }, { provider: "deepseek", plan: "按量" }, { provider: "other", plan: "未知" }], guidance: null });
    expect(harnessDetailsView(row({ account: { ...account, accountType: "mystery" } }), t).account?.state).toBe("已配置 · 未知");
    expect(harnessDetailsView(row({ account }), t).account?.billing).toEqual([]);
    const guided = (reasonCode: string | null, guidance: string | null) =>
      harnessDetailsView(row({ account: { ...account, status: "logged-out", reasonCode, guidance } }), t).account!.guidance;
    expect(guided("ACCOUNT_LOGIN_REQUIRED", "recorded")).toBe("完成当前来源的登录后检测。");
    expect(guided("ACCOUNT_NEW", "recorded as is")).toBe("recorded as is");
    expect(guided("ACCOUNT_LOGIN_REQUIRED", null)).toBeNull();
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
      record({ provider: "settled", blocked: false }),
    ] }), t);
    expect(view.exhausted.map(entry => [entry.provider, entry.limitId, entry.word])).toEqual([
      ["deepseek-official", null, "余额为零"], ["other", "tokens", "额度耗尽"]]);
    expect(view.exhausted[0].observation).toBe(`观测 ${clock("2026-09-29T09:00:00.000Z")} · dsh-native`);
  });

  it("offers a retry only for a blocked provider with no reset, once each", () => {
    expect(retryProviders([record(), record({ limitId: "b" }), record({ provider: "known", resetsAt: "2026-09-29T13:00:00.000Z", retry: null }),
      record({ provider: "free", blocked: false })])).toEqual(["deepseek-official"]);
    expect(retryProviders([])).toEqual([]);
  });
});
