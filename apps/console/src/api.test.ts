import { describe, expect, it, vi } from "vitest";
import { createApi } from "./api";

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
