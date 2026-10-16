import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { StorageApplyResult, StorageCategory, StorageOrphanProcess } from "../api";
import { Hint } from "../components/hint";
import { Button } from "../components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger, Disclosure } from "../components/ui/collapsible";
import { StateMark } from "../components/ui/state-mark";
import { formatList, formatNumber } from "../i18n/format";
import { ALWAYS_PROTECTED, byteHint, byteText, categoryText, reasonText } from "./storage-display";

function Bytes({ value, hint }: { value: number; hint?: string }) {
  return <Hint text={hint ?? byteHint(value)}><span tabIndex={0} className="whitespace-nowrap tabular-nums">{byteText(value)}</span></Hint>;
}

export function StorageTable({ categories }: { categories: StorageCategory[] }) {
  const { t } = useTranslation("settingsStorage");
  const [open, setOpen] = useState<Set<string>>(new Set());
  return <table className="w-full text-left text-sm">
    <thead className="text-xs font-normal text-ink3"><tr className="border-b">
      <th className="pb-2 font-normal" scope="col">{t("category")}</th><th className="pb-2 pl-3 text-right font-normal" scope="col">{t("count")}</th>
      <th className="pb-2 pl-3 text-right font-normal" scope="col">{t("used")}</th><th className="pb-2 pl-3 text-right font-normal" scope="col">{t("reclaimable")}</th>
    </tr></thead>
    {categories.map(category => <Collapsible key={category.id} asChild open={open.has(category.id)} onOpenChange={nextOpen => setOpen(previous => {
      const next = new Set(previous); if (nextOpen) next.add(category.id); else next.delete(category.id); return next;
    })}><tbody>
      <tr className="border-b">
        <th scope="row" className="py-2.5 font-normal">
          {category.reasons.length ? <CollapsibleTrigger asChild><button type="button" className="group flex min-h-7 items-center gap-1 text-left text-ink2 transition-colors duration-150 hover:text-foreground motion-reduce:transition-none" aria-label={t("reasonsLabel", { category: categoryText(category.id) })}>
            <Disclosure open={open.has(category.id)} />{categoryText(category.id)}
          </button></CollapsibleTrigger> : <span className="block pl-5">{categoryText(category.id)}</span>}
        </th>
        <td className="pl-3 text-right tabular-nums text-ink2">{formatNumber(category.count)}</td>
        <td className="pl-3 text-right"><Bytes value={category.bytes} /></td>
        <td className={`pl-3 text-right ${category.reclaimableBytes > 0 ? "text-hand" : "text-ink3"}`}><Bytes value={category.reclaimableBytes} hint={ALWAYS_PROTECTED.has(category.id) ? t("protected") : category.reclaimableBytes ? undefined : t("none")} /></td>
      </tr>
      {category.reasons.length > 0 && <tr><td colSpan={4}><CollapsibleContent>
        <ul className="flex list-none flex-wrap gap-x-5 gap-y-2 bg-muted px-5 py-3 text-xs text-ink2">{category.reasons.map(code => <li key={code}>{reasonText(code)}</li>)}</ul>
      </CollapsibleContent></td></tr>}
    </tbody></Collapsible>)}
  </table>;
}

export function StorageOrphans({ processes }: { processes: StorageOrphanProcess[] }) {
  const { t } = useTranslation("settingsStorage");
  return <div data-slot="storage-orphans" className="space-y-2 text-xs">
    <Hint text={t("orphansHint")}><h3 tabIndex={0} className="flex w-fit items-center gap-2 font-normal text-ink2"><StateMark kind="hand" />{t("orphans", { n: formatNumber(processes.length) })}</h3></Hint>
    <ul className="space-y-2">{processes.map(process => <li key={process.pid} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
      <span>{process.kind === "daemon" ? t("daemon") : process.kind === "execution" ? t("execution") : process.kind || t("unknownKind")}</span>
      <code className="tabular-nums">{t("process", { pid: process.pid })}</code>
      <span className="min-w-0 break-all font-mono text-ink3">{formatList([process.stateDir, process.runtimeDir].filter((path): path is string => !!path))}</span>
    </li>)}</ul>
  </div>;
}

export function StorageOutcome({ result }: { result: StorageApplyResult }) {
  const { t } = useTranslation("settingsStorage");
  const [removed, setRemoved] = useState(false);
  const [skipped, setSkipped] = useState(false);
  return <div role="status" className="space-y-2 text-xs">
    <p className="flex items-center gap-2"><StateMark kind="done" />{t("removed", { bytes: byteText(result.removedBytes), n: formatNumber(result.removed.length) })}</p>
    {result.removed.length > 0 && <Collapsible open={removed} onOpenChange={setRemoved}><CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="px-0 text-xs"><Disclosure open={removed} />{t(removed ? "hideRemoved" : "showRemoved")}</Button></CollapsibleTrigger>
      <CollapsibleContent><ul className="space-y-2 py-2 font-mono text-ink2">{result.removed.map(item => <li key={item.id} className="break-all"><Hint text={byteHint(item.bytes)}><span tabIndex={0}>{t("item", { path: item.path, detail: byteText(item.bytes) })}</span></Hint></li>)}</ul></CollapsibleContent>
    </Collapsible>}
    {result.skipped.length > 0 && <Collapsible open={skipped} onOpenChange={setSkipped}>
      <p className="flex items-center gap-2"><StateMark kind="hand" />{t("skipped", { n: formatNumber(result.skipped.length) })}</p>
      <CollapsibleTrigger asChild><Button variant="ghost" size="sm" className="px-0 text-xs"><Disclosure open={skipped} />{t(skipped ? "hideSkipped" : "showSkipped")}</Button></CollapsibleTrigger>
      <CollapsibleContent><ul className="space-y-2 py-2 text-ink2">{result.skipped.map(item => <li key={item.id} className="break-all">{t("item", { path: item.path, detail: item.reasons.length ? formatList(item.reasons.map(reasonText)) : t("changed") })}</li>)}</ul></CollapsibleContent>
    </Collapsible>}
  </div>;
}
