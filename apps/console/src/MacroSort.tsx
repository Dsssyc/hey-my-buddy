import { useTranslation } from "react-i18next";
import { ArrowDownWideNarrow } from "lucide-react";
import type { MacroTaskSort } from "./macro-task-types";
import { Hint } from "./components/hint";
import { Button } from "./components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuTrigger, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuLabel, DropdownMenuSeparator } from "./components/ui/dropdown-menu";

export function MacroSort({ sort, onChange }: { sort: MacroTaskSort; onChange: (sort: MacroTaskSort) => void }) {
  const { t } = useTranslation("tasks");
  const [basis, direction] = sort.split("-") as ["activity" | "created", "desc" | "asc"];
  return <DropdownMenu modal={false}>
    <Hint text={t("sort.label")}><DropdownMenuTrigger asChild>
      <Button data-slot="macro-sort-button" className="size-6!" variant="ghost" size="icon-sm" aria-label={t("sort.label")}><ArrowDownWideNarrow aria-hidden="true"/></Button>
    </DropdownMenuTrigger></Hint>
    <DropdownMenuContent data-slot="macro-sort-menu" aria-label={t("sort.label")}>
      <DropdownMenuLabel>{t("sort.basis")}</DropdownMenuLabel>
      <DropdownMenuRadioGroup aria-label={t("sort.basis")} value={basis} onValueChange={value => onChange(`${value}-${direction}` as MacroTaskSort)}>
        {(["activity", "created"] as const).map(value => <DropdownMenuRadioItem key={value} value={value}>{t(`sort.${value}`)}</DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
      <DropdownMenuSeparator/>
      <DropdownMenuLabel>{t("sort.direction")}</DropdownMenuLabel>
      <DropdownMenuRadioGroup aria-label={t("sort.direction")} value={direction} onValueChange={value => onChange(`${basis}-${value}` as MacroTaskSort)}>
        {(["desc", "asc"] as const).map(value => <DropdownMenuRadioItem key={value} value={value}>{t(value === "desc" ? "sort.descending" : "sort.ascending")}</DropdownMenuRadioItem>)}
      </DropdownMenuRadioGroup>
    </DropdownMenuContent>
  </DropdownMenu>;
}
