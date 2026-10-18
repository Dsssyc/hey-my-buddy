import i18n from "i18next";
import type zh from "./zh/reasons";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import { formatCoolingTime, formatDateTime, formatList, formatNumber } from "./format";

function parameter(value: unknown, key: string): string {
  if (isReason(value)) return reasonText(value);
  if (Array.isArray(value)) return formatList(value.map(item => parameter(item, key)));
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return i18n.t(`shell:reasonValue.${value ? "yes" : "no"}`);
  if (value === null) return i18n.t("shell:actor.unrecorded");
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    if ("model" in row) return [row.harness, row.provider, row.model, row.effort].join(" / ");
    if ("runId" in row) return `${row.runId} (${i18n.t(`shell:reasonValue.state.${row.state}`)})`;
    return JSON.stringify(value);
  }
  if (key === "retryAt") return formatCoolingTime(Date.parse(String(value)));
  if (key === "catalogReadAt") return formatDateTime(Date.parse(String(value)));
  if (key === "operation" || key === "bound" || key === "disposition") return i18n.t(`shell:reasonValue.${key}.${value}`);
  return String(value);
}

/** The single formatter for every program reason, evaluated in the active language. */
export function reasonText(reason: Reason): string {
  const template = i18n.getResource(i18n.resolvedLanguage!, "reasons", reason.code, { keySeparator: false }) as string;
  const values = Object.fromEntries(Object.entries(reason.params).map(([key, value]) => [key, parameter(value, key)]));
  const text = i18n.t(reason.code as keyof typeof zh, { ns: "reasons", keySeparator: false, ...values });
  const facts = Object.entries(values).filter(([key]) => !template.includes(`{{${key}}}`))
    .map(([key, value]) => i18n.t("shell:reasonFact", { name: i18n.t(`shell:reasonParam.${key}`), value }));
  return [text, ...facts].join(" ");
}
