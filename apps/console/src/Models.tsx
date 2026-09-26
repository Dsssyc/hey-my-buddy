import { useEffect, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { historyView } from "./draft";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { Empty } from "./ui";
import { effortText } from "./profile-display";
import { familyKey, modelFamilies, preferredVariant } from "./console-data";
import { MAX_HISTORY_PROFILES, useProfileHistory } from "./use-profile-history";
import { SplitView } from "./SplitView";
import { ModelDetail } from "./ModelDetail";
import { READ_ONLY_ACTION_REFUSAL, READ_ONLY_DRAFT_NOTE } from "./console-session";

export function Models({ snapshot, editor, api, refresh, active = true, mutationsAvailable = true }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  active?: boolean; mutationsAvailable?: boolean;
}) {
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [harness, setHarness] = useState(""), [enabledOnly, setEnabledOnly] = useState(false);
  const [showUnavailable, setShowUnavailable] = useState(false);
  const [section, setSection] = useState("overview");
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [guard, setGuard] = useState("");
  const history = useProfileHistory(api, snapshot.csrfToken, snapshot.tableRevision,
    { query, adapter: harness, enabled: showUnavailable });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const editing = editor.editing;
  // The editor's latch-aware flag, so a refusal before the next poll is not
  // described as a still-writable session.
  const sessionWritable = editor.sessionWritable;
  const families = modelFamilies(data.profiles);
  const visible = families.filter(g => (!harness || g.adapter === harness) && (!enabledOnly || g.profiles.some(p => p.enabled))
    && (showUnavailable || g.profiles.some(p => p.available))
    && g.profiles.some(p => [p.label, p.model, p.provider, p.adapter, effortText(p.effort)].join(" ").toLowerCase().includes(query.toLowerCase())));
  const shownProfiles = data.profiles.filter(p => showUnavailable || p.available);
  const hiddenCount = data.profiles.length - shownProfiles.length;
  // `unavailableProfileCount` is the table-wide count of every unavailable row,
  // including one the bounded snapshot still lists (the retained decision
  // selector). Subtract each unavailable row the local view already holds —
  // listed plus paged history, deduplicated by profileId — exactly once; rows
  // that are not loaded stay counted. A backend that omits the field reports
  // nothing rather than an invented number.
  const unavailableTotal = snapshot.unavailableProfileCount;
  const loadedUnavailable = data.profiles.filter(p => !p.available).length;
  const unlisted = typeof unavailableTotal === "number" && Number.isFinite(unavailableTotal)
    ? Math.max(0, unavailableTotal - loadedUnavailable)
    : 0;
  const profile = data.profiles.find(p => p.profileId === selected);
  const family = profile && families.find(g => g.key === familyKey(profile));
  // A refusal only stays on screen while it still describes the current state.
  const shownGuard = editing && mutationsAvailable ? "" : guard;
  // Loaded retained rows join the local view and, while editing, the draft. The
  // revision dependency also clears a page that belonged to the previous table
  // before it could seed a baseline, even when both renders see `null`.
  useEffect(() => { editor.adoptHistory(history.page); }, [history.page, snapshot.tableRevision]);
  function toggleUnavailable(next: boolean) {
    setShowUnavailable(next);
    if (next) history.open();
  }
  async function discover() {
    if (busy || !mutationsAvailable) {
      setGuard(!sessionWritable
        ? READ_ONLY_ACTION_REFUSAL
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
        const added = next.profiles.filter(p => !snapshot.profiles.some(known => known.profileId === p.profileId)).length;
        editor.rebase(next);
        setNote(added
          ? `目录已更新：新增 ${added} 个配置（默认未启用），用户设置和历史保留。`
          : "目录已更新：程序资料已刷新，用户设置和历史保留。");
      }
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }
  const list = <section className="panel list-panel" aria-label="模型目录">
    <div className="panel-toolbar"><h2>模型 <span className="muted">{visible.length}</span></h2>
      <div className="actions"><button className="button small-button" aria-pressed={historyOpen} onClick={() => setHistoryOpen(true)}>更新记录</button>
        <button className="button small-button" aria-disabled={busy || !mutationsAvailable}
          title={!sessionWritable ? READ_ONLY_ACTION_REFUSAL : !mutationsAvailable ? "连接中断或缺少写入资格" : "由程序发布目录事实，不影响用户设置"}
          onClick={() => void discover()}>发现模型</button></div></div>
    <div className="list-filters">
      <label className="search"><span className="sr-only">搜索模型配置</span>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型、Harness、提供方" /></label>
      <div className="filter-pair"><label><span className="sr-only">Harness 筛选</span>
        <select value={harness} onChange={e => setHarness(e.target.value)}><option value="">全部 Harness</option>
          {[...new Set(families.map(g => g.adapter))].map(name => <option key={name}>{name}</option>)}</select></label>
        <label className="check-field"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)} />仅已启用</label>
        <label className="check-field"><input type="checkbox" checked={showUnavailable} onChange={e => toggleUnavailable(e.target.checked)} />显示不可用配置</label></div>
      <p className="small muted">{visible.length} 个模型 · {shownProfiles.length} 个执行配置</p>
      {hiddenCount > 0 && <p className="small muted">已隐藏 {hiddenCount} 个不可用配置；勾选“显示不可用配置”可以查看保留的历史。</p>}
      {!showUnavailable && unlisted > 0 && <p className="small muted">目录中另有 {unlisted} 个不可用配置保留在历史记录中；勾选“显示不可用配置”可按页查看。</p>}
      {!showUnavailable && (query || harness) && unlisted > 0 && <p className="small muted">当前搜索只在已加载的配置中匹配，没有检索服务端历史；勾选“显示不可用配置”后可按关键词在服务端查找。</p>}
      {showUnavailable && (query || harness) && <p className="small muted">搜索与 Harness 筛选由服务端在保留历史中匹配（分页前过滤），可以命中本地显示上限之外的配置。</p>}
      {history.loading && <p className="small muted" role="status">正在读取保留的配置历史…</p>}
      {history.error && <p className="banner guard-banner" role="alert">{history.error}
        <button className="button small-button" onClick={history.reload}>重试读取</button></p>}
      {showUnavailable && history.hasMore && <button className="button small-button" disabled={history.loading} onClick={history.loadMore}>加载更多历史配置</button>}
      {showUnavailable && history.limitReached && <p className="small muted">已读取到控制台显示上限（{MAX_HISTORY_PROFILES} 个配置）；请用搜索或 Harness 筛选在服务端查找更早的保留配置。</p>}
      {!profile && (shownGuard || error) && <p className="banner guard-banner" role="status">{shownGuard || error}</p>}
      {!profile && note && <p className="success-message" role="status">{note}</p>}
    </div>
    <div className="list-scroll" tabIndex={0} aria-label="模型条目">
      {visible.length ? <ul className="profile-list">{visible.map((group, index) => <li key={group.key}>
        {(index === 0 || visible[index - 1].adapter !== group.adapter) && <h3 className="group-heading">{group.adapter}</h3>}
        <button className={"profile-row " + (family?.key === group.key ? "selected" : "")} aria-pressed={family?.key === group.key}
          onClick={() => { setHistoryOpen(false); if (family?.key !== group.key) setSelected(preferredVariant(group, data.preferences).profileId); }}>
          <span className="row-between"><strong>{group.name}</strong><span className="small muted">{group.profiles.filter(p => p.enabled).length}/{group.profiles.length} 启用</span></span>
          <span className="small muted truncate">{group.provider}</span>
          <span className="small effort-summary">{group.profiles.map(p => effortText(p.effort)).join(" · ")}
            {!group.profiles.some(p => p.available) && <span className="unavailable-mark"> · 不可用</span>}
            {group.profiles.some(p => data.preferences.some(x => x.profileId === p.profileId && ["pin", "prefer"].includes(x.mode))) && " · 有用户偏好"}</span>
        </button>
      </li>)}</ul> : <Empty title={data.profiles.length ? "没有匹配的模型" : "尚未接入模型"}>
        {hiddenCount > 0 ? `有 ${hiddenCount} 个不可用配置已隐藏；勾选“显示不可用配置”可以查看保留的历史。` : "点击“发现模型”读取本机目录。"}</Empty>}
    </div>
  </section>;
  return <SplitView selected={historyOpen || !!profile} list={list} detail={
    <ModelDetail data={data} recorded={recorded} editor={editor} api={api} active={active}
      historyOpen={historyOpen} section={section} onSection={setSection} profileId={selected} showUnavailable={showUnavailable}
      onSelect={setSelected} onCloseHistory={() => setHistoryOpen(false)} onCloseList={() => setSelected(null)}
      guard={shownGuard} error={error} note={note} />
  } />;
}
