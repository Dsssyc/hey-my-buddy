/** Directory selection; the existing roles view sends a buddy using the models intent. */
export const BUDDY_VIEWS = ["models", "roles"] as const;
export type BuddyView = (typeof BUDDY_VIEWS)[number];
export type BuddyLocation = { view: BuddyView; target?: string; targetKind?: "harness" | "model" | "buddy" };

export function buddyHash({ view, target, targetKind }: BuddyLocation): string {
  if (view === "roles") return `#buddies/roles${target ? `/${encodeURIComponent(target)}` : ""}`;
  return `#buddies/catalog${target ? `/${targetKind ?? "buddy"}/${encodeURIComponent(target)}` : ""}`;
}

/** Old table addresses are absent. Invalid addresses name no Buddy location. */
export function buddyLocation(hash: string): BuddyLocation | null {
  const [page, route, kind, target, extra] = hash.slice(1).split("/");
  if (page !== "buddies") return null;
  if (!route) return { view: "models" };
  if (route === "roles" && !target) {
    try { return { view: "roles", ...(kind ? { target: decodeURIComponent(kind) } : {}) }; }
    catch { return null; }
  }
  if (route !== "catalog" || extra) return null;
  if (!kind) return { view: "models" };
  if (!["harness", "model", "buddy"].includes(kind) || !target) return null;
  try { return { view: "models", targetKind: kind as BuddyLocation["targetKind"], target: decodeURIComponent(target) }; }
  catch { return null; }
}
