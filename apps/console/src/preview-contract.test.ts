import { parseActor } from "./actor";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { spawnSync } from "node:child_process";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { afterAll, describe, expect, it } from "vitest";
import { ApiError, createApi, snapshotHarnesses } from "./api";
import { parseWorkflowReply } from "./use-workflow";
import { hostConclusionView, quotaView, tokenUsageView, artifactStateView, cumulativePatchView, integrationHostPaths } from "./host-workflow";
import { finalArtifact } from "./integration";
import type { Workflow } from "./workflow-types";
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
  kind: "console-snapshot" | "task-page" | "backup-preflight" | "runtime-version" | "macro-tasks-page" | "macro-task-timeline" | "workflow-get";
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
    "HMB_WORKER_STATE", "HMB_WORKER_ID", "HMB_AGENT_CREDENTIAL_FILE",
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
  if (entry.kind === "workflow-get" && value && typeof value === "object" && !("ok" in value)) {
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
        // full execution records stay on the paginated tasks read.
        pendingCount: snapshot.tasks.pendingCount,
        harnesses: harnesses.map(row => ({
          harness: row.harness,
          status: row.status,
          // The display view drops the raw metered balance fact the Python
          // regression checks; keep it on the report row.
          quota: row.quota?.balanceZero === true
            ? { ...quotaView(row.quota)!, balanceZero: true }
            : quotaView(row.quota),
        })),
      };
    } else if (entry.kind === "task-page") {
      const page = await api.tasks({});
      outcome.facts = { total: page.total, runs: page.runs.length, nextCursor: page.nextCursor };
    } else if (entry.kind === "backup-preflight") {
      const report = await api.backupPreflight();
      outcome.facts = { policy: report.policy, needsAttention: report.needsAttention,
        skipped: report.skipped.count, rejected: report.rejected.count };
    } else if (entry.kind === "runtime-version") {
      const version = await api.runtimeVersion();
      outcome.facts = { mode: version.running.mode, softwareVersion: version.running.softwareVersion,
        installed: version.installed !== null };
    } else if (entry.kind === "macro-tasks-page") {
      const page = await api.macroTasks({});
      outcome.facts = { total: page.total, cursor: page.cursor, changed: page.changed };
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
    } else {
      const runId = entry.request?.runId ?? "";
      const raw = await api.command<Workflow>("workflow_get", { runId }, "fixture-csrf");
      const workflow = parseWorkflowReply(raw, runId);
      expect(workflow.hostId === null || typeof workflow.hostId === "string").toBe(true);
      if (workflow.hostConclusion) expect(parseActor(workflow.hostConclusion.actor)).toEqual(workflow.hostConclusion.actor);
      for (const record of workflow.integrations) expect(parseActor(record.actor)).toEqual(record.actor);
      const artifact = finalArtifact(workflow);
      outcome.facts = {
        runId: workflow.runId,
        hostId: workflow.hostId,
        revision: workflow.revision,
        buddyLocked: workflow.buddyLocked ?? null,
        hostConclusion: hostConclusionView(workflow.hostConclusion),
        turns: (workflow.turns ?? []).map(turn => ({
          turnIndex: turn.turnIndex,
          attemptId: turn.attemptId,
          tokenUsage: tokenUsageView(turn.tokenUsage).text,
          tokenUsageTitle: tokenUsageView(turn.tokenUsage).title,
          source: turn.tokenUsage?.source ?? null,
        })),
        currentTurnUsage: workflow.currentTurn ? tokenUsageView(workflow.currentTurn.tokenUsage).text : null,
        artifacts: workflow.artifacts.map(item => ({
          artifactId: item.artifactId,
          kind: item.kind,
          state: artifactStateView(item),
          cumulativePatch: cumulativePatchView(item.cumulativePatch),
        })),
        finalArtifactId: artifact?.artifactId ?? null,
        hostPaths: workflow.integrations.map(record => integrationHostPaths(record)),
      };
    }
    outcome.accepted = true;
  } catch (error) {
    outcome.code = error instanceof ApiError ? error.code : error instanceof Error ? "ERROR" : "UNKNOWN";
  }
  return outcome;
}

