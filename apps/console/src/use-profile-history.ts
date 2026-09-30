import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import { familyKey } from "./console-data";
import type { ModelFamily, ProfilePage } from "./types";

/** One bounded page of retained identities, including unavailable history. */
export const PROFILE_PAGE_SIZE = 100;
/**
 * Display budget for the paged view. Entries beyond it stay reachable through a
 * targeted server search, which filters before pagination.
 */
export const MAX_HISTORY_PROFILES = 600;
/** `model_profiles` accepts at most 200 characters and 8 terms; bound first. */
export const MAX_QUERY_CHARS = 200;
export const MAX_QUERY_TERMS = 8;
/** Typing settles before a server search runs; the view never polls. */
export const FILTER_DEBOUNCE_MS = 300;

function count(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : Number.NaN;
}

/** Rejects an incomplete envelope instead of rendering invented profile facts. */
export function parseProfilePage(value: unknown): ProfilePage {
  const page = value as ProfilePage | null;
  // The family rows and overrides are optional on a page (absent reads as
  // none), but a present value must be a list.
  const optionalList = (value: unknown) => value === undefined || Array.isArray(value);
  const valid = !!page
    && Array.isArray(page.profiles)
    && Array.isArray(page.cards)
    && Array.isArray(page.preferences)
    && optionalList(page.preferenceOverrides)
    && optionalList(page.familyPreferences)
    && optionalList(page.familyAnnotations)
    && Array.isArray(page.modelConcurrency)
    && !!page.sampleCounts && typeof page.sampleCounts === "object"
    && Number.isInteger(page.tableRevision)
    && (page.nextCursor === null || typeof page.nextCursor === "string");
  if (!valid) throw new Error("配置历史响应不完整，请检查服务版本。");
  return page;
}

/**
 * Bounded server-side search text: whitespace-separated terms, at most 8 of
 * them and at most 200 characters in total. An oversized term is truncated to
 * the remaining budget instead of silently dropping the whole query. The board
 * filters before paging, so a matching retained row is found without loading
 * the whole archive.
 */
export function boundProfileQuery(raw: string): string {
  const terms = raw.trim().split(/\s+/).filter(Boolean).slice(0, MAX_QUERY_TERMS);
  const kept: string[] = [];
  let length = 0;
  for (const term of terms) {
    const room = MAX_QUERY_CHARS - length - (kept.length ? 1 : 0);
    if (room <= 0) break;
    const bounded = term.slice(0, room);
    if (!bounded) break;
    kept.push(bounded);
    length += bounded.length + (kept.length > 1 ? 1 : 0);
  }
  return kept.join(" ");
}

function addMissing<T extends { profileId: string }>(current: T[], extra: T[]): T[] {
  const known = new Set(current.map((entry) => entry.profileId));
  const added = extra.filter((entry) => !known.has(entry.profileId) && (known.add(entry.profileId), true));
  return added.length ? [...current, ...added] : current;
}

function addMissingFamilies<T extends ModelFamily>(current: T[], extra: T[]): T[] {
  const known = new Set(current.map(familyKey));
  const added = extra.filter((entry) => !known.has(familyKey(entry)) && (known.add(familyKey(entry)), true));
  return added.length ? [...current, ...added] : current;
}

/**
 * Appends one page of the same revision. A page read at another `tableRevision`
 * is never merged in or relabelled: pages from different revisions do not
 * describe the same table, so the already-loaded revision is kept.
 */
export function mergePage(previous: ProfilePage | null, next: ProfilePage): ProfilePage {
  if (!previous) return next;
  if (previous.tableRevision !== next.tableRevision) return previous;
  return {
    profiles: addMissing(previous.profiles, next.profiles),
    cards: addMissing(previous.cards, next.cards),
    preferences: addMissing(previous.preferences, next.preferences),
    preferenceOverrides: addMissing(previous.preferenceOverrides ?? [], next.preferenceOverrides ?? []),
    familyPreferences: addMissingFamilies(previous.familyPreferences ?? [], next.familyPreferences ?? []),
    familyAnnotations: addMissingFamilies(previous.familyAnnotations ?? [], next.familyAnnotations ?? []),
    modelConcurrency: addMissingFamilies(previous.modelConcurrency, next.modelConcurrency),
    sampleCounts: { ...next.sampleCounts, ...previous.sampleCounts },
    tableRevision: next.tableRevision,
    nextCursor: next.nextCursor,
  };
}

