/**
 * ADR-018 Host-workflow facts for the read-only console: per-execution token
 * usage, recorded harness quota observations, the independent Host conclusion
 * of a failed or cancelled goal, partial outputs and cumulative patches.
 *
 * Every helper is total: a missing, malformed or unknown value resolves to an
 * explicit unknown — never to 0, an empty success or a fabricated acceptance.
 */
import type { HarnessQuota, QuotaWindow, TokenUsage } from "./types";
import type { CumulativePatch, HostConclusion, IntegrationRecord } from "./workflow-types";

/** The recorded near-limit threshold: a window at or above this warns. */
export const QUOTA_NEAR_LIMIT_PERCENT = 90;

function nonempty(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function countOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

/** Recorded strings only; malformed entries are dropped instead of invented. */
export function stringList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  const seen = new Set<string>();
  const result: string[] = [];
  for (const item of value) {
    const text = nonempty(item);
    if (text && !seen.has(text)) { seen.add(text); result.push(text); }
  }
  return result;
}

/** Decimal grouping without a locale dependency, so tests and bundles agree. */
export function formatCount(value: number): string {
  if (!Number.isFinite(value)) return "未知";
  const rounded = Math.trunc(value);
  return String(rounded).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/**
 * One execution's recorded native usage. A malformed or partially malformed
 * object is refused as unknown rather than displayed with fabricated numbers;
 * `null` always means "未记录", never zero tokens.
 */
export function parseTokenUsage(value: unknown): TokenUsage | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  if (row.scope !== "attempt") return null;
  const source = nonempty(row.source);
  if (!source) return null;
  const numbers: Record<"inputTokens" | "cachedInputTokens" | "outputTokens", number | null> = {
    inputTokens: null, cachedInputTokens: null, outputTokens: null,
  };
  for (const key of ["inputTokens", "cachedInputTokens", "outputTokens"] as const) {
    const raw = row[key];
    if (raw === null || raw === undefined) continue;
    const parsed = countOrNull(raw);
    if (parsed === null) return null;
    numbers[key] = parsed;
  }
  if (numbers.cachedInputTokens !== null && numbers.inputTokens !== null
    && numbers.cachedInputTokens > numbers.inputTokens) {
    // The cached input is a subset of the input total; a larger value is not a
    // usage this console may present as fact.
    return null;
  }
  return { ...numbers, source, scope: "attempt" };
}

export type TokenUsageView = {
  /** True only when at least one native count was recorded. */
  recorded: boolean;
  inputText: string;
  cachedText: string;
  outputText: string;
  text: string;
  title: string;
};

/**
 * One attempt's usage line. `inputTokens` already contains the cached tokens,
 * so the cache is shown as a parenthetical subset and never added in.
 */
export function tokenUsageView(usage: TokenUsage | null | undefined): TokenUsageView {
  if (!usage) {
    return {
      recorded: false, inputText: "未知", cachedText: "未知", outputText: "未知",
      text: "未记录（未知）",
      title: "服务没有记录这次执行的用量；未知不等于 0。",
    };
  }
  const input = usage.inputTokens === null ? "未知" : formatCount(usage.inputTokens);
  const cached = usage.cachedInputTokens === null ? "未知" : formatCount(usage.cachedInputTokens);
  const output = usage.outputTokens === null ? "未知" : formatCount(usage.outputTokens);
  const inputText = usage.cachedInputTokens === null ? `输入 ${input}` : `输入 ${input}（含缓存 ${cached}）`;
  const outputText = `输出 ${output}`;
  const recorded = usage.inputTokens !== null || usage.cachedInputTokens !== null || usage.outputTokens !== null;
  return {
    recorded,
    inputText,
    cachedText: cached,
    outputText,
    text: recorded ? `${inputText} · ${outputText}` : "未记录（未知）",
    title: `来源 ${usage.source} · 范围 单次执行（不是会话累计；输入已含缓存，不重复相加）`,
  };
}

/**
 * The newest recorded quota observation of one harness. Windows and the whole
 * observation are validated: a malformed value reads as no observation rather
 * than as an available account, and an unknown window never reads as 0%.
 */
