import { useTranslation } from "react-i18next";
import { ArrowDownWideNarrow, ArrowUpNarrowWide, Check } from "lucide-react";
import { RadioGroup } from "radix-ui";
import type { MacroTaskSort } from "./macro-task-types";
import { Hint } from "./components/hint";
import { Button } from "./components/ui/button";
import { Popover, PopoverContent, PopoverTrigger } from "./components/ui/popover";
import { Segmented, SegmentedItem } from "./components/ui/segmented";

export function MacroSort({ sort, onChange }: { sort: MacroTaskSort; onChange: (sort: MacroTaskSort) => void }) {
  const { t } = useTranslation("tasks");
  const [basis, direction] = sort.split("-") as ["activity" | "created", "desc" | "asc"];
  const Icon = direction === "desc" ? ArrowDownWideNarrow : ArrowUpNarrowWide;
  const label = t("sort.current", { basis: t(`sort.${basis}`), order: t(direction === "desc" ? "sort.newest" : "sort.oldest") });
  return <Popover>
    <Hint text={label}><PopoverTrigger asChild>
      <Button data-slot="macro-sort-button" variant="ghost" size="icon-sm" data-emphasized={sort !== "activity-desc" || undefined} aria-label={label}><Icon aria-hidden="true"/></Button>
    </PopoverTrigger></Hint>
    <PopoverContent data-slot="macro-sort-popover" align="end" side="bottom" className="w-56 p-2" aria-label={t("sort.label")}>
      <RadioGroup.Root aria-label={t("sort.label")} orientation="vertical" value={basis} onValueChange={value => onChange(`${value}-${direction}` as MacroTaskSort)} className="flex flex-col gap-1">
        {(["activity", "created"] as const).map(value => <RadioGroup.Item key={value} value={value} className="flex items-center gap-2 rounded-sm px-2 py-2 text-left text-sm outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset">
          <span className="size-4"><RadioGroup.Indicator><Check className="size-4" aria-hidden="true"/></RadioGroup.Indicator></span>{t(`sort.${value}`)}
        </RadioGroup.Item>)}
      </RadioGroup.Root>
      <div role="separator" className="my-2 border-t"/>
      <Segmented aria-label={t("sort.order")} value={direction} onValueChange={value => onChange(`${basis}-${value}` as MacroTaskSort)} className="flex w-full">
        <SegmentedItem value="desc" className="flex-1 px-2">{t("sort.newest")}</SegmentedItem>
        <SegmentedItem value="asc" className="flex-1 px-2">{t("sort.oldest")}</SegmentedItem>
      </Segmented>
    </PopoverContent>
  </Popover>;
}
