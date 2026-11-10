import { createContext, useContext, Fragment } from "react";
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
  return <span data-slot="buddy-name" className="inline">{children}<AliasTag alias={named?.alias} /></span>;
}

/** The same recorded four fields, with each word kept whole while separators can wrap. */
export function BuddyIdentityName({ buddy, empty = "" }: { buddy: BuddyIdentity | null | undefined; empty?: string }) {
  const parts = buddy ? [buddy.harness, buddy.provider, buddy.model, buddy.effort].filter(Boolean) : [];
  return <BuddyName buddy={buddy}>{parts.length ? parts.map((part, index) => <Fragment key={index}>{index > 0 && " / "}<span className="whitespace-nowrap">{part}</span></Fragment>) : empty}</BuddyName>;
}

/** Square brackets belong only to nonvisual accessibility text in the console. */
export function accessibleBuddyName(name: string | null, buddy: BuddyIdentity | null | undefined): string | null {
  return name && buddy?.alias ? `${name} [${buddy.alias}]` : name;
}
