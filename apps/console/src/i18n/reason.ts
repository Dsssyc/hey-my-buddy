import i18n from "i18next";
import type zh from "./zh/reasons";
import type zhShell from "./zh/shell";
import { isReason } from "../reason";
import type { Reason } from "../reason";
import { formatCoolingTime, formatDateTime, formatList, formatNumber } from "./format";

type MaintenanceIssue = keyof typeof zhShell.maintenanceValidation;

const schemaIssues = new Map<string, MaintenanceIssue>([
  ["type", "wrongType"],
  ["enum", "invalidValue"],
  ["required", "requiredField"],
  ["additionalProperties", "unexpectedField"],
  ["minLength", "textTooShort"],
  ["maxLength", "textTooLong"],
  ["maxItems", "tooManyItems"],
]);

const entryIssues = new Map<string, MaintenanceIssue>([
  ["invalid Unicode", "invalidUnicode"],
  ["conflicts with staged entry", "stagedConflict"],
  ["duplicate id", "duplicateNode"],
  ["name and definition must be nonblank", "blankNode"],
  ["existing node cannot be changed", "changedNode"],
  ["domain node fields must not contain NUL characters", "nulNode"],
  ["parent is not active", "inactiveParent"],
  ["parent must be an active node", "inactiveParent"],
  ["parent cycle", "parentCycle"],
  ["domain parent cycle at", "parentCycle"],
  ["active domain name already exists", "duplicateName"],
  ["duplicate source", "duplicateMerge"],
  ["duplicate sourceId", "duplicateMerge"],
  ["merge source is not active", "inactiveMergeSource"],
  ["merge target must be an active node", "inactiveMergeTarget"],
  ["duplicate runId", "duplicateClassification"],
  ["domain node is not active", "inactiveClassificationNode"],
  ["unknown runId", "unknownRun"],
  ["missing classification", "missingClassification"],
  ["missing runId", "missingClassification"],
  ["read the complete task section", "unreadTaskSection"],
  ["duplicate kindId", "duplicateSummary"],
  ["unknown kindId", "unknownKind"],
  ["missing kindId", "missingSummary"],
  ["read all five sections of every unread micro task in this batch", "unreadSummarySections"],
  ["duplicate citation", "duplicateCitation"],
  ["citation is outside the frozen kind evidence", "citationOutsideEvidence"],
  ["cited micro task was deleted; rewrite summary", "deletedCitation"],
]);

function maintenanceValidationIssue(error: unknown): { key: MaintenanceIssue; target?: string } {
  if (error === "answer-invalid-json") return { key: "invalidJson" };
  if (error === "summary-citation-outside-batch") return { key: "citationOutsideBatch" };
  if (typeof error !== "string") return { key: "unknown" };

  const schema = error.match(/^((?:\$|answer)(?:[.\[].*)?): (\w+)$/s);
  if (schema) {
    const [, path, keyword] = schema;
    // Earlier answers reported out-of-batch citations as enum violations.
    const key = keyword === "enum" && path.startsWith("answer.") && path.includes(".citations")
      ? "citationOutsideBatch" : schemaIssues.get(keyword) ?? "unknown";
    return { key, target: path };
  }

  if (!/^(newNodes|merges|classifications|summaries)\[/.test(error)) return { key: "unknown" };
  let firstTarget: string | undefined;
  // An identifier can contain a closing bracket. Find the separator followed by a known issue.
  for (const separator of error.matchAll(/\]: /g)) {
    const target = error.slice(0, separator.index + 1);
    firstTarget ??= target;
    const issue = error.slice(separator.index + separator[0].length);
    const key = entryIssues.get(issue.split(": ", 1)[0])
      ?? (/^domain parent cycle at /s.test(issue) ? "parentCycle" : undefined)
      ?? (/^text exceeds \d+ UTF-8 bytes$/.test(issue) ? "summaryTooLong" : undefined);
    if (key) return { key, target };
  }
  return { key: "unknown", target: firstTarget };
}

function maintenanceValidationText(errors: unknown[]): string {
  const groups = new Map<MaintenanceIssue, Set<string>>();
  for (const error of errors) {
    const { key, target } = maintenanceValidationIssue(error);
    let targets = groups.get(key);
    if (!targets) groups.set(key, targets = new Set());
    if (target !== undefined) targets.add(target);
  }
  return formatList([...groups].map(([key, targets]) => i18n.t(
    `shell:maintenanceValidation.${key}.${targets.size > 1 ? "multiple" : "single"}`,
    { total: formatNumber(targets.size) },
  )));
}

export function reasonParameterText(value: unknown, key: string, code: string): string {
  if (isReason(value)) return reasonText(value);
  if (Array.isArray(value)) {
    if (key === "errors" && code === "maintenance-answer-invalid") return maintenanceValidationText(value);
    if (!value.length && (key === "processes" || key === "unreadableProcesses")) return i18n.t("shell:reasonNoProcesses");
    return formatList(value.map(item => reasonParameterText(item, key, code)));
  }
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "boolean") return i18n.t(`shell:reasonValue.${value ? "yes" : "no"}`);
  if (value === null) return i18n.t("shell:actor.unrecorded");
  if (typeof value === "object") {
    const row = value as Record<string, unknown>;
    if ((key === "processes" || key === "unreadableProcesses") && "pid" in row && "executable" in row)
      return i18n.t("shell:reasonProcess", { executable: row.executable, pid: String(row.pid) });
    if ("model" in row) {
      return [row.harness, row.provider, row.model, row.effort].join(" / ");
    }
    if ("runId" in row) return `${row.runId} (${i18n.t(`shell:reasonValue.state.${row.state}`)})`;
    return JSON.stringify(value);
  }
  if (key === "retryAt") return formatCoolingTime(Date.parse(String(value)));
  if (key === "catalogReadAt") return formatDateTime(Date.parse(String(value)));
  if (key === "operation" || key === "bound" || key === "disposition" || key === "violation") return i18n.t(`shell:reasonValue.${key}.${value}`);
  return String(value);
}

/** The single formatter for every program reason, evaluated in the active language. */
export function reasonText(reason: Reason): string {
  const template = i18n.getResource(i18n.resolvedLanguage!, "reasons", reason.code, { keySeparator: false }) as string;
  const values = Object.fromEntries(Object.entries(reason.params).map(([key, value]) => [key, reasonParameterText(value, key, reason.code)]));
  const text = i18n.t(reason.code as keyof typeof zh, { ns: "reasons", keySeparator: false, ...values });
  const facts = Object.entries(values).filter(([key]) => !template.includes(`{{${key}}}`))
    .map(([key, value]) => i18n.t("shell:reasonFact", { name: i18n.t(`shell:reasonParam.${key}`), value }));
  return [text, ...facts].join(" ");
}
