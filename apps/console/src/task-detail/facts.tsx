import { useState } from "react";
import type { ReactNode } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { actorRoleText, stopActorText } from "../actor";
import { formatCoolingTime, formatList } from "../i18n/format";
import { quad } from "./names";
import { Lines } from "./parts";
import type { Basis, Ending, Session } from "./types";

/** Why an execution did not end as it should: the cause, who stopped it and why, a native limit. */
export function endingText(ending: Ending, t: TFunction<"taskDetail">): string {
  const cause = ending.cause === "wrap-up" && ending.stoppedBy === "lead" ? t("termination.wrapUpLead")
    : ending.cause === "console-stop" ? stopActorText("console")
    : ending.cause ? t(`termination.${ending.cause}`, { defaultValue: ending.cause }) : null;
  return [
    cause ? t("ending.cause", { cause }) : ending.stoppedBy || ending.quota ? null : t("ending.unrecorded"),
    ending.stoppedBy ? stopActorText(ending.stoppedBy) : null,
    ending.actor ? `${actorRoleText(ending.actor.role)} · ${ending.actor.id}` : null,
    ending.note,
    ending.quota ? t(`quota.${ending.quota}`) : null,
  ].filter(Boolean).join(" · ");
}

/** Where an execution's native session is kept, who holds it, whether the native app lists it, and whether it can be resumed: the receipt's own words, or unknown. */
export function sessionHint(session: Session | null, t: TFunction<"taskDetail">): string {
  const known = (value: string | null, group: "storage" | "owner" | "visibility", unknown: "session.storageUnknown" | "session.visibilityUnknown") => (value ? t(`session.${group}.${value}`, { defaultValue: value }) : t(unknown));
  return [
    known(session?.scope ?? null, "storage", "session.storageUnknown"),
    session?.owner ? known(session.owner, "owner", "session.storageUnknown") : null,
    known(session?.visibility ?? null, "visibility", "session.visibilityUnknown"),
    session?.resumable != null ? t(session.resumable ? "session.resumable" : "session.notResumable") : null,
    session?.binding != null ? t(session.binding ? "session.bound" : "session.unbound") : null,
    session?.note ?? null,
  ].filter(Boolean).join(" · ");
}

/** What a routing decision was bound by, one fact to a line. */
function basisLines(basis: Basis, t: TFunction<"taskDetail">): string[] {
  const named = (buddy: Parameters<typeof quad>[0]) => quad(buddy) ?? t("unrecorded");
  const userExcluded = basis.excluded.filter(item => !item.reason);
  const overloaded = basis.excluded.filter(item => item.reason === "model-overloaded");
  return [
    ...(Object.keys(basis.constraints).length > 0 ? [t("basis.constraints", { buddy: named(basis.constraints) })] : []),
    ...(basis.requiredCapabilities.length > 0 ? [t("basis.capabilities", { list: formatList(basis.requiredCapabilities) })] : []),
    ...(basis.candidateCount !== null ? [t("basis.candidates", { n: basis.candidateCount })] : []),
    ...(userExcluded.length > 0
      ? [t("basis.excluded", { n: userExcluded.length, list: userExcluded.map(item => item.note ? `${named(item.buddy)} (${item.note})` : named(item.buddy)).join("; ") })]
      : []),
    ...(overloaded.length > 0
      ? [t("basis.overloaded", { n: overloaded.length, list: overloaded.map(item => named(item.buddy)).join("; ") })] : []),
    ...((basis.coolingCount ?? basis.cooling.length) > 0
      ? [t("basis.cooling", { n: basis.coolingCount ?? basis.cooling.length,
        list: basis.cooling.map(item => t("basis.coolingBuddy", { buddy: named(item.buddy),
          time: item.coolingUntil ? formatCoolingTime(Date.parse(item.coolingUntil)) : t("unrecorded") })).join("; ") })]
      : []),
    ...basis.preferences.map(item => t(`basis.${item.mode}`, { buddy: item.buddyId, note: item.note ?? t("unrecorded") })),
  ];
}

/** The word that says how the buddy came to be the one; with the frozen bounds, where they were recorded, on hover and focus. */
export function Origin({ basis, children }: { basis: Basis | null; children: ReactNode }) {
  const { t } = useTranslation("taskDetail");
  const lines = basis ? basisLines(basis, t) : [];
  if (lines.length === 0) return <>{children}</>;
  return <Tooltip>
    <TooltipTrigger asChild>
      <span tabIndex={0} className="rounded-sm underline decoration-dotted underline-offset-2 outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50">{children}</span>
    </TooltipTrigger>
    <TooltipContent className="max-w-96 text-left text-pretty">
      <ul className="space-y-1">{lines.map(line => <li key={line} className="wrap-anywhere">{line}</li>)}</ul>
    </TooltipContent>
  </Tooltip>;
}

/** The same bounds, folded under a route's result. */
export function BasisFold({ basis }: { basis: Basis }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const lines = basisLines(basis, t);
  if (lines.length === 0) return null;
  return <Collapsible open={open} onOpenChange={setOpen} className="mt-2">
    <CollapsibleTrigger className="inline-flex items-center gap-1 rounded-sm text-xs">
      <Disclosure open={open} />{t("basis.name")}
    </CollapsibleTrigger>
    <CollapsibleContent><Lines items={lines} className="mt-1.5 text-ink2" /></CollapsibleContent>
  </Collapsible>;
}
