import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { Snapshot, Task } from "./types";
import type { TimelineRow } from "./objective-types";
import { TaskDetails } from "./TaskDetails";
import { taskTitle } from "./task-state";
import type { SectionId } from "./objective-display";

export type DetailTarget = {
  runId: string;
  /** Existing detail tab to preselect once on open. */
  section?: SectionId;
  /** Short span facts for the locator's 来自时间轴 line. */
  locator?: string;
  /** Timeline item key that keeps its selected outline on return. */
  key?: string;
};

/**
 * Right pane, layer two: the locator bar plus the existing read-only
 * TaskDetails for the delegation opened from the timeline. No control set is
 * duplicated here, and nothing locks navigation: record browsing is free even
 * while an objective-level stop has an unconfirmed reply. In dock mode the
 * timeline stays visible beside or above this pane, so the return control
 * reads as closing the detail instead of going back.
 */
export function RunDetailPane({ mode = "layer", objectiveTitle, target, snapshot, api, refresh, active, onBack, onNavigate, stopStatusNode, overviewRow, rowTitleFor }: {
  /** "layer" replaces the timeline; "dock" keeps it visible beside/above. */
  mode?: "layer" | "dock";
  objectiveTitle: string;
  target: DetailTarget;
  snapshot: Snapshot;
  api: ConsoleApi;
  refresh: () => Promise<Snapshot | null>;
  active: boolean;
  onBack: () => void;
  onNavigate: (runId: string) => void;
  /** The objective-level stop status line shown in the overview tab (§4/§7). */
  stopStatusNode?: ReactNode;
  /** The timeline's own row for the fixed three-row overview block. */
  overviewRow?: TimelineRow | null;
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
      <button type="button" className="button small-button"
        onClick={onBack}>{mode === "dock" ? "× 关闭详情" : "‹ 返回时间轴"}</button>
      <span className="crumbs" title={crumbs}>{crumbs}</span>
      {target.locator && <span className="from">来自时间轴：{target.locator}</span>}
    </div>
    {remote
      ? <TaskDetails key={remote.runId} task={remote} snapshot={snapshot} api={api} refresh={refresh}
        selectTask={runId => { if (runId) onNavigate(runId); }} active={active}
        onTaskUpdate={next => setRemote(previous =>
          previous?.runId === next.runId && previous.revision === next.revision
          && previous.workflow?.revision === next.workflow?.revision ? previous : next)}
        hideBackButton initialSection={target.section} stopStatusNode={stopStatusNode} overviewRow={overviewRow} />
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
