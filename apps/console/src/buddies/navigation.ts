/** Directory, Router and evaluation addresses use stable identities. */
export type BuddyView = "models" | "router" | "evaluation";
export type EvaluationIdentity = { harness: string; modelId: string; effort: string };
export type BuddyLocation = { view: BuddyView; target?: string; targetKind?: "harness" | "model" | "buddy";
  evaluation?: EvaluationIdentity; profile?: boolean; profileRunId?: string };

export function buddyHash({ view, target, targetKind, evaluation, profile, profileRunId }: BuddyLocation): string {
  if (view === "evaluation") return `#buddies/evaluation${evaluation ? `/${[evaluation.harness, evaluation.modelId, evaluation.effort].map(encodeURIComponent).join("/")}${profile ? "/profile" : ""}` : ""}`;
  if (view === "router") return `#buddies/router${target ? `/${encodeURIComponent(target)}${profileRunId ? `/${encodeURIComponent(profileRunId)}` : ""}` : ""}`;
  return `#buddies/catalog${target ? `/${targetKind ?? "buddy"}/${encodeURIComponent(target)}` : ""}`;
}

/** Retired addresses and malformed identities name no Buddy location. */
export function buddyLocation(hash: string): BuddyLocation | null {
  const [page, route, ...parts] = hash.slice(1).split("/");
  if (page !== "buddies") return null;
  if (!route) return { view: "models" };
  try {
    if (route === "evaluation") {
      if (parts.length === 0) return { view: "evaluation" };
      if ((parts.length !== 3 && parts.length !== 4) || parts.slice(0, 3).some(part => !part) || parts.length === 4 && parts[3] !== "profile") return null;
      const [harness, modelId, effort] = parts.map(decodeURIComponent);
      return { view: "evaluation", evaluation: { harness, modelId, effort }, ...(parts[3] ? { profile: true } : {}) };
    }
    if (route === "router") {
      if (parts.length === 0) return { view: "router" };
      const [target, run] = parts.map(decodeURIComponent);
      if (!["routing", "maintenance", "profiles"].includes(target) || parts.length > 2 || parts.length === 2 && (target !== "profiles" || !run)) return null;
      return { view: "router", target, ...(run ? { profileRunId: run } : {}) };
    }
    if (route !== "catalog" || parts.length > 2) return null;
    if (parts.length === 0) return { view: "models" };
    const [kind, target] = parts;
    if (!["harness", "model", "buddy"].includes(kind) || !target) return null;
    return { view: "models", targetKind: kind as BuddyLocation["targetKind"], target: decodeURIComponent(target) };
  } catch { return null; }
}
