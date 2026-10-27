import i18n from "i18next";
import { z } from "zod";
import { locale } from "./i18n";
import type { RuntimeVersionInfo, Configuration, ConsoleAccess, ConsoleSession, ReminderSettings, RoutingHealth, Snapshot, Preference } from "./types";
import type {
  MacroTaskFilter, MacroTaskPage, MacroTaskQuery, MacroTaskTimeline,
} from "./macro-task-types";
import { macroTaskPageSchema, macroTaskTimelineSchema } from "./macro-task-types";
import {
  configurationSchema, consoleAccessSchema, consoleSessionSchema, harnessHealthSchema, permissionLevelsSchema,
  preferencesSchema, providerCoolingSchema, reminderSettingsSchema, routingHealthSchema, runtimeVersionSchema, safeCount, dateString, snapshotReadSchema,
} from "./types";

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string | (() => string),
  ) {
    super(typeof message === "function" ? message() : message);
    if (typeof message === "function") Object.defineProperty(this, "message", { get: message, configurable: true });
    this.name = "ApiError";
  }
}

/**
 * The one identity-loss refusal the board still issues: an HTTP 401
 * `CONSOLE_SESSION_EXPIRED`. A callback receiving it must not automatically
 * retry a write; a fresh `hey-my-buddy console` login restores authority.
 */
const SESSION_EXPIRED_CODE = "CONSOLE_SESSION_EXPIRED";

export function isSessionExpiredRefusal(error: unknown): boolean {
  return error instanceof ApiError && error.code === SESSION_EXPIRED_CODE;
}

/**
 * Strict parse of the authenticated snapshot's session descriptor. The live
 * service issues `{id}` for a valid login. A missing descriptor or invalid id
 * is malformed and never grants write access. An expired or absent login is
 * reported by the HTTP layer as a 401 `CONSOLE_SESSION_EXPIRED`.
 */
function parseConsoleSession(value: unknown): ConsoleSession {
  if (!consoleSessionSchema.safeParse(value).success) {
    throw new ApiError(
      "INVALID_RESPONSE",
      () => i18n.t("common:error.session"),
    );
  }
  return value as ConsoleSession;
}

export function parseConsoleAccess(value: unknown): ConsoleAccess {
  if (!consoleAccessSchema.safeParse(value).success) {
    throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.access"));
  }
  return value as ConsoleAccess;
}

export function parseReminderSettings(value: unknown): ReminderSettings {
  if (!reminderSettingsSchema.safeParse(value).success) {
    throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.reminders"));
  }
  return value as ReminderSettings;
}


/** Current L4 list settings; retired single/dual Router slots are refused. */
export function validRoutingConfiguration(value: unknown): value is Configuration {
  return configurationSchema.safeParse(value).success;
}

/** Preferences carry only user-authored notes and their effective mode. */
export function validPreferences(value: unknown): value is Preference[] {
  return preferencesSchema.safeParse(value).success;
}

/** Optional health observations are never inferred from a failure count. */
export function parseRoutingHealth(value: unknown): RoutingHealth | undefined {
  return routingHealthSchema.safeParse(value).success ? value as RoutingHealth : undefined;
}

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return i18n.exists(`common:error.codes.${error.code}`) ? i18n.t(`common:error.codes.${error.code}`) : error.message;
  return error instanceof Error ? error.message : i18n.t("common:error.failed");
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
    ? `${i18n.t("common:snapshot.verified")} ${new Date(verifiedAtMs).toLocaleString(locale, { hour12: false })}`
    : i18n.t("common:snapshot.unverified");
}

/* ---- 0.16.0 storage wire shapes (docs/reference/operations.md) ---- */

