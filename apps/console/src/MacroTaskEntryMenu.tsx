import { useEffect, useRef, useState } from "react";
import { Ellipsis } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "./components/ui/button";
import { Hint } from "./components/hint";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger } from "./components/ui/dropdown-menu";
import type { MacroTaskSummary } from "./macro-task-types";
import { stopScope, type MacroTaskStopEntry } from "./macro-task-stop";

export function MacroTaskEntryMenu({ summary, entry, writable, active, onStop, onRetry, onDismiss, onDelete }: {
  summary: MacroTaskSummary; entry?: MacroTaskStopEntry; writable: boolean; active: boolean;
  onStop: (summary: MacroTaskSummary, button: HTMLButtonElement) => void;
  onRetry: (summary: MacroTaskSummary) => void; onDismiss: (id: string) => void;
  onDelete: (summary: MacroTaskSummary, button: HTMLButtonElement) => void;
}) {
  const { t } = useTranslation(["tasks", "shell", "macroHeader"]);
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null), openingDialog = useRef(false);
  useEffect(() => { if (!active) setOpen(false); }, [active]);
  const stopping = entry?.phase === "stopping";
  const scope = stopScope(summary);
  const reason = !writable ? t("tasks:readonly") : stopping ? t("tasks:stopping") : scope.roots + scope.helpers === 0 ? t("shell:macroStop.noActive") : undefined;
  return <DropdownMenu modal={false} open={open && active} onOpenChange={setOpen}>
    <DropdownMenuTrigger asChild>
      <Button ref={button} data-slot="macro-menu-button" className="size-6!" variant="ghost" size="icon-sm" aria-label={t("tasks:more")}><Ellipsis aria-hidden="true"/></Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent data-slot="macro-entry-menu" onCloseAutoFocus={event => {
      if (openingDialog.current) { event.preventDefault(); openingDialog.current = false; }
    }}>
      <Hint text={reason}><DropdownMenuItem data-slot="macro-stop-action" aria-label={t("shell:macroStop.title")} disabled={!!reason} onSelect={() => {
        openingDialog.current = true; onStop(summary, button.current!);
      }}>{t("tasks:stop")}</DropdownMenuItem></Hint>
      {entry?.phase === "unknown" && writable && <DropdownMenuItem onSelect={() => onRetry(summary)}>{t("shell:macroStop.retry")}</DropdownMenuItem>}
      {entry?.phase === "refused" && <DropdownMenuItem onSelect={() => onDismiss(summary.macroTaskId)}>{t("shell:macroStop.dismiss")}</DropdownMenuItem>}
      <DropdownMenuSeparator/>
      <DropdownMenuItem data-slot="macro-delete-action" className="text-fail" onSelect={() => {
        openingDialog.current = true; onDelete(summary, button.current!);
      }}>{t("macroHeader:deletion.action")}</DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>;
}
