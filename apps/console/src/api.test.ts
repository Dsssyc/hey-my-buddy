import { describe, expect, it, vi } from "vitest";
import { createApi, parseRoutingHealth, readVerificationText, validRoutingConfiguration } from "./api";
import { objectiveTimelineFixture, OBSERVED_AT } from "./objective-fixtures";

describe("L4 ordered Router list configuration", () => {
  const current = { revision: 4, routerProfileIds: [], routerRetryIntervalSeconds: 600,
    defaultRoutingMode: "fast", routingBudget: "brief" };
  it("accepts the list settings and rejects retired slots, selectors or intervals", () => {
    expect(validRoutingConfiguration(current)).toBe(true);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "review", routerProfileIds: ["review", "fast"] })).toBe(true);
    // An empty list is an explicit clear; duplicates are a board refusal.
    expect(validRoutingConfiguration({ ...current, routerProfileIds: [] })).toBe(true);
    expect(validRoutingConfiguration({ ...current, routerProfileIds: ["a", "a"] })).toBe(false);
    for (const retired of [{ decisionProfileId: "old" }, { routerProfileId: "old" },
      { fastRouterProfileId: "old" }, { reviewRouterProfileId: "old" },
      { fastRouterProfileId: null, reviewRouterProfileId: null }]) {
      expect(validRoutingConfiguration({ ...current, ...retired })).toBe(false);
    }
    expect(validRoutingConfiguration({ ...current, routingBudget: "quick" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "other" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerProfileIds: undefined })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerProfileIds: [""] })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerProfileIds: "a" })).toBe(false);
    for (const interval of [0, -1, 1.5, NaN, 2147483648, "600", true, null]) {
      expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: interval })).toBe(false);
    }
    expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: 1 })).toBe(true);
    expect(validRoutingConfiguration({ ...current, routerRetryIntervalSeconds: 2147483647 })).toBe(true);
  });
});

