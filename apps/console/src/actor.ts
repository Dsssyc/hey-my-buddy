import i18n from "i18next";
import { ACTOR_ROLES } from "./types";
import type { Actor, ActorRole, Task } from "./types";

/** One current wire form; an attribution name never supplies a role. */
export function parseActor(value: unknown): Actor | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const actor = value as Record<string, unknown>;
  if (!ACTOR_ROLES.includes(actor.role as ActorRole)
    || (actor.role !== "host" || Object.hasOwn(actor, "id")) && (typeof actor.id !== "string" || !actor.id.trim() || actor.id.length > 256
    || /[\x00-\x1f\x7f]/.test(actor.id))) return null;
  if (actor.role === "host") return { role: "host" };
  return { role: actor.role as ActorRole, id: actor.id as string };
}

export function actorRoleText(name?: ActorRole): string {
  const roles = {
    host: i18n.t("shell:actor.roles.host"), lead: i18n.t("shell:actor.roles.lead"),
    console: i18n.t("shell:actor.roles.console"), service: i18n.t("shell:actor.roles.service"),
    workflow: i18n.t("shell:actor.roles.workflow"), bridge: i18n.t("shell:actor.roles.bridge"),
  };
  return name ? roles[name] : i18n.t("shell:actor.unknownOrigin");
}

/** One attribution for a recorded stop across the timeline, detail and shell. */
export function stopActorText(role: ActorRole | "unknown" | null | undefined): string {
  return i18n.t(`shell:actor.stop.${role ?? "unknown"}`);
}

export function actorText(actor: Actor | null | undefined): string {
  if (!actor) return i18n.t("shell:actor.unrecorded");
  if (actor.role === "host") return actorRoleText("host");
  return i18n.t("shell:actor.identity", { role: actorRoleText(actor.role), id: actor.id });
}

/** Current ownership follows the recorded Lead relationship. */
export function workflowOwnerText(task: Pick<Task, "lineage">): string {
  const lead = task.lineage?.leadOwnership?.leadRunId ?? task.lineage?.rootLeadRunId;
  return lead ? actorText({ role: "lead", id: lead }) : actorRoleText("host");
}
