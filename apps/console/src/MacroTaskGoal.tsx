import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Dialog } from "radix-ui";
import remarkBreaks from "remark-breaks";
import { defaultRemarkPlugins } from "streamdown";
import { X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import type { MacroTaskGoal } from "./macro-task-types";
import { MessageResponse } from "./session-view/Markdown";
import { formatDateTime, formatMomentMinutes } from "./i18n/format";
import { Hint } from "./components/hint";
import { Button } from "./components/ui/button";

const goalRemarkPlugins = [...Object.values(defaultRemarkPlugins), remarkBreaks];

/** A non-modal sheet stays over the lanes; Radix owns dismissal and focus return. */
export function MacroTaskGoal({ macroTaskId, goal, readGoals, active }: {
  macroTaskId: string; goal: MacroTaskGoal; readGoals: ConsoleApi["macroTaskGoals"]; active: boolean;
}) {
  const { t } = useTranslation("macroHeader");
  const [open, setOpen] = useState(false);
  const [goals, setGoals] = useState<MacroTaskGoal[] | null>(null);
  const [revision, setRevision] = useState(goal.revision);
  const [error, setError] = useState("");
  const [geometry, setGeometry] = useState<CSSProperties>({});
  const trigger = useRef<HTMLButtonElement>(null);
  const content = useRef<HTMLDivElement>(null);
  useEffect(() => { if (!active) setOpen(false); }, [active]);
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    setGoals(null); setError(""); setRevision(goal.revision);
    readGoals(macroTaskId, controller.signal).then(result => {
      if (!controller.signal.aborted) { setGoals(result.goals); setRevision(result.goals[0]!.revision); }
    }).catch(error => { if (!isAbortError(error) && !controller.signal.aborted) setError(errorText(error)); });
    return () => controller.abort();
  }, [open, macroTaskId, goal.revision, readGoals]);
  const selected = goals?.find(item => item.revision === revision) ?? goal;
  useLayoutEffect(() => {
    if (!open) return;
    const button = trigger.current!, header = button.closest("header")!;
    function measure() {
      const anchor = button.getBoundingClientRect(), bounds = header.getBoundingClientRect();
      const inset = bounds.width < 480 ? 12 : 24;
      setGeometry({ "--goal-top": `${bounds.bottom - 4}px`, "--goal-left": `${Math.max(0, anchor.left - bounds.left - inset)}px`,
        "--goal-right": `${Math.max(0, bounds.right - inset - anchor.right)}px`,
        "--goal-start-y": `${anchor.bottom - bounds.bottom + 4}px` } as CSSProperties);
    }
    measure();
    const observer = new ResizeObserver(measure); observer.observe(header);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [open]);
  return <Dialog.Root modal={false} open={open} onOpenChange={setOpen}>
    <div data-slot="macro-goal-fact" className="flex min-w-0 flex-[1_1_12rem] gap-1.5">
      <dt className="shrink-0 text-ink3">{t("goal")}</dt>
      <dd className="min-w-0 flex-1"><Dialog.Trigger asChild>
        <button ref={trigger} type="button" data-slot="macro-goal-trigger" className="goal-trigger block w-full truncate border-b-[1.5px] border-goal text-left text-ink outline-none transition-colors duration-150 hover:text-ink2 focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none">{goal.body.split(/\r?\n/, 1)[0]}</button>
      </Dialog.Trigger></dd>
    </div>
    <Dialog.Content ref={content} data-slot="macro-goal-card" style={geometry} className="goal-card" aria-describedby={undefined}
      onOpenAutoFocus={event => { event.preventDefault(); content.current?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }} tabIndex={-1}>
      <div className="flex items-center gap-2.5 px-6 pt-3 pb-2">
        <Dialog.Title className="text-base font-semibold">{t("goalTitle")}</Dialog.Title>
        <span className="text-xs text-ink3 tabular-nums">{t("goalRevision", { revision: selected.revision })} · {formatMomentMinutes(Date.parse(selected.createdAt), Date.now())}</span>
        <Hint text={t("goalClose")}><Dialog.Close asChild><Button variant="ghost" size="icon-sm" aria-label={t("goalClose")} className="ml-auto"><X aria-hidden="true"/></Button></Dialog.Close></Hint>
      </div>
      <div data-slot="macro-goal-scroll" className="goal-scroll">
        <div data-slot="macro-goal-body" className="min-w-0 text-sm leading-[1.85]"><MessageResponse remarkPlugins={goalRemarkPlugins}>{selected.body}</MessageResponse></div>
        <nav aria-label={t("goalHistory")} className="goal-history">
          <h3 className="mb-3 text-xs font-semibold text-ink3">{t("goalHistory")}</h3>
          {goals === null && !error && <p role="status" className="text-xs text-ink3">{t("goalLoading")}</p>}
          {error && <p role="alert" className="text-xs text-fail">{error}</p>}
          {goals?.map(item => <button type="button" key={item.revision} className="goal-version" data-current={item.revision === goals[0]!.revision} aria-pressed={item.revision === revision} onClick={() => setRevision(item.revision)}>
            <span aria-hidden="true" className="goal-version-dot"/>
            <span className="text-xs font-medium">{t("goalRevision", { revision: item.revision })}</span>
            <time dateTime={item.createdAt} className="ml-1.5 text-xs text-ink3 tabular-nums">{formatMomentMinutes(Date.parse(item.createdAt), Date.now())}</time>
            <span className="mt-0.5 block truncate text-xs text-ink2">{item.revision === goals[0]!.revision ? t("goalCurrent") : item.body.split(/\r?\n/, 1)[0]}</span>
            <span className="sr-only">{formatDateTime(Date.parse(item.createdAt))}</span>
          </button>)}
        </nav>
      </div>
    </Dialog.Content>
  </Dialog.Root>;
}
