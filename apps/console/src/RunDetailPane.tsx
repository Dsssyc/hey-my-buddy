import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task } from "./types";
import { TaskDetails } from "./TaskDetails";
import { taskTitle } from "./task-state";
import type { AuthorityLatch } from "./console-session";
import type { SectionId } from "./objective-display";

const LOCK_NOTE = "有结果未确认的操作：核对前不切换，以免丢失操作标识。";

export type DetailTarget = {
  runId: string;
  /** Existing detail tab to preselect (written into the run's section draft). */
  section?: SectionId;
  /** Short span facts for the locator's 来自时间轴 line. */
  locator?: string;
  /** Timeline item key that keeps its selected outline on return. */
  key?: string;
};

/**
 * Right pane, layer two: the locator bar plus the existing TaskDetails for the
 * delegation opened from the timeline. No control set is duplicated here. In
 * dock mode the timeline stays visible beside or above this pane, so the
 * return control reads as closing the detail instead of going back.
 */
export function RunDetailPane({ mode = "layer", objectiveTitle, target, snapshot, api, refresh, active, authority, writesAvailable, locked, onBack, onNavigate, onLockChange, rowTitleFor }: {
  /** "layer" replaces the timeline; "dock" keeps it visible beside/above. */
  mode?: "layer" | "dock";
  objectiveTitle: string;
  target: DetailTarget;
  snapshot: Snapshot;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
  active: boolean;
  authority?: AuthorityLatch;
  writesAvailable: boolean;
  locked: boolean;
  onBack: () => void;
  onNavigate: (runId: string) => void;
  onLockChange: (value: boolean) => void;
  /** The timeline read's own resolved title for a run, when it knows the run. */
  rowTitleFor?: (runId: string) => string | null;
}) {
  const [remote, setRemote] = useState<Task | null>(null);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let current = true;
    setRemote(null); setError("");
    api.task(target.runId).then(value => {
      if (!current) return;
      if (!value || typeof value !== "object" || !("runId" in value) || value.runId !== target.runId || !("task" in value)) {
        throw new Error("委派详情不完整。");
      }
      setRemote(value as Task);
    }).catch(reason => { if (current) setError(errorText(reason)); });
    return () => { current = false; };
  }, [api, target.runId, attempt]);
  const crumbs = remote ? `${objectiveTitle} › ${rowTitleFor?.(remote.runId) ?? taskTitle(remote)}` : `${objectiveTitle} › ${target.runId}`;
  return <div className="run-view">
    <div className="locator">
      <button type="button" className="button small-button" aria-disabled={locked || undefined}
        title={locked ? LOCK_NOTE : undefined}
        onClick={() => { if (!locked) onBack(); }}>{mode === "dock" ? "× 关闭详情" : "‹ 返回时间轴"}</button>
      <span className="crumbs" title={crumbs}>{crumbs}</span>
      {target.locator && <span className="from">来自时间轴：{target.locator}</span>}
      {locked && <span className="lock-note" role="status">{LOCK_NOTE} 当前委派的其他栏目仍可浏览。</span>}
    </div>
    {remote
      ? <TaskDetails key={remote.runId} task={remote} snapshot={snapshot} api={api} refresh={refresh}
        selectTask={runId => { if (runId && !locked) onNavigate(runId); }} active={active}
        onLockChange={onLockChange} onTaskUpdate={next => setRemote(previous =>
          previous?.runId === next.runId && previous.revision === next.revision
          && previous.workflow?.revision === next.workflow?.revision ? previous : next)}
        navigationLocked={locked} authority={authority} writesAvailable={writesAvailable}
        hideBackButton initialSection={target.section} />
      : error
        ? <div className="detail-placeholder">
          <h2>读取委派失败</h2>
          <p role="alert">{error}</p>
          <p className="small muted">定位条保留了时间轴给出的事实；可重试读取或返回时间轴。</p>
          <div className="actions">
            <button type="button" className="button small-button" onClick={() => setAttempt(value => value + 1)}>重试读取</button>
          </div>
        </div>
        : <div className="detail-placeholder"><h2>正在读取委派…</h2><p>{target.locator ? `来自时间轴：${target.locator}` : "从时间轴打开的委派详情。"}</p></div>}
  </div>;
}
