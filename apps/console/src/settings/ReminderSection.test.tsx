import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ConsoleApi } from "../api";
import { TooltipProvider } from "../components/ui/tooltip";
import { ReminderSection } from "./ReminderSection";
import "../i18n";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const initial = { silenceSeconds: 900, revision: 1, updatedAt: "2026-10-15T00:00:00Z" };
function setup(settings = initial) {
  const command = vi.fn().mockResolvedValue({ ...initial, silenceSeconds: 1200, revision: 2 });
  const refresh = vi.fn(async () => {});
  const props = { api: { command } as unknown as ConsoleApi, csrfToken: "csrf", settings, refresh, unavailable: false };
  const view = render(<TooltipProvider><ReminderSection {...props} /></TooltipProvider>);
  const user = userEvent.setup();
  const input = screen.getByRole("spinbutton", { name: "静默提醒阈值（分钟）" }) as HTMLInputElement;
  return { command, refresh, user, input, save: () => screen.getByRole("button", { name: "保存" }), rerender: (next: Partial<typeof props>) => view.rerender(<TooltipProvider><ReminderSection {...props} {...next} /></TooltipProvider>) };
}

it("saves valid minutes and refreshes the snapshot", async () => {
  const { input, command, refresh, user, save } = setup();
  const description = screen.getByText("一次执行这么久没有任何进展，时间轴与微任务里标为可能卡住。");
  expect(description.tagName).toBe("P");
  expect(input.getAttribute("aria-describedby")).toBe(description.id);
  await user.clear(input); await user.type(input, "20");
  expect(screen.getByRole("img", { name: "未保存" })).toBeTruthy();
  await user.click(save());
  await waitFor(() => expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: 1200 }, "csrf"));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(input.value).toBe("20");
  expect(screen.queryByRole("img", { name: "未保存" })).toBeNull();
});

it.each(["0", "1441", "1.5", ""])("rejects invalid minute value %s without disabling focus", async value => {
  const { input, command, user, save } = setup();
  await user.clear(input); if (value) await user.type(input, value);
  expect(input.getAttribute("aria-invalid")).toBe(value ? "true" : null);
  expect(save().getAttribute("aria-disabled")).toBe("true");
  expect(save().hasAttribute("disabled")).toBe(false);
  expect(save().getAttribute("aria-description")).toContain("1 至 1440");
  await user.click(save()); expect(command).not.toHaveBeenCalled();
});

it.each(["1", "1440"])("accepts the inclusive boundary %s", async value => {
  const { input, command, user, save } = setup();
  command.mockResolvedValue({ ...initial, revision: 2, silenceSeconds: Number(value) * 60 });
  await user.clear(input); await user.type(input, value); await user.click(save());
  expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: Number(value) * 60 }, "csrf");
});

it("shows nonintegral stored minutes without rounding and requires whole minutes", async () => {
  const { input, command, user, save } = setup({ ...initial, silenceSeconds: 1 });
  expect(input.value).toBe("");
  expect([input.min, input.max, input.step]).toEqual(["1", "1440", "1"]);
  expect(screen.getByText("当前保存值为 1 秒；请输入整数分钟才能修改。")).toBeTruthy();
  await user.click(save()); expect(command).not.toHaveBeenCalled();
  command.mockResolvedValue({ ...initial, silenceSeconds: 120, revision: 2 });
  await user.type(input, "2"); await user.click(save());
  expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: 120 }, "csrf");
  await waitFor(() => expect(input.value).toBe("2"));
});

it("guards save and input when disconnected after editing", async () => {
  const { input, command, user, save, rerender } = setup();
  await user.clear(input); await user.type(input, "20");
  rerender({ unavailable: true });
  expect(input.hasAttribute("disabled")).toBe(false);
  expect(input.readOnly).toBe(true);
  await user.click(save()); expect(command).not.toHaveBeenCalled();
});

it("preserves drafts and recorded values on failed and malformed responses", async () => {
  const { input, command, refresh, user, save } = setup();
  await user.clear(input); await user.type(input, "20");
  command.mockRejectedValueOnce(new Error("save failed"));
  await user.click(save()); expect((await screen.findByRole("alert")).textContent).toContain("save failed");
  expect(input.value).toBe("20");
  command.mockResolvedValue({}); await user.click(save());
  await waitFor(() => expect(screen.getByRole("alert").textContent).not.toContain("save failed"));
  expect(refresh).not.toHaveBeenCalled(); expect(screen.queryByRole("status")).toBeNull();
});

it("ignores older snapshots and an older save reply while taking newer settings", async () => {
  const { input, command, user, save, rerender } = setup();
  await user.clear(input); await user.type(input, "20");
  await user.click(save()); await screen.findByText("提醒设置已保存。");
  rerender({ settings: initial }); expect(input.value).toBe("20");
  let resolve!: (value: typeof initial) => void;
  command.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await user.clear(input); await user.type(input, "30"); await user.click(save());
  rerender({ settings: { ...initial, revision: 4, silenceSeconds: 2400 } });
  resolve({ ...initial, revision: 3, silenceSeconds: 1800 });
  await waitFor(() => expect(save().textContent).toBe("保存"));
  expect(input.value).toBe("40");
});
