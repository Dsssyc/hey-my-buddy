import type { TFunction } from "i18next";
import { parseReadOnlyStructured } from "../api";
import { parseHarnessAccount } from "../harness-account";
import { formatShortDateTime } from "../i18n/format";
import type { HarnessAccount } from "../harness-account";
import { quotaView } from "../host-workflow";
import type { QuotaWindowView } from "../host-workflow";
import zh from "../i18n/zh/buddies-harness";
import type { HarnessCandidate, HarnessHealth, QuotaRoutingRecord } from "../types";

type T = TFunction<"buddies">;

/** A recorded moment in the short form; null when it is not recorded or not a time. */
export function harnessTime(value: string | null | undefined): string | null {
  const ms = Date.parse(value ?? "");
  return Number.isNaN(ms) ? null : formatShortDateTime(ms);
}

/** A manual path is absolute; Windows drive paths count as absolute too. */
export function absolutePath(value: string): boolean {
  return value.startsWith("/") || /^[A-Za-z]:[\\/]/.test(value);
}

const STATUSES = ["ready", "missing", "login-required", "unhealthy", "unknown"] as const;

/** The state of a harness in words; one this console does not know yet stays as recorded. */
export function statusText(status: string, t: T): string {
  const key = STATUSES.find(name => name === status);
  return key ? t(`harness.status.${key}`) : status;
}

/** A discovery reason code in a short word; a code not known stays as recorded. */
function reasonText(code: string | undefined, t: T): string {
  const value = code?.trim() ?? "";
  return Object.hasOwn(zh.reason, value) ? t(`harness.reason.${value as keyof typeof zh.reason}`) : value;
}

/** How a tried location ended: its status and its reason, each word once. */
function candidateText(candidate: HarnessCandidate, t: T): string {
  const status = candidate.status?.trim() ?? "";
  const known = Object.hasOwn(zh.candidate, status);
  const words = [known ? t(`harness.candidate.${status as keyof typeof zh.candidate}`) : status, reasonText(candidate.reasonCode, t)];
  // "missing" and "not_found" describe the same fact; say it once.
  return [...new Set(words.filter(Boolean))].join(" · ");
}

/** The chosen executable: the recorded one, else the launched command's first word, else the manual path. */
export function harnessPath(row: HarnessHealth): string | null {
  return row.executable?.trim() || row.command?.[0]?.trim() || row.manualPath || null;
}

/** What a harness that is not found offers to do: the recorded remedy, else one for its state. */
function remedyText(row: HarnessHealth, t: T): string | null {
  const known = (["unknown", "missing", "login-required", "unhealthy"] as const).find(name => name === row.status);
  return row.remedy?.trim() || (known ? t(`harness.remedy.${known}`) : null);
}

/** The quota summary of a harness section: the window nearest its limit. */
export type QuotaCell = {
  /** The share used, 0–100; null while it is unknown, which never reads as 0. */
  used: number | null;
  text: string;
  /** Near or at its limit, or the native record reports a limit. */
  hot: boolean;
  stale: boolean;
  resets: string | null;
};

function accountStatusText(status: HarnessAccount["status"], t: T): string {
  return t(`harness.account.status.${status}`);
}

export function harnessSummary(row: HarnessHealth, t: T): QuotaCell | null {
  const view = quotaView(row.quota);
  if (!view) return null;
  // The window nearest its limit; while none has a known use the first stands, as unknown.
  const known = view.windows.filter(window => window.usedPercent !== null);
  const nearest: QuotaWindowView | undefined = known.reduce<QuotaWindowView | undefined>(
    (best, window) => !best || window.usedPercent! > best.usedPercent! ? window : best, undefined) ?? view.windows[0];
  const used = nearest?.usedPercent ?? null;
  return {
    used,
    text: used === null ? t("harness.quota.unknown") : nearest!.usedText,
    hot: !!nearest?.nearLimit || view.limitReported,
    stale: view.stale || nearest?.stale === true,
    resets: harnessTime(nearest?.resetsAt),
  };
}

/** One window of the quota block. */
export type WindowRow = {
  name: string;
  text: string;
  hot: boolean;
  resets: string;
  flag: "atLimit" | "nearLimit" | null;
};

/** One persisted exhaustion record, with where its recovery stands. */
export type ExhaustedRow = {
  key: string;
  provider: string;
  limitId: string | null;
  word: string;
  observation: string;
  recovery: string;
};

export type HarnessDetailsView = {
  location: {
    path: string | null;
    source: string;
    checked: string | null;
    review: { text: string; reason: string | null; note: string };
    /** Only for a harness that is not found. */
    exception: null | { reason: string | null; remedy: string | null; tried: { path: string | null; source: string | null; result: string }[] };
  };
  account: null | {
    state: string;
    billing: { provider: string; plan: string }[];
    guidance: string | null;
  };
  quota: null | {
    observed: string | null;
    stale: boolean;
    /** The board's own note that this is an observation and not a live reading. */
    note: string;
    source: string;
    limitReported: boolean;
    windows: WindowRow[];
  };
  exhausted: ExhaustedRow[];
  /** The providers of this harness for which a retry can be opened now. */
  retryProviders: string[];
};

