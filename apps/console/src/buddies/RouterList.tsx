import { cn } from "cn";
import { ChevronDownIcon, ChevronUpIcon, XIcon } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatShortDateTime } from "../i18n/format";
import type { Buddy, RoutingMode, RouterHealthEntry } from "../types";
import { RouterAddDialog } from "./RouterAddDialog";
import { RouterSection } from "./RouterSection";
import { RecordRegion } from "./RecordRegion";
import { QuantBarDisclosure } from "./QuantBar";
import { RouterFailure } from "./RouterFailure";
import { buddyHash } from "./navigation";
import { buddyQuad } from "./roles";
import type { RouterLine, RouterStatus } from "./roles";

/** The DOM id of one buddy's line in the list, so that an address can land on it. */
export function routerLineId(buddyId: string): string {
  return `router-${buddyId}`;
}

/** A small control of a line: it stays on screen when it cannot be used, and says why where it can. */
function LineButton({ label, disabled, reason, className, onClick, children }: {
  label: string; disabled: boolean; reason?: string; className?: string; onClick: () => void; children: ReactNode;
}) {
  return <Hint text={reason}>
    <Button variant="ghost" size="icon-sm" className={cn("aria-disabled:opacity-35 aria-disabled:cursor-default", className)} aria-label={label} aria-disabled={disabled || undefined}
      onClick={() => { if (!disabled) onClick(); }}>{children}</Button>
  </Hint>;
}

/**
 * The mark and the word of a buddy's standing; the recorded code of an unavailable one is the word's hint.
 */
function Standing({ status }: { status: RouterStatus }) {
  const { t } = useTranslation("buddies");
  const hint = status.kind === "unavailable" ? status.code ?? undefined : undefined;
  return <span className="flex items-center gap-1.5 whitespace-nowrap">
    <StateMark kind={status.kind === "current" ? "live" : "idle"} />
    <Hint text={hint}>
      <span tabIndex={hint ? 0 : undefined} className={cn("rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50", status.kind === "current" ? "text-foreground" : "text-ink2")}>
        {t(`roles.list.status.${status.kind}`)}
      </span>
    </Hint>
    {status.kind === "retry" && status.at && <time className="text-ink3 tabular-nums" dateTime={status.at}>{formatShortDateTime(Date.parse(status.at))}</time>}
  </span>;
}

/**
 * The Router list in the order it serves: the first buddy serves first. Each
 * line moves up and down, names the buddy by its four parts as a link to its
 * model, says where it stands, and can be removed; all of it only changes the
 * draft. Its recorded observations share the same row; adding is in the card heading.
 */