export type ProfileHistoryFilters = {
  /** Local search text; it is bounded before it becomes a server query. */
  query: string;
  /** Harness (adapter) filter, applied by the server before pagination. */
  adapter: string;
  /** False keeps a hidden view from fetching in the background. */
  enabled: boolean;
};

/** Stored page plus the revision and filter generation it was read for. */
type StoredPage = { revision: number; key: string; page: ProfilePage | null };

/**
 * Pages the retained profile history through `model_profiles` with
 * `includeUnavailable: true`. Reading it never writes and never calls a model.
 *
 * The stored page is only exposed while its `tableRevision` and filter match the
 * live ones, so a render that happens before the reload effect, or a queued
 * reply from an older generation, can never seed a new editor baseline. A filter
 * change drops the cursor and asks the server for a fresh first page; the view
 * never fetches more than the explicitly requested bounded pages.
 */
export function useProfileHistory(
  api: ConsoleApi,
  csrfToken: string,
  tableRevision: number,
  filters: ProfileHistoryFilters,
) {
  const query = boundProfileQuery(filters.query);
  const adapter = filters.adapter.trim();
  const filterKey = `${query}\u0000${adapter}`;
  const [stored, setStored] = useState<StoredPage>({
    revision: tableRevision,
    key: filterKey,
    page: null,
  });
  const [wanted, setWanted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const csrf = useRef(csrfToken);
  csrf.current = csrfToken;
  const alive = useRef(true);
  const sequence = useRef(0);
  /** Filter generation whose first page was already requested; no retry loop. */
  const requestedKey = useRef<string | null>(null);
  const params = useRef({ query, adapter, revision: tableRevision, key: filterKey });
  params.current = { query, adapter, revision: tableRevision, key: filterKey };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const fetchPage = useCallback(async (after: string | undefined, replace: boolean) => {
    const request = ++sequence.current;
    const { query: search, adapter: harness, revision, key } = params.current;
    setLoading(true);
    setError("");
    try {
      const value = await api.command<ProfilePage>("model_profiles", {
        includeUnavailable: true,
        limit: PROFILE_PAGE_SIZE,
        ...(search ? { query: search } : {}),
        ...(harness ? { adapter: harness } : {}),
        ...(after ? { after } : {}),
      }, csrf.current);
      const next = parseProfilePage(value);
      if (!alive.current || request !== sequence.current) return;
      // A reply for another revision or another filter describes another table
      // generation; it is discarded instead of being relabelled.
      if (next.tableRevision !== revision || key !== params.current.key
        || revision !== params.current.revision) return;
      setStored((previous) => {
        const same = previous.revision === revision && previous.key === key;
        return same && previous.page && !replace
          ? { ...previous, page: mergePage(previous.page, next) }
          : { revision, key, page: next };
      });
    } catch (failure) {
      if (alive.current && request === sequence.current) setError(errorText(failure));
    } finally {
      if (alive.current && request === sequence.current) setLoading(false);
    }
  }, [api]);

  // One bounded first-page request per revision/filter generation while the
  // retained view is wanted (and visible). A settled generation is not retried.
  useEffect(() => {
    if (!wanted || !filters.enabled) return;
    const key = `${tableRevision}\u0000${filterKey}`;
    if (requestedKey.current === key) return;
    const timer = setTimeout(() => {
      requestedKey.current = key;
      void fetchPage(undefined, true);
    }, FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [wanted, filters.enabled, tableRevision, filterKey, fetchPage]);

  const page = stored.revision === tableRevision && stored.key === filterKey ? stored.page : null;
  const loaded = page?.profiles.length ?? 0;
  const open = useCallback(() => {
    const key = `${tableRevision}\u0000${filterKey}`;
    if (!stored.page || stored.revision !== tableRevision || stored.key !== filterKey) {
      requestedKey.current = null;
    } else {
      requestedKey.current = key;
    }
    setWanted(true);
  }, [tableRevision, filterKey, stored]);
  const reload = useCallback(() => {
    requestedKey.current = null;
    void fetchPage(undefined, true);
  }, [fetchPage]);
  const loadMore = useCallback(() => {
    const cursor = page?.nextCursor;
    if (!cursor || loading || loaded >= MAX_HISTORY_PROFILES) return;
    void fetchPage(cursor, false);
  }, [page?.nextCursor, loading, loaded, fetchPage]);
  return {
    page,
    loading,
    error,
    /** True while the current filter still has retained identities to page. */
    hasMore: !!page?.nextCursor && loaded < MAX_HISTORY_PROFILES,
    limitReached: loaded >= MAX_HISTORY_PROFILES,
    open,
    reload,
    loadMore,
  };
}