export function parseQuota(value: unknown): HarnessQuota | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Record<string, unknown>;
  const observedAt = nonempty(row.observedAt);
  const source = nonempty(row.source);
  if (!observedAt || !source) return null;
  if (!Array.isArray(row.windows)) return null;
  const windows: QuotaWindow[] = [];
  for (const item of row.windows) {
    if (!item || typeof item !== "object" || Array.isArray(item)) return null;
    const entry = item as Record<string, unknown>;
    const name = nonempty(entry.name);
    if (!name) return null;
    let usedPercent: number | null = null;
    if (entry.usedPercent !== null && entry.usedPercent !== undefined) {
      usedPercent = countOrNull(entry.usedPercent);
      if (usedPercent === null) return null;
    }
    let resetsAt: string | null = null;
    if (entry.resetsAt !== null && entry.resetsAt !== undefined) {
      resetsAt = nonempty(entry.resetsAt);
      if (resetsAt === null) return null;
    }
    windows.push({ name, usedPercent, resetsAt });
  }
  const provider = nonempty(row.provider);
  return {
    observedAt,
    source,
    ...(provider ? { provider } : {}),
    stale: row.stale === true,
    windows,
  };
}

export type QuotaWindowView = QuotaWindow & {
  /** 0–100 text, or 未知; unknown stays unknown instead of reading as 0. */
  usedText: string;
  /** True at or above the near-limit threshold; an unknown window is never near. */
  nearLimit: boolean;
  atLimit: boolean;
};

export type QuotaView = {
  stale: boolean;
  /** True when some recorded window is at or above the near-limit threshold. */
  alert: boolean;
  source: string;
  provider?: string;
  observedAt: string;
  windows: QuotaWindowView[];
  /** Always says this is an observation, never a live account reading. */
  note: string;
};

/** Recorded quota windows with their display facts; `null` when nothing was recorded. */
export function quotaView(quota: HarnessQuota | null | undefined): QuotaView | null {
  if (!quota) return null;
  const windows = quota.windows.map(window => {
    const nearLimit = window.usedPercent !== null && window.usedPercent >= QUOTA_NEAR_LIMIT_PERCENT;
    return {
      ...window,
      usedText: window.usedPercent === null ? "未知" : `${formatPercent(window.usedPercent)}%`,
      nearLimit,
      atLimit: window.usedPercent !== null && window.usedPercent >= 100,
    };
  });
  const alert = windows.some(window => window.nearLimit);
  const note = quota.stale
    ? "这是最近一次记录的额度观测，可能已经过期；不代表当前或实时的账户额度。"
    : "这是最近一次记录的额度观测；不代表实时账户额度。";
  return {
    stale: quota.stale === true,
    alert,
    source: quota.source,
    ...(quota.provider ? { provider: quota.provider } : {}),
    observedAt: quota.observedAt,
    windows,
    note,
  };
}

function formatPercent(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Math.round(value * 10) / 10);
}

export type HostConclusionView = {
  conclusionId: string;
  /** The recorded target outcome: 失败 / 已取消 or the raw recorded status. */
  statusLabel: string;
  failed: boolean;
  cancelled: boolean;
  note: string;
  evidence: string[];
  actor: string;
  createdAt: string;
  /** Attempt / artifact / integration references that exist, labelled for display. */
  references: { label: string; value: string }[];
  ownerGeneration: number | null;
  runRevision: number | null;
};

/**
 * The recorded Host conclusion of a failed or cancelled goal. It is evidence
 * about the remains, never an acceptance: the goal keeps its recorded outcome.
 */
