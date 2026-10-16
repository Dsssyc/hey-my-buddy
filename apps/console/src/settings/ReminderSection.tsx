import { useCopyState } from "../i18n/use-copy-state";
import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "../api";
import { errorText, parseReminderSettings } from "../api";
import type { ReminderSettings } from "../types";
import { formatNumber } from "../i18n/format";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { StateMark, UnsavedMark } from "../components/ui/state-mark";
import { Section } from "./Section";

const draftOf = (value: ReminderSettings) => value.silenceSeconds % 60 === 0 ? String(value.silenceSeconds / 60) : "";

export function ReminderSection({ api, csrfToken, settings, refresh, unavailable }: {
  api: ConsoleApi; csrfToken: string; settings?: ReminderSettings;
  refresh: () => Promise<unknown>; unavailable: boolean;
}) {
  const { t } = useTranslation("settings");
  const inputId = useId();
  const [current, setCurrent] = useState(settings), [draft, setDraft] = useState(settings ? draftOf(settings) : "");
  const latest = useRef(current);
  latest.current = current;
  const [busy, setBusy] = useState(false), [error, setError] = useCopyState(), [note, setNote] = useState(false);
  useEffect(() => {
    if (settings && (!current || settings.revision > current.revision)) {
      setCurrent(settings); setDraft(draftOf(settings)); setNote(false);
    }
  }, [settings, current]);
  const minutes = Number(draft), seconds = minutes * 60;
  const valid = /^\d+$/.test(draft) && Number.isSafeInteger(minutes) && minutes >= 1 && minutes <= 1440;
  const changed = valid && seconds !== current?.silenceSeconds;
  const reason = unavailable ? t("reminders.disconnected") : busy ? t("reminders.busy") : !current ? t("reminders.unavailable")
    : !valid ? t("reminders.range") : !changed ? t("reminders.unchanged") : undefined;
  async function save() {
    if (busy || unavailable || !current || !changed) return;
    setBusy(true); setError(""); setNote(false);
    try {
      const saved = parseReminderSettings(await api.command("reminder_settings_set", { silenceSeconds: seconds }, csrfToken));
      if (!latest.current || saved.revision >= latest.current.revision) {
        setCurrent(saved); setDraft(draftOf(saved));
      }
      setNote(true);
      await refresh();
    } catch (failure) { setError(() => errorText(failure)); }
    finally { setBusy(false); }
  }
  return <Section title={t("reminders.title")}>
    <div className="min-w-0 space-y-3" aria-busy={busy}>
      {current ? <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
        <label htmlFor={inputId} className="inline-flex items-center gap-2">{t("reminders.possiblyStuck")}{changed && <UnsavedMark label={t("reminders.unsaved")} />}</label>
        <div className="flex flex-wrap items-center gap-3">
        <Hint text={unavailable ? t("reminders.disconnected") : busy ? t("reminders.busy") : t("reminders.range")}>
          <Input id={inputId} className="h-8 w-16 text-sm tabular-nums" type="number" inputMode="numeric" min="1" max="1440" step="1" value={draft}
            readOnly={unavailable || busy} aria-disabled={unavailable || busy || undefined} aria-label={t("reminders.silenceMinutes")} aria-describedby={`${inputId}-description`}
            aria-invalid={draft.length > 0 && !valid || undefined}
            onChange={event => { if (unavailable || busy) return; setDraft(event.target.value); setError(""); setNote(false); }} />
        </Hint>
        <span>{t("reminders.minutesWithoutProgress")}</span>
        <Hint text={reason}><Button variant="outline" aria-disabled={!!reason || undefined} onClick={() => void save()}>{t(busy ? "reminders.saving" : "reminders.save")}</Button></Hint>
        </div>
        </div>
        <p id={`${inputId}-description`} className="max-w-[560px] text-xs leading-relaxed text-ink2">{t("reminders.description")}</p>
      </div> : <p className="text-xs text-ink2">{t("reminders.unavailable")}</p>}
      {current && current.silenceSeconds % 60 !== 0 && <p className="flex items-center gap-2 text-xs text-hand" role="status"><StateMark kind="hand" />{t("reminders.currentSeconds", { n: formatNumber(current.silenceSeconds) })}</p>}
      {note && <p className="flex items-center gap-2 text-xs text-ink2" role="status"><StateMark kind="done" />{t("reminders.saved")}</p>}
      {error && <p className="flex items-center gap-2 text-xs text-fail" role="alert"><StateMark kind="fail" />{error}</p>}
    </div>
  </Section>;
}
