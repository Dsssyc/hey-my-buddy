import type { ConsoleApi } from "../api";
import type { Task } from "../types";
import { parseAudit, parseInquiries, parseTask, parseWorkflow } from "./parse";
import type { Audit, Inquiries, RoutingEntry, Workflow } from "./types";

/** How many inquiries one read asks for; as many back means earlier ones may not be listed. */
export const INQUIRY_LIMIT = 200;
/** How many routing decisions one audit read asks for. */
const ROUTING_LIMIT = 100;

/** What the detail reads, each reply parsed once; every read carries the instant the server checked it. */
export type TaskDetailClient = {
  task(runId: string, signal: AbortSignal): Promise<{ value: Task; verifiedAtMs: number | null }>;
  workflow(runId: string): Promise<{ value: Workflow; verifiedAtMs: number | null }>;
  audit(runId: string): Promise<{ value: { audit: Audit; routing: RoutingEntry[] }; verifiedAtMs: number | null }>;
  inquiries(runId: string): Promise<{ value: Inquiries; verifiedAtMs: number | null }>;
};

export function createTaskDetailClient(api: ConsoleApi, csrfToken: string): TaskDetailClient {
  const verified = (reply: unknown) => api.readVerifiedAt?.(reply) ?? null;
  return {
    async task(runId, signal) {
      const reply = await api.task(runId, signal);
      return { value: parseTask(reply, runId), verifiedAtMs: verified(reply) };
    },
    async workflow(runId) {
      const reply = await api.command("workflow_get", { runId }, csrfToken);
      return { value: parseWorkflow(reply, runId), verifiedAtMs: verified(reply) };
    },
    async audit(runId) {
      const reply = await api.command("workflow_get", { runId, includeAudit: true, routingHistory: { limit: ROUTING_LIMIT } }, csrfToken);
      return { value: parseAudit(reply, runId), verifiedAtMs: verified(reply) };
    },
    async inquiries(runId) {
      const reply = await api.command("message_list", { runId, limit: INQUIRY_LIMIT }, csrfToken);
      return { value: parseInquiries(reply, INQUIRY_LIMIT), verifiedAtMs: verified(reply) };
    },
  };
}
