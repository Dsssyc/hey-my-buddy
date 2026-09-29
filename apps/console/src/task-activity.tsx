import type { ActivityCounts, ActivityPhase, Task, TaskActivity } from "./types";
import { formatDate } from "./ui";

/**
 * The frozen ADR-010 activity projection. Only these keys are read; anything
 * else stays unknown, and no prompt text, tool arguments, credentials or hidden
 * reasoning is ever requested or displayed.
 */
export const ACTIVITY_PHASES: ActivityPhase[] = [
  "starting",
  "waiting-model",
  "streaming-model",
  "tool-running",
  "waiting-external",
  "waiting-host",
  "finishing",
  "unknown",
];

export const phaseLabels: Record<ActivityPhase, string> = {
  starting: "正在启动",
  "waiting-model": "等待模型",
  "streaming-model": "模型输出中",
  "tool-running": "工具执行中",
  "waiting-external": "等待外部结果",
  "waiting-host": "等待 Host",
  finishing: "正在收尾",
  unknown: "进展未知",
};

const MAX_TEXT = 200;

function boundedText(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const text = value.trim();
  return text ? text.slice(0, MAX_TEXT) : null;
}

function boundedCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : null;
}

function parseCounts(value: unknown): ActivityCounts | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  const modelTurns = boundedCount(record.modelTurns);
  const toolCalls = boundedCount(record.toolCalls);
  if (modelTurns === null && toolCalls === null) return null;
  return { modelTurns, toolCalls };
}

/** Rejects an unusable payload instead of rendering invented progress. */
export function parseActivity(value: unknown): TaskActivity | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const record = value as Record<string, unknown>;
  // A recognized phase is itself a recorded observation, including "unknown";
  // only a payload with no recognized field at all is treated as no record.
  const hasPhase = ACTIVITY_PHASES.includes(record.phase as ActivityPhase);
  const phase = hasPhase ? (record.phase as ActivityPhase) : "unknown";
  const observedAt = boundedText(record.observedAt);
  const eventSeq = boundedCount(record.eventSeq);
  const nativeSessionId = boundedText(record.nativeSessionId);
  const lastNativeActivityAt = boundedText(record.lastNativeActivityAt);
  const lastToolActivityAt = boundedText(record.lastToolActivityAt);
  const toolName = boundedText(record.toolName);
  const waitingReason = boundedText(record.waitingReason);
  const counts = parseCounts(record.counts);
  if (
    !hasPhase &&
    !observedAt &&
    eventSeq === null &&
    !nativeSessionId &&
    !lastNativeActivityAt &&
    !lastToolActivityAt &&
    !toolName &&
    !waitingReason &&
    !counts
  ) {
    return null;
  }
  return {
    phase,
    observedAt,
    eventSeq,
    nativeSessionId,
    lastNativeActivityAt,
    lastToolActivityAt,
    toolName,
    waitingReason,
    counts,
  };
}

export type ActivityEvidence = "tool" | "native" | "heartbeat" | "unknown";

/**
 * What actually backs this projection. A supervisor heartbeat or a renew reply
 * is not native progress, and an idle native session list is not proof that a
 * process stopped.
 */
export function activityEvidence(activity: TaskActivity): ActivityEvidence {
  if (activity.toolName || activity.lastToolActivityAt) return "tool";
  if (activity.lastNativeActivityAt || activity.nativeSessionId) return "native";
  if (activity.observedAt || activity.eventSeq != null) return "heartbeat";
  return "unknown";
}

export const evidenceLabels: Record<ActivityEvidence, string> = {
  tool: "收到工具活动",
  native: "收到原生活动",
  heartbeat: "仅监管心跳",
  unknown: "未知",
};

export function countsText(activity: TaskActivity): string {
  const modelTurns = activity.counts?.modelTurns;
  const toolCalls = activity.counts?.toolCalls;
  const parts = [
    modelTurns == null ? "模型回合未记录" : `模型回合 ${modelTurns}`,
    toolCalls == null ? "工具调用未记录" : `工具调用 ${toolCalls}`,
  ];
  return parts.join(" · ");
}

const terminationLabels: Record<string, string> = {
  deadline: "执行时限到期",
  "harness-error": "Harness 错误",
  "transport-error": "传输故障",
  completed: "正常完成",
};

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function cancellationDetails(task: Task): { label: string; hostId: string | null; reason: string | null } {
  const cancellation = record(task.cancellation);
  const actor = cancellation?.actor;
  const reason = cancellation?.reason;
  const safeReason = typeof reason === "string" && reason.trim() && reason.length <= 4000
    ? reason.trim() : null;
  if (typeof actor === "string" && actor.startsWith("host:") && actor.length <= 133) {
    const hostId = actor.slice(5);
    if (hostId && !/[\x00-\x1f\x7f]/.test(hostId)) return { label: "Host 取消", hostId, reason: safeReason };
  }
  if (typeof actor === "string" && /^console:[^\x00-\x1f\x7f]{1,128}$/.test(actor)) {
    return { label: "在控制台停止", hostId: null, reason: safeReason };
  }
  if (actor === "service-stop") return { label: "服务停止", hostId: null, reason: safeReason };
  return { label: "取消（发起者未知）", hostId: null, reason: safeReason };
}

