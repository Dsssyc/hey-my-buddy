import { locale } from "./index";

// Clock times use 24 hours in every language: they are read against a timeline.
const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const dateTime = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" });
const list = new Intl.ListFormat(locale, { type: "conjunction" });

/** 18:06:41 */
export function formatClock(ms: number): string {
  return clock.format(ms);
}

/** 09-27 06:05, in digits and local time: for rows and fields that have room for no more. */
export function formatShortDateTime(ms: number): string {
  const date = new Date(ms);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Several names as the language lists them: "Codex and DSH", "Codex和DSH". */
export function formatList(names: string[]): string {
  return list.format(names);
}

/** A full local date and time, with the month written out where the locale's numeric order is ambiguous. */
export function formatDateTime(ms: number): string {
  return dateTime.format(ms);
}