const storageCategorySchema = z.looseObject({
  id: z.string().min(1), bytes: safeCount, reclaimableBytes: safeCount, count: safeCount, eligibleCount: safeCount, reasons: z.array(z.string()),
});
export type StorageCategory = z.infer<typeof storageCategorySchema>;
const storageCandidateSchema = z.looseObject({
  id: z.string().min(1), category: z.string().min(1), path: z.string(), bytes: safeCount, eligible: z.boolean(), reasons: z.array(z.string()),
});
const storageOrphanProcessSchema = z.looseObject({ pid: z.int().positive(), kind: z.string(), stateDir: z.string().nullable(), runtimeDir: z.string().nullable() });
export type StorageOrphanProcess = z.infer<typeof storageOrphanProcessSchema>;
const storagePlanSchema = z.looseObject({
  planId: z.string().min(1), createdAt: dateString, expiresAt: dateString,
  categories: z.array(storageCategorySchema).refine(rows => new Set(rows.map(row => row.id)).size === rows.length),
  candidates: z.array(storageCandidateSchema), orphanProcesses: z.array(storageOrphanProcessSchema),
});
export type StoragePlan = z.infer<typeof storagePlanSchema>;
const storageApplyResultSchema = z.looseObject({
  planId: z.string().min(1), removedBytes: safeCount, complete: z.literal(true),
  removed: z.array(z.looseObject({ id: z.string().min(1), path: z.string(), bytes: safeCount })),
  skipped: z.array(z.looseObject({ id: z.string().min(1), path: z.string(), reasons: z.array(z.string()) })),
});
export type StorageApplyResult = z.infer<typeof storageApplyResultSchema>;

function parseStoragePlan(value: unknown): StoragePlan {
  if (!storagePlanSchema.safeParse(value).success) {
    throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.storagePlan"));
  }
  return value as StoragePlan;
}

function validStorageApplyResult(value: unknown): value is StorageApplyResult {
  return storageApplyResultSchema.safeParse(value).success;
}

/* ---- harness health wire shapes (ADR-017 §15) ---- */

export type { HarnessStatus, HarnessCandidate, HarnessHealth } from "./types";
import type { HarnessHealth, ProviderCooling } from "./types";

/** The supported harnesses, in the Buddy page's fixed display order. */
const HARNESS_ORDER = ["dsh", "zcode", "codex", "claude"] as const;

const providersSchema = z.array(providerCoolingSchema);
const harnessRowsSchema = z.array(z.unknown());

/** Parse provider cooling facts once at the service boundary. */
function parseProviders(value: unknown): ProviderCooling[] | undefined {
  return providersSchema.safeParse(value).success ? value as ProviderCooling[] : undefined;
}

/** The harness's declared levels, with their native enforcement strength. */
export function parsePermissionLevels(value: unknown): NonNullable<HarnessHealth["permissions"]> | null {
  if (!permissionLevelsSchema.safeParse(value).success) return null;
  return Object.fromEntries(Object.entries(value as NonNullable<HarnessHealth["permissions"]>));
}

/**
 * Strict parse of one harness row. A malformed row is dropped as unknown rather
 * than presented as health; an absent snapshot field is not invented either.
 */
function harnessRow(value: unknown): HarnessHealth | null {
  if (!harnessHealthSchema.safeParse(value).success) return null;
  const row = value as HarnessHealth;
  const { providers: rawProviders, permissions: rawPermissions, ...rest } = row;
  const providers = parseProviders(rawProviders);
  const permissions = parsePermissionLevels(rawPermissions);
  return { ...rest, harness: row.harness.trim(), manualPath: row.manualPath ?? null,
    ...(providers ? { providers } : {}),
    ...(permissions ? { permissions } : {}) };
}

/** Known harnesses first in their fixed order; anything else keeps its own order. */
function harnessRows(value: unknown): HarnessHealth[] {
  const rows = harnessRowsSchema.safeParse(value);
  if (!rows.success) return [];
  const rank = (harness: string) => {
    const index = (HARNESS_ORDER as readonly string[]).indexOf(harness.trim().toLowerCase());
    return index === -1 ? HARNESS_ORDER.length : index;
  };
  return rows.data.map(harnessRow)
    .filter((row): row is HarnessHealth => row !== null)
    .sort((a, b) => rank(a.harness) - rank(b.harness));
}

/** The snapshot's `harnesses` rows; a missing field reads as no recorded health. */
export function snapshotHarnesses(snapshot: Snapshot): HarnessHealth[] {
  return harnessRows(snapshot.harnesses);
}

