import { locale } from "./index";

// Clock times use 24 hours in every language: they are read against a timeline.
const clock = new Intl.DateTimeFormat(locale, { hour: "2-digit", minute: "2-digit", second: "2-digit", hourCycle: "h23" });
const dateTime = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" });

/** 18:06:41 */
export function formatClock(ms: number): string {
  return clock.format(ms);
}

/** A full local date and time, with the month written out where the locale's numeric order is ambiguous. */
export function formatDateTime(ms: number): string {
  return dateTime.format(ms);
}