/**
 * The durable cause is the one recorded on the selected attempt receipt
 * (`selectedAttempt.result.terminationReason`). A task-level `terminationReason`
 * is only an accepted hoisted copy; nothing is inferred from status alone.
 */
export function recordedTermination(task: Task): string | null {
  const attempt = record(task.selectedAttempt);
  const selectedId = task.activeAttemptId || task.selectedAttemptId;
  if (selectedId && attempt?.attemptId !== selectedId) return null;
  const receipt = record(attempt?.result);
  const stored =
    typeof receipt?.terminationReason === "string" ? receipt.terminationReason.trim() : "";
  const direct =
    typeof task.terminationReason === "string" ? task.terminationReason.trim() : "";
  return stored || direct || null;
}

/** Recorded termination cause. A missing cause stays unknown, never "user cancel". */
export function terminationText(task: Task): string | null {
  const raw = recordedTermination(task);
  if (raw === "user-cancel") return cancellationDetails(task).label;
  if (raw) return terminationLabels[raw] ?? raw;
  if (task.status === "cancelled") {
    return cancellationDetails(task).label;
  }
  if (task.status === "completed" || task.status === "failed") {
    return "未记录终止原因（未知）";
  }
  return null;
}

/**
 * Bounded, read-only activity for one delegation. It reports only what the
 * worker recorded: no percentage, no synthesized completion estimate, and no
 * claim that a heartbeat proves model progress.
 */
export function TaskActivityView({ task }: { task: Task }) {
  const activity = parseActivity(task.activity);
  const termination = terminationText(task);
  // The suffix is about the record, not about which field carried the value.
  const causeRecorded = recordedTermination(task) !== null;
  const cancelled = task.status === "cancelled" || task.status === "cancelling" || task.workflow?.state === "cancelled";
  const cancellation = cancelled ? cancellationDetails(task) : null;
  const evidence = activity ? activityEvidence(activity) : "unknown";
  return (
    <section className="activity-view" aria-label="执行活动（只读）">
      <h3>执行活动</h3>
      {activity ? (
        <>
          <dl className="facts">
            <dt>当前阶段</dt>
            <dd>{phaseLabels[activity.phase]}</dd>
            <dt>活动依据</dt>
            <dd>{evidenceLabels[evidence]}</dd>
            {activity.waitingReason && <><dt>等待原因</dt><dd className="wrap">{activity.waitingReason}</dd></>}
            {evidence === "tool" && activity.toolName && <><dt>最近工具</dt><dd>{activity.toolName}</dd></>}
            {activity.lastToolActivityAt && <><dt>最近工具活动</dt><dd>{formatDate(activity.lastToolActivityAt)}</dd></>}
            {activity.lastNativeActivityAt && <><dt>最近原生活动</dt><dd>{formatDate(activity.lastNativeActivityAt)}</dd></>}
            {activity.nativeSessionId && <><dt>原生会话</dt><dd className="mono wrap">{activity.nativeSessionId}</dd></>}
            {activity.observedAt && <><dt>最近上报</dt><dd>{formatDate(activity.observedAt)}</dd></>}
            {activity.eventSeq !== null && <><dt>事件序号</dt><dd>{activity.eventSeq}</dd></>}
            <dt>已记录计数</dt><dd>{countsText(activity)}</dd>
          </dl>
          <p className="small muted">
            {evidence === "heartbeat"
              ? "仅有监管心跳 · 进展未知"
              : evidence === "tool"
                ? "工具执行中"
                : evidence === "native"
                  ? "已收到原生活动"
                  : "活动未知 · 停止未确认"}
          </p>
        </>
      ) : (
        <p className="small muted">暂无活动记录 · 停止未确认</p>
      )}
      {termination && (
        <p className="small">
          终止原因：<strong>{termination}</strong>
          {causeRecorded || task.status === "cancelled" ? "" : "（原始记录未给出原因）"}
        </p>
      )}
      {cancellation && <dl className="facts">
        {termination !== cancellation.label && <><dt>取消发起</dt><dd>{cancellation.label}</dd></>}
        {cancellation.hostId && <><dt>发起 Host</dt><dd className="mono wrap">{cancellation.hostId}</dd></>}
        {cancellation.reason && <><dt>取消理由</dt><dd className="wrap">{cancellation.reason}</dd></>}
      </dl>}
    </section>
  );
}
