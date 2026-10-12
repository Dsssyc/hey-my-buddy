import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ApiError } from "../api";
import type { ConsoleApi } from "../api";
import type { HarnessAccount } from "../harness-account";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import { HarnessAccountDialog } from "./HarnessAccountDialog";

/**
 * The account dialog of a harness: where its login comes from, and for a
 * separate account its login, key, logout and removal. What is kept here is what
 * keeps a credential safe: a key leaves its field when it is submitted and is
 * never kept or replayed, the link and code of a login come only from the reply
 * that starts it, a lost write permission takes the key and the login away, and
 * the login is finished by the user in the browser.
 */

afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); sessionStorage.clear(); localStorage.clear(); });

const facts: HarnessAccount = { adapter: "codex", source: "worker", revision: 3, credentialRevision: 2,
  status: "logged-out", accountType: null, checkedAt: null, reasonCode: null, guidance: null,
  capabilities: { workerAccount: true, oauth: true, apiKey: true, logout: true, remove: true } };
const ticket = { loginId: "login-fixture", state: "pending", expiresAt: "2026-09-30T14:00:00Z",
  authUrl: "https://auth.openai.com/fixture-private", userCode: "CODE-A" };

function props(command = vi.fn(), account: HarnessAccount = facts) {
  return { account, name: "Codex", api: { command } as unknown as ConsoleApi, csrfToken: "fixture-csrf",
    canWrite: true, reason: undefined as string | undefined, onRefreshed: vi.fn(), onClose: vi.fn() };
}
type Props = ReturnType<typeof props>;
const view = (data: Props) => <TooltipProvider><HarnessAccountDialog {...data} /></TooltipProvider>;
function open(data: Props) {
  const rendered = render(view(data));
  return { ...rendered, rerender: (next: Props) => rendered.rerender(view(next)) };
}
const dialog = () => screen.getByRole("dialog", { name: "Codex 账户" });
const button = (name: string) => within(dialog()).getByRole("button", { name });
const key = () => within(dialog()).getByLabelText("API key") as HTMLInputElement;
const inert = (element: HTMLElement) => element.getAttribute("aria-disabled") === "true" && !element.hasAttribute("disabled");

describe("the source of the account", () => {
  it("leaves the native login to the native tool and needs an explicit change of source", async () => {
    const command = vi.fn().mockResolvedValue({});
    const data = props(command, { ...facts, source: "native" });
    open(data);
    expect(command).not.toHaveBeenCalled();
    for (const name of ["登录", "登出", "移除", "保存密钥"]) expect(within(dialog()).queryByRole("button", { name })).toBeNull();
    expect(within(dialog()).queryByLabelText("API key")).toBeNull();
    expect(within(dialog()).getByRole("radio", { name: "共用登录" }).getAttribute("aria-checked")).toBe("true");
    await userEvent.click(within(dialog()).getByRole("radio", { name: "独立账户" }));
    expect(command).toHaveBeenCalledWith("account_set", { adapter: "codex", expectedRevision: 3, source: "worker" }, "fixture-csrf");
    expect(await within(dialog()).findByText("账户操作已完成。")).toBeTruthy();
    expect(data.onRefreshed).toHaveBeenCalledTimes(1);
  });

  it("changes nothing when the chosen source is already the source", async () => {
    const command = vi.fn();
    open(props(command));
    await userEvent.click(within(dialog()).getByRole("radio", { name: "独立账户" }));
    expect(command).not.toHaveBeenCalled();
  });

  it("explains each choice on the choice itself", () => {
    open(props());
    expect(within(dialog()).getByRole("radio", { name: "共用登录" }).getAttribute("aria-description")).toBe("由你在原生工具里管理");
    expect(within(dialog()).getByRole("radio", { name: "独立账户" }).getAttribute("aria-description"))
      .toBe("之后开始的执行使用独立账户，运行中的执行保留原账户");
  });

  it("leaves the separate account inert, and says why, when the harness has none", async () => {
    const command = vi.fn();
    open(props(command, { ...facts, source: "native", capabilities: { ...facts.capabilities, workerAccount: false } }));
    const worker = within(dialog()).getByRole("radio", { name: "独立账户" });
    expect(inert(worker)).toBe(true);
    expect(worker.getAttribute("aria-description")).toBe("这个 Harness 不支持独立账户");
    await userEvent.click(worker);
    expect(command).not.toHaveBeenCalled();
  });
});

