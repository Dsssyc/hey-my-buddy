import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { HarnessReview, parseReviewVerification } from "./HarnessReview";
import type { ConsoleApi } from "./api";
import type { HarnessHealth, Snapshot } from "./types";

afterEach(() => { cleanup(); sessionStorage.clear(); });
const row: HarnessHealth = { adapter: "codex", version: "0.159.0", status: "ready", available: true, revision: 3, manualPath: null,
  reviewVerification: { adapter: "codex", version: "0.159.0", platform: "darwin", status: "new-version", implemented: true, verified: false } };
const snapshot = { csrfToken: "fixture-csrf", configuration: { reviewRouterProfileId: "codex-profile" }, profiles: [{ profileId: "codex-profile",
  adapter: "codex", provider: "openai", model: "gpt-6-sol", effort: "high", enabled: true, available: true }] } as Snapshot;

describe("explicit review verification", () => {
  it("starts only when clicked and displays a pending version without inventing a certificate", async () => {
    const command = vi.fn().mockResolvedValue({ runId: "verification-1", status: "queued" });
    const refresh = vi.fn();
    render(<HarnessReview row={row} snapshot={snapshot} api={{ command } as unknown as ConsoleApi} canWrite onRefresh={refresh} />);
    expect(screen.getByText("审阅能力：新版本待验证")).toBeTruthy();
    expect(command).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "重新验证审阅能力" }));
    expect(command).toHaveBeenCalledWith("harness_verify", expect.objectContaining({ adapter: "codex", profileId: "codex-profile",
      expectedRevision: 3, execute: true }), "fixture-csrf");
    expect(refresh).toHaveBeenCalledOnce();
    expect(screen.getByText("审阅能力：等待验证")).toBeTruthy();
    expect((screen.getByRole("button", { name: "重新验证审阅能力" }) as HTMLButtonElement).disabled).toBe(true);
  });
  it("keeps the exact request identity after a lost reply", async () => {
    const command = vi.fn().mockRejectedValueOnce(new Error("回复丢失，请核对本次请求")).mockResolvedValueOnce({ runId: "verification-1", status: "queued" });
    render(<HarnessReview row={row} snapshot={snapshot} api={{ command } as unknown as ConsoleApi} canWrite onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "重新验证审阅能力" }));
    await userEvent.click(screen.getByRole("button", { name: "核对本次验证" }));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
  });
  it('preserves an unconfirmed identity across folding or reloading the details', async () => {
    const command = vi.fn().mockRejectedValueOnce(new Error('lost reply')).mockResolvedValueOnce({runId: 'verification-1', status: 'completed'});
    const first = render(<HarnessReview row={row} snapshot={snapshot} api={{command} as unknown as ConsoleApi} canWrite onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', {name: '重新验证审阅能力'}));
    first.unmount();
    render(<HarnessReview row={row} snapshot={snapshot} api={{command} as unknown as ConsoleApi} canWrite onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole('button', {name: '核对本次验证'}));
    expect(command.mock.calls[0]).toEqual(command.mock.calls[1]);
    expect(sessionStorage.getItem('buddy-review-intent:codex')).toBeNull();
  });
  it('shows the recorded binding failure with a relevant remedy', () => {
    const failed = {...row, reviewVerification: {...row.reviewVerification!, status: 'failed' as const,
      reasonCode: 'HARNESS_REVIEW_BINDING_CHANGED', failedChecks: []}};
    render(<HarnessReview row={failed} snapshot={snapshot} api={{command: vi.fn()} as unknown as ConsoleApi} canWrite onRefresh={vi.fn()} />);
    expect(screen.getByText('版本或路径已变化，请刷新后重新验证。')).toBeTruthy();
    expect(screen.queryByText(/检查 CLI 权限/)).toBeNull();
  });
  it("does not submit while writes are unavailable", async () => {
    const command = vi.fn();
    render(<HarnessReview row={row} snapshot={snapshot} api={{ command } as unknown as ConsoleApi} canWrite={false} onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "重新验证审阅能力" }));
    expect(command).not.toHaveBeenCalled();
  });
  it("refuses certificates with missing checks or a different version", () => {
    expect(parseReviewVerification({ ...row.reviewVerification, status: "verified", verified: true }, row)).toBeNull();
    expect(parseReviewVerification({ ...row.reviewVerification, version: "0.157.0" }, row)).toBeNull();
  });
});
