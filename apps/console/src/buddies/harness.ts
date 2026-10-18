import type { TFunction } from "i18next";
import { parseReadOnlyStructured } from "../api";
import { parseHarnessAccount } from "../harness-account";
import { formatShortDateTime } from "../i18n/format";
import type { HarnessAccount } from "../harness-account";
import { reasonText } from "../i18n/reason";
import type { HarnessCandidate, HarnessHealth } from "../types";

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

/** A tried location's own reason, or its recorded status when no reason is needed. */
function candidateText(candidate: HarnessCandidate, t: T): string {
  return candidate.reason ? reasonText(candidate.reason) : candidate.status ? statusText(candidate.status, t) : "";
}

/** The chosen executable: the recorded one, else the launched command's first word, else the manual path. */
export function harnessPath(row: HarnessHealth): string | null {
  return row.executable?.trim() || row.command?.[0]?.trim() || row.manualPath || null;
}

function accountStatusText(status: HarnessAccount["status"], t: T): string {
  return t(`harness.account.status.${status}`);
}

export type HarnessDetailsView = {
  location: {
    path: string | null;
    source: string;
    checked: string | null;
    review: { text: string; reason: string | null; note: string };
    /** Only for a harness that is not found. */
    exception: null | { reason: string | null; tried: { path: string | null; source: string | null; result: string }[] };
  };
  account: null | {
    state: string;
    billing: { provider: string; plan: string }[];
    reason: string | null;
  };
};

const PLANS = ["subscription", "metered"] as const;
const ACCOUNT_TYPES = ["chatgpt", "apiKey", "subscription", "metered"] as const;

export function harnessDetailsView(row: HarnessHealth, t: T): HarnessDetailsView {
  const eligibility = parseReadOnlyStructured(row.readOnlyStructured);
  const reviewNote = [t("harness.review.note"),
    eligibility ? t(eligibility.systemSandbox ? "harness.review.sandbox" : "harness.review.noSandbox") : ""].filter(Boolean).join(" ");
  const parsed = parseHarnessAccount(row.account, row.harness);
  const found = row.status === "ready";
  return {
    location: {
      path: harnessPath(row),
      source: row.manualPath ? t("harness.pane.manual") : row.source?.trim() || t("harness.unrecorded"),
      checked: harnessTime(row.checkedAt),
      review: {
        text: t(!eligibility ? "harness.review.unknown" : eligibility.eligible ? "harness.review.eligible" : "harness.review.ineligible"),
        reason: eligibility?.reason ? reasonText(eligibility.reason) : null,
        note: reviewNote,
      },
      exception: found ? null : {
        reason: row.reason ? reasonText(row.reason) : null,
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
      reason: parsed.reason ? reasonText(parsed.reason) : null,
    },
  };
}