describe("snapshot upgrade boundary and routing health", () => {
  const configuration = { revision: 4, routerProfileIds: ["head"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast", routingBudget: "standard" };
  const snapshot = { csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null },
    tableRevision: 1, configuration, configurationError: null, gate: { phase: "open" }, profiles: [], cards: [],
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], modelConcurrency: [],
    tasks: { pendingCount: 0 } };
  const error = { code: "router-settings-upgrade-required", message: "Router 设置需升级", revision: 4 };
  const health = { available: false, reasonCode: "router-consecutive-failures", windowSize: 20, sampleCount: 3,
    failureCount: 3, consecutiveFailures: 3, abstentionCount: 0, cancelledCount: 0, staleCount: 0,
    lastSuccessAt: null, lastSuccessDecisionId: null,
    recentFailures: [{ decisionId: "timeout", runId: "run", at: "2026-10-02T00:00:00Z", code: "router-timeout" }] };
  function apiFor(body: unknown) {
    return createApi("", vi.fn(async () => new Response(JSON.stringify(body))) as typeof fetch);
  }
  it("accepts the explicit unavailable upgrade state without inventing settings", async () => {
    await expect(apiFor({ ...snapshot, configuration: null, configurationError: error }).snapshot())
      .resolves.toMatchObject({ configuration: null, configurationError: error });
    for (const broken of [null, { ...error, revision: "4" }, { ...error, message: "" }, { ...error, code: "unknown" }]) {
      await expect(apiFor({ ...snapshot, configuration: null, configurationError: broken }).snapshot())
        .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    await expect(apiFor({ ...snapshot, configurationError: error }).snapshot()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
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
    await expect(apiFor({ ...snapshot, configuration: { ...configuration, routerProfileId: "head" } }).snapshot())
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(apiFor({ ...snapshot, configuration: { ...configuration, fastRouterProfileId: "fast", reviewRouterProfileId: "review" } }).snapshot())
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });
  it("preserves service availability and timeout failures without calculating a threshold", async () => {
    await expect(apiFor({ ...snapshot, routingHealth: health }).snapshot()).resolves.toMatchObject({ routingHealth: health });
    expect(parseRoutingHealth({ ...health, available: true, reasonCode: null })).toMatchObject({ available: true });
    expect(parseRoutingHealth({ ...health, consecutiveFailures: 0 })).toMatchObject({ available: false });
    for (const broken of [{ ...health, available: "false" }, { ...health, reasonCode: 3 }, { ...health, recentFailures: {} }]) {
      await expect(apiFor({ ...snapshot, routingHealth: broken }).snapshot()).resolves.toMatchObject({ routingHealth: undefined });
    }
  });
  it("parses per-buddy health entries and drops only malformed health", async () => {
    const routers = [{
      profileId: "head", index: 0, identity: { adapter: "dsh", provider: "p", model: "m", effort: "max" },
      eligible: true, code: null, inSkipWindow: false, skipUntil: null, retryAt: null, retryInProgress: false,
      answeredCount: 2, noAnswerCount: 4, lastAnsweredAt: "2026-10-02T00:00:00Z", lastNoAnswerAt: "2026-10-02T01:00:00Z",
      consecutiveNoAnswers: 4, windowSize: 20, windowEntries: 6, windowAnsweredCount: 2, windowFailureCount: 4,
      windowBudgetExhaustedCount: 4, windowBoundsRejectedCount: 0, windowAttemptCount: 4,
      lastError: { code: "timeout", at: "2026-10-02T01:00:00Z", phase: "runtime" },
    }, { profileId: "tail", index: 1, identity: null, eligible: false, code: "router-skip-window" }];
    const listHealth = { ...health, available: false, reasonCode: "router-skip-window",
      currentRouterProfileId: "tail", routers, unattributedCount: 1,
      unattributed: [{ decisionId: "dec-u", at: "2026-10-02T02:00:00Z", outcome: "unknown", code: null }] };
    await expect(apiFor({ ...snapshot, routingHealth: listHealth }).snapshot()).resolves.toMatchObject({ routingHealth: listHealth });
    // A malformed entry or an unknown-nature outcome stays unknown rather than
    // being read as success or failure: the whole health object is dropped.
    for (const broken of [
      { ...listHealth, routers: [{ ...routers[0], profileId: "" }] },
      { ...listHealth, routers: [{ ...routers[0], index: -1 }] },
      { ...listHealth, routers: [{ ...routers[0], eligible: "yes" }] },
      { ...listHealth, routers: "head" },
      { ...listHealth, currentRouterProfileId: 3 },
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
      method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-Buddy-CSRF": "csrf" },
      body: JSON.stringify({ operation: "selection_request", params: { requestId: "stable" } }),
    }));
  });
});

describe("delegation history reads", () => {
  it("sends filters and the opaque cursor through GET with the caller's abort signal", async () => {
    const page = { runs: [], total: 120, nextCursor: "next-page" };
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify(page)));
    const api = createApi("/private/", fetcher);
    const controller = new AbortController();
    await expect(api.tasks({ rootsOnly: false, before: "time+id/==", limit: 50, query: "项目 A",
      projectId: "repository:a", hostId: "Host B", filter: "host" }, controller.signal)).resolves.toEqual(page);
    const [location, init] = fetcher.mock.calls[0];
    const url = new URL(String(location), "http://localhost");
    expect(url.pathname).toBe("/private/api/tasks");
    expect(Object.fromEntries(url.searchParams)).toEqual({ rootsOnly: "false", before: "time+id/==", limit: "50",
      query: "项目 A", projectId: "repository:a", hostId: "Host B", filter: "host" });
    expect(init).toMatchObject({ credentials: "same-origin", cache: "no-store", signal: controller.signal });
    expect(init?.method || "GET").toBe("GET");
    expect(init?.body).toBeUndefined();
  });

  it("rejects incomplete history envelopes rather than treating them as an empty page", async () => {
    for (const page of [{ runs: [], total: 0 }, { runs: {}, total: 0, nextCursor: null },
      { runs: [], total: "0", nextCursor: null }, { runs: [], total: 0, nextCursor: 1 }]) {
      const api = createApi("/private", vi.fn(async () => new Response(JSON.stringify(page))) as typeof fetch);
      await expect(api.tasks({ rootsOnly: true })).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("preserves an aborted read instead of reporting a connection failure", async () => {
    const cancelled = new DOMException("cancelled", "AbortError");
    const api = createApi("/private", vi.fn(async () => { throw cancelled; }) as typeof fetch);
    await expect(api.tasks({ rootsOnly: true }, new AbortController().signal)).rejects.toBe(cancelled);
  });
});

describe("local read verification evidence", () => {
  const early = "Fri, 09 Oct 2026 18:00:00 GMT";
  const later = "Fri, 09 Oct 2026 18:05:00 GMT";
  const page = { runs: [{ runId: "raw", updatedAt: "2001-01-01T00:00:00Z" }], total: 1, nextCursor: null };
  const response = (body: unknown, date?: string, etag = '"read"') => new Response(JSON.stringify(body), {
    headers: { ETag: etag, ...(date === undefined ? {} : { Date: date }) },
  });

  it("keeps history DTOs unchanged and binds fresh 200/304 dates to each displayed read", async () => {
    const fetcher = vi.fn<typeof fetch>()
      .mockResolvedValueOnce(response(page, early))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }));
    const api = createApi("/private", fetcher);
    const first = await api.tasks({ limit: 50, before: "bounded" });
    const second = await api.tasks({ limit: 50, before: "bounded" });
    expect(first).toEqual(page);
    expect(second).toEqual(page);
    expect(Object.keys(second)).toEqual(["runs", "total", "nextCursor"]);
    expect(api.readVerifiedAt(first)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(second)).toBe(Date.parse(later));
    expect(fetcher.mock.calls[1][1]?.headers).toEqual({ "If-None-Match": '"read"' });
  });

  it("keeps missing and malformed response clocks unknown rather than using updatedAt or the client clock", async () => {
    const clientClock = vi.spyOn(Date, "now").mockReturnValue(Date.parse("2030-01-01T00:00:00Z"));
    try {
      for (const date of [undefined, "not a server date"]) {
        const api = createApi("", vi.fn<typeof fetch>().mockResolvedValue(response(page, date)));
        const data = await api.tasks({ limit: 50 });
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
      .mockResolvedValueOnce(response(page, early))
      .mockImplementationOnce(() => pending)
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }))
      .mockResolvedValueOnce(new Response(null, { status: 304, headers: { Date: later } }));
    const api = createApi("", fetcher);
    const first = await api.tasks({});
    const oldRead = api.tasks({});
    const newest = await api.tasks({});
    release(new Response(null, { status: 304, headers: { Date: early } }));
    const older = await oldRead;
    expect(api.readVerifiedAt(newest)).toBe(Date.parse(later));
    expect(api.readVerifiedAt(older)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(first)).toBe(Date.parse(early));
    expect(api.readVerifiedAt(await api.tasks({}))).toBe(Date.parse(later));
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

  it("attaches the command HTTP Date to configuration history without extending the result shape", async () => {
    const result = { entries: [{ id: "entry", updatedAt: "2001-01-01T00:00:00Z" }], nextCursor: "older" };
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(response({ ok: true, result }, later));
    const api = createApi("", fetcher);
    const data = await api.command("evaluation_history", { limit: 50 }, "csrf");
    expect(data).toEqual(result);
    expect(api.readVerifiedAt(data)).toBe(Date.parse(later));
    expect(api.readVerifiedAt({ ...result })).toBeNull();
    expect(fetcher.mock.calls[0][1]?.body).toBe(JSON.stringify({ operation: "evaluation_history", params: { limit: 50 } }));
  });
});

