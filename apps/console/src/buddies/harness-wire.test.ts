import { describe, expect, it, vi } from "vitest";
import { createApi, snapshotHarnesses } from "../api";
import type { HarnessHealth } from "../api";
import { parseHarnessAccount } from "../harness-account";
import type { HarnessAccount } from "../harness-account";
import type { Snapshot } from "../types";
import { CHECKED_AT, harness } from "./harness.fixtures";

/**
 * What the harness view reads from the wire, before any of it is drawn: the
 * calls that check and that save a path, the rows of a snapshot, a quota
 * observation, and the account a row carries. What does not parse is dropped as
 * unknown; it is never shown as health.
 */

describe("the harness wire contract", () => {
  const codexRow: HarnessHealth = {
    adapter: "codex", status: "ready", available: true, revision: 4, manualPath: null,
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
      headers: { "Content-Type": "application/json", "X-Buddy-CSRF": "csrf" },
      body: JSON.stringify({ operation: "capabilities", params: { refresh: true, adapter: "codex" } }),
    }));
  });

  it("omits the adapter when every harness is checked", async () => {
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

  it("saves a path or null with the expected revision and refuses a mismatched reply", async () => {
    const saved = commandApi({ harness: codexRow });
    await expect(saved.api.harnessSet("codex", "/opt/bin/codex", 3, "csrf")).resolves.toEqual(codexRow);
    expect(body(saved.fetcher)).toEqual({
      operation: "harness_set", params: { adapter: "codex", path: "/opt/bin/codex", expectedRevision: 3 },
    });
    const cleared = commandApi({ harness: codexRow });
    await cleared.api.harnessSet("codex", null, 4, "csrf");
    expect(body(cleared.fetcher).params).toEqual({ adapter: "codex", path: null, expectedRevision: 4 });
    await expect(commandApi({ harness: { ...codexRow, adapter: "claude" } }).api.harnessSet("codex", "/opt/bin/codex", 4, "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    await expect(commandApi({}).api.harnessSet("codex", "/opt/bin/codex", 4, "csrf"))
      .rejects.toHaveProperty("code", "INVALID_RESPONSE");
  });

  it("parses free local qualification from snapshots and command replies without retaining certificates", async () => {
    const readOnlyStructured = { eligible: true, systemSandbox: true, reason: null, reasonCode: null, sameAttemptContinuation: true };
    const raw = { ...codexRow, systemSandbox: true, readOnlyStructured: { ...readOnlyStructured, verified: true },
      reviewVerification: { verified: true }, verified: true };
    const parsed = snapshotHarnesses({ harnesses: [raw] } as unknown as Snapshot)[0];
    expect(parsed.readOnlyStructured).toEqual(readOnlyStructured);
    expect(parsed.systemSandbox).toBe(true);
    expect(parsed).not.toHaveProperty("reviewVerification");
    expect(parsed).not.toHaveProperty("verified");
    await expect(commandApi({ harnesses: [raw] }).api.harnessRefresh("csrf", "codex")).resolves.toEqual([parsed]);
    await expect(commandApi({ harness: raw }).api.harnessSet("codex", null, 4, "csrf")).resolves.toEqual(parsed);
  });

  it("keeps discovery health but treats malformed local qualification as unknown", () => {
    const valid = { eligible: true, systemSandbox: true, reason: null, reasonCode: null, sameAttemptContinuation: true };
    for (const readOnlyStructured of [null, {}, { eligible: true }, { ...valid, systemSandbox: "true" },
      { ...valid, sameAttemptContinuation: 1 }, { ...valid, reasonCode: "readonly-resource-missing" },
      { ...valid, reason: undefined }, { verified: true }]) {
      const parsed = snapshotHarnesses({ harnesses: [{ ...codexRow, readOnlyStructured }] } as unknown as Snapshot)[0];
      expect(parsed.status).toBe("ready");
      expect(parsed).not.toHaveProperty("readOnlyStructured");
    }
  });

  it("reads the snapshot's harness rows in the fixed order and treats an absent field as none", () => {
    const dsh = { ...codexRow, adapter: "dsh", executable: "/usr/local/bin/dsh" };
    const rows = snapshotHarnesses({ harnesses: [codexRow, dsh] } as unknown as Snapshot);
    expect(rows.map(row => row.adapter)).toEqual(["dsh", "codex"]);
    expect(snapshotHarnesses({} as unknown as Snapshot)).toEqual([]);
    // A malformed row is dropped instead of presented as health.
    expect(snapshotHarnesses({ harnesses: [codexRow, { adapter: "zcode" }] } as unknown as Snapshot)
      .map(row => row.adapter)).toEqual(["codex"]);
  });

  it("drops a row whose state this console does not know instead of presenting it", () => {
    expect(snapshotHarnesses({ harnesses: [{ ...codexRow, status: "paused", available: false }] } as unknown as Snapshot)).toEqual([]);
  });
});

describe("a quota observation on the wire", () => {
  it("drops a malformed observation as unknown instead of failing the row", () => {
    const rows = snapshotHarnesses({
      harnesses: [harness({ adapter: "dsh", status: "ready",
        quota: { observedAt: CHECKED_AT, source: "dsh-native", windows: "near-limit" } as unknown as HarnessHealth["quota"] })],
    } as unknown as Snapshot);
    expect(rows).toHaveLength(1);
    expect(rows[0].quota ?? null).toBeNull();
    expect(snapshotHarnesses({ harnesses: [harness({ adapter: "dsh", status: "ready", quota: null })] } as unknown as Snapshot)[0].quota ?? null).toBeNull();
  });

  it("drops malformed exhaustion records as a whole instead of presenting part of them", () => {
    const record = { provider: "deepseek-official", limitId: null, code: "HARNESS_BALANCE_ZERO", source: "dsh-native",
      observedAt: CHECKED_AT, resetsAt: null, blocked: true, retry: null };
    const parse = (quotaRouting: unknown) => snapshotHarnesses({ harnesses: [harness({ adapter: "dsh", status: "ready", quotaRouting: quotaRouting as never })] } as unknown as Snapshot)[0].quotaRouting;
    expect(parse([record])).toEqual([record]);
    expect(parse([record, { ...record, retry: { eligibleAt: 42, open: "yes" } }])).toBeUndefined();
    expect(parse([{ ...record, provider: " " }])).toBeUndefined();
    expect(parse("blocked")).toBeUndefined();
  });
});

describe("native login facts on the wire", () => {
  const facts: HarnessAccount = { adapter: "codex", status: "logged-out", accountType: null,
    checkedAt: null, reasonCode: null, guidance: null };

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
