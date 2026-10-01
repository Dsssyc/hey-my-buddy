import { describe, expect, it, vi } from "vitest";
import { createApi, parseRoutingHealth, validRoutingConfiguration } from "./api";

describe("L4 single Router configuration", () => {
  const current = { revision: 4, routerProfileId: null,
    defaultRoutingMode: "fast", routingBudget: "brief" };
  it("accepts one Router and rejects retired selectors or budgets", () => {
    expect(validRoutingConfiguration(current)).toBe(true);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "review", routerProfileId: "review" })).toBe(true);
    expect(validRoutingConfiguration({ ...current, decisionProfileId: "old" })).toBe(false);
    for (const legacy of [{ fastRouterProfileId: "old" }, { reviewRouterProfileId: "old" },
      { fastRouterProfileId: null, reviewRouterProfileId: null }]) {
      expect(validRoutingConfiguration({ ...current, ...legacy })).toBe(false);
    }
    expect(validRoutingConfiguration({ ...current, routingBudget: "quick" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "other" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routerProfileId: undefined })).toBe(false);
  });
});

describe("snapshot upgrade boundary and routing health", () => {
  const configuration = { revision: 4, routerProfileId: null, defaultRoutingMode: "fast", routingBudget: "standard" };
  const snapshot = { csrfToken: "csrf", consoleSession: { id: "session", canWrite: true, reason: null },
    tableRevision: 1, configuration, configurationError: null, gate: { phase: "open" }, profiles: [], cards: [],
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], modelConcurrency: [],
    tasks: { runs: [] } };
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
  it("refuses legacy dual settings at the live snapshot parser", async () => {
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