describe("a key", () => {
  it("leaves its field when it is submitted, before the reply, and no failure keeps or shows it", async () => {
    let reject!: (value: Error) => void;
    const command = vi.fn().mockReturnValue(new Promise((_, failed) => { reject = failed; }));
    open(props(command));
    const input = key();
    const secret = "fixture-key-must-not-persist";
    await userEvent.type(input, secret);
    await userEvent.click(button("保存密钥"));
    expect(input.value).toBe("");
    expect(command).toHaveBeenCalledWith("account_login", { adapter: "codex", expectedRevision: 3, mode: "api-key", apiKey: secret }, "fixture-csrf");
    reject(new ApiError("NATIVE_ERROR", secret));
    expect((await screen.findByRole("alert")).textContent).toBe("密钥设置未确认，请刷新账户状态；需要再次设置时重新输入密钥。");
    expect(document.body.textContent).not.toContain(secret);
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
    // The request is not replayed, and the field stays empty for a new key.
    expect(command).toHaveBeenCalledTimes(1);
    expect(key().value).toBe("");
  });

  it("is submitted from the keyboard and cannot be saved empty", async () => {
    const command = vi.fn().mockResolvedValue({});
    open(props(command));
    expect(inert(button("保存密钥"))).toBe(true);
    await userEvent.click(button("保存密钥"));
    await userEvent.type(key(), "   {Enter}");
    expect(command).not.toHaveBeenCalled();
    await userEvent.type(key(), "k-1{Enter}");
    expect(command).toHaveBeenCalledWith("account_login", expect.objectContaining({ mode: "api-key", apiKey: "   k-1" }), "fixture-csrf");
    expect(key().value).toBe("");
  });

  it("is cleared, with the login, when the permission to write is lost", async () => {
    const command = vi.fn().mockResolvedValue({ login: ticket });
    const data = props(command);
    const rendered = open(data);
    await userEvent.type(key(), "typed-and-not-sent");
    await userEvent.click(button("登录"));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    rendered.rerender({ ...data, canWrite: false });
    await waitFor(() => expect(screen.queryByRole("link", { name: "在浏览器完成登录" })).toBeNull());
    expect(key().value).toBe("");
    expect(within(dialog()).queryByRole("status")).toBeNull();
    // Regaining it does not bring either back.
    rendered.rerender({ ...data, canWrite: true });
    expect(key().value).toBe("");
    expect(within(dialog()).queryByRole("link")).toBeNull();
    expect(document.body.textContent).not.toContain("typed-and-not-sent");
    expect(sessionStorage.length).toBe(0);
    expect(localStorage.length).toBe(0);
  });

  it("offers no key or login where the native paths are not verified", () => {
    open(props(vi.fn(), { ...facts, capabilities: { ...facts.capabilities, oauth: false, apiKey: false, logout: false } }));
    expect(within(dialog()).queryByRole("button", { name: "登录" })).toBeNull();
    expect(within(dialog()).queryByRole("button", { name: "保存密钥" })).toBeNull();
    expect(within(dialog()).queryByRole("button", { name: "登出" })).toBeNull();
    expect(within(dialog()).getByRole("button", { name: "移除" })).toBeTruthy();
  });
});

