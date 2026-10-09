import type { RuntimeVersionInfo, VersionFacts, BackupPreflight, Configuration, ConsoleAccess, ConsoleSession, RoutingHealth, Snapshot, TaskPage, TaskQuery } from "./types";
import type {
  ObjectiveFilter, ObjectivePage, ObjectiveQuery, ObjectiveSummary, ObjectiveTimeline, TimelineRow,
} from "./objective-types";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const messages: Record<string, string> = {
  REVISION_CONFLICT: "记录已更新；请刷新核对后重试。",
  CONFLICT: "记录冲突；请刷新核对后重试。",
  FORBIDDEN: "无操作权限；请重新打开控制台。",
  WRITER_EXPIRED: "编辑权限已过期；请重新取得权限，草稿已保留。",
  WRITER_NOT_ACTIVE: "编辑权限已失效；请重新取得权限，草稿已保留。",
  STALE_GENERATION: "操作资格已失效；请刷新核对负责人。",
  SHUTDOWN_UNCONFIRMED: "停止未确认；请稍后核对再重试。",
  STORAGE_INCOMPLETE: "清理未完成；请重试同一请求。",
  PLAN_EXPIRED: "计划已过期；请重新检查。",
  CONSOLE_SESSION_EXPIRED: "登录已失效；请运行 buddy console 重新登录。",
  CONSOLE_ENTRY_EXPIRED: "入口已过期；请重新打开控制台。",
};

/**
 * The one identity-loss refusal the board still issues: an HTTP 401
 * `CONSOLE_SESSION_EXPIRED`. A callback receiving it must not retry, renew or
 * release anything: only the bounded lease expires by itself, and a fresh
 * `buddy console` login is the explicit way to restore authority.
 */
export const SESSION_EXPIRED_CODE = "CONSOLE_SESSION_EXPIRED";

export function isSessionExpiredRefusal(error: unknown): boolean {
  return error instanceof ApiError && error.code === SESSION_EXPIRED_CODE;
}

/**
 * Strict parse of the authenticated snapshot's session descriptor. The live
 * service issues exactly `{id, canWrite: true, reason: null}` for a valid
 * login; anything else — a missing descriptor, a non-string id, a non-true
 * `canWrite` or any non-null reason — is malformed and never grants write
 * access. An expired or absent login is reported by the HTTP layer as a 401
 * `CONSOLE_SESSION_EXPIRED`, not by a writable-but-flagged descriptor.
 */
export function parseConsoleSession(value: unknown): ConsoleSession {
  const session = value as Partial<ConsoleSession> | null | undefined;
  const valid = !!session && typeof session === "object" && !Array.isArray(session)
    && typeof session.id === "string" && session.id.trim().length > 0
    && session.canWrite === true
    && session.reason === null;
  if (!valid) {
    throw new ApiError(
      "INVALID_RESPONSE",
      "会话信息无法识别；请检查服务版本或重新登录。",
    );
  }
  return session as ConsoleSession;
}

export function parseConsoleAccess(value: unknown): ConsoleAccess {
  const access = value as ConsoleAccess | null;
  if (!access || typeof access.requireLogin !== "boolean" || !Number.isSafeInteger(access.revision)
    || access.revision < 0 || !Array.isArray(access.sessions) || access.sessions.length > 64
    || access.sessions.some(session => !session || typeof session.id !== "string" || !/^[a-f0-9]{24}$/.test(session.id)
      || !Number.isFinite(session.lastSeen) || typeof session.current !== "boolean")) {
    throw new ApiError("INVALID_RESPONSE", "控制台访问设置无法识别，请刷新后重试。");
  }
  return access;
}


/**
 * Current L4 list settings: the ordered Router list, its retry interval, mode
 * and review budget. Every retired slot shape — the schema-13 decision profile
 * and the single/dual Router positions — is refused; no production alias exists.
 */
const ROUTER_RETRY_INTERVAL_MAX = 2147483647;

function validRouterProfileIds(value: unknown): value is string[] {
  return Array.isArray(value) && value.every(id => typeof id === "string" && id.trim().length > 0)
    && new Set(value).size === value.length;
}

export function validRoutingConfiguration(value: unknown): value is Configuration {
  const config = value as Partial<Configuration> | null;
  if (!config || typeof config !== "object" || Array.isArray(config)) return false;
  const interval = config.routerRetryIntervalSeconds;
  return !["decisionProfileId", "routerProfileId", "fastRouterProfileId", "reviewRouterProfileId"].some(key => key in config)
    && Number.isInteger(config.revision)
    && validRouterProfileIds(config.routerProfileIds)
    && typeof interval === "number" && Number.isInteger(interval) && interval >= 1 && interval <= ROUTER_RETRY_INTERVAL_MAX
    && (config.defaultRoutingMode === "fast" || config.defaultRoutingMode === "review")
    && (config.routingBudget === "brief" || config.routingBudget === "standard" || config.routingBudget === "deep");
}

