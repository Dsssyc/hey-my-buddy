import { useCallback, useEffect, useRef, useState } from "react";
import type { ConsoleApi } from "./api";
import { errorText } from "./api";
import type { ProfilePage } from "./types";

/** One bounded page of retained identities, including unavailable history. */
export const PROFILE_PAGE_SIZE = 100;
/** Display budget: further history stays readable through the server cursor. */
export const MAX_HISTORY_PROFILES = 600;

function count(value: unknown): number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0
    ? value
    : Number.NaN;
}

/** Rejects an incomplete envelope instead of rendering invented profile facts. */
export function parseProfilePage(value: unknown): ProfilePage {
  const page = value as ProfilePage | null;
  const valid = !!page
    && Array.isArray(page.profiles)
    && Array.isArray(page.cards)
    && Array.isArray(page.annotations)
    && Array.isArray(page.preferences)
    && !!page.sampleCounts && typeof page.sampleCounts === "object"
    && Number.isInteger(page.tableRevision)
    && (page.nextCursor === null || typeof page.nextCursor === "string");
  if (!valid) throw new Error("配置历史响应不完整，请检查服务版本。");
  return page;
}

function addMissing<T extends { profileId: string }>(current: T[], extra: T[]): T[] {
  const known = new Set(current.map((entry) => entry.profileId));
  const added = extra.filter((entry) => !known.has(entry.profileId) && (known.add(entry.profileId), true));
  return added.length ? [...current, ...added] : current;
}

function mergePage(previous: ProfilePage | null, next: ProfilePage): ProfilePage {
  if (!previous) return next;
  return {
    profiles: addMissing(previous.profiles, next.profiles),
    cards: addMissing(previous.cards, next.cards),
    annotations: addMissing(previous.annotations, next.annotations),
    preferences: addMissing(previous.preferences, next.preferences),
    sampleCounts: { ...next.sampleCounts, ...previous.sampleCounts },
    tableRevision: next.tableRevision,
    nextCursor: next.nextCursor,
  };
}

/**
 * Pages the retained profile history through `model_profiles` with
 * `includeUnavailable: true`. Reading it never writes and never calls a model;
 * the newest page wins only for identities the snapshot already shows.
 */
export function useProfileHistory(api: ConsoleApi, csrfToken: string) {
  const [page, setPage] = useState<ProfilePage | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const csrf = useRef(csrfToken);
  csrf.current = csrfToken;
  const alive = useRef(true);
  const sequence = useRef(0);
  useEffect(() => {
    alive.current = true;
    return () => { alive.current = false; };
  }, []);
  const fetchPage = useCallback(async (after: string | undefined, replace: boolean) => {
    const request = ++sequence.current;
    setLoading(true);
    setError("");
    try {
      const value = await api.command<ProfilePage>("model_profiles", {
        includeUnavailable: true,
        limit: PROFILE_PAGE_SIZE,
        ...(after ? { after } : {}),
      }, csrf.current);
      const next = parseProfilePage(value);
      if (!alive.current || request !== sequence.current) return;
      setPage((previous) => (replace ? next : mergePage(previous, next)));
    } catch (failure) {
      if (alive.current && request === sequence.current) setError(errorText(failure));
    } finally {
      if (alive.current && request === sequence.current) setLoading(false);
    }
  }, [api]);
  const loaded = page?.profiles.length ?? 0;
  /** Loads the first page once; re-opening a toggled-off view stays cheap. */
  const open = useCallback(() => {
    if (!page && !loading) void fetchPage(undefined, true);
  }, [page, loading, fetchPage]);
  const reload = useCallback(() => { void fetchPage(undefined, true); }, [fetchPage]);
  const loadMore = useCallback(() => {
    const cursor = page?.nextCursor;
    if (!cursor || loading || loaded >= MAX_HISTORY_PROFILES) return;
    void fetchPage(cursor, false);
  }, [page?.nextCursor, loading, loaded, fetchPage]);
  return {
    page,
    loading,
    error,
    /** True while the server cursor still holds retained identities. */
    hasMore: !!page?.nextCursor && loaded < MAX_HISTORY_PROFILES,
    limitReached: loaded >= MAX_HISTORY_PROFILES,
    open,
    reload,
    loadMore,
  };
}
