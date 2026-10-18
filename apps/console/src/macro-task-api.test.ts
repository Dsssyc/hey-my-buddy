import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";
import type { MacroTaskPage, MacroTaskTimeline } from "./macro-task-types";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";

describe("work macro task reads", () => {
  it("reads null titles and event facts from the current service without labels or summaries", async () => {
    const timeline = macroTaskTimelineFixture();
    timeline.macroTask.title = null;
    timeline.macroTask.titleSource = "none";
    timeline.rows[0] = { ...timeline.rows[0]!, title: null, titleSource: "none", fixes: "original", fixesTitle: null, fixesTitleSource: "none" };
    timeline.events[0] = { ...timeline.events[0]!, decision: "approve", verdict: null, state: null, strategy: null, note: "Host 原文" };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(timeline)));
    const result = await createApi("/private", fetcher).macroTaskTimeline(timeline.macroTask.macroTaskId, {});
    expect(result.timeline).toEqual(timeline);
    expect(result.timeline.events[0]).not.toHaveProperty("label");
    expect(result.timeline.events[0]).not.toHaveProperty("summary");
  });
  it("reports macroTask list verification from each HTTP response without changing its envelope", async () => {
    const page: MacroTaskPage = { macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 41, changed: false };
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
    const page: MacroTaskPage = { macroTasks: [], totals: { all: 7, active: 3, host: 2, review: 1 }, nextCursor: "keyset+cursor/==", cursor: 41, changed: true };
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
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })));
    const api = createApi("/private", fetcher);
    await api.macroTasks({ query: "", projectId: "", hostId: "", filter: "all" });
    const url = new URL(String(fetcher.mock.calls[0][0]), "http://localhost");
    expect(Object.fromEntries(url.searchParams)).toEqual({ filter: "all" });
  });

  it("rejects incomplete list envelopes instead of treating them as an empty page", async () => {
    const totals = { all: 0, active: 0, host: 0, review: 0 };
    for (const payload of [{ macroTasks: [], totals }, { macroTasks: {}, totals, nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], totals: { ...totals, all: "0" }, nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], totals: { ...totals, review: -1 }, nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], totals: { all: 0, active: 0, host: 0 }, nextCursor: null, cursor: 0, changed: false },
      { macroTasks: [], totals, nextCursor: 1, cursor: 0, changed: false },
      { macroTasks: [], totals, nextCursor: null, cursor: 0 }]) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.macroTasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("accepts nullable descriptions and root outcome counts, refusing malformed ones", async () => {
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
      { ...good, macroTask: { ...good.macroTask, counts: { ...good.macroTask.counts, failed: undefined } } },
      { ...good, macroTask: { ...good.macroTask, counts: { ...good.macroTask.counts, failed: -1 } } },
      { ...good, macroTask: { ...good.macroTask, counts: { roots: 1, helpers: 0, active: 0, host: 0, review: 0, ended: 0 } } },
      { ...good, rows: good.rows.map(row => ({ ...row, taskSummary: 5 })) },
    ]) {
      const reader = createApi("/private", vi.fn(async () => new Response(JSON.stringify(malformed))) as typeof fetch);
      await expect(reader.macroTaskTimeline("macro-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    const badList = { macroTasks: [{ ...good.macroTask, description: [] }], totals: { all: 1, active: 1, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false };
    const listApi = createApi("/private", vi.fn(async () => new Response(JSON.stringify(badList))) as typeof fetch);
    await expect(listApi.macroTasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    for (const malformed of [
      { ...good.macroTask, summary: undefined },
      { ...good.macroTask, sourceHostId: undefined },
      { ...good.macroTask, project: { ...good.macroTask.project, path: 4 } },
      { ...good.macroTask, counts: { ...good.macroTask.counts, host: -1 } },
      { ...good.macroTask, counts: { ...good.macroTask.counts, failed: "1" } },
    ]) {
      const reader = createApi("/private", vi.fn(async () => new Response(JSON.stringify({
        macroTasks: [malformed], totals: { all: 1, active: 1, host: 0, review: 0 },
        nextCursor: null, cursor: 0, changed: false,
      }))) as typeof fetch);
      await expect(reader.macroTasks({})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("accepts nullable summary fields from the service's standalone projection", async () => {
    const source = macroTaskTimelineFixture().macroTask;
    const standalone = { ...source, kind: "standalone", titleSource: "none", description: null,
      summary: null, sourceHostId: null, project: null, createdAt: null, lastActivityAt: null };
    const payload = { macroTasks: [standalone], totals: { all: 1, active: 1, host: 0, review: 0 },
      nextCursor: null, cursor: 41, changed: false };
    const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
    await expect(api.macroTasks({})).resolves.toEqual(payload);
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

  it("reads execution timing, current reminders and Host inquiry metadata from timeline spans", async () => {
    const good = macroTaskTimelineFixture();
    const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(good))) as typeof fetch);
    const { timeline } = await api.macroTaskTimeline("macro-1", {});
    expect(timeline.spans.find(span => span.spanId === "s-r4-e")).toMatchObject({
      expectedSeconds: 1800,
      reminders: { overdue: true, possiblyStuck: false, lastNativeEventAt: "2026-09-26T08:10:00Z", toolInProgress: true },
      inquiries: [],
    });
    expect(timeline.spans.find(span => span.spanId === "s-r2-e1")?.inquiries).toEqual([
      { inquiryId: "inq-r2a", createdAt: "2026-09-26T01:50:00Z", author: "codex-desktop", state: "answered" },
    ]);
    expect(timeline.spans.find(span => span.spanId === "s-r2-e1")?.reminders).toBeNull();
    expect(timeline.spans.find(span => span.spanId === "s-r1-q")).toMatchObject({
      expectedSeconds: null, reminders: null, inquiries: null,
    });
    const incompleteTurn = macroTaskTimelineFixture();
    incompleteTurn.spans.find(span => span.spanId === "s-r2-e1")!.expectedSeconds = null;
    const incompleteReader = createApi("/private", vi.fn(async () => new Response(JSON.stringify(incompleteTurn))) as typeof fetch);
    await expect(incompleteReader.macroTaskTimeline("macro-1", {})).resolves.toMatchObject({
      timeline: { spans: expect.arrayContaining([expect.objectContaining({ spanId: "s-r2-e1", expectedSeconds: null })]) },
    });
  });

  it("rejects missing or malformed execution facts and non-execution projections", async () => {
    const good = macroTaskTimelineFixture();
    const execution = good.spans.find(span => span.spanId === "s-r4-e")!;
    const queue = good.spans[0]!;
    const variants = [
      { spanId: execution.spanId, expectedSeconds: undefined },
      { spanId: execution.spanId, expectedSeconds: 0 },
      { spanId: execution.spanId, expectedSeconds: 1.5 },
      { spanId: execution.spanId, reminders: undefined },
      { spanId: execution.spanId, reminders: { ...execution.reminders, overdue: "yes" } },
      { spanId: execution.spanId, reminders: { ...execution.reminders, lastNativeEventAt: undefined } },
      { spanId: execution.spanId, reminders: { ...execution.reminders, lastNativeEventAt: "invalid" } },
      { spanId: execution.spanId, reminders: { ...execution.reminders, toolInProgress: undefined } },
      { spanId: execution.spanId, inquiries: undefined },
      { spanId: execution.spanId, inquiries: null },
      { spanId: execution.spanId, inquiries: [{ inquiryId: "i", createdAt: "now", author: "host" }] },
      { spanId: execution.spanId, inquiries: [{ inquiryId: "i", createdAt: "invalid", author: "host", state: "queued" }] },
      { spanId: queue.spanId, expectedSeconds: undefined },
      { spanId: queue.spanId, reminders: { overdue: false, possiblyStuck: false, lastNativeEventAt: null, toolInProgress: false } },
      { spanId: queue.spanId, inquiries: [] },
    ];
    for (const variant of variants) {
      const malformed = { ...good, spans: good.spans.map(span => span.spanId === variant.spanId ? { ...span, ...variant } : span) };
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(malformed))) as typeof fetch);
      await expect(api.macroTaskTimeline("macro-1", {})).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("accepts valid fix links and rejects malformed optional row relations at the response boundary", async () => {
    const good = macroTaskTimelineFixture();
    good.rows[0]!.fixedBy = ["r2"];
    Object.assign(good.rows[1]!, { fixes: "r1", fixesTitle: "目标任务", fixesTitleSource: "task" });
    const reader = createApi("/private", vi.fn(async () => new Response(JSON.stringify(good))) as typeof fetch);
    const result = await reader.macroTaskTimeline("macro-1", {});
    expect(result.timeline.rows[0]).toMatchObject({ fixedBy: ["r2"] });
    expect(result.timeline.rows[1]).toMatchObject({ fixes: "r1", fixesTitle: "目标任务", fixesTitleSource: "task" });
    for (const relation of [{ fixes: "" }, { fixes: null }, { fixes: 4 }, { fixes: "r1" }, { fixes: "r1", fixesTitle: 3, fixesTitleSource: "title" },
      { fixes: "r1", fixesTitle: "目标", fixesTitleSource: "other" }, { fixesTitle: "orphan" }, { fixesTitleSource: "title" },
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
