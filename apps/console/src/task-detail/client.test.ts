import { describe, expect, it, vi } from "vitest";
import type { ConsoleApi } from "../api";
import { createTaskDetailClient } from "./client";

const entry = (id: string) => ({ decisionId: id, status: "completed", source: "model-selection", selectedBuddy: null, routerBuddy: null, routingMode: "fast", reason: null });
const reply = (entries: string[], nextCursor: number | null, audit = true) => ({
  runId: "m1", revision: 1, routingHistory: { entries: entries.map(entry), nextCursor, total: 9 },
  ...(audit ? { audit: { turns: [], continuations: [], requests: [], hostConclusions: [] } } : {}),
});

function api(pages: unknown[]) {
  const command = vi.fn(async () => pages.shift());
  return { client: createTaskDetailClient({ command, readVerifiedAt: () => 7 } as unknown as ConsoleApi, "csrf"), command };
}

describe("the routing decisions of a micro task", () => {
  it("are read page after page until every one the timeline names is found", async () => {
    const { client, command } = api([reply(["d3", "d2"], 20), reply(["d1"], 10, false), reply(["d0"], null, false)]);
    const { value, verifiedAtMs } = await client.audit("m1", ["d2", "d1"]);
    expect(value.routing.map(item => item.decisionId)).toEqual(["d3", "d2", "d1"]);
    expect(verifiedAtMs).toBe(7);
    expect(command.mock.calls).toEqual([
      ["workflow_get", { runId: "m1", includeAudit: true, routingHistory: { limit: 100 } }, "csrf"],
      ["workflow_get", { runId: "m1", routingHistory: { limit: 100, before: 20 } }, "csrf"],
    ]);
  });

  it("are read in one page when it holds them all, and to the end when one is not on the board", async () => {
    const first = api([reply(["d2", "d1"], 5)]);
    await first.client.audit("m1", ["d1"]);
    expect(first.command).toHaveBeenCalledTimes(1);
    const missing = api([reply(["d2"], 5), reply(["d1"], null, false)]);
    const { value } = await missing.client.audit("m1", ["gone"]);
    expect(value.routing.map(item => item.decisionId)).toEqual(["d2", "d1"]);
    expect(missing.command).toHaveBeenCalledTimes(2);
    const none = api([reply(["d2"], 5)]);
    await none.client.audit("m1", []);
    expect(none.command).toHaveBeenCalledTimes(1);
  });

  it("read the bounds of a decision with its audit, and no more", async () => {
    const command = vi.fn(async () => ({ decision: { decisionId: "d1", constraints: { harness: "dsh" } } }));
    const client = createTaskDetailClient({ command, readVerifiedAt: () => null } as unknown as ConsoleApi, "csrf");
    expect((await client.basis("d1")).value.constraints).toEqual({ harness: "dsh" });
    expect(command).toHaveBeenCalledExactlyOnceWith("selection_get", { decisionId: "d1", includeAudit: true }, "csrf");
  });
});

describe("the inquiries of a micro task", () => {
  it("are asked for as many as the console may, and read through the one operation that lists them", async () => {
    const command = vi.fn(async () => ({ messages: [] }));
    const client = createTaskDetailClient({ command, readVerifiedAt: () => null } as unknown as ConsoleApi, "csrf");
    expect((await client.inquiries("m1")).value).toEqual({ items: [], more: false });
    expect(command).toHaveBeenCalledExactlyOnceWith("message_list", { runId: "m1", limit: 200 }, "csrf");
  });
});