describe("synthetic preview fixtures through the real console parsers", () => {
  it("declares the six preview reads and a self-consistent manifest", () => {
    expect(manifest.fixtureVersion).toBe(1);
    expect(manifest.source).toBe("tests/probes/macro_task_console_preview.py");
    expect(manifest.scenarios).toEqual(["normal", "readonly", "truncated", "error"]);
    expect(new Set(manifest.files.map(entry => entry.kind))).toEqual(
      new Set(["console-snapshot", "task-page", "backup-preflight", "runtime-version", "macro-tasks-page", "macro-task-timeline", "workflow-get"]));
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
    expect(failed, JSON.stringify(failed.map(item => `${item.path}: accepted=${item.accepted} code=${item.code}`))).toEqual([]);

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
    expect(incompatible.filter(outcome => !outcome.accepted)).toHaveLength(5);
    // A malformed optional quota observation keeps the page readable and is
    // dropped as unknown: it never surfaces as a recorded 0% or near-limit flag.
    const downgraded = incompatible.find(outcome => entryFor(outcome).expected === "accepted")!;
    expect(downgraded.accepted).toBe(true);
    const downgradedRows = downgraded.facts.harnesses as { harness: string; quota: { windows: { usedText: string }[] } | null }[];
    expect(downgradedRows[0].harness).toBe("dsh");
    expect(downgradedRows[0].quota).toBeNull();
    expect(downgradedRows.slice(1).some(row => row.quota !== null)).toBe(true);
    expect(downgradedRows.every(row => row.quota === null
      || row.quota.windows.every(window => window.usedText !== "0%"))).toBe(true);

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

    // DSH is metered: a zero balance is recorded without invented percentage windows.
    const normalSnapshot = await apiFor(manifest.files.find(entry => entry.scenario === "normal" && entry.kind === "console-snapshot")!).snapshot();
    expect(snapshotHarnesses(normalSnapshot).find(row => row.harness === "dsh")).toMatchObject({
      billingByProvider: { "deepseek-official": { kind: "metered" } },
      quota: { balanceZero: true, windows: [] },
    });
    // Recorded quota: the near-limit, normal, stale-and-unknown and absent
    // observations stay four distinct states; only claude alerts.
    const quotaFacts = snapshotFacts.harnesses as { harness: string; quota: ReturnType<typeof quotaView> }[];
    expect(quotaFacts.filter(row => row.quota?.alert).map(row => row.harness)).toEqual(["claude"]);
    expect(quotaFacts.some(row => row.quota === null)).toBe(true);
    expect(quotaFacts.some(row => row.quota?.stale && row.quota.windows.some(window => window.usedPercent === null))).toBe(true);

    // Per-execution usage: known, partial-unknown and unknown all appear, and
    // no value is a session cumulative.
    const turns = workflows.flatMap(workflow => (workflow.facts.turns as { tokenUsage: string }[]) ?? []);
    expect(turns.some(turn => turn.tokenUsage.includes("含缓存"))).toBe(true);
    expect(turns.some(turn => turn.tokenUsage.includes("未知"))).toBe(true);

    // Host conclusion, partial output, cumulative patch and Host paths.
    const concluded = workflows.find(workflow => workflow.facts.hostConclusion !== null)!;
    const conclusion = concluded.facts.hostConclusion as { executionStatus?: string; statusLabel: string; evidence: string[] };
    expect(["失败", "已取消"]).toContain(conclusion.statusLabel);
    expect(conclusion.evidence.length).toBeGreaterThan(0);

    const artifacts = workflows.flatMap(workflow => (workflow.facts.artifacts as {
      state: ReturnType<typeof artifactStateView>; cumulativePatch: ReturnType<typeof cumulativePatchView>;
    }[]) ?? []);
    const partial = artifacts.find(item => item.state.partial)!;
    expect(partial).toBeTruthy();
    expect(partial.state.verified).toBe(false);
    expect(partial.state.final).toBe(false);
    expect(artifacts.some(item => item.cumulativePatch !== null)).toBe(true);

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
