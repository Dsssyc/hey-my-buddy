import type { ConsoleApi } from "./api";
import { annotationText, emptyCard, setAnnotation, setConcurrencyLimit, setPreference } from "./draft";
import type { Editor } from "./use-editor";
import type { ConsoleView, ModelFamily, Snapshot } from "./types";
import { Badge, formatDate } from "./ui";
import { effortText, profileName } from "./profile-display";
import {
  MODEL_CONCURRENCY_DEFAULT,
  MODEL_CONCURRENCY_MAX,
  MODEL_CONCURRENCY_MIN,
  cardOriginText,
  concurrencyEntryFor,
  familyKey,
  isMaintenanceCard,
  modelFamilies,
  recordedSampleCount,
} from "./console-data";
import { DetailTabs } from "./DetailTabs";
import { EvaluationHistory } from "./EvaluationHistory";


const sections = [["overview", "概览"], ["assessment", "评价与意见"], ["preferences", "偏好与启用"], ["evidence", "证据"]] as const;
const EMPTY_EVIDENCE = "暂无评价证据。你可以让已配置 hey-my-buddy skill 的 Harness 执行一次模型评价更新，或在该 Harness 中设置定时更新任务。";

/**
 * Invalid input stays in the local draft so Save cannot silently publish an
 * earlier valid prefix. NaN represents an empty number field; policy validation
 * blocks it before acquiring any publication grant.
 */
function ConcurrencyField({ editor, family, draftLimit, occupancy, editable }: {
  editor: Editor; family: ModelFamily; draftLimit: number; occupancy: number | null; editable: boolean;
}) {
  const invalid = !Number.isInteger(draftLimit)
    || draftLimit < MODEL_CONCURRENCY_MIN || draftLimit > MODEL_CONCURRENCY_MAX;
  return <div className="form-pair concurrency-row">
    <label className="field concurrency-value">
      <span>并发任务上限</span>
      <input type="number" inputMode="numeric" min={MODEL_CONCURRENCY_MIN} max={MODEL_CONCURRENCY_MAX}
        step={1} value={Number.isNaN(draftLimit) ? "" : draftLimit} readOnly={!editable}
        aria-invalid={invalid || undefined} aria-describedby="concurrency-note"
        onChange={(event) => {
          const raw = event.target.value;
          const value = raw === "" ? NaN : Number(raw);
          editor.setDraft((draft) => (draft ? setConcurrencyLimit(draft, family, value) : draft));
        }} />
    </label>
    <p className="small muted concurrency-occupancy" id="concurrency-note">
      当前占用 {occupancy ?? "未知"}（只读）{invalid ? " · 需要输入 1–32 的整数，当前修改不会保存" : ""}
    </p>
  </div>;
}

