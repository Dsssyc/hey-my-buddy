import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import { errorText, parseReminderSettings } from "./api";
import type { ReminderSettings as ReminderSettingsValue } from "./types";

/** The board-wide native-event silence threshold, saved independently of Buddy drafts. */
export function ReminderSettings({ api, csrfToken, settings, refresh, unavailable }: {
  api: ConsoleApi; csrfToken: string; settings?: ReminderSettingsValue;
  refresh: () => Promise<unknown>; unavailable: boolean;
}) {
  const { t } = useTranslation("shell");
  const [current, setCurrent] = useState(settings);
  const [draft, setDraft] = useState(settings ? String(settings.silenceSeconds / 60) : "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  useEffect(() => {
    if (settings && (!current || settings.revision > current.revision)) {
      setCurrent(settings);
      setDraft(String(settings.silenceSeconds / 60));
      setNote("");
    }
  }, [settings, current]);
  const minutes = Number(draft);
  const seconds = Math.round(minutes * 60);
  const valid = /^\d+(?:\.\d+)?$/.test(draft) && Number.isFinite(minutes)
    && minutes >= 1 / 60 && minutes <= 1440 && Number.isSafeInteger(seconds);
  const changed = valid && seconds !== current?.silenceSeconds;
  async function save() {
    if (busy || unavailable || !current || !changed) return;
    setBusy(true); setError(""); setNote("");
    try {
      const saved = parseReminderSettings(await api.command("reminder_settings_set", { silenceSeconds: seconds }, csrfToken));
      setCurrent(saved);
      setDraft(String(saved.silenceSeconds / 60));
      setNote(t("reminders.saved"));
      await refresh();
    } catch (failure) {
      setError(errorText(failure));
    } finally { setBusy(false); }
  }
  return <section className="panel settings-panel" aria-labelledby="reminder-settings-title" aria-busy={busy}>
    <div className="panel-heading"><h2 id="reminder-settings-title">{t("reminders.title")}</h2></div>
    {current ? <div className="flex flex-wrap items-end gap-3">
      <label className="grid gap-1 text-sm" htmlFor="reminder-silence-minutes">
        <span>{t("reminders.silenceMinutes")}</span>
        <input id="reminder-silence-minutes" className="h-9 w-32 rounded-sm border border-input bg-background px-2 font-mono tabular-nums text-foreground"
          type="number" inputMode="decimal" min={1 / 60} max="1440" step="any" value={draft}
          disabled={unavailable || busy} aria-invalid={draft.length > 0 && !valid || undefined}
          aria-describedby="reminder-range"
          onChange={event => { setDraft(event.target.value); setError(""); setNote(""); }} />
      </label>
      <button type="button" className="button" disabled={unavailable || busy || !changed} onClick={() => void save()}>
        {busy ? t("reminders.saving") : t("reminders.save")}
      </button>
    </div> : <p className="small muted">{t("reminders.unavailable")}</p>}
    <p id="reminder-range" className="small muted">{t("reminders.range")}</p>
    {note && <p className="success-message" role="status">{note}</p>}
    {error && <p className="error-message" role="alert">{error}</p>}
  </section>;
}