function validConfigurationState(data: Snapshot): boolean {
  const error = data.configurationError;
  if (data.configuration !== null) return validRoutingConfiguration(data.configuration) && error == null;
  return !!error && typeof error === "object" && !Array.isArray(error)
    && error.code === "router-settings-upgrade-required"
    && typeof error.message === "string" && error.message.trim().length > 0
    && Number.isSafeInteger(error.revision) && error.revision >= 0;
}

/** One per-buddy health entry; a malformed field refuses the whole entry. */
function validRouterHealthEntry(entry: unknown): entry is NonNullable<RoutingHealth["routers"]>[number] {
  const item = entry as NonNullable<RoutingHealth["routers"]>[number] | null;
  if (!item || typeof item !== "object" || Array.isArray(item)) return false;
  if (typeof item.profileId !== "string" || !item.profileId.trim()) return false;
  if (!Number.isSafeInteger(item.index) || item.index < 0) return false;
  if (typeof item.eligible !== "boolean") return false;
  for (const key of ["code", "skipUntil", "retryAt", "lastAnsweredAt", "lastNoAnswerAt"] as const) {
    if (!(item[key] === undefined || item[key] === null || typeof item[key] === "string")) return false;
  }
  for (const key of ["inSkipWindow", "retryInProgress"] as const) {
    if (!(item[key] === undefined || typeof item[key] === "boolean")) return false;
  }
  for (const key of ["consecutiveNoAnswers", "answeredCount", "noAnswerCount", "windowSize", "windowEntries",
    "windowAnsweredCount", "windowFailureCount", "windowBudgetExhaustedCount", "windowBoundsRejectedCount",
    "windowAttemptCount"] as const) {
    if (!(item[key] === undefined || (Number.isSafeInteger(item[key]) && item[key]! >= 0))) return false;
  }
  if (!(item.identity === undefined || item.identity === null
    || (!!item.identity && typeof item.identity === "object" && !Array.isArray(item.identity)
      && ["adapter", "provider", "model", "effort"].every(key => typeof (item.identity as Record<string, unknown>)[key] === "string")))) return false;
  const lastError = item.lastError;
  if (!(lastError === undefined || lastError === null
    || (!!lastError && typeof lastError === "object"
      && typeof lastError.at === "string"
      && (lastError.code === null || typeof lastError.code === "string")
      && (lastError.phase === undefined || lastError.phase === null || typeof lastError.phase === "string")))) return false;
  return true;
}

/** Optional health observations are never inferred from a failure count. */
export function parseRoutingHealth(value: unknown): RoutingHealth | undefined {
  const health = value as RoutingHealth | null;
  if (!health || typeof health !== "object" || Array.isArray(health)) return undefined;
  const counts = [health.windowSize, health.sampleCount, health.failureCount, health.consecutiveFailures,
    health.abstentionCount, health.cancelledCount, health.staleCount];
  if (counts.some(count => !Number.isSafeInteger(count) || count < 0)
    || [health.budgetExhaustedCount, health.boundsRejectedCount, health.inputChangedCount, health.attemptCount,
      health.unattributedCount]
      .some(count => count !== undefined && (!Number.isSafeInteger(count) || count < 0))
    || (health.available !== undefined && typeof health.available !== "boolean")
    || (health.reasonCode !== undefined && health.reasonCode !== null && typeof health.reasonCode !== "string")
    || (health.currentRouterProfileId !== undefined && health.currentRouterProfileId !== null
      && typeof health.currentRouterProfileId !== "string")
    || (health.routers !== undefined
      && (!Array.isArray(health.routers) || !health.routers.every(validRouterHealthEntry)))
    || !(health.lastSuccessAt === null || typeof health.lastSuccessAt === "string")
    || !(health.lastSuccessDecisionId === null || typeof health.lastSuccessDecisionId === "string")
    || !Array.isArray(health.recentFailures)
    || health.recentFailures.some(failure => !failure || typeof failure.decisionId !== "string"
      || !(failure.runId === null || typeof failure.runId === "string")
      || typeof failure.at !== "string" || typeof failure.code !== "string"))
    return undefined;
  if (health.unattributed !== undefined
    && (!Array.isArray(health.unattributed) || health.unattributed.some(entry => !entry
      || typeof entry.decisionId !== "string" || typeof entry.at !== "string"
      || typeof entry.outcome !== "string"
      || !(entry.code === null || typeof entry.code === "string")))) return undefined;
  return health;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return messages[error.code] || error.message;
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

export function uncertainResponse(error: unknown): boolean {
  return !(error instanceof ApiError) || ["NETWORK", "INVALID_RESPONSE", "INTERNAL_ERROR"].includes(error.code) || /^HTTP_5/.test(error.code);
}

export function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}

/** Server verification of the displayed read; missing evidence stays explicit. */
export function readVerificationText(verifiedAtMs: number | null | undefined): string {
  return typeof verifiedAtMs === "number" && Number.isFinite(verifiedAtMs)
    ? `核对时间 ${new Date(verifiedAtMs).toLocaleString("zh-CN", { hour12: false })}`
    : "核对时间未记录";
}

/* ---- 0.16.0 storage wire shapes (docs/reference/operations.md) ---- */

