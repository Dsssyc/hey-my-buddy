/** The three views of the Buddy page: models and harnesses are the two tables of its catalogue, roles is its other section. */
export const BUDDY_VIEWS = ["models", "harness", "roles"] as const;
export type BuddyView = (typeof BUDDY_VIEWS)[number];
export type BuddyLocation = { view: BuddyView; target?: string };

export function buddyHash({ view, target }: BuddyLocation): string {
  return `#buddies/${view}${target ? `/${encodeURIComponent(target)}` : ""}`;
}

/** The view and target a hash names on the Buddy page, whose bare address opens the models. Other pages give null. */
export function buddyLocation(hash: string): BuddyLocation | null {
  const [page, view, target] = hash.slice(1).split("/");
  if (page !== "buddies") return null;
  if (!(BUDDY_VIEWS as readonly string[]).includes(view)) return { view: "models" };
  try { return { view: view as BuddyView, target: target ? decodeURIComponent(target) : undefined }; }
  catch { return { view: view as BuddyView }; }
}
