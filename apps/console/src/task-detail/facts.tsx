import { useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "@/components/ui/collapsible";
import { actorRoleText, stopActorText } from "../actor";
import { formatCoolingTime, formatList } from "../i18n/format";
import { quad } from "./names";
import { Paths } from "./parts";
import type { Basis, Ending } from "./types";

/** Why an execution did not end as it should: the cause, who stopped it and why, a native limit. */
export function endingText(ending: Ending, t: TFunction<"taskDetail">): string {
  const cause = ending.cause === "wrap-up" && ending.stoppedBy === "lead" ? t("termination.wrapUpLead")
    : ending.cause === "console-stop" ? stopActorText("console")
    : ending.cause ? t(`termination.${ending.cause}`, { defaultValue: ending.cause }) : null;
  return [
    cause ? t("ending.cause", { cause }) : ending.stoppedBy || ending.rejectionCode ? null : t("ending.unrecorded"),
    ending.stoppedBy ? stopActorText(ending.stoppedBy) : null,
    ending.actor ? `${actorRoleText(ending.actor.role)} · ${ending.actor.id}` : null,
    ending.note,
    ending.rejectionCode ? t(`providerRejection.${ending.rejectionCode}`) : null,
  ].filter(Boolean).join(" · ");
}

/** What a routing decision was bound by, one fact to a line. */
function basisLines(basis: Basis, t: TFunction<"taskDetail">): string[] {
  const named = (buddy: Parameters<typeof quad>[0]) => quad(buddy) ?? t("unrecorded");
  return [
    ...(Object.keys(basis.constraints).length > 0 ? [t("basis.constraints", { buddy: named(basis.constraints) })] : []),
    ...(basis.requiredCapabilities.length > 0 ? [t("basis.capabilities", { list: formatList(basis.requiredCapabilities) })] : []),
    ...(basis.candidateCount !== null ? [t("basis.candidates", { n: basis.candidateCount })] : []),
    ...(basis.excluded.length > 0
      ? [t("basis.excluded", { n: basis.excluded.length, list: basis.excluded.map(item => item.note ? `${named(item.buddy)} (${item.note})` : named(item.buddy)).join("; ") })]
      : []),
    ...(basis.overloadedModelServices.length > 0
      ? [t("basis.overloaded", { n: basis.overloadedModelServices.length,
        list: basis.overloadedModelServices.map(item => [item.harness, item.provider, item.model].join(" / ")).join("; ") })] : []),
    ...(basis.cooling.length > 0
      ? [t("basis.cooling", { n: basis.cooling.length,
        list: basis.cooling.map(item => t("basis.coolingBuddy", { buddy: named(item.buddy),
          time: item.coolingUntil ? formatCoolingTime(Date.parse(item.coolingUntil)) : t("unrecorded") })).join("; ") })]
      : []),
    ...basis.preferences.map(item => t(`basis.${item.mode}`, { buddy: item.buddyId, note: item.note ?? t("unrecorded") })),
  ];
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
    <CollapsibleContent><div className="mt-1.5 text-ink2"><Paths items={lines} /></div></CollapsibleContent>
  </Collapsible>;
}
