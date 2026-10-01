import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { ApiError } from "./api";
import type { HarnessHealth } from "./types";
import { accountCommand, parseAccountLogin, parseHarnessAccount } from "./harness-account";
import type { AccountLogin, AccountSource } from "./harness-account";
import { dayClock } from "./objective-display";

const statusLabel = { unknown: "状态未知", "logged-out": "尚未登录", ready: "已配置", unsupported: "尚未验证" };
const loginLabel = { pending: "等待浏览器授权", completed: "登录完成", cancelled: "已取消", failed: "登录失败", unconfirmed: "停止未确认" };
const accountTypeLabel: Record<string, string> = { chatgpt: "ChatGPT", apiKey: "API key", subscription: "订阅", metered: "按量" };
const accountGuidance: Record<string, string> = {
  ACCOUNT_CAPABILITY_UNVERIFIED: "该原生账户路径尚未验证，请在原生工具中管理本机登录。",
  ACCOUNT_CREDENTIAL_CHANGE_PENDING: "账户操作尚未完成，凭据已保留。",
  ACCOUNT_NOT_CHECKED: "重新检测当前 Harness 后查看账户状态。",
  ACCOUNT_LOGIN_REQUIRED: "完成当前来源的登录后重新检测。",
};

/** Secret requests never retain an error body or a replay packet. */
function failureText(error: unknown, secret: boolean): string {
  if (error instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT"].includes(error.code)) return "账户设置已变化，请刷新后重新操作。";
  if (error instanceof ApiError && error.code === "ACCOUNT_BUSY") return "仍有执行使用该账户；结束并确认停止后再操作。";
  return secret ? "密钥设置未确认，请刷新账户状态；需要再次设置时重新输入密钥。" : "账户操作未确认，请刷新状态后再操作。";
}

/** Account mutations are explicit, authenticated actions in the Harness section. */
export function HarnessAccount({ row, name, api, csrfToken, canWrite, title, onRefreshed }: {
  row: HarnessHealth; name: string; api: ConsoleApi; csrfToken: string; canWrite: boolean;
  title: string | undefined; onRefreshed: () => Promise<unknown> | void;
}) {
  const account = parseHarnessAccount(row.account, row.adapter);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [key, setKey] = useState("");
  const [login, setLogin] = useState<AccountLogin | null>(null);
  const [removing, setRemoving] = useState(false);
  useEffect(() => { setKey(""); setLogin(null); setRemoving(false); }, [account?.source, account?.credentialRevision]);
  useEffect(() => { if (!canWrite) { setKey(""); setLogin(null); setRemoving(false); } }, [canWrite]);
  useEffect(() => {
    if (canWrite && account?.pendingLogin && !login) setLogin(account.pendingLogin);
  }, [canWrite, account?.pendingLogin?.loginId, login]);
  useEffect(() => {
    if (!login || login.state !== "pending" || !canWrite) return;
    let closed = false;
    const timer = window.setInterval(async () => {
      try {
        const reply = await accountCommand(api, "account_status", { adapter: row.adapter, loginId: login.loginId }, csrfToken);
        const next = parseAccountLogin(reply.login);
        if (!closed && next) {
          // Status responses cannot introduce or repeat the browser URL/code.
          setLogin(previous => previous ? { ...previous, state: next.state } : null);
          if (next.state === "completed") await onRefreshed();
        }
      } catch (failure) { if (!closed) {
        setError("登录状态读取失败；可取消本次登录或刷新账户状态。");
        if (failure instanceof ApiError && failure.code === "ACCOUNT_OPERATION_UNAVAILABLE") {
          setLogin(previous => previous ? { ...previous, state: "unconfirmed", authUrl: undefined, verificationUrl: undefined, userCode: undefined } : null);
        }
      } }
    }, 2000);
    return () => { closed = true; window.clearInterval(timer); };
  }, [login?.loginId, login?.state, canWrite, api, csrfToken, row.adapter]);
  if (!account) return <div id={`harness-${row.adapter}-account`} tabIndex={-1} className="harness-account-controls"><h4>账户</h4><p className="small muted">尚未记录账户来源。</p></div>;
  const independent = account.source === "worker";
  const pending = login?.state === "pending" || login?.state === "unconfirmed";
  const disabled = busy || !canWrite || !!pending;

  async function action(operation: string, params: Record<string, unknown> = {}, secret = false) {
    if (busy || !canWrite || !account) return;
    setBusy(true); setError(""); setNote(""); setRemoving(false);
    try {
      const reply = await accountCommand(api, operation, { adapter: row.adapter, expectedRevision: account.revision, ...params }, csrfToken);
      const ticket = parseAccountLogin(reply.login);
      if (ticket) setLogin(ticket);
      else setNote("账户操作已完成。");
      await onRefreshed();
    } catch (failure) { setError(failureText(failure, secret)); }
    finally { setBusy(false); }
  }
  function source(value: AccountSource) {
    if (disabled || value === account?.source) return;
    void action("account_set", { source: value });
  }
  function submitKey() {
    if (disabled || !independent || !account?.capabilities.apiKey || !key.trim()) return;
    const value = key;
    setKey("");
    void action("account_login", { mode: "api-key", apiKey: value }, true);
  }
  async function cancel() {
    if (!login || busy || !canWrite) return;
    setBusy(true); setError("");
    try {
      const reply = await accountCommand(api, "account_cancel", { adapter: row.adapter, loginId: login.loginId }, csrfToken);
      const ticket = parseAccountLogin(reply.login);
      if (ticket) setLogin({ loginId: ticket.loginId, state: ticket.state, expiresAt: ticket.expiresAt });
      else setError("取消结果未知，尚未确认停止。");
      await onRefreshed();
    } catch { setError("取消未确认，请刷新状态；尚未确认停止。"); }
    finally { setBusy(false); }
  }
  return <div id={`harness-${row.adapter}-account`} tabIndex={-1} className="harness-account-controls" aria-label={`${name} 账户`} aria-busy={busy}>
    <h4>账户</h4>
    <label className="harness-account-source"><span>账户来源</span>
      <select aria-label={`${name} 账户来源`} value={account.source} disabled={disabled} title={title}
        onChange={event => source(event.target.value as AccountSource)}>
        <option value="native">沿用本机登录</option>
        <option value="worker" disabled={!account.capabilities.workerAccount}>Worker 独立账户</option>
      </select>
    </label>
    <p className="small">{statusLabel[account.status]}{account.accountType ? ` · ${accountTypeLabel[account.accountType] ?? "未知"}` : ""}
      {account.checkedAt ? ` · ${dayClock(account.checkedAt)}` : ""}</p>
    <p className="small muted">{independent ? "之后开始的执行使用独立账户；运行中的执行保留原账户。" : "本机登录由你在原生工具中管理。"}</p>
    {account.guidance && <p className="small muted">{accountGuidance[account.reasonCode ?? ""] ?? account.guidance}</p>}
    {independent && <>
      {account.capabilities.oauth && <button type="button" className="button small-button" disabled={disabled} title={title}
        onClick={() => void action("account_login", { mode: "oauth" })}>登录 Worker 账户</button>}
      {account.capabilities.apiKey && <form className="harness-account-key" onSubmit={event => { event.preventDefault(); submitKey(); }}>
        <label><span>API key</span><input type="password" value={key} onChange={event => setKey(event.target.value)}
          autoComplete="off" spellCheck={false} aria-label={`${name} Worker API key`} disabled={disabled} /></label>
        <button type="submit" className="button small-button" disabled={disabled || !key.trim()} title={title}>保存密钥</button>
      </form>}
      <div className="actions">
        {account.capabilities.logout && <button type="button" className="button small-button" disabled={disabled} title={title}
          onClick={() => void action("account_logout")}>登出 Worker 账户</button>}
        {account.capabilities.remove && <button type="button" className="button small-button" disabled={disabled} title={title}
          onClick={() => setRemoving(true)}>移除独立账户</button>}
      </div>
    </>}
    {removing && <div role="group" aria-label="确认移除独立账户"><p>移除该 Worker 账户及其凭据，之后需要重新登录。</p>
      <button type="button" className="button small-button" disabled={disabled} onClick={() => void action("account_remove")}>确认移除</button>
      <button type="button" className="button small-button" onClick={() => setRemoving(false)}>保留账户</button>
    </div>}
    {login && <div className="harness-account-login" role="status">
      <p>{login.state === "pending" && login.kind && login.kind !== "oauth" ? "账户操作进行中" : loginLabel[login.state]}</p>
      {login.state === "pending" && canWrite && <>
        {(login.authUrl || login.verificationUrl) && <a href={login.authUrl || login.verificationUrl} target="_blank" rel="noopener noreferrer">在浏览器完成登录</a>}
        {login.userCode && <p>设备码：<code>{login.userCode}</code></p>}
        <p className="small muted">由你本人完成授权；截至 {dayClock(login.expiresAt)}。</p>
      </>}
      {pending && <button type="button" className="button small-button" disabled={busy || !canWrite} onClick={() => void cancel()}>取消本次登录</button>}
    </div>}
    {error && <p className="error-message" role="alert">{error}</p>}
    {note && <p className="success-message" role="status">{note}</p>}
  </div>;
}
