import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError, parseConsoleAccess } from "../api";
import type { ConsoleApi } from "../api";
import type { ConsoleAccess } from "../types";
import { TooltipProvider } from "../components/ui/tooltip";
import { AccessSection } from "./AccessSection";
import { setLanguageChoice } from "../i18n";

afterEach(cleanup);
const local: ConsoleAccess = { requireLogin: false, revision: 0, sessions: [] };
const current = { id: "a".repeat(24), current: true, lastSeen: 1_700_000_000 };
const other = { id: "b".repeat(24), current: false, lastSeen: 1_700_000_000 };
const login: ConsoleAccess = { requireLogin: true, revision: 1, sessions: [current, other] };
const gate = "只允许已授权的浏览器";
function setup(access: ConsoleAccess = local, unavailable = false) {
  const command = vi.fn().mockResolvedValue(login), refresh = vi.fn().mockResolvedValue(null);
  const api = { command } as unknown as ConsoleApi;
  const props = { api, csrfToken: "fixture-csrf", access, refresh, unavailable };
  const view = render(<TooltipProvider><AccessSection {...props} /></TooltipProvider>);
  return { command, refresh, user: userEvent.setup(), rerender: (next: Partial<typeof props>) => view.rerender(<TooltipProvider><AccessSection {...props} {...next} /></TooltipProvider>) };
}

it("enables authorization and refreshes rotated authority", async () => {
  const { command, refresh, user } = setup();
  expect(screen.getByRole("switch", { name: gate }).getAttribute("aria-checked")).toBe("false");
  await user.click(screen.getByRole("switch", { name: gate }));
  expect(command).toHaveBeenCalledWith("console_access_set", { requireLogin: true }, "fixture-csrf");
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1));
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
  expect(screen.getByRole("button", { name: "撤销当前浏览器" })).toBeTruthy();
});

it("disables authorization and reports that previous authorizations were revoked", async () => {
  const { command, user } = setup(login);
  command.mockResolvedValue({ ...local, revision: 2 });
  await user.click(screen.getByRole("switch"));
  expect(command).toHaveBeenCalledWith("console_access_set", { requireLogin: false }, "fixture-csrf");
  expect(await screen.findByRole("status")).toHaveProperty("textContent", expect.stringContaining("原有授权均已撤销"));
  expect(screen.queryByRole("list")).toBeNull();
});

it("reports failure and preserves the recorded setting", async () => {
  const { command, refresh, user } = setup();
  command.mockRejectedValue(new ApiError("NETWORK", "无法连接本地黑板"));
  await user.click(screen.getByRole("switch"));
  expect((await screen.findByRole("alert")).textContent).toContain("无法连接本地黑板");
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
});

it("revokes another browser without revoking the current one", async () => {
  const { command, user } = setup(login);
  command.mockResolvedValue({ ...login, sessions: [current] });
  await user.click(screen.getByRole("button", { name: "撤销浏览器 bbbbbbbb" }));
  expect(command).toHaveBeenCalledWith("console_session_revoke", { sessionId: other.id }, "fixture-csrf");
  expect(screen.queryByRole("button", { name: "撤销浏览器 bbbbbbbb" })).toBeNull();
  expect(screen.getByRole("button", { name: "撤销当前浏览器" }).getAttribute("aria-disabled")).toBeNull();
});

it("requires confirmation for the current browser, focuses cancel, and restores focus on cancel", async () => {
  const { command, user } = setup(login);
  const revoke = screen.getByRole("button", { name: "撤销当前浏览器" });
  await user.click(revoke);
  expect(screen.getByRole("dialog", { name: "撤销当前浏览器？" })).toBeTruthy();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "取消" }));
  expect(command).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(revoke);
  expect(command).not.toHaveBeenCalled();
});

it("revocation blocks subsequent writes and explains how to authorize again", async () => {
  const { command, user } = setup(login);
  command.mockResolvedValue({ ...login, sessions: [other] });
  await user.click(screen.getByRole("button", { name: "撤销当前浏览器" }));
  await user.click(screen.getByRole("button", { name: "确认撤销" }));
  expect(command).toHaveBeenCalledWith("console_logout", {}, "fixture-csrf");
  expect((await screen.findByRole("status")).textContent).toContain("hey-my-buddy console");
  expect(screen.getByRole("switch").getAttribute("aria-disabled")).toBe("true");
  await user.click(screen.getByRole("switch"));
  await user.click(screen.getByRole("button", { name: "撤销浏览器 bbbbbbbb" }));
  expect(command).toHaveBeenCalledTimes(1);
});

it("keeps disconnected controls focusable and rejects writes including an already open confirmation", async () => {
  const { user, command, rerender } = setup(login);
  await user.click(screen.getByRole("button", { name: "撤销当前浏览器" }));
  rerender({ unavailable: true });
  const confirm = screen.getByRole("button", { name: "确认撤销" });
  expect(confirm.hasAttribute("disabled")).toBe(false);
  expect(confirm.getAttribute("aria-description")).toContain("连接中断");
  await user.click(confirm);
  expect(command).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "取消" }));
  await user.click(screen.getByRole("switch"));
  expect(command).not.toHaveBeenCalled();
});

it("rejects malformed replies without recording success or refreshing", async () => {
  const { command, refresh, user } = setup();
  command.mockResolvedValue({});
  await user.click(screen.getByRole("switch"));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  expect(refresh).not.toHaveBeenCalled();
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("false");
});

it("does not let stale snapshots or command replies replace a newer revision", async () => {
  const { command, user, rerender } = setup();
  command.mockResolvedValue({ ...login, revision: 3 });
  await user.click(screen.getByRole("switch"));
  rerender({ access: { ...local, revision: 2 } });
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
  let resolve!: (value: ConsoleAccess) => void;
  command.mockReturnValueOnce(new Promise(done => { resolve = done; }));
  await user.click(screen.getByRole("switch"));
  rerender({ access: { ...login, revision: 5 } });
  resolve({ ...local, revision: 4 });
  await waitFor(() => expect(screen.getByRole("switch").getAttribute("aria-disabled")).toBeNull());
  expect(screen.getByRole("switch").getAttribute("aria-checked")).toBe("true");
});

it("rejects malformed access values at the response boundary", () => {
  for (const value of [null, {}, { ...local, revision: true }, { ...local, requireLogin: "false" },
    { ...login, sessions: [{ ...current, id: "not-a-session" }] }, { ...login, sessions: [{ ...current, lastSeen: null }] }]) expect(() => parseConsoleAccess(value)).toThrow(ApiError);
});

it("updates an already displayed authorization result when the language changes", async () => {
  const { user } = setup();
  await user.click(screen.getByRole("switch"));
  await screen.findByRole("status");
  try {
    act(() => setLanguageChoice("en"));
    expect(screen.getByRole("status").textContent).toContain("Browser authorization enabled");
    expect(screen.getByRole("status").textContent).not.toMatch(/[一-鿿]/);
  } finally { act(() => setLanguageChoice("browser")); }
});
