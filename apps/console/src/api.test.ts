import { describe, expect, it, vi } from "vitest";
import { createApi, validRoutingConfiguration } from "./api";

describe("0.20.0 routing configuration", () => {
  const current = { revision: 4, fastRouterProfileId: "fast", reviewRouterProfileId: null,
    defaultRoutingMode: "fast", routingBudget: "brief" };
  it("accepts both Router slots and rejects the old public selector or budget", () => {
    expect(validRoutingConfiguration(current)).toBe(true);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "review", reviewRouterProfileId: "review" })).toBe(true);
    expect(validRoutingConfiguration({ ...current, decisionProfileId: "old" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, routingBudget: "quick" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, defaultRoutingMode: "other" })).toBe(false);
    expect(validRoutingConfiguration({ ...current, fastRouterProfileId: undefined })).toBe(false);
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
