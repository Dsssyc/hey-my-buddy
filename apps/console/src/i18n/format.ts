import i18n from "i18next";
import { locale } from "./index";

// Clock times use 24 hours in every language: they are read against a timeline.
function makeFormatters() {
  return {
    clock: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" }),
    dateTime: new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" }),
    list: new Intl.ListFormat(locale, { type: "conjunction" }),
    minuteClock: new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", hourCycle: "h23" }),
    whole: new Intl.NumberFormat(locale),
    tenth: new Intl.NumberFormat(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 }),
  };
}
let formattingLocale = locale;
let currentFormatters = makeFormatters();
function formatters() {
  if (formattingLocale !== locale) {
    formattingLocale = locale;
    currentFormatters = makeFormatters();
  }
  return currentFormatters;
}

/** 18:06:41 */
export function formatClock(ms: number): string {
  return formatters().clock.format(ms);
}

/** 09-27 06:05, in digits and local time: for rows and fields that have room for no more. */
export function formatShortDateTime(ms: number): string {
  const date = new Date(ms);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Several names as the language lists them: "Codex and DSH", "Codex和DSH". */
export function formatList(names: string[]): string {
  return formatters().list.format(names);
}

/** A full local date and time, with the month written out where the locale's numeric order is ambiguous. */
export function formatDateTime(ms: number): string {
  return formatters().dateTime.format(ms);
}

/** 18:06 */
export function formatClockMinutes(ms: number): string {
  return formatters().minuteClock.format(ms);
}

/** A local instant without seconds, with enough date context to distinguish older days. */
export function formatMomentMinutes(ms: number, nowMs: number): string {
  const date = new Date(ms);
  const now = new Date(nowMs);
  if (date.toDateString() === now.toDateString()) return formatClockMinutes(ms);
  const short = formatShortDateTime(ms);
  return date.getFullYear() === now.getFullYear() ? short : `${date.getFullYear()}-${short}`;
}

/** 12,000 */
export function formatNumber(value: number): string {
  return formatters().whole.format(value);
}

/** 23.4k: a count of tokens in the room of a few characters; below a thousand it is written whole. */
export function formatCompact(value: number): string {
  return value < 1000 ? formatters().whole.format(value) : `${formatters().tenth.format(value / 1000)}k`;
}

/**
 * A length of time in the two units that matter. Fine adds the seconds that a
 * coarse reading drops: 3 min 08 s against 32 min.
 */
export function formatDuration(ms: number, fine = false): string {
  const total = Math.max(0, Math.round(ms / 1000));
  const [h, m, s] = [Math.floor(total / 3600), Math.floor(total / 60) % 60, total % 60];
  const part = (unit: "hour" | "minute" | "second", n: number, padded = false) =>
    i18n.t(`shell:duration.${unit}`, { n: padded ? String(n).padStart(2, "0") : formatters().whole.format(n) });
  if (h > 0) return fine ? `${part("hour", h)} ${part("minute", m, true)} ${part("second", s, true)}` : m ? `${part("hour", h)} ${part("minute", m)}` : part("hour", h);
  if (m > 0) return fine ? `${part("minute", m)} ${part("second", s, true)}` : total < 600 && s ? `${part("minute", m)} ${part("second", s)}` : part("minute", m);
  return part("second", s);
}

/** Wall time in whole minutes, including an explicit zero-minute part after hours. */
export function formatDurationMinutes(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 60_000));
  const hours = Math.floor(total / 60);
  const minutes = total % 60;
  const minute = (n: number) => i18n.t("shell:duration.minute", { n: formatNumber(n) });
  return hours ? `${i18n.t("shell:duration.hour", { n: formatNumber(hours) })} ${minute(minutes)}` : minute(minutes);
}

/** 19 minutes ago, in the nearest unit, written like a duration; an instant that has not come yet reads as now. */
export function formatRelative(ms: number, nowMs: number): string {
  const seconds = Math.max(0, Math.round((nowMs - ms) / 1000));
  const [unit, n] = seconds < 60 ? ["second", seconds] as const : seconds < 3600 ? ["minute", Math.round(seconds / 60)] as const
    : seconds < 86400 ? ["hour", Math.round(seconds / 3600)] as const : ["day", Math.round(seconds / 86400)] as const;
  return i18n.t(`shell:relative.${unit}`, { n: formatters().whole.format(n) });
}

/**
 * An exact instant in local time, as short as it can be and still not mislead:
 * today the clock, this year the date and the clock, another year the year too.
 */
export function formatMoment(ms: number, nowMs: number): string {
  const [date, now] = [new Date(ms), new Date(nowMs)];
  const pad = (part: number) => String(part).padStart(2, "0");
  const clockText = `${pad(date.getHours())}:${pad(date.getMinutes())}:${pad(date.getSeconds())}`;
  if (date.toDateString() === now.toDateString()) return clockText;
  const day = `${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
  return `${date.getFullYear() === now.getFullYear() ? "" : `${date.getFullYear()}-`}${day} ${clockText}`;
}

/** Calendar date for a records group, in the active locale. */
export function formatCalendarDate(ms: number): string {
  return new Intl.DateTimeFormat(locale, { year: "numeric", month: "2-digit", day: "2-digit" }).format(ms);
}
