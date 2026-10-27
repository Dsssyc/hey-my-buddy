import i18n from "i18next";
import { describe, expect, it } from "vitest";
import {
  ApiError, createApi, parseConsoleAccess, parsePermissionLevels, parseReminderSettings,
  parseRoutingHealth, parseRuntimeVersion, snapshotHarnesses, validPreferences, validRoutingConfiguration,
} from "./api";
import { macroTaskSummary, macroTaskTimelineFixture } from "./macro-task-fixtures";
import type { Snapshot } from "./types";

const unsafe = Number.MAX_SAFE_INTEGER + 1;
const date = "Fri, 09 Oct 2026 18:05:00 GMT"; // Date.parse, rather than ISO-only validation.
const configuration = { routerBuddyIds: [" head ", "head"], routerRetryIntervalSeconds: 1,
  defaultRoutingMode: "fast", routingBudget: "brief" };
const snapshot = { csrfToken: "", eventCursor: 0, consoleSession: { id: " session " }, configuration,
  buddies: [], preferences: [], modelServiceNotes: [], modelServiceConcurrency: [], tasks: { pendingCount: 0 } };

// Keep undefined, NaN and unsafe integers intact at the parser boundary.
function apiFor(value: unknown, command = false) {
  return createApi("", async () => Object.assign(new Response(), {
    json: async () => command ? { ok: true, result: value } : value,
  }));
}
function without(value: Record<string, unknown>, key: string) {
  const copy = { ...value };
  delete copy[key];
  return copy;
}
function invalid(parse: () => unknown, key: string) {
  expect(parse).toThrow(ApiError);
  expect(parse).toThrow(expect.objectContaining({ code: "INVALID_RESPONSE", message: i18n.t(`common:error.${key}`) }));
}

