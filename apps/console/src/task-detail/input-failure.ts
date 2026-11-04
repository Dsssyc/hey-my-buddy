import i18n from "i18next";
import { ApiError } from "../api";

export const INPUT_PARTS = ["role", "task", "hostInput", "context", "delivery", "format"] as const;
export type InputParts = Record<(typeof INPUT_PARTS)[number], number>;
export type InputUnit = "unicode-scalars" | "utf-8-bytes";
type Declaration = { form: string; scope: string; source: string };
export type InputFailure = {
  version: 1;
  stage: "preflight" | "native-admission";
  harness: { name: "codex" | "claude" | "zcode" | "dsh"; form: string; version: string | null };
  limit: Declaration & ({ state: "known"; value: number; unit: InputUnit; versions: string[] } | { state: "unknown"; note: string });
  measurement: { unit: InputUnit; total: number; parts: InputParts; utf8Bytes?: number; utf8Parts?: InputParts };
  nativeRequestSent: boolean | null;
  modelStarted: boolean | null;
  nativeMethod?: string;
  nativeCode?: string | number | null;
  input_error_code?: string;
  max_chars?: number;
  actual_chars?: number;
  message?: string;
};

/** The result boundary's v1 shape, including the six partitions and their sums. */
export function parseInputFailure(value: unknown, what: string): InputFailure | undefined {
  if (value === null || value === undefined) return undefined;
  const fail = (): never => { throw new ApiError("INVALID_RESPONSE", () => i18n.t("error.incomplete", { ns: "taskDetail", what })); };
  const record = (item: unknown): Record<string, unknown> => {
    if (!item || typeof item !== "object" || Array.isArray(item)) fail();
    return item as Record<string, unknown>;
  };
  const keys = (item: Record<string, unknown>, required: readonly string[], optional: readonly string[] = []) => {
    if (required.some(key => !Object.hasOwn(item, key)) || Object.keys(item).some(key => !required.includes(key) && !optional.includes(key))) fail();
  };
  const text = (item: unknown, maximum: number): boolean => typeof item === "string" && item.length > 0 && new TextEncoder().encode(item).length <= maximum;
  const count = (item: unknown): item is number => Number.isSafeInteger(item) && (item as number) >= 0;
  const unit = (item: unknown) => item === "unicode-scalars" || item === "utf-8-bytes";
  const parts = (item: unknown, total: unknown) => {
    const sizes = record(item);
    keys(sizes, INPUT_PARTS);
    if (!count(total) || !Object.values(sizes).every(count) || Object.values(sizes).reduce<number>((sum, size) => sum + (size as number), 0) !== total) fail();
  };
  const failure = record(value);
  const native = ["nativeMethod", "nativeCode", "input_error_code", "max_chars", "actual_chars", "message"];
  keys(failure, ["version", "stage", "harness", "limit", "measurement", "nativeRequestSent", "modelStarted"], native);
  if (failure.version !== 1 || !["preflight", "native-admission"].includes(failure.stage as string)) fail();
  const harness = record(failure.harness), limit = record(failure.limit), measured = record(failure.measurement);
  keys(harness, ["name", "form", "version"]);
  if (!["codex", "claude", "zcode", "dsh"].includes(harness.name as string) || !text(harness.form, 80)
    || !(harness.version === null || typeof harness.version === "string" && [...harness.version].length <= 80)) fail();
  const known = limit.state === "known";
  keys(limit, ["state", "form", "scope", "source", ...(known ? ["value", "unit", "versions"] : ["note"])]);
  if (limit.form !== harness.form || ["form", "scope", "source"].some(key => !text(limit[key], 256))) fail();
  if (known) {
    if (!count(limit.value) || limit.value === 0 || !unit(limit.unit) || !Array.isArray(limit.versions)
      || limit.versions.length < 1 || limit.versions.length > 16 || !limit.versions.every(item => text(item, 320))) fail();
  } else if (limit.state !== "unknown" || !text(limit.note, 1024)) fail();
  keys(measured, ["unit", "total", "parts", ...(known ? [] : ["utf8Bytes", "utf8Parts"])]);
  if (measured.unit !== (known ? limit.unit : "unicode-scalars")) fail();
  parts(measured.parts, measured.total);
  if (!known) parts(measured.utf8Parts, measured.utf8Bytes);
  for (const key of ["nativeRequestSent", "modelStarted"]) if (failure[key] !== null && typeof failure[key] !== "boolean") fail();
  if (failure.stage === "preflight" && (!known || (measured.total as number) <= (limit.value as number)
    || failure.nativeRequestSent !== false || failure.modelStarted !== false || native.some(key => Object.hasOwn(failure, key)))) fail();
  for (const key of ["nativeMethod", "input_error_code"]) if (Object.hasOwn(failure, key) && !text(failure[key], 128)) fail();
  if (Object.hasOwn(failure, "nativeCode") && !(failure.nativeCode === null || Number.isSafeInteger(failure.nativeCode) || text(failure.nativeCode, 128))) fail();
  for (const key of ["max_chars", "actual_chars"]) if (Object.hasOwn(failure, key) && !count(failure[key])) fail();
  if (Object.hasOwn(failure, "message") && !(typeof failure.message === "string" && new TextEncoder().encode(failure.message).length <= 512)) fail();
  return value as InputFailure;
}
