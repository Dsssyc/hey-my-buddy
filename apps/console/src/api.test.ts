import i18n from "i18next";
import { describe, expect, it, vi } from "vitest";
import { ApiError, createApi, errorText, parseRoutingHealth, readVerificationText, validRoutingConfiguration } from "./api";
import { macroTaskTimelineFixture, OBSERVED_AT } from "./macro-task-fixtures";

describe("L4 ordered Router list configuration", () => {
  const current = { routerBuddyIds: [], routerRetryIntervalSeconds: 600,
    defaultRoutingMode: "fast", routingBudget: "brief" };
  it("accepts the list settings and rejects retired slots, selectors or intervals", () => {
    expect(validRoutingConfiguration(current)).toBe(true);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "review", routerBuddyIds: ["review", "fast"] })).toBe(true);
    // An empty list is an explicit clear; duplicates are a board refusal.
    expect(validRoutingConfiguration({ ...current, routerBuddyIds: [] })).toBe(true);
    expect(validRoutingConfiguration({ ...current, routerBuddyIds: ["a", "a"] })).toBe(false);
    for (const retired of [{ decisionBuddyId: "old" }, { routerBuddyId: "old" },
      { fastRouterBuddyId: "old" }, { reviewRouterBuddyId: "old" },
      { fastRouterBuddyId: null, reviewRouterBuddyId: null }]) {
      expect(validRoutingConfiguration({ ...current, ...retired })).toBe(false);
    }
    expect(validRoutingConfiguration({ ...current, routingBudget: "quick" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "other" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerBuddyIds: undefined })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerBuddyIds: [""] })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerBuddyIds: "a" })).toBe(false);
    for (const interval of [0, -1, 1.5, NaN, 2147483648, "600", true, null]) {
      expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: interval })).toBe(false);
    }
    expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: 1 })).toBe(true);
    expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: 2147483647 })).toBe(true);
  });
});

