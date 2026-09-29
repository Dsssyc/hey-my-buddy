import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText, parseConsoleAccess } from "./api";
import type { ConsoleAccess } from "./types";


export function ConsoleAccessSettings({ api, csrfToken, access, refresh, unavailable }: {
  api: ConsoleApi; csrfToken: string; access?: ConsoleAccess;
  refresh: () => Promise<unknown>; unavailable: boolean;
}) {
  const [current, setCurrent] = useState(access);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [loggedOut, setLoggedOut] = useState(false);
  useEffect(() => {
    setCurrent(previous => access && previous && previous.revision > access.revision ? previous : access);
    if (access?.sessions.some(session => session.current) || access?.requireLogin === false) setLoggedOut(false);
  }, [access]);
  async function run(operation: string, params: object, message: string, exits = false) {
    if (busy || unavailable || loggedOut || !current) return;
    setBusy(true); setError(""); setNote("");
    try {
      const reply = parseConsoleAccess(await api.command(operation, params, csrfToken));
      setCurrent(reply); setLoggedOut(exits); setNote(message);
      // Access transitions rotate CSRF authority; never keep writing with the
      // old snapshot. The shared refresh also updates other visible controls.
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
      if (failure instanceof ApiError && failure.code === "REVISION_CONFLICT") await refresh();
    } finally { setBusy(false); }
  }
  const disabled = unavailable || busy || loggedOut || !current;
  return <section className="panel settings-panel" aria-labelledby="console-access-title" aria-busy={busy}>
    <div className="panel-heading"><h2 id="console-access-title">本机访问</h2>
      <p>默认直接打开本机固定地址。开启登录后，其他浏览器通过 buddy console 的一次性入口登录。</p></div>
    {current ? <>
      <label className="check-field"><input type="checkbox" role="switch" checked={current.requireLogin} disabled={disabled}
        onChange={event => void run("console_access_set", { requireLogin: event.target.checked, expectedRevision: current.revision },
          event.target.checked ? "已开启登录，当前窗口已登录。" : "已关闭登录，原有登录均已撤销。")}/><span>需要登录</span></label>
      <p className="small muted access-note">入口有效期为 10 分钟。登录不会因 30 天未使用而过期；关闭此开关会撤销所有登录。</p>
      {current.requireLogin && <ul className="access-sessions" aria-label="控制台登录">
        {current.sessions.map(session => <li key={session.id}>
          <div><span>{session.current ? "当前登录" : `登录 ${session.id.slice(0, 8)}`}</span>
            <p className="small muted">最近使用 {new Date(session.lastSeen * 1000).toLocaleString("zh-CN")}</p></div>
          <button type="button" className="button small-button" disabled={disabled}
            aria-label={session.current ? "退出当前登录" : `撤销登录 ${session.id.slice(0, 8)}`}
            onClick={() => void run(session.current ? "console_logout" : "console_session_revoke",
              session.current ? {} : { sessionId: session.id },
              session.current ? "已退出；运行 buddy console 重新登录。" : "已撤销该登录。", session.current)}>{session.current ? "退出登录" : "撤销"}</button>
        </li>)}
      </ul>}
    </> : <p className="small muted">访问设置暂不可用，请刷新工作台。</p>}
    {note && <p className="success-message" role="status">{note}</p>}
    {error && <p className="error-message" role="alert">{error}</p>}
  </section>;
}
