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
  "user-cancel": "用户取消",
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

/**
 * The durable cause is the one recorded on the selected attempt receipt
 * (`selectedAttempt.result.terminationReason`). A task-level `terminationReason`
 * is only an accepted hoisted copy; nothing is inferred from status alone.
 */
export function recordedTermination(task: Task): string | null {
  const receipt = record(record(task.selectedAttempt)?.result);
  const stored =
    typeof receipt?.terminationReason === "string" ? receipt.terminationReason.trim() : "";
  const direct =
    typeof task.terminationReason === "string" ? task.terminationReason.trim() : "";
  return stored || direct || null;
}

/** Recorded termination cause. A missing cause stays unknown, never "user cancel". */
export function terminationText(task: Task): string | null {
  const raw = recordedTermination(task);
  if (raw) return terminationLabels[raw] ?? raw;
  if (task.status === "cancelled") {
    return "未记录终止原因（未知，不能据此判定为用户取消）";
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
              ? "只有监管进程的上报，尚未收到原生活动；续租成功不代表模型已推进。"
              : evidence === "tool"
                ? "工具正在执行；这不表示任务接近完成。"
                : evidence === "native"
                  ? "已收到原生活动；未知的部分保持未知。"
                  : "活动内容未知；未知不代表停机。"}
            活动只是有界观察，不提供完成百分比，也不能单独证明进程已停止。
          </p>
        </>
      ) : (
        <p className="small muted">尚无活动记录。未知不代表停机；停止与否以真实停止证据为准。</p>
      )}
      {termination && (
        <p className="small">
          终止原因：<strong>{termination}</strong>
          {causeRecorded ? "" : "（原始记录未给出原因）"}
        </p>
      )}
    </section>
  );
}