export type StorageCategory = {
  id: string;
  label: string;
  bytes: number;
  reclaimableBytes: number;
  count: number;
  eligibleCount: number;
  reasons: string[];
};
export type StorageCandidate = {
  id: string;
  category: string;
  path: string;
  bytes: number;
  eligible: boolean;
  reasons: string[];
};
export type StorageOrphanProcess = {
  pid: number;
  kind: string;
  stateDir: string | null;
  runtimeDir: string | null;
};
export type StoragePlan = {
  planId: string;
  createdAt: string;
  expiresAt: string;
  categories: StorageCategory[];
  candidates: StorageCandidate[];
  orphanProcesses: StorageOrphanProcess[];
};
export type StorageApplyResult = {
  planId: string;
  removedBytes: number;
  /** Removed candidates; a record list, never a count. */
  removed: { id: string; path: string; bytes: number }[];
  /** Skipped candidates with their per-item reason codes. */
  skipped: { id: string; path: string; reasons: string[] }[];
  /** True only when the whole plan finished; anything else is unresolved. */
  complete: boolean;
};

/** Strict shape checks for the storage plan reply; anything malformed is refused. */
function validStoragePlan(value: unknown): value is StoragePlan {
  const plan = value as StoragePlan | null;
  if (!plan || typeof plan !== "object") return false;
  if (typeof plan.planId !== "string" || !plan.planId) return false;
  if (typeof plan.createdAt !== "string" || typeof plan.expiresAt !== "string") return false;
  if (!Array.isArray(plan.categories) || !Array.isArray(plan.candidates) || !Array.isArray(plan.orphanProcesses)) return false;
  return plan.categories.every(category =>
    !!category && typeof category === "object" && typeof category.id === "string"
    && Number.isFinite(category.bytes) && Number.isFinite(category.reclaimableBytes)
    && Number.isFinite(category.count) && Number.isFinite(category.eligibleCount)
    && Array.isArray(category.reasons));
}

export function parseStoragePlan(value: unknown): StoragePlan {
  if (!validStoragePlan(value)) {
    throw new ApiError("INVALID_RESPONSE", "存储计划响应不完整，请检查服务版本。");
  }
  return value as StoragePlan;
}

export function validStorageApplyResult(value: unknown): value is StorageApplyResult {
  const result = value as StorageApplyResult | null;
  return !!result && typeof result === "object" && typeof result.planId === "string"
    && Number.isFinite(result.removedBytes)
    && Array.isArray(result.removed) && result.removed.every(row =>
      !!row && typeof row === "object" && typeof row.id === "string"
      && typeof row.path === "string" && Number.isFinite(row.bytes))
    && Array.isArray(result.skipped) && result.skipped.every(row =>
      !!row && typeof row === "object" && typeof row.id === "string"
      && typeof row.path === "string" && Array.isArray(row.reasons));
}

/**
 * Strict shape checks for the 0.15.1 objective read projections: `description`
 * and `taskSummary` are nullable strings and `counts.accepted` is a number, so
 * a malformed field refuses the read instead of reaching presentation.
 */
function validObjectiveSummary(value: unknown): boolean {
  const summary = value as ObjectiveSummary | null;
  return !!summary && typeof summary === "object"
    && (summary.description === null || typeof summary.description === "string")
    && !!summary.counts && Number.isFinite(summary.counts.accepted);
}

function validTimelineRows(rows: unknown): boolean {
  return Array.isArray(rows) && rows.every(row => {
    const entry = row as TimelineRow | null;
    return !!entry && (entry.taskSummary === null || typeof entry.taskSummary === "string");
  });
}

/* ---- harness health wire shapes (ADR-017 §15) ---- */

export type { HarnessStatus, HarnessCandidate, HarnessHealth } from "./types";
import type { HarnessHealth, QuotaRetryWindow, QuotaRoutingRecord } from "./types";
import { parseQuota } from "./host-workflow";

/** The supported harnesses, in the Buddy 配置 page's fixed display order. */
export const HARNESS_ADAPTERS = ["dsh", "zcode", "codex", "claude"] as const;

/** Strict parse of one retry window (ADR-019): every fact is named or the value is null. */
function parseQuotaRetry(value: unknown): QuotaRetryWindow | null {
  const facts = value as QuotaRetryWindow | null;
  if (!facts || typeof facts !== "object") return null;
  if (typeof facts.eligibleAt !== "string" || typeof facts.open !== "boolean") return null;
  if (typeof facts.pendingManual !== "boolean") return null;
  for (const key of ["manualAt", "consumedAt", "consumedBy"] as const) {
    if (!(facts[key] === null || typeof facts[key] === "string")) return null;
  }
  return facts;
}

/**
 * Strict parse of the harness's persisted exhaustion records (ADR-019). A
 * malformed list or entry is dropped as unknown instead of being presented as
 * recovery state; the records never claim a live balance.
 */
