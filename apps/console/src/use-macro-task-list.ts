import { useCopyState } from "./i18n/use-copy-state";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import { retainEqual } from "./retain-equal";
import { documentVisibleNow, useDocumentVisible } from "./page-visibility";
import type { MacroTaskFilter, MacroTaskGroup, MacroTaskQuery } from "./macro-task-types";

type PageState = { groups: MacroTaskGroup[]; totals: Record<MacroTaskFilter, number> };
const emptyPage = (): PageState => ({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 } });
const identities = (groups: MacroTaskGroup[]) => groups.map(group => [group.id, group.macroTasks.map(row => row.macroTaskId)]);

/** Each group owns its keyset. Refreshes replay the loaded extent of each group. */
export function useMacroTaskList(api: ConsoleApi, query: MacroTaskQuery, active: boolean) {
  const [page, setPage] = useState<PageState>(emptyPage);
  const [loading, setLoading] = useState(active), [error, setError] = useCopyState();
  const [reorder, setReorder] = useState<{ count: number | null } | null>(null);
  // Verification evidence advances even on 304, but has no visible effect until
  // an error needs the connection strip. It must not commit an unchanged list.
  const verifiedAt = useRef<number | null>(null);
  const [revision, setRevision] = useState(0);
  const generation = useRef(0), pending = useRef(false), request = useRef<AbortController | null>(null);
  const visible = useDocumentVisible(), previousVisible = useRef(visible);
  const initialReadStarted = useRef(false), needsFirst = useRef(false);
  const pageRef = useRef(page); pageRef.current = page;
  const key = JSON.stringify(query);
  const { sort: _sort, ...scope } = query;
  const scopeKey = JSON.stringify(scope);
  const current = useRef({ query, active }); current.current = { query, active };

  const fetchPage = useCallback(async (mode: "first" | "more" | "poll", foreground = mode !== "poll", groupId?: string) => {
    if (!current.current.active) return;
    const loaded = pageRef.current;
    const group = mode === "more" ? loaded.groups.find(item => item.id === groupId)! : null;
    if (mode === "more" && !group!.nextCursor) return;
    if (pending.current) {
      if (!foreground) return;
      ++generation.current; request.current?.abort();
    }
    if (mode === "poll" && needsFirst.current) mode = "first";
    const version = generation.current, controller = new AbortController();
    const params = current.current.query;
    initialReadStarted.current = true;
    request.current = controller; pending.current = true;
    if (foreground) { setLoading(true); setError(""); }
    try {
      const next = await api.macroTasks({ ...params, ...(group ? { group: group.id, before: group.nextCursor!, limit: 20 } : {}) }, controller.signal);
      let checked = api.readVerifiedAt?.(next) ?? null;
      let groups = next.groups;
      let changed = next.changed;
      // A first read returns every group, then only groups already expanded past
      // five need their loaded extent replayed. The same path handles sorting,
      // polling, retry and reset; no refresh collapses a loaded group to five.
      if (!group) groups = await Promise.all(next.groups.map(async first => {
        const count = loaded.groups.find(item => item.id === first.id)?.macroTasks.length ?? 0;
        if (count <= first.macroTasks.length || !first.nextCursor) return first;
        let result = first;
        while (result.macroTasks.length < count && result.nextCursor) {
          const more = await api.macroTasks({ ...params, group: first.id, before: result.nextCursor, limit: Math.min(100, count - result.macroTasks.length) }, controller.signal);
          changed ||= more.changed;
          const time = api.readVerifiedAt?.(more) ?? null;
          checked = checked === null || time === null ? null : Math.min(checked, time);
          const tail = more.groups[0];
          const merged = new Map(result.macroTasks.map(row => [row.macroTaskId, row]));
          for (const row of tail.macroTasks) merged.set(row.macroTaskId, row);
          result = { ...tail, macroTasks: [...merged.values()] };
        }
        return result;
      }));
      if (version !== generation.current || controller.signal.aborted) return;
      verifiedAt.current = group ? verifiedAt.current === null || checked === null ? null : Math.min(verifiedAt.current, checked) : checked;
      setError("");
      if (group) {
        const tail = groups[0];
        const known = new Set(group.macroTasks.map(row => row.macroTaskId));
        setPage(previous => retainEqual(previous, { groups: previous.groups.map(item => item.id !== group.id ? item : {
          ...tail, macroTasks: [...item.macroTasks, ...tail.macroTasks.filter(row => !known.has(row.macroTaskId))],
        }), totals: next.totals }));
        if (changed) setReorder(previous => previous ?? { count: null });
      } else if (mode === "poll" && loaded.groups.length) {
        const nextById = new Map(groups.map(item => [item.id, item]));
        const retained = loaded.groups.filter(item => nextById.has(item.id)).map(item => {
          const fresh = nextById.get(item.id)!;
          const byId = new Map(fresh.macroTasks.map(row => [row.macroTaskId, row]));
          return { ...fresh, nextCursor: item.nextCursor, macroTasks: item.macroTasks
            .filter(row => fresh.nextCursor !== null || byId.has(row.macroTaskId))
            .map(row => byId.get(row.macroTaskId) ?? row) };
        });
        const orderChanged = JSON.stringify(identities(groups)) !== JSON.stringify(identities(retained));
        setPage(previous => retainEqual(previous, { groups: orderChanged ? retained : groups, totals: next.totals }));
        if (orderChanged || changed) setReorder(previous => previous ?? { count: null });
      } else {
        setPage(previous => retainEqual(previous, { groups, totals: next.totals }));
        needsFirst.current = false;
        setReorder(changed ? { count: null } : null);
      }
    } catch (failure) {
      if (version === generation.current && !controller.signal.aborted && !isAbortError(failure)) setError(() => errorText(failure));
    } finally {
      if (version === generation.current) { pending.current = false; if (foreground) setLoading(false); }
    }
  }, [api]);

  const applyReorder = useCallback(() => {
    if (!reorder || !current.current.active) return;
    needsFirst.current = true;
    setReorder(null);
    void fetchPage("first");
  }, [reorder, fetchPage]);
  const loadedKey = useRef<string | null>(null), loadedScope = useRef<string | null>(null);
  const armed = useRef(false);
  useEffect(() => {
    ++generation.current; request.current?.abort(); pending.current = false; setLoading(false);
    armed.current = false;
    const cleanup = () => { ++generation.current; request.current?.abort(); pending.current = false; };
    const becameVisible = visible && !previousVisible.current;
    previousVisible.current = visible;
    if (!active) return cleanup;
    const sameScope = loadedKey.current === key + revision;
    if (!sameScope) {
      loadedKey.current = key + revision;
      if (loadedScope.current !== scopeKey) {
        loadedScope.current = scopeKey;
        pageRef.current = emptyPage(); setPage(pageRef.current);
        verifiedAt.current = null;
      }
      needsFirst.current = true;
      setReorder(null);
    }
    if (sameScope && pageRef.current.groups.length && !needsFirst.current) return cleanup;
    const initialRead = !initialReadStarted.current;
    if (!visible && !initialRead) return cleanup;
    if (becameVisible) return cleanup;
    setLoading(true); armed.current = true;
    const timer = setTimeout(() => {
      armed.current = false;
      if (initialRead || documentVisibleNow()) void fetchPage("first");
    }, 180);
    return () => { clearTimeout(timer); armed.current = false; cleanup(); };
  }, [active, visible, key, scopeKey, revision, fetchPage]);

  useEffect(() => {
    if (!active || !visible) return;
    let timer: ReturnType<typeof setTimeout>;
    let stopped = false;
    const poll = async () => {
      if (documentVisibleNow() && !armed.current) await fetchPage(pageRef.current.groups.length && !needsFirst.current ? "poll" : "first", false);
      if (!stopped && documentVisibleNow()) timer = setTimeout(poll, 3000);
    };
    void poll();
    return () => { stopped = true; clearTimeout(timer); };
  }, [active, visible, fetchPage]);

  const rows = useMemo(() => page.groups.flatMap(group => group.macroTasks), [page.groups]);
  return {
    groups: page.groups, rows, verifiedAtMs: verifiedAt.current, totals: page.totals, loading, error, reorder,
    more: (groupId: string) => void fetchPage("more", true, groupId),
    retry: () => void fetchPage(needsFirst.current || pageRef.current.groups.length === 0 ? "first" : "poll", true),
    reset: () => setRevision(value => value + 1), applyReorder,
  };
}
