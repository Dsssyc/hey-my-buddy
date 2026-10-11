import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { StateMark } from "@/components/ui/state-mark";
import type { HarnessTableRow, QuotaCell } from "./harness";
import { tableCell, tableHandle, tableHead, tableRow } from "./styles";

const cell = tableCell;
const dash = <span className="text-ink3">—</span>;

/** The DOM id of one harness's button in the table, so that an address can land on it. */
export function harnessRowId(adapter: string): string {
  return `harness-${adapter}`;
}

/**
 * The window nearest its limit: a thin bar, the share used and when it resets.
 * A window whose use is not known draws no bar, so it cannot read as 0%.
 */
function Quota({ cell: quota }: { cell: QuotaCell }) {
  const { t } = useTranslation("buddies");
  return <div className="flex items-center gap-2.5 text-xs tabular-nums">
    {quota.used !== null && <div className="h-1.5 w-[120px] shrink-0 bg-border" aria-hidden="true">
      <div className={cn("h-full", quota.hot ? "bg-hand-mark" : "bg-past")} style={{ width: `${Math.min(100, quota.used)}%` }} />
    </div>}
    <span className={cn(quota.hot && "text-hand")}>{quota.text}</span>
    {quota.resets && <span className="whitespace-nowrap text-ink3">{t("harness.quota.reset", { time: quota.resets })}</span>}
    {quota.stale && <span className="text-ink3">{t("harness.quota.stale")}</span>}
  </div>;
}

/**
 * The harness table. A row is one harness; the selected row is the one the side
 * column shows, and it is tinted where the two stand side by side. A harness
 * that is not found is written in the lighter ink.
 */
export function HarnessTable({ rows, current, onSelect }: {
  rows: HarnessTableRow[]; current: string | null; onSelect: (adapter: string) => void;
}) {
  const { t } = useTranslation("buddies");
  const head = tableHead;
  return <table className="w-full min-w-[720px] border-separate border-spacing-0" aria-label={t("harness.table")}>
    <thead className="sticky top-0 z-10 bg-card">
      <tr>
        <th scope="col" className={cn(head, "w-[176px] pl-6")}>{t("harness.columns.harness")}</th>
        <th scope="col" className={cn(head, "w-[132px]")}>{t("harness.columns.status")}</th>
        <th scope="col" className={cn(head, "w-[112px]")}>{t("harness.columns.version")}</th>
        <th scope="col" className={cn(head, "w-[176px]")}>{t("harness.columns.account")}</th>
        <th scope="col" className={cn(head, "pr-6")}>{t("harness.columns.quota")}</th>
      </tr>
    </thead>
    <tbody>
      {rows.map(row => {
        const selected = row.adapter === current;
        return <tr key={row.adapter} data-selected={selected} onClick={() => onSelect(row.adapter)}
          className={cn(tableRow, !row.found && "text-ink3")}>
          <td className={cn(cell, "pl-6 whitespace-nowrap")}>
            <button type="button" id={harnessRowId(row.adapter)} className={cn(tableHandle, "font-medium")} aria-pressed={selected}>
              {row.name}
            </button>
          </td>
          <td className={cell}>
            <span className="inline-flex items-center gap-2 whitespace-nowrap">
              <StateMark kind={row.found ? "live" : "idle"} />{row.statusText}
            </span>
          </td>
          <td className={cn(cell, "font-mono text-xs whitespace-nowrap tabular-nums")}>{row.version ?? dash}</td>
          <td className={cn(cell, "whitespace-nowrap")}>
            {row.account
              ? <>{row.account.source}{row.account.state && <span className="ml-2 text-xs text-ink3">{row.account.state}</span>}</>
              : dash}
          </td>
          <td className={cn(cell, "pr-6")}>{row.quota ? <Quota cell={row.quota} /> : dash}</td>
        </tr>;
      })}
    </tbody>
  </table>;
}
