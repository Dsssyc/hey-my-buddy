import i18n from "i18next";
import type { Actor, Task } from "./types";

/** One current wire form; an attribution name never supplies a role. */
export function parseActor(value: unknown): Actor | null {
  if (!value || typeof value !== "object" || Array.isArray(value)) return null;
  const actor = value as Record<string, unknown>;
  if (typeof actor.role !== "string" || !actor.role.trim() || actor.role.length > 32
    || typeof actor.id !== "string" || !actor.id.trim() || actor.id.length > 256
    || /[\x00-\x1f\x7f]/.test(actor.role + actor.id)) return null;
  return { role: actor.role, id: actor.id };
}

export function actorRoleText(name?: string): string {
  const roles = {
    host: i18n.t("shell:actor.roles.host"), lead: i18n.t("shell:actor.roles.lead"),
    console: i18n.t("shell:actor.roles.console"), service: i18n.t("shell:actor.roles.service"),
    workflow: i18n.t("shell:actor.roles.workflow"), bridge: i18n.t("shell:actor.roles.bridge"),
    worker: i18n.t("shell:actor.roles.worker"), agent: i18n.t("shell:actor.roles.agent"),
  };
  return name ? roles[name as keyof typeof roles] ?? name : i18n.t("shell:actor.unknownOrigin");
}

export function actorText(actor: Actor | null | undefined): string {
  if (!actor) return i18n.t("shell:actor.unrecorded");
  return i18n.t("shell:actor.identity", { role: actorRoleText(actor.role), id: actor.id });
}

/** A Lead relationship explains an absent Host name; it never invents one. */
export function workflowOwnerText(task: Pick<Task, "lineage" | "workflow">, hostId?: string | null): string {
  const host = task.lineage?.currentHostId ?? hostId ?? task.workflow?.hostId;
  if (host) return host;
  const lead = task.lineage?.leadOwnership?.leadRunId ?? task.lineage?.rootLeadRunId;
  return actorText(lead ? { role: "lead", id: lead } : null);
}