function parseQuotaRouting(value: unknown): QuotaRoutingRecord[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const records = value.map(entry => {
    const record = entry as QuotaRoutingRecord | null;
    if (!record || typeof record !== "object") return null;
    if (typeof record.provider !== "string" || !record.provider.trim()) return null;
    for (const key of ["code", "source", "observedAt"] as const) {
      if (typeof record[key] !== "string") return null;
    }
    if (!(record.limitId === null || typeof record.limitId === "string")) return null;
    if (!(record.resetsAt === null || typeof record.resetsAt === "string")) return null;
    if (typeof record.blocked !== "boolean") return null;
    if (!("retry" in record) || record.retry === null) return { ...record, retry: null };
    const retry = parseQuotaRetry(record.retry);
    return retry ? { ...record, retry } : null;
  });
  return records.every(record => record !== null) ? records as QuotaRoutingRecord[] : undefined;
}

/** A boolean declaration or retired certificate cannot stand in for local eligibility. */
export function parseReadOnlyStructured(value: unknown): NonNullable<HarnessHealth["readOnlyStructured"]> | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  if (typeof record.eligible !== "boolean" || typeof record.systemSandbox !== "boolean"
    || typeof record.sameAttemptContinuation !== "boolean") return null;
  for (const key of ["reasonCode", "reason"] as const) {
    if (!(record[key] === null || typeof record[key] === "string")) return null;
  }
  if (record.eligible && (record.reasonCode !== null || record.reason !== null)) return null;
  if (record.implemented !== undefined && typeof record.implemented !== "boolean") return null;
  return { eligible: record.eligible, systemSandbox: record.systemSandbox,
    reasonCode: record.reasonCode as string | null, reason: record.reason as string | null,
    sameAttemptContinuation: record.sameAttemptContinuation,
    ...(typeof record.implemented === "boolean" ? { implemented: record.implemented } : {}) };
}

/**
 * Strict parse of one harness row. A malformed row is dropped as unknown rather
 * than presented as health; an absent snapshot field is not invented either.
 */
function harnessRow(value: unknown): HarnessHealth | null {
  const row = value as HarnessHealth | null;
  if (!row || typeof row !== "object" || Array.isArray(row)) return null;
  if (typeof row.adapter !== "string" || !row.adapter.trim()) return null;
  if (!["unknown", "ready", "missing", "login-required", "unhealthy"].includes(row.status)) return null;
  if ((row.status === "ready") !== row.available) return null;
  if (typeof row.available !== "boolean" || !Number.isInteger(row.revision) || row.revision < 0) return null;
  if (!(row.manualPath === null || row.manualPath === undefined || typeof row.manualPath === "string")) return null;
  for (const key of ["executable", "version", "source", "reasonCode", "remedy", "checkedAt", "expiresAt"] as const) {
    if (row[key] != null && typeof row[key] !== "string") return null;
  }
  if (row.command != null && (!Array.isArray(row.command) || row.command.some(value => typeof value !== "string"))) return null;
  if (row.candidates != null && (!Array.isArray(row.candidates) || row.candidates.some(candidate =>
    !candidate || typeof candidate !== "object" || ["path", "source", "reasonCode", "status"].some(key =>
      (candidate as Record<string, unknown>)[key] != null && typeof (candidate as Record<string, unknown>)[key] !== "string")))) return null;
  // ADR-018 §23: a malformed quota observation is dropped as unknown instead of
  // blanking the page or being presented as a recorded 0%. An observation that
  // was not recorded adds no key at all.
  const { quota: rawQuota, quotaRouting: rawRouting, readOnlyStructured: rawReadOnly, systemSandbox: rawSandbox,
    reviewVerification: _retiredCertificate, verified: _retiredVerified, ...rest } = row as HarnessHealth & { reviewVerification?: unknown; verified?: unknown };
  const quota = parseQuota(rawQuota);
  const quotaRouting = parseQuotaRouting(rawRouting);
  const readOnlyStructured = parseReadOnlyStructured(rawReadOnly);
  return { ...rest, adapter: row.adapter.trim(), manualPath: row.manualPath ?? null,
    ...(quota ? { quota } : {}), ...(quotaRouting ? { quotaRouting } : {}),
    ...(readOnlyStructured ? { readOnlyStructured } : {}),
    ...(typeof rawSandbox === "boolean" ? { systemSandbox: rawSandbox } : {}) };
}

/** Known harnesses first in their fixed order; anything else keeps its own order. */
function harnessRows(value: unknown): HarnessHealth[] {
  if (!Array.isArray(value)) return [];
  const rank = (adapter: string) => {
    const index = (HARNESS_ADAPTERS as readonly string[]).indexOf(adapter.trim().toLowerCase());
    return index === -1 ? HARNESS_ADAPTERS.length : index;
  };
  return value.map(harnessRow)
    .filter((row): row is HarnessHealth => row !== null)
    .sort((a, b) => rank(a.adapter) - rank(b.adapter));
}

/** The snapshot's `harnesses` rows; a missing field reads as no recorded health. */
export function snapshotHarnesses(snapshot: Snapshot): HarnessHealth[] {
  return harnessRows(snapshot.harnesses);
}

/** The `harnesses` rows carried by a `capabilities` reply. */
export function commandHarnesses(reply: unknown): HarnessHealth[] {
  return harnessRows((reply as { harnesses?: unknown } | null)?.harnesses);
}