describe("the login", () => {
  it("is finished by the user in the browser: a link and a code are shown and nothing opens by itself", async () => {
    const opener = vi.spyOn(window, "open").mockReturnValue(null);
    const command = vi.fn().mockResolvedValue({ login: ticket });
    open(props(command));
    await userEvent.click(button("登录"));
    expect(command).toHaveBeenCalledWith("account_login", { adapter: "codex", expectedRevision: 3, mode: "oauth" }, "fixture-csrf");
    const link = await screen.findByRole("link", { name: "在浏览器完成登录" });
    expect(link.getAttribute("href")).toBe(ticket.authUrl);
    expect(link.getAttribute("target")).toBe("_blank");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
    const login = within(dialog()).getByRole("status", { name: "登录" });
    expect(within(login).getByText("等待授权")).toBeTruthy();
    expect(within(login).getByText("CODE-A")).toBeTruthy();
    expect(login.textContent).toMatch(/截止 \d{2}-\d{2} \d{2}:\d{2}/);
    expect(opener).not.toHaveBeenCalled();
    expect(command).toHaveBeenCalledTimes(1);
  });

  it("words each state of the login, and a pending operation that is not a browser login as in progress", () => {
    const words: [string, string][] = [["pending", "等待授权"], ["completed", "已登录"], ["cancelled", "已取消"], ["failed", "失败"], ["unconfirmed", "停止未确认"]];
    for (const [state, word] of words) {
      const rendered = open(props(vi.fn(), { ...facts, pendingLogin: { loginId: "l", state: state as never, expiresAt: ticket.expiresAt, kind: "oauth" } }));
      expect(within(dialog()).getByRole("status", { name: "登录" }).textContent).toContain(word);
      rendered.unmount();
    }
    open(props(vi.fn(), { ...facts, pendingLogin: { loginId: "l", state: "pending", expiresAt: ticket.expiresAt, kind: "api-key" } }));
    expect(within(dialog()).getByRole("status", { name: "登录" }).textContent).toContain("进行中");
  });

  it("keeps an unconfirmed cancellation visible and lets no second login or removal start", async () => {
    const command = vi.fn().mockResolvedValueOnce({ login: ticket }).mockResolvedValueOnce({ login: { ...ticket, state: "unconfirmed" } });
    open(props(command));
    await userEvent.click(button("登录"));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    await userEvent.click(button("取消"));
    expect(command).toHaveBeenLastCalledWith("account_cancel", { adapter: "codex", loginId: "login-fixture" }, "fixture-csrf");
    await within(dialog()).findByText("停止未确认");
    expect(inert(button("登录"))).toBe(true);
    expect(inert(button("移除"))).toBe(true);
    expect(within(dialog()).queryByRole("link")).toBeNull();
    await userEvent.click(button("登录"));
    await userEvent.click(button("移除"));
    expect(command).toHaveBeenCalledTimes(2);
    expect(within(dialog()).queryByRole("group", { name: "确认移除" })).toBeNull();
  });

  it("says a cancellation it cannot confirm", async () => {
    const command = vi.fn().mockResolvedValueOnce({ login: ticket }).mockRejectedValueOnce(new ApiError("NETWORK", "ignored"));
    open(props(command));
    await userEvent.click(button("登录"));
    await userEvent.click(await within(dialog()).findByRole("button", { name: "取消" }));
    expect((await within(dialog()).findByRole("alert")).textContent).toBe("取消未确认，请刷新状态；尚未确认停止。");
  });

  it("restores a login the record says is pending, without its link or code", () => {
    open(props(vi.fn(), { ...facts, pendingLogin: { loginId: "restored", state: "pending", expiresAt: ticket.expiresAt, kind: "oauth" } }));
    expect(within(dialog()).getByText("等待授权")).toBeTruthy();
    expect(within(dialog()).queryByRole("link")).toBeNull();
    expect(inert(button("登录"))).toBe(true);
    expect(within(dialog()).getByRole("button", { name: "取消" })).toBeTruthy();
  });
});