describe("conditional reads (ETag / If-None-Match / 304)", () => {
  const snapshot = { csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null },
    tableRevision: 1, configuration: { revision: 1, routerProfileIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "fast", routingBudget: "brief" },
    configurationError: null, gate: { phase: "open" }, profiles: [], cards: [], preferences: [], familyPreferences: [],
    preferenceOverrides: [], familyAnnotations: [], modelConcurrency: [], tasks: { pendingCount: 3 } };
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
    await expect(api.snapshot()).resolves.toMatchObject({ tableRevision: 1 });
    expect((fetcher.mock.calls[0][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
    // The second read revalidates; the empty 304 body must never reach the
    // JSON parser and the existing data must survive intact.
    await expect(api.snapshot()).resolves.toMatchObject({ tableRevision: 1, tasks: { pendingCount: 3 } });
    expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({ "If-None-Match": '"etag-1"' });
  });

  it("caches per full URL, so different filters and pages revalidate separately", async () => {
    const page = (runs: string[]) => ({ runs: runs.map(runId => ({ runId })), total: runs.length, nextCursor: null });
    const fetcher = queued([
      () => jsonResponse(page(["a"]), '"roots-1"'),
      () => jsonResponse(page(["b"]), '"all-1"'),
      () => new Response(null, { status: 304, headers: { ETag: '"roots-1"' } }),
      () => jsonResponse(page(["a"]), '"roots-2"'),
    ]);
    const api = createApi("/private/", fetcher);
    await api.tasks({ rootsOnly: true, limit: 50 });
    await api.tasks({ rootsOnly: false });
    expect(fetcher.mock.calls[1][0]).not.toBe(fetcher.mock.calls[0][0]);
    await api.tasks({ rootsOnly: true, limit: 50 });
    expect(fetcher.mock.calls[2][1]?.headers).toMatchObject({ "If-None-Match": '"roots-1"' });
    await api.tasks({ rootsOnly: false });
    // The all-records URL still carries its own validator, untouched by the
    // other page's exchanges.
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({ "If-None-Match": '"all-1"' });
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
      () => jsonResponse({ ...snapshot, csrfToken: "csrf-2", consoleSession: { id: "session-2", canWrite: true, reason: null } }, '"s2"'),
      () => jsonResponse({ ...snapshot, csrfToken: "csrf-2", consoleSession: { id: "session-2", canWrite: true, reason: null } }, '"s2"'),
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
      .mockImplementationOnce(async () => jsonResponse({ ...snapshot, tableRevision: 2 }, '"fast-new"'))
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"check"'));
    const api = createApi("/private/", fetcher);
    const slow = api.snapshot();
    const fast = api.snapshot();
    await expect(fast).resolves.toMatchObject({ tableRevision: 2 });
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
    expect(fetcher.mock.calls[0][1]?.headers).toMatchObject({ "X-Buddy-CSRF": "csrf" });
    expect((fetcher.mock.calls[0][1]?.headers as Record<string, string>)["If-None-Match"]).toBeUndefined();
  });

  it("never publishes a reply issued before a session change after the clear", async () => {
    const sessionB = { ...snapshot, csrfToken: "csrf-b", consoleSession: { id: "session-b", canWrite: true, reason: null } };
    let releaseTasks: ((value: Response) => void) | undefined;
    const tasksReply = new Promise<Response>(resolve => { releaseTasks = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"snap-a"'))
      .mockImplementationOnce(() => tasksReply)
      .mockImplementationOnce(async () => jsonResponse(sessionB, '"snap-b"'))
      .mockImplementationOnce(async () => jsonResponse({ runs: [], total: 0, nextCursor: null }, '"tasks-b"'));
    const api = createApi("/private/", fetcher);
    await api.snapshot();
    const inflight = api.tasks({ rootsOnly: true });
    // The snapshot under session B clears the cache while the tasks read of
    // session A is still in the air.
    await api.snapshot();
    releaseTasks!(jsonResponse({ runs: [], total: 0, nextCursor: null }, '"tasks-a-late"'));
    await expect(inflight).resolves.toBeTruthy();
    // The late pre-session reply must not have published: the next tasks read
    // carries no validator at all instead of session A's stale etag.
    await api.tasks({ rootsOnly: true });
    expect((fetcher.mock.calls[3][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
  });

  it("answers a 304 with the representation this request validated, not the cache's newer body", async () => {
    const body1 = { runs: [{ runId: "one" }], total: 1, nextCursor: null };
    const body2 = { runs: [{ runId: "two" }], total: 1, nextCursor: null };
    let release304: ((value: Response) => void) | undefined;
    const reply304 = new Promise<Response>(resolve => { release304 = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(body1, '"e1"'))
      .mockImplementationOnce(() => reply304)
      .mockImplementationOnce(async () => jsonResponse(body2, '"e2"'));
    const api = createApi("/private/", fetcher);
    await api.tasks({ rootsOnly: true });
    const read2 = api.tasks({ rootsOnly: true });
    await api.tasks({ rootsOnly: true });
    release304!(new Response(null, { status: 304 }));
    // The 304 validated this request's own e1 representation; the newer body
    // the cache now holds must not be handed to this caller instead.
    await expect(read2).resolves.toEqual(body1);
  });

  it("refuses a 304 whose session changed in flight instead of serving the previous session's body", async () => {
    const sessionB = { ...snapshot, csrfToken: "csrf-b", consoleSession: { id: "session-b", canWrite: true, reason: null } };
    let release304: ((value: Response) => void) | undefined;
    const reply304 = new Promise<Response>(resolve => { release304 = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(snapshot, '"snap-a"'))
      .mockImplementationOnce(async () => jsonResponse({ runs: [], total: 0, nextCursor: null }, '"e1"'))
      .mockImplementationOnce(() => reply304)
      .mockImplementationOnce(async () => jsonResponse(sessionB, '"snap-b"'));
    const api = createApi("/private/", fetcher);
    await api.snapshot();
    await api.tasks({ rootsOnly: true });
    const read2 = api.tasks({ rootsOnly: true });
    await api.snapshot();
    release304!(new Response(null, { status: 304 }));
    await expect(read2).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("a late reply from a released ticket can never pose as a later request's", async () => {
    // ABA guard: the per-URL slot is released on completion, but tickets come
    // from one global counter — a third request's freshly issued slot must
    // not match an older read's late reply.
    const first = { runs: [{ runId: "first" }], total: 1, nextCursor: null };
    const second = { runs: [{ runId: "second" }], total: 1, nextCursor: null };
    const third = { runs: [{ runId: "third" }], total: 1, nextCursor: null };
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
    const slow = api.tasks({ rootsOnly: true });
    await api.tasks({ rootsOnly: true });
    const thirdRead = api.tasks({ rootsOnly: true });
    // The first read's reply lands after the third request was issued; the
    // third request's own reply lands last of the three.
    releaseSlow!(jsonResponse(first, '"e1"'));
    await expect(slow).resolves.toBeTruthy();
    releaseThird!(jsonResponse(third, '"e3"'));
    await expect(thirdRead).resolves.toBeTruthy();
    // The third request's own reply wins the entry: the next read revalidates
    // with e3, never with the slow reply's e1.
    await api.tasks({ rootsOnly: true });
    expect(fetcher.mock.calls[3][1]?.headers).toMatchObject({ "If-None-Match": '"e3"' });
  });

  it("does not publish an older JSON body after a newer same-URL reply", async () => {
    // The ticket is held through the body read and the publication verdict is
    // evaluated when the body actually arrives: a header that lands early and
    // a body that lands late must not smuggle an older etag into the cache.
    const older = { runs: [{ runId: "old" }], total: 1, nextCursor: null };
    const newer = { runs: [], total: 2, nextCursor: null };
    let finishBody: ((value: unknown) => void) | undefined;
    const delayed = jsonResponse(older, '"older-body"');
    delayed.json = () => new Promise(resolve => { finishBody = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => delayed)
      .mockImplementationOnce(async () => jsonResponse(newer, '"newer-body"'))
      .mockImplementationOnce(async () => jsonResponse(newer, '"newer-body"'));
    const api = createApi("/private/", fetcher);
    const slow = api.tasks({ query: "same" });
    // Let the first reply's headers arrive (and its header-time bookkeeping
    // run) before the second request is even issued — the real race shape.
    await new Promise(resolve => setTimeout(resolve, 0));
    await api.tasks({ query: "same" });
    finishBody!(older);
    await expect(slow).resolves.toBeTruthy();
    await api.tasks({ query: "same" });
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
      for (let index = 0; index < 80; index += 1) await api.tasks({ query: `failed-${index}` }).catch(() => undefined);
      const urlMaps = observedMaps.filter(map => map.size > 0
        && [...map.keys()].every(key => typeof key === "string" && key.startsWith("/private/api")));
      // A missing release would leave one retained entry per failed URL —
      // 80 entries here — so the bound check fails exactly then.
      expect(urlMaps.every(map => map.size <= 32)).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });

  it("keeps the successful response body cache bounded across many unique filters", async () => {
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
      const api = createApi("/private", vi.fn(async () => jsonResponse({ runs: [], total: 0, nextCursor: null }, '"tag"')) as typeof fetch);
      for (let index = 0; index < 80; index += 1) await api.tasks({ query: `bound-${index}` });
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
    const warm = objectiveTimelineFixture();                            // body observedAt 08:12 — may predate the read
    const warmDate = new Date("2026-09-24T12:00:00Z").toUTCString();    // served earlier
    const fresh304 = new Date("2026-09-24T12:05:00Z").toUTCString();    // revalidated later
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => new Response(JSON.stringify(warm), { status: 200, headers: { ETag: '"e1"', Date: warmDate } }))
      .mockImplementationOnce(async () => new Response(null, { status: 304, headers: { ETag: '"e1"', Date: fresh304 } }));
    const api = createApi("/private", fetcher);
    // Warm 200: the verification time is the response's own Date — the body's
    // observedAt may be older, and that fact stays in the body untouched.
    const { timeline, verifiedAtMs } = await api.objectiveTimeline("obj-1", {});
    expect(timeline.observedAt).toBe(OBSERVED_AT);
    expect(verifiedAtMs).toBe(Date.parse(warmDate));
    // Same-body 304: the Date is the revalidation instant — fresh, not the
    // cached body's date.
    const second = await api.objectiveTimeline("obj-1", {});
    expect(second.timeline).toEqual(timeline);
    expect(second.verifiedAtMs).toBe(Date.parse(fresh304));
  });

  it("treats a legitimate HTTP 200 negative assessment as domain data, not a failure", async () => {
    // The preflight DTO itself carries a negative verdict (ok:false with
    // per-class reasons and no error object): a legal domain answer the panel
    // must show as reasons — never as 读取失败.
    const blocked = { policy: "attempt-evidence", ok: false, needsAttention: true,
      legacyPlan: { present: false },
      copied: { count: 2, paths: ["a"], entries: [{ path: "a", reason: "evidence" }] },
      skipped: { count: 1, paths: ["b"], entries: [{ path: "b", reason: "not-evidence-file" }] },
      rejected: { count: 1, paths: ["c"], entries: [{ path: "c", reason: "linked-path" }] } };
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(blocked, '"neg-1"'))
      .mockImplementationOnce(async () => new Response(null, { status: 304, headers: { ETag: '"neg-1"' } }));
    const api = createApi("/private", fetcher);
    await expect(api.backupPreflight()).resolves.toMatchObject({ ok: false, needsAttention: true });
    // The cached negative body revalidated by 304 stays domain data too.
    await expect(api.backupPreflight()).resolves.toMatchObject({ rejected: { count: 1 } });
  });

  it("still refuses a real refusal envelope, HTTP-level failures and invalid reports on the preflight route", async () => {
    const report = { policy: "attempt-evidence", ok: true, needsAttention: false,
      copied: { count: 0, paths: [], entries: [] }, skipped: { count: 0, paths: [], entries: [] },
      rejected: { count: 0, paths: [], entries: [] } };
    // A service refusal envelope ({ok:false, error:{...}}) is a failure even
    // on this route — and it clears the session-scoped cache.
    let releaseEnvelope: ((value: Response) => void) | undefined;
    const envelopeReply = new Promise<Response>(resolve => { releaseEnvelope = resolve; });
    const fetcher = vi.fn<typeof fetch>()
      .mockImplementationOnce(async () => jsonResponse(report, '"pf-1"'))
      .mockImplementationOnce(() => envelopeReply)
      .mockImplementationOnce(async () => new Response(JSON.stringify({ ok: false, error: { code: "CONSOLE_SESSION_EXPIRED", message: "expired" } }), { status: 401, headers: { "Content-Type": "application/json" } }));
    const api = createApi("/private", fetcher);
    await api.backupPreflight();
    const refused = api.backupPreflight();
    releaseEnvelope!(new Response(JSON.stringify({ ok: false, error: { code: "FORBIDDEN", message: "no" } }), { status: 200, headers: { "Content-Type": "application/json" } }));
    await expect(refused).rejects.toHaveProperty("code", "FORBIDDEN");
    // A later read carries no validator: the refusal cleared the cache.
    const fresh = createApi("/private", vi.fn(async () => jsonResponse(report)));
    await fresh.backupPreflight();
    void fresh;
    await expect(api.backupPreflight()).rejects.toHaveProperty("code", "CONSOLE_SESSION_EXPIRED");
    // HTTP-level failures stay failures.
    const http = createApi("/private", vi.fn(async () => new Response(
      JSON.stringify({ ...report, ok: false }), { status: 403, headers: { "Content-Type": "application/json" } })) as typeof fetch);
    await expect(http.backupPreflight()).rejects.toHaveProperty("code", "HTTP_403");
    // An incomplete report body stays INVALID_RESPONSE.
    const broken = createApi("/private", vi.fn(async () => jsonResponse({ policy: "x", ok: true })) as typeof fetch);
    await expect(broken.backupPreflight()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("tolerates a response without a Date header by reporting no verification time", async () => {
    const fetcher = vi.fn(async () => new Response(JSON.stringify(objectiveTimelineFixture())));
    const api = createApi("/private", fetcher);
    const { verifiedAtMs } = await api.objectiveTimeline("obj-1", {});
    expect(verifiedAtMs).toBeNull();
  });

  it("refuses a thin snapshot without the server's pending count, including the legacy full-runs shape", async () => {
    for (const tasks of [undefined, {}, { runs: [], total: 0 }, { pendingCount: "3" }, { pendingCount: -1 }]) {
      const api = createApi("/private/", queued([() => jsonResponse({ ...snapshot, tasks })]));
      await expect(api.snapshot()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });

  it("parses the on-demand backup preflight and refuses an incomplete report", async () => {
    const report = { policy: "attempt-evidence", ok: true, needsAttention: false,
      copied: { count: 1, paths: ["a"], entries: [{ path: "a", reason: "evidence" }] },
      skipped: { count: 0, paths: [], entries: [] },
      rejected: { count: 0, paths: [], entries: [] } };
    const fetcher = queued([
      () => jsonResponse(report, '"pf-1"'),
      () => new Response(null, { status: 304 }),
      () => jsonResponse(report, '"pf-2"'),
    ]);
    const api = createApi("/private/", fetcher);
    await expect(api.backupPreflight()).resolves.toMatchObject({ policy: "attempt-evidence" });
    await expect(api.backupPreflight()).resolves.toMatchObject({ needsAttention: false });
    expect(fetcher.mock.calls[1][1]?.headers).toMatchObject({ "If-None-Match": '"pf-1"' });
    // An explicit re-check omits the validator so the fresh walk transfers.
    await expect(api.backupPreflight(undefined, { refresh: true })).resolves.toMatchObject({ ok: true });
    expect((fetcher.mock.calls[2][1]?.headers as Record<string, string> | undefined)?.["If-None-Match"]).toBeUndefined();
    const broken = createApi("/private/", queued([() => jsonResponse({ ...report, ok: "yes" })]));
    await expect(broken.backupPreflight()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
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
      { ...version, installed: { ...facts, schemaVersion: "15" } }]) {
      const api = createApi("", vi.fn(async () => new Response(JSON.stringify(broken))) as typeof fetch);
      await expect(api.runtimeVersion()).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
    }
    const api = createApi("", vi.fn(async () => new Response(JSON.stringify({ error: { code: "CONSOLE_SESSION_EXPIRED", message: "expired" } }), { status: 401 })) as typeof fetch);
    await expect(api.runtimeVersion()).rejects.toMatchObject({ code: "CONSOLE_SESSION_EXPIRED" });
  });
});
