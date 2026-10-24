import i18n from "i18next";
import { locale } from "../i18n";
import { formatNumber } from "../i18n/format";

export const CATEGORY_ORDER = ["session-records", "harnesses", "workspaces", "runtimes", "backup", "durable"] as const;
export const ALWAYS_PROTECTED = new Set(["session-records", "backup", "durable"]);

export function categoryText(id: string): string {
  return i18n.t(`settingsStorage:categories.${id}`, { defaultValue: id });
}

export function reasonText(code: string): string {
  return i18n.t(`settingsStorage:reasons.${code}`, { defaultValue: code });
}

/** Binary units for compact display; exact bytes remain accessible in the hint. */
export function byteText(bytes: number): string {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let value = bytes, unit = 0;
  while (value >= 1024 && unit < units.length - 1) { value /= 1024; unit += 1; }
  return `${new Intl.NumberFormat(locale, { maximumFractionDigits: unit > 0 && value < 100 ? 1 : 0, minimumFractionDigits: unit > 0 && value < 100 ? 1 : 0 }).format(value)} ${units[unit]}`;
}

export function byteHint(bytes: number): string {
  return i18n.t("settingsStorage:bytes", { n: formatNumber(bytes) });
}