describe("snapshot upgrade boundary and routing health", () => {
  const configuration = { routerBuddyIds: ["head"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast", routingBudget: "standard" };
  const snapshot = { csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "session" }, configuration, buddies: [],
    preferences: [], modelServiceNotes: [], modelServiceConcurrency: [],
    tasks: { pendingCount: 0 } };
  const health = { available: false, reason: { code: "router-consecutive-failures", params: {} }, windowSize: 20, sampleCount: 3,
    failureCount: 3, consecutiveFailures: 3, abstentionCount: 0, cancelledCount: 0,
    lastSuccessAt: null, lastSuccessDecisionId: null,
    recentFailures: [{ decisionId: "timeout", runId: "run", at: "2026-10-02T00:00:00Z", code: "router-timeout" }] };
  function apiFor(body: unknown) {
    return createApi("", vi.fn(async () => new Response(JSON.stringify(body))) as typeof fetch);
  }
  it("refuses an unavailable Router configuration", async () => {
    await expect(apiFor({ ...snapshot, configuration: null }).snapshot())
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });
  it("reads nullable preference notes and refuses a malformed preference publication view", async () => {
    for (const note of [null, "", " 用户备注 "]) {
      const preferences = [{ buddyId: "dsh:p:m:off", mode: "prefer", note }];
      await expect(apiFor({ ...snapshot, preferences }).snapshot()).resolves.toMatchObject({ preferences });
    }
    for (const preference of [
      { buddyId: "a", mode: "prefer", note: 1 },
      { buddyId: "a", mode: "prefer", reason: "old field" },
      { buddyId: "a", mode: "prefer", note: null, reason: "old field" },
      { buddyId: "a", mode: "other", note: null },
    ]) {
      await expect(apiFor({ ...snapshot, preferences: [preference] }).snapshot())
        .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });
  it("preserves snapshot parsing while reporting its own revalidation time", async () => {
    const date = "Fri, 09 Oct 2026 18:05:00 GMT";
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(new Response(JSON.stringify(snapshot), { headers: { ETag: '"snapshot"' } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: date } }));
    const api = createApi("", fetcher);
    const first = await api.snapshot();
    const second = await api.snapshot();
    expect(api.readVerifiedAt(first)).toBeNull();
    expect(api.readVerifiedAt(second)).toBe(Date.parse(date));
    expect(second).toEqual(first);
    expect(second.consoleSession).toEqual(snapshot.consoleSession);
  });
  it("refuses legacy single and dual slots at the live snapshot parser", async () => {
    await expect(apiFor({ ...snapshot, configuration: { ...configuration, routerBuddyId: "head" } }).snapshot())
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(apiFor({ ...snapshot, configuration: { ...configuration, fastRouterBuddyId: "fast", reviewRouterBuddyId: "review" } }).snapshot())
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });
  it("preserves service availability and timeout failures without calculating a threshold", async () => {
    await expect(apiFor({ ...snapshot, routingHealth: health }).snapshot()).resolves.toMatchObject({ routingHealth: health });
    expect(parseRoutingHealth({ ...health, available: true, reason: null })).toMatchObject({ available: true });
    expect(parseRoutingHealth({ ...health, consecutiveFailures: 0 })).toMatchObject({ available: false });
    for (const broken of [{ ...health, available: "false" }, { ...health, reason: 3 }, { ...health, recentFailures: {} }]) {
      await expect(apiFor({ ...snapshot, routingHealth: broken }).snapshot()).resolves.toMatchObject({ routingHealth: undefined });
    }
  });
  it("parses per-buddy health entries and drops only malformed health", async () => {
    const routers = [{
      buddyId: "head", index: 0, identity: { harness: "dsh", provider: "p", model: "m", effort: "max" },
      eligible: true, code: null, inSkipWindow: false, skipUntil: null, retryAt: null, retryInProgress: false,
      answeredCount: 2, noAnswerCount: 4, lastAnsweredAt: "2026-10-02T00:00:00Z", lastNoAnswerAt: "2026-10-02T01:00:00Z",
      consecutiveNoAnswers: 4, windowSize: 20, windowEntries: 6, windowAnsweredCount: 2, windowFailureCount: 4,
      windowBudgetExhaustedCount: 4, windowBoundsRejectedCount: 0, windowAttemptCount: 4,
      lastError: { code: "timeout", at: "2026-10-02T01:00:00Z", phase: "runtime" },
    }, { buddyId: "tail", index: 1, identity: null, eligible: false, code: "router-skip-window" }];
    const listHealth = { ...health, available: false, reason: { code: "router-skip-window", params: {} },
      currentRouterBuddyId: "tail", routers, unattributedCount: 1,
      unattributed: [{ decisionId: "dec-u", at: "2026-10-02T02:00:00Z", outcome: "unknown", code: null }] };
    await expect(apiFor({ ...snapshot, routingHealth: listHealth }).snapshot()).resolves.toMatchObject({ routingHealth: listHealth });
    // A malformed entry or an unknown-nature outcome stays unknown rather than
    // being read as success or failure: the whole health object is dropped.
    for (const broken of [
      { ...listHealth, routers: [{ ...routers[0], buddyId: "" }] },
      { ...listHealth, routers: [{ ...routers[0], index: -1 }] },
      { ...listHealth, routers: [{ ...routers[0], eligible: "yes" }] },
      { ...listHealth, routers: "head" },
      { ...listHealth, currentRouterBuddyId: 3 },
      { ...listHealth, unattributed: [{ decisionId: 4, at: "", outcome: "", code: null }] },
    ]) {
      expect(parseRoutingHealth(broken)).toBeUndefined();
    }
    expect(parseRoutingHealth({ ...health })?.routers).toBeUndefined();
  });
});

describe("command receipts", () => {
  it("does not treat a malformed success response as a committed command", async () => {
    for (const payload of [{}, { ok: true }, { ok: false, error: { code: "INTERNAL_ERROR", message: "failed" } }]) {
      const api = createApi("/private/", vi.fn(async () => new Response(JSON.stringify(payload))) as typeof fetch);
      await expect(api.command("selection_request", { requestId: "stable" }, "csrf")).rejects.toHaveProperty("code", payload.ok === false ? "INTERNAL_ERROR" : "INVALID_RESPONSE");
    }
  });

  it("sends the private session and CSRF header and returns only the receipt", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify({ ok: true, result: { decisionId: "durable" } })));
    const api = createApi("/private/", fetcher as typeof fetch);
    await expect(api.command("selection_request", { requestId: "stable" }, "csrf")).resolves.toEqual({ decisionId: "durable" });
    expect(fetcher).toHaveBeenCalledWith("/private/api/command", expect.objectContaining({
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Hey-My-Buddy-CSRF": "csrf" },
      body: JSON.stringify({ operation: "selection_request", params: { requestId: "stable" } }),
    }));
  });
});

