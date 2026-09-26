import type { ConsoleSession, Snapshot, TaskPage, TaskQuery } from "./types";
import { READ_ONLY_ACTION_REFUSAL } from "./console-session";

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
  FORBIDDEN: "当前页面没有这项操作的权限，请从 Buddy 重新打开控制台。",
  WRITER_EXPIRED: "编辑权限已过期。你的草稿仍在，请重新取得权限。",
  WRITER_NOT_ACTIVE: "编辑权限已失效。草稿仍然保留，需要重新取得权限。",
  STALE_GENERATION: "操作资格已失效，未提交任何变更。请刷新并核对当前负责人。",
  SHUTDOWN_UNCONFIRMED: "尚未确认前一次执行已停止，暂时不能重试。",
  CONSOLE_READ_ONLY: READ_ONLY_ACTION_REFUSAL,
  CONSOLE_SESSION_EXPIRED: "控制台会话已过期或 Cookie 无效。请从 Buddy 重新打开控制台；草稿仍然保留，不会自动重新取得写权限。",
  CONSOLE_ENTRY_EXPIRED: "控制台入口票据已过期或已被使用，请重新打开入口取得新链接。",
};

/**
 * Codes that prove the current browser session may no longer write. A callback
 * receiving one of these must not retry, renew or release anything: only the
 * bounded lease expires by itself, and a fresh CLI entry is the explicit way to
 * acquire write authority again.
 */
export const READ_ONLY_REFUSAL_CODES = ["CONSOLE_READ_ONLY", "CONSOLE_SESSION_EXPIRED"] as const;

export function isReadOnlyRefusal(error: unknown): boolean {
  return error instanceof ApiError
    && (READ_ONLY_REFUSAL_CODES as readonly string[]).includes(error.code);
}

/**
 * Strict parse of the authenticated snapshot's session descriptor. Every
 * malformation is refused instead of defaulting to write access: a missing
 * descriptor, a non-boolean `canWrite`, an empty public id, an unknown reason
 * or a contradictory pair (`canWrite:true` with a read-only reason, or a
 * read-only session without its `superseded` reason) rejects the snapshot.
 */
export function parseConsoleSession(value: unknown): ConsoleSession {
  const session = value as Partial<ConsoleSession> | null | undefined;
  const valid = !!session && typeof session === "object" && !Array.isArray(session)
    && typeof session.id === "string" && session.id.trim().length > 0
    && typeof session.canWrite === "boolean"
    && (session.canWrite ? session.reason === null : session.reason === "superseded");
  if (!valid) {
    throw new ApiError(
      "INVALID_RESPONSE",
      "控制台会话信息缺失或无法识别；为安全起见不会授予写权限，请检查服务版本或重新打开入口。",
    );
  }
  return session as ConsoleSession;
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
        !data.gate ||
        !Array.isArray(data.profiles) ||
        !Array.isArray(data.cards) ||
        !Array.isArray(data.annotations) ||
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
  };
}
export type ConsoleApi = ReturnType<typeof createApi>;
