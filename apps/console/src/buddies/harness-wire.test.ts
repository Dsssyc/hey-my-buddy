import { describe, expect, it, vi } from "vitest";
import { createApi, snapshotHarnesses } from "../api";
import type { HarnessHealth } from "../api";
import { parseHarnessAccount } from "../harness-account";
import type { HarnessAccount } from "../harness-account";
import type { Snapshot } from "../types";
import { CHECKED_AT, harness } from "./harness.fixtures";

/**
 * What the harness view reads from the wire, before any of it is drawn: the
 * calls that check and that save a path, the rows of a snapshot, provider
 * cooling records, and the account a row carries. What does not parse is dropped as
 * unknown; it is never shown as health.
 */

describe("the harness wire contract", () => {
  it("reads current health and candidate reason envelopes and refuses their old text shape", () => {
    const reason = { code: "harness.not-found", params: { operation: "probe" } };
    const row = harness({ harness: "codex", status: "missing", reason,
      candidates: [{ path: "/missing/codex", status: "missing", reason }] });
    expect(snapshotHarnesses({ harnesses: [row] } as unknown as Snapshot)).toEqual([row]);
    expect(snapshotHarnesses({ harnesses: [{ ...row, reason: "not found" }] } as unknown as Snapshot)).toEqual([]);
    expect(snapshotHarnesses({ harnesses: [{ ...row, candidates: [{ reason: "not found" }] }] } as unknown as Snapshot)).toEqual([]);
  });
  const codexRow: HarnessHealth = {
    harness: "codex", status: "ready", revision: 4, manualPath: null,
    command: ["/opt/bin/codex"], executable: "/opt/bin/codex", version: "0.157.0", source: "PATH",
    candidates: [], checkedAt: CHECKED_AT,
  };

  function commandApi(result: unknown) {
    const fetcher = vi.fn<typeof fetch>(async () => new Response(JSON.stringify({ ok: true, result })));
    return { api: createApi("/private/", fetcher), fetcher };
  }
  const body = (fetcher: ReturnType<typeof commandApi>["fetcher"]) =>
    JSON.parse(String(fetcher.mock.calls[0][1]?.body));

  it("checks through the existing command route with the session CSRF header", async () => {
    const { api, fetcher } = commandApi({ harnesses: [codexRow] });
    await expect(api.harnessRefresh("csrf", "codex")).resolves.toEqual([codexRow]);
    expect(fetcher).toHaveBeenCalledWith("/private/api/command", expect.objectContaining({
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-Hey-My-Buddy-CSRF": "csrf" },
      body: JSON.stringify({ operation: "capabilities", params: { refresh: true, harness: "codex" } }),
    }));
  });

  it("omits the harness when every harness is checked", async () => {
    const { api, fetcher } = commandApi({ harnesses: [codexRow] });
    await api.harnessRefresh("csrf");
    expect(body(fetcher)).toEqual({ operation: "capabilities", params: { refresh: true } });
  });

  it("refuses a reply to a check that has no harness rows", async () => {
    await expect(commandApi({ selection: true }).api.harnessRefresh("csrf", "codex"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(commandApi({ harnesses: [] }).api.harnessRefresh("csrf", "codex"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("saves a path or null without a revision and refuses a mismatched reply", async () => {
    const saved = commandApi({ harness: codexRow });
    await expect(saved.api.harnessSet("codex", "/opt/bin/codex", "csrf")).resolves.toEqual(codexRow);
    expect(body(saved.fetcher)).toEqual({
      operation: "harness_set", params: { harness: "codex", path: "/opt/bin/codex" },
    });
    const cleared = commandApi({ harness: codexRow });
    await cleared.api.harnessSet("codex", null, "csrf");
    expect(body(cleared.fetcher).params).toEqual({ harness: "codex", path: null });
    await expect(commandApi({ harness: { ...codexRow, harness: "claude" } }).api.harnessSet("codex", "/opt/bin/codex", "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(commandApi({}).api.harnessSet("codex", "/opt/bin/codex", "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("parses the same permission declarations from snapshots and command replies", async () => {
    const permissions = { none: "enforced", read: "enforced", write: "convention", full: "enforced" };
    const raw = { ...codexRow, permissions };
    const parsed = snapshotHarnesses({ harnesses: [raw] } as unknown as Snapshot)[0];
    expect(parsed.permissions).toEqual(permissions);
    await expect(commandApi({ harnesses: [raw] }).api.harnessRefresh("csrf", "codex")).resolves.toEqual([parsed]);
    await expect(commandApi({ harness: raw }).api.harnessSet("codex", null, "csrf")).resolves.toEqual(parsed);
  });

  it("treats malformed declarations as unknown", () => {
    for (const permissions of [null, [], { read: true }, { read: "sandbox" }, { review: "enforced" }]) {
      const parsed = snapshotHarnesses({ harnesses: [{ ...codexRow, permissions }] } as unknown as Snapshot)[0];
      expect(parsed.status).toBe("ready");
      expect(parsed).not.toHaveProperty("permissions");
    }
  });

  it("reads the snapshot's harness rows in the fixed order and treats an absent field as none", () => {
    const dsh = { ...codexRow, harness: "dsh", executable: "/usr/local/bin/dsh" };
    const rows = snapshotHarnesses({ harnesses: [codexRow, dsh] } as unknown as Snapshot);
    expect(rows.map(row => row.harness)).toEqual(["dsh", "codex"]);
    expect(snapshotHarnesses({} as unknown as Snapshot)).toEqual([]);
    // A malformed row is dropped instead of presented as health.
    expect(snapshotHarnesses({ harnesses: [codexRow, { harness: "zcode" }] } as unknown as Snapshot)
      .map(row => row.harness)).toEqual(["codex"]);
  });

  it("drops a row whose state this console does not know instead of presenting it", () => {
    expect(snapshotHarnesses({ harnesses: [{ ...codexRow, status: "paused" }] } as unknown as Snapshot)).toEqual([]);
  });
});

describe("provider cooling on the wire", () => {
  const record = { provider: "openai", coolingUntil: "2026-10-19T12:00:00Z", consecutiveRejections: 2 };
  const parse = (providers: unknown) => snapshotHarnesses({ harnesses: [harness({ harness: "codex", status: "ready", providers: providers as never })] } as unknown as Snapshot)[0].providers;
  it("reads active, expired and manually cleared records", () => {
    const providers = [record, { ...record, provider: "other", coolingUntil: null }];
    expect(parse(providers)).toEqual(providers);
  });
  it("rejects malformed provider records at the boundary", () => {
    for (const providers of [[{ ...record, coolingUntil: "invalid" }], [{ ...record, consecutiveRejections: 0 }], [{ ...record, provider: " " }], "cooling"])
      expect(parse(providers)).toBeUndefined();
  });
});

describe("native login facts on the wire", () => {
  const facts: HarnessAccount = { harness: "codex", status: "logged-out", accountType: null,
    checkedAt: null, reason: null };

  it("accepts only the named login facts of the requested harness", () => {
    expect(parseHarnessAccount(facts, "codex")).toEqual(facts);
    expect(parseHarnessAccount(facts, "claude")).toBeNull();
    expect(parseHarnessAccount({ ...facts, status: "configured" }, "codex")).toBeNull();
    expect(parseHarnessAccount({ ...facts, checkedAt: 42 }, "codex")).toBeNull();
    expect(parseHarnessAccount({ ...facts, apiKey: "not-public" }, "codex")).toEqual(facts);
    expect(parseHarnessAccount({ ...facts, plan: "plus" }, "codex")).toEqual({ ...facts, plan: "plus" });
    expect(parseHarnessAccount({ ...facts, plan: false }, "codex")).toBeNull();
  });
});
