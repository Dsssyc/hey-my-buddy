import type { Snapshot } from "./types";

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
  REVISION_CONFLICT: "评价表已更新，请重新取得编辑权限后再保存。",
  CONFLICT: "此操作与现有记录冲突，未覆盖已有内容。",
  FORBIDDEN: "当前页面没有这项操作的权限，请从 Buddy 重新打开控制台。",
  WRITER_EXPIRED: "编辑权限已过期。你的草稿仍在，请重新取得权限。",
  WRITER_NOT_ACTIVE: "编辑权限已失效。草稿仍然保留，需要重新取得权限。",
  STALE_GENERATION: "这份编辑资格已经失效，未提交任何变更。",
  SHUTDOWN_UNCONFIRMED: "尚未确认前一次执行已停止，暂时不能重试。",
};

export function errorText(error: unknown): string {
  if (error instanceof ApiError) return messages[error.code] || error.message;
  return error instanceof Error ? error.message : "请求失败，请稍后重试。";
}

export function uncertainResponse(error: unknown): boolean {
  return !(error instanceof ApiError) || ["NETWORK", "INVALID_RESPONSE", "INTERNAL_ERROR"].includes(error.code) || /^HTTP_5/.test(error.code);
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
      if (error instanceof Error && error.name === "AbortError") throw error;
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
        !Array.isArray(data.tasks?.runs) ||
        typeof data.csrfToken !== "string"
      ) {
        throw new ApiError(
          "INVALID_RESPONSE",
          "控制台数据不完整，请检查服务版本。",
        );
      }
      return data;
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
  };
}
export type ConsoleApi = ReturnType<typeof createApi>;
