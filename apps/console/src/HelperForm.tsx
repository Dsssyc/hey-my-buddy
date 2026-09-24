import type { Profile } from "./types";
import type { HelperDraft } from "./workflow-types";
import { profileTitle } from "./profile-display";

export function HelperForm({ helper, index, profiles, onChange, onRemove }: {
  helper: HelperDraft;
  index: number;
  profiles: Profile[];
  onChange: (next: HelperDraft) => void;
  onRemove: () => void;
}) {
  const patch = (next: Partial<HelperDraft>) => onChange({ ...helper, ...next });
  return <fieldset className="helper-form">
    <legend>协助任务 {index + 1}</legend>
    <label className="field"><span>执行配置</span><select value={helper.profileId} onChange={e => patch({ profileId: e.target.value })}>
      <option value="">自动路由：由固定决策 Buddy 选择</option>
      {profiles.map(p => <option key={p.profileId} value={p.profileId}>{p.adapter} · {profileTitle(p)}</option>)}
    </select></label>
    <label className="field"><span>工作内容与验收条件</span><textarea rows={4} maxLength={16000} value={helper.task} onChange={e => patch({ task: e.target.value })} /></label>
    <label className="field"><span>输入 checkout 的绝对路径</span><input value={helper.cwd} onChange={e => patch({ cwd: e.target.value })} /></label>
    <div className="form-pair">
      <label className="field"><span>执行工作区</span><select value={helper.kind} onChange={e => patch({ kind: e.target.value as HelperDraft["kind"] })}>
        <option value="worktree">从当前内容创建独立 worktree</option><option value="existing">顺序移交现有 checkout</option>
      </select></label>
      <label className="field"><span>访问方式</span><select value={helper.access} onChange={e => patch({ access: e.target.value as HelperDraft["access"] })}>
        <option value="write">写入指定范围</option><option value="read">只读</option>
      </select></label>
    </div>
    <label className="field"><span>允许写入的相对路径（每行一项）</span><textarea rows={2} disabled={helper.access === "read"} value={helper.writeScope} onChange={e => patch({ writeScope: e.target.value })} placeholder="src/state.ts&#10;tests/state.test.ts" /></label>
    <label className="field"><span>传入的未跟踪文件（每行一项，可留空）</span><textarea rows={2} value={helper.includeUntracked} onChange={e => patch({ includeUntracked: e.target.value })} /></label>
    <p className="small muted">提交时固定当前 tracked 内容及显式列出的未跟踪文件。接续任务负责集成；并行写入使用独立 worktree。</p>
    <button type="button" className="button small-button" onClick={onRemove}>移除协助任务 {index + 1}</button>
  </fieldset>;
}
