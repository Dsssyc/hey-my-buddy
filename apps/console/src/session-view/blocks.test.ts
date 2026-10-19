import { describe, expect, it } from "vitest";
import { parseSession, records } from "./types";
import { sessionBlocks } from "./blocks";

const message = (text: string, replace = false, segment = "answer") => ({ sessionId: "attempt", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text }, _meta: { nativeItemId: "item", contentSegment: segment, replace } } });
const page = (events: unknown[]) => parseSession({ state: "ended", omitted: [], events, before: 10, after: 100, hasEarlier: false });

describe("session projection blocks", () => {
  it("reconstructs a message across page boundaries and replaces its revised snapshot without changing its position", () => {
    const early = records(page([message("The old ")]));
    const late = records(parseSession({ ...page([message("answer"), message("The revised answer", true), message("A separate segment", false, "other")]), before: 100, after: 200 }));
    const blocks = sessionBlocks([...early, ...late]);
    expect(blocks.map(block => block.type === "message" && block.update.content.text)).toEqual(["The revised answer", "A separate segment"]);
    expect(early[0].event.update).toEqual(message("The old ").update);
  });
  it("keeps tool identity and all segments when a page begins with tool updates; latest snapshots replace only their own segment", () => {
    const tool = (text: string, segment: string, replace = false, fields = {}) => ({ sessionId: "attempt", update: { sessionUpdate: "tool_call_update", toolCallId: "call", content: [{ type: "content", content: { type: "text", text } }], _meta: { nativeItemId: "call", contentSegment: segment, replace }, ...fields } });
    const blocks = sessionBlocks(records(page([tool("first ", "stdout", false, { title: "command", status: "in_progress", rawInput: { command: "test" } }), tool("second", "stdout"), tool("diagnostic", "stderr"), tool("complete snapshot", "stdout", true, { status: "completed", rawOutput: { exitCode: 0 } })])));
    expect(blocks).toHaveLength(1);
    const block = blocks[0];
    expect(block.type).toBe("tool");
    if (block.type !== "tool") throw Error("Expected tool");
    expect([...block.segments.values()]).toEqual(["complete snapshot", "diagnostic"]);
    expect(block.update).toMatchObject({ title: "command", status: "completed", rawInput: { command: "test" }, rawOutput: { exitCode: 0 } });
  });
  it("refuses a malformed event instead of silently dropping it", () => {
    expect(() => page([message("valid"), { sessionId: "attempt", update: { sessionUpdate: "unknown" } }])).toThrow("Invalid session response");
    expect(() => parseSession({ ...page([]), before: 100, after: 10 })).toThrow();
  });
});
