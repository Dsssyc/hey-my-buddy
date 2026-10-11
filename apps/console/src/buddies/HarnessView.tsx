import { cn } from "cn";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area";
import type { ConsoleApi } from "../api";
import { ApiError, errorText, snapshotHarnesses } from "../api";
import { harnessName } from "../buddy-display";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import type { HarnessHealth, Snapshot } from "../types";
import type { BuddyAttention } from "./attention";
import { CatalogPick } from "./BuddyHeader";
import { harnessPath, harnessTableRow, lastChecked, statusText } from "./harness";
import { HarnessPane } from "./HarnessPane";
import { openQuotaRetry } from "./harness-retry";
import { HarnessTable, harnessRowId } from "./HarnessTable";
import type { BuddyLocation } from "./navigation";

/** One explicit action; kept so that a failed attempt can be retried as it was. */
type Action =
  | { kind: "check"; adapter?: string }
  | { kind: "set"; adapter: string; path: string | null }
  | { kind: "retry"; adapter: string; provider: string };

/**
 * The harness table of the catalogue with its side column. It reads the recorded
 * health of every supported harness from the snapshot; entering the view calls
 * nothing and makes no draft. Every action is an explicit click: checking one
 * harness or all of them through the existing `capabilities` operation (no model
 * call), setting a path, and opening a quota retry. Each reloads the snapshot,
 * which stays the recorded truth.
 */
