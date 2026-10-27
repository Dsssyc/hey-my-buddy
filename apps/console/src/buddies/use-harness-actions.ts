import i18n from "i18next";
import { useCopyState } from "../i18n/use-copy-state";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { errorText, snapshotHarnesses } from "../api";
import type { ConsoleApi } from "../api";
import { harnessName } from "../buddy-display";
import type { HarnessHealth, Snapshot } from "../types";
import { harnessPath, statusText } from "./harness";
import { clearCooldown } from "./harness-retry";

const liveT = i18n.getFixedT(null, "buddies");

/** One explicit action; kept so that a failed attempt can be retried as it was. */
type Action =
  | { kind: "check"; harness?: string }
  | { kind: "set"; harness: string; path: string | null }
  | { kind: "retry"; harness: string; provider: string };

/** Explicit directory operations, preserving recorded replies. */
export function useHarnessActions({ snapshot, api, refresh, mutationsAvailable, sessionWritable }: {
  snapshot: Snapshot; api: ConsoleApi; refresh: () => Promise<Snapshot | null>;
  mutationsAvailable: boolean; sessionWritable: boolean;
}) {
  const { t } = useTranslation("buddies");
  const recorded = useMemo(() => snapshotHarnesses(snapshot), [snapshot]);
  /** Rows returned by this view's own actions, shown until a snapshot reload lands. */
  const [replies, setReplies] = useState<Record<string, HarnessHealth>>({});
  /** Whether an action is under way; only one is at a time. */
  const [busy, setBusy] = useState(false);
  const [error, setError] = useCopyState(), [note, setNote] = useCopyState();
  const [retry, setRetry] = useState<Action | null>(null);
  const [refused, setRefused] = useState(false);

  const rows = recorded.map(row => replies[row.harness] ?? row);
  const refusal = !sessionWritable ? t("loginExpired") : !mutationsAvailable ? t("harness.refused") : "";
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
        const checked = await api.harnessRefresh(snapshot.csrfToken, action.harness);
        setReplies(previous => ({ ...previous, ...Object.fromEntries(checked.map(row => [row.harness, row])) }));
        setNote(() => action.harness
          ? liveT("harness.done.checked", { name: harnessName(action.harness), status: statusText(checked.find(row => row.harness === action.harness)?.status ?? "unknown", liveT) })
          : liveT("harness.done.checkedAll", { ready: checked.filter(row => row.status === "ready").length, n: checked.length }));
      } else if (action.kind === "set") {
        const updated = await api.harnessSet(action.harness, action.path, snapshot.csrfToken);
        setReplies(previous => ({ ...previous, [updated.harness]: updated }));
        setNote(() => {
          const status = { name: harnessName(updated.harness), status: statusText(updated.status, liveT) };
          return action.path ? liveT("harness.done.saved", { path: harnessPath(updated) ?? action.path, ...status }) : liveT("harness.done.restored", status);
        });
      } else {
        const outcome = await clearCooldown(api, snapshot.csrfToken, action.harness, action.provider);
        setNote(() => liveT(`harness.cooling.done.${outcome}`, { provider: action.provider }));
      }
      await reloadSnapshot();
    } catch (failure) {
      setError(() => errorText(failure));
      setRetry(action);
    } finally {
      setBusy(false);
    }
  }

  return { rows, busy, error, note, retry, guard: refused ? refusal : "", refusal, run, reloadSnapshot };

}
