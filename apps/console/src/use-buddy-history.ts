import { useCopyState } from "./i18n/use-copy-state";
import i18n from "i18next";
import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { ApiError, errorText } from "./api";
import { modelServiceKey } from "./console-data";
import type { ModelService, BuddyPage, Snapshot } from "./types";

/** One bounded page of retained identities, including unavailable history. */
export const BUDDY_PAGE_SIZE = 100;
/**
 * Display budget for the paged view. Entries beyond it stay reachable through a
 * targeted server search, which filters before pagination.
 */
export const MAX_HISTORY_BUDDIES = 600;
/** `buddy_list` accepts at most 200 characters and 8 terms; bound first. */
export const MAX_QUERY_CHARS = 200;
export const MAX_QUERY_TERMS = 8;
/** Typing settles before a server search runs; the view never polls. */
export const FILTER_DEBOUNCE_MS = 300;

/** Rejects an incomplete envelope instead of rendering invented buddy facts. */
export function parseBuddyPage(value: unknown): BuddyPage {
  const page = value as BuddyPage | null;
  // Model service notes are optional on a page, but a present value must be a list.
  const optionalList = (value: unknown) => value === undefined || Array.isArray(value);
  const valid = !!page
    && Array.isArray(page.buddies)
    && Array.isArray(page.preferences)
    && optionalList(page.modelServiceNotes)
    && Array.isArray(page.modelServiceConcurrency)
    && (page.nextCursor === null || typeof page.nextCursor === "string");
  if (!valid) throw new ApiError("INVALID_RESPONSE", () => i18n.t("console:theBuddyHistoryReplyIsIncompleteCheck"));
  return page;
}

/**
 * Bounded server-side search text: whitespace-separated terms, at most 8 of
 * them and at most 200 characters in total. An oversized term is truncated to
 * the remaining budget instead of silently dropping the whole query. The board
 * filters before paging, so a matching retained row is found without loading
 * the whole archive.
 */
