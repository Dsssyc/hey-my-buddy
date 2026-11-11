import { useEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "./components/ui/button";
import { Hint } from "./components/hint";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import type { MacroTaskSummary, WorkAction } from "./macro-task-types";
import { canRetryEnd, type WorkEntries } from "./macro-task-control";

export function MacroTaskEntryMenu({ summary, entries, writable, busy, active, onAction, onEnd, onDelete }: {
  summary: MacroTaskSummary; entries?: WorkEntries; writable: boolean; busy: boolean; active: boolean;
  onAction: (summary: MacroTaskSummary, action: WorkAction) => void;
  onEnd: (summary: MacroTaskSummary, button: HTMLButtonElement) => void;
  onDelete: (summary: MacroTaskSummary, button: HTMLButtonElement) => void;
}) {
  const { t } = useTranslation(["tasks", "shell", "macroHeader"]);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null), openingDialog = useRef(false);
  useEffect(() => { if (!active) setOpen(false); }, [active]);
  const action = summary.paused === null ? "pause" : "resume";
  const reason = !writable ? t("tasks:readonly") : undefined;
  return <DropdownMenu modal={false} open={open && active} onOpenChange={setOpen}>
    <DropdownMenuTrigger asChild>
      <Button ref={button} data-slot="macro-menu-button" className="size-6!" variant="ghost" size="icon-sm" aria-label={t("tasks:more")}><Ellipsis aria-hidden="true"/></Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent data-slot="macro-entry-menu" onCloseAutoFocus={event => {
      if (openingDialog.current) { event.preventDefault(); openingDialog.current = false; }
    }}>
      <Hint text={reason}><DropdownMenuItem data-slot={`macro-${action}-action`} disabled={!writable || busy} aria-busy={entries?.[action]?.phase === "pending" || undefined} onSelect={() => onAction(summary, action)}>{t(`shell:macroWork.${action}`)}</DropdownMenuItem></Hint>
      {summary.counts.unfinished > 0 && <Hint text={reason}><DropdownMenuItem data-slot="macro-end-action" disabled={!writable || busy} onSelect={() => {
        if (entries?.end?.phase === "unknown") onAction(summary, "end");
        else { openingDialog.current = true; onEnd(summary, button.current!); }
      }}>{t(canRetryEnd(entries?.end) ? "shell:macroWork.retryEnd" : "shell:macroWork.end")}</DropdownMenuItem></Hint>}
      <DropdownMenuSeparator/>
      <DropdownMenuItem data-slot="macro-delete-action" className="text-fail" onSelect={() => {
        openingDialog.current = true; onDelete(summary, button.current!);
      }}>{t("macroHeader:deletion.action")}</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
