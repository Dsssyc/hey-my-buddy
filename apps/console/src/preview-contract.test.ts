import i18n from "i18next";
import { parseActor } from "./actor";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { ApiError, createApi, snapshotHarnesses } from "./api";
import { createTaskDetailClient } from "./task-detail/client";
import { parseTask } from "./task-detail/parse";
import { usageLabel } from "./task-detail/usage";
import type { Snapshot } from "./types";

/**
 * Cross-boundary regression for the synthetic preview data (ADR-018 leftover
 * defect: the preview snapshot lagged behind the schema and the new console
 * refused to load it).
 *
 * This test consumes the synthetic scenario tree emitted by
 * `tests/probes/macro_task_console_preview.py --emit-fixtures` through the real
 * console parsers — `createApi(...).snapshot()`, `.macroTasks()`,
 * `.macroTaskTimeline()`, the `workflow_get` command path and
 * `parseWorkflowReply`. Only synthetic Router settings and eligibility are
 * re-authored for L4 at the test boundary below. A future
 * snapshot shape the frontend refuses, or preview data that stops matching the
 * parser, fails here; `tests/python/cli/test_host_preview.py` additionally asserts
 * the resulting report and proves the emitted tree is deterministic, so the
 * guard is part of the normal `hey_my_buddy.cli.checks` run.
 *
 * With no `HMB_PREVIEW_FIXTURES` in the environment (a plain `npm test`) the
 * script is run into a private temporary directory first.
 */

type ManifestEntry = {
  scenario: string;
  path: string;
  kind: "console-snapshot" | "lineage-records" | "runtime-version" | "macro-tasks-page" | "macro-task-timeline" | "workflow-get"
    | "workflow-audit" | "message-list";
  endpoint: string;
  httpStatus: number;
  expected: "accepted" | "rejected";
  expectedCode?: string;
  request?: { runId?: string; macroTaskId?: string };
  mutation?: string;
  note?: string;
};

type Manifest = {
  fixtureVersion: number;
  source: string;
  scenarios: string[];
  files: ManifestEntry[];
};

const testDirectory = dirname(fileURLToPath(import.meta.url));
const repositoryRoot = resolve(testDirectory, "..", "..", "..");
const previewScript = join(repositoryRoot, "tests", "probes", "macro_task_console_preview.py");

/** The preview script's emitted tree, generated once for this test file. */
function resolveFixtures(): { directory: string; generated: boolean } {
  const configured = process.env.HMB_PREVIEW_FIXTURES;
  if (configured) return { directory: resolve(configured), generated: false };
  const python = process.env.HMB_PREVIEW_PYTHON ?? "python3";
  const directory = mkdtempSync(join(tmpdir(), "hey-my-buddy-preview-"));
  const previewEnvironment = { ...process.env };
  for (const key of ["HMB_STATE_DIR", "HMB_RUNTIME_ROOT", "HMB_RUNTIME", "HMB_RUNTIME_IDENTITY",
    "HMB_WORKER_STATE", "HMB_WORKER_ID", "HMB_AGENT_CREDENTIAL_FILE", "HMB_AGENT_CREDENTIAL_FD",
    "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"]) delete previewEnvironment[key];
  const result = spawnSync(python, [previewScript, "--emit-fixtures", directory], {
    cwd: repositoryRoot, encoding: "utf8", env: previewEnvironment,
  });
  if (result.status !== 0) {
    throw new Error(`could not emit the preview fixtures with ${python}: ${result.stdout ?? ""}${result.stderr ?? ""}`);
  }
  return { directory, generated: true };
}

const fixtures = resolveFixtures();
const fixtureDir = fixtures.directory;
afterAll(() => { if (fixtures.generated) rmSync(fixtureDir, { recursive: true, force: true }); });

const reportPath = process.env.HMB_PREVIEW_REPORT ?? "";
const manifest = JSON.parse(readFileSync(join(fixtureDir, "manifest.json"), "utf8")) as Manifest;