describe("local read verification evidence", () => {
  const early = "Fri, 09 Oct 2026 18:00:00 GMT";
  const later = "Fri, 09 Oct 2026 18:05:00 GMT";
  const details = { task: { runId: "raw", updatedAt: "2001-01-01T00:00:00Z" }, attempts: [], turns: [] };
  const response = (body: unknown, date?: string, etag = '"read"') => new Response(JSON.stringify(body), {
    headers: { ETag: etag, ...(date === undefined ? {} : { Date: date }) },
  });

  it("keeps task details unchanged and binds fresh 200/304 dates to each displayed read", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(details, early))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }));
    const api = createApi("/private", fetcher);
    const first = await api.task("raw");
    const second = await api.task("raw");
    expect(first).toEqual(details);
    expect(second).toEqual(details);
    expect(Object.keys(second as object)).toEqual(["task", "attempts", "turns"]);
    expect(api.readVerifiedAt(first)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(second)).toBe(Date.parse(later));
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({ "If-None-Match": '"read"' });
  });

  it("keeps missing and malformed response clocks unknown rather than using updatedAt or the client clock", async () => {
    const clientClock = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2030-01-01T00:00:00Z"));
    try {
      for (const date of [undefined, "not a server date"]) {
        const api = createApi("", vi.fn<typeof fetch>().mockResolvedValue(response(details, date)));
        const data = await api.task("raw");
        expect(api.readVerifiedAt(data)).toBeNull();
        expect(readVerificationText(api.readVerifiedAt(data))).toBe("核对时间未记录");
      }
      expect(readVerificationText(undefined)).toBe("核对时间未记录");
      expect(readVerificationText(NaN)).toBe("核对时间未记录");
      expect(readVerificationText(Date.parse(early))).toBe(`核对时间 ${new Date(early).toLocaleString("zh-CN", { hour12: false })}`);
    } finally {
      clientClock.mockRestore();
    }
  });

  it("does not overwrite newer displayed verification when an older 304 finishes late", async () => {
    let release!: (reply: Response) => void;
    const pending = new Promise<Response>(resolve => { release = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(details, early))
      .mockImplementationOnce(() => pending)
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }));
    const api = createApi("", fetcher);
    const first = await api.task("raw");
    const oldRead = api.task("raw");
    const newest = await api.task("raw");
    release(new Response(null, { status: 304, headers: { Date: early } }));
    const older = await oldRead;
    expect(api.readVerifiedAt(newest)).toBe(Date.parse(later));
    expect(api.readVerifiedAt(older)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(first)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(await api.task("raw"))).toBe(Date.parse(later));
  });

  it("uses bounded conditional task details with an abort signal and a separate response clock", async () => {
    const details = { task: { runId: "run/a", updatedAt: "2001-01-01T00:00:00Z" }, attempts: [], turns: [] };
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(details, early))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }));
    const api = createApi("/private", fetcher);
    const controller = new AbortController();
    const first = await api.task("run/a", controller.signal);
    const secondRead = api.task("run/a", controller.signal);
    await expect(secondRead).resolves.toEqual(details);
    const second = await secondRead;
    expect(first).toEqual(details);
    expect(second).toEqual(details);
    expect(api.readVerifiedAt(first)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(second)).toBe(Date.parse(later));
    expect(fetcher.mock.calls[0]).toEqual(["/private/api/tasks/run%2Fa", expect.objectContaining({ signal: controller.signal })]);
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({ "If-None-Match": '"read"' });
    const cancelled = new DOMException("cancelled", "AbortError");
    const abortedApi = createApi("", vi.fn<typeof fetch>().mockRejectedValue(cancelled));
    await expect(abortedApi.task("run/a", controller.signal)).rejects.toBe(cancelled);
  });

  it("attaches the command HTTP Date to routing records without extending the result shape", async () => {
    const result = { entries: [{ id: "entry", updatedAt: "2001-01-01T00:00:00Z" }], nextCursor: "older" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ok: true, result }, later));
    const api = createApi("", fetcher);
    const data = await api.command("selection_list", { limit: 50 }, "csrf");
    expect(data).toEqual(result);
    expect(api.readVerifiedAt(data)).toBe(Date.parse(later));
    expect(api.readVerifiedAt({ ...result })).toBeNull();
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ operation: "selection_list", params: { limit: 50 } }));
  });
});

