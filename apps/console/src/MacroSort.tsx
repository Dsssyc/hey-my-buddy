import { useTranslation } from "react-i18next";
import { ArrowDownWideNarrow, ArrowUpNarrowWide, CalendarPlus, Check, History } from "lucide-react";
import { RadioGroup } from "radix-ui";
import type { MacroTaskSort } from "./macro-task-types";
import { Hint } from "./components/hint";
import { Button } from "./components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";

const basisIcons = { activity: History, created: CalendarPlus } as const;

export function MacroSort({ sort, onChange }: { sort: MacroTaskSort; onChange: (sort: MacroTaskSort) => void }) {
  const { t } = useTranslation("tasks");
  const [basis, direction] = sort.split("-") as ["activity" | "created", "desc" | "asc"];
  const BasisIcon = basisIcons[basis];
  const DirectionIcon = direction === "desc" ? ArrowDownWideNarrow : ArrowUpNarrowWide;
  const basisLabel = t("sort.basis", { basis: t(`sort.${basis}`) });
  const directionLabel = t(direction === "desc" ? "sort.descending" : "sort.ascending");
  return <>
    <Popover>
      <Hint text={basisLabel}><PopoverTrigger asChild>
        <Button data-slot="macro-sort-basis" variant="ghost" size="icon-sm" data-emphasized={basis !== "activity" || undefined} aria-label={basisLabel}><BasisIcon aria-hidden="true"/></Button>
      </PopoverTrigger></Hint>
      <PopoverContent data-slot="macro-sort-popover" align="end" side="bottom" className="w-48 p-2" aria-label={t("sort.label")}>
        <RadioGroup.Root aria-label={t("sort.label")} orientation="vertical" value={basis} onValueChange={value => onChange(`${value}-${direction}` as MacroTaskSort)} className="flex flex-col gap-1">
          {(["activity", "created"] as const).map(value => <RadioGroup.Item key={value} value={value} className="flex items-center gap-2 rounded-sm px-2 py-2 text-left text-sm outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset">
            <span className="size-4"><RadioGroup.Indicator><Check className="size-4" aria-hidden="true"/></RadioGroup.Indicator></span>{t(`sort.${value}`)}
          </RadioGroup.Item>)}
        </RadioGroup.Root>
      </PopoverContent>
    </Popover>
    <Hint text={directionLabel}><Button data-slot="macro-sort-direction" variant="ghost" size="icon-sm" data-emphasized={direction !== "desc" || undefined} aria-label={directionLabel} onClick={() => onChange(`${basis}-${direction === "desc" ? "asc" : "desc"}` as MacroTaskSort)}><DirectionIcon aria-hidden="true"/></Button></Hint>
  </>;
}