/** The `harnesses` rows carried by a `capabilities` reply. */
function commandHarnesses(reply: unknown): HarnessHealth[] {
  return harnessRows((reply as { harnesses?: unknown } | null)?.harnesses);
}

export function parseRuntimeVersion(value: unknown): RuntimeVersionInfo {
  if (!runtimeVersionSchema.safeParse(value).success) {
    throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.version"));
  }
  return value as RuntimeVersionInfo;
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
      throw new ApiError("NETWORK", () => i18n.t("common:error.network"));
    }
    let data: unknown;
    try {
      data = await response.json();
    } catch {
      throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.unparsable"));
    }
    const body = data as {
      ok?: boolean;
      error?: { code?: string; message?: string };
    };
    if (!response.ok || body?.ok === false) {
      throw new ApiError(
        body?.error?.code || `HTTP_${response.status}`,
        body?.error?.message || (() => i18n.t("common:error.refused")),
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

  async function conditionalRead(path: string, signal?: AbortSignal): Promise<{ data: unknown; verifiedAtMs: number | null }> {
    const url = `${base}/api${path}`;
    const cached = conditionalCache.get(url);
    const atGeneration = cacheGeneration;
    const ticket = ++ticketSource;
    outstanding.set(url, ticket);
    const headers: Record<string, string> = {};
    if (cached) headers["If-None-Match"] = cached.etag;
    try {
      let response: Response;
      try {
        response = await fetcher(url, { credentials: "same-origin", cache: "no-store", headers, signal });
      } catch (error) {
        if (isAbortError(error)) throw error;
        throw new ApiError("NETWORK", () => i18n.t("common:error.network"));
      }
      if (response.status === 304) {
        if (cached && cacheGeneration === atGeneration && !signal?.aborted) {
          return { data: cached.data, verifiedAtMs: responseVerifiedAtMs(response) };
        }
        // A 304 is only meaningful against a representation this instance still
        // holds under this session; without one the body cannot be reconstructed
        // and the read stays failed instead of guessing.
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.unparsable"));
      }
      let data: unknown;
      try {
        data = await response.json();
      } catch {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.unparsable"));
      }
      // The body has arrived: only now is the publication verdict decided. The
      // ticket was held through the body read, so this fresh check outranks any
      // same-URL reply that completed meanwhile.
      const stillCurrent = outstanding.get(url) === ticket;
      const body = data as {
        ok?: boolean;
        error?: { code?: string; message?: string };
      };
      if (!response.ok || body?.ok === false) {
        if (body?.ok === false && body?.error?.code === SESSION_EXPIRED_CODE) clearConditionalCache();
        throw new ApiError(
          body?.error?.code || `HTTP_${response.status}`,
          body?.error?.message || (() => i18n.t("common:error.refused")),
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
      if (!snapshotReadSchema.safeParse(data).success) {
        throw new ApiError(
          "INVALID_RESPONSE",
          () => i18n.t("common:error.snapshot"),
        );
      }
      // A valid session descriptor is required; a missing or malformed one is
      // never read as write access. A different session id empties the read
      // cache: its bodies were read under the previous session.
      const session = parseConsoleSession(data.consoleSession);
      if (sessionId !== null && session.id !== sessionId) clearConditionalCache();
      sessionId = session.id;
      return verifiedRead({ ...data, routingHealth: parseRoutingHealth(data.routingHealth), consoleSession: session,
        ...(data.consoleAccess === undefined ? {} : { consoleAccess: parseConsoleAccess(data.consoleAccess) }),
        ...(data.reminderSettings === undefined ? {} : { reminderSettings: parseReminderSettings(data.reminderSettings) }) }, verifiedAtMs);
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
          "X-Hey-My-Buddy-CSRF": csrfToken,
        },
        body: JSON.stringify({ operation, params }),
      })) as { ok: boolean; result: T };
      if (!data || data.ok !== true || !Object.hasOwn(data, "result")) {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.submit"));
      }
      return verifiedRead(data.result, readVerifiedAt(data));
    },
    async task(runId: string, signal?: AbortSignal) {
      const { data, verifiedAtMs } = await conditionalRead(`/tasks/${encodeURIComponent(runId)}`, signal);
      return verifiedRead(data, verifiedAtMs);
    },
    /** Session bodies are never cached; their byte cursors belong to one attempt. */
    async session(taskId: string, attemptId: string, params: { before?: number; after?: number; limit?: number }, signal: AbortSignal): Promise<unknown> {
      const query = new URLSearchParams(Object.entries(params).map(([key, value]) => [key, String(value)]));
      return request(`/tasks/${encodeURIComponent(taskId)}/attempts/${encodeURIComponent(attemptId)}/session?${query}`, { signal });
    },
    /** Read-only work macro task list for any logged-in session; no lease or model call. */
    async macroTasks(params: MacroTaskQuery, signal?: AbortSignal): Promise<MacroTaskPage> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const { data, verifiedAtMs } = await conditionalRead(`/macro-tasks?${query}`, signal) as { data: MacroTaskPage; verifiedAtMs: number | null };
      if (!macroTaskPageSchema.safeParse(data).success) {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.macroTasks"));
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
    async macroTaskTimeline(
      macroTaskId: string,
      params: { limit?: number; query?: string; filter?: MacroTaskFilter },
      signal?: AbortSignal,
    ): Promise<{ timeline: MacroTaskTimeline; verifiedAtMs: number | null }> {
      const query = new URLSearchParams();
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== "") query.set(key, String(value));
      }
      const { data, verifiedAtMs } = await conditionalRead(
        `/macro-tasks/${encodeURIComponent(macroTaskId)}/timeline?${query}`,
        signal,
      ) as { data: MacroTaskTimeline; verifiedAtMs: number | null };
      if (!macroTaskTimelineSchema.safeParse(data).success) {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.timeline"));
      }
      return { timeline: data, verifiedAtMs };
    },
    /** Version facts are read once by visible settings, independent of snapshots. */
    async runtimeVersion(signal?: AbortSignal): Promise<RuntimeVersionInfo> {
      return parseRuntimeVersion(await request("/runtime-version", { signal }));
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
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.cleanup"));
      }
      return result;
    },
    /**
     * `capabilities` with `refresh: true` (ADR-017 §15): one explicit, bounded
     * harness re-check, optionally for a single harness. It performs no model
     * call, and the reply is the capabilities object carrying the refreshed
     * `harnesses` rows. Nothing is cached here: the caller reloads the snapshot
     * so the page shows the recorded health rather than a local guess.
     */
    async harnessRefresh(csrfToken: string, harness?: string): Promise<HarnessHealth[]> {
      const reply = await command<unknown>(
        "capabilities",
        harness ? { refresh: true, harness } : { refresh: true },
        csrfToken,
      );
      const rows = commandHarnesses(reply);
      if (!rows.length) {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.recheck"));
      }
      return rows;
    },
    /**
     * `harness_set` (ADR-017 §15): stores one manual absolute path, or `null`
     * to restore automatic detection.
     */
    async harnessSet(
      harness: string,
      path: string | null,
      csrfToken: string,
    ): Promise<HarnessHealth> {
      const reply = await command<unknown>(
        "harness_set",
        { harness, path },
        csrfToken,
      );
      const row = harnessRow((reply as { harness?: unknown } | null)?.harness);
      if (!row || row.harness.toLowerCase() !== harness.trim().toLowerCase()) {
        throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.pathSaved"));
      }
      return row;
    },
  };
  async function command<T = unknown>(operation: string, params: unknown, csrfToken: string): Promise<T> {
    const data = await request("/command", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Hey-My-Buddy-CSRF": csrfToken,
      },
      body: JSON.stringify({ operation, params }),
    }) as { ok: boolean; result: T };
    if (!data || data.ok !== true || !Object.hasOwn(data, "result")) {
      throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.submit"));
    }
    return data.result;
  }
}
export type ConsoleApi = Omit<ReturnType<typeof createApi>, "readVerifiedAt"> & {
  readVerifiedAt?: (value: unknown) => number | null;
};
