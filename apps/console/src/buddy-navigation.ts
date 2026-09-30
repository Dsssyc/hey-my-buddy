export const BUDDY_SECTIONS = { models: "模型", router: "Router", harness: "Harness" } as const;
export type BuddySection = keyof typeof BUDDY_SECTIONS;
export type BuddyLocation = { section: BuddySection; target?: string };

export function buddyHash({ section, target }: BuddyLocation): string {
  return `#buddy/${section}${target ? `/${encodeURIComponent(target)}` : ""}`;
}

/** Old bookmarks still enter the default model section. Unrelated pages keep their own state. */
export function buddyLocation(hash: string): BuddyLocation | null {
  if (["#buddy", "#models", "#settings"].includes(hash)) return { section: "models" };
  const [page, section, target] = hash.slice(1).split("/");
  if (page !== "buddy") return null;
  if (!Object.hasOwn(BUDDY_SECTIONS, section)) return { section: "models" };
  try { return { section: section as BuddySection, target: target ? decodeURIComponent(target) : undefined }; }
  catch { return { section: section as BuddySection }; }
}
