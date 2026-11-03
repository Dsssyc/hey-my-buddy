import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Dialog } from "radix-ui";
import remarkBreaks from "remark-breaks";
import { defaultRemarkPlugins } from "streamdown";
import { Target, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import { errorText, isAbortError } from "./api";
import type { MacroTaskGoal } from "./macro-task-types";
import { MessageResponse } from "./session-view/Markdown";
import { formatMomentMinutes } from "./i18n/format";
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
      const top = anchor.bottom + 4;
      setGeometry({ top: top - bounds.top, "--goal-top": `${top}px`, "--goal-left": `${Math.max(0, anchor.left - bounds.left - inset)}px`,
        "--goal-right": `${Math.max(0, bounds.right - inset - anchor.right)}px`,
        "--goal-start-y": `${anchor.bottom - top}px` } as CSSProperties);
    }
    measure();
    const observer = new ResizeObserver(measure); observer.observe(header);
    window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, [open]);
  return <Dialog.Root modal={false} open={open} onOpenChange={setOpen}>
    <Hint text={t("goalHint")}><Dialog.Trigger asChild>
      <Button ref={trigger} type="button" variant="ghost" size="icon-sm" data-slot="macro-goal-trigger" className="goal-trigger" aria-label={t("goalHint")}><Target aria-hidden="true"/></Button>
    </Dialog.Trigger></Hint>
    <Dialog.Content ref={content} data-slot="macro-goal-card" style={geometry} className="goal-card" aria-describedby={undefined}
      onOpenAutoFocus={event => { event.preventDefault(); content.current?.focus(); }}
      onCloseAutoFocus={event => { event.preventDefault(); trigger.current?.focus(); }} tabIndex={-1}>
      <Dialog.Close asChild><Button variant="ghost" size="icon-sm" aria-label={t("goalClose")} className="goal-close"><X aria-hidden="true"/></Button></Dialog.Close>
      <div data-slot="macro-goal-scroll" className="goal-scroll" data-history={!!goals && goals.length > 1}>
        <div className="goal-main">
          <div className="goal-heading">
            <Dialog.Title className="text-base font-semibold">{t("goalTitle")}</Dialog.Title>
            <span className="text-xs text-ink3 tabular-nums">{t("goalRevision", { revision: selected.revision })} · {formatMomentMinutes(Date.parse(selected.createdAt), Date.now())}</span>
          </div>
          <div data-slot="macro-goal-body" className="goal-body"><MessageResponse remarkPlugins={goalRemarkPlugins}>{selected.body}</MessageResponse></div>
          {goals === null && !error && <p role="status" className="mt-3 text-xs text-ink3">{t("goalLoading")}</p>}
          {error && <p role="alert" className="mt-3 text-xs text-fail">{error}</p>}
        </div>
        {goals && goals.length > 1 && <nav aria-label={t("goalHistory")} className="goal-history">
          <h3 className="goal-heading text-xs font-semibold text-ink3">{t("goalHistory")}</h3>
          <div className="goal-versions">
            {goals.map(item => <button type="button" key={item.revision} className="goal-version" data-current={item.revision === goals[0]!.revision} aria-pressed={item.revision === revision} onClick={() => setRevision(item.revision)}>
              <span aria-hidden="true" className="goal-version-dot"/>
              <span className="text-xs font-medium">{t("goalRevision", { revision: item.revision })} · <time dateTime={item.createdAt} className="text-ink3 tabular-nums">{formatMomentMinutes(Date.parse(item.createdAt), Date.now())}</time></span>
            </button>)}
          </div>
        </nav>}
      </div>
    </Dialog.Content>
  </Dialog.Root>;
}
