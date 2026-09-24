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
