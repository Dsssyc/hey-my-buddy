import type { ConsoleApi } from "../api";
import { parseAudit, parseBasis, parseInquiries, parseRoutingPage, parseTask, parseWorkflow } from "./parse";
import type { Audit, Basis, Inquiries, RoutingEntry, TaskView, Workflow } from "./types";

/** How many inquiries one read asks for; as many back means earlier ones may not be listed. */
const INQUIRY_LIMIT = 200;
/** How many routing decisions one page of the history asks for. */
const ROUTING_LIMIT = 100;

/** One read: what was parsed, and the instant the server checked it (null when the reply carries none). */
export type Read<T> = { value: T; verifiedAtMs: number | null };

/** What the detail reads, each reply parsed once. */
export type TaskDetailClient = {
  task(runId: string, signal: AbortSignal): Promise<Read<TaskView>>;
  workflow(runId: string): Promise<Read<Workflow>>;
  /** The audit, and the routing decisions of the micro task down to those named, or to the oldest. */
  audit(runId: string, decisionIds: string[]): Promise<Read<{ audit: Audit; routing: RoutingEntry[] }>>;
  inquiries(runId: string): Promise<Read<Inquiries>>;
  basis(decisionId: string): Promise<Read<Basis>>;
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
    async audit(runId, decisionIds) {
      const reply = await api.command("workflow_get", { runId, includeAudit: true, routingHistory: { limit: ROUTING_LIMIT } }, csrfToken);
      const { audit, routing } = parseAudit(reply, runId);
      const entries = [...routing.entries];
      let cursor = routing.nextCursor;
      while (cursor !== null && decisionIds.some(id => !entries.some(entry => entry.decisionId === id))) {
        const page = parseRoutingPage(await api.command("workflow_get", { runId, routingHistory: { limit: ROUTING_LIMIT, before: cursor } }, csrfToken), runId);
        entries.push(...page.entries);
        cursor = page.nextCursor;
      }
      return { value: { audit, routing: entries }, verifiedAtMs: verified(reply) };
    },
    async inquiries(runId) {
      const reply = await api.command("message_list", { runId, limit: INQUIRY_LIMIT }, csrfToken);
      return { value: parseInquiries(reply, INQUIRY_LIMIT), verifiedAtMs: verified(reply) };
    },
    async basis(decisionId) {
      const reply = await api.command("selection_get", { decisionId, includeAudit: true }, csrfToken);
      return { value: parseBasis(reply, decisionId), verifiedAtMs: verified(reply) };
    },
  };
}
