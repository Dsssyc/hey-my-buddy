import i18n from "i18next";
import type zh from "./zh/reasons";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import { formatCoolingTime, formatDateTime, formatList, formatNumber } from "./format";

function maintenanceValidationError(value: unknown): string {
  const error = typeof value === "string" ? value : "";
  let key = "unknown";
  if (error === "answer-invalid-json") key = "invalidJson";
  else if (error === "summary-citation-outside-batch") key = "citationOutsideBatch";
  else if (error.startsWith("answer")) {
    const issue = error.split(": ").at(-1);
    if (issue === "enum" && error.includes(".citations")) key = "citationOutsideBatch";
    else key = ({
      type: "wrongType", enum: "invalidValue", required: "requiredField",
      additionalProperties: "unexpectedField", minLength: "textTooShort",
      maxLength: "textTooLong", maxItems: "tooManyItems",
    } as Record<string, string>)[issue ?? ""] ?? "unknown";
  } else if (error.startsWith("newNodes[")) {
    if (error.endsWith(": duplicate id")) key = "duplicateNode";
    else if (error.includes("name and definition must be nonblank")) key = "blankNode";
    else if (error.includes("existing node cannot be changed")) key = "changedNode";
    else if (error.includes("parent is not active") || error.includes("parent must be an active node")) key = "inactiveParent";
    else if (error.includes("parent cycle")) key = "parentCycle";
    else if (error.includes("active domain name already exists")) key = "duplicateName";
  } else if (error.startsWith("merges[")) {
    if (error.includes("duplicate source")) key = "duplicateMerge";
    else if (error.includes("merge source is not active")) key = "inactiveMergeSource";
    else if (error.includes("merge target must be an active node")) key = "inactiveMergeTarget";
    else key = "invalidMerge";
  } else if (error.startsWith("classifications[")) {
    if (error.includes("duplicate runId")) key = "duplicateClassification";
    else if (error.includes("domain node is not active")) key = "inactiveClassificationNode";
    else if (error.includes("missing classification")) key = "missingClassification";
  } else if (error.startsWith("summaries[")) {
    if (error.includes("duplicate citation")) key = "duplicateCitation";
    else if (error.includes("cited micro task was deleted")) key = "deletedCitation";
  }
  return i18n.t(`shell:maintenanceValidation.${key}`);
}

function parameter(value: unknown, key: string, code: string): string {
  if (isReason(value)) return reasonText(value);
  if (Array.isArray(value)) {
    if (!value.length && (key === "processes" || key === "unreadableProcesses")) return i18n.t("shell:reasonNoProcesses");
    return formatList(value.map(item => key === "errors" && code === "maintenance-answer-invalid"
      ? maintenanceValidationError(item) : parameter(item, key, code)));
  }
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return i18n.t(`shell:reasonValue.${value ? "yes" : "no"}`);
  if (value === null) return i18n.t("shell:actor.unrecorded");
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    if ((key === "processes" || key === "unreadableProcesses") && "pid" in row && "executable" in row)
      return i18n.t("shell:reasonProcess", { executable: row.executable, pid: String(row.pid) });
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
  const values = Object.fromEntries(Object.entries(reason.params).map(([key, value]) => [key, parameter(value, key, reason.code)]));
  const text = i18n.t(reason.code as keyof typeof zh, { ns: "reasons", keySeparator: false, ...values });
  const facts = Object.entries(values).filter(([key]) => !template.includes(`{{${key}}}`))
    .map(([key, value]) => i18n.t("shell:reasonFact", { name: i18n.t(`shell:reasonParam.${key}`), value }));
  return [text, ...facts].join(" ");
}