/** One blocked record's recovery, phrased from the recorded facts only. */
export function recoveryText(record: QuotaRoutingRecord, t: T): string {
  const time = (value: string | null | undefined) => harnessTime(value) ?? t("harness.unrecorded");
  if (record.resetsAt) return t("harness.quota.recovery.reset", { time: time(record.resetsAt) });
  const retry = record.retry;
  if (!retry) return t("harness.quota.recovery.unknown");
  if (retry.open) return retry.consumedAt
    ? t("harness.quota.recovery.openUsed", { used: time(retry.consumedAt) })
    : t("harness.quota.recovery.open");
  return retry.consumedAt
    ? t("harness.quota.recovery.waitUsed", { used: time(retry.consumedAt), time: time(retry.eligibleAt) })
    : t("harness.quota.recovery.wait", { time: time(retry.eligibleAt) });
}

/** The providers of this harness that a retry could help right now: blocked, with no reset recorded. */
export function retryProviders(records: QuotaRoutingRecord[]): string[] {
  return [...new Set(records.filter(record => record.blocked && !record.resetsAt).map(record => record.provider))];
}

const PLANS = ["subscription", "metered"] as const;
const ACCOUNT_TYPES = ["chatgpt", "apiKey", "subscription", "metered"] as const;

export function harnessDetailsView(row: HarnessHealth, t: T): HarnessDetailsView {
  const eligibility = parseReadOnlyStructured(row.readOnlyStructured);
  const reviewNote = [t("harness.review.note"),
    eligibility ? t(eligibility.systemSandbox ? "harness.review.sandbox" : "harness.review.noSandbox") : ""].filter(Boolean).join(" ");
  const parsed = parseHarnessAccount(row.account, row.adapter);
  const quota = quotaView(row.quota);
  const records = row.quotaRouting ?? [];
  const found = row.status === "ready";
  return {
    location: {
      path: harnessPath(row),
      source: row.manualPath ? t("harness.pane.manual") : row.source?.trim() || t("harness.unrecorded"),
      checked: harnessTime(row.checkedAt),
      review: {
        text: t(!eligibility ? "harness.review.unknown" : eligibility.eligible ? "harness.review.eligible" : "harness.review.ineligible"),
        reason: eligibility?.reason ?? null,
        note: reviewNote,
      },
      exception: found ? null : {
        reason: reasonText(row.reasonCode, t) || null,
        remedy: remedyText(row, t),
        tried: (row.candidates ?? []).map(candidate => ({
          path: candidate.path?.trim() || null, source: candidate.source?.trim() || null, result: candidateText(candidate, t),
        })),
      },
    },
    account: parsed && {
      state: [accountStatusText(parsed.status, t),
        parsed.accountType ? t(`harness.account.type.${ACCOUNT_TYPES.find(name => name === parsed.accountType) ?? "unknown"}`) : "",
        harnessTime(parsed.checkedAt) ?? ""].filter(Boolean).join(" · "),
      billing: Object.entries(row.billingByProvider ?? {}).map(([provider, fact]) =>
        ({ provider, plan: t(`harness.account.plan.${PLANS.find(name => name === fact?.kind) ?? "unknown"}`) })),
      guidance: parsed.guidance && Object.hasOwn(zh.account.guidance, parsed.reasonCode ?? "")
        ? t(`harness.account.guidance.${parsed.reasonCode as keyof typeof zh.account.guidance}`) : parsed.guidance,
    },
    quota: quota && {
      observed: harnessTime(quota.observedAt),
      stale: quota.stale,
      note: [t(quota.stale ? "harness.quota.note.stale" : "harness.quota.note.recent"), quota.limitReported ? t("harness.quota.note.reported") : ""].filter(Boolean).join(" "),
      source: quota.provider ? `${quota.source} · ${quota.provider}` : quota.source,
      limitReported: quota.limitReported,
      windows: quota.windows.map(window => ({
        name: window.name,
        text: window.usedPercent === null ? t("harness.quota.unknown") : window.usedText,
        hot: window.nearLimit,
        resets: window.resetsAt ? t("harness.quota.reset", { time: harnessTime(window.resetsAt) ?? t("harness.unrecorded") }) : t("harness.quota.resetUnrecorded"),
        flag: window.atLimit ? "atLimit" : window.nearLimit ? "nearLimit" : null,
      })),
    },
    exhausted: records.filter(record => record.blocked).map(record => ({
      key: `${record.provider}:${record.limitId ?? ""}`,
      provider: record.provider,
      limitId: record.limitId,
      word: t(record.code === "HARNESS_BALANCE_ZERO" ? "harness.quota.balanceZero" : "harness.quota.exhausted"),
      observation: t("harness.quota.observation", { time: harnessTime(record.observedAt) ?? t("harness.unrecorded"), source: record.source }),
      recovery: recoveryText(record, t),
    })),
    retryProviders: retryProviders(records),
  };
}
