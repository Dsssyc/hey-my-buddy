import type { Configuration, ConsoleSession, Snapshot, TaskPage, TaskQuery } from "./types";
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
  REVISION_CONFLICT: "记录已更新，此操作未提交。请刷新并核对最新版本后再操作。",
  CONFLICT: "此操作与现有记录冲突，未覆盖已有内容。",
  FORBIDDEN: "当前页面没有这项操作的权限，请从 hey-my-buddy 重新打开控制台。",
  WRITER_EXPIRED: "编辑权限已过期。你的草稿仍在，请重新取得权限。",
  WRITER_NOT_ACTIVE: "编辑权限已失效。草稿仍然保留，需要重新取得权限。",
  STALE_GENERATION: "操作资格已失效，未提交任何变更。请刷新并核对当前负责人。",
  SHUTDOWN_UNCONFIRMED: "尚未确认前一次执行已停止，暂时不能重试。",
  STORAGE_INCOMPLETE: "清理尚未完成：删除已经开始，重试同一请求会继续完成它。",
  PLAN_EXPIRED: "计划已过期。请重新检查占用。",
  CONSOLE_SESSION_EXPIRED: "登录已失效：在终端运行 buddy console 重新登录后，本页会自动恢复。",
  CONSOLE_ENTRY_EXPIRED: "控制台入口票据已过期或已被使用，请重新打开入口取得新链接。",
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
      "控制台会话信息缺失或无法识别；为安全起见不会授予写权限，请检查服务版本或重新登录。",
    );
  }
  return session as ConsoleSession;
}

/** Contract 0.20.0 / schema 14: settings use two Router slots and one default mode. */
export function validRoutingConfiguration(value: unknown): value is Configuration {
  const config = value as Partial<Configuration> | null;
  return !!config && typeof config === "object" && !Array.isArray(config)
    && !("decisionProfileId" in config)
    && Number.isInteger(config.revision)
    && (config.fastRouterProfileId === null || typeof config.fastRouterProfileId === "string")
    && (config.reviewRouterProfileId === null || typeof config.reviewRouterProfileId === "string")
    && (config.defaultRoutingMode === "fast" || config.defaultRoutingMode === "review")
    && (config.routingBudget === "brief" || config.routingBudget === "standard" || config.routingBudget === "deep");
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
import type { HarnessHealth } from "./types";

/** The supported harnesses, in the Buddy 配置 page's fixed display order. */
export const HARNESS_ADAPTERS = ["dsh", "zcode", "codex", "claude"] as const;

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
  return { ...row, adapter: row.adapter.trim(), manualPath: row.manualPath ?? null };
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

export function createApi(prefix: string, fetcher: typeof fetch = fetch) {
  const base = prefix.replace(/\/+$/, "");
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
      throw new ApiError("NETWORK", "无法连接本地黑板。已有任务仍由后台管理。");
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
    return data;
  }
  return {
    async snapshot(signal?: AbortSignal): Promise<Snapshot> {
      const data = (await request("/console", { signal })) as Snapshot;
      if (
        !data ||
        !Number.isInteger(data.tableRevision) ||
        !validRoutingConfiguration(data.configuration) ||
        !data.gate ||
        !Array.isArray(data.profiles) ||
        !Array.isArray(data.cards) ||
        !Array.isArray(data.preferences) ||
        !Array.isArray(data.familyPreferences) ||
        !Array.isArray(data.preferenceOverrides) ||
        !Array.isArray(data.familyAnnotations) ||
        !Array.isArray(data.modelConcurrency) ||
        !Array.isArray(data.tasks?.runs) ||
        typeof data.csrfToken !== "string"
      ) {
        throw new ApiError(
          "INVALID_RESPONSE",
          "控制台数据不完整，请检查服务版本。",
        );
      }
      // A valid session descriptor is required; a missing or malformed one is
      // never read as write access.
      return { ...data, consoleSession: parseConsoleSession(data.consoleSession) };
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
        throw new ApiError("INVALID_RESPONSE", "操作响应不完整，提交结果尚未确认。");
      }
      return data.result;
    },
    async task(runId: string) {
      return request(`/tasks/${encodeURIComponent(runId)}`);
    },
    async tasks(params: TaskQuery, signal?: AbortSignal): Promise<TaskPage> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const data = await request(`/tasks?${query}`, { signal }) as TaskPage;
      if (!data || !Array.isArray(data.runs) || !Number.isInteger(data.total)
        || !(data.nextCursor === null || typeof data.nextCursor === "string")) {
        throw new ApiError("INVALID_RESPONSE", "委派历史响应不完整，请检查服务版本。");
      }
      return data;
    },
    /** Read-only work-objective list for any logged-in session; no lease or model call. */
    async objectives(params: ObjectiveQuery, signal?: AbortSignal): Promise<ObjectivePage> {      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const data = await request(`/objectives?${query}`, { signal }) as ObjectivePage;
      if (!data || !Array.isArray(data.objectives) || !Number.isInteger(data.total)
        || !(data.nextCursor === null || typeof data.nextCursor === "string")
        || !Number.isInteger(data.cursor) || typeof data.changed !== "boolean"
        || !data.objectives.every(validObjectiveSummary)) {
        throw new ApiError("INVALID_RESPONSE", "工作目标列表响应不完整，请检查服务版本。");
      }
      return data;
    },
    async objectiveTimeline(
      objectiveId: string,
      params: { limit?: number; query?: string; filter?: ObjectiveFilter },
      signal?: AbortSignal,
    ): Promise<ObjectiveTimeline> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const data = await request(
        `/objectives/${encodeURIComponent(objectiveId)}/timeline?${query}`,
        { signal },
      ) as ObjectiveTimeline;
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
        throw new ApiError("INVALID_RESPONSE", "工作目标时间轴响应不完整，请检查服务版本。");
      }
      return data;
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
        throw new ApiError("INVALID_RESPONSE", "清理尚未完成：回复不完整或与本计划不符，结果未知；可重试同一请求。");
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
        throw new ApiError("INVALID_RESPONSE", "重新检测的回复不完整，请检查服务版本。");
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
        throw new ApiError("INVALID_RESPONSE", "手动路径保存结果不完整，请检查服务版本。");
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
      throw new ApiError("INVALID_RESPONSE", "操作响应不完整，提交结果尚未确认。");
    }
    return data.result;
  }
}
export type ConsoleApi = ReturnType<typeof createApi>;