export function hostConclusionView(value: unknown): HostConclusionView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Partial<HostConclusion> & Record<string, unknown>;
  const conclusionId = nonempty(row.conclusionId);
  if (!conclusionId) return null;
  const status = nonempty(row.executionStatus) ?? "";
  const references: { label: string; value: string }[] = [];
  const attemptId = nonempty(row.attemptId);
  const artifactId = nonempty(row.artifactId);
  const integrationId = nonempty(row.integrationId);
  if (attemptId) references.push({ label: "被审查执行", value: attemptId });
  if (artifactId) references.push({ label: "关联成果", value: artifactId });
  if (integrationId) references.push({ label: "整合记录", value: integrationId });
  return {
    conclusionId,
    statusLabel: status === "failed" ? "失败" : status === "cancelled" ? "已取消" : status || "未记录",
    failed: status === "failed",
    cancelled: status === "cancelled",
    note: nonempty(row.note) ?? "未记录",
    evidence: stringList(row.evidence),
    actor: nonempty(row.actor) ?? "未记录",
    createdAt: nonempty(row.createdAt) ?? "",
    references,
    ownerGeneration: typeof row.ownerGeneration === "number" && Number.isInteger(row.ownerGeneration)
      ? row.ownerGeneration : null,
    runRevision: typeof row.runRevision === "number" && Number.isInteger(row.runRevision)
      ? row.runRevision : null,
  };
}

/** Sealed output kinds that are a partial, unverified intermediate result. */
export function isPartialOutput(kind: string): boolean {
  return kind === "partial-output";
}

export function artifactKindLabel(kind: string): string {
  if (kind === "partial-output") return "部分成果（未验证、非最终）";
  if (kind === "resolved-output") return "经 Host 处理并重新封存的产物";
  if (kind === "output") return "执行输出";
  return kind;
}

export type ArtifactStateView = {
  /** Explicit partial 标记, never inferred from the kind alone. */
  partial: boolean;
  verified: boolean;
  final: boolean;
  text: string;
};

/**
 * The recorded artifact flags. A missing flag is unknown, not an implicit
 * true: only the recorded values are named here.
 */
export function artifactStateView(artifact: {
  kind: string; partial?: boolean; verified?: boolean; final?: boolean;
}): ArtifactStateView {
  const partial = artifact.partial === true || isPartialOutput(artifact.kind);
  const verified = artifact.verified === true;
  const final = artifact.final === true;
  const flags = [
    partial ? "部分" : "",
    verified ? "已验证" : "",
    final ? "最终" : "",
  ].filter(Boolean);
  return {
    partial, verified, final,
    text: partial
      ? "部分、未验证、非最终（可查看、整合或用于续做，不能当作已验收成果）"
      : flags.length ? flags.join(" · ") : "未记录标记",
  };
}

export type CumulativePatchView = {
  baseCommit: string;
  outputCommit: string;
  path: string;
  sha256: string;
  changedPaths: string[];
  /** True when every fixed field of the reference was recorded. */
  complete: boolean;
};

/** The recorded cumulative patch reference; `null` when none was recorded. */
export function cumulativePatchView(value: unknown): CumulativePatchView | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const row = value as Partial<CumulativePatch>;
  const baseCommit = nonempty(row.baseCommit);
  const outputCommit = nonempty(row.outputCommit);
  const path = nonempty(row.path);
  const sha256 = nonempty(row.sha256);
  if (!baseCommit && !outputCommit && !path && !sha256) return null;
  return {
    baseCommit: baseCommit ?? "未记录",
    outputCommit: outputCommit ?? "未记录",
    path: path ?? "未记录",
    sha256: sha256 ?? "未记录",
    changedPaths: stringList(row.changedPaths),
    complete: !!(baseCommit && outputCommit && path && sha256),
  };
}

/**
 * The Host's own supplementary paths from one integration record, kept apart
 * from the artifact's changed paths (ADR-018 §19).
 */
export function integrationHostPaths(value: IntegrationRecord | null | undefined): string[] {
  return stringList(value?.verification?.hostPaths);
}

/**
 * ADR-018 §18: whether the delegation's explicit configuration is a user
 * hard constraint. Absent is unknown, not a default.
 */
export function configurationLockText(locked: boolean | null | undefined): string | null {
  if (locked === true) return "用户要求锁定，续做时不能更换";
  if (locked === false) return "Host 指定，续做时可由 Host 附理由更换";
  return null;
}
