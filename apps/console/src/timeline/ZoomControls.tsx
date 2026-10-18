import { cn } from "cn";
import { Minus, Plus, ScanLine } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "../components/ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "../components/ui/tooltip";

/** Both timeline views use the same gestures and the same order. */
export function ZoomControls({ onChange, atMinimum, atMaximum, className }: {
  onChange: (direction: -1 | 0 | 1) => void;
  atMinimum: boolean; atMaximum: boolean; className?: string;
}) {
  const { t } = useTranslation("tasks");
  return <div data-slot="timeline-zoom" className={cn("flex shrink-0 gap-1", className)}>
    {([[-1, Minus, "zoomOut", atMinimum], [1, Plus, "zoomIn", atMaximum], [0, ScanLine, "zoomFit", atMinimum]] as const).map(([direction, Icon, label, unavailable]) =>
      <Tooltip key={label}><TooltipTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={t(label)} aria-disabled={unavailable} onClick={() => onChange(direction)}><Icon size={16} strokeWidth={1.5}/></Button>
      </TooltipTrigger><TooltipContent>{t(label)}</TooltipContent></Tooltip>)}
  </div>;
}
