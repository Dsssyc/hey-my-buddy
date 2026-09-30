import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, parseConsoleAccess } from "./api";
import type { ConsoleApi } from "./api";
import type { ConsoleAccess } from "./types";
import { ConsoleAccessSettings } from "./ConsoleAccessSettings";

afterEach(cleanup);
const local: ConsoleAccess = { requireLogin: false, revision: 0, sessions: [] };
const current = { id: "a".repeat(24), current: true, lastSeen: 1_700_000_000 };
const other = { id: "b".repeat(24), current: false, lastSeen: 1_700_000_000 };
const login: ConsoleAccess = { requireLogin: true, revision: 1, sessions: [current, other] };

function setup(access: ConsoleAccess = local, unavailable = false) {
  const command = vi.fn().mockResolvedValue(login), refresh = vi.fn().mockResolvedValue(null);
  render(<ConsoleAccessSettings api={{ command } as unknown as ConsoleApi} csrfToken="fixture-csrf"
    access={access} refresh={refresh} unavailable={unavailable} />);
  return { command, refresh, user: userEvent.setup() };
}

it("enables login using the shown revision and refreshes the rotated authority", async () => {
  const { command, refresh, user } = setup();
  expect(screen.getByRole("switch", { name: "需要登录" })).toHaveProperty("checked", false);
  await user.click(screen.getByRole("switch", { name: "需要登录" }));
  expect(command).toHaveBeenCalledWith("console_access_set", { requireLogin: true, expectedRevision: 0 }, "fixture-csrf");
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("switch", { name: "需要登录" })).toHaveProperty("checked", true);
  expect(screen.getByRole("button", { name: "退出当前登录" })).toBeTruthy();
});

it("refuses a stale switch response, reloads and shows the conflict", async () => {
  const { command, refresh, user } = setup();
  command.mockRejectedValue(new ApiError("REVISION_CONFLICT", "conflict"));
  await user.click(screen.getByRole("switch"));
  expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("记录已更新"));
  expect(refresh).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("switch")).toHaveProperty("checked", false);
});

it("revokes the chosen other login without logging out the current one", async () => {
  const { command, user } = setup(login);
  command.mockResolvedValue({ ...login, sessions: [current] });
  await user.click(screen.getByRole("button", { name: "撤销登录 bbbbbbbb" }));
  expect(command).toHaveBeenCalledWith("console_session_revoke", { sessionId: other.id }, "fixture-csrf");
  expect(screen.queryByRole("button", { name: "撤销登录 bbbbbbbb" })).toBeNull();
  expect(screen.getByRole("button", { name: "退出当前登录" })).toHaveProperty("disabled", false);
});

it("logout blocks subsequent writes and gives the login entry instruction", async () => {
  const { command, user } = setup(login);
  command.mockResolvedValue({ ...login, sessions: [other] });
  await user.click(screen.getByRole("button", { name: "退出当前登录" }));
  expect(command).toHaveBeenCalledWith("console_logout", {}, "fixture-csrf");
  expect(screen.getByRole("status").textContent).toContain("buddy console");
  expect(screen.getByRole("switch")).toHaveProperty("disabled", true);
  await user.click(screen.getByRole("button", { name: "撤销登录 bbbbbbbb" }));
  expect(command).toHaveBeenCalledTimes(1);
});

it("does not change access while disconnected", async () => {
  const { user, command } = setup(local, true);
  await user.click(screen.getByRole("switch"));
  expect(command).not.toHaveBeenCalled();
});

it("rejects malformed access responses rather than showing a successful switch", () => {
  for (const value of [null, {}, { ...local, revision: true }, { ...local, requireLogin: "false" },
    { ...login, sessions: [{ ...current, id: "not-a-session" }] }, { ...login, sessions: [{ ...current, lastSeen: null }] }]) {
    expect(() => parseConsoleAccess(value)).toThrow(ApiError);
  }
});
