import { useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { addProfiles, emptyCard, setPreference, splitLines } from "./draft";
import type { Editor } from "./use-editor";
import type { Card, Profile, Snapshot } from "./types";
import { Badge, Empty, formatDate } from "./ui";
import { effortText, profileName } from "./profile-display";
import { familyKey, modelFamilies, preferredVariant, recordedSampleCount } from "./console-data";
import { DetailTabs } from "./DetailTabs";
import { SplitView } from "./SplitView";
import { EvaluationHistory } from "./EvaluationHistory";

const sections = [["overview", "概览"], ["assessment", "能力评价"], ["preferences", "偏好与启用"], ["evidence", "证据"]] as const;
const EMPTY_EVIDENCE = "暂无评价证据。你可以让已配置 hey-my-buddy skill 的 Harness 执行一次模型评价更新，或在该 Harness 中设置定时更新任务。";

export function Models({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [harness, setHarness] = useState(""), [enabledOnly, setEnabledOnly] = useState(false);
  const [section, setSection] = useState("overview");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const [guard, setGuard] = useState("");
  const data = editor.draft || snapshot;
  const editing = editor.editing;
  const families = modelFamilies(data.profiles);
  const visible = families.filter(g => (!harness || g.adapter === harness) && (!enabledOnly || g.profiles.some(p => p.enabled))
    && g.profiles.some(p => [p.label, p.model, p.provider, p.adapter, effortText(p.effort)].join(" ").toLowerCase().includes(query.toLowerCase())));
  const profile = data.profiles.find(p => p.profileId === selected);
  const published = profile ? snapshot.profiles.find(p => p.profileId === profile.profileId) : undefined;
  const family = profile && families.find(g => g.key === familyKey(profile));
  const card = profile ? data.cards.find(c => c.profileId === profile.profileId) || emptyCard(profile.profileId) : null;
  const preference = data.preferences.find(p => p.profileId === selected);
  const evidence = profile ? snapshot.evidence.filter(e => e.profileId === profile.profileId) : [];
  const samples = profile ? recordedSampleCount(snapshot, profile.profileId) : 0;
  const decisionProfileId = data.configuration.decisionProfileId;
  // A refusal only stays on screen while it still describes the current state.
  const shownGuard = editing && mutationsAvailable ? "" : guard;
  function updateCard(patch: Partial<Card>) {
    editor.setDraft(d => d && profile ? { ...d, cards: [...d.cards.filter(c => c.profileId !== profile.profileId), { ...card!, ...patch }] } : d);
  }
  async function discover() {
    if (!editing || busy || !mutationsAvailable) {
      setGuard(!editing ? "开启编辑模式后才能把发现的配置加入草稿。"
        : !mutationsAvailable ? "连接中断或缺少写入资格：暂时不能发现模型，草稿仍保留。" : "");
      return;
    }
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setGuard("");
    try {
      const result = await api.command<{ profiles: Profile[] }>("model_catalog_refresh", { requestId: crypto.randomUUID() }, snapshot.csrfToken);
      if (!Array.isArray(result.profiles)) throw new Error("目录未返回可识别的模型配置。");
      editor.setDraft(d => d ? addProfiles(d, result.profiles) : d);
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  const list = <section className="panel list-panel" aria-label="模型目录">
    <div className="panel-toolbar"><h2>模型 <span className="muted">{families.length}</span></h2>
      <div className="actions"><button className="button small-button" aria-pressed={historyOpen} onClick={() => setHistoryOpen(true)}>更新记录</button>
        <button className="button small-button" aria-disabled={!editing || busy || !mutationsAvailable}
          title={!editing ? "开启编辑模式后才能发现模型" : !mutationsAvailable ? "连接中断或缺少写入资格" : undefined}
          onClick={() => void discover()}>发现模型</button></div></div>
    <div className="list-filters">
      <label className="search"><span className="sr-only">搜索模型配置</span>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型、Harness、提供方" /></label>
      <div className="filter-pair"><label><span className="sr-only">Harness 筛选</span>
        <select value={harness} onChange={e => setHarness(e.target.value)}><option value="">全部 Harness</option>
          {[...new Set(families.map(g => g.adapter))].map(name => <option key={name}>{name}</option>)}</select></label>
        <label className="check-field"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)} />仅已启用</label></div>
      <p className="small muted">{visible.length} 个模型 · {data.profiles.length} 个执行配置</p>
      {!profile && (shownGuard || error) && <p className="banner guard-banner" role="status">{shownGuard || error}</p>}
    </div>
    <div className="list-scroll" tabIndex={0} aria-label="模型条目">
      {visible.length ? <ul className="profile-list">{visible.map((group, index) => <li key={group.key}>
        {(index === 0 || visible[index - 1].adapter !== group.adapter) && <h3 className="group-heading">{group.adapter}</h3>}
        <button className={"profile-row " + (family?.key === group.key ? "selected" : "")} aria-pressed={family?.key === group.key}
          onClick={() => { setHistoryOpen(false); if (family?.key !== group.key) setSelected(preferredVariant(group, data.preferences).profileId); }}>
          <span className="row-between"><strong>{group.name}</strong><span className="small muted">{group.profiles.filter(p => p.enabled).length}/{group.profiles.length} 启用</span></span>
          <span className="small muted truncate">{group.provider}</span>
          <span className="small effort-summary">{group.profiles.map(p => effortText(p.effort)).join(" · ")}
            {group.profiles.some(p => data.preferences.some(x => x.profileId === p.profileId && ["pin", "prefer"].includes(x.mode))) && " · 有用户偏好"}</span>
        </button>
      </li>)}</ul> : <Empty title={data.profiles.length ? "没有匹配的模型" : "尚未接入模型"}>开启编辑模式后可发现本机模型。</Empty>}
    </div>
  </section>;
  const detail = <aside className="panel detail-panel" aria-label="评价卡片详情">
    <div className="model-detail-content" hidden={!historyOpen}>
      <EvaluationHistory snapshot={snapshot} api={api} active={active && historyOpen} onBack={() => setHistoryOpen(false)} />
    </div>
    <div className="model-detail-content" hidden={historyOpen}>
    {profile && card && family ? <>
      <header className="detail-header">
        <div className="row-between"><button className="button small-button mobile-back" onClick={() => setSelected(null)}>返回模型列表</button>
          <span className="small muted">{profile.adapter} / {profile.provider}</span>
          <span className="chip-row">
            <Badge tone={profile.enabled ? "green" : "neutral"}>{profile.enabled ? "已启用" : "已停用"}</Badge>
            {published && published.enabled !== profile.enabled && <span className="unsaved-mark">未保存</span>}
            <Badge tone={profile.available ? "green" : "amber"}>{profile.available ? "目录可用" : "待确认"}</Badge>
          </span></div>
        <h2>{profileName(profile)}</h2>
        <div className="variant-switch" role="group" aria-label="思考强度">
          {family.profiles.map(p => {
            const inspected = p.profileId === selected;
            const wasEnabled = snapshot.profiles.find(x => x.profileId === p.profileId)?.enabled === true;
            const pending = p.enabled !== wasEnabled;
            return <button key={p.profileId} type="button" aria-pressed={inspected}
              aria-label={`${effortText(p.effort)}，${p.enabled ? "已启用" : "未启用"}${pending ? "，未保存" : ""}${inspected ? "，正在查看" : ""}`}
              className={"effort-choice" + (p.enabled ? " enabled" : "") + (inspected ? " inspected" : "")}
              onClick={() => setSelected(p.profileId)}>
              <span className="effort-name">{effortText(p.effort)}</span>
              {p.enabled && <span className="enabled-mark">✓ 已启用</span>}
              {pending && <span className="unsaved-mark">未保存</span>}
            </button>;
          })}
        </div>
        <p className="small muted">当前查看：{profile.model} / {effortText(profile.effort)} · {samples} 个验证样本
          {decisionProfileId === profile.profileId ? " · 已设为决策模型" : ""}
          {editor.configurationDirty && decisionProfileId === profile.profileId ? " · 未保存" : ""}</p>
        {editor.mode && <p className="small muted">草稿只在本页保存；发布前不会影响正在运行的任务。</p>}
      </header>
      <DetailTabs id="model-detail" label="模型详情栏目" value={section} items={sections} onChange={setSection} />
      <div className="detail-body">
        {shownGuard && <p className="small muted" role="status">{shownGuard}</p>}
        {error && <p role="alert" className="error-message">{error}</p>}
        {sections.map(([panel]) => <section key={panel} id={"model-detail-" + panel} role="tabpanel" aria-labelledby={"model-detail-" + panel + "-tab"} hidden={section !== panel}>
          {panel === "overview" && <>
            <h3>当前评价</h3><p className="read-text">{card.summary || "暂无评价，等待实际经验。"}</p>
            <div className="reading-grid"><Reading title="适用工作" values={card.strengths} /><Reading title="适用限制" values={card.limitations} /><Reading title="未解决问题" values={card.risks} /></div>
            <dl className="facts"><dt>上下文</dt><dd>{profile.contextWindow ? profile.contextWindow.toLocaleString() + " tokens" : "未知"}</dd>
              <dt>验证样本</dt><dd>{samples} 个</dd>
              <dt>用户偏好</dt><dd>{preference ? ({ prefer: "优先考虑", pin: "固定选择", exclude: "排除" })[preference.mode] : "无额外偏好"}</dd>
              <dt>评价更新</dt><dd>{formatDate(card.updatedAt)}</dd></dl>
            <details><summary>配置标识与来源</summary><dl className="facts"><dt>配置 ID</dt><dd>{profile.profileId}</dd><dt>目录来源</dt><dd>{profile.source || "未记录"}</dd>
              <dt>可用性</dt><dd>{profile.unavailableReason || "目录声明可用，实际调用仍需验证"}</dd></dl></details>
          </>}
          {panel === "assessment" && (editing ? <>
            <label className="field"><span>当前评价</span><textarea rows={3} value={card.summary} maxLength={2000} onChange={e => updateCard({ summary: e.target.value })} placeholder="描述适用条件与实际表现" /></label>
            <div className="reading-grid">{(["strengths", "limitations", "risks"] as const).map(key => <label className="field" key={key}>
              <span>{{ strengths: "适用工作", limitations: "适用限制", risks: "未解决问题" }[key]}</span>
              <textarea rows={4} value={card[key].join("\n")} maxLength={4000} onChange={e => updateCard({ [key]: splitLines(e.target.value) })} placeholder="每行一条，保留适用条件" />
            </label>)}</div><p className="small muted">草稿保留在当前配置，切换档位或页面不会丢失。发布时统一提交。</p>
          </> : <><h3>当前评价</h3><p className="read-text">{card.summary || "暂无评价"}</p>
            <div className="reading-grid"><Reading title="适用工作" values={card.strengths} /><Reading title="适用限制" values={card.limitations} /><Reading title="未解决问题" values={card.risks} /></div>
            <p className="small muted">开启右上角“编辑模式”后可以修改此配置的评价。</p></>)}
          {panel === "preferences" && (editing ? <>
            <label className="field"><span>用户偏好</span><select value={preference?.mode || ""}
              onChange={e => editor.setDraft(d => d ? setPreference(d, profile.profileId, e.target.value as "prefer" | "pin" | "exclude" | "", preference?.reason || "") : d)}>
              <option value="">无额外偏好</option><option value="prefer">优先考虑</option><option value="pin">固定选择</option><option value="exclude">排除</option></select></label>
            <label className="field"><span>偏好依据</span><input disabled={!preference} value={preference?.reason || ""} maxLength={500}
              onChange={e => editor.setDraft(d => d ? { ...d, preferences: d.preferences.map(p => p.profileId === profile.profileId ? { ...p, reason: e.target.value } : p) } : d)} /></label>
            <label className="checkbox-field"><input type="checkbox" checked={profile.enabled}
              onChange={e => editor.setDraft(d => d ? { ...d, profiles: d.profiles.map(p => p.profileId === profile.profileId ? { ...p, enabled: e.target.checked } : p) } : d)} />允许后续选择使用此配置</label>
            {published && published.enabled !== profile.enabled && <p className="unsaved-mark">启用状态未保存；发布前正在运行的任务仍按原配置执行。</p>}
            <p className="small muted">仅修改当前 Harness、提供方、模型和思考档位。发布不会改变运行中任务的配置。</p>
          </> : <>
            <dl className="facts"><dt>用户偏好</dt><dd>{preference ? ({ prefer: "优先考虑", pin: "固定选择", exclude: "排除" })[preference.mode] : "无额外偏好"}</dd>
              <dt>偏好依据</dt><dd>{preference?.reason || "未记录"}</dd>
              <dt>启用状态</dt><dd>{profile.enabled ? "允许后续选择使用此配置" : "已停用"}</dd></dl>
            <p className="small muted">开启右上角“编辑模式”后可以修改偏好与启用状态；发布不会改变运行中任务的配置。</p>
          </>)}
          {panel === "evidence" && <section className="evidence-readonly" aria-label="评价证据（只读）">
            <h3>依据与观察</h3>
            <p className="small muted">证据始终只读，不受编辑模式影响。反馈请交给配置了 hey-my-buddy skill 的 Harness，由其保留来源并纳入整理。</p>
            {evidence.length ? <ul className="evidence-list">{evidence.map(e => <li className="evidence-item" key={e.evidenceId}>
              <p>{e.summary}</p><span className="small muted">{e.source} · {formatDate(e.createdAt)}{e.project ? " · " + e.project : ""}</span>
              {!!e.conditions.length && <p className="small">条件：{e.conditions.join("；")}</p>}
              {card.evidenceIds.includes(e.evidenceId) && <p className="small muted">已用于当前卡片</p>}
            </li>)}</ul> : <p className="muted evidence-empty">{EMPTY_EVIDENCE}</p>}
          </section>}
        </section>)}
      </div>
    </> : <div className="detail-placeholder"><h2>选择模型</h2><p>同一模型的思考档位集中展示，评价与偏好仍分别保存。</p></div>}
    </div>
  </aside>;
  return <SplitView selected={historyOpen || !!profile} list={list} detail={detail} />;
}
function Reading({ title, values }: { title: string; values: string[] }) {
  return <section className="reading-section"><h3>{title}</h3>{values.length ? <ul>{values.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="muted">未记录</p>}</section>;
}
