import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { ConsoleApi } from "./api";
import { ReminderSettings } from "./ReminderSettings";
import { TaskDetails } from "./TaskDetails";
import type { Snapshot, Task } from "./types";
import "./i18n";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves a valid silence threshold and keeps read-only sessions unable to change it", async () => {
  const command = vi.fn(async () => ({ silenceSeconds: 1200, revision: 2, updatedAt: "2026-10-15T01:00:00Z" }));
  const refresh = vi.fn(async () => {});
  const api = { command } as unknown as ConsoleApi;
  const initial = { silenceSeconds: 900, revision: 1, updatedAt: "2026-10-15T00:00:00Z" };
  const user = userEvent.setup();
  const view = render(<ReminderSettings api={api} csrfToken="csrf" settings={initial} refresh={refresh} unavailable={false} />);
  const input = screen.getByRole("spinbutton", { name: "静默提醒阈值（秒）" });
  await user.clear(input);
  await user.type(input, "0");
  expect(input.getAttribute("aria-invalid")).toBe("true");
  expect(screen.getByRole("button", { name: "保存" }).hasAttribute("disabled")).toBe(true);
  await user.clear(input);
  await user.type(input, "1200");
  await user.click(screen.getByRole("button", { name: "保存" }));
  await waitFor(() => expect(command).toHaveBeenCalledWith("reminder_settings_set", { silenceSeconds: 1200 }, "csrf"));
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect((input as HTMLInputElement).value).toBe("1200");
  view.rerender(<ReminderSettings api={api} csrfToken="csrf" settings={initial} refresh={refresh} unavailable />);
  expect(input.hasAttribute("disabled")).toBe(true);
});

it("shows reminders only for the active selected attempt beside the task status", () => {
  const attempt = { attemptId: "attempt-a", reminders: { overdue: true, possiblyStuck: true,
    lastNativeEventAt: "2026-10-15T00:00:00Z", toolInProgress: false } };
  const task = { runId: "run-a", task: "检查任务", status: "running", owner: "worker", cwd: "/fixture", revision: 1,
    createdAt: "2026-10-15T00:00:00Z", acceptedAt: null, acceptanceVerdict: null, reviewRejectedAt: null,
    reviewRejectionNote: null, activeAttemptId: "attempt-a", selectedAttemptId: "attempt-a", selectedAttempt: attempt } as Task;
  const api = { task: vi.fn(async () => task) } as unknown as ConsoleApi;
  const props = { snapshot: { csrfToken: "csrf" } as Snapshot, api, refresh: vi.fn(async () => null),
    selectTask: vi.fn(), active: false, onTaskUpdate: vi.fn() };
  const view = render(<TaskDetails task={task} {...props} />);
  expect(screen.getByText("已超过预期")).toBeTruthy();
  expect(screen.getByText("可能卡住")).toBeTruthy();
  view.rerender(<TaskDetails task={{ ...task, activeAttemptId: "attempt-b" }} {...props} />);
  expect(screen.queryByText("已超过预期")).toBeNull();
  expect(screen.queryByText("可能卡住")).toBeNull();
});
