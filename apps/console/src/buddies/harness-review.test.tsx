import { cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { parseReadOnlyStructured } from "../api";
import type { HarnessHealth } from "../api";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { fieldValue, paneBlock } from "./harness.testing";

/**
 * Local review eligibility on the harness's side column: a free check of the
 * shipped mechanism, never native-run evidence. It calls no model and has no
 * certificate, no verification entry and no request that could be lost and
 * retried; the words that say so are on the word "审阅" itself.
 */

afterEach(() => { cleanup(); sessionStorage.clear(); window.location.hash = ""; document.documentElement.dataset.theme = ""; });

const eligibility = { eligible: true, systemSandbox: true, sameAttemptContinuation: true, reasonCode: null, reason: null };
const note = "本地资格检查不调用模型，不代表原生调用已验证。";
const sandbox = "具有原生系统沙盒；实际策略由每次运行核对。";
const noSandbox = "无系统沙盒，不能保证阻止副本外读取或外传；黑板的事后判定只覆盖上报的工具事件。";

function codex(extra: Partial<HarnessHealth> = {}): HarnessHealth {
  return harness({ adapter: "codex", version: "0.159.0", status: "ready", revision: 3, readOnlyStructured: eligibility, ...extra });
}
const review = () => fieldValue(paneBlock("位置"), "审阅");
const word = () => within(review()).getByText(/^(可用|不可用|未知)$/);

function expectNoVerificationControls() {
  expect(screen.queryByRole("button", { name: /验证/ })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "审阅验证配置" })).toBeNull();
  expect(screen.queryByText("核对本次验证")).toBeNull();
  expect(screen.queryByText("新版本待验证")).toBeNull();
}

describe("local review eligibility", () => {
  it("shows local eligibility without a model invocation or certificate requirement", async () => {
    const f = fixture({ harnesses: [codex()] });
    await openHarness(f);
    expect(word().textContent).toBe("可用");
    expect(word().getAttribute("aria-description")).toBe(`${note} ${sandbox}`);
    expectNoVerificationControls();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("shows no retired verification entry even with stale certificate data", async () => {
    const stale = codex();
    Object.assign(stale, { reviewVerification: { status: "new-version", verified: false } });
    const f = fixture({ harnesses: [stale] });
    await openHarness(f);
    expect(word().textContent).toBe("可用");
    expectNoVerificationControls();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("does not retry a retired request after a lost reply", async () => {
    sessionStorage.setItem("buddy-review-intent:codex", JSON.stringify({ requestId: "lost-reply", profileId: "codex-profile", expectedRevision: 3 }));
    const f = fixture({ harnesses: [codex()] });
    await openHarness(f);
    expectNoVerificationControls();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("does not restore a retired paid verification on reloading", async () => {
    sessionStorage.setItem("buddy-review-intent:codex", JSON.stringify({ requestId: "old-request", profileId: "codex-profile", expectedRevision: 3 }));
    await openHarness(fixture({ harnesses: [codex()] }));
    expectNoVerificationControls();
    cleanup();
    await openHarness(fixture({ harnesses: [codex()] }));
    expectNoVerificationControls();
    expect(word().textContent).toBe("可用");
  });

  it("shows the recorded local failure reason with no paid remedy", async () => {
    await openHarness(fixture({ harnesses: [codex({ readOnlyStructured: { ...eligibility, eligible: false,
      reasonCode: "readonly-resource-missing", reason: "The native read-only controller is missing" } })] }));
    expect(word().textContent).toBe("不可用");
    expect(within(review()).getByRole("status").textContent).toBe("The native read-only controller is missing");
    expect(word().getAttribute("aria-description")).toContain(note);
    expectNoVerificationControls();
  });

  it("has no mutation entry even in a session that cannot write", async () => {
    const f = fixture({ harnesses: [codex()], readOnly: true });
    await openHarness(f);
    expectNoVerificationControls();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("refuses incomplete eligibility and retired certificates instead of inventing availability", async () => {
    expect(parseReadOnlyStructured({ eligible: true })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, eligible: "true" })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, systemSandbox: "true" })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, reason: "failed local check" })).toBeNull();
    expect(parseReadOnlyStructured({ verified: true, status: "verified", version: "0.157.0" })).toBeNull();
    await openHarness(fixture({ harnesses: [codex({ readOnlyStructured: undefined })] }));
    expect(word().textContent).toBe("未知");
    expect(screen.queryByText("可用")).toBeNull();
    // Without a recorded eligibility only the first sentence stands: nothing is said about a sandbox.
    expect(word().getAttribute("aria-description")).toBe(note);
  });

  it.each(["dsh", "zcode"])("states %s's remaining boundary without a system sandbox", async adapter => {
    await openHarness(fixture({ harnesses: [codex({ adapter, readOnlyStructured: { ...eligibility, systemSandbox: false } })] }));
    expect(word().getAttribute("aria-description")).toBe(`${note} ${noSandbox}`);
    expectNoVerificationControls();
  });
});