export function RouterList({ lines, buddies, mode, locked, note, records, unsaved, onShow, onAdd, onMove, onRemove }: {
  lines: RouterLine[]; buddies: Buddy[]; records?: RouterHealthEntry[]; unsaved?: string;
  /** The default mode, which the add dialog judges the buddies by; absent while the settings cannot be read. */
  mode: RoutingMode | undefined;
  /** Why the list cannot be changed now; absent while it can. */
  locked: string | undefined;
  /** What the user needs to know about the list, written out; absent when there is nothing. */
  note: string | undefined;
  onShow: (buddyId: string) => void;
  onAdd: (buddyId: string) => void;
  onMove: (buddyId: string, by: -1 | 1) => void;
  onRemove: (buddyId: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const [adding, setAdding] = useState(false);
  const added = useRef<string | null>(null);
  useEffect(() => {
    if (added.current && lines.some(line => line.buddyId === added.current)) {
      document.getElementById(routerLineId(added.current))?.scrollIntoView({ block: "nearest", inline: "nearest" });
      added.current = null;
    }
  }, [lines]);
  const maximum = Math.max(0, ...(records ?? []).map(row => (row.answeredCount ?? 0) + (row.noAnswerCount ?? 0)));
  const none = t("roles.records.routers.none");
  return <RouterSection name={t("roles.list.label")} unsaved={unsaved} actions={<Hint text={locked}>
    <Button variant="outline" size="sm" aria-disabled={!!locked || undefined} onClick={() => { if (!locked) setAdding(true); }}>{t("roles.add.button")}</Button>
  </Hint>}>
    {lines.length > 0 && <RecordRegion aria-label={t("roles.list.label")} data-slot="routing-routers-region">
      <table aria-label={t("roles.list.label")} className="w-full text-xs whitespace-nowrap">
        <thead><tr className="text-left font-normal text-ink3">
          <th aria-label={t("roles.list.label")} className="w-8" />
          <th className="pr-4 font-normal">{t("roles.records.routers.buddy")}</th><th />
          <th className="pr-4 font-normal">{t("routerWork.answers")}</th>
          <th className="pr-4 font-normal">{t("roles.records.routers.lastAnswered")}</th>
          <th className="pr-4 font-normal">{t("roles.records.routers.lastError")}</th><th />
        </tr></thead>
        <tbody>{lines.map(({ buddyId, buddy, status }, index) => {
          const name = buddy ? buddyQuad(buddy) : buddyId;
          const record = records?.find(row => row.buddyId === buddyId);
          const answerHint = record ? t("routerWork.answerHint", { answered: record.answeredCount ?? t("roles.records.unknown"), missing: record.noAnswerCount ?? t("roles.records.unknown"), consecutive: record.consecutiveNoAnswers ?? t("roles.records.unknown") }) : none;
          return <tr key={buddyId} id={routerLineId(buddyId)} tabIndex={-1} aria-label={name} className="outline-none focus-visible:bg-sunk">
            <td className="pr-3 text-right text-ink3 tabular-nums">{index + 1}</td>
            <td className="pr-4 font-mono">{buddy
              ? <a href={buddyHash({ view: "models", target: buddyId })} className="rounded-sm whitespace-nowrap outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
                onClick={event => { event.preventDefault(); onShow(buddyId); }}>{name}</a>
              : <span>{name}</span>}</td>
            <td className="pr-4">{status && <Standing status={status} />}</td>
            <td className="pr-4">{record ? <QuantBarDisclosure floating preferenceKey={`routing.${buddyId}`} label={`${name}: ${answerHint}`} maximum={maximum}
              parts={[{ value: record.answeredCount ?? null, tone: "green" }, { value: record.noAnswerCount ?? null, tone: "pink" }]}>
              {answerHint}
            </QuantBarDisclosure> : none}</td>
            <td className="pr-4 text-ink2">{record?.lastAnsweredAt ? <time dateTime={record.lastAnsweredAt}>{formatShortDateTime(Date.parse(record.lastAnsweredAt))}</time> : none}</td>
            <td className="pr-4 text-ink2">{record ? record.lastError?.code ? <RouterFailure failure={record.lastError.code} /> : t("roles.records.unknown") : none}</td>
            <td><div className="flex justify-end">
              <LineButton label={t("roles.list.moveUp", { name })} disabled={!!locked || index === 0} reason={locked ?? (index === 0 ? t("routerWork.first") : undefined)} onClick={() => onMove(buddyId, -1)}><ChevronUpIcon /></LineButton>
              <LineButton label={t("roles.list.moveDown", { name })} disabled={!!locked || index === lines.length - 1} reason={locked ?? (index === lines.length - 1 ? t("routerWork.last") : undefined)} onClick={() => onMove(buddyId, 1)}><ChevronDownIcon /></LineButton>
              <LineButton label={t("roles.list.remove", { name })} disabled={!!locked} reason={locked} onClick={() => onRemove(buddyId)}><XIcon /></LineButton>
            </div></td>
          </tr>;
        })}</tbody>
      </table>
    </RecordRegion>}
    {lines.length === 0 && mode && <div className="py-1.5">
      <p className="font-medium">{t("roles.list.empty.title")}</p>
      <p className="mt-1 text-xs text-ink2">{t("roles.list.empty.hint")}</p>
    </div>}
    {note && <p className="mt-3 text-xs text-hand" role="status">{note}</p>}
    {mode && <RouterAddDialog open={adding} onOpenChange={setAdding} buddies={buddies} list={lines.map(line => line.buddyId)} mode={mode}
      onAdd={buddyId => { added.current = buddyId; onAdd(buddyId); }} />}
  </RouterSection>;
}