describe("conditional reads (ETag / If-None-Match / 304)", () => {
  const snapshot = { csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "session" }, configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast", routingBudget: "brief" },
    buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], tasks: { pendingCount: 3 } };
  function jsonResponse(value: unknown, etag?: string) {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (etag) headers.ETag = etag;
    return new Response(JSON.stringify(value), { status: 200, headers });
  }
  /** A fetcher whose replies are queued per call, so races are scripted. */
  function queued(replies: (() => Response)[]) {
    const fetcher = vi.fn<typeof fetch>();
    for (const reply of replies) fetcher.mockImplementationOnce(async () => reply());
    return fetcher;
  }

  it("sends no validator on the first read, then revalidates with If-None-Match and keeps data on 304", async () => {
    const body = structuredClone(snapshot);
    const fetcher = queued([
      () => jsonResponse(body, '"etag-1"'),
      () => new Response(null, { status: 304, headers: { ETag: '"etag-1"' } }),
    ]);
    const api = createApi("/private/", fetcher);
    await expect(api.snapshot()).resolves.toMatchObject({});
    expect((fetcher.mock.calls[0][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
    // The second read revalidates; the empty 304 body must never reach the
    // JSON parser and the existing data must survive intact.
    await expect(api.snapshot()).resolves.toMatchObject({ tasks: { pendingCount: 3 } });
    expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({ "If-None-Match": '"etag-1"' });
  });

  it("caches per full URL, so different task details revalidate separately", async () => {
    const detail = (runId: string) => ({ task: { runId }, attempts: [], turns: [] });
    const fetcher = queued([
      () => jsonResponse(detail("a"), '"task-a-1"'),
      () => jsonResponse(detail("b"), '"task-b-1"'),
      () => new Response(null, { status: 304, headers: { ETag: '"task-a-1"' } }),
      () => jsonResponse(detail("b"), '"task-b-2"'),
    ]);
    const api = createApi("/private/", fetcher);
    await api.task("a");
    await api.task("b");
    expect(fetcher.mock.calls[1][0]).not.toBe(fetcher.mock.calls[0][0]);
    await api.task("a");
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "If-None-Match": '"task-a-1"' });
    await api.task("b");
    // The second task URL still carries its own validator.
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({ "If-None-Match": '"task-b-1"' });
  });

  it("isolates the cache per API instance", async () => {
    const fetcherA = queued([() => jsonResponse(snapshot, '"a-1"'), () => jsonResponse(snapshot, '"a-2"')]);
    const fetcherB = queued([() => jsonResponse(snapshot, '"b-1"')]);
    const apiA = createApi("/private/", fetcherA);
    const apiB = createApi("/private/", fetcherB);
    await apiA.snapshot();
    await apiB.snapshot();
    // B was never given A's validator: instance caches never mix.
    expect((fetcherB.mock.calls[0][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
    await apiA.snapshot();
    expect(fetcherA.mock.calls[1][1]?.headers).toMatchObject({ "If-None-Match": '"a-1"' });
  });

  it("keeps the cached entry when a read fails and revalidates with the old validator afterwards", async () => {
    const fetcher = queued([
      () => jsonResponse(snapshot, '"good-1"'),
      () => new Response(JSON.stringify({ ok: false, error: { code: "INTERNAL_ERROR", message: "down" } }), { status: 500 }),
      () => new Response(null, { status: 304 }),
    ]);
    const api = createApi("/private/", fetcher);
    await expect(api.snapshot()).resolves.toBeTruthy();
    await expect(api.snapshot()).rejects.toHaveProperty("code", "INTERNAL_ERROR");
    // The failure must not poison or evict the cache: the next read still
    // revalidates with the old ETag and the 304 restores the old data.
    await expect(api.snapshot()).resolves.toMatchObject({ tasks: { pendingCount: 3 } });
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "If-None-Match": '"good-1"' });
  });

  it("empties the cache when the session expires, so a later login re-reads", async () => {
    const fetcher = queued([
      () => jsonResponse(snapshot, '"s1"'),
      () => new Response(JSON.stringify({ ok: false, error: { code: "CONSOLE_SESSION_EXPIRED", message: "expired" } }), { status: 401 }),
      () => jsonResponse({ ...snapshot, csrfToken: "csrf-2", consoleSession: { id: "session-2" } }, '"s2"'),
      () => jsonResponse({ ...snapshot, csrfToken: "csrf-2", consoleSession: { id: "session-2" } }, '"s2"'),
    ]);
    const api = createApi("/private/", fetcher);
    await api.snapshot();
    await expect(api.snapshot()).rejects.toHaveProperty("code", "CONSOLE_SESSION_EXPIRED");
    // After the 401 the next read carries no validator: the cached bodies
    // belonged to the previous session.
    await api.snapshot();
    expect((fetcher.mock.calls[2][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
    // A different session id in a 200 clears the cache the same way.
    await api.snapshot();
    expect((fetcher.mock.calls[3][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
  });

  it("never lets a superseded or late reply overwrite a newer cache entry", async () => {
    let releaseSlow: ((value: Response) => void) | undefined;
    const slowReply = new Promise<Response>(resolve => { releaseSlow = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(() => slowReply)
      .mockImplementationOnce(async () => jsonResponse({ ...snapshot,}, '"fast-new"'))
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"check"'));
    const api = createApi("/private/", fetcher);
    const slow = api.snapshot();
    const fast = api.snapshot();
    await expect(fast).resolves.toMatchObject({});
    releaseSlow!(jsonResponse(snapshot, '"slow-old"'));
    await expect(slow).resolves.toBeTruthy();
    // The next read must revalidate with the fast reply's validator: the
    // late slow reply lost its ticket and never touched the cache.
    await api.snapshot();
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "If-None-Match": '"fast-new"' });
  });

  it("does not cache or revalidate mutating commands", async () => {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true, result: { done: true } })));
    const api = createApi("/private/", fetcher);
    await api.command("selection_request", { requestId: "stable" }, "csrf");
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ "X-Hey-My-Buddy-CSRF": "csrf" });
    expect((fetcher.mock.calls[0][1]?.headers as Record<string, string>)["If-None-Match"]).toBeUndefined();
  });

  it("never publishes a reply issued before a session change after the clear", async () => {
    const sessionB = { ...snapshot, csrfToken: "csrf-b", consoleSession: { id: "session-b" } };
    let releaseTask: ((value: Response) => void) | undefined;
    const taskReply = new Promise<Response>(resolve => { releaseTask = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"snap-a"'))
      .mockImplementationOnce(() => taskReply)
      .mockImplementationOnce(async () => jsonResponse(sessionB, '"snap-b"'))
      .mockImplementationOnce(async () => jsonResponse({ task: { runId: "run" } }, '"task-b"'));
    const api = createApi("/private/", fetcher);
    await api.snapshot();
    const inflight = api.task("run");
    // The snapshot under session B clears the cache while the task read of
    // session A is still in the air.
    await api.snapshot();
    releaseTask!(jsonResponse({ task: { runId: "run" } }, '"task-a-late"'));
    await expect(inflight).resolves.toBeTruthy();
    // The late pre-session reply must not have published: the next task read
    // carries no validator at all instead of session A's stale etag.
    await api.task("run");
    expect((fetcher.mock.calls[3][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
  });

  it("answers a 304 with the representation this request validated, not the cache's newer body", async () => {
    const body1 = { task: { runId: "one" } };
    const body2 = { task: { runId: "two" } };
    let release304: ((value: Response) => void) | undefined;
    const reply304 = new Promise<Response>(resolve => { release304 = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(body1, '"e1"'))
      .mockImplementationOnce(() => reply304)
      .mockImplementationOnce(async () => jsonResponse(body2, '"e2"'));
    const api = createApi("/private/", fetcher);
    await api.task("run");
    const read2 = api.task("run");
    await api.task("run");
    release304!(new Response(null, { status: 304 }));
    // The 304 validated this request's own e1 representation; the newer body
    // the cache now holds must not be handed to this caller instead.
    await expect(read2).resolves.toEqual(body1);
  });

  it("refuses a 304 whose session changed in flight instead of serving the previous session's body", async () => {
    const sessionB = { ...snapshot, csrfToken: "csrf-b", consoleSession: { id: "session-b" } };
    let release304: ((value: Response) => void) | undefined;
    const reply304 = new Promise<Response>(resolve => { release304 = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"snap-a"'))
      .mockImplementationOnce(async () => jsonResponse({ task: { runId: "run" } }, '"e1"'))
      .mockImplementationOnce(() => reply304)
      .mockImplementationOnce(async () => jsonResponse(sessionB, '"snap-b"'));
    const api = createApi("/private/", fetcher);
    await api.snapshot();
    await api.task("run");
    const read2 = api.task("run");
    await api.snapshot();
    release304!(new Response(null, { status: 304 }));
    await expect(read2).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("a late reply from a released ticket can never pose as a later request's", async () => {
    // ABA guard: the per-URL slot is released on completion, but tickets come
    // from one global counter — a third request's freshly issued slot must
    // not match an older read's late reply.
    const first = { task: { runId: "first" } };
    const second = { task: { runId: "second" } };
    const third = { task: { runId: "third" } };
    let releaseSlow: ((value: Response) => void) | undefined;
    const slowReply = new Promise<Response>(resolve => { releaseSlow = resolve; });
    let releaseThird: ((value: Response) => void) | undefined;
    const thirdReply = new Promise<Response>(resolve => { releaseThird = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(() => slowReply)
      .mockImplementationOnce(async () => jsonResponse(second, '"e2"'))
      .mockImplementationOnce(() => thirdReply)
      .mockImplementationOnce(async () => jsonResponse(third, '"e3"'));
    const api = createApi("/private/", fetcher);
    const slow = api.task("run");
    await api.task("run");
    const thirdRead = api.task("run");
    // The first read's reply lands after the third request was issued; the
    // third request's own reply lands last of the three.
    releaseSlow!(jsonResponse(first, '"e1"'));
    await expect(slow).resolves.toBeTruthy();
    releaseThird!(jsonResponse(third, '"e3"'));
    await expect(thirdRead).resolves.toBeTruthy();
    // The third request's own reply wins the entry: the next read revalidates
    // with e3, never with the slow reply's e1.
    await api.task("run");
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({ "If-None-Match": '"e3"' });
  });

  it("does not publish an older JSON body after a newer same-URL reply", async () => {
    // The ticket is held through the body read and the publication verdict is
    // evaluated when the body actually arrives: a header that lands early and
    // a body that lands late must not smuggle an older etag into the cache.
    const older = { task: { runId: "old" } };
    const newer = { task: { runId: "new" } };
    let finishBody: ((value: unknown) => void) | undefined;
    const delayed = jsonResponse(older, '"older-body"');
    delayed.json = () => new Promise(resolve => { finishBody = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => delayed)
      .mockImplementationOnce(async () => jsonResponse(newer, '"newer-body"'))
      .mockImplementationOnce(async () => jsonResponse(newer, '"newer-body"'));
    const api = createApi("/private/", fetcher);
    const slow = api.task("same");
    // Let the first reply's headers arrive (and its header-time bookkeeping
    // run) before the second request is even issued — the real race shape.
    await new Promise(resolve => setTimeout(resolve, 0));
    await api.task("same");
    finishBody!(older);
    await expect(slow).resolves.toBeTruthy();
    await api.task("same");
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "If-None-Match": '"newer-body"' });
  });

  it("releases settled per-URL state on every completion path, including failures and aborts", async () => {
    // Every read fails (alternating network error and abort): the per-URL
    // ticket state must be released by each settled request, so the only
    // URL-keyed map left is the bounded response cache itself.
    const NativeMap = Map;
    const observedMaps: Map<unknown, unknown>[] = [];
    class WatchedMap<K, V> extends NativeMap<K, V> {
      constructor(...args: unknown[]) { super(...(args as [])); observedMaps.push(this as Map<unknown, unknown>); }
    }
    vi.stubGlobal("Map", WatchedMap);
    try {
      let calls = 0;
      const api = createApi("/private", vi.fn(async () => {
        throw calls++ % 2 ? new DOMException("aborted", "AbortError") : new Error("network failure");
      }) as typeof fetch);
      for (let index = 0; index < 80; index += 1) await api.task(`failed-${index}`).catch(() => undefined);
      const urlMaps = observedMaps.filter(map => map.size > 0
        && [...map.keys()].every(key => typeof key === "string" && key.startsWith("/private/api")));
      // A missing release would leave one retained entry per failed URL —
      // 80 entries here — so the bound check fails exactly then.
      expect(urlMaps.every(map => map.size <= 32)).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps the successful response body cache bounded across many task details", async () => {
    // The LRU bound (32) covers the SUCCESS path too: 80 distinct cached
    // 200+ETag bodies must evict their oldest entries instead of growing the
    // per-instance map without limit. This is the body cache — the failed/
    // aborted ticket release above is a separate regression.
    const NativeMap = Map;
    const observedMaps: Map<unknown, unknown>[] = [];
    class WatchedMap<K, V> extends NativeMap<K, V> {
      constructor(...args: unknown[]) { super(...(args as [])); observedMaps.push(this as Map<unknown, unknown>); }
    }
    vi.stubGlobal("Map", WatchedMap);
    try {
      const api = createApi("/private", vi.fn(async () => jsonResponse({ task: { runId: "bound" } }, '"tag"')) as typeof fetch);
      for (let index = 0; index < 80; index += 1) await api.task(`bound-${index}`);
      const urlMaps = observedMaps.filter(map => map.size > 0
        && [...map.keys()].every(key => typeof key === "string" && key.startsWith("/private/api")));
      // Success bodies really are cached (this is not the failed-path cleanup),
      // and the cached set never exceeds the bounded capacity.
      expect(urlMaps.length).toBeGreaterThan(0);
      expect(Math.max(...urlMaps.map(map => map.size))).toBeLessThanOrEqual(32);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("names each response's own HTTP Date as the verification time, fresh on 304 even for a cached body", async () => {
    const warm = macroTaskTimelineFixture();                            // body observedAt 08:12 — may predate the read
    const warmDate = new Date("2026-09-24T12:00:00Z").toUTCString();    // served earlier
    const fresh304 = new Date("2026-09-24T12:05:00Z").toUTCString();    // revalidated later
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => new Response(JSON.stringify(warm), { status: 200, headers: { ETag: '"e1"', Date: warmDate } }))
      .mockImplementationOnce(async () => new Response(null, { status: 304, headers: { ETag: '"e1"', Date: fresh304 } }));
    const api = createApi("/private", fetcher);
    // Warm 200: the verification time is the response's own Date — the body's
    // observedAt may be older, and that fact stays in the body untouched.
    const { timeline, verifiedAtMs } = await api.macroTaskTimeline("macro-1", {});
    expect(timeline.observedAt).toBe(OBSERVED_AT);
    expect(verifiedAtMs).toBe(Date.parse(warmDate));
    // Same-body 304: the Date is the revalidation instant — fresh, not the
    // cached body's date.
    const second = await api.macroTaskTimeline("macro-1", {});
    expect(second.timeline).toEqual(timeline);
    expect(second.verifiedAtMs).toBe(Date.parse(fresh304));
  });

  it("tolerates a response without a Date header by reporting no verification time", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(macroTaskTimelineFixture())));
    const api = createApi("/private", fetcher);
    const { verifiedAtMs } = await api.macroTaskTimeline("macro-1", {});
    expect(verifiedAtMs).toBeNull();
  });

  it("refuses a thin snapshot without the server's pending count, including the legacy full-runs shape", async () => {
    for (const tasks of [undefined, {}, { runs: [], total: 0 }, { pendingCount: "3" }, { pendingCount: -1 }]) {
      const api = createApi("/private/", queued([() => jsonResponse({ ...snapshot, tasks })]));
      await expect(api.snapshot()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });


});

describe("storage wire shapes (0.16, operations.md 3b0a2e6)", () => {
  const planEnvelope = {
    planId: "plan-9", createdAt: "2026-09-27T12:00:00Z", expiresAt: "2026-09-27T12:15:00Z",
    categories: [{ id: "zcode", label: "", bytes: 1, reclaimableBytes: 0, count: 1, eligibleCount: 0, reasons: [] }],
    candidates: [], orphanProcesses: [],
  };
  function commandApi(result: unknown) {
    return createApi("/private", vi.fn(async () => new Response(JSON.stringify({ ok: true, result }))) as typeof fetch);
  }

  it("returns the plan as-is when its shape is complete", async () => {
    await expect(commandApi(planEnvelope).storagePlan("csrf")).resolves.toMatchObject({ planId: "plan-9" });
  });

  it("refuses a plan missing any collection or scalar", async () => {
    for (const broken of [
      { ...planEnvelope, categories: "many" },
      { ...planEnvelope, candidates: null },
      { ...planEnvelope, orphanProcesses: {} },
      { ...planEnvelope, planId: "" },
      { ...planEnvelope, createdAt: "unrecorded" },
      { ...planEnvelope, expiresAt: "never" },
      { ...planEnvelope, categories: [{ ...planEnvelope.categories[0], count: -1 }] },
      { ...planEnvelope, categories: [{ ...planEnvelope.categories[0], reasons: [null] }] },
      { ...planEnvelope, categories: [...planEnvelope.categories, ...planEnvelope.categories] },
      { ...planEnvelope, candidates: [{ id: "c", category: "zcode", path: "/p", bytes: 1, eligible: true, reasons: [3] }] },
      { ...planEnvelope, orphanProcesses: [{ pid: 12, kind: "execution", stateDir: false, runtimeDir: null }] },
      { ...planEnvelope, categories: [{ id: "zcode", bytes: "1", reclaimableBytes: 0, count: 0, eligibleCount: 0, reasons: [] }] },
    ]) {
      await expect(commandApi(broken).storagePlan("csrf")).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("parses the real apply result: removed/skipped record arrays with complete true", async () => {
    const result = {
      planId: "plan-9", removedBytes: 12, complete: true,
      removed: [{ id: "c1", path: "/p/one", bytes: 8 }, { id: "c2", path: "/p/two", bytes: 4 }],
      skipped: [{ id: "c3", path: "/p/three", reasons: ["candidate-changed", "shutdown-unconfirmed"] }],
    };
    await expect(commandApi(result).storageApply("plan-9", "cmd-1", "csrf")).resolves.toMatchObject({ removedBytes: 12, complete: true });
  });

  it("treats a numeric removed/skipped reply, a planId mismatch or a non-complete reply as unresolved", async () => {
    const numeric = { planId: "plan-9", removedBytes: 12, removed: 2, skipped: 0, complete: true };
    await expect(commandApi(numeric).storageApply("plan-9", "cmd-1", "csrf")).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    const mismatch = { planId: "plan-other", removedBytes: 0, removed: [], skipped: [], complete: true };
    await expect(commandApi(mismatch).storageApply("plan-9", "cmd-1", "csrf")).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    const badReasons = { planId: "plan-9", removedBytes: 0, removed: [], skipped: [{ id: "c", path: "/p", reasons: [null] }], complete: true };
    await expect(commandApi(badReasons).storageApply("plan-9", "cmd-1", "csrf")).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    const unfinished = { planId: "plan-9", removedBytes: 4, removed: [{ id: "c1", path: "/p/one", bytes: 4 }], skipped: [], complete: false };
    await expect(commandApi(unfinished).storageApply("plan-9", "cmd-1", "csrf")).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });
});

describe("on-demand running and installed version facts", () => {
  const facts = { softwareVersion: "0.29.0", contractVersion: "fixture", schemaVersion: 15, sourceCommit: null, installedAt: null };
  const version = { running: { ...facts, mode: "source" }, installed: null };
  it("reads only the authenticated version GET, with same-origin credentials and no cache", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(version)));
    const api = createApi("/session", fetcher as typeof fetch);
    await expect(api.runtimeVersion()).resolves.toEqual(version);
    expect(fetcher).toHaveBeenCalledExactlyOnceWith("/session/api/runtime-version", expect.objectContaining({ credentials: "same-origin", cache: "no-store" }));
  });
  it("refuses malformed or missing version facts and propagates session loss", async () => {
    for (const broken of [null, {}, { running: { ...facts, mode: "unknown" }, installed: null },
      { ...version, running: { ...version.running, softwareVersion: "" } },
      { ...version, running: { ...version.running, sourceCommit: undefined } },
      { ...version, running: { ...version.running, installedAt: "not-a-date" } },
      { ...version, installed: { ...facts, schemaVersion: "15" } }]) {
      const api = createApi("", vi.fn(async () => new Response(JSON.stringify(broken))) as typeof fetch);
      await expect(api.runtimeVersion()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    }
    const api = createApi("", vi.fn(async () => new Response(JSON.stringify({ error: { code: "CONSOLE_SESSION_EXPIRED", message: "expired" } }), { status: 401 })) as typeof fetch);
    await expect(api.runtimeVersion()).rejects.toMatchObject({ code: "CONSOLE_SESSION_EXPIRED" });
  });
});

describe("what the data layer says when a read fails", () => {
  it("says it in the language of the page, for a code it knows and for a failure it makes itself", async () => {
    const failure = new ApiError("CONFLICT", "board words");
    const unreadable = createApi("", vi.fn(async () => { throw new TypeError("offline"); }) as typeof fetch);
    expect(errorText(failure)).toBe("记录冲突；请刷新核对后重试。");
    await expect(unreadable.task("run")).rejects.toMatchObject({ message: "无法连接本地黑板；请刷新重试。" });
    await i18n.changeLanguage("en");
    try {
      expect(errorText(failure)).toBe("The records conflict; refresh, check them and try again.");
      await expect(unreadable.task("run")).rejects.toMatchObject({ message: "Cannot reach the local blackboard; refresh and retry." });
      // A code the console has no words for keeps what the board said.
      expect(errorText(new ApiError("OTHER_CODE", "board words"))).toBe("board words");
      expect(errorText("plain")).toBe("The request failed; try again later.");
    } finally { await i18n.changeLanguage("zh"); }
  });
});
