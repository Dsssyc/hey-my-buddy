/** Strict recorded timestamps shared by timeline metrics and task detail. */
const ISO_INSTANT = /^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})(\.\d{1,9})?(Z|z|[+-]\d{2}:?\d{2})$/;

function isLeapYear(year: number): boolean {
  return (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0;
}

function daysInMonth(year: number, month: number): number {
  switch (month) {
    case 2: return isLeapYear(year) ? 29 : 28;
    case 4: case 6: case 9: case 11: return 30;
    default: return 31;
  }
}

/**
 * A strict instant parser. It rejects unparseable text, calendar dates that do
 * not exist (for example February 30), impossible clock values, and timestamps
 * without an explicit offset, because none of those can be placed on a shared
 * axis without inventing a reading.
 */
export function parseTimelineInstant(value: string | number | null | undefined): number | null {
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  if (typeof value !== "string") return null;
  const text = value.trim();
  const match = ISO_INSTANT.exec(text);
  if (match === null) return null;
  const [, year, month, day, hour, minute, second, , zone] = match;
  if (!isCalendarDate(Number(year), Number(month), Number(day))) return null;
  if (Number(hour) > 23 || Number(minute) > 59 || Number(second) > 59) return null;
  const offset = zone.replace(":", "");
  if (offset !== "Z" && offset !== "z" && (Number(offset.slice(1, 3)) > 23 || Number(offset.slice(3, 5)) > 59)) return null;
  const normalizedOffset = offset === "Z" || offset === "z" ? "Z" : `${offset.slice(0, 3)}:${offset.slice(3, 5)}`;
  const plain = text.slice(0, text.length - zone.length).replace(/^(\d{4}-\d{2}-\d{2})[Tt ]/, "$1T");
  const parsed = Date.parse(`${plain}${normalizedOffset}`);
  return Number.isFinite(parsed) ? parsed : null;
}

function isCalendarDate(year: number, month: number, day: number): boolean {
  if (month < 1 || month > 12 || day < 1) return false;
  return day <= daysInMonth(year, month);
}

