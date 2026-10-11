import type { TFunction } from "i18next";
import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import type { ConsoleApi } from "../api";
import { ApiError } from "../api";
import { accountCommand, parseAccountLogin } from "../harness-account";
import type { AccountLogin, AccountSource, HarnessAccount } from "../harness-account";
import { harnessTime } from "./harness";

/** Secret requests never retain an error body or a replay packet. */
function failureText(error: unknown, secret: boolean, t: TFunction<"buddies">): string {
  if (error instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT"].includes(error.code)) return t("harness.dialog.conflict");
  if (error instanceof ApiError && error.code === "ACCOUNT_BUSY") return t("harness.dialog.busy");
  return t(secret ? "harness.dialog.secretUnconfirmed" : "harness.dialog.unconfirmed");
}

/**
 * The account of one harness: where its login comes from, and for a separate
 * account its login, its API key, its logout and its removal. Every change is an
 * explicit, authenticated click. The login itself is finished by the user in the
 * browser; this dialog only shows the link and waits.
 */
export function HarnessAccountDialog({ account, name, api, csrfToken, canWrite, reason, onRefreshed, onClose }: {
  account: HarnessAccount; name: string; api: ConsoleApi; csrfToken: string;
  canWrite: boolean;
  /** Why nothing can be changed, when the session cannot write. */
  reason: string | undefined;
  onRefreshed: () => Promise<unknown> | void;
  onClose: () => void;
}) {
  const { t } = useTranslation("buddies");
  const id = useId();
  const adapter = account.adapter;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const [key, setKey] = useState("");
  const [login, setLogin] = useState<AccountLogin | null>(null);
  const [removing, setRemoving] = useState(false);
  useEffect(() => { setKey(""); setLogin(null); setRemoving(false); }, [account.source, account.credentialRevision]);
  useEffect(() => { if (!canWrite) { setKey(""); setLogin(null); setRemoving(false); } }, [canWrite]);
  useEffect(() => {
    if (canWrite && account.pendingLogin && !login) setLogin(account.pendingLogin);
  }, [canWrite, account.pendingLogin?.loginId, login]);
  useEffect(() => {
    if (!login || login.state !== "pending" || !canWrite) return;
    let closed = false;
    const timer = window.setInterval(async () => {
      try {
        const reply = await accountCommand(api, "account_status", { adapter, loginId: login.loginId }, csrfToken);
        const next = parseAccountLogin(reply.login);
        if (!closed && next) {
          // Status responses cannot introduce or repeat the browser URL/code.
          setLogin(previous => previous ? { ...previous, state: next.state } : null);
          if (next.state === "completed") await onRefreshed();
        }
      } catch (failure) { if (!closed) {
        setError(t("harness.dialog.statusFailed"));
        if (failure instanceof ApiError && failure.code === "ACCOUNT_OPERATION_UNAVAILABLE") {
          setLogin(previous => previous ? { ...previous, state: "unconfirmed", authUrl: undefined, verificationUrl: undefined, userCode: undefined } : null);
        }
      } }
    }, 2000);
    return () => { closed = true; window.clearInterval(timer); };
  }, [login?.loginId, login?.state, canWrite, api, csrfToken, adapter]);

  const independent = account.source === "worker";
  const pending = login?.state === "pending" || login?.state === "unconfirmed";
  const disabled = busy || !canWrite || !!pending;
  const inert = disabled || undefined;

  async function action(operation: string, params: Record<string, unknown> = {}, secret = false) {
    if (busy || !canWrite) return;
    setBusy(true); setError(""); setNote(""); setRemoving(false);
    try {
      const reply = await accountCommand(api, operation, { adapter, expectedRevision: account.revision, ...params }, csrfToken);
      const ticket = parseAccountLogin(reply.login);
      if (ticket) setLogin(ticket);
      else setNote(t("harness.dialog.done"));
      await onRefreshed();
    } catch (failure) { setError(failureText(failure, secret, t)); }
    finally { setBusy(false); }
  }
  function source(value: AccountSource) {
    if (disabled || value === account.source || (value === "worker" && !account.capabilities.workerAccount)) return;
    void action("account_set", { source: value });
  }
  function submitKey() {
    if (disabled || !independent || !account.capabilities.apiKey || !key.trim()) return;
    const value = key;
    // The key leaves the field the moment it is submitted, whatever the reply is.
    setKey("");
    void action("account_login", { mode: "api-key", apiKey: value }, true);
  }
  async function cancel() {
    if (!login || busy || !canWrite) return;
    setBusy(true); setError("");
    try {
      const reply = await accountCommand(api, "account_cancel", { adapter, loginId: login.loginId }, csrfToken);
      const ticket = parseAccountLogin(reply.login);
      if (ticket) setLogin({ loginId: ticket.loginId, state: ticket.state, expiresAt: ticket.expiresAt });
      else setError(t("harness.dialog.cancelUnknown"));
      await onRefreshed();
    } catch { setError(t("harness.dialog.cancelUnconfirmed")); }
    finally { setBusy(false); }
  }

  const loginWord = login && (login.state === "pending" && login.kind && login.kind !== "oauth"
    ? t("harness.dialog.loginState.running") : t(`harness.dialog.loginState.${login.state}`));
  return <Dialog open onOpenChange={open => { if (!open) onClose(); }}>
    <DialogContent showCloseButton={false} aria-describedby={undefined} aria-busy={busy} className="sm:max-w-md">
      <DialogHeader><DialogTitle>{t("harness.dialog.title", { name })}</DialogTitle></DialogHeader>
      <div className="flex flex-col gap-4">
        <Segmented aria-label={t("harness.dialog.source")} value={account.source}
          onValueChange={value => source(value as AccountSource)}>
          <Hint text={reason ?? t("harness.dialog.nativeHint")}>
            <SegmentedItem value="native" aria-disabled={inert}>{t("harness.account.source.native")}</SegmentedItem>
          </Hint>
          <Hint text={!account.capabilities.workerAccount ? t("harness.dialog.workerUnsupported") : reason ?? t("harness.dialog.workerHint")}>
            <SegmentedItem value="worker" aria-disabled={disabled || !account.capabilities.workerAccount || undefined}>
              {t("harness.account.source.worker")}
            </SegmentedItem>
          </Hint>
        </Segmented>

        {independent && <div className="flex flex-col gap-3">
          {account.capabilities.oauth && <div>
            <Hint text={reason}>
              <Button variant="outline" aria-disabled={inert}
                onClick={() => { if (!disabled) void action("account_login", { mode: "oauth" }); }}>{t("harness.dialog.login")}</Button>
            </Hint>
          </div>}
          {account.capabilities.apiKey && <form className="flex flex-col gap-1.5" onSubmit={event => { event.preventDefault(); submitKey(); }}>
            <label htmlFor={`${id}-key`} className="text-xs text-ink3">{t("harness.dialog.apiKey")}</label>
            <div className="flex gap-2">
              <Input id={`${id}-key`} type="password" className="h-8 flex-1 text-sm shadow-none" value={key} autoComplete="off" spellCheck={false}
                aria-disabled={inert} readOnly={disabled} onChange={event => setKey(event.target.value)} />
              <Hint text={reason}>
                <Button type="submit" variant="outline" aria-disabled={disabled || !key.trim() || undefined}>{t("harness.dialog.saveKey")}</Button>
              </Hint>
            </div>
          </form>}
          {(account.capabilities.logout || account.capabilities.remove) && <div className="flex gap-2">
            {account.capabilities.logout && <Hint text={reason}>
              <Button variant="outline" aria-disabled={inert}
                onClick={() => { if (!disabled) void action("account_logout"); }}>{t("harness.dialog.logout")}</Button>
            </Hint>}
            {account.capabilities.remove && <Hint text={reason}>
              <Button variant="outline" aria-disabled={inert}
                onClick={() => { if (!disabled) setRemoving(true); }}>{t("harness.dialog.remove")}</Button>
            </Hint>}
          </div>}
        </div>}

        {removing && <div role="group" aria-label={t("harness.dialog.removeGroup")} className="flex flex-col gap-3 border-t pt-4">
          <p>{t("harness.dialog.removeAsk")}</p>
          <div className="flex gap-2">
            <Button variant="outline" aria-disabled={inert} onClick={() => { if (!disabled) void action("account_remove"); }}>
              {t("harness.dialog.removeConfirm")}
            </Button>
            {/* Focus rests on the choice that removes nothing. */}
            <Button variant="outline" autoFocus onClick={() => setRemoving(false)}>{t("harness.dialog.removeKeep")}</Button>
          </div>
        </div>}

        {login && <div role="status" aria-label={t("harness.dialog.loginGroup")} className="flex flex-col gap-1.5 border-t pt-4">
          <span className="font-medium">{loginWord}</span>
          {login.state === "pending" && canWrite && <>
            {(login.authUrl || login.verificationUrl) && <a className="w-fit underline underline-offset-4" href={login.authUrl || login.verificationUrl}
              target="_blank" rel="noopener noreferrer">{t("harness.dialog.browser")}</a>}
            {login.userCode && <span><span className="text-xs text-ink3">{t("harness.dialog.code")}</span> <code className="font-mono">{login.userCode}</code></span>}
            <span className="text-xs text-ink3">{t("harness.dialog.expires")} {harnessTime(login.expiresAt)}</span>
          </>}
          {pending && <div><Button variant="outline" aria-disabled={busy || !canWrite || undefined}
            onClick={() => void cancel()}>{t("harness.dialog.cancel")}</Button></div>}
        </div>}

        {error && <p className="text-xs text-fail" role="alert">{error}</p>}
        {note && <p className="text-xs text-ink2" role="status">{note}</p>}
      </div>
      <DialogFooter><Button variant="outline" onClick={onClose}>{t("harness.dialog.close")}</Button></DialogFooter>
    </DialogContent>
  </Dialog>;
}
