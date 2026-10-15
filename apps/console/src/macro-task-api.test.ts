import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import type { MacroTaskPage, MacroTaskTimeline } from "./macro-task-types";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";

describe("work macro task reads", () => {
  it("reports macroTask list verification from each HTTP response without changing its envelope", async () => {
    const page: MacroTaskPage = { macroTasks: [], total: 0, nextCursor: null, cursor: 41, changed: false };
    const firstDate = "Fri, 09 Oct 2026 18:00:00 GMT";
    const secondDate = "Fri, 09 Oct 2026 18:05:00 GMT";
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(page), { headers: { Date: firstDate, ETag: '"macroTasks"' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: secondDate } }));
    const api = createApi("/private", fetcher);
    const first = await api.macroTasks({ limit: 50 });
    const second = await api.macroTasks({ limit: 50 });
    expect(first).toEqual(page);
    expect(second).toEqual(page);
    expect(api.readVerifiedAt(first)).toBe(Date.parse(firstDate));
    expect(api.readVerifiedAt(second)).toBe(Date.parse(secondDate));
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({ "If-None-Match": '"macroTasks"' });
  });
  it("sends list filters through GET /api/macro-tasks with the caller's abort signal", async () => {
    const page: MacroTaskPage = { macroTasks: [], total: 7, nextCursor: "keyset+cursor/==", cursor: 41, changed: true };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(page)));
    const api = createApi("/private/", fetcher);
    const controller = new AbortController();
    await expect(api.macroTasks({ limit: 50, before: "keyset+cursor/==", projectId: "repository:a", hostId: "Host B",
      query: "时间轴", filter: "review" }, controller.signal)).resolves.toEqual(page);
    const [location, init] = fetcher.mock.calls[0];
    const url = new URL(String(location), "http://localhost");
    expect(url.pathname).toBe("/private/api/macro-tasks");
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "50", before: "keyset+cursor/==",
      projectId: "repository:a", hostId: "Host B", query: "时间轴", filter: "review" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(init?.method || "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("omits empty optional list parameters instead of sending blank filters", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })));
    const api = createApi("/private", fetcher);
    await api.macroTasks({ query: "", projectId: "", hostId: "", filter: "all" });
    const url = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
    expect(Object.fromEntries(url.searchParams)).toEqual({ filter: "all" });
  });

  it("rejects incomplete list envelopes instead of treating them as an empty page", async () => {
    for (const payload of [{ macroTasks: [], total: 0 }, { macroTasks: {}, total: 0, nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], total: "0", nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], total: 0, nextCursor: 1, cursor: 0, changed: false },
      { macroTasks: [], total: 0, nextCursor: null, cursor: 0 }]) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.macroTasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("accepts the 0.15.1 nullable description and accepted count, refusing malformed ones", async () => {
    const good = macroTaskTimelineFixture();
    const withDescription = { ...good, macroTask: { ...good.macroTask, description: "用户口述的目标描述。", counts: { ...good.macroTask.counts, accepted: 2 } } };
    // The read resolves to {timeline, verifiedAtMs}; verifiedAtMs is the
    // response's own HTTP Date, pinned here with a fixed header value.
    const verifiedAtMs = Date.parse("Mon, 05 Oct 2026 02:30:00 GMT");
    const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(withDescription),
      { headers: { Date: new Date(verifiedAtMs).toUTCString() } })) as typeof fetch);
    await expect(api.macroTaskTimeline("macro-1", {})).resolves.toMatchObject({
      timeline: { macroTask: { description: "用户口述的目标描述。", counts: { accepted: 2 } },
        rows: expect.any(Array) },
      verifiedAtMs,
    });
    for (const malformed of [
      { ...good, macroTask: { ...good.macroTask, description: 7 } },
      { ...good, macroTask: { ...good.macroTask, counts: { ...good.macroTask.counts, accepted: "2" } } },
      { ...good, macroTask: { ...good.macroTask, counts: { roots: 1, helpers: 0, active: 0, host: 0, review: 0, ended: 0 } } },
      { ...good, rows: good.rows.map(row => ({ ...row, taskSummary: 5 })) },
    ]) {
      const reader = createApi("/private", vi.fn(async () => new Response(JSON.stringify(malformed))) as typeof fetch);
      await expect(reader.macroTaskTimeline("macro-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    const badList = { macroTasks: [{ ...good.macroTask, description: [] }], total: 1, nextCursor: null, cursor: 0, changed: false };
    const listApi = createApi("/private", vi.fn(async () => new Response(JSON.stringify(badList))) as typeof fetch);
    await expect(listApi.macroTasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("reads one macroTask's timeline from its encoded path with the bounded limit", async () => {
    const timeline = macroTaskTimelineFixture();
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(timeline)));
    const api = createApi("/private/", fetcher);
    const controller = new AbortController();
    await expect(api.macroTaskTimeline("obj/a+b", { limit: 200 }, controller.signal)).resolves.toMatchObject({
      timeline: { macroTask: { macroTaskId: "macro-1" }, totals: { rows: 6, allRows: 6 } },
      // This fixture response carries no HTTP Date, so the client's
      // verification time is the documented null fallback.
      verifiedAtMs: null,
    });
    const [location, init] = fetcher.mock.calls[0];
    const url = new URL(String(location), "http://localhost");
    expect(url.pathname).toBe("/private/api/macro-tasks/obj%2Fa%2Bb/timeline");
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: "200" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal: controller.signal });
  });

  it("rejects incomplete timeline envelopes without rendering partial facts", async () => {
    const good = macroTaskTimelineFixture();
    const variants: unknown[] = [
      { ...good, rows: {} }, { ...good, spans: null }, { ...good, events: "none" },
      { ...good, observedAt: null }, { ...good, totals: { rows: 6, allRows: 6 } },
      { ...good, truncated: {} }, { ...good, scopeComplete: "yes" }, { ...good, filtered: null },
      { ...good, macroTask: null },
    ];
    for (const payload of variants) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.macroTaskTimeline("macro-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("accepts valid fix links and rejects malformed optional row relations at the response boundary", async () => {
    const good = macroTaskTimelineFixture();
    good.rows[0]!.fixedBy = ["r2"];
    good.rows[1]!.fixes = "r1";
    const reader = createApi("/private", vi.fn(async () => new Response(JSON.stringify(good))) as typeof fetch);
    const result = await reader.macroTaskTimeline("macro-1", {});
    expect(result.timeline.rows[0]).toMatchObject({ fixedBy: ["r2"] });
    expect(result.timeline.rows[1]).toMatchObject({ fixes: "r1" });
    for (const relation of [{ fixes: "" }, { fixes: null }, { fixes: 4 },
      { fixedBy: null }, { fixedBy: [] }, { fixedBy: ["r2", ""] }, { fixedBy: "r2" }]) {
      const malformed = { ...good, rows: [{ ...good.rows[0], ...relation }, ...good.rows.slice(1)] };
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(malformed))) as typeof fetch);
      await expect(api.macroTaskTimeline("macro-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("surfaces the service error code for a refused read", async () => {
    const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(
      { ok: false, error: { code: "NOT_FOUND", message: "no such objective" } }), { status: 404 })) as typeof fetch);
    await expect(api.macroTaskTimeline("run:gone", {})).rejects.toMatchObject({ code: "NOT_FOUND" });
  });

  it("keeps an aborted timeline read instead of reporting a connection failure", async () => {
    const cancelled = new DOMException("cancelled", "AbortError");
    const api = createApi("/private", vi.fn<typeof fetch>(async () => { throw cancelled; }) as typeof fetch);
    await expect(api.macroTaskTimeline("macro-1", {}, new AbortController().signal)).rejects.toBe(cancelled);
  });

  it("types the fixture through the read contract", () => {
    const timeline: MacroTaskTimeline = macroTaskTimelineFixture();
    expect(timeline.rows.map(row => row.runId)).toEqual(["r1", "r2", "r3", "r5", "r4", "r6"]);
    expect(timeline.spans.every(span => typeof span.spanId === "string")).toBe(true);
  });
});
