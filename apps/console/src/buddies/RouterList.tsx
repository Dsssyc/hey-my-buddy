import { cn } from "cn";
import { ChevronDownIcon, ChevronUpIcon, XIcon } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Button } from "@/components/ui/button";
import { StateMark } from "@/components/ui/state-mark";
import { formatShortDateTime } from "../i18n/format";
import type { Buddy, RoutingMode } from "../types";
import { RouterAddDialog } from "./RouterAddDialog";
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
  return <Hint text={reason || label}>
    <Button variant="ghost" size="icon-sm" className={className} aria-label={label} aria-disabled={disabled || undefined}
      onClick={() => { if (!disabled) onClick(); }}>{children}</Button>
  </Hint>;
}

/**
 * The mark and the word of a buddy's standing; the recorded code of an unavailable one is the word's hint.
 * In a window too narrow for one row it stands under the name, which then has the row's width to itself.
 */
function Standing({ status }: { status: RouterStatus }) {
  const { t } = useTranslation("buddies");
  const hint = status.kind === "unavailable" ? status.code ?? undefined : undefined;
  return <span className="flex items-center gap-1.5 whitespace-nowrap max-sm:col-start-3 max-sm:row-start-2">
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
 * draft. Below the list are what the user needs to know about it and the
 * button that adds a buddy.
 */
export function RouterList({ lines, buddies, mode, locked, note, onShow, onAdd, onMove, onRemove }: {
  lines: RouterLine[]; buddies: Buddy[];
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
  return <div className="max-w-[720px]">
    {lines.length > 0 && <ol aria-label={t("roles.list.label")} className="grid grid-cols-[auto_20px_minmax(0,1fr)_auto_28px] gap-x-2.5">
      {lines.map(({ buddyId, buddy, status }, index) => {
        const name = buddy ? buddyQuad(buddy) : buddyId;
        return <li key={buddyId} id={routerLineId(buddyId)} tabIndex={-1} aria-label={name}
          className="col-span-full grid grid-cols-subgrid items-center border-b py-1.5 outline-none focus-visible:bg-sunk">
          <span className="flex">
            <LineButton label={t("roles.list.moveUp", { name })} disabled={!!locked || index === 0} reason={locked} onClick={() => onMove(buddyId, -1)}><ChevronUpIcon /></LineButton>
            <LineButton label={t("roles.list.moveDown", { name })} disabled={!!locked || index === lines.length - 1} reason={locked} onClick={() => onMove(buddyId, 1)}><ChevronDownIcon /></LineButton>
          </span>
          <span className="text-right text-ink3 tabular-nums">{index + 1}</span>
          {buddy
            ? <a href={buddyHash({ view: "models", target: buddyId })} className="min-w-0 rounded-sm font-mono text-xs wrap-anywhere outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50"
              onClick={event => { event.preventDefault(); onShow(buddyId); }}>{name}</a>
            : <span className="min-w-0 font-mono text-xs wrap-anywhere">{name}</span>}
          {status ? <Standing status={status} /> : <span />}
          <LineButton label={t("roles.list.remove", { name })} disabled={!!locked} reason={locked} className="max-sm:col-start-5 max-sm:row-start-1"
            onClick={() => onRemove(buddyId)}><XIcon /></LineButton>
        </li>;
      })}
    </ol>}
    {lines.length === 0 && mode && <div className="py-1.5">
      <p className="font-medium">{t("roles.list.empty.title")}</p>
      <p className="mt-1 text-xs text-ink2">{t("roles.list.empty.hint")}</p>
    </div>}
    {note && <p className="mt-3 border-l-2 border-hand-mark pl-3 text-xs text-hand" role="status">{note}</p>}
    <Hint text={locked}>
      <Button variant="outline" size="sm" className="mt-3" aria-disabled={!!locked || undefined} onClick={() => { if (!locked) setAdding(true); }}>
        {t("roles.add.button")}
      </Button>
    </Hint>
    {mode && <RouterAddDialog open={adding} onOpenChange={setAdding} buddies={buddies} list={lines.map(line => line.buddyId)} mode={mode}
      onAdd={onAdd} />}
  </div>;
}
