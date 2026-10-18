import { act, cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api";
import { formatCoolingTime } from "../i18n/format";
import type { ProviderCooling } from "../types";
import { CoolingStatus, activeCooling } from "./cooling";
import { fixture, harness, openHarness } from "./harness.fixtures";
import { harnessButton, paneBlock } from "./harness.testing";

const deadline = new Date(Date.now() + 3600000).toISOString();
const record = (extra: Partial<ProviderCooling> = {}): ProviderCooling => ({ provider: "zai", coolingUntil: deadline, consecutiveRejections: 2, ...extra });
const rows = () => [harness({ harness: "dsh", status: "ready", providers: [record(), record({ provider: "deepseek", coolingUntil: null }), record({ provider: "expired", coolingUntil: "2020-01-01T00:00:00Z" })] })];
const clear = () => within(paneBlock("冷却", "DSH")).getByRole("button", { name: "解除冷却" });
const reply = (cleared: boolean, duplicate = false) => ({ harness: "dsh", provider: "zai", cleared, duplicate });

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); sessionStorage.clear(); localStorage.clear(); window.location.hash = ""; });

describe("provider cooldown", () => {
  it("shows only active providers with a neutral status and one clear action each", async () => {
    await openHarness(fixture({ harnesses: rows() }));
    const block = paneBlock("冷却", "DSH");
    expect(within(block).getByText("zai")).toBeTruthy();
    expect(within(block).getByText(`冷却中 · 约 ${formatCoolingTime(Date.parse(deadline))} 可再试`).className).toContain("text-ink3");
    expect(within(block).getAllByRole("button")).toHaveLength(1);
    expect(harnessButton("DSH").textContent).toContain("冷却中");
    expect(activeCooling(rows()[0].providers!).map(record => record.provider)).toEqual(["zai"]);
  });

  it.each([true, false])("reports cleared=%s from the flat reply and refreshes", async cleared => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: rows(), replies: { quota_redetect: () => reply(cleared) } });
    await openHarness(f);
    await user.click(clear());
    expect(await screen.findByText(cleared ? "已解除冷却" : "当前未在冷却")).toBeTruthy();
    expect(f.command).toHaveBeenCalledWith("quota_redetect", expect.objectContaining({ harness: "dsh", provider: "zai", requestId: expect.any(String) }), "csrf");
    expect(f.snapshotMock.mock.calls.length).toBeGreaterThan(1);
    expect(sessionStorage.length).toBe(0);
  });

  it("replays the same request after a lost or invalid reply, including duplicate receipt", async () => {
    const user = userEvent.setup();
    const replies = [() => { throw new ApiError("NETWORK_ERROR", "回复未知"); }, () => ({}), () => reply(true, true)];
    const f = fixture({ harnesses: rows(), replies: { quota_redetect: () => replies.shift()!() } });
    await openHarness(f);
    await user.click(clear());
    await screen.findByRole("alert");
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("解除冷却的结果未知；请重放此请求。");
    await user.click(clear());
    await screen.findByText("已解除冷却");
    const calls = f.calls.filter(call => call.operation === "quota_redetect");
    expect(calls).toHaveLength(3);
    expect(new Set(calls.map(call => call.params.requestId)).size).toBe(1);
    expect(sessionStorage.length).toBe(0);
  });

  it("retains request identity across an unmount until a known reply", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: rows(), replies: { quota_redetect: () => { throw new ApiError("NETWORK_ERROR", "回复未知"); } } });
    await openHarness(f); await user.click(clear()); await screen.findByRole("alert");
    const id = f.calls[0].params.requestId;
    cleanup();
    const next = fixture({ harnesses: rows(), replies: { quota_redetect: () => reply(false, true) } });
    await openHarness(next); await user.click(clear()); await screen.findByText("当前未在冷却");
    expect(next.calls[0].params.requestId).toBe(id);
  });

  it("honors write permission and does not send from a read-only session", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: rows(), readOnly: true });
    await openHarness(f);
    expect(clear().getAttribute("aria-disabled")).toBe("true");
    await user.click(clear());
    expect(f.command).not.toHaveBeenCalled();
  });

  it("does not start a second action while clearing", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    const f = fixture({ harnesses: rows(), replies: { quota_redetect: () => new Promise(resolve => { finish = resolve; }) } });
    await openHarness(f); await user.click(clear()); await user.click(clear());
    expect(f.calls).toHaveLength(1);
    await act(async () => finish(reply(true)));
    await screen.findByText("已解除冷却");
  });

  it("expires without a snapshot refresh and reappears when a new deadline arrives in the same slot", () => {
    vi.useFakeTimers(); vi.setSystemTime(new Date("2026-10-19T10:00:00Z"));
    const { container, rerender } = render(<CoolingStatus providers={[record({ coolingUntil: "2026-10-19T10:00:01Z" })]} />);
    const slot = container.firstElementChild;
    expect(slot?.textContent).toContain("冷却中");
    act(() => vi.advanceTimersByTime(1000));
    expect(slot?.textContent).toBe("");
    rerender(<CoolingStatus providers={[record({ coolingUntil: "2026-10-19T10:01:00Z" })]} />);
    expect(container.firstElementChild).toBe(slot);
    expect(slot?.textContent).toContain("冷却中");
  });
});