describe("authenticated snapshot read boundaries", () => {
  it("preserves unknown fields and the untrimmed session id", async () => {
    const consoleSession = { id: " session ", extra: { retained: true } };
    const value = { ...snapshot, consoleSession, extra: true,
      configuration: { ...configuration, extra: true, routingBudgetLimits: "unchecked" } };
    const parsed = await apiFor(value).snapshot();
    expect(parsed).toMatchObject(value);
    expect(parsed.consoleSession).toBe(consoleSession);
    expect(parsed.configuration).toBe(value.configuration);
  });
  it("distinguishes snapshot, session, access and reminder refusals", async () => {
    for (const consoleSession of [undefined, null, [], {}, { id: "" }, { id: " \n " }, { id: 3 }]) {
      await expect(apiFor({ ...snapshot, consoleSession }).snapshot()).rejects.toMatchObject({
        code: "INVALID_RESPONSE", message: i18n.t("common:error.session"),
      });
    }
    for (const [key, error] of [["consoleAccess", "access"], ["reminderSettings", "reminders"]]) {
      await expect(apiFor({ ...snapshot, [key]: undefined }).snapshot()).resolves.toBeDefined();
      await expect(apiFor({ ...snapshot, [key]: null }).snapshot()).rejects.toMatchObject({
        code: "INVALID_RESPONSE", message: i18n.t(`common:error.${error}`),
      });
    }
    for (const key of ["eventCursor", "csrfToken", "configuration", "buddies", "preferences", "modelServiceNotes", "modelServiceConcurrency", "tasks"]) {
      await expect(apiFor(without(snapshot, key)).snapshot()).rejects.toMatchObject({
        code: "INVALID_RESPONSE", message: i18n.t("common:error.snapshot"),
      });
    }
  });
  it("checks safe snapshot counts and model service limits", async () => {
    for (const count of [-1, 0.5, unsafe, NaN, Infinity, "0", null, undefined]) {
      await expect(apiFor({ ...snapshot, eventCursor: count }).snapshot()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
      await expect(apiFor({ ...snapshot, tasks: { pendingCount: count } }).snapshot()).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    const entry = { harness: "dsh", provider: "fixture", model: "alpha", limit: Number.MAX_SAFE_INTEGER, active: 1 };
    for (const limit of [0, Number.MAX_SAFE_INTEGER + 1, 1.5, "33", null]) {
      await expect(apiFor({ ...snapshot, modelServiceConcurrency: [{ ...entry, limit }] }).snapshot())
        .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    const value = { ...snapshot, eventCursor: Number.MAX_SAFE_INTEGER, buddies: [null],
      modelServiceNotes: [false], modelServiceConcurrency: [entry], tasks: { pendingCount: Number.MAX_SAFE_INTEGER } };
    await expect(apiFor(value).snapshot()).resolves.toMatchObject(value);
  });
});

describe("access, reminders and policy settings", () => {
  const session = { id: "abcdef012345abcdef012345", lastSeen: -0.5, current: false, extra: true };
  const access = { requireLogin: false, revision: 0, sessions: [session], extra: true };
  const reminders = { silenceSeconds: 1, revision: 1, updatedAt: date, extra: true };
  it("retains access records, finite fractional timestamps and the 64-session limit", () => {
    expect(parseConsoleAccess(access)).toBe(access);
    expect(parseConsoleAccess({ ...access, revision: Number.MAX_SAFE_INTEGER, sessions: Array(64).fill(session) }).sessions).toHaveLength(64);
    for (const value of [null, {}, ...Object.keys(access).filter(k => k !== "extra").map(k => without(access, k)),
      { ...access, requireLogin: 0 }, { ...access, sessions: Array(65).fill(session) },
      ...[-1, 0.5, unsafe, "0", null].map(revision => ({ ...access, revision })),
      ...["ABCDEF012345abcdef012345", "abcdef", null, undefined].map(id => ({ ...access, sessions: [{ ...session, id }] })),
      ...[NaN, Infinity, "0", null, undefined].map(lastSeen => ({ ...access, sessions: [{ ...session, lastSeen }] })),
      { ...access, sessions: [{ ...session, current: null }] }]) invalid(() => parseConsoleAccess(value), "access");
  });
  it("keeps reminder range and Date.parse semantics, without normalizing strings", () => {
    expect(parseReminderSettings(reminders)).toBe(reminders);
    expect(parseReminderSettings({ ...reminders, silenceSeconds: 86400, revision: Number.MAX_SAFE_INTEGER }).updatedAt).toBe(date);
    for (const value of [null, ...["silenceSeconds", "revision", "updatedAt"].map(k => without(reminders, k)),
      ...[0, -1, 1.5, 86401, unsafe, "1", null].map(silenceSeconds => ({ ...reminders, silenceSeconds })),
      ...[0, 1.5, unsafe, "1", null].map(revision => ({ ...reminders, revision })),
      ...["", "not-a-date", 0, null].map(updatedAt => ({ ...reminders, updatedAt }))]) invalid(() => parseReminderSettings(value), "reminders");
  });
  it("allows unknown configuration data and distinct whitespace ids, but rejects retired keys even when undefined", () => {
    expect(validRoutingConfiguration({ ...configuration, extra: true, routingBudgetLimits: null })).toBe(true);
    for (const key of ["decisionBuddyId", "routerBuddyId", "fastRouterBuddyId", "reviewRouterBuddyId"]) {
      expect(validRoutingConfiguration({ ...configuration, [key]: undefined })).toBe(false);
    }
    for (const [key, values] of Object.entries({ routerBuddyIds: [null, undefined, [" "], ["a", "a"], [1]],
      routerRetryIntervalSeconds: [null, undefined, 0, 1.5, 2147483648, "1"],
      defaultRoutingMode: [null, undefined, "other"], routingBudget: [null, undefined, "quick"] })) {
      for (const value of values) expect(validRoutingConfiguration({ ...configuration, [key]: value })).toBe(false);
    }
    for (const mode of ["fast", "review"]) for (const budget of ["brief", "standard", "deep"]) {
      expect(validRoutingConfiguration({ ...configuration, routerRetryIntervalSeconds: 2147483647,
        defaultRoutingMode: mode, routingBudget: budget })).toBe(true);
    }
  });
  it("requires exactly three preference keys, including a present nullable note", () => {
    for (const mode of ["prefer", "pin", "exclude"]) for (const note of [null, "", " user note "]) {
      expect(validPreferences([{ buddyId: "", mode, note }])).toBe(true);
    }
    for (const entry of [{ buddyId: "a", mode: "pin" }, { buddyId: "a", mode: "pin", note: undefined },
      { buddyId: "a", mode: "pin", note: null, extra: undefined }, { buddyId: "a", mode: "other", note: null }, []]) {
      expect(validPreferences([entry])).toBe(false);
    }
  });
});

describe("optional Router observations", () => {
  const router = { buddyId: " head ", index: 0, eligible: true, extra: true };
  const health = { windowSize: 0, sampleCount: 0, failureCount: 0, consecutiveFailures: 0, abstentionCount: 0,
    cancelledCount: 0, lastSuccessAt: null, lastSuccessDecisionId: null, recentFailures: [], routers: [router], extra: true };
  it("preserves optional and nullable fields, unknown fields and raw strings", () => {
    expect(parseRoutingHealth(health)).toBe(health);
    const value = { ...health, reason: { code: "", params: {}, extra: true }, currentRouterBuddyId: "",
      routers: [{ ...router, identity: { harness: "", provider: "", model: "", effort: "", extra: true },
        code: null, skipUntil: "not-a-date", lastError: { at: "not-a-date", code: null } }],
      recentFailures: [{ decisionId: "", runId: null, at: "not-a-date", code: "", extra: true }],
      unattributed: [{ decisionId: "", at: "", outcome: "", code: null, extra: true }] };
    expect(parseRoutingHealth(value)).toBe(value);
    expect(parseRoutingHealth({ ...health, routers: [{ ...router, identity: null, lastError: null }] })).toBeDefined();
  });
  it("drops all health, while retaining the snapshot, for any malformed entry", async () => {
    const broken: unknown[] = [null, [], ...["lastSuccessAt", "lastSuccessDecisionId", "recentFailures"].map(k => without(health, k)),
      { ...health, reason: { code: "x", params: [] } }, { ...health, available: null }, { ...health, routers: null },
      { ...health, recentFailures: [{ decisionId: "d", at: "", code: "c" }] },
      { ...health, unattributed: [{ decisionId: "d", at: "", outcome: "x" }] }];
    for (const key of ["windowSize", "sampleCount", "failureCount", "consecutiveFailures", "abstentionCount", "cancelledCount",
      "budgetExhaustedCount", "boundsRejectedCount", "inputChangedCount", "attemptCount", "unattributedCount"]) {
      for (const count of [null, -1, 0.5, unsafe, "0"]) broken.push({ ...health, [key]: count });
    }
    for (const key of ["index", "consecutiveNoAnswers", "answeredCount", "noAnswerCount", "windowSize", "windowEntries",
      "windowAnsweredCount", "windowFailureCount", "windowBudgetExhaustedCount", "windowBoundsRejectedCount", "windowAttemptCount"]) {
      for (const count of [null, -1, unsafe, 0.5]) broken.push({ ...health, routers: [{ ...router, [key]: count }] });
    }
    for (const extra of [{ buddyId: " " }, { eligible: null }, { identity: [] }, { identity: {} },
      { inSkipWindow: null }, { retryInProgress: null }, { code: 0 }, { skipUntil: 0 }, { retryAt: 0 },
      { lastAnsweredAt: 0 }, { lastNoAnswerAt: 0 }, { lastError: { at: "" } }, { lastError: { code: null } },
      { lastError: { at: "", code: null, phase: 0 } }]) broken.push({ ...health, routers: [{ ...router, ...extra }] });
    for (const routingHealth of broken) {
      expect(parseRoutingHealth(routingHealth)).toBeUndefined();
      await expect(apiFor({ ...snapshot, routingHealth }).snapshot()).resolves.toMatchObject({ routingHealth: undefined });
    }
  });
});

describe("harness read normalization and optional observations", () => {
  const row = { harness: " codex ", status: "ready", revision: 0, extra: { retained: true } };
  const provider = { provider: " provider ", coolingUntil: date, consecutiveRejections: unsafe, extra: true };
  const rows = (value: unknown) => snapshotHarnesses({ harnesses: value } as Snapshot);
  it("normalizes only harness and manualPath, retaining nullable fields and unchecked facts", () => {
    const value = { ...row, revision: unsafe, executable: null, version: null, source: null, checkedAt: "not-a-date",
      command: null, candidates: [{ path: null, source: null, status: null }, []],
      billingByProvider: false, account: 3, providers: [provider], permissions: {} };
    expect(rows([value])).toEqual([{ ...value, harness: "codex", manualPath: null }]);
    expect(rows([value])[0].extra).toBe(value.extra);
    expect(rows([{ ...row, harness: "other" }, row, { ...row, harness: "dsh" }, { ...row, harness: "claude" },
      { ...row, harness: "zcode" }, { ...row, harness: "unknown" }]).map(x => x.harness))
      .toEqual(["dsh", "zcode", "codex", "claude", "other", "unknown"]);
  });
  it("discards providers or permissions independently without losing a valid row", () => {
    for (const providers of [undefined, null, {}, [{ ...provider, consecutiveRejections: 0 }],
      [{ ...provider, consecutiveRejections: 1.5 }], [{ ...provider, provider: " " }],
      [{ ...provider, coolingUntil: undefined }], [{ ...provider, coolingUntil: "bad" }], [provider, null]]) {
      expect(rows([{ ...row, providers, permissions: {} }])).toEqual([{ ...row, harness: "codex", manualPath: null, permissions: {} }]);
    }
    expect(rows([{ ...row, providers: [], permissions: null }])).toEqual([{ ...row, harness: "codex", manualPath: null, providers: [] }]);
    for (const permissions of [undefined, null, [], { read: undefined }, { unknown: "enforced" }, { write: "other" }]) {
      expect(parsePermissionLevels(permissions)).toBeNull();
      expect(rows([{ ...row, permissions }])).toEqual([{ ...row, harness: "codex", manualPath: null }]);
    }
    for (const enforcement of ["enforced", "convention"]) {
      expect(parsePermissionLevels({ none: enforcement, read: enforcement, write: enforcement, full: enforcement }))
        .toEqual({ none: enforcement, read: enforcement, write: enforcement, full: enforcement });
    }
  });
  it("drops a malformed core row and accepts all five statuses", () => {
    for (const status of ["unknown", "ready", "missing", "login-required", "unhealthy"]) expect(rows([{ ...row, status }])).toHaveLength(1);
    for (const extra of [{ harness: " " }, { status: "other" }, { revision: -1 }, { revision: 0.5 },
      { manualPath: 3 }, { executable: 3 }, { version: 3 }, { source: 3 }, { checkedAt: 3 }, { expiresAt: 3 },
      { reason: "bad" }, { command: [3] }, { candidates: [null] }, { candidates: [{ path: 3 }] },
      { candidates: [{ source: 3 }] }, { candidates: [{ status: 3 }] }, { candidates: [{ reason: {} }] }]) {
      expect(rows([{ ...row, ...extra }])).toEqual([]);
    }
    for (const value of [null, {}, "rows"]) expect(rows(value)).toEqual([]);
  });
});

describe("storage, macro task and version response boundaries", () => {
  const category = { id: " ", bytes: 0, reclaimableBytes: 0, count: 0, eligibleCount: 0, reasons: [""], extra: true };
  const candidate = { id: "c", category: "unlisted", path: "", bytes: 0, eligible: false, reasons: [], extra: true };
  const orphan = { pid: 1, kind: "", stateDir: null, runtimeDir: "", extra: true };
  const plan = { planId: " ", createdAt: date, expiresAt: date, categories: [category], candidates: [candidate], orphanProcesses: [orphan], extra: true };
  const apply = { planId: " ", removedBytes: 0, complete: true, removed: [{ id: " ", path: "", bytes: 0, extra: true }],
    skipped: [{ id: "s", path: "", reasons: [""], extra: true }], extra: true };
  it("preserves storage extras, empty paths, whitespace ids and Date.parse timestamps", async () => {
    const parsed = await apiFor(plan, true).storagePlan("csrf");
    expect(parsed).toMatchObject(plan);
    expect(parsed.categories).toBe(plan.categories);
    await expect(apiFor({ ...plan, candidates: [candidate, candidate] }, true).storagePlan("csrf")).resolves.toBeDefined();
    await expect(apiFor(apply, true).storageApply(" ", "command", "csrf")).resolves.toMatchObject(apply);
  });
  it("requires storage fields, safe counts and positive pids, rejecting only duplicate category ids", async () => {
    const broken = [null, ...Object.keys(plan).filter(k => k !== "extra").map(k => without(plan, k)),
      { ...plan, categories: [category, category] }, { ...plan, categories: [null] },
      { ...plan, candidates: [{ ...candidate, category: "" }] }, { ...plan, candidates: [{ ...candidate, eligible: null }] },
      { ...plan, orphanProcesses: [{ ...orphan, stateDir: undefined }] }];
    for (const key of ["bytes", "reclaimableBytes", "count", "eligibleCount"]) for (const count of [-1, 0.5, unsafe, "0", null]) {
      broken.push({ ...plan, categories: [{ ...category, [key]: count }] });
    }
    for (const pid of [0, -1, 0.5, unsafe, "1"]) broken.push({ ...plan, orphanProcesses: [{ ...orphan, pid }] });
    for (const value of broken) await expect(apiFor(value, true).storagePlan("csrf")).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: i18n.t("common:error.storagePlan") });
    for (const value of [without(apply, "complete"), { ...apply, complete: false }, { ...apply, removedBytes: unsafe },
      { ...apply, removed: [{ ...apply.removed[0], bytes: -1 }] }, { ...apply, skipped: [{ ...apply.skipped[0], reasons: [null] }] }]) {
      await expect(apiFor(value, true).storageApply(" ", "command", "csrf")).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: i18n.t("common:error.cleanup") });
    }
  });
  it("keeps permissive timeline projections and integer cursors while checking summary safe counts", async () => {
    const macroTask = { ...macroTaskSummary(), createdAt: "bad", lastActivityAt: "bad", extra: true };
    const page = { macroTasks: [macroTask], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: "", cursor: -unsafe, changed: false, extra: true };
    await expect(apiFor(page).macroTasks({})).resolves.toMatchObject(page);
    for (const value of [{ ...page, macroTasks: [{ ...macroTask, lastActivitySeq: unsafe }] },
      { ...page, macroTasks: [{ ...macroTask, counts: { ...macroTask.counts, roots: -1 } }] },
      { ...page, macroTasks: [{ ...macroTask, matchingRuns: unsafe }] }, { ...page, totals: { ...page.totals, all: unsafe } }]) {
      await expect(apiFor(value).macroTasks({})).rejects.toMatchObject({ code: "INVALID_RESPONSE", message: i18n.t("common:error.macroTasks") });
    }
    const timeline = { ...macroTaskTimelineFixture(), observedAt: "bad", cursor: -unsafe,
      rows: [{ delegationAuthority: false, title: null, taskSummary: null, extra: true }], events: [null],
      spans: [{ expectedSeconds: null, reminders: null, inquiries: null, routing: 7 }],
      totals: { rows: -unsafe, allRows: -1, spans: -2, events: -3 } };
    await expect(apiFor(timeline).macroTaskTimeline("id", {})).resolves.toMatchObject({ timeline });
    for (const relation of [{ fixes: undefined }, { fixesTitle: undefined }, { fixesTitleSource: undefined }, { fixedBy: undefined }]) {
      await expect(apiFor({ ...timeline, rows: [{ ...timeline.rows[0], ...relation }] }).macroTaskTimeline("id", {}))
        .rejects.toMatchObject({ code: "INVALID_RESPONSE", message: i18n.t("common:error.timeline") });
    }
  });
  it("requires every summary field and count while keeping its exact enums and nullable fields", async () => {
    const summary = macroTaskSummary();
    const read = (value: unknown) => apiFor({ macroTasks: [value], totals: { all: 0, active: 0, host: 0, review: 0 },
      nextCursor: null, cursor: 0, changed: false }).macroTasks({});
    for (const key of Object.keys(summary)) {
      await expect(read(without(summary, key))).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    for (const key of Object.keys(summary.counts)) {
      await expect(read({ ...summary, counts: without(summary.counts, key) })).rejects.toHaveProperty("code", "INVALID_RESPONSE");
      await expect(read({ ...summary, counts: { ...summary.counts, [key]: Number.MAX_SAFE_INTEGER } })).resolves.toBeDefined();
    }
    for (const [key, values] of Object.entries({ kind: ["macro-task", "standalone"],
      titleSource: ["macro-task", "title", "task", "none"], state: ["host", "active", "review", "ended"] })) {
      for (const value of values) await expect(read({ ...summary, [key]: value })).resolves.toBeDefined();
      await expect(read({ ...summary, [key]: "other" })).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
    for (const key of ["title", "description", "summary", "project", "sourceHostId", "createdAt", "lastActivityAt"]) {
      await expect(read({ ...summary, [key]: null })).resolves.toBeDefined();
      await expect(read({ ...summary, [key]: undefined })).rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });
  it("checks execution-only timing and router identity without validating other routing data", async () => {
    const timeline = macroTaskTimelineFixture();
    const inquiry = { inquiryId: " ", createdAt: date, state: " ", extra: true };
    const span = { kind: "execution", expectedSeconds: Number.MAX_SAFE_INTEGER, reminders: null, inquiries: [inquiry],
      routing: { routerBuddy: { harness: "", provider: "", model: "", effort: "", extra: true }, selectedBuddy: 3 } };
    await expect(apiFor({ ...timeline, spans: [span] }).macroTaskTimeline("id", {})).resolves.toMatchObject({ timeline: { spans: [span] } });
    for (const change of [{ expectedSeconds: unsafe }, { inquiries: null },
      { inquiries: [{ ...inquiry, createdAt: "bad" }] }, { routing: { routerBuddy: {} } },
      { routing: { routerBuddy: { ...span.routing.routerBuddy, effort: null } } }]) {
      await expect(apiFor({ ...timeline, spans: [{ ...span, ...change }] }).macroTaskTimeline("id", {}))
        .rejects.toHaveProperty("code", "INVALID_RESPONSE");
    }
  });
  it("distinguishes nullable version facts from missing ones, preserving raw strings and extras", () => {
    const facts = { softwareVersion: null, contractVersion: " version ", sourceCommit: " commit ", schemaVersion: 0, installedAt: date, extra: true };
    const value = { running: { ...facts, mode: "source" }, installed: { ...facts, mode: "unchecked" }, extra: true };
    expect(parseRuntimeVersion(value)).toBe(value);
    for (const mode of ["source", "runtime"]) expect(parseRuntimeVersion({ ...value, running: { ...facts, mode } })).toBeDefined();
    for (const key of Object.keys(facts).filter(k => k !== "extra")) {
      invalid(() => parseRuntimeVersion({ ...value, installed: without(facts, key) }), "version");
      invalid(() => parseRuntimeVersion({ ...value, running: without(value.running, key) }), "version");
    }
    for (const extra of [{ softwareVersion: " " }, { contractVersion: 3 }, { sourceCommit: "" },
      { schemaVersion: unsafe }, { schemaVersion: -1 }, { schemaVersion: "0" }, { installedAt: "bad" }]) {
      invalid(() => parseRuntimeVersion({ ...value, installed: { ...facts, ...extra } }), "version");
    }
  });
});
