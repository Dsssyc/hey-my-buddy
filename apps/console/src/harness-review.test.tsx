import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HarnessReview } from "./HarnessReview";
import { parseReadOnlyStructured } from "./api";
import type { HarnessHealth } from "./types";

afterEach(() => { cleanup(); sessionStorage.clear(); });
const eligibility = { eligible: true, systemSandbox: true, sameAttemptContinuation: true, reasonCode: null, reason: null };
const row: HarnessHealth = { adapter: "codex", version: "0.159.0", status: "ready", available: true, revision: 3, manualPath: null,
  readOnlyStructured: eligibility };

function expectNoVerificationControls() {
  expect(screen.queryByRole("button", { name: /验证/ })).toBeNull();
  expect(screen.queryByRole("combobox", { name: "审阅验证配置" })).toBeNull();
}

describe("local review eligibility", () => {
  it("shows local eligibility without a model invocation or certificate requirement", () => {
    render(<HarnessReview row={row} />);
    expect(screen.getByText("审阅资格：符合本地检查")).toBeTruthy();
    expect(screen.getByText("本地资格检查不调用模型，不代表原生调用已验证。")).toBeTruthy();
    expect(screen.getByText("具有原生系统沙盒；实际策略由每次运行核对。")).toBeTruthy();
    expectNoVerificationControls();
  });

  it("does not retry a retired request after a lost reply", () => {
    sessionStorage.setItem("buddy-review-intent:codex", JSON.stringify({ requestId: "lost-reply", profileId: "codex-profile", expectedRevision: 3 }));
    render(<HarnessReview row={row} />);
    expectNoVerificationControls();
    expect(screen.queryByText("核对本次验证")).toBeNull();
  });

  it("does not restore retired paid verification on folding or reloading", () => {
    sessionStorage.setItem("buddy-review-intent:codex", JSON.stringify({ requestId: "old-request", profileId: "codex-profile", expectedRevision: 3 }));
    const first = render(<HarnessReview row={row} />);
    expectNoVerificationControls();
    first.unmount();
    render(<HarnessReview row={row} />);
    expectNoVerificationControls();
    expect(screen.getByText("审阅资格：符合本地检查")).toBeTruthy();
  });

  it("shows the recorded local failure reason with no paid remedy", () => {
    render(<HarnessReview row={{ ...row, readOnlyStructured: { ...eligibility, eligible: false,
      reasonCode: "readonly-resource-missing", reason: "The native read-only controller is missing" } }} />);
    expect(screen.getByText("审阅资格：不可用")).toBeTruthy();
    expect(screen.getByRole("status").textContent).toContain("The native read-only controller is missing");
    expectNoVerificationControls();
  });

  it("has no mutation entry even when rendered inside a writable or unavailable page", () => {
    const command = vi.fn();
    // Old props cannot recreate the removed action on stale caller data.
    render(<HarnessReview {...{ row, api: { command }, canWrite: false }} />);
    expectNoVerificationControls();
    expect(command).not.toHaveBeenCalled();
  });

  it("refuses incomplete eligibility and retired certificates instead of inventing availability", () => {
    expect(parseReadOnlyStructured({ eligible: true })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, eligible: "true" })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, systemSandbox: "true" })).toBeNull();
    expect(parseReadOnlyStructured({ ...eligibility, reason: "failed local check" })).toBeNull();
    expect(parseReadOnlyStructured({ verified: true, status: "verified", version: "0.157.0" })).toBeNull();
    render(<HarnessReview row={{ ...row, readOnlyStructured: undefined }} />);
    expect(screen.getByText("审阅资格：未知")).toBeTruthy();
    expect(screen.queryByText(/符合本地检查/)).toBeNull();
  });

  it.each(["dsh", "zcode"])("states %s's remaining boundary without a system sandbox", adapter => {
    render(<HarnessReview row={{ ...row, adapter, readOnlyStructured: { ...eligibility, systemSandbox: false } }} />);
    expect(screen.getByText("无系统沙盒，不能保证阻止副本外读取或外传；黑板的事后判定只覆盖上报的工具事件。")).toBeTruthy();
    expectNoVerificationControls();
  });
});