function bodyFor(entry: ManifestEntry): unknown {
  const value = JSON.parse(readFileSync(join(fixtureDir, entry.path), "utf8")) as unknown;
  // `workflow_get` fixtures store the compact reply payload itself; the real
  // command route wraps it in `{ok: true, result}`. The recorded error
  // envelopes already carry `ok: false` and stay exactly as emitted.
  if (["workflow-get", "workflow-audit", "message-list"].includes(entry.kind) && value && typeof value === "object" && !("ok" in value)) {
    return { ok: true, result: value };
  }
  return value;
}

/** The real `createApi` request path against one fixed fixture response. */
function apiFor(entry: ManifestEntry) {
  const original = bodyFor(entry);
  const body = JSON.stringify(original);
  const fetcher = (async () => new Response(body, {
    status: entry.httpStatus,
    headers: { "Content-Type": "application/json" },
  })) as typeof fetch;
  return createApi("", fetcher);
}

type Outcome = {
  scenario: string;
  path: string;
  kind: string;
  accepted: boolean;
  code: string | null;
  facts: Record<string, unknown>;
};

async function parseEntry(entry: ManifestEntry): Promise<Outcome> {
  const api = apiFor(entry);
  const outcome: Outcome = { scenario: entry.scenario, path: entry.path, kind: entry.kind,
    accepted: false, code: null, facts: {} };
  try {
    if (entry.kind === "console-snapshot") {
      const snapshot: Snapshot = await api.snapshot();
      const harnesses = snapshotHarnesses(snapshot);
      outcome.facts = {
        configuration: snapshot.configuration,
        // The slim snapshot carries the server's board-wide pending count;
        // each micro task has its own detail read.
        pendingCount: snapshot.tasks.pendingCount,
        buddyEffortRanks: snapshot.buddies.map(buddy => ({ buddyId: buddy.buddyId, effortRank: buddy.effortRank })),
        harnesses: harnesses.map(row => ({
          harness: row.harness,
          status: row.status,
          providers: row.providers,
        })),
      };
    } else if (entry.kind === "lineage-records") {
      // Internal task views support the probe's lineage checks; this is no HTTP read.
      const records = bodyFor(entry) as { runs: import("./types").Task[]; total: number; nextCursor: string | null };
      for (const run of records.runs.filter(run => run.workflow)) parseTask(run, run.runId);
      outcome.facts = { total: records.total, runs: records.runs.length, nextCursor: records.nextCursor };
    } else if (entry.kind === "runtime-version") {
      const version = await api.runtimeVersion();
      outcome.facts = { mode: version.running.mode, softwareVersion: version.running.softwareVersion,
        installed: version.installed !== null };
    } else if (entry.kind === "macro-tasks-page") {
      const page = await api.macroTasks({});
      outcome.facts = { totals: page.totals, cursor: page.cursor, changed: page.changed };
    } else if (entry.kind === "macro-task-timeline") {
      const { timeline } = await api.macroTaskTimeline(entry.request?.macroTaskId ?? "", {});
      for (const event of timeline.events) if (event.actor !== null) expect(parseActor(event.actor)).toEqual(event.actor);
      outcome.facts = {
        macroTaskId: timeline.macroTask.macroTaskId,
        rows: timeline.rows.length,
        spans: timeline.spans.length,
        events: timeline.events.length,
        truncated: timeline.truncated,
        filtered: timeline.filtered,
      };
    } else if (entry.kind === "workflow-audit") {
      const { audit, routing } = (await createTaskDetailClient(api, "fixture-csrf").audit(entry.request?.runId ?? "", [])).value;
      outcome.facts = {
        turns: audit.turns.map(turn => ({ turnId: turn.turnId, summary: turn.summary })),
        continuations: audit.continuations.map(item => ({ turnId: item.turnId, input: item.input })),
        requests: audit.requests.map(request => ({ requestId: request.requestId, state: request.state, decision: request.decision?.decision ?? null })),
        routing: routing.map(entry => ({ decisionId: entry.decisionId, source: entry.source, routerBuddy: entry.routerBuddy })),
      };
    } else if (entry.kind === "message-list") {
      const { items, more } = (await createTaskDetailClient(api, "fixture-csrf").inquiries(entry.request?.runId ?? "")).value;
      outcome.facts = { more, inquiries: items.map(item => ({ messageId: item.messageId, attemptId: item.attemptId, state: item.state, answered: item.answer !== null })) };
    } else {
      const runId = entry.request?.runId ?? "";
      const { value: workflow } = await createTaskDetailClient(api, "fixture-csrf").workflow(runId);
      expect(workflow.hostId === null || typeof workflow.hostId === "string").toBe(true);
      if (workflow.hostConclusion?.actor) expect(parseActor(workflow.hostConclusion.actor)).toEqual(workflow.hostConclusion.actor);
      for (const record of workflow.integrations) if (record.actor) expect(parseActor(record.actor)).toEqual(record.actor);
      const t = i18n.getFixedT(null, "taskDetail");
      const conclusion = workflow.hostConclusion;
      outcome.facts = {
        runId: workflow.runId,
        hostId: workflow.hostId,
        revision: workflow.revision,
        buddyLocked: workflow.buddyLocked,
        hostConclusion: conclusion && {
          executionStatus: conclusion.executionStatus,
          statusLabel: t(`acceptance.status.${conclusion.executionStatus}` as "acceptance.status.failed", { defaultValue: conclusion.executionStatus }),
          failed: conclusion.executionStatus === "failed", cancelled: conclusion.executionStatus === "cancelled",
        },
        turns: workflow.turns.map(turn => ({
          turnIndex: turn.turnIndex,
          attemptId: turn.attemptId,
          tokenUsage: usageLabel(t, turn.tokenUsage),
          tokenUsageTitle: usageLabel(t, turn.tokenUsage),
          source: turn.tokenUsage?.source ?? null,
        })),
        currentTurnUsage: workflow.currentTurn ? usageLabel(t, workflow.currentTurn.tokenUsage) : null,
        artifacts: workflow.artifacts.map(item => ({
          artifactId: item.artifactId,
          kind: item.kind,
          state: { partial: item.partial, verified: item.verified === true, final: item.final === true },
          cumulativePatch: item.cumulativePatch && { complete: Object.values({ ...item.cumulativePatch, changedPaths: true }).every(Boolean) },
        })),
        finalArtifactId: workflow.finalArtifactId,
        hostPaths: workflow.integrations.map(record => record.hostPaths),
      };
    }
    outcome.accepted = true;
  } catch (error) {
    outcome.code = error instanceof ApiError ? error.code : error instanceof Error ? "ERROR" : "UNKNOWN";
    outcome.facts = { refusal: error instanceof Error ? error.message : String(error) };
  }
  return outcome;
}

