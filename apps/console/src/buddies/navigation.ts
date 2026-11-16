/** Directory, Router and evaluation addresses use stable identities. */
export type BuddyView = "harness" | "models" | "router" | "evaluation";
export type EvaluationIdentity = { harness: string; modelId: string; effort: string };
export type BuddyLocation = { view: BuddyView; target?: string; targetKind?: "model" | "buddy";
  evaluation?: EvaluationIdentity; profile?: boolean; profileRunId?: string };

export function buddyHash({ view, target, targetKind, evaluation, profile, profileRunId }: BuddyLocation): string {
  if (view === "evaluation") return `#buddies/evaluation${evaluation ? `/${[evaluation.harness, evaluation.modelId, evaluation.effort].map(encodeURIComponent).join("/")}${profile ? "/profile" : ""}` : ""}`;
  if (view === "router") return `#buddies/router${target ? `/${encodeURIComponent(target)}${profileRunId ? `/${encodeURIComponent(profileRunId)}` : ""}` : ""}`;
  if (view === "harness") return `#buddies/harness${target ? `/${encodeURIComponent(target)}` : ""}`;
  return `#buddies/models${target ? `/${targetKind ?? "buddy"}/${encodeURIComponent(target)}` : ""}`;
}

/** Retired addresses and malformed identities name no Buddy location. */
export function buddyLocation(hash: string, defaultView: "harness" | "models" = "models"): BuddyLocation | null {
  const [page, route, ...parts] = hash.slice(1).split("/");
  if (page !== "buddies") return null;
  if (!route) return { view: defaultView };
  try {
    if (route === "harness") {
      if (parts.length === 0) return { view: "harness" };
      if (parts.length !== 1 || !parts[0]) return null;
      return { view: "harness", target: decodeURIComponent(parts[0]) };
    }
    if (route === "models") {
      if (parts.length === 0) return { view: "models" };
      if (parts.length !== 2 || !parts[1] || !["model", "buddy"].includes(parts[0])) return null;
      return { view: "models", targetKind: parts[0] as "model" | "buddy", target: decodeURIComponent(parts[1]) };
    }
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
    return null;
  } catch { return null; }
}