describe("reading the status of a login", () => {
  function polling() {
    vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  }
  const tick = () => act(async () => { await vi.advanceTimersByTimeAsync(2000); });

  it("can neither bring back nor repeat a link or a code", async () => {
    polling();
    const reply = { login: { ...ticket, authUrl: "https://elsewhere.example/replayed", userCode: "CODE-B" } };
    const command = vi.fn(async (operation: string) => operation === "account_status" ? reply : { login: ticket });
    // A login restored from the record has no link or code; the status cannot add them.
    const restored = open(props(command as never, { ...facts, pendingLogin: { loginId: "login-fixture", state: "pending", expiresAt: ticket.expiresAt, kind: "oauth" } }));
    await tick();
    expect(command).toHaveBeenCalledWith("account_status", { adapter: "codex", loginId: "login-fixture" }, "fixture-csrf");
    expect(within(dialog()).queryByRole("link")).toBeNull();
    expect(dialog().textContent).not.toContain("CODE-B");
    restored.unmount();
    // A login started here keeps the link and the code its own reply gave.
    open(props(command as never));
    await userEvent.click(button("登录"));
    const link = await screen.findByRole("link", { name: "在浏览器完成登录" });
    await tick();
    expect(command.mock.calls.filter(call => call[0] === "account_status")).toHaveLength(2);
    expect(screen.getByRole("link", { name: "在浏览器完成登录" })).toBe(link);
    expect(link.getAttribute("href")).toBe(ticket.authUrl);
    expect(dialog().textContent).toContain("CODE-A");
    expect(dialog().textContent).not.toContain("CODE-B");
    expect(dialog().textContent).not.toContain("elsewhere.example");
  });

  it("reloads the record once the login is completed, and stops asking", async () => {
    polling();
    const command = vi.fn(async (operation: string) => operation === "account_status" ? { login: { ...ticket, state: "completed" } } : { login: ticket });
    const data = props(command as never);
    open(data);
    await userEvent.click(button("登录"));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    data.onRefreshed.mockClear();
    await tick();
    await within(dialog()).findByText("已登录");
    expect(data.onRefreshed).toHaveBeenCalledTimes(1);
    await tick();
    expect(command.mock.calls.filter(call => call[0] === "account_status")).toHaveLength(1);
  });

  it("says when it cannot read the status, and drops the link when the operation is no longer there", async () => {
    polling();
    const command = vi.fn(async (operation: string) => {
      if (operation === "account_status") throw new ApiError("ACCOUNT_OPERATION_UNAVAILABLE", "gone");
      return { login: ticket };
    });
    open(props(command as never));
    await userEvent.click(button("登录"));
    await screen.findByRole("link", { name: "在浏览器完成登录" });
    await tick();
    expect((await within(dialog()).findByRole("alert")).textContent).toBe("登录状态读取失败；可取消本次登录或刷新账户状态。");
    expect(within(dialog()).getByText("停止未确认")).toBeTruthy();
    expect(within(dialog()).queryByRole("link")).toBeNull();
    expect(dialog().textContent).not.toContain("CODE-A");
  });

  it("asks nothing while the permission to write is missing", async () => {
    polling();
    const command = vi.fn();
    open({ ...props(command, { ...facts, pendingLogin: { loginId: "l", state: "pending", expiresAt: ticket.expiresAt, kind: "oauth" } }), canWrite: false });
    await tick();
    expect(command).not.toHaveBeenCalled();
  });
});

describe("what fails", () => {
  const failing = (error: Error) => vi.fn().mockRejectedValue(error);

  it("says a revision conflict in its own words", async () => {
    open(props(failing(new ApiError("REVISION_CONFLICT", "stale"))));
    await userEvent.click(button("登录"));
    expect((await within(dialog()).findByRole("alert")).textContent).toBe("账户设置已变化，请刷新后重新操作。");
  });

  it("says an account that is still in use in its own words", async () => {
    open(props(failing(new ApiError("ACCOUNT_BUSY", "in use"))));
    await userEvent.click(button("登出"));
    expect((await within(dialog()).findByRole("alert")).textContent).toBe("仍有执行使用该账户；结束并确认停止后再操作。");
  });

  it("says anything else is not confirmed, and never shows the body of the failure", async () => {
    open(props(failing(new ApiError("NATIVE_ERROR", "stack trace and token abc"))));
    await userEvent.click(button("登出"));
    expect((await within(dialog()).findByRole("alert")).textContent).toBe("账户操作未确认，请刷新状态后再操作。");
    expect(document.body.textContent).not.toContain("token abc");
  });
});

