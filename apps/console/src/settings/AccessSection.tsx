import { useCopyState } from "../i18n/use-copy-state";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "../api";
import { errorText, parseConsoleAccess } from "../api";
import type { ConsoleAccess } from "../types";
import { formatDateTime, formatShortDateTime } from "../i18n/format";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";
import { Switch } from "../components/ui/switch";
import { StateMark } from "../components/ui/state-mark";
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle, DialogTrigger } from "../components/ui/dialog";
import { Section } from "./Section";

type AccessNote = "" | "access.enabled" | "access.disabled" | "access.revoked" | "access.loggedOut";

export function AccessSection({ api, csrfToken, access, refresh, unavailable }: {
  api: ConsoleApi; csrfToken: string; access?: ConsoleAccess;
  refresh: () => Promise<unknown>; unavailable: boolean;
}) {
  const { t } = useTranslation("settings");
  const [current, setCurrent] = useState(access);
  const [busy, setBusy] = useState(false), [error, setError] = useCopyState(), [note, setNote] = useState<AccessNote>("");
  const [loggedOut, setLoggedOut] = useState(false), [confirming, setConfirming] = useState(false);
  const revokedCurrent = useRef<string | undefined>(undefined);
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    setCurrent(previous => access && previous && previous.revision > access.revision ? previous : access);
    if (access && (!current || access.revision >= current.revision)
      && (!access.requireLogin || access.sessions.some(session => session.current && session.id !== revokedCurrent.current))) setLoggedOut(false);
  }, [access]);
  const reason = unavailable ? t("access.disconnected") : busy ? t("access.busy")
    : loggedOut ? t("access.loggedOutReason") : !current ? t("access.unavailable") : undefined;
  async function run(operation: string, params: object, message: AccessNote, exits = false) {
    if (busy || unavailable || loggedOut || !current) return;
    setBusy(true); setError(""); setNote("");
    try {
      const reply = parseConsoleAccess(await api.command(operation, params, csrfToken));
      setCurrent(previous => previous && previous.revision > reply.revision ? previous : reply);
      if (exits) revokedCurrent.current = current.sessions.find(session => session.current)?.id;
      setLoggedOut(exits); setNote(message); setConfirming(false);
      // Access changes rotate CSRF authority. Refresh before another write.
      await refresh();
    } catch (failure) { setError(() => errorText(failure)); }
    finally { setBusy(false); }
  }
  return <Dialog open={confirming} onOpenChange={setConfirming}><Section title={t("access.title")}>
    <div className="min-w-0 space-y-3" aria-busy={busy}>
      {current ? <>
        <label className="flex min-h-8 items-center gap-3">
          <Hint text={reason}><Switch checked={current.requireLogin} aria-disabled={!!reason || undefined}
            onCheckedChange={checked => void run("console_access_set", { requireLogin: checked }, checked ? "access.enabled" : "access.disabled")} /></Hint>
          <span>{t("access.allowAuthorized")}</span>
        </label>
        <p className="max-w-[560px] text-xs leading-relaxed text-ink2">{t("access.description")}</p>
        {current.requireLogin && <div className="max-w-[560px]">
          <h3 className="mb-1 text-xs text-ink2">{t("access.browsers")}</h3>
          <ul aria-label={t("access.browsers")} className="divide-y border-t">
            {current.sessions.map(session => <li key={session.id} className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-x-3 gap-y-2 py-2.5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_96px] sm:gap-x-5">
              <span className="inline-flex items-center gap-2 font-mono tabular-nums">{session.id.slice(0, 8)}
                {session.current && <span className="inline-flex items-center gap-1 font-sans text-xs text-live"><StateMark kind="live" />{t("access.current")}</span>}
              </span>
              <Hint text={t("access.lastSeen", { time: formatDateTime(session.lastSeen * 1000) })}>
                <time tabIndex={0} dateTime={new Date(session.lastSeen * 1000).toISOString()} className="text-xs text-ink2 tabular-nums">{formatShortDateTime(session.lastSeen * 1000)}</time>
              </Hint>
              <Hint text={reason}>{session.current
                ? <DialogTrigger asChild><Button variant="outline" size="sm" className="ml-auto" aria-disabled={!!reason || undefined}
                    aria-label={t("access.revokeCurrent")} onClick={event => { if (reason) event.preventDefault(); }}>{t("access.revoke")}</Button></DialogTrigger>
                : <Button variant="outline" size="sm" className="ml-auto" aria-disabled={!!reason || undefined}
                    aria-label={t("access.revokeBrowser", { id: session.id.slice(0, 8) })}
                    onClick={() => void run("console_session_revoke", { sessionId: session.id }, "access.revoked")}>{t("access.revoke")}</Button>}
              </Hint>
            </li>)}
          </ul>
        </div>}
      </> : <p className="text-xs text-ink2">{t("access.unavailable")}</p>}
      {note && <p className="flex items-center gap-2 text-xs text-ink2" role="status"><StateMark kind="done" />{t(note)}</p>}
      {error && <p className="flex items-center gap-2 text-xs text-fail" role="alert"><StateMark kind="fail" />{error}</p>}
        <DialogContent onOpenAutoFocus={event => { event.preventDefault(); cancel.current?.focus(); }}>
          <DialogHeader><DialogTitle>{t("access.confirmTitle")}</DialogTitle><DialogDescription>{t("access.confirmBody")}</DialogDescription></DialogHeader>
          <DialogFooter>
            <DialogClose asChild><Button ref={cancel} variant="outline">{t("access.cancel")}</Button></DialogClose>
            <Hint text={reason}><Button variant="default" aria-disabled={!!reason || undefined}
              onClick={() => void run("console_logout", {}, "access.loggedOut", true)}>{t("access.confirm")}</Button></Hint>
          </DialogFooter>
        </DialogContent>
    </div>
  </Section></Dialog>;
}
