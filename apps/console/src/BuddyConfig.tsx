import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { historyView } from "./draft";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { Empty, Help } from "./ui";
import { effortText } from "./profile-display";
import { familyKey, modelFamilies } from "./console-data";
import { MAX_HISTORY_PROFILES, useProfileHistory } from "./use-profile-history";
import { SplitView } from "./SplitView";
import { EvaluationHistory } from "./EvaluationHistory";
import { FamilyDetail, effortTagId } from "./FamilyDetail";
import { RoutingStatusBar } from "./RoutingStatusBar";
import { harnessGroups, harnessUnavailableText } from "./buddy-display";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";

/**
 * "Buddy 配置": which configurations may work as a buddy, and which effort is
 * the Router. The left list groups model families by harness; the right side
 * edits the selected family directly. The routing state sits in one line on
 * top. Nothing here recommends a configuration.
 */
export function BuddyConfig({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [enabledOnly, setEnabledOnly] = useState(false);
  /** Retained unavailable history was requested (paged through `model_profiles`). */
  const [retainedOpen, setRetainedOpen] = useState(false);
  /** Harness groups the user folded or unfolded, overriding the default. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  /** Families added by this page's own discovery, marked "新" for this session. */
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  const [focusRequest, setFocusRequest] = useState<{ profileId: string; n: number } | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [guard, setGuard] = useState("");
  const history = useProfileHistory(api, snapshot.csrfToken, snapshot.tableRevision,
    { query, adapter: "", enabled: retainedOpen });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const sessionWritable = editor.sessionWritable;
  const families = modelFamilies(data.profiles);
  const needle = query.trim().toLowerCase();
  const visible = families.filter(g => (!enabledOnly || g.profiles.some(p => p.enabled))
    && (!needle || g.profiles.some(p => [p.label, p.model, p.provider, p.adapter, effortText(p.effort)].join(" ").toLowerCase().includes(needle))));
  const groups = harnessGroups(visible, data.profiles);
  // `unavailableProfileCount` is the table-wide count of every unavailable row,
  // including one the bounded snapshot still lists (the retained Router).
  // Subtract each unavailable row the local view already holds — listed plus
  // paged history, deduplicated by profileId — exactly once. A backend that
  // omits the field reports nothing rather than an invented number.
  const unavailableTotal = snapshot.unavailableProfileCount;
  const loadedUnavailable = data.profiles.filter(p => !p.available).length;
  const unlisted = typeof unavailableTotal === "number" && Number.isFinite(unavailableTotal)
    ? Math.max(0, unavailableTotal - loadedUnavailable)
    : 0;
  const routerId = data.configuration.decisionProfileId;
  const family = selected ? families.find(g => g.key === selected) : undefined;
  const shownGuard = editor.editing && mutationsAvailable ? "" : guard;
  // Loaded retained rows join the local view and, while editing, the draft. The
  // revision dependency also clears a page that belonged to the previous table
  // before it could seed a baseline, even when both renders see `null`.
  useEffect(() => { editor.adoptHistory(history.page); }, [history.page, snapshot.tableRevision]);
  useEffect(() => {
    if (focusRequest) document.getElementById(effortTagId(focusRequest.profileId))?.focus();
  }, [focusRequest]);
  function openRetained() {
    setRetainedOpen(true);
    history.open();
  }
  function showRouter(profileId: string) {
    const target = data.profiles.find(p => p.profileId === profileId);
    if (!target) return;
    setHistoryOpen(false);
    setSelected(familyKey(target));
    setFolded(previous => ({ ...previous, [target.adapter]: false }));
    setFocusRequest(previous => ({ profileId, n: (previous?.n ?? 0) + 1 }));
  }
  async function discover() {
    if (busy || !mutationsAvailable) {
      setGuard(!sessionWritable
        ? LOGIN_EXPIRED_ACTION_REFUSAL
        : !mutationsAvailable ? "连接中断或缺少写入资格：暂时不能发现模型，草稿仍保留。" : "");
      return;
    }
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setGuard(""); setNote("");
    try {
      await api.command("model_catalog_refresh", { requestId: crypto.randomUUID() }, snapshot.csrfToken);
      // The program publishes directory facts itself; this page only re-reads the
      // snapshot and rebases the local draft. It never uploads profiles or cards.
      const next = await refresh();
      if (next) {
        const added = next.profiles.filter(p => !snapshot.profiles.some(known => known.profileId === p.profileId));
        editor.rebase(next);
        setFresh(previous => new Set([...previous, ...added.map(familyKey)]));
        setNote(added.length
          ? `目录已更新：新增 ${added.length} 个配置（默认未启用）。`
          : "目录已更新：没有新的配置。");
      }
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  const list = <section className="panel list-panel" aria-label="模型家族">
    <div className="panel-toolbar"><h2>模型 <span className="muted">{visible.length}</span></h2>
      <div className="actions"><button className="button small-button" aria-pressed={historyOpen} onClick={() => setHistoryOpen(true)}>更新记录</button>
        <button className="button small-button" aria-disabled={busy || !mutationsAvailable}
          title={!sessionWritable ? LOGIN_EXPIRED_ACTION_REFUSAL : !mutationsAvailable ? "连接中断或缺少写入资格" : "由程序发布目录事实，不影响用户设置"}
          onClick={() => void discover()}>发现模型</button></div></div>
    <div className="list-filters">
      <label className="search"><span className="sr-only">搜索模型</span>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型、Harness、提供方" /></label>
      <label className="check-field"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)} />只看已启用</label>
      {(shownGuard || error) && <p className="banner guard-banner" role="status">{shownGuard || error}</p>}
      {note && <p className="success-message" role="status">{note}</p>}
    </div>
    <div className="list-scroll" tabIndex={0} aria-label="模型条目">
      {groups.length ? groups.map(group => {
        const isFolded = folded[group.adapter] ?? group.unavailable;
        const reason = group.unavailable ? harnessUnavailableText(group) : "";
        return <section key={group.adapter} className="harness-group" aria-label={group.name}>
          <button type="button" className="group-heading harness-heading" aria-expanded={!isFolded}
            onClick={() => setFolded(previous => ({ ...previous, [group.adapter]: !isFolded }))}>
            <span>{isFolded ? "▸" : "▾"} {group.name}{group.unavailable ? "（不可用）" : ""}</span>
            <span className="group-count">{group.families.length}</span>
          </button>
          {group.unavailable && <p className="small muted harness-reason">原因：{reason}</p>}
          {!isFolded && <ul className="profile-list">{group.families.map(g => {
            const enabled = g.profiles.filter(p => p.enabled).length;
            const hasRouter = g.profiles.some(p => p.profileId === routerId);
            const offline = !g.profiles.some(p => p.available);
            const isNew = fresh.has(g.key);
            const label = [g.name, `已启用 ${enabled}/${g.profiles.length}`, hasRouter ? "Router" : "",
              offline ? "不可用" : "", isNew ? "新" : ""].filter(Boolean).join("，");
            return <li key={g.key}>
              <button className={"profile-row family-row" + (family?.key === g.key ? " selected" : "")}
                aria-pressed={family?.key === g.key} aria-label={label}
                onClick={() => { setHistoryOpen(false); setSelected(g.key); }}>
                <span className="row-between"><strong>{g.name}</strong>
                  <span className="family-row-marks">
                    {isNew && <span className="family-new-mark">新</span>}
                    {hasRouter && <span className="router-mark" title="Router 所在家族">Router</span>}
                    <span className="small muted">{enabled}/{g.profiles.length}</span>
                  </span></span>
                {offline && <span className="small unavailable-mark">不可用</span>}
              </button>
            </li>;
          })}</ul>}
        </section>;
      }) : <Empty title={data.profiles.length ? "没有匹配的模型" : "尚未接入模型"}>
        {data.profiles.length ? "调整搜索或取消“只看已启用”。" : "点击“发现模型”读取本机目录。"}</Empty>}
      <div className="retained-footer">
        {!retainedOpen && unlisted > 0 && <p className="small muted">另有 {unlisted} 个不可用配置保留在历史中
          <button type="button" className="button small-button" onClick={openRetained}>查看历史配置</button></p>}
        {retainedOpen && history.loading && <p className="small muted" role="status">正在读取保留的配置历史…</p>}
        {retainedOpen && history.error && <p className="banner guard-banner" role="alert">{history.error}
          <button className="button small-button" onClick={history.reload}>重试读取</button></p>}
        {retainedOpen && history.hasMore && <button className="button small-button" disabled={history.loading} onClick={history.loadMore}>加载更多历史配置</button>}
        {retainedOpen && history.limitReached && <p className="small muted">已读取到显示上限（{MAX_HISTORY_PROFILES} 个配置）
          <Help label="历史配置说明">请用搜索在服务端查找更早的保留配置；搜索在分页前过滤。</Help></p>}
      </div>
    </div>
  </section>;
  return <div className="buddy-page">
    <RoutingStatusBar data={data} snapshot={snapshot} editor={editor} onShowRouter={showRouter} />
    <SplitView selected={historyOpen || !!family} list={list} detail={
      <aside className="panel detail-panel" aria-label="模型家族详情">
        <div className="model-detail-content" hidden={!historyOpen}>
          <EvaluationHistory snapshot={recorded} api={api} active={active && historyOpen} onBack={() => setHistoryOpen(false)} />
        </div>
        <div className="model-detail-content" hidden={historyOpen}>
          {family ? <FamilyDetail key={family.key} family={family} data={data} recorded={recorded} editor={editor}
            isNew={fresh.has(family.key)} onCloseList={() => setSelected(null)} />
            : <div className="detail-placeholder"><h2>选择模型家族</h2><p>从左侧选择一个模型家族，查看档位、偏好与评价。</p></div>}
        </div>
      </aside>
    } />
  </div>;
}