export function boundBuddyQuery(raw: string): string {
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

function addMissing<T extends { buddyId: string }>(current: T[], extra: T[]): T[] {
  const known = new Set(current.map((entry) => entry.buddyId));
  const added = extra.filter((entry) => !known.has(entry.buddyId) && (known.add(entry.buddyId), true));
  return added.length ? [...current, ...added] : current;
}

function addMissingModelServices<T extends ModelService>(current: T[], extra: T[]): T[] {
  const known = new Set(current.map(modelServiceKey));
  const added = extra.filter((entry) => !known.has(modelServiceKey(entry)) && (known.add(modelServiceKey(entry)), true));
  return added.length ? [...current, ...added] : current;
}

/**
 * Appends one page within the current source content generation.
 */
export function mergePage(previous: BuddyPage | null, next: BuddyPage): BuddyPage {
  if (!previous) return next;
  return {
    buddies: addMissing(previous.buddies, next.buddies),
    preferences: addMissing(previous.preferences, next.preferences),
    modelServiceNotes: addMissingModelServices(previous.modelServiceNotes ?? [], next.modelServiceNotes ?? []),
    modelServiceConcurrency: addMissingModelServices(previous.modelServiceConcurrency, next.modelServiceConcurrency),
    nextCursor: next.nextCursor,
  };
}

export type BuddyHistoryFilters = {
  /** Local search text; it is bounded before it becomes a server query. */
  query: string;
  /** Harness (harness) filter, applied by the server before pagination. */
  harness: string;
  /** False keeps a hidden view from fetching in the background. */
  enabled: boolean;
  /** A restored browser preference requests the same bounded history. */
  autoOpen?: boolean;
};

/** Snapshot content that can change the retained model history. */
export function historySourceKey(snapshot: Snapshot): string {
  return JSON.stringify({
    eventCursor: snapshot.eventCursor,
    buddies: snapshot.buddies.map(({ buddyId, available, enabled }) => [buddyId, available, enabled]),
    preferences: snapshot.preferences,
    modelServiceNotes: snapshot.modelServiceNotes,
    modelServiceConcurrency: snapshot.modelServiceConcurrency.map(({ harness, provider, model, limit }) => [harness, provider, model, limit]),
    configuration: snapshot.configuration,
    unavailableBuddyCount: snapshot.unavailableBuddyCount,
  });
}

type StoredPage = { sourceKey: string; key: string; page: BuddyPage | null };

/**
 * Pages the retained buddy history through `buddy_list` with
 * `includeUnavailable: true`. Reading it never writes and never calls a model.
 *
 * The stored page is only exposed while its source content and filter match the
 * live ones, so a render that happens before the reload effect, or a queued
 * reply from an older generation, can never seed a new editor baseline. A filter
 * change drops the cursor and asks the server for a fresh first page; the view
 * never fetches more than the explicitly requested bounded pages.
 */
export function useBuddyHistory(
  api: ConsoleApi,
  csrfToken: string,
  sourceKey: string,
  filters: BuddyHistoryFilters,
) {
  const query = boundBuddyQuery(filters.query);
  const harness = filters.harness.trim();
  const filterKey = `${query}\u0000${harness}`;
  const [stored, setStored] = useState<StoredPage>({
    sourceKey,
    key: filterKey,
    page: null,
  });
  const [wanted, setWanted] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useCopyState();
  const csrf = useRef(csrfToken);
  csrf.current = csrfToken;
  const alive = useRef(true);
  const sequence = useRef(0);
  /** Filter generation whose first page was already requested; no retry loop. */
  const requestedKey = useRef<string | null>(null);
  const params = useRef({ query, harness, sourceKey, key: filterKey });
  params.current = { query, harness, sourceKey, key: filterKey };
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);

  const fetchPage = useCallback(async (after: string | undefined, replace: boolean) => {
    const request = ++sequence.current;
    const { query: search, harness: harness, sourceKey: source, key } = params.current;
    setLoading(true);
    setError("");
    try {
      const value = await api.command<BuddyPage>("buddy_list", {
        includeUnavailable: true,
        limit: BUDDY_PAGE_SIZE,
        ...(search ? { query: search } : {}),
        ...(harness ? { harness: harness } : {}),
        ...(after ? { after } : {}),
      }, csrf.current);
      const next = parseBuddyPage(value);
      if (!alive.current || request !== sequence.current) return;
      // A reply from an older snapshot or filter cannot seed the current view.
      if (key !== params.current.key || source !== params.current.sourceKey) return;
      setStored((previous) => {
        const same = previous.sourceKey === source && previous.key === key;
        return same && previous.page && !replace
          ? { ...previous, page: mergePage(previous.page, next) }
          : { sourceKey: source, key, page: next };
      });
    } catch (failure) {
      if (alive.current && request === sequence.current) setError(() => errorText(failure));
    } finally {
      if (alive.current && request === sequence.current) setLoading(false);
    }
  }, [api]);

  // One bounded first-page request per content/filter generation while the
  // retained view is wanted (and visible). A settled generation is not retried.
  useEffect(() => {
    if (!(wanted || filters.autoOpen) || !filters.enabled) return;
    const key = `${sourceKey}\u0000${filterKey}`;
    if (requestedKey.current === key) return;
    const timer = setTimeout(() => {
      requestedKey.current = key;
      void fetchPage(undefined, true);
    }, FILTER_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [wanted, filters.autoOpen, filters.enabled, sourceKey, filterKey, fetchPage]);

  const page = stored.sourceKey === sourceKey && stored.key === filterKey ? stored.page : null;
  const loaded = page?.buddies.length ?? 0;
  const open = useCallback(() => {
    const key = `${sourceKey}\u0000${filterKey}`;
    if (!stored.page || stored.sourceKey !== sourceKey || stored.key !== filterKey) {
      requestedKey.current = null;
    } else {
      requestedKey.current = key;
    }
    setWanted(true);
  }, [sourceKey, filterKey, stored]);
  const reload = useCallback(() => {
    requestedKey.current = null;
    void fetchPage(undefined, true);
  }, [fetchPage]);
  const loadMore = useCallback(() => {
    const cursor = page?.nextCursor;
    if (!cursor || loading || loaded >= MAX_HISTORY_BUDDIES) return;
    void fetchPage(cursor, false);
  }, [page?.nextCursor, loading, loaded, fetchPage]);
  return {
    page,
    loading,
    error,
    /** True while the current filter still has retained identities to page. */
    hasMore: !!page?.nextCursor && loaded < MAX_HISTORY_BUDDIES,
    limitReached: loaded >= MAX_HISTORY_BUDDIES,
    open,
    reload,
    loadMore,
  };
}