describe("synthetic preview fixtures through the real console parsers", () => {
  it("declares the seven preview reads and internal lineage records and a self-consistent manifest", () => {
    expect(manifest.fixtureVersion).toBe(1);
    expect(manifest.source).toBe("tests/probes/macro_task_console_preview.py");
    expect(manifest.scenarios).toEqual(["normal", "readonly", "truncated", "error"]);
    expect(new Set(manifest.files.map(entry => entry.kind))).toEqual(
      new Set(["console-snapshot", "lineage-records", "runtime-version", "macro-tasks-page", "macro-task-timeline", "workflow-get", "workflow-audit", "message-list"]));
    for (const entry of manifest.files) {
      expect(entry.expected === "accepted" ? entry.expectedCode === undefined : typeof entry.expectedCode === "string").toBe(true);
    }
  });

  it("parses every fixture exactly as the manifest records, including the refused shapes", async () => {
    const outcomes: Outcome[] = [];
    for (const entry of manifest.files) outcomes.push(await parseEntry(entry));
    const failed = outcomes.filter((outcome) => {
      const entry = manifest.files.find(item => item.path === outcome.path)!;
      return entry.expected === "accepted"
        ? !outcome.accepted
        : outcome.accepted || outcome.code !== entry.expectedCode;
    });
    expect(failed, JSON.stringify(failed.map(item => `${item.path}: accepted=${item.accepted} code=${item.code} ${String(item.facts.refusal ?? "")}`))).toEqual([]);

    // The four preview scenarios stay covered, and the error scenario stays visibly broken.
    const byScenario = new Map<string, Outcome[]>();
    for (const outcome of outcomes) byScenario.set(outcome.scenario, [...(byScenario.get(outcome.scenario) ?? []), outcome]);
    const entryFor = (outcome: Outcome) => manifest.files.find(item => item.path === outcome.path)!;
    expect([...byScenario.keys()].sort()).toEqual([...manifest.scenarios, "incompatible"].sort());
    expect(byScenario.get("readonly")!.some(outcome => outcome.kind === "console-snapshot" && outcome.code === "INVALID_RESPONSE")).toBe(true);
    expect(byScenario.get("readonly")!.some(outcome => outcome.kind === "workflow-get" && outcome.accepted)).toBe(true);
    expect(byScenario.get("truncated")!.find(outcome => outcome.kind === "macro-task-timeline")!.accepted).toBe(true);
    expect(byScenario.get("error")!.filter(outcome => !outcome.accepted).map(outcome => outcome.code))
      .toEqual(["SERVICE_UNAVAILABLE", "SERVICE_UNAVAILABLE"]);
    const incompatible = byScenario.get("incompatible")!;
    expect(incompatible.filter(outcome => !outcome.accepted).map(outcome => outcome.code))
      .toEqual(incompatible.filter(outcome => entryFor(outcome).expected === "rejected").map(() => "INVALID_RESPONSE"));
    expect(incompatible.filter(outcome => !outcome.accepted)).toHaveLength(4);

    report({ fixtureVersion: manifest.fixtureVersion, fixtureDir, outcomes });
  });

  it("keeps the ADR-018 fields readable from the fixture data", async () => {
    const outcomes: Outcome[] = [];
    for (const entry of manifest.files.filter(item => item.scenario === "normal")) outcomes.push(await parseEntry(entry));
    const snapshots = outcomes.filter(outcome => outcome.kind === "console-snapshot");
    const workflows = outcomes.filter(outcome => outcome.kind === "workflow-get");
    expect(snapshots).toHaveLength(1);
    expect(workflows.length).toBeGreaterThanOrEqual(3);

    // A preview snapshot must carry the single Router and the model service note
    // schema the current console requires (the original schema-13 defect).
    const snapshotFacts = snapshots[0].facts as { configuration: Record<string, unknown>; harnesses: unknown[] };
    expect(snapshotFacts.configuration).toMatchObject({
      routerBuddyIds: expect.any(Array),
      routerRetryIntervalSeconds: expect.any(Number),
      defaultRoutingMode: "fast", routingBudget: "standard",
    });
    expect("decisionBuddyId" in snapshotFacts.configuration).toBe(false);
    expect("routerBuddyId" in snapshotFacts.configuration).toBe(false);

    // Two providers on one harness retain independent cooling states.
    const normalSnapshot = await apiFor(manifest.files.find(entry => entry.scenario === "normal" && entry.kind === "console-snapshot")!).snapshot();
    expect(snapshotHarnesses(normalSnapshot).find(row => row.harness === "dsh")).toMatchObject({
      billingByProvider: { "deepseek-official": { kind: "metered" } },
      providers: [
        { provider: "deepseek-official", coolingUntil: expect.any(String), consecutiveRejections: 2 },
        { provider: "deepseek-partner", coolingUntil: null, consecutiveRejections: 1 },
      ],
    });
    const providerFacts = snapshotFacts.harnesses as { harness: string; providers: { coolingUntil: string | null }[] }[];
    expect(providerFacts.find(row => row.harness === "zcode")?.providers[0].coolingUntil).toBe("2020-01-01T00:00:00.000Z");
    expect(providerFacts.find(row => row.harness === "codex")?.providers).toEqual([]);

    // Per-execution usage: known, partial-unknown and unknown all appear, and
    // no value is a session cumulative.
    const turns = workflows.flatMap(workflow => (workflow.facts.turns as { tokenUsage: string }[]) ?? []);
    expect(turns.some(turn => turn.tokenUsage.includes("含缓存"))).toBe(true);
    expect(turns.some(turn => turn.tokenUsage.includes("未知"))).toBe(true);

    // Host conclusion, partial output, cumulative patch and Host paths.
    const concluded = workflows.find(workflow => workflow.facts.hostConclusion !== null)!;
    const conclusion = concluded.facts.hostConclusion as { executionStatus?: string; statusLabel: string };
    expect(["失败", "已取消"]).toContain(conclusion.statusLabel);

    const artifacts = workflows.flatMap(workflow => (workflow.facts.artifacts as {
      state: { partial: boolean; verified: boolean; final: boolean }; cumulativePatch: { complete: boolean } | null;
    }[]) ?? []);
    const partial = artifacts.find(item => item.state.partial)!;
    expect(partial).toBeTruthy();
    expect(partial.state.verified).toBe(false);
    expect(partial.state.final).toBe(false);
    expect(artifacts.some(item => item.cumulativePatch !== null)).toBe(true);

    // The audit read, the inquiries and the micro task's routing decisions are the same shapes the detail reads.
    const audits = outcomes.filter(outcome => outcome.kind === "workflow-audit");
    expect(audits).toHaveLength(2);
    const continuations = audits.flatMap(audit => audit.facts.continuations as { turnId: string | null; input: string }[]);
    expect(continuations.every(item => item.turnId && item.input)).toBe(true);
    expect(audits.flatMap(audit => audit.facts.turns as { summary: string }[]).every(turn => turn.summary !== "")).toBe(true);
    expect(audits.flatMap(audit => audit.facts.routing as { routerBuddy: unknown }[]).every(entry => entry.routerBuddy !== null)).toBe(true);
    const inquiries = outcomes.filter(outcome => outcome.kind === "message-list")
      .flatMap(list => list.facts.inquiries as { state: string; answered: boolean; attemptId: string | null }[]);
    expect(new Set(inquiries.map(item => item.state))).toEqual(new Set(["answered", "unavailable", "delivered", "discarded"]));
    expect(inquiries.filter(item => item.answered)).toHaveLength(1);
    // One inquiry names no attempt: the detail still has to show it.
    expect(inquiries.filter(item => item.attemptId === null)).toHaveLength(1);

    const hostPaths = workflows.flatMap(workflow => workflow.facts.hostPaths as string[][]);
    expect(hostPaths.some(list => list.length > 0)).toBe(true);
    expect(hostPaths.some(list => list.length === 0)).toBe(true);

    // A partial output is never the recorded final artifact of its micro task.
    for (const workflow of workflows) {
      const finalId = workflow.facts.finalArtifactId as string | null;
      const finalRecord = (workflow.facts.artifacts as { artifactId: string; state: { partial: boolean } }[])
        .find(item => item.artifactId === finalId);
      expect(finalRecord?.state.partial ?? false).toBe(false);
    }

    // The report is written once, by the test that parsed every fixture above,
    // so `tests/python/cli/test_host_preview.py` always sees the complete surface.
  });
});

/**
 * The machine-readable report `tests/python/cli/test_host_preview.py` asserts
 * against, so the Python side checks the real parser's output instead of its
 * own copy of the fields.
 */
function report(payload: Record<string, unknown>) {
  if (!reportPath) return;
  mkdirSync(dirname(reportPath), { recursive: true });
  writeFileSync(reportPath, JSON.stringify(payload, null, 2) + "\n");
}
