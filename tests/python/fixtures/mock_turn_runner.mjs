/**
 * Mock structured DSH runner for the governed turn protocol test.
 *
 * It implements exactly the flags the adapter passes for a turn, writes the
 * version-1 output record the service imports, and emits the ordinary runner JSON
 * line with true shutdown evidence. It never calls a model.
 */
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";

function flag(name) {
  const index = process.argv.indexOf(name);
  if (index >= 0) return process.argv[index + 1];
  const prefixed = process.argv.find((value) => value.startsWith(`${name}=`));
  return prefixed === undefined ? undefined : prefixed.slice(name.length + 1);
}

const inputPath = flag("--turn-input-file");
const outputPath = flag("--turn-output-file");
const logDir = flag("--log-dir");
const cwd = flag("--cwd");
const activityPath = flag("--activity-file");
if (!inputPath || !outputPath) {
  process.stderr.write("mock runner: --turn-input-file and --turn-output-file are required together\n");
  process.exit(2);
}
const raw = readFileSync(inputPath, "utf8");
const input = JSON.parse(raw);
const inputSha256 = createHash("sha256").update(Buffer.from(raw, "utf8")).digest("hex");
const prompt = `${readFileSync(flag("--task-file"), "utf8")}\n${raw}`;
const promptSha256 = createHash("sha256").update(prompt, "utf8").digest("hex");
const record = {
  version: 1,
  taskId: input.taskId,
  attemptId: input.attemptId,
  generation: input.generation,
  turnId: input.turnId,
  resumeMode: input.resumeMode,
  previousSessionId: input.previousSessionId ?? null,
  sessionId: `mock-session-${String(input.turnId).slice(0, 8)}`,
  promptSha256,
  inputSha256,
  outcome: {
    disposition: "completed",
    summary: "mock structured turn completed",
    remaining: [],
    decisions: [],
    artifacts: [],
    request: null,
  },
  provenance: {
    tool: "buddy_finish_turn",
    turnEnd: "completed",
    flush: "awaited",
    rootSessionMatched: true,
    toolCallId: "mock-call-1",
    rootCallId: "mock-call-1",
    toolResultSeq: 1,
    turnEndSeq: 2,
    flushSeq: 3,
  },
};
mkdirSync(dirname(outputPath), { recursive: true });
writeFileSync(outputPath, JSON.stringify(record));
writeFileSync(join(cwd, "mock-output.txt"), `output for turn ${input.turnId}\n`);
mkdirSync(logDir, { recursive: true });
writeFileSync(join(logDir, "capture.json"), JSON.stringify({ sessionId: record.sessionId }));
if (activityPath !== undefined) {
  // The real activity observer writes the frozen bounded projection for the exact
  // attempt; this mock writes the same document shape so the adapter's sidecar
  // handoff is exercised without a native model run.
  const observedAt = new Date().toISOString();
  writeFileSync(
    activityPath,
    `${JSON.stringify({
      version: 1,
      taskId: input.taskId,
      attemptId: input.attemptId,
      generation: input.generation,
      updatedAt: observedAt,
      activity: {
        phase: "finishing",
        observedAt,
        eventSeq: 3,
        nativeSessionId: record.sessionId,
        lastNativeActivityAt: observedAt,
        lastToolActivityAt: observedAt,
        toolName: "mock-tool",
        counts: { modelTurns: 1, toolCalls: 1 },
      },
    })}\n`,
    { mode: 0o600 },
  );
}
process.stdout.write(
  `${JSON.stringify({
    status: "ok",
    mode: "run",
    finalText: "mock done",
    logPaths: { stdout: join(logDir, "stdout.log"), stderr: join(logDir, "stderr.log"), capture: join(logDir, "capture.json") },
    nativeActivity: {
      enabled: activityPath !== undefined,
      sidecar: "activity.json",
      note: "bounded metadata-only projection this run wrote for its owning Worker",
    },
    nativeStorage: {
      // The governed default keeps the inherited harness home so the owning
      // workspace host can verify and group the completed session.
      scope: "harness-user-store",
      relocated: false,
      sessionsSubdir: "sessions",
      nativeAppVisibility: "user-store",
      resumeMode: "reconstructed-new-session",
    },
    workspace: { enabled: true, bound: true, id: "mock-workspace", path: cwd, sessionId: record.sessionId },
    processState: { shutdownConfirmed: true },
  })}\n`,
);
