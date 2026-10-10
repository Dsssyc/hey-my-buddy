export const BUDDY_SECTIONS = { models: "模型", router: "Router", harness: "Harness" } as const;
export type BuddySection = keyof typeof BUDDY_SECTIONS;
export type BuddyLocation = { section: BuddySection; target?: string };

export function buddyHash({ section, target }: BuddyLocation): string {
  return `#buddies/${section}${target ? `/${encodeURIComponent(target)}` : ""}`;
}

/** The section and target a hash names on the buddies page, whose bare address opens the model section. Other pages give null. */
export function buddyLocation(hash: string): BuddyLocation | null {
  const [page, section, target] = hash.slice(1).split("/");
  if (page !== "buddies") return null;
  if (!Object.hasOwn(BUDDY_SECTIONS, section)) return { section: "models" };
  try { return { section: section as BuddySection, target: target ? decodeURIComponent(target) : undefined }; }
  catch { return { section: section as BuddySection }; }
}
