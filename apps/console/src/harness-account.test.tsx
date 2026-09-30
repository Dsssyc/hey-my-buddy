import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./api";
import type { ConsoleApi } from "./api";
import { HarnessAccount } from "./HarnessAccount";
import { parseAccountLogin, parseHarnessAccount } from "./harness-account";
import type { HarnessAccount as Account } from "./harness-account";
import type { HarnessHealth } from "./types";

afterEach(() => { cleanup(); sessionStorage.clear(); localStorage.clear(); });
const facts: Account = { adapter: "codex", source: "worker", revision: 3, credentialRevision: 2,
  status: "logged-out", accountType: null, checkedAt: null, reasonCode: null, guidance: null,
  capabilities: { workerAccount: true, oauth: true, apiKey: true, logout: true, remove: true } };
const ticket = { loginId: "login-fixture", state: "pending", expiresAt: "2026-09-30T14:00:00Z",
  authUrl: "https://auth.openai.com/fixture-private" };
function row(account: Account = facts): HarnessHealth {
  return { adapter: "codex", status: "ready", available: true, revision: 1, manualPath: null, account };
}
function props(command = vi.fn(), account: Account = facts) {
  return { row: row(account), name: "Codex", api: { command } as unknown as ConsoleApi,
    csrfToken: "fixture-csrf", canWrite: true, title: undefined, onRefreshed: vi.fn() };
}

describe("private Worker account controls", () => {
  it("leaves native login management in the native tool and requires an explicit source change", async () => {
    const command = vi.fn().mockResolvedValue({});
    render(<HarnessAccount {...props(command, { ...facts, source: "native" })} />);
    expect(command).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "登录 Worker 账户" })).toBeNull();
    expect(screen.queryByRole("button", { name: "登出 Worker 账户" })).toBeNull();
    expect(screen.queryByLabelText("Codex Worker API key")).toBeNull();
    await userEvent.selectOptions(screen.getByLabelText("Codex 账户来源"), "worker");
    expect(command).toHaveBeenCalledWith("account_set", { adapter: "codex", expectedRevision: 3, source: "worker" }, "fixture-csrf");
  });
  it("clears a submitted key before the reply and does not retain or display an echoed failure", async () => {
    let reject!: (value: Error) => void;
    const command = vi.fn().mockReturnValue(new Promise((_, failed) => { reject = failed; }));
    render(<HarnessAccount {...props(command)} />);
    const input = screen.getByLabelText("Codex Worker API key") as HTMLInputElement;
    const secret = "fixture-key-must-not-persist";
    await userEvent.type(input, secret);
    await userEvent.click(screen.getByRole("button", { name: "保存密钥" }));
    expect(input.value).toBe("");
    expect(command).toHaveBeenCalledWith("account_login", { adapter: "codex", expectedRevision: 3, mode: "api-key", apiKey: secret }, "fixture-csrf");
    reject(new ApiError("NATIVE_ERROR", secret));
    await screen.findByRole("alert");
    expect(document.body.textContent).not.toContain(secret);
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
    expect(command).toHaveBeenCalledTimes(1);
  });
  it("clears the key and removes the login link when browser authority is lost", async () => {
    const command = vi.fn().mockResolvedValue({ login: ticket });
    const data = props(command);
    const rendered = render(<HarnessAccount {...data} />);
    await userEvent.click(screen.getByRole("button", { name: "登录 Worker 账户" }));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    rendered.rerender(<HarnessAccount {...data} canWrite={false} />);
    await waitFor(() => expect(screen.queryByRole("link", { name: "在浏览器完成登录" })).toBeNull());
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });
  it("keeps unconfirmed cancellation visible and prevents a second login or credential removal", async () => {
    const command = vi.fn().mockResolvedValueOnce({ login: ticket }).mockResolvedValueOnce({ login: { ...ticket, state: "unconfirmed" } });
    render(<HarnessAccount {...props(command)} />);
    await userEvent.click(screen.getByRole("button", { name: "登录 Worker 账户" }));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    await userEvent.click(screen.getByRole("button", { name: "取消本次登录" }));
    await screen.findByText("停止未确认");
    expect((screen.getByRole("button", { name: "登录 Worker 账户" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "移除独立账户" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.queryByRole("link")).toBeNull();
  });
  it("offers no key or OAuth controls when their native paths are unverified", () => {
    render(<HarnessAccount {...props(vi.fn(), { ...facts, capabilities: { ...facts.capabilities, oauth: false, apiKey: false, logout: false } })} />);
    expect(screen.queryByRole("button", { name: "登录 Worker 账户" })).toBeNull();
    expect(screen.queryByRole("button", { name: "保存密钥" })).toBeNull();
  });
  it("rejects malformed capability flags and unsafe or credential-bearing URLs", () => {
    expect(parseHarnessAccount({ ...facts, capabilities: { ...facts.capabilities, apiKey: "true" } }, "codex")).toBeNull();
    expect(parseHarnessAccount(facts, "claude")).toBeNull();
    expect(parseAccountLogin({ ...ticket, authUrl: "javascript:alert(1)" })).toBeNull();
    expect(parseAccountLogin({ ...ticket, authUrl: "https://user:secret@auth.openai.com/" })).toBeNull();
    expect(parseHarnessAccount({ ...facts, apiKey: "not-public" }, "codex")).not.toHaveProperty("apiKey");
    expect(parseHarnessAccount({ ...facts, pendingLogin: ticket }, "codex")?.pendingLogin).not.toHaveProperty("authUrl");
  });
});
