import { z } from "zod";
import { ApiError } from "../api";

const meta = z.object({ nativeItemId: z.string().min(1), contentSegment: z.string().min(1), replace: z.boolean().optional(), nativeType: z.string().optional(), phase: z.string().optional(), marker: z.boolean().optional(), reasoning: z.enum(["summary", "text"]).optional(), partIndex: z.number().int().optional() });
const content = z.object({ type: z.literal("text"), text: z.string() });
const message = z.object({ sessionUpdate: z.enum(["user_message_chunk", "agent_message_chunk", "agent_thought_chunk"]), content, _meta: meta.optional() });
const tool = z.object({ sessionUpdate: z.enum(["tool_call", "tool_call_update"]), toolCallId: z.string(), title: z.string().optional(), kind: z.enum(["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "switch_mode", "other"]).optional(), status: z.enum(["pending", "in_progress", "completed", "failed"]).optional(), rawInput: z.json().optional(), rawOutput: z.json().optional(), content: z.array(z.object({ type: z.literal("content"), content })).optional(), locations: z.array(z.object({ path: z.string() })).optional(), _meta: meta.optional() });
const plan = z.object({ sessionUpdate: z.literal("plan"), entries: z.array(z.object({ content: z.string(), status: z.enum(["pending", "in_progress", "completed"]), priority: z.enum(["high", "medium", "low"]) })) });
const notification = z.object({ sessionId: z.string(), update: z.union([message, tool, plan]) });
const page = z.object({ state: z.enum(["running", "ended"]), omitted: z.array(z.object({ code: z.string(), params: z.record(z.string(), z.unknown()) })), events: z.array(notification), before: z.number().int().nonnegative(), after: z.number().int().nonnegative(), hasEarlier: z.boolean() });
export type SessionPage = z.infer<typeof page>;
export type SessionEvent = z.infer<typeof notification>;
export type MessageUpdate = z.infer<typeof message>;
export type ToolUpdate = z.infer<typeof tool>;
export type PlanUpdate = z.infer<typeof plan>;
export type RecordedEvent = { id: string; event: SessionEvent };
export type SessionPosition = { before: number; scrollTop: number; atBottom: boolean; focus: number | null; expanded: string[] };

/** The HTTP response is the single boundary; renderers receive parsed facts. */
export function parseSession(value: unknown): SessionPage {
  const result = page.safeParse(value);
  if (!result.success || result.data.before > result.data.after) throw new ApiError("INVALID_RESPONSE", "Invalid session response");
  return result.data;
}
export function records(page: SessionPage): RecordedEvent[] {
  return page.events.map((event, index) => ({ id: `${page.before}:${index}`, event }));
}