export function ModelDetail({
  data, recorded, editor, api, active = true, historyOpen, section, onSection, profileId, showUnavailable,
  onSelect, onCloseHistory, onCloseList, guard = "", error = "", note = "",
}: {
  /** Human draft (when editing) merged with retained history rows. */
  data: ConsoleView; /** Recorded snapshot merged with retained history rows. */
  recorded: Snapshot; editor: Editor; api: ConsoleApi; active?: boolean;
  historyOpen: boolean; section: string; onSection: (value: string) => void;
  profileId: string | null; showUnavailable: boolean;
  onSelect: (profileId: string) => void; onCloseHistory: () => void;
  onCloseList: () => void; guard?: string; error?: string; note?: string;
}) {
  const editing = editor.editing;
  // A retained draft stays on screen (and copyable) even when the session lost
  // write authority; only its editability is withdrawn.
  const draftShown = editor.mode;
  const draftFrozen = draftShown && !editor.sessionWritable;
  const profile = data.profiles.find(p => p.profileId === profileId);
  const published = profile ? recorded.profiles.find(p => p.profileId === profile.profileId) : undefined;
  const family = profile && modelFamilies(data.profiles).find(g => g.key === familyKey(profile));
  // The assessment card is program-owned: it always comes from the recorded
  // snapshot/history and is never part of the local draft.
  const card = profile ? data.cards.find(c => c.profileId === profile.profileId) || emptyCard(profile.profileId) : null;
  const evidence = profile ? recorded.evidence.filter(e => e.profileId === profile.profileId) : [];
  const samples = profile ? recordedSampleCount(data, profile.profileId) : 0;
  const decisionProfileId = data.configuration.decisionProfileId;
  const opinion = profile ? annotationText(data, profile.profileId) : "";
  const publishedOpinion = profile ? annotationText(recorded, profile.profileId) : "";
  const preference = data.preferences.find(p => p.profileId === profileId);
  // One shared concurrency setting per adapter/provider/model family; every
  // effort variant only inspects or edits that single value. Occupancy comes
  // from the recorded snapshot: it is observation and never part of the draft.
  const familyConcurrency = profile ? concurrencyEntryFor(data.modelConcurrency, profile) : null;
  const recordedConcurrency = profile ? concurrencyEntryFor(recorded.modelConcurrency, profile) : null;
  const draftLimit = familyConcurrency ? familyConcurrency.limit : MODEL_CONCURRENCY_DEFAULT;
  const recordedLimit = recordedConcurrency ? recordedConcurrency.limit : null;
  const occupancy = recordedConcurrency && Number.isInteger(recordedConcurrency.active)
    ? recordedConcurrency.active : null;
  const concurrencyUnsaved = editing && recordedLimit !== null && recordedLimit !== draftLimit;
  const variants = profile
    ? family?.profiles.filter(p => showUnavailable || p.available || p.profileId === profile.profileId) ?? []
    : [];
  // `editor.blocking` stays empty without a draft; `editor.attention` reports the
  // published state then and the draft state while editing.
  const profileAttention = editor.attention.filter(issue => issue.profileId === profile?.profileId);
  const profileBlocking = editor.blocking.filter(issue => issue.profileId === profile?.profileId);
  const canEnable = !!profile && (profile.available || profile.enabled);
  // A pin that already exists may keep its reason edited; only a transition into
  // pin needs the configuration to be currently available and enabled.
  const baselinePreference = editor.baseline?.preferences.find(p => p.profileId === profileId);
  const pinIsExisting = (baselinePreference ?? preference)?.mode === "pin";
  // The card's recorded origin decides how it is labelled; unknown authorship
  // is never presented as an automatic maintenance assessment.
  const maintenanceCard = isMaintenanceCard(card ?? undefined);
  const cardHeading = maintenanceCard ? "自动评价" : "评价（发布者未记录）";
  const cardReadonlyLabel = maintenanceCard ? "自动评价（只读）" : "评价（发布者未记录，只读）";
  return <aside className="panel detail-panel" aria-label="评价卡片详情">
    <div className="model-detail-content" hidden={!historyOpen}>
      <EvaluationHistory snapshot={recorded} api={api} active={active && historyOpen} onBack={onCloseHistory} />
    </div>
    <div className="model-detail-content" hidden={historyOpen}>
    {profile && card && family ? <>
      <header className="detail-header">
        <div className="row-between"><button className="button small-button mobile-back" onClick={onCloseList}>返回模型列表</button>
          <span className="small muted">{profile.adapter} / {profile.provider}</span>
          <span className="chip-row">
            <Badge tone={profile.enabled ? "green" : "neutral"}>{profile.enabled ? "已启用" : "已停用"}</Badge>
            {published && published.enabled !== profile.enabled && <span className="unsaved-mark">未保存</span>}
            <Badge tone={profile.available ? "green" : "amber"}>{profile.available ? "目录可用" : "目录不可用"}</Badge>
          </span></div>
        <h2>{profileName(profile)}</h2>
        <div className="variant-switch" role="group" aria-label="思考强度">
          {variants.map(p => {
            const inspected = p.profileId === profile.profileId;
            const wasEnabled = recorded.profiles.find(x => x.profileId === p.profileId)?.enabled === true;
            const pending = p.enabled !== wasEnabled;
            return <button key={p.profileId} type="button" aria-pressed={inspected}
              aria-label={`${effortText(p.effort)}，${p.enabled ? "已启用" : "未启用"}${p.available ? "" : "，目录不可用"}${pending ? "，未保存" : ""}${inspected ? "，正在查看" : ""}`}
              className={"effort-choice" + (p.enabled ? " enabled" : "") + (inspected ? " inspected" : "") + (p.available ? "" : " unavailable")}
              onClick={() => onSelect(p.profileId)}>
              <span className="effort-name">{effortText(p.effort)}</span>
              {p.enabled && <span className="enabled-mark">✓ 已启用</span>}
              {!p.available && <span className="unavailable-mark">不可用</span>}
              {pending && <span className="unsaved-mark">未保存</span>}
            </button>;
          })}
        </div>
        <p className="small muted">当前查看：{profile.model} / {effortText(profile.effort)} · {samples} 个验证样本
          {decisionProfileId === profile.profileId ? " · 已设为决策模型" : ""}
          {editor.configurationDirty && decisionProfileId === profile.profileId ? " · 未保存" : ""}</p>
        {editor.mode && <p className="small muted">{draftFrozen
          ? "登录已失效：草稿仍保留在本页，重新登录后可继续编辑。"
          : "草稿只在本页保存；发布前不会影响正在运行的任务；发现模型由程序发布目录事实。"}</p>}
      </header>
      <DetailTabs id="model-detail" label="模型详情栏目" value={section} items={sections} onChange={onSection} />
      <div className="detail-body">
        {guard && <p className="small muted" role="status">{guard}</p>}
        {error && <p role="alert" className="error-message">{error}</p>}
        {note && <p className="success-message" role="status">{note}</p>}
        {!!profileBlocking.length && <p className="banner error-banner" role="alert">{profileBlocking.map(issue => issue.message).join(" ")}</p>}
        {!!profileAttention.length && <p className="banner attention-banner" role="status">{profileAttention.map(issue => issue.message).join(" ")}</p>}
        {sections.map(([panel]) => <section key={panel} id={"model-detail-" + panel} role="tabpanel" aria-labelledby={"model-detail-" + panel + "-tab"} hidden={section !== panel}>
          {panel === "overview" && <>
            <h3>{cardHeading}</h3><p className="read-text">{card.summary || "暂无评价，等待实际经验。"}</p>
            <p className="small muted">{cardOriginText(card)}</p>
            <div className="reading-grid"><Reading title="适用工作" values={card.strengths} /><Reading title="适用限制" values={card.limitations} /><Reading title="未解决问题" values={card.risks} /></div>
            <h3>我的意见</h3><p className="read-text">{opinion || "尚未记录；开启编辑设置后可以留下单独的人工意见。"}</p>
            <dl className="facts"><dt>上下文</dt><dd>{profile.contextWindow ? profile.contextWindow.toLocaleString() + " tokens" : "未知"}</dd>
              <dt>验证样本</dt><dd>{samples} 个</dd>
              <dt>用户偏好</dt><dd>{preference ? ({ prefer: "优先考虑", pin: "固定选择", exclude: "排除" })[preference.mode] : "无额外偏好"}</dd>
              <dt>评价记录更新</dt><dd>{formatDate(card.updatedAt)}</dd></dl>
            <details><summary>配置标识与来源</summary><dl className="facts"><dt>配置 ID</dt><dd>{profile.profileId}</dd><dt>目录来源</dt><dd>{profile.source || "未记录"}</dd>
              <dt>可用性</dt><dd>{profile.unavailableReason || (profile.available ? "目录声明可用，实际调用仍需验证" : "当前不在目录中")}</dd></dl></details>
          </>}
          {panel === "assessment" && <>
            <section className="assessment-readonly" aria-label={cardReadonlyLabel}>
              <h3>{cardReadonlyLabel}</h3>
              <p className="read-text">{card.summary || "暂无评价"}</p>
              <div className="reading-grid"><Reading title="适用工作" values={card.strengths} /><Reading title="适用限制" values={card.limitations} /><Reading title="未解决问题" values={card.risks} /></div>
              <p className="small muted">{maintenanceCard
                ? "自动评价与验证样本由获授权的维护 Harness 依据证据发布，控制台不能修改，也不会被人工意见覆盖。"
                : "这张卡片没有记录发布者，不能判断为自动评价；内容只读，不会被人工意见覆盖。"}</p>
            </section>
            {draftShown ? <>
              <label className="field"><span>我的意见{opinion !== publishedOpinion && <span className="unsaved-mark">未保存</span>}</span>
                <textarea rows={4} value={opinion} maxLength={4000} aria-label="我的意见" readOnly={!editing}
                  onChange={e => editor.setDraft(d => d ? setAnnotation(d, profile.profileId, e.target.value) : d)}
                  placeholder="单独记录你的使用感受与适用条件；留空表示清除" /></label>
              <p className="small muted">{draftFrozen
                ? "登录已失效：草稿仍保留在本页，重新登录后可继续编辑。"
                : "人工意见单独存储并保留来源，不覆盖已发布的评价、证据或样本计数。发布只提交发生变化的意见。"}</p>
            </> : <section className="annotation-readonly"><h3>我的意见</h3>
              <p className="read-text">{opinion || "尚未记录人工意见。"}</p>
              <p className="small muted">开启右上角“编辑设置”后可以写下或清除人工意见；已发布的评价内容始终只读。</p></section>}
          </>}
          {panel === "preferences" && (draftShown ? <>
            <label className="field"><span>用户偏好</span><select value={preference?.mode || ""} disabled={!editing}
              onChange={e => editor.setDraft(d => d ? setPreference(d, profile.profileId, e.target.value as "prefer" | "pin" | "exclude" | "", preference?.reason || "") : d)}>
              <option value="">无额外偏好</option><option value="prefer">优先考虑</option>
              <option value="pin" disabled={!pinIsExisting && (!profile.available || !profile.enabled)}>固定选择</option><option value="exclude">排除</option></select></label>
            <label className="field"><span>偏好依据</span><input disabled={!preference} readOnly={!editing} value={preference?.reason || ""} maxLength={500}
              onChange={e => editor.setDraft(d => d ? { ...d, preferences: d.preferences.map(p => p.profileId === profile.profileId ? { ...p, reason: e.target.value } : p) } : d)} /></label>
            <label className="checkbox-field"><input type="checkbox" checked={profile.enabled} disabled={!editing || !canEnable}
              onChange={e => editor.setDraft(d => d ? { ...d, profiles: d.profiles.map(p => p.profileId === profile.profileId ? { ...p, enabled: e.target.checked } : p) } : d)} />允许后续选择使用此配置</label>
            <ConcurrencyField key={familyKey(profile)} editor={editor} family={profile}
              draftLimit={draftLimit} occupancy={occupancy} editable={editing} />
            <p className="small muted">并发上限由同一模型的所有思考档位与路由、执行共用；保存后立即对后续任务生效，调低上限不会中断正在运行的任务，只会等占用回落后再放行新任务。计数不包含 Harness 内部子代理、重试或其他应用的 API 请求。{concurrencyUnsaved && <span className="unsaved-mark">并发上限未保存</span>}</p>
            {!profile.available && (profile.enabled
              ? <p className="small muted">此配置当前不在目录中。你仍可以停用它、修改意见或偏好依据；重新启用需要它再次被发现。</p>
              : <p className="small muted">此配置当前不在目录中，不能新启用；可以修改意见或保留历史。</p>)}
            {published && published.enabled !== profile.enabled && <p className="unsaved-mark">启用状态未保存；发布前正在运行的任务仍按原配置执行。</p>}
            <p className="small muted">保存只提交实际修改的字段；可用性、Provider 和模型目录由程序维护，不会随保存上传。</p>
          </> : <>
            <dl className="facts"><dt>用户偏好</dt><dd>{preference ? ({ prefer: "优先考虑", pin: "固定选择", exclude: "排除" })[preference.mode] : "无额外偏好"}</dd>
              <dt>偏好依据</dt><dd>{preference?.reason || "未记录"}</dd>
              <dt>启用状态</dt><dd>{profile.enabled ? "允许后续选择使用此配置" : "已停用"}</dd>
              <dt>并发任务上限</dt><dd>{recordedLimit ?? `${MODEL_CONCURRENCY_DEFAULT}（默认）`}{occupancy !== null ? ` · 当前占用 ${occupancy}` : ""}</dd></dl>
            <p className="small muted">并发上限由同一模型的所有思考档位与路由、执行共用；当前占用是只读观察，调低上限不会中断正在运行的任务。计数不包含 Harness 内部子代理、重试或其他应用的 API 请求。</p>
            <p className="small muted">开启右上角“编辑设置”后可以修改偏好与启用状态；发布不会改变运行中任务的配置。</p>
          </>)}
          {panel === "evidence" && <section className="evidence-readonly" aria-label="评价证据（只读）">
            <h3>依据与观察</h3>
            <p className="small muted">证据与样本计数始终只读，不受编辑设置影响。反馈请交给配置了 hey-my-buddy skill 的 Harness，由其保留来源并纳入整理。</p>
            {evidence.length ? <ul className="evidence-list">{evidence.map(e => <li className="evidence-item" key={e.evidenceId}>
              <p>{e.summary}</p><span className="small muted">{e.source} · {formatDate(e.createdAt)}{e.project ? " · " + e.project : ""}</span>
              {!!e.conditions.length && <p className="small">条件：{e.conditions.join("；")}</p>}
              {card.evidenceIds.includes(e.evidenceId) && <p className="small muted">已用于当前卡片</p>}
            </li>)}</ul> : <p className="muted evidence-empty">{EMPTY_EVIDENCE}</p>}
          </section>}
        </section>)}
      </div>
    </> : <div className="detail-placeholder"><h2>选择模型</h2><p>同一模型的思考档位集中展示；已发布评价、人工意见与偏好分别保存。</p></div>}
    </div>
  </aside>;
}

function Reading({ title, values }: { title: string; values: string[] }) {
  return <section className="reading-section"><h3>{title}</h3>{values.length ? <ul>{values.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="muted">未记录</p>}</section>;
}
