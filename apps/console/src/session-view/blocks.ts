import type { MessageUpdate, PlanUpdate, RecordedEvent, ToolUpdate } from "./types";

type MessageBlock = { type: "message"; key: string; update: MessageUpdate };
type ToolBlock = { type: "tool"; key: string; update: ToolUpdate; segments: Map<string, string> };
type PlanBlock = { type: "plan"; key: string; update: PlanUpdate };
export type SessionBlock = MessageBlock | ToolBlock | PlanBlock;

/** Projection v1 appends within an item/segment; only an explicit replace is a snapshot. */
export function sessionBlocks(events: RecordedEvent[]): SessionBlock[] {
  const blocks: SessionBlock[] = [];
  const messages = new Map<string, MessageBlock>();
  const tools = new Map<string, ToolBlock>();
  for (const { id, event: { update } } of events) {
    if (update.sessionUpdate === "plan") {
      blocks.push({ type: "plan", key: id, update });
    } else if (update.sessionUpdate === "tool_call" || update.sessionUpdate === "tool_call_update") {
      let block = tools.get(update.toolCallId);
      if (!block) {
        block = { type: "tool", key: `tool:${update.toolCallId}`, update: { ...update }, segments: new Map() };
        tools.set(update.toolCallId, block);
        blocks.push(block);
      }
      block.update = { ...block.update, ...update };
      if (update.content) {
        const key = update._meta ? JSON.stringify([update._meta.nativeItemId, update._meta.contentSegment]) : id;
        const text = update.content.map(item => item.content.text).join("");
        block.segments.set(key, (update._meta?.replace ? "" : block.segments.get(key) ?? "") + text);
      }
    } else {
      const chunk = update as MessageUpdate;
      const key = chunk._meta ? JSON.stringify([chunk._meta.nativeItemId, chunk._meta.contentSegment]) : id;
      const previous = messages.get(key);
      if (previous) {
        previous.update = { ...chunk, content: { type: "text", text: (chunk._meta?.replace ? "" : previous.update.content.text) + chunk.content.text } };
      } else {
        const block: MessageBlock = { type: "message", key, update: { ...chunk, content: { ...chunk.content } } };
        messages.set(key, block);
        blocks.push(block);
      }
    }
  }
  return blocks;
}