export function HarnessView({ snapshot, api, refresh, active, mutationsAvailable, sessionWritable, location, attention, onNavigate, hidden }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  /** The page is in front and this is the view it shows. */
  active: boolean; mutationsAvailable: boolean; sessionWritable: boolean;
  location: BuddyLocation; attention: BuddyAttention; onNavigate: (next: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  const recorded = useMemo(() => snapshotHarnesses(snapshot), [snapshot]);
  /** Rows returned by this view's own actions, shown until a snapshot reload lands. */
  const [replies, setReplies] = useState<Record<string, HarnessHealth>>({});
  const [selected, setSelected] = useState<string | null>(null);
  /** In a window too narrow for both, the side column takes the table's place once a row is opened. */
  const [opened, setOpened] = useState(false);
  const [focusRequest, setFocusRequest] = useState<{ adapter: string; n: number } | null>(null);
  const handledLocation = useRef<BuddyLocation | null>(null);
  /** Whether an action is under way; only one is at a time. */
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(""), [note, setNote] = useState("");
  const [retry, setRetry] = useState<Action | null>(null);
  const [refused, setRefused] = useState(false);

  const rows = recorded.map(row => replies[row.adapter] ?? row);
  // The side column always shows a row that is on screen: the chosen one, else the first.
  const current = rows.find(row => row.adapter === selected) ?? rows[0] ?? null;
  // An address that names a harness selects its row and puts the focus on it.
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === location) return;
    const target = recorded.find(row => row.adapter === location.target);
    if (!target) return;
    setSelected(target.adapter); setOpened(true);
    setFocusRequest(previous => ({ adapter: target.adapter, n: (previous?.n ?? 0) + 1 }));
    handledLocation.current = location;
  }, [location, active, recorded]);
  useEffect(() => {
    if (!active || !focusRequest) return;
    const target = document.getElementById(harnessRowId(focusRequest.adapter));
    target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
  }, [focusRequest, active]);

  const refusal = !sessionWritable ? LOGIN_EXPIRED_ACTION_REFUSAL : !mutationsAvailable ? t("harness.refused") : "";
  const expectedRevision = (adapter: string) => rows.find(row => row.adapter === adapter)?.revision ?? 0;
  /** The reload is the point of the action: the snapshot stays the recorded truth. */
  async function reloadSnapshot() {
    const next = await refresh();
    if (next) setReplies({});
    return next;
  }
  async function run(action: Action) {
    if (busy) return;
    // A refusal is said only while the action would still be refused.
    if (refusal) { setRefused(true); return; }
    setBusy(true); setError(""); setNote(""); setRetry(null); setRefused(false);
    try {
      if (action.kind === "check") {
        const checked = await api.harnessRefresh(snapshot.csrfToken, action.adapter);
        setReplies(previous => ({ ...previous, ...Object.fromEntries(checked.map(row => [row.adapter, row])) }));
        setNote(action.adapter
          ? t("harness.done.checked", { name: harnessName(action.adapter), status: statusText(checked.find(row => row.adapter === action.adapter)?.status ?? "unknown", t) })
          : t("harness.done.checkedAll", { ready: checked.filter(row => row.status === "ready").length, n: checked.length }));
      } else if (action.kind === "set") {
        const updated = await api.harnessSet(action.adapter, action.path, expectedRevision(action.adapter), snapshot.csrfToken);
        setReplies(previous => ({ ...previous, [updated.adapter]: updated }));
        const status = { name: harnessName(updated.adapter), status: statusText(updated.status, t) };
        setNote(action.path ? t("harness.done.saved", { path: harnessPath(updated) ?? action.path, ...status }) : t("harness.done.restored", status));
      } else {
        const outcome = await openQuotaRetry(api, snapshot.csrfToken, action.adapter, action.provider, t);
        setNote(t(`harness.quota.done.${outcome}`, { provider: action.provider }));
      }
      await reloadSnapshot();
    } catch (failure) {
      setError(errorText(failure));
      setRetry(action);
      // A revision conflict means the recorded facts changed: reread them so a
      // retry fences against the new revision instead of replaying a stale one.
      if (failure instanceof ApiError && ["REVISION_CONFLICT", "CONFLICT", "STALE_GENERATION"].includes(failure.code)) {
        await reloadSnapshot();
      }
    } finally {
      setBusy(false);
    }
  }

  const guard = refused ? refusal : "";
  const checked = lastChecked(rows);
  // In a window too narrow for both, an opened row's side column takes the place of the toolbar and the table.
  const replaced = opened && !!current && "max-xl:hidden";
  // Three rows: the toolbar, what the last action said, and the table. The line in the middle stays when the
  // side column replaces the other two, so an action started there still says how it ended.
  return <section hidden={hidden} aria-label={t("catalog.harness")} aria-busy={busy}
    className={cn("grid min-h-0 flex-1 grid-cols-1 grid-rows-[auto_auto_minmax(0,1fr)] bg-card", current && "xl:grid-cols-[minmax(0,1fr)_352px]")}>
    <div data-slot="harness-toolbar" className={cn("col-start-1 row-start-1 flex min-w-0 flex-wrap items-center gap-x-[22px] gap-y-2 border-b px-3 py-3 text-ink2 sm:px-6", replaced)}>
      <CatalogPick location={location} attention={attention} onNavigate={onNavigate} />
      <div className="ml-auto flex items-center gap-[22px]">
        <span className="whitespace-nowrap"><span className="mr-1.5 text-ink3">{t("harness.lastChecked")}</span><span className="tabular-nums">{checked ?? "—"}</span></span>
        <Hint text={refusal || undefined}>
          <Button variant="outline" aria-disabled={busy || !!refusal || undefined} onClick={() => void run({ kind: "check" })}>{t("harness.checkAll")}</Button>
        </Hint>
      </div>
    </div>
    {(guard || error || note) && <div data-slot="harness-feedback" className="col-start-1 row-start-2 flex min-w-0 flex-wrap items-center gap-x-5 gap-y-1 border-b px-3 py-2 text-xs sm:px-6">
      {guard && <p className="text-ink2" role="status">{guard}</p>}
      {error && <p className="flex items-center gap-2.5 text-fail" role="alert">{error}
        {retry && <Button variant="outline" size="xs" onClick={() => void run(retry)}>{t("harness.retry")}</Button>}</p>}
      {note && <p className="text-ink2" role="status">{note}</p>}
    </div>}
    <ScrollArea className={cn("col-start-1 row-start-3 min-h-0 min-w-0", replaced)}>
      {rows.length > 0 && <HarnessTable rows={rows.map(row => harnessTableRow(row, t))} current={current?.adapter ?? null}
        onSelect={adapter => { setSelected(adapter); setOpened(true); }} />}
      {!rows.length && <div className="px-6 py-16 text-center">
        <p className="font-medium">{t("harness.empty.title")}</p>
        <p className="mt-1.5 text-xs text-ink2">{t("harness.empty.hint")}</p>
      </div>}
      <ScrollBar orientation="horizontal" />
    </ScrollArea>
    {/* The side column is drawn only while this view is in front, so that a list it opened cannot stay over another page. */}
    {current && active && <ScrollArea className={cn("col-start-1 row-start-3 min-h-0 xl:col-start-2 xl:row-span-3 xl:row-start-1 xl:border-l", !opened && "max-xl:hidden")}>
      <HarnessPane key={current.adapter} row={current} name={harnessName(current.adapter)} api={api} csrfToken={snapshot.csrfToken}
        canWrite={!refusal} refusal={refusal} busy={busy}
        onCheck={() => void run({ kind: "check", adapter: current.adapter })}
        onSavePath={path => void run({ kind: "set", adapter: current.adapter, path })}
        onRetry={provider => void run({ kind: "retry", adapter: current.adapter, provider })}
        onRefreshed={reloadSnapshot} onBack={() => setOpened(false)} />
    </ScrollArea>}
  </section>;
}
