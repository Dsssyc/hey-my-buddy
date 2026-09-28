import { useId, useRef, useState } from "react";
import {
  emptyCard,
  familyAnnotationText,
  setConcurrencyLimit,
  setFamilyAnnotation,
  setFamilyPreference,
  setPreferenceOverride,
} from "./draft";
import type { Editor } from "./use-editor";
import type {
  ConsoleView,
  FamilyPreference,
  OverrideMode,
  Preference,
  PreferenceMode,
  PreferenceOverride,
  Profile,
  Snapshot,
} from "./types";
import { Badge, Help, formatDate } from "./ui";
import { effortText, profileTitle } from "./profile-display";
import {
  MODEL_CONCURRENCY_DEFAULT,
  MODEL_CONCURRENCY_MAX,
  MODEL_CONCURRENCY_MIN,
  cardOriginText,
  concurrencyEntryFor,
  familyKey,
  recordedSampleCount,
} from "./console-data";
import type { ModelFamilyGroup } from "./console-data";
import {
  OVERRIDE_LABEL,
  PREFERENCE_HELP,
  PREFERENCE_ICON,
  PREFERENCE_LABEL,
  harnessName,
} from "./buddy-display";
import { routerRefusal } from "./policy";
import { Popover, popoverButtonProps } from "./Popover";
import { ConfirmDialog } from "./ConfirmDialog";

export const EMPTY_EVIDENCE = "暂无评价证据。你可以让已配置 hey-my-buddy skill 的 Harness 执行一次模型评价更新，或在该 Harness 中设置定时更新任务。";
const PREFERENCE_MODES: PreferenceMode[] = ["prefer", "pin", "exclude"];

/** DOM id of an effort tag, so the routing details can jump to it. */
export function effortTagId(profileId: string): string {
  return `effort-tag-${profileId}`;
}

type TagProps = {
  profile: Profile;
  editor: Editor;
  /** Published enabled intent; a differing draft value is marked unsaved. */
  recordedEnabled: boolean | undefined;
  effective: Preference | undefined;
  override: PreferenceOverride | undefined;
  familyPreference: FamilyPreference | undefined;
  /** The override mode in the draft's baseline, for the pin-transition rule. */
  baselineOverrideMode: OverrideMode | undefined;
  isRouter: boolean;
  onSetRouter: (profile: Profile) => void;
};

/**
 * One effort as a tag: its enable switch, its effective preference (colour,
 * glyph and border style together, never colour alone), an override notch
 * when the effort overrides its family, and the Router mark. The menu holds
 * the effort's preference override and "设为 Router".
 */
