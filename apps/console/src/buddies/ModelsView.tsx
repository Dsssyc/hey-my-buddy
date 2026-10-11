import { cn } from "cn";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import type { ConsoleApi } from "../api";
import { errorText, snapshotHarnesses } from "../api";
import { familyKey } from "../console-data";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import { historyView } from "../draft";
import type { Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { MAX_HISTORY_PROFILES, PROFILE_PAGE_SIZE, useProfileHistory } from "../use-profile-history";
import { useViewPreference } from "../view-preferences";
import type { BuddyAttention } from "./attention";
import { CatalogPick } from "./BuddyHeader";
import { ModelPane, buddyRowId } from "./ModelPane";
import { ModelsTable } from "./ModelsTable";
import { harnessBlocks, harnessStateText, listedModels } from "./models";
import type { BuddyLocation } from "./navigation";

/**
 * The models table of the catalogue with its side column. The toolbar searches
 * and filters the rows; a row is a model, selecting it opens its settings beside
 * the table, and every change goes into the page's draft. Nothing here
 * recommends a buddy.
 */
export function ModelsView({ snapshot, editor, api, refresh, active, mutationsAvailable, location, attention, onNavigate, hidden }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  /** The page is in front and this is the view it shows. */
  active: boolean; mutationsAvailable: boolean;
  location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  const [selected, setSelected] = useState<string | null>(null);
  /** In a window too narrow for both, the side column takes the table's place once a row is opened. */
  const [opened, setOpened] = useState(false);
  const [query, setQuery] = useState("");
  const [enabledOnly, setEnabledOnly, setEnabledOnlyForView] = useViewPreference("models.enabledOnly");
  const [showUnavailable, setShowUnavailable, setShowUnavailableForView] = useViewPreference("models.showUnavailable");
  /** Harnesses the user folded or unfolded, overriding the default. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  /** Models this page's own discovery added, marked as new for this session. */
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const [focusRequest, setFocusRequest] = useState<{ profileId: string; n: number } | null>(null);
  const handledLocation = useRef<BuddyLocation | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [note, setNote] = useState("");
  const [refused, setRefused] = useState(false);
  const history = useProfileHistory(api, snapshot.csrfToken, snapshot.tableRevision,
    { query, adapter: "", enabled: active && showUnavailable, autoOpen: showUnavailable });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const listed = listedModels(data.profiles, { query, enabledOnly, showUnavailable });
  const blocks = harnessBlocks({ listed, data, recorded, harnesses: snapshotHarnesses(snapshot), folded, fresh });
  // The side column always shows a row that is on screen: the chosen one, else the first.
  const rows = blocks.filter(block => !block.folded).flatMap(block => block.models);
  const current = rows.find(row => row.key === selected) ?? rows[0] ?? null;
  const currentBlock = blocks.find(block => current && block.models.includes(current));
  const buddies = blocks.flatMap(block => block.models).flatMap(model => model.chips);
  // The count on the filter is the table-wide number the board records, never
  // below what this view already holds; without the field it is the local count.
  const loadedUnavailable = data.profiles.filter(profile => !profile.available).length;
  const unavailableCount = typeof snapshot.unavailableProfileCount === "number" && Number.isFinite(snapshot.unavailableProfileCount)
    ? Math.max(snapshot.unavailableProfileCount, loadedUnavailable) : loadedUnavailable;
  // A checked filter asks for every unavailable buddy, so the bounded pages
  // continue on their own; the hook's display budget is the hard stop, and this
  // counter keeps a server that repeats a cursor from being asked forever.
  const autoPages = useRef(0);
  useEffect(() => { autoPages.current = 0; }, [query, snapshot.tableRevision]);
  useEffect(() => {
    if (!showUnavailable) { autoPages.current = 0; return; }
    if (!active || history.error || history.loading || !history.hasMore) return;
    if (autoPages.current >= Math.ceil(MAX_HISTORY_PROFILES / PROFILE_PAGE_SIZE)) return;
    autoPages.current += 1;
    history.loadMore();
  }, [active, showUnavailable, history.error, history.loading, history.hasMore, history.loadMore]);
  // Retained rows that were read join the view and, while editing, the draft. The
  // revision also clears a page of the previous table before it could seed a baseline.
  useEffect(() => { editor.adoptHistory(history.page); }, [history.page, snapshot.tableRevision]);
  // An address that names a buddy shows it whatever the search and the filters say;
  // only a change of a checkbox by hand is remembered.
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === location) return;
    const target = data.profiles.find(profile => profile.profileId === location.target);
    if (!target) return;
    setSelected(familyKey(target)); setOpened(true);
    setQuery(""); setEnabledOnlyForView(false);
    if (!target.available) setShowUnavailableForView(true);
    setFolded(previous => ({ ...previous, [target.adapter]: false }));
    setFocusRequest(previous => ({ profileId: target.profileId, n: (previous?.n ?? 0) + 1 }));
    handledLocation.current = location;
  }, [location, active, data.profiles]);
  useEffect(() => {
    if (!active || !focusRequest) return;
    const target = document.getElementById(buddyRowId(focusRequest.profileId));
    target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
  }, [focusRequest, active]);

  const refusal = !editor.sessionWritable ? LOGIN_EXPIRED_ACTION_REFUSAL : !mutationsAvailable ? t("models.refused") : "";
  async function discover() {
    if (busy) return;
    // A refusal is said only while the action would still be refused.
    if (refusal) { setRefused(true); return; }
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setRefused(false); setNote("");
    try {
      await api.command("model_catalog_refresh", { requestId: crypto.randomUUID() }, snapshot.csrfToken);
      // The program publishes the catalogue itself; this page only reads the
      // snapshot again and moves its draft onto it. It never uploads a buddy.
      const next = await refresh();
      if (next) {
        const added = next.profiles.filter(profile => !snapshot.profiles.some(known => known.profileId === profile.profileId));
        editor.rebase(next);
        setFresh(previous => new Set([...previous, ...added.map(familyKey)]));
        setNote(added.length ? t("models.discovered", { n: added.length }) : t("models.discoveredNone"));
      }
    } catch (failure) { setError(errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }

  const reading = showUnavailable && history.loading;
  const readFailure = showUnavailable ? history.error : "";
  const guard = refused ? refusal : "";
  return <section hidden={hidden} aria-label={t("catalog.models")}
    className={cn("grid min-h-0 flex-1 grid-cols-1 bg-card", current && "xl:grid-cols-[minmax(0,1fr)_352px]")}>
    <div className={cn("flex min-h-0 min-w-0 flex-col", opened && current && "max-xl:hidden")}>
      <div data-slot="models-toolbar" className="flex shrink-0 flex-wrap items-center gap-x-[22px] gap-y-2 border-b px-3 py-3 text-ink2 sm:px-6">
        <CatalogPick location={location} attention={attention} onNavigate={onNavigate} />
        <Input type="search" className="h-8 w-[220px] bg-card text-foreground shadow-none" aria-label={t("models.search")} placeholder={t("models.search")}
          value={query} onChange={event => setQuery(event.target.value)} />
        <label className="flex items-center gap-2 whitespace-nowrap">
          <Checkbox checked={enabledOnly} onCheckedChange={checked => setEnabledOnly(checked === true)} />{t("models.enabledOnly")}
        </label>
        {unavailableCount > 0 && <label className="flex items-center gap-2 whitespace-nowrap">
          <Checkbox checked={showUnavailable} aria-label={`${t("models.showUnavailable")} ${unavailableCount}`}
            onCheckedChange={checked => setShowUnavailable(checked === true)} />
          {t("models.showUnavailable")}<span className="text-ink3 tabular-nums">{unavailableCount}</span>
        </label>}
        <div className="ml-auto flex items-center gap-[22px]">
          {/* A live region: what the filters leave is read out when it changes. */}
          <span className="whitespace-nowrap tabular-nums" role="status">{t("models.enabled")} {buddies.filter(chip => chip.profile.enabled).length} / {buddies.length}</span>
          <Hint text={refusal || undefined}>
            <Button variant="outline" aria-disabled={busy || !!refusal || undefined} onClick={() => void discover()}>{t("models.discover")}</Button>
          </Hint>
        </div>
      </div>
      {(reading || readFailure || (showUnavailable && history.limitReached) || guard || error || note) && <div className="flex shrink-0 flex-wrap items-center gap-x-5 gap-y-1 border-b px-3 py-2 text-xs sm:px-6">
        {reading && <p className="text-ink3" role="status">{t("models.reading")}</p>}
        {readFailure && <p className="flex items-center gap-2.5 text-fail" role="alert">{readFailure}
          <Button variant="outline" size="xs" onClick={history.reload}>{t("models.retry")}</Button></p>}
        {showUnavailable && history.limitReached && <p className="text-ink3">{t("models.limit", { n: MAX_HISTORY_PROFILES })}</p>}
        {guard && <p className="text-ink2" role="status">{guard}</p>}
        {error && <p className="text-fail" role="alert">{error}</p>}
        {note && <p className="text-ink2" role="status">{note}</p>}
      </div>}
      <ScrollArea className="min-h-0 flex-1">
        <ModelsTable blocks={blocks} current={current?.key ?? null}
          onSelect={key => { setSelected(key); setOpened(true); }}
          onFold={(adapter, next) => setFolded(previous => ({ ...previous, [adapter]: next }))} />
        {!blocks.length && <div className="px-6 py-16 text-center">
          <p className="font-medium">{t(data.profiles.length ? "models.empty.filtered" : "models.empty.none")}</p>
          <p className="mt-1.5 text-xs text-ink2">{t(data.profiles.length ? "models.empty.filteredHint" : "models.empty.noneHint")}</p>
        </div>}
        <ScrollBar orientation="horizontal" />
      </ScrollArea>
    </div>
    {/* The side column is drawn only while this view is in front, so that a list it opened cannot stay over another page. */}
    {current && active && <ScrollArea className={cn("min-h-0 xl:border-l", !opened && "max-xl:hidden")}>
      <ModelPane key={current.key} row={current} data={data}
        harnessState={currentBlock?.state ? `${currentBlock.name} ${harnessStateText(currentBlock.state, t)}` : undefined} recorded={recorded} editor={editor} onBack={() => setOpened(false)} />
    </ScrollArea>}
  </section>;
}