describe("removing the separate account", () => {
  it("asks again in the dialog, with the focus on keeping the account, and removes only on confirmation", async () => {
    const command = vi.fn().mockResolvedValue({});
    const data = props(command);
    open(data);
    await userEvent.click(button("移除"));
    const ask = within(dialog()).getByRole("group", { name: "确认移除" });
    expect(ask.textContent).toContain("移除独立账户及其凭据，之后需要重新登录。");
    expect(command).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(within(ask).getByRole("button", { name: "保留账户" }));
    await userEvent.click(within(ask).getByRole("button", { name: "保留账户" }));
    expect(within(dialog()).queryByRole("group", { name: "确认移除" })).toBeNull();
    expect(command).not.toHaveBeenCalled();
    await userEvent.click(button("移除"));
    await userEvent.click(within(dialog()).getByRole("button", { name: "确认移除" }));
    expect(command).toHaveBeenCalledWith("account_remove", { adapter: "codex", expectedRevision: 3 }, "fixture-csrf");
    await waitFor(() => expect(within(dialog()).queryByRole("group", { name: "确认移除" })).toBeNull());
    expect(data.onRefreshed).toHaveBeenCalledTimes(1);
  });

  it("logs out with the recorded revision", async () => {
    const command = vi.fn().mockResolvedValue({});
    open(props(command));
    await userEvent.click(button("登出"));
    expect(command).toHaveBeenCalledWith("account_logout", { adapter: "codex", expectedRevision: 3 }, "fixture-csrf");
  });
});

describe("a session that cannot write", () => {
  it("leaves every change inert, says why on each, and sends nothing", async () => {
    const command = vi.fn();
    open({ ...props(command), canWrite: false, reason: LOGIN_EXPIRED_ACTION_REFUSAL });
    for (const name of ["登录", "登出", "移除", "保存密钥"]) {
      expect(inert(button(name))).toBe(true);
      expect(button(name).getAttribute("aria-description")).toBe(LOGIN_EXPIRED_ACTION_REFUSAL);
      await userEvent.click(button(name));
    }
    for (const name of ["共用登录", "独立账户"]) {
      const radio = within(dialog()).getByRole("radio", { name });
      expect(inert(radio)).toBe(true);
      expect(radio.getAttribute("aria-description")).toBe(LOGIN_EXPIRED_ACTION_REFUSAL);
    }
    await userEvent.click(within(dialog()).getByRole("radio", { name: "共用登录" }));
    await userEvent.type(key(), "never-sent{Enter}");
    expect(key().value).toBe("");
    expect(command).not.toHaveBeenCalled();
    expect(within(dialog()).queryByRole("group", { name: "确认移除" })).toBeNull();
  });

  it("lets no second change start while one is under way", async () => {
    let finish!: (value: unknown) => void;
    const command = vi.fn().mockReturnValue(new Promise(resolve => { finish = resolve; }));
    open(props(command));
    await userEvent.click(button("登出"));
    expect(inert(button("登录"))).toBe(true);
    await userEvent.click(button("登录"));
    await userEvent.click(within(dialog()).getByRole("radio", { name: "共用登录" }));
    expect(command).toHaveBeenCalledTimes(1);
    await act(async () => { finish({}); });
    await waitFor(() => expect(inert(button("登录"))).toBe(false));
  });
});

describe("closing", () => {
  it("closes from its own button and from the keyboard, and keeps what it did not do unsent", async () => {
    const data = props();
    open(data);
    await userEvent.click(button("关闭"));
    expect(data.onClose).toHaveBeenCalledTimes(1);
    await userEvent.keyboard("{Escape}");
    expect(data.onClose).toHaveBeenCalledTimes(2);
  });
});