function EffortTag({
  profile, editor, recordedEnabled, effective, override, familyPreference, baselineOverrideMode, isRouter, onSetRouter,
}: TagProps) {
  const [menuOpen, setMenuOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  const effort = effortText(profile.effort) || profile.effort || "默认";
  const menuId = `effort-menu-${profile.profileId}`;
  const reasonId = `router-reason-${profile.profileId}`;
  const pending = recordedEnabled !== undefined && recordedEnabled !== profile.enabled;
  const canToggle = editor.editing && (profile.available || profile.enabled);
  const switchTitle = !editor.sessionWritable ? "登录已失效；重新登录后可修改"
    : !editor.editing ? "保存进行中或结果未确认，暂不能修改"
      : !profile.available && !profile.enabled ? "该档位当前不可用，不能新启用" : undefined;
  const refusal = isRouter ? "已是 Router" : routerRefusal(profile);
  const pinLocked = baselineOverrideMode !== "pin" && (!profile.available || !profile.enabled);
  const overrideValue: OverrideMode | "" = override?.mode ?? "";
  const preferenceText = effective
    ? `偏好：${PREFERENCE_LABEL[effective.mode]}（${effective.source === "override" ? "档位覆盖" : "来自家族"}）`
    : override?.mode === "none" ? "偏好：无偏好（档位覆盖）" : "偏好：无";
  const classes = ["effort-tag",
    profile.enabled ? "enabled" : "",
    profile.available ? "" : "unavailable",
    effective ? `pref-${effective.mode}` : "",
    override ? "override" : "",
    isRouter ? "router" : ""].filter(Boolean).join(" ");
  function toggle() {
    if (!canToggle) return;
    editor.update(d => ({ ...d, profiles: d.profiles.map(p => p.profileId === profile.profileId ? { ...p, enabled: !p.enabled } : p) }));
  }
  function setOverride(mode: OverrideMode | "") {
    editor.update(d => setPreferenceOverride(d, profile.profileId, mode, mode ? override?.reason ?? "" : ""));
  }
  return <div className={classes} id={effortTagId(profile.profileId)} tabIndex={-1} role="group"
    aria-label={`${effort} 档位`} title={effective ? `${PREFERENCE_LABEL[effective.mode]}${effective.reason ? `：${effective.reason}` : ""}` : undefined}>
    <button type="button" role="switch" className="tag-switch" aria-checked={profile.enabled}
      aria-label={`启用 ${effort}`} disabled={!canToggle} title={switchTitle} onClick={toggle}>
      <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
    </button>
    <span className="effort-name">{effort}</span>
    {effective && <span className="pref-icon" aria-hidden="true">{PREFERENCE_ICON[effective.mode]}</span>}
    {isRouter && <span className="router-mark">Router</span>}
    {!profile.available && <span className="unavailable-mark">不可用</span>}
    {pending && <span className="unsaved-dot" title="启用状态未保存" aria-hidden="true">•</span>}
    <span className="sr-only">{[profile.enabled ? "已启用" : "未启用", preferenceText,
      profile.available ? "" : "目录不可用", pending ? "未保存" : ""].filter(Boolean).join("，")}</span>
    <button ref={menuButton} type="button" className="tag-menu-button" aria-label={`${effort} 档位菜单`}
      aria-haspopup="dialog" {...popoverButtonProps(menuId, menuOpen)} onClick={() => setMenuOpen(value => !value)}>▾</button>
    {menuOpen && <Popover id={menuId} anchor={menuButton.current} label={`${profileTitle(profile)} 档位设置`}
      onClose={() => setMenuOpen(false)} className="effort-menu" width="min(20em, calc(100vw - 16px))">
      <fieldset className="menu-group" disabled={!editor.editing}>
        <legend>档位偏好 <Help label="档位偏好说明">{`档位覆盖优先于家族偏好。“无偏好”表示这个档位明确不带偏好，即使家族有默认值；“跟随家族”删除覆盖。${PREFERENCE_HELP}`}</Help></legend>
        <label className="menu-radio"><input type="radio" name={`override-${profile.profileId}`} checked={overrideValue === ""}
          onChange={() => setOverride("")} />跟随家族（{familyPreference ? PREFERENCE_LABEL[familyPreference.mode] : "无"}）</label>
        {(["none", ...PREFERENCE_MODES] as OverrideMode[]).map(mode => {
          const locked = mode === "pin" && pinLocked;
          return <label key={mode} className="menu-radio" title={locked ? "只有可用且已启用的档位才能新设为固定" : undefined}>
            <input type="radio" name={`override-${profile.profileId}`} checked={overrideValue === mode}
              disabled={locked} onChange={() => setOverride(mode)} />
            {mode !== "none" && <span className={`pref-swatch pref-${mode}`} aria-hidden="true">{PREFERENCE_ICON[mode]}</span>}
            {OVERRIDE_LABEL[mode]}
          </label>;
        })}
        {override && <label className="field compact-field"><span>覆盖理由</span>
          <input value={override.reason} maxLength={500}
            onChange={e => editor.update(d => setPreferenceOverride(d, profile.profileId, override.mode, e.target.value))} /></label>}
      </fieldset>
      <div className="menu-group">
        <button type="button" className="button small-button menu-action" aria-disabled={!!refusal || !editor.editing || undefined}
          aria-describedby={refusal ? reasonId : undefined}
          onClick={() => {
            if (refusal || !editor.editing) return;
            setMenuOpen(false);
            onSetRouter(profile);
          }}>设为 Router</button>
        {refusal && <p id={reasonId} className="small muted menu-reason">{refusal}</p>}
      </div>
    </Popover>}
  </div>;
}

/**
 * Invalid input stays in the local draft so Save cannot silently publish an
 * earlier valid prefix. NaN represents an empty number field; policy validation
 * blocks it before acquiring any publication grant.
 */
function ConcurrencyField({ editor, family, draftLimit, occupancy, unsaved }: {
  editor: Editor; family: ModelFamilyGroup; draftLimit: number; occupancy: number | null; unsaved: boolean;
}) {
  const invalid = !Number.isInteger(draftLimit)
    || draftLimit < MODEL_CONCURRENCY_MIN || draftLimit > MODEL_CONCURRENCY_MAX;
  const member = family.profiles[0];
  return <div className="concurrency-row">
    <input type="number" inputMode="numeric" min={MODEL_CONCURRENCY_MIN} max={MODEL_CONCURRENCY_MAX}
      step={1} value={Number.isNaN(draftLimit) ? "" : draftLimit} disabled={!editor.editing}
      aria-label="并发上限" aria-invalid={invalid || undefined} aria-describedby="concurrency-note"
      onChange={(event) => {
        const raw = event.target.value;
        const value = raw === "" ? NaN : Number(raw);
        editor.update((draft) => setConcurrencyLimit(draft, member, value));
      }} />
    <span className="small muted" id="concurrency-note">
      当前占用 {occupancy ?? "未知"}{invalid ? " · 需要输入 1–32 的整数，当前修改不会保存" : ""}
    </span>
    {unsaved && <span className="unsaved-mark">未保存</span>}
  </div>;
}

function Reading({ title, values }: { title: string; values: string[] }) {
  return <section className="reading-section"><h4>{title}</h4>{values.length ? <ul>{values.map((item, i) => <li key={i}>{item}</li>)}</ul> : <p className="muted">未记录</p>}</section>;
}

/**
 * The selected family: effort tags, the family-level preference, concurrency
 * and note, and the read-only evaluations. Every control edits the local draft
 * directly; the page's save bar publishes it.
 */
export function FamilyDetail({ family, data, recorded, editor, isNew = false, onCloseList }: {
  family: ModelFamilyGroup;
  /** Draft (when editing) merged with retained history rows. */
  data: ConsoleView;
  /** Recorded snapshot merged with retained history rows. */
  recorded: Snapshot;
  editor: Editor;
  isNew?: boolean;
  onCloseList: () => void;
}) {
  const [routerConfirm, setRouterConfirm] = useState<Profile | null>(null);
  const radioName = useId();
  const key = family.key;
  const efforts = family.profiles;
  const enabledCount = efforts.filter(p => p.enabled).length;
  const available = efforts.some(p => p.available);
  const routerId = data.configuration.decisionProfileId;
  const currentRouter = routerId ? data.profiles.find(p => p.profileId === routerId) : undefined;
  const familyPreference = data.familyPreferences.find(p => familyKey(p) === key);
  const recordedFamilyPreference = recorded.familyPreferences.find(p => familyKey(p) === key);
  const baselineFamilyMode = editor.baseline?.familyPreferences.find(p => familyKey(p) === key)?.mode
    ?? recordedFamilyPreference?.mode;
  const familyPinLocked = baselineFamilyMode !== "pin" && !efforts.some(p => p.available && p.enabled);
  const preferenceUnsaved = (familyPreference?.mode ?? "") !== (recordedFamilyPreference?.mode ?? "")
    || (familyPreference?.reason ?? "") !== (recordedFamilyPreference?.reason ?? "");
  const note = familyAnnotationText(data, family.profiles[0]);
  const noteUnsaved = note !== familyAnnotationText(recorded, family.profiles[0]);
  // One shared concurrency setting per adapter/provider/model family. Occupancy
  // comes from the recorded snapshot: it is observation and never drafted.
  const draftEntry = concurrencyEntryFor(data.modelConcurrency, family.profiles[0]);
  const recordedEntry = concurrencyEntryFor(recorded.modelConcurrency, family.profiles[0]);
  const draftLimit = draftEntry ? draftEntry.limit : MODEL_CONCURRENCY_DEFAULT;
  const recordedLimit = recordedEntry ? recordedEntry.limit : MODEL_CONCURRENCY_DEFAULT;
  const occupancy = recordedEntry && Number.isInteger(recordedEntry.active) ? recordedEntry.active : null;
  const ids = new Set(efforts.map(p => p.profileId));
  const blocking = editor.blocking.filter(issue => issue.family === key || (!issue.family && ids.has(issue.profileId)));
  // The Router's own warning lives in the page's routing status row.
  const attention = editor.attention.filter(issue => !issue.router
    && (issue.family === key || (!issue.family && ids.has(issue.profileId))));
  const cards = efforts.map(p => data.cards.find(c => c.profileId === p.profileId) ?? emptyCard(p.profileId));
  const assessed = cards.filter(card => card.summary).length;
  const samples = efforts.reduce((sum, p) => sum + recordedSampleCount(data, p.profileId), 0);
  const updates = cards.map(card => card.updatedAt).filter((value): value is string => !!value).sort();
  const latest = updates.length ? updates[updates.length - 1] : null;

  function setRouter(profile: Profile) {
    editor.update(d => ({ ...d, configuration: { ...d.configuration, decisionProfileId: profile.profileId } }));
  }
  function requestRouter(profile: Profile) {
    if (routerId && routerId !== profile.profileId) setRouterConfirm(profile);
    else setRouter(profile);
  }
  function closeConfirm(target: Profile) {
    setRouterConfirm(null);
    document.getElementById(effortTagId(target.profileId))?.focus();
  }
  function setFamilyMode(mode: PreferenceMode | "") {
    editor.update(d => setFamilyPreference(d, family.profiles[0], mode, mode ? familyPreference?.reason ?? "" : ""));
  }

  return <>
    <header className="detail-header family-header">
      <div className="row-between">
        <button className="button small-button mobile-back" onClick={onCloseList}>返回模型列表</button>
        <span className="small muted">{harnessName(family.adapter)} / {family.provider}</span>
        <span className="chip-row">
          {isNew && <Badge tone="green">新</Badge>}
          <Badge tone={available ? "green" : "amber"}>{available ? "可用" : "不可用"}</Badge>
          <Badge tone={enabledCount ? "green" : "neutral"}>已启用 {enabledCount}/{efforts.length}</Badge>
        </span>
      </div>
      <h2>{family.name}</h2>
    </header>
    <div className="detail-body family-body">
      {!!blocking.length && <p className="banner error-banner" role="alert">{blocking.map(issue => issue.message).join(" ")}</p>}
      {!!attention.length && <p className="banner attention-banner" role="status">{attention.map(issue => issue.message).join(" ")}</p>}
      <div className="family-fields">
        <div className="family-field">
          <span className="family-label">档位 <Help label="档位说明">点开关即启用或停用该档位；标签颜色、图标和描边表示生效偏好（▲ 优先、◆ 固定、⊘ 排除），右上角缺口表示该档位覆盖了家族偏好。偏好覆盖和“设为 Router”在标签菜单 ▾ 中。</Help></span>
          <div className="effort-tags" role="group" aria-label="档位">
            {efforts.map(profile => <EffortTag key={profile.profileId} profile={profile} editor={editor}
              recordedEnabled={recorded.profiles.find(p => p.profileId === profile.profileId)?.enabled}
              effective={data.preferences.find(p => p.profileId === profile.profileId)}
              override={data.preferenceOverrides.find(p => p.profileId === profile.profileId)}
              familyPreference={familyPreference}
              baselineOverrideMode={(editor.baseline ?? recorded).preferenceOverrides.find(p => p.profileId === profile.profileId)?.mode}
              isRouter={routerId === profile.profileId} onSetRouter={requestRouter} />)}
          </div>
        </div>
        <div className="family-field">
          <span className="family-label">偏好 <Help label="家族偏好说明">{`家族偏好作用于该家族所有没有覆盖的档位；单个档位可在标签菜单中覆盖。${PREFERENCE_HELP}`}</Help></span>
          <div className="family-value">
            <div className="segmented" role="radiogroup" aria-label="家族偏好">
              {(["", ...PREFERENCE_MODES] as (PreferenceMode | "")[]).map(mode => {
                const locked = mode === "pin" && familyPinLocked;
                return <label key={mode || "none"} className={"segment" + ((familyPreference?.mode ?? "") === mode ? " checked" : "") + (mode ? ` pref-${mode}` : "")}
                  title={locked ? "没有可用且已启用的档位，不能新设为固定" : undefined}>
                  <input type="radio" name={radioName} checked={(familyPreference?.mode ?? "") === mode}
                    disabled={!editor.editing || locked} onChange={() => setFamilyMode(mode)} />
                  {mode && <span aria-hidden="true">{PREFERENCE_ICON[mode]} </span>}{mode ? PREFERENCE_LABEL[mode] : "无"}
                </label>;
              })}
              {preferenceUnsaved && <span className="unsaved-mark">未保存</span>}
            </div>
            {familyPreference && <input className="reason-input" aria-label="偏好理由" placeholder="理由（可选）" maxLength={500}
              value={familyPreference.reason} disabled={!editor.editing}
              onChange={e => editor.update(d => setFamilyPreference(d, family.profiles[0], familyPreference.mode, e.target.value))} />}
          </div>
        </div>
        <div className="family-field">
          <span className="family-label">并发 <Help label="并发上限说明">并发上限由同一模型的所有思考档位与路由、执行共用，范围 1–32；保存后立即对后续任务生效。调低上限不会中断正在运行的任务，只会等占用回落后再放行新任务。计数不包含 Harness 内部子代理、重试或其他应用的 API 请求。</Help></span>
          <ConcurrencyField editor={editor} family={family} draftLimit={draftLimit} occupancy={occupancy}
            unsaved={!Object.is(draftLimit, recordedLimit)} />
        </div>
        <div className="family-field">
          <span className="family-label">备注 <Help label="家族备注说明">备注属于整个模型家族，单独存储，不覆盖评价、证据或样本计数；留空表示清除。</Help></span>
          <div className="family-value">
            <textarea rows={3} value={note} maxLength={4000} aria-label="家族备注" disabled={!editor.editing}
              placeholder="记录使用感受与适用条件"
              onChange={e => editor.update(d => setFamilyAnnotation(d, family.profiles[0], e.target.value))} />
            {noteUnsaved && <span className="unsaved-mark">未保存</span>}
          </div>
        </div>
        <section className="family-field" aria-label="评价（只读）">
          <span className="family-label">评价 <Help label="评价说明">评价、证据与样本计数由获授权的维护 Harness 依据证据按档位发布，控制台只读，不会被偏好或备注覆盖。</Help></span>
          <div className="family-value evaluation-list">
            <p className="small">{assessed}/{efforts.length} 个档位有评价 · 验证样本 {samples} 个{latest ? ` · 最近更新 ${formatDate(latest)}` : ""}</p>
            {efforts.map((profile, index) => {
              const card = cards[index];
              const evidence = recorded.evidence.filter(e => e.profileId === profile.profileId);
              return <details key={profile.profileId} className="effort-evaluation">
                <summary><strong>{effortText(profile.effort) || profile.effort}</strong>
                  <span className="muted"> · {card.summary ? card.summary.split("\n")[0] : "暂无评价"} · 样本 {recordedSampleCount(data, profile.profileId)}</span></summary>
                <div className="effort-evaluation-body">
                  {card.summary && <p className="read-text">{card.summary}</p>}
                  <p className="small muted">{cardOriginText(card)} · 更新于 {formatDate(card.updatedAt)}</p>
                  <div className="reading-grid"><Reading title="适用工作" values={card.strengths} /><Reading title="适用限制" values={card.limitations} /><Reading title="未解决问题" values={card.risks} /></div>
                  <h4>证据</h4>
                  {evidence.length ? <ul className="evidence-list">{evidence.map(e => <li className="evidence-item" key={e.evidenceId}>
                    <p>{e.summary}</p><span className="small muted">{e.source} · {formatDate(e.createdAt)}{e.project ? " · " + e.project : ""}</span>
                    {!!e.conditions.length && <p className="small">条件：{e.conditions.join("；")}</p>}
                    {card.evidenceIds.includes(e.evidenceId) && <p className="small muted">已用于当前评价</p>}
                  </li>)}</ul> : <p className="small muted evidence-empty">{EMPTY_EVIDENCE}</p>}
                  <dl className="facts"><dt>配置 ID</dt><dd>{profile.profileId}</dd>
                    <dt>上下文</dt><dd>{profile.contextWindow ? profile.contextWindow.toLocaleString() + " tokens" : "未知"}</dd>
                    <dt>能力</dt><dd>{profile.capabilities.length ? profile.capabilities.join("、") : "未记录"}</dd>
                    <dt>目录来源</dt><dd>{profile.source || "未记录"}</dd>
                    <dt>可用性</dt><dd>{profile.unavailableReason || (profile.available ? "目录声明可用，实际调用仍需验证" : "当前不在目录中")}</dd></dl>
                </div>
              </details>;
            })}
          </div>
        </section>
      </div>
    </div>
    {routerConfirm && <ConfirmDialog title="替换 Router" confirmLabel="替换"
      onCancel={() => closeConfirm(routerConfirm)}
      onConfirm={() => { setRouter(routerConfirm); closeConfirm(routerConfirm); }}>
      将替换当前 Router {currentRouter ? profileTitle(currentRouter) : routerId}，改为 {profileTitle(routerConfirm)}。保存后用于后续路由，正在运行的任务不受影响。
    </ConfirmDialog>}
  </>;
}
