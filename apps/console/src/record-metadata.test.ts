import { describe, expect, it, vi } from "vitest";
import { recordMetadata, recordSummary } from "./record-metadata";
import { createApi } from "./api";

const metadata = () => ({ kind: "decision", sourceHostId: null, currentHostId: null, parentRunId: null,
  rootRunId: null, rootLeadRunId: null, project: null, buddy: { harness: "codex", provider: "openai", model: "router", effort: "high" },
  routingTask: { runId: "micro", title: "Task title", titleSource: "title", macroTaskId: "macro", macroTaskTitle: "Macro task title" } });

describe("record metadata at the read boundary", () => {
  it("accepts a routing association and null projects from the service", () => {
    expect(recordMetadata(metadata())).toBe(true);
    expect(recordMetadata({ ...metadata(), routingTask: null, buddy: null })).toBe(true);
  });
  it.each([undefined, [], { ...metadata(), project: undefined }, { ...metadata(), buddy: "router" },
    { ...metadata(), routingTask: {} }, { ...metadata(), routingTask: undefined },
    { ...metadata(), routingTask: { ...metadata().routingTask, titleSource: "guessed" } },
    { ...metadata(), project: { id: "p", label: null, path: null } }])("refuses an incomplete or malformed lineage", value => {
    expect(recordMetadata(value)).toBe(false);
  });
  it("rejects malformed titles and dates before the list renders", () => {
    const row = { runId: "route", task: "Router task", status: "completed", revision: 1, createdAt: "2026-10-17T00:00:00Z", lineage: metadata() };
    expect(recordSummary(row)).toBe(true);
    expect(recordSummary({ ...row, createdAt: "invalid" })).toBe(false);
    for (const title of [7, false, { text: "object" }, ["array"]])
      expect(recordSummary({ ...row, workflow: { state: "accepted", title, macroTaskId: null } })).toBe(false);
    expect(recordSummary({ ...row, workflow: { state: "accepted", title: null, macroTaskId: null } })).toBe(true);
  });
  it("the list refuses malformed rows before rendering and accepts the server's null project", async () => {
    const row = { runId: "route", revision: 1, task: "Router task", status: "completed", createdAt: "2026-10-17T00:00:00Z", lineage: metadata() };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValueOnce(new Response(JSON.stringify({ runs: [row], total: 1, nextCursor: null })))
      .mockResolvedValueOnce(new Response(JSON.stringify({ runs: [{ ...row, lineage: { ...row.lineage, routingTask: {} } }], total: 1, nextCursor: null })));
    const api = createApi("", fetcher);
    await expect(api.tasks({})).resolves.toMatchObject({ runs: [row] });
    await expect(api.tasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });
});
