import { useEffect, useState } from "react";
import { z } from "zod";
import type { ConsoleApi } from "../api";
import { ApiError, errorText } from "../api";
import i18n from "i18next";
import { documentVisibleNow, useDocumentVisible } from "../page-visibility";
import { LiveActivity } from "../task-detail/LiveActivity";
import { parseActivity } from "../task-detail/parse";
import type { Activity } from "../task-detail/types";

/** Read the existing task view while this maintenance execution is visible. */
export function MaintenanceActivity({ taskId, api }: { taskId: string; api: ConsoleApi }) {
  const visible = useDocumentVisible();
  const [reading, setReading] = useState<{ activity: Activity | null; at: number } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!visible) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    async function poll() {
      if (!documentVisibleNow()) return;
      try {
        const task = z.object({ runId: z.literal(taskId), activity: z.unknown() }).safeParse(await api.task(taskId, controller.signal));
        if (!task.success) throw new ApiError("INVALID_RESPONSE", () => i18n.t("common:error.unparsable"));
        const activity = parseActivity(task.data.activity);
        if (!controller.signal.aborted) { setReading({ activity, at: Date.now() }); setError(""); }
      } catch (failure) {
        if (!controller.signal.aborted) { setReading(null); setError(errorText(failure)); }
      } finally {
        if (!controller.signal.aborted && documentVisibleNow()) timer = setTimeout(poll, 3000);
      }
    }
    void poll();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [taskId, api, visible]);
  return <div>
    <LiveActivity className="w-fit" activity={reading?.activity ?? null} stuck={false} nowMs={reading?.at ?? Date.now()} />
    {error && <p role="alert" className="mt-1 text-fail">{error}</p>}
  </div>;
}
