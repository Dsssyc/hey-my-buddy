import { createContext, useContext } from "react";
import type { ReactNode } from "react";
import type { Buddy } from "../types";

export type BuddyIdentity = { harness?: string | null; provider?: string | null; model?: string | null; effort?: string | null; alias?: string | null };
const BuddyNames = createContext<readonly Buddy[]>([]);

/** The live catalogue owns aliases; execution identities remain recorded facts. */
export function BuddyNamesProvider({ buddies, children }: { buddies: readonly Buddy[]; children: ReactNode }) {
  return <BuddyNames.Provider value={buddies}>{children}</BuddyNames.Provider>;
}

export function useBuddies() { return useContext(BuddyNames); }

export function useNamedBuddy() {
  const buddies = useContext(BuddyNames);
  return <T extends BuddyIdentity>(identity: T | null | undefined): T | null | undefined => {
    if (!identity) return identity;
    const current = buddies.find(buddy => buddy.harness === identity.harness && buddy.provider === identity.provider
      && buddy.model === identity.model && buddy.effort === identity.effort);
    return current ? { ...identity, alias: current.alias } : identity;
  };
}

export function AliasTag({ alias }: { alias: string | null | undefined }) {
  return alias ? <span data-slot="alias-tag" className="ml-2 inline-flex h-5 items-center rounded-[4px] border px-1.5 text-xs leading-none whitespace-nowrap"
    style={{ color: "var(--alias)", borderColor: "var(--alias-line)", backgroundColor: "var(--alias-wash)" }}>{alias}</span> : null;
}

/** A complete or abbreviated name followed by the same current alias everywhere. */
export function BuddyName({ buddy, children }: { buddy: BuddyIdentity | null | undefined; children: ReactNode }) {
  const named = useNamedBuddy()(buddy);
  return <span className="inline-flex items-center">{children}<AliasTag alias={named?.alias} /></span>;
}
