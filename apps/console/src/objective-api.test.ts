import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import type { ObjectivePage, ObjectiveTimeline } from "./objective-types";
import { objectiveTimelineFixture } from "./objective-fixtures";

describe("work-objective reads", () => {
  it("sends list filters through GET /api/objectives with the caller's abort signal", async () => {
    const page: ObjectivePage = { objectives: [], total: 7, nextCursor: "keyset+cursor/==", cursor: 41, changed: true };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(page)));
    const api = createApi("/private/", fetcher);
    const controller = new AbortController();
    await expect(api.objectives({ limit: 50, before: "keyset+cursor/==", projectId: "repository:a", hostId: "Host B",
      query: "时间轴", filter: "review" }, controller.signal)).resolves.toEqual(page);
    const [location, init] = fetcher.mock.calls[0];
    const url = new URL(String(location), "http://localhost");
    expect(url.pathname).toBe("/private/api/objectives");
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "50", before: "keyset+cursor/==",
      projectId: "repository:a", hostId: "Host B", query: "时间轴", filter: "review" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(init?.method || "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("omits empty optional list parameters instead of sending blank filters", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })));
    const api = createApi("/private", fetcher);
    await api.objectives({ query: "", projectId: "", hostId: "", filter: "all" });
    const url = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
    expect(Object.fromEntries(url.searchParams)).toEqual({ filter: "all" });
  });

  it("rejects incomplete list envelopes instead of treating them as an empty page", async () => {
    for (const payload of [{ objectives: [], total: 0 }, { objectives: {}, total: 0, nextCursor: null, cursor: 0, changed: false },
      { objectives: [], total: "0", nextCursor: null, cursor: 0, changed: false },
      { objectives: [], total: 0, nextCursor: 1, cursor: 0, changed: false },
      { objectives: [], total: 0, nextCursor: null, cursor: 0 }]) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.objectives({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("reads one objective's timeline from its encoded path with the bounded limit", async () => {
    const timeline = objectiveTimelineFixture();
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(timeline)));
    const api = createApi("/private/", fetcher);
    const controller = new AbortController();
    await expect(api.objectiveTimeline("obj/a+b", { limit: 200 }, controller.signal)).resolves.toMatchObject({
      objective: { objectiveId: "obj-1" }, totals: { rows: 6, allRows: 6 },
    });
    const [location, init] = fetcher.mock.calls[0];
    const url = new URL(String(location), "http://localhost");
    expect(url.pathname).toBe("/private/api/objectives/obj%2Fa%2Bb/timeline");
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "200" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal: controller.signal });
  });

  it("rejects incomplete timeline envelopes without rendering partial facts", async () => {
    const good = objectiveTimelineFixture();
    const variants: unknown[] = [
      { ...good, rows: {} }, { ...good, spans: null }, { ...good, events: "none" },
      { ...good, observedAt: null }, { ...good, totals: { rows: 6, allRows: 6 } },
      { ...good, truncated: {} }, { ...good, scopeComplete: "yes" }, { ...good, filtered: null },
      { ...good, objective: null },
    ];
    for (const payload of variants) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.objectiveTimeline("obj-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("surfaces the service error code for a refused read", async () => {
    const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(
      { ok: false, error: { code: "NOT_FOUND", message: "no such objective" } }), { status: 404 })) as typeof fetch);
    await expect(api.objectiveTimeline("run:gone", {})).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("keeps an aborted timeline read instead of reporting a connection failure", async () => {
    const cancelled = new DOMException("cancelled", "AbortError");
    const api = createApi("/private", vi.fn<typeof fetch>(async () => { throw cancelled; }) as typeof fetch);
    await expect(api.objectiveTimeline("obj-1", {}, new AbortController().signal)).rejects.toBe(cancelled);
  });

  it("types the fixture through the read contract", () => {
    const timeline: ObjectiveTimeline = objectiveTimelineFixture();
    expect(timeline.rows.map(row => row.runId)).toEqual(["r1", "r2", "r3", "r5", "r4", "r6"]);
    expect(timeline.spans.every(span => typeof span.spanId === "string")).toBe(true);
  });
});