export function parseRuntimeVersion(value: unknown): RuntimeVersionInfo {
  const info = value as RuntimeVersionInfo | null;
  const facts = (record: VersionFacts | null) => !!record && typeof record === "object" && !Array.isArray(record)
    && (["softwareVersion", "contractVersion", "sourceCommit", "installedAt"] as const).every(key =>
      record[key] === null || typeof record[key] === "string" && !!record[key].trim())
    && (record.schemaVersion === null || Number.isSafeInteger(record.schemaVersion) && record.schemaVersion >= 0);
  if (!info || !facts(info.running) || !["source", "runtime"].includes(info.running.mode)
    || !(info.installed === null || facts(info.installed))) {
    throw new ApiError("INVALID_RESPONSE", "版本信息无法识别，请检查服务版本。");
  }
  return info;
}

export function createApi(prefix: string, fetcher: typeof fetch = fetch) {
  const base = prefix.replace(/\/+$/, "");
  // Metadata belongs to a returned representation, without extending any DTO.
  // Each read gets its own root object: revalidation of a cached body cannot
  // rewrite the verification evidence of data already displayed elsewhere.
  const verificationTimes = new WeakMap<object, number | null>();
  function readVerifiedAt(value: unknown): number | null {
    return value !== null && typeof value === "object" ? verificationTimes.get(value) ?? null : null;
  }
  function verifiedRead<T>(value: T, verifiedAtMs: number | null): T {
    if (value === null || typeof value !== "object") return value;
    const representation = (Array.isArray(value) ? [...value] : { ...value }) as T & object;
    verificationTimes.set(representation, verifiedAtMs);
    return representation;
  }
  async function request(path: string, init: RequestInit = {}) {
    let response: Response;
    try {
      response = await fetcher(`${base}/api${path}`, {
        credentials: "same-origin",
        cache: "no-store",
        ...init,
      });
    } catch (error) {
      if (isAbortError(error)) throw error;
      throw new ApiError("NETWORK", "无法连接本地黑板；请刷新重试。");
    }
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      throw new ApiError("INVALID_RESPONSE", "本地服务返回了无法解析的响应。");
    }
    const body = data as {
      ok?: boolean;
      error?: { code?: string; message?: string };
    };
    if (!response.ok || body?.ok === false) {
      throw new ApiError(
        body?.error?.code || `HTTP_${response.status}`,
        body?.error?.message || "请求未成功。",
      );
    }
    return verifiedRead(data, responseVerifiedAtMs(response));
  }

  /* ---- conditional reads (standard HTTP ETag / If-None-Match / 304) ---- */

  /** One cached read representation: the validator and the parsed body it vouches for. */
  type CachedRead = { etag: string; data: unknown };
  /** LRU bound; unique filter/pagination URLs stay bounded per API instance. */
  const MAX_CONDITIONAL_ENTRIES = 32;
  const conditionalCache = new Map<string, CachedRead>();
  /**
   * Tickets of the reads currently outstanding, keyed by URL. Tickets come
   * from one global monotonic counter, so a released and later re-created
   * entry can never let a late reply masquerade as a newer request (no ABA),
   * and the entry is released as soon as the request it names completes:
   * per-URL state stays bounded by the reads actually in the air.
   */
  let ticketSource = 0;
  const outstanding = new Map<string, number>();
  /**
   * Cache generation: every clear — a session change or a 401 expiry — bumps
   * it and thereby voids the publication of every read already in the air, so
   * a reply that belonged to the previous session can neither store a body
   * nor hand one to the caller as a 304 hit.
   */
  let cacheGeneration = 0;
  /** The browser session the cached bodies were read under; a new session starts empty. */
  let sessionId: string | null = null;

  function clearConditionalCache() {
    conditionalCache.clear();
    cacheGeneration += 1;
  }

  function storeConditionalRead(url: string, entry: CachedRead) {
    conditionalCache.delete(url);
    conditionalCache.set(url, entry);
    while (conditionalCache.size > MAX_CONDITIONAL_ENTRIES) {
      const oldest = conditionalCache.keys().next().value;
      if (oldest === undefined) break;
      conditionalCache.delete(oldest);
    }
  }

  /**
   * One conditional GET of a read route that answers ETag / If-None-Match / 304.
   *
   * The request's ticket is held from issue until its body has actually been
   * consumed, and the publication verdict — current ticket, unchanged cache
   * generation, un-aborted signal — is evaluated fresh at that moment, never
   * from a header-time snapshot: a delayed body can therefore never publish
   * an older representation over a newer same-URL reply. A 304 returns the
   * representation this request itself offered for revalidation — never
   * whatever body sits in the cache now — and only while it still belongs to
   * this session; the empty 304 payload is never handed to the JSON parser,
   * existing data is not lost and the outcome is not an error. Entries are
   * keyed by the full URL (different filters or pages are different entries),
   * live per API instance and per browser session — a session change or a 401
   * expiry empties the cache and voids every in-flight publication. A reply
   * that loses its ticket (a superseded read) returns its data but never
   * rewrites a newer cache entry. The finally block releases the ticket
   * latest-only on every completion path — network failure, abort, 304, parse
   * error, status refusal and success alike — so settled per-URL state stays
   * bounded and a newer pending ticket is never deleted. Compression is the
   * server's standard response negotiation; the browser decompresses `fetch`
   * bodies, so nothing here touches Content-Encoding.
   */
  /**
   * The verification time of one response: its standard HTTP `Date` header,
   * read per response and never stored with the representation. A 304 carries
   * a fresh `Date` even when its body is the cached one, and a warm 200 from
   * the service's read cache is stamped when served — so this is "when the
   * server last verified this state", exactly what a display clock needs.
   * Absent or unparseable (mocks, clock-less test doubles) reads as null;
   * verification displays must never substitute a client or record clock.
   */
  function responseVerifiedAtMs(response: Response): number | null {
    const header = response.headers.get("Date");
    if (!header) return null;
    const parsed = Date.parse(header);
    return Number.isFinite(parsed) ? parsed : null;
  }

  async function conditionalRead(path: string, signal?: AbortSignal, { refresh = false, domainNegativeReport = false }: { refresh?: boolean; domainNegativeReport?: boolean } = {}): Promise<{ data: unknown; verifiedAtMs: number | null }> {
    const url = `${base}/api${path}`;
    const cached = conditionalCache.get(url);
    const atGeneration = cacheGeneration;
    const ticket = ++ticketSource;
    outstanding.set(url, ticket);
    const headers: Record<string, string> = {};
    if (cached && !refresh) headers["If-None-Match"] = cached.etag;
    try {
      let response: Response;
      try {
        response = await fetcher(url, { credentials: "same-origin", cache: "no-store", headers, signal });
      } catch (error) {
        if (isAbortError(error)) throw error;
        throw new ApiError("NETWORK", "无法连接本地黑板；请刷新重试。");
      }
      if (response.status === 304) {
        if (cached && cacheGeneration === atGeneration && !signal?.aborted) {
          return { data: cached.data, verifiedAtMs: responseVerifiedAtMs(response) };
        }
        // A 304 is only meaningful against a representation this instance still
        // holds under this session; without one the body cannot be reconstructed
        // and the read stays failed instead of guessing.
        throw new ApiError("INVALID_RESPONSE", "本地服务返回了无法解析的响应。");
      }
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        throw new ApiError("INVALID_RESPONSE", "本地服务返回了无法解析的响应。");
      }
      // The body has arrived: only now is the publication verdict decided. The
      // ticket was held through the body read, so this fresh check outranks any
      // same-URL reply that completed meanwhile.
      const stillCurrent = outstanding.get(url) === ticket;
      const body = data as {
        ok?: boolean;
        error?: { code?: string; message?: string };
      };
      // The refusal envelope is exactly `{ok:false, error:{code,message}}`.
      // Routes whose own DTO legitimately carries a top-level negative
      // assessment (backup-preflight's ok:false with per-class reasons and no
      // error object) are domain data, not failures — enabled per route so
      // every other route's ok:false keeps refusing as before.
      const envelopeError = body?.ok === false && body?.error !== undefined;
      if (!response.ok || (body?.ok === false && (envelopeError || !domainNegativeReport))) {
        if (envelopeError && body?.error?.code === SESSION_EXPIRED_CODE) clearConditionalCache();
        throw new ApiError(
          body?.error?.code || `HTTP_${response.status}`,
          body?.error?.message || "请求未成功。",
        );
      }
      if (stillCurrent && cacheGeneration === atGeneration && !signal?.aborted) {
        const etag = response.headers.get("ETag");
        if (etag) storeConditionalRead(url, { etag, data });
      }
      return { data, verifiedAtMs: responseVerifiedAtMs(response) };
    } finally {
      // Latest-only release: a superseded read never deletes the newer
      // request's pending ticket, and a settled request leaves nothing behind.
      if (outstanding.get(url) === ticket) outstanding.delete(url);
    }
  }
  return {
    readVerifiedAt,
    async snapshot(signal?: AbortSignal): Promise<Snapshot> {
      const { data, verifiedAtMs } = (await conditionalRead("/console", signal)) as { data: Snapshot; verifiedAtMs: number | null };
      if (
        !data ||
        !Number.isInteger(data.tableRevision) ||
        !validConfigurationState(data) ||
        !data.gate ||
        !Array.isArray(data.profiles) ||
        !Array.isArray(data.cards) ||
        !Array.isArray(data.preferences) ||
        !Array.isArray(data.familyPreferences) ||
        !Array.isArray(data.preferenceOverrides) ||
        !Array.isArray(data.familyAnnotations) ||
        !Array.isArray(data.modelConcurrency) ||
        !data.tasks ||
        !Number.isSafeInteger(data.tasks.pendingCount) ||
        data.tasks.pendingCount < 0 ||
        typeof data.csrfToken !== "string"
      ) {
        throw new ApiError(
          "INVALID_RESPONSE",
          "控制台数据不完整；请检查服务版本。",
        );
      }
      // A valid session descriptor is required; a missing or malformed one is
      // never read as write access. A different session id empties the read
      // cache: its bodies were read under the previous session.
      const session = parseConsoleSession(data.consoleSession);
      if (sessionId !== null && session.id !== sessionId) clearConditionalCache();
      sessionId = session.id;
      return verifiedRead({ ...data, routingHealth: parseRoutingHealth(data.routingHealth), consoleSession: session,
        ...(data.consoleAccess === undefined ? {} : { consoleAccess: parseConsoleAccess(data.consoleAccess) }) }, verifiedAtMs);
    },
    async command<T = unknown>(
      operation: string,
      params: unknown,
      csrfToken: string,
    ): Promise<T> {
      const data = (await request("/command", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Buddy-CSRF": csrfToken,
        },
        body: JSON.stringify({ operation, params }),
      })) as { ok: boolean; result: T };
      if (!data || data.ok !== true || !Object.hasOwn(data, "result")) {
        throw new ApiError("INVALID_RESPONSE", "提交结果未知；请核对后重试。");
      }
      return verifiedRead(data.result, readVerifiedAt(data));
    },
    async task(runId: string, signal?: AbortSignal) {
      const { data, verifiedAtMs } = await conditionalRead(`/tasks/${encodeURIComponent(runId)}`, signal);
      return verifiedRead(data, verifiedAtMs);
    },
    async tasks(params: TaskQuery, signal?: AbortSignal): Promise<TaskPage> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const { data, verifiedAtMs } = await conditionalRead(`/tasks?${query}`, signal) as { data: TaskPage; verifiedAtMs: number | null };
      if (!data || !Array.isArray(data.runs) || !Number.isInteger(data.total)
        || !(data.nextCursor === null || typeof data.nextCursor === "string")) {
        throw new ApiError("INVALID_RESPONSE", "委派历史不完整；请检查服务版本。");
      }
      return verifiedRead(data, verifiedAtMs);
    },
    /** Read-only work-objective list for any logged-in session; no lease or model call. */
    async objectives(params: ObjectiveQuery, signal?: AbortSignal): Promise<ObjectivePage> {      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const { data, verifiedAtMs } = await conditionalRead(`/objectives?${query}`, signal) as { data: ObjectivePage; verifiedAtMs: number | null };
      if (!data || !Array.isArray(data.objectives) || !Number.isInteger(data.total)
        || !(data.nextCursor === null || typeof data.nextCursor === "string")
        || !Number.isInteger(data.cursor) || typeof data.changed !== "boolean"
        || !data.objectives.every(validObjectiveSummary)) {
        throw new ApiError("INVALID_RESPONSE", "工作目标列表不完整；请检查服务版本。");
      }
      return verifiedRead(data, verifiedAtMs);
    },
    /**
     * One timeline read: the parsed projection plus the response's own
     * verification time (`verifiedAtMs`, from the standard HTTP `Date`
     * header of the 200 or 304 — fresh per response even when the body is
     * the service's cached projection; null when a response carries none).
     * The display clock anchors on this, never on the body's own
     * `observedAt`, which stays the recorded source fact of an older
     * generation.
     */
    async objectiveTimeline(
      objectiveId: string,
      params: { limit?: number; query?: string; filter?: ObjectiveFilter },
      signal?: AbortSignal,
    ): Promise<{ timeline: ObjectiveTimeline; verifiedAtMs: number | null }> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const { data, verifiedAtMs } = await conditionalRead(
        `/objectives/${encodeURIComponent(objectiveId)}/timeline?${query}`,
        signal,
      ) as { data: ObjectiveTimeline; verifiedAtMs: number | null };
      if (!data || typeof data.observedAt !== "string"
        || !data.objective || typeof data.objective.objectiveId !== "string"
        || !validObjectiveSummary(data.objective)
        || !Array.isArray(data.rows) || !Array.isArray(data.spans) || !Array.isArray(data.events)
        || !validTimelineRows(data.rows)
        || !data.totals || !Number.isInteger(data.totals.rows) || !Number.isInteger(data.totals.allRows)
        || !Number.isInteger(data.totals.spans) || !Number.isInteger(data.totals.events)
        || !data.truncated || typeof data.truncated.rows !== "boolean"
        || typeof data.truncated.spans !== "boolean" || typeof data.truncated.events !== "boolean"
        || !Number.isInteger(data.cursor) || typeof data.scopeComplete !== "boolean"
        || typeof data.filtered !== "boolean") {
        throw new ApiError("INVALID_RESPONSE", "工作目标时间轴不完整；请检查服务版本。");
      }
      return { timeline: data, verifiedAtMs };
    },
    /** Version facts are read once by visible settings, independent of snapshots. */
    async runtimeVersion(signal?: AbortSignal): Promise<RuntimeVersionInfo> {
      return parseRuntimeVersion(await request("/runtime-version", { signal }));
    },
    /**
     * The on-demand backup preflight (`GET /api/backup-preflight`): the same
     * report the periodic snapshot used to carry, computed only when the
     * storage panel asks for it — never from the 3-second reads. `refresh`
     * names an explicit re-check: it omits If-None-Match so the server's fresh
     * walk is actually transferred instead of answered by a 304. The report
     * itself is recomputed server-side on every read either way.
     */
    async backupPreflight(signal?: AbortSignal, { refresh = false }: { refresh?: boolean } = {}): Promise<BackupPreflight> {
      // This route's report legitimately carries a negative assessment
      // (ok:false with per-class reasons) as domain data; only a real refusal
      // envelope (with an error object) is a failure.
      const { data: report } = await conditionalRead("/backup-preflight", signal, { refresh, domainNegativeReport: true }) as { data: BackupPreflight | null };
      const inventory = (value: unknown) => !!value && typeof value === "object" && !Array.isArray(value)
        && Number.isSafeInteger((value as { count?: unknown }).count) && (value as { count: number }).count >= 0
        && Array.isArray((value as { paths?: unknown }).paths) && (value as { paths: unknown[] }).paths.every(p => typeof p === "string")
        && Array.isArray((value as { entries?: unknown }).entries) && (value as { entries: unknown[] }).entries.every(entry =>
          !!entry && typeof entry === "object" && typeof (entry as { path?: unknown }).path === "string"
          && typeof (entry as { reason?: unknown }).reason === "string");
      if (!report || typeof report.policy !== "string" || typeof report.ok !== "boolean"
        || typeof report.needsAttention !== "boolean"
        || !inventory(report.copied) || !inventory(report.skipped) || !inventory(report.rejected)) {
        throw new ApiError("INVALID_RESPONSE", "备份预检响应不完整，请检查服务版本。");
      }
      return report;
    },
    /**
     * `storage_plan` (0.16.0): a private, expiring reclamation plan. It scans
     * disk, so the panel only ever calls it on an explicit 检查占用 click —
     * never on page entry and never from the 3-second refresh.
     */
    async storagePlan(csrfToken: string): Promise<StoragePlan> {
      return parseStoragePlan(await command("storage_plan", {}, csrfToken));
    },
    /**
     * `storage_apply` (0.16.0): applies one exact confirmed plan. The command
     * identity is the caller's; a lost reply keeps it so 重试同一请求 replays
     * the same command instead of minting a second one. A reply whose planId
     * does not match the request, or that lacks `complete: true`, resolves
     * nothing — the caller keeps the identity and the unresolved state.
     */
    async storageApply(planId: string, commandId: string, csrfToken: string): Promise<StorageApplyResult> {
      const result = await command<StorageApplyResult>("storage_apply", { planId, commandId, confirm: true }, csrfToken);
      const mismatch = !validStorageApplyResult(result) || result.planId !== planId || result.complete !== true;
      if (mismatch) {
        throw new ApiError("INVALID_RESPONSE", "清理结果未知；请重试同一请求。");
      }
      return result;
    },
    /**
     * `capabilities` with `refresh: true` (ADR-017 §15): one explicit, bounded
     * harness re-check, optionally for a single adapter. It performs no model
     * call, and the reply is the capabilities object carrying the refreshed
     * `harnesses` rows. Nothing is cached here: the caller reloads the snapshot
     * so the page shows the recorded health rather than a local guess.
     */
    async harnessRefresh(csrfToken: string, adapter?: string): Promise<HarnessHealth[]> {
      const reply = await command<unknown>(
        "capabilities",
        adapter ? { refresh: true, adapter } : { refresh: true },
        csrfToken,
      );
      const rows = commandHarnesses(reply);
      if (!rows.length) {
        throw new ApiError("INVALID_RESPONSE", "重新检测结果不完整；请检查服务版本。");
      }
      return rows;
    },
    /**
     * `harness_set` (ADR-017 §15): stores one manual absolute path, or `null`
     * to restore automatic detection. `expectedRevision` fences the write
     * against a concurrent check; a conflict is reported, never overwritten.
     */
    async harnessSet(
      adapter: string,
      path: string | null,
      expectedRevision: number,
      csrfToken: string,
    ): Promise<HarnessHealth> {
      const reply = await command<unknown>(
        "harness_set",
        { adapter, path, expectedRevision },
        csrfToken,
      );
      const row = harnessRow((reply as { harness?: unknown } | null)?.harness);
      if (!row || row.adapter.toLowerCase() !== adapter.trim().toLowerCase()) {
        throw new ApiError("INVALID_RESPONSE", "路径保存结果未知；请检查服务版本。");
      }
      return row;
    },
  };
  async function command<T = unknown>(operation: string, params: unknown, csrfToken: string): Promise<T> {
    const data = await request("/command", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Buddy-CSRF": csrfToken,
      },
      body: JSON.stringify({ operation, params }),
    }) as { ok: boolean; result: T };
    if (!data || data.ok !== true || !Object.hasOwn(data, "result")) {
      throw new ApiError("INVALID_RESPONSE", "提交结果未知；请核对后重试。");
    }
    return data.result;
  }
}
export type ConsoleApi = Omit<ReturnType<typeof createApi>, "readVerifiedAt"> & {
  readVerifiedAt?: (value: unknown) => number | null;
};
