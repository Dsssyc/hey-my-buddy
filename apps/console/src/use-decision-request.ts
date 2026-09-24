import { useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, uncertainResponse } from "./api";
import type { Snapshot } from "./types";

export function useDecisionRequest(
  api: ConsoleApi,
  snapshot: Snapshot,
  refresh: () => Promise<Snapshot | null>,
) {
  const pending = useRef<{
    operation: string;
    params: Record<string, unknown>;
  } | null>(null);
  const active = useRef(false);
  const [busy, setBusy] = useState(false);
  const [retryId, setRetryId] = useState("");
  const [message, setMessage] = useState("");

  async function submit(
    operation: string,
    params: Record<string, unknown> = {},
  ) {
    if (active.current) return;
    const request = pending.current || {
      operation,
      params: { ...params, requestId: crypto.randomUUID() },
    };
    pending.current = request;
    active.current = true;
    setBusy(true);
    setMessage("");
    try {
      await api.command(request.operation, request.params, snapshot.csrfToken);
      pending.current = null;
      setRetryId("");
      setMessage("请求已记录，可在评价维护中查看进度与建议。");
      try { await refresh(); }
      catch { setMessage("请求已记录；页面刷新失败，可在评价维护中刷新记录。"); }
    } catch (error) {
      const uncertain = uncertainResponse(error);
      if (uncertain) {
        setRetryId(String(request.params.requestId));
        setMessage(
          "提交结果尚未确认。重试会使用同一个请求 ID，不会重复调用模型。",
        );
      } else {
        pending.current = null;
        setRetryId("");
        setMessage(errorText(error));
      }
    } finally {
      active.current = false;
      setBusy(false);
    }
  }
  return { submit, busy, retryId, message };
}
