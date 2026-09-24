import type { Task } from "./types";

/**
 * Bounded native-session evidence read from the frozen attempt receipt:
 * `selectedAttempt.result.result.nativeSession`, which every adapter records
 * (DSH, ZCode and Codex alike). Anything at another level, or any missing
 * field, stays unknown.
 */
export type NativeSessionEvidence = {
  adapter: string | null;
  sessionId: string | null;
  storageScope: string | null;
  storageOwner: string | null;
  appVisibility: string | null;
  resumeMode: string | null;
  resumable: boolean | null;
  bindingPresent: boolean | null;
  note: string | null;
};

const MAX_TEXT = 300;

function record(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function text(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed.slice(0, MAX_TEXT) : null;
}

function bool(value: unknown): boolean | null {
  return typeof value === "boolean" ? value : null;
}

export function nativeSessionEvidence(task: Task): NativeSessionEvidence | null {
  const attempt = record(task.selectedAttempt);
  const selectedId = task.activeAttemptId || task.selectedAttemptId;
  if (selectedId && attempt?.attemptId !== selectedId) return null;
  const receipt = record(attempt?.result);
  const result = record(receipt?.result);
  if (!result) return null;
  const native = record(result.nativeSession);
  if (!native) return null;
  return {
    adapter: text(native.adapter),
    sessionId: text(native.sessionId),
    storageScope: text(native.storageScope),
    storageOwner: text(native.storageOwner),
    appVisibility: text(native.nativeAppVisibility),
    resumeMode: text(native.resumeMode),
    resumable: bool(native.resumable),
    bindingPresent: bool(native.bindingPresent),
    note: text(native.note),
  };
}

const storageLabels: Record<string, string> = {
  "task-private": "Buddy 私有存储",
  "harness-user-store": "Harness 用户存储",
};

const storageOwnerLabels: Record<string, string> = {
  "buddy-attempt": "由 Buddy 尝试持有",
  "harness-user-store": "由 Harness 用户目录持有",
};

const resumeLabels: Record<string, string> = {
  initial: "首次原生会话",
  "native-session": "接续原生会话",
  "reconstructed-new-session": "新会话，从持久上下文重建",
};

function storageText(evidence: NativeSessionEvidence): string {
  if (evidence.storageScope && evidence.storageScope !== "unknown") {
    const owner = evidence.storageOwner ? storageOwnerLabels[evidence.storageOwner] : null;
    return `${storageLabels[evidence.storageScope] ?? `${evidence.storageScope}（原样记录）`}${owner ? `，${owner}` : ""}`;
  }
  return "未记录（未知）";
}

function visibilityText(evidence: NativeSessionEvidence): string {
  if (evidence.appVisibility === "not-listed-in-native-app") return "未在原生 App 中列出（私有库）";
  if (evidence.appVisibility && evidence.appVisibility !== "unknown") return `${evidence.appVisibility}（原样记录）`;
  return "未记录（未知）";
}

function resumeText(mode: string | null): string {
  if (!mode) return "未记录（未知）";
  return resumeLabels[mode] ?? `${mode}（原样记录）`;
}

/**
 * Read-only native-session facts for one delegation. It repeats the recorded
 * receipt fields without claiming that a private store can be opened from the
 * page; the activity, tool summary and fixed artifacts remain the checkable
 * entrypoints.
 */
export function NativeSessionView({ task, turnSessionId }: { task: Task; turnSessionId?: string }) {
  const evidence = nativeSessionEvidence(task);
  const sessionId = evidence?.sessionId || turnSessionId || null;
  return <section className="detail-section" aria-label="原生会话（只读）">
    <h3>原生会话证据</h3>
    {evidence ? <dl className="facts">
      {task.selectedAttempt?.attemptId && <><dt>收据尝试</dt><dd className="mono wrap">{task.selectedAttempt.attemptId}</dd></>}
      {evidence.adapter && <><dt>Harness</dt><dd>{evidence.adapter}</dd></>}
      <dt>会话 ID</dt><dd className="mono wrap">{sessionId || "未记录"}</dd>
      <dt>会话存储</dt><dd className="wrap">{storageText(evidence)}</dd>
      <dt>原生 App 可见性</dt><dd className="wrap">{visibilityText(evidence)}</dd>
      <dt>恢复方式</dt><dd className="wrap">{resumeText(evidence.resumeMode)}</dd>
      {evidence.resumable !== null && <><dt>可恢复</dt><dd>{evidence.resumable ? "已记录为可恢复" : "已记录为不可恢复"}</dd></>}
      {evidence.bindingPresent !== null && <><dt>私有绑定</dt><dd>{evidence.bindingPresent ? "私有绑定存在" : "私有绑定不存在"}</dd></>}
      {evidence.note && <><dt>Harness 说明</dt><dd className="wrap">{evidence.note}</dd></>}
    </dl> : <dl className="facts">
      <dt>会话 ID</dt><dd className="mono wrap">{sessionId || "未记录"}</dd>
      <dt>存储、可见性与恢复信息</dt><dd>未记录（未知）</dd>
    </dl>}
    <p className="small muted">信息来自执行收据。原生会话保存在私有库时，可通过本任务的活动、工具摘要与固定产物查看执行情况。</p>
  </section>;
}
