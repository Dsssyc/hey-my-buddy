import i18n from "i18next";
import { useCopyState } from "../i18n/use-copy-state";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import type { ConsoleApi } from "../api";
import { errorText } from "../api";
import { harnessName } from "../buddy-display";
import { modelServiceKey, modelServices } from "../console-data";
import { historyView } from "../draft";
import type { Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { MAX_HISTORY_BUDDIES, BUDDY_PAGE_SIZE, historySourceKey, useBuddyHistory } from "../use-buddy-history";
import { useViewPreference } from "../view-preferences";
import { HarnessHeader } from "./HarnessHeader";
import { ModelDrawer, buddyRowId } from "./ModelDrawer";
import { ModelLine } from "./ModelLine";
import { harnessBlocks, harnessStateText, listedModels } from "./models";
import type { ModelRow } from "./models";
import { buddyHash } from "./navigation";
import type { BuddyLocation } from "./navigation";
import type { useHarnessActions } from "./use-harness-actions";

const liveT = i18n.getFixedT(null, "buddies");

/**
 * One directory, grouped by harness, with a harness sidebar and one model drawer.
 */
export function CatalogView({ snapshot, editor, api, refresh, active, mutationsAvailable, location, onNavigate, hidden, actions }: {
  snapshot: Snapshot; editor: Editor; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  /** The page is in front and this is the view it shows. */
  active: boolean; mutationsAvailable: boolean;
  location: BuddyLocation; onNavigate: (next: BuddyLocation) => void; hidden: boolean; actions: ReturnType<typeof useHarnessActions>;
}) {
  const { t } = useTranslation("buddies");
  const [selected, setSelected] = useState<string | null>(null);
  /** The buddy chosen on each model's list; a model keeps its choice while its drawer closes. */
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const [query, setQuery] = useState("");
  const [enabledOnly, setEnabledOnly, setEnabledOnlyForView] = useViewPreference("models.enabledOnly");
  const [showUnavailable, setShowUnavailable, setShowUnavailableForView] = useViewPreference("models.showUnavailable");
  /** Harnesses the user folded or unfolded, overriding the default. */
  const [folded, setFolded] = useState<Record<string, boolean>>({});
  /** Models this page's own discovery added, marked as new for this session. */
  const [fresh, setFresh] = useState<ReadonlySet<string>>(() => new Set());
  const [focusRequest, setFocusRequest] = useState<{ id: string; n: number } | null>(null);
  const handledLocation = useRef<string | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useCopyState(), [note, setNote] = useCopyState();
  const [refused, setRefused] = useState(false);
  const sourceKey = historySourceKey(snapshot);
  const history = useBuddyHistory(api, snapshot.csrfToken, sourceKey,
    { query, harness: "", enabled: active && showUnavailable, autoOpen: showUnavailable });
  const data = editor.view;
  const recorded = historyView(snapshot, history.page);
  const listed = listedModels(data.buddies, { query, enabledOnly, showUnavailable });
  const listedKeys = new Set(listed.map(modelService => modelService.key));
  const blocks = harnessBlocks({ listed: modelServices(data.buddies), data, recorded, harnesses: actions.rows, folded, fresh })
    .map(block => ({ ...block, modelCount: block.models.length, models: block.models.filter(row => listedKeys.has(row.key)) }));
  const healthBlocks = actions.rows.filter(health => !data.buddies.some(buddy => buddy.harness === health.harness))
    .map(health => ({ harness: health.harness, name: harnessName(health.harness), state: health.status === "ready" ? null : health.status,
      reasons: [], folded: folded[health.harness] ?? true, models: [], modelCount: 0, providers: Object.keys(health.billingByProvider ?? {}), enabled: 0, total: 0 }));
  const sections = [...blocks, ...healthBlocks].map(block => {
    const health = actions.rows.find(row => row.harness === block.harness);
    const state = health && health.status !== "ready" ? health.status : block.state;
    return { ...block, state };
  }).sort((a, b) => Number(!!a.state) - Number(!!b.state));
  // The count on the filter is the table-wide number the board records, never
  // below what this view already holds; without the field it is the local count.
  const loadedUnavailable = data.buddies.filter(buddy => !buddy.available).length;
  const unavailableCount = typeof snapshot.unavailableBuddyCount === "number" && Number.isFinite(snapshot.unavailableBuddyCount)
    ? Math.max(snapshot.unavailableBuddyCount, loadedUnavailable) : loadedUnavailable;
  // A checked filter asks for every unavailable buddy, so the bounded pages
  // continue on their own; the hook's display budget is the hard stop, and this
  // counter keeps a server that repeats a cursor from being asked forever.
  const autoPages = useRef(0);
  useEffect(() => { autoPages.current = 0; }, [query, sourceKey]);
  useEffect(() => {
    if (!showUnavailable) { autoPages.current = 0; return; }
    if (!active || history.error || history.loading || !history.hasMore) return;
    if (autoPages.current >= Math.ceil(MAX_HISTORY_BUDDIES / BUDDY_PAGE_SIZE)) return;
    autoPages.current += 1;
    history.loadMore();
  }, [active, showUnavailable, history.error, history.loading, history.hasMore, history.loadMore]);
  // Retained rows that were read join the view and, while editing, the draft. The
  // Content changes clear an older page before it could seed a baseline.
  useEffect(() => { editor.adoptHistory(history.page, sourceKey); }, [history.page, sourceKey]);
  // Deep selections cut through local filters without changing the saved preferences.
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === buddyHash(location)) return;
    {
      const target = data.buddies.find(buddy => location.targetKind === "model" ? modelServiceKey(buddy) === location.target : buddy.buddyId === location.target);
      if (!target) return;
      const modelService = modelServices(data.buddies).find(row => row.key === modelServiceKey(target))!;
      const first = modelService.buddies.find(buddy => buddy.enabled) ?? modelService.buddies[0];
      const buddyId = location.targetKind === "model" ? first.buddyId : target.buddyId;
      setSelected(modelServiceKey(target)); setChosen(previous => ({ ...previous, [modelServiceKey(target)]: buddyId }));
      setQuery(""); setEnabledOnlyForView(false);
      if (!target.available) setShowUnavailableForView(true);
      setFolded(previous => ({ ...previous, [target.harness]: false }));
      setFocusRequest(previous => ({ id: buddyRowId(buddyId), n: (previous?.n ?? 0) + 1 }));
    }
    handledLocation.current = buddyHash(location);
  }, [location, active, data.buddies, actions.rows]);
  useEffect(() => {
    if (!active || !focusRequest) return;
    const target = document.getElementById(focusRequest.id);
    target?.focus({ preventScroll: true }); target?.scrollIntoView?.({ block: "nearest" });
  }, [focusRequest, active]);

  function selectModel(row: ModelRow) {
    const opening = selected !== row.key;
    setSelected(opening ? row.key : null);
    if (opening) {
      // Opening a model pins its harness open: switching its last buddy off must not fold it away.
      setFolded(previous => ({ ...previous, [row.modelService.harness]: false }));
      const first = row.chips.find(chip => chip.buddy.enabled) ?? row.chips[0];
      setChosen(previous => ({ ...previous, [row.key]: first.buddy.buddyId }));
    }
    const next: BuddyLocation = opening ? { view: "models", targetKind: "model", target: row.key } : { view: "models" };
    handledLocation.current = buddyHash(next);
    onNavigate(next);
  }
  function selectBuddy(model: string, buddyId: string) {
    setChosen(previous => ({ ...previous, [model]: buddyId }));
    const next: BuddyLocation = { view: "models", targetKind: "buddy", target: buddyId };
    handledLocation.current = buddyHash(next);
    onNavigate(next);
  }
  const refusal = !editor.sessionWritable ? t("loginExpired") : !mutationsAvailable ? t("models.refused") : "";
  async function discover() {
    if (busy) return;
    // A refusal is said only while the action would still be refused.
    if (refusal) { setRefused(true); return; }
    setBusy(true); editor.setAuxiliaryBusy(true); setError(""); setRefused(false); setNote("");
    try {
      await api.command("model_catalog_refresh", {}, snapshot.csrfToken);
      // The program publishes the catalogue itself; this page only reads the
      // snapshot again and moves its draft onto it. It never uploads a buddy.
      const next = await refresh();
      if (next) {
        const added = next.buddies.filter(buddy => !snapshot.buddies.some(known => known.buddyId === buddy.buddyId));
        editor.rebase(next);
        setFresh(previous => new Set([...previous, ...added.map(modelServiceKey)]));
        setNote(() => added.length ? liveT("models.discovered", { n: added.length }) : liveT("models.discoveredNone"));
      }
    } catch (failure) { setError(() => errorText(failure)); }
    finally { setBusy(false); editor.setAuxiliaryBusy(false); }
  }

  const reading = showUnavailable && history.loading;
  const readFailure = showUnavailable ? history.error : "";
  const guard = refused ? refusal : "";
  return <section hidden={hidden} aria-label={t("sections.models")}
    className="view-presence flex min-h-0 flex-1 flex-col bg-background">
      <div data-slot="catalogue-toolbar" className="min-h-12 shrink-0 border-b bg-card px-6 py-2 text-ink2">
        <div data-slot="catalogue-controls" className="mx-auto flex max-w-[1040px] min-w-0 flex-wrap items-center gap-x-6 gap-y-2">
          <Input type="search" className="h-8 w-full max-w-full bg-card text-sm text-foreground shadow-none sm:w-[200px]" aria-label={t("models.search")} placeholder={t("models.search")}
            value={query} onChange={event => setQuery(event.target.value)} />
          <div data-slot="catalogue-group" className="flex min-w-0 flex-1 flex-wrap items-center gap-x-6 gap-y-2">
            <div data-slot="catalogue-filters" className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2 text-xs sm:gap-x-5 sm:text-sm">
              <label className="flex items-center gap-2 whitespace-nowrap">
                <Checkbox checked={enabledOnly} onCheckedChange={checked => setEnabledOnly(checked === true)} />{t("models.enabledOnly")}
              </label>
              {unavailableCount > 0 && <label className="flex items-center gap-2 whitespace-nowrap">
                <Checkbox checked={showUnavailable} aria-label={`${t("models.showUnavailable")} ${unavailableCount}`}
                  onCheckedChange={checked => setShowUnavailable(checked === true)} />
                {t("models.showUnavailable")}<span className="text-ink3 tabular-nums">{unavailableCount}</span>
              </label>}
            </div>
            <div data-slot="catalogue-actions" className="ml-auto flex items-center gap-2">
              <Hint text={refusal || (busy ? t("models.discoverBusy") : undefined)}>
                <Button variant="outline" className="h-7 px-2 text-xs sm:h-8 sm:px-3 sm:text-sm" aria-disabled={busy || !!refusal || undefined} onClick={() => void discover()}>{t("models.discover")}</Button>
              </Hint>
            </div>
          </div>
        </div>
      </div>
      {(reading || readFailure || (showUnavailable && history.limitReached) || guard || error || note) && <div className="shrink-0 border-b px-3 py-2 text-xs sm:px-6"><div className="mx-auto flex max-w-[1040px] flex-wrap items-center gap-x-5 gap-y-1">
        {reading && <p className="text-ink3" role="status">{t("models.reading")}</p>}
        {readFailure && <p className="flex items-center gap-2.5 text-fail" role="alert">{readFailure}
          <Button variant="outline" size="sm" onClick={history.reload}>{t("models.retry")}</Button></p>}
        {showUnavailable && history.limitReached && <p className="text-ink3">{t("models.limit", { n: MAX_HISTORY_BUDDIES })}</p>}
        {guard && <p className="text-ink2" role="status">{guard}</p>}
        {error && <p className="text-fail" role="alert">{error}</p>}
        {note && <p className="text-ink2" role="status">{note}</p>}
      </div></div>}
      {active && actions.scope === "models" && (actions.guard || actions.error || actions.note) && <div className="shrink-0 border-b bg-card px-3 py-2 text-xs sm:px-6"><div data-slot="harness-feedback" className="mx-auto flex max-w-[1040px] flex-wrap items-center gap-x-5 gap-y-1">
        {actions.guard && <p role="status">{actions.guard}</p>}
        {actions.error && <p className="flex flex-wrap items-center gap-2 text-fail" role="alert">{actions.error}{actions.retry && <Button variant="outline" size="sm" onClick={() => void actions.run(actions.retry!)}>{t("harness.retry")}</Button>}</p>}
        {actions.note && <p role="status">{actions.note}</p>}
      </div></div>}
      <ScrollArea data-slot="catalog-models" className="min-h-0 min-w-0 flex-1" viewportProps={{ className: "[&>div]:block!" }}>
        <div className="mx-auto max-w-[1040px] space-y-3 p-3 sm:p-6">
          {sections.map(block => {
            const health = actions.rows.find(row => row.harness === block.harness);
            return <Collapsible asChild key={block.harness} open={!block.folded}
              onOpenChange={open => setFolded(previous => ({ ...previous, [block.harness]: !open }))}>
              <section aria-label={block.name} data-slot="harness-section" className="overflow-clip rounded-(--radius-card) border bg-card">
                <HarnessHeader block={block} health={health} state={block.state}
                  gate={{ canWrite: !actions.refusal, refusal: actions.refusal, busy: actions.busy }}
                  onRetry={provider => void actions.run({ kind: "retry", harness: block.harness, provider })} onNavigate={onNavigate} />
                <CollapsibleContent>
                  {block.models.map(row => <Collapsible key={row.key} open={selected === row.key}>
                    <ModelLine showProvider={block.providers.length > 1} row={row} harnessState={block.state} recorded={recorded} data={data} expanded={selected === row.key} onSelect={() => selectModel(row)} />
                    <CollapsibleContent>
                      {active && <ModelDrawer row={row} data={data} recorded={recorded} editor={editor} onNavigate={onNavigate}
                        harnessState={block.state ? `${block.name} ${harnessStateText(block.state, t)}` : undefined}
                        selectedBuddyId={chosen[row.key]!} onSelectBuddy={buddyId => selectBuddy(row.key, buddyId)} />}
                    </CollapsibleContent>
                  </Collapsible>)}
                </CollapsibleContent>
              </section>
            </Collapsible>;
          })}
          {!listed.length && <div className="px-6 py-12 text-center">
            <p className="font-medium">{t(data.buddies.length ? "models.empty.filtered" : "models.empty.none")}</p>
            <p className="mt-1.5 text-xs text-ink2">{t(data.buddies.length ? "models.empty.filteredHint" : "models.empty.noneHint")}</p>
          </div>}
        </div>
      </ScrollArea>
  </section>;
}
