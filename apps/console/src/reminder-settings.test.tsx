import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { ReminderSettings } from "./ReminderSettings";
import "./i18n";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves a valid silence threshold and keeps read-only sessions unable to change it", async () => {
  const command = vi.fn(async () => ({ silenceSeconds: 1200, revision: 2, updatedAt: "2026-10-15T01:00:00Z" }));
  const refresh = vi.fn(async () => {});
  const api = { command } as unknown as ConsoleApi;
  const initial = { silenceSeconds: 900, revision: 1, updatedAt: "2026-10-15T00:00:00Z" };
  const user = userEvent.setup();
  const view = render(<ReminderSettings api={api} csrfToken="csrf" settings={initial} refresh={refresh} unavailable={false} />);
  const input = screen.getByRole("spinbutton", { name: "静默提醒阈值（分钟）" });
  await user.clear(input);
  await user.type(input, "0");
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  await user.clear(input);
  await user.type(input, "20");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: 1200 }, "csrf"));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect((input as HTMLInputElement).value).toBe("20");
  view.rerender(<ReminderSettings api={api} csrfToken="csrf" settings={initial} refresh={refresh} unavailable />);
  expect(input.hasAttribute("disabled")).toBe(true);
});

it("shows a stored second-level threshold without rounding it into an editable minute", async () => {
  const command = vi.fn(async () => ({ silenceSeconds: 120, revision: 2, updatedAt: "2026-10-15T01:00:00Z" }));
  const initial = { silenceSeconds: 1, revision: 1, updatedAt: "2026-10-15T00:00:00Z" };
  const user = userEvent.setup();
  render(<ReminderSettings api={{ command } as unknown as ConsoleApi} csrfToken="csrf" settings={initial}
    refresh={async () => {}} unavailable={false} />);
  const input = screen.getByRole("spinbutton", { name: "静默提醒阈值（分钟）" }) as HTMLInputElement;
  expect(input.value).toBe("");
  expect(input.min).toBe("1");
  expect(input.max).toBe("1440");
  expect(input.step).toBe("1");
  expect(screen.getByText("当前保存值为 1 秒；请输入整数分钟才能修改。")).toBeTruthy();
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  await user.clear(input);
  await user.type(input, "1.5");
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  expect(command).not.toHaveBeenCalled();
  await user.clear(input);
  await user.type(input, "2");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: 120 }, "csrf"));
  expect(input.value).toBe("2");
});
