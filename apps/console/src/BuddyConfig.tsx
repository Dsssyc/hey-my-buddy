import { useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { historyView } from "./draft";
import type { Editor } from "./use-editor";
import type { Snapshot } from "./types";
import { Empty, Icon } from "./ui";
import { familyKey, modelFamilies } from "./console-data";
import { MAX_HISTORY_PROFILES, PROFILE_PAGE_SIZE, useProfileHistory } from "./use-profile-history";
import { SplitView } from "./SplitView";
import { EvaluationHistory } from "./EvaluationHistory";
import { FamilyDetail, effortTagId } from "./FamilyDetail";
import { RoutingStatusBar } from "./RoutingStatusBar";
import { familySearchText, harnessGroups, harnessUnavailableText } from "./buddy-display";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "./console-session";
import { HarnessStatus } from "./HarnessStatus";
import { BuddyStatusBar } from "./BuddyStatusBar";
import { BUDDY_SECTIONS, buddyHash, buddyLocation } from "./buddy-navigation";
import type { BuddyLocation, BuddySection } from "./buddy-navigation";

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
  const [location, setLocation] = useState<BuddyLocation>(() => buddyLocation(window.location.hash) ?? { section: "models" });
  const [historyOpen, setHistoryOpen] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState(""), [enabledOnly, setEnabledOnly] = useState(false);
  /** The user asked to see unavailable configurations as well. */
  const [showUnavailable, setShowUnavailable] = useState(false);
  /** Harness groups the user folded or unfolded, overriding the default. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  /** Families added by this page's own discovery, marked "新" for this session. */
  const [fresh, setFresh] = useState<Set<string>>(() => new Set());
  const [focusRequest, setFocusRequest] = useState<{ profileId: string; n: number } | null>(null);
  const handledLocation = useRef<BuddyLocation | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [guard, setGuard] = useState("");
  const history = useProfileHistory(api, snapshot.csrfToken, snapshot.tableRevision,
    { query, adapter: "", enabled: showUnavailable });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const sessionWritable = editor.sessionWritable;
  const families = modelFamilies(data.profiles);
  const needle = query.trim().toLowerCase();
  // Two combinable filters. A family is listed when at least one configuration
  // survives both: 只看已启用 keeps families with an enabled effort among the
  // shown ones, and 显示不可用配置 unhides the unavailable efforts, so a family
  // with no available configuration only appears once that box is checked.
  const visible = families.filter(g => {
    const shown = showUnavailable ? g.profiles : g.profiles.filter(p => p.available);
    return shown.length > 0
      && (!enabledOnly || shown.some(p => p.enabled))
      && (!needle || g.profiles.some(p => familySearchText(p).includes(needle)));
  });
  const groups = harnessGroups(visible, data.profiles);
  // The checkbox counts every unavailable configuration: the table-wide count
  // the board records — including a row the bounded snapshot still lists, such
  // as a retained Router — never below what this view already holds. A backend
  // that omits the field still gets the honest local count instead of an
  // invented number.
  const loadedUnavailable = data.profiles.filter(p => !p.available).length;
  const unavailableCount = typeof snapshot.unavailableProfileCount === "number"
    && Number.isFinite(snapshot.unavailableProfileCount)
    ? Math.max(snapshot.unavailableProfileCount, loadedUnavailable)
    : loadedUnavailable;
  // A checked box asks for every unavailable configuration, so the bounded
  // pages continue on their own; the hook's display budget is the hard stop,
  // and this counter keeps a server that repeats a cursor from being asked
  // forever.
  const autoPages = useRef(0);
  // A new filter generation gets a fresh page budget: its first page is a new
  // read, not a continuation of the previous chain.
  useEffect(() => { autoPages.current = 0; }, [query, snapshot.tableRevision]);
  useEffect(() => {
    if (!showUnavailable) { autoPages.current = 0; return; }
    if (history.error || history.loading || !history.hasMore) return;
    if (autoPages.current >= Math.ceil(MAX_HISTORY_PROFILES / PROFILE_PAGE_SIZE)) return;
    autoPages.current += 1;
    history.loadMore();
  }, [showUnavailable, history.error, history.loading, history.hasMore, history.loadMore]);
  const routerIds = new Set([data.configuration.fastRouterProfileId, data.configuration.reviewRouterProfileId]);
  const family = selected ? families.find(g => g.key === selected) : undefined;
  // A refusal recorded by 发现模型 is stale once the page can act again: it is
  // shown only while the action would still be refused, never as a lingering
  // banner over usable controls.
  const shownGuard = editor.editing && mutationsAvailable ? "" : guard;
  // Loaded retained rows join the local view and, while editing, the draft. The
  // revision dependency also clears a page that belonged to the previous table
  // before it could seed a baseline, even when both renders see `null`.
  useEffect(() => { editor.adoptHistory(history.page); }, [history.page, snapshot.tableRevision]);
  useEffect(() => {
    const changed = () => {
      const next = buddyLocation(window.location.hash);
      if (next) setLocation(next);
    };
    window.addEventListener("hashchange", changed);
    return () => window.removeEventListener("hashchange", changed);
  }, []);
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === location) return;
    if (location.section === "models") {
      const target = data.profiles.find(p => p.profileId === location.target);
      if (!target) return;
      setHistoryOpen(false);
      setSelected(familyKey(target));
      setQuery(""); setEnabledOnly(false);
      if (!target.available) setShowUnavailable(true);
      setFolded(previous => ({ ...previous, [target.adapter]: false }));
      setFocusRequest(previous => ({ profileId: target.profileId, n: (previous?.n ?? 0) + 1 }));
    } else if (location.section === "router") {
      const target = location.target === "health" ? document.querySelector<HTMLElement>(".routing-status[aria-label='路由健康']")
        : document.getElementById(`router-${location.target}`);
      target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
    }
    handledLocation.current = location;
  }, [location, active, data.profiles]);
  useEffect(() => {
    if (active && location.section === "models" && focusRequest) {
      const target = document.getElementById(effortTagId(focusRequest.profileId));
      target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
    }
  }, [focusRequest, active, location.section]);
  function navigate(next: BuddyLocation) {
    setLocation(next);
    window.location.hash = buddyHash(next);
  }
  function showRouter(profileId: string) {
    navigate({ section: "models", target: profileId });
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
          title={!sessionWritable ? LOGIN_EXPIRED_ACTION_REFUSAL : !mutationsAvailable ? "连接中断或缺少写入资格" : undefined}
          onClick={() => void discover()}>发现模型</button></div></div>
    <div className="list-filters">
      <label className="search"><span className="sr-only">搜索模型</span>
        <input value={query} onChange={e => setQuery(e.target.value)} placeholder="搜索模型、Harness、提供方" /></label>
      <label className="check-field"><input type="checkbox" checked={enabledOnly} onChange={e => setEnabledOnly(e.target.checked)} />只看已启用</label>
      {unavailableCount > 0 && <label className="check-field"><input type="checkbox" checked={showUnavailable}
        onChange={e => {
          setShowUnavailable(e.target.checked);
          // Checking the box is the explicit request that starts the bounded
          // `model_profiles` read; unchecking only hides what is already loaded.
          if (e.target.checked) history.open();
        }} />显示不可用配置（{unavailableCount}）</label>}
      {showUnavailable && history.loading && <p className="small muted" role="status">正在读取不可用配置…</p>}
      {showUnavailable && history.error && <p className="banner guard-banner" role="alert">{history.error}
        <button type="button" className="button small-button" onClick={history.reload}>重试读取</button></p>}
      {showUnavailable && history.limitReached && <p className="small muted">已达显示上限（{MAX_HISTORY_PROFILES}）；请搜索更早配置。</p>}
      {(shownGuard || error) && <p className="banner guard-banner" role="status">{shownGuard || error}</p>}
      {note && <p className="success-message" role="status">{note}</p>}
    </div>
    <div className="list-scroll" tabIndex={0} aria-label="模型条目">
      {groups.length ? groups.map(group => {
        const groupProfiles = group.families.flatMap(g => g.profiles);
        const groupEnabled = groupProfiles.filter(p => p.enabled).length;
        const isFolded = folded[group.adapter] ?? groupEnabled === 0;
        const reason = group.unavailable ? harnessUnavailableText(group) : "";
        return <section key={group.adapter} className="harness-group" aria-label={group.name}>
          <button type="button" className="group-heading harness-heading" aria-expanded={!isFolded}
            onClick={() => setFolded(previous => ({ ...previous, [group.adapter]: !isFolded }))}>
            <span>{isFolded ? "▸" : "▾"} {group.name}{group.unavailable ? "（不可用）" : ""}</span>
            <span className="group-count">已启用 {groupEnabled}/{groupProfiles.length}</span>
          </button>
          {group.unavailable && <p className="small muted harness-reason">{reason}</p>}
          {!isFolded && <ul className="profile-list">{group.families.map(g => {
            const enabled = g.profiles.filter(p => p.enabled).length;
            const hasRouter = g.profiles.some(p => routerIds.has(p.profileId));
            // With 显示不可用配置 on, every shown family says how many of its
            // configurations are unavailable and which reasons were recorded.
            const unavailable = g.profiles.filter(p => !p.available);
            const reasons = [...new Set(unavailable.map(p => p.unavailableReason?.trim() ?? "").filter(Boolean))];
            const unavailableText = reasons.join("；");
            const unavailableShown = showUnavailable && unavailable.length > 0;
            const unavailableMark = unavailable.length === g.profiles.length
              ? "不可用" : `不可用 ${unavailable.length}/${g.profiles.length}`;
            const isNew = fresh.has(g.key) || g.profiles.some(profile => profile.newlyDiscovered);
            const label = [g.name, `已启用 ${enabled}/${g.profiles.length}`, hasRouter ? "Router" : "",
              unavailableShown ? (unavailableText ? `${unavailableMark}（${unavailableText}）` : unavailableMark) : "",
              isNew ? "新" : ""].filter(Boolean).join("，");
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
                {unavailableShown && <span className="small unavailable-mark" title={unavailableText || undefined}>
                  {unavailableMark}{unavailableText ? ` · ${unavailableText}` : ""}</span>}
              </button>
            </li>;
          })}</ul>}
        </section>;
      }) : <Empty title={data.profiles.length ? "没有匹配的模型" : "尚未接入模型"}>
        {data.profiles.length ? "试试其他搜索或筛选。" : "点击“发现模型”查找本机模型。"}</Empty>}
    </div>
  </section>;
  return <div className="buddy-page">
    <nav className="buddy-sections" aria-label="Buddy 配置分区">
      {(Object.keys(BUDDY_SECTIONS) as BuddySection[]).map(section => <a key={section} href={buddyHash({ section })}
        aria-current={location.section === section ? "page" : undefined}
        onClick={event => { event.preventDefault(); navigate({ section }); }}>
        <Icon name={section === "models" ? "models" : section === "router" ? "route" : "terminal"} size={16} />
        <span>{BUDDY_SECTIONS[section]}</span>
      </a>)}
    </nav>
    <div className="buddy-section-page">
      <BuddyStatusBar data={data} snapshot={snapshot} onNavigate={navigate} />
      <section className="buddy-section buddy-models" hidden={location.section !== "models"} aria-label="模型分区">
        <SplitView selected={historyOpen || !!family} list={list} detail={
          <aside className="panel detail-panel" aria-label="模型家族详情">
            <div className="model-detail-content" hidden={!historyOpen}>
              <EvaluationHistory snapshot={recorded} api={api} active={active && location.section === "models" && historyOpen} onBack={() => setHistoryOpen(false)} />
            </div>
            <div className="model-detail-content" hidden={historyOpen}>
              {family ? <FamilyDetail key={family.key} family={family} data={data} recorded={recorded} editor={editor}
                isNew={fresh.has(family.key)} onCloseList={() => setSelected(null)} />
                : <div className="detail-placeholder"><h2>选择模型家族</h2></div>}
            </div>
          </aside>
        } />
      </section>
      <section className="buddy-section" hidden={location.section !== "router"} aria-label="Router 分区">
        <RoutingStatusBar data={data} snapshot={snapshot} editor={editor} onShowRouter={showRouter} expanded />
      </section>
      <section className="buddy-section" hidden={location.section !== "harness"} aria-label="Harness 分区">
        <HarnessStatus snapshot={snapshot} api={api} refresh={refresh} location={location} active={active && location.section === "harness"}
          mutationsAvailable={mutationsAvailable} sessionWritable={sessionWritable} />
      </section>
    </div>
  </div>;
}
