import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogTitle } from "@/components/ui/dialog";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Hint } from "@/components/hint";
import { UnsavedMark } from "@/components/ui/state-mark";
import { modelServiceKey } from "../console-data";
import { stageModelCorrespondence } from "../draft";
import { formatList } from "../i18n/format";
import type { ConsoleView, KindEvaluation, ModelCorrespondenceChange } from "../types";
import type { Editor } from "../use-editor";

type Action = ModelCorrespondenceChange["action"];

/** A name's current service mapping is frozen while its explanation is open. */
export function EvaluationNames({ evaluation, data, editor }: { evaluation: KindEvaluation; data: ConsoleView; editor: Editor }) {
  const { t } = useTranslation("buddies");
  const [intent, setIntent] = useState<{ action: Action; key: string; correspondenceId: string; explanation: boolean } | null>(null);
  const [target, setTarget] = useState("");
  const entries = data.modelCorrespondences.filter(entry => entry.harness === evaluation.harness && entry.modelId === evaluation.modelId);
  const choices = intent?.action === "undo" ? entries.filter(entry => entry.canUndo) : entries;
  const entry = intent ? entries.find(row => modelServiceKey(row) === intent.key && row.correspondenceId === intent.correspondenceId) : undefined;
  const lock = !editor.sessionWritable ? t("pane.locked.session") : !editor.editing ? t("pane.locked.saving") : undefined;
  const pending = (data.modelCorrespondenceChanges ?? []).filter(change => entries.some(row => modelServiceKey(row) === modelServiceKey(change)));
  const actionLabel = (action: Action) => t(`modelProfile.${action}`);
  const names = [...new Set(evaluation.modelNames.filter(name => name.harness === evaluation.harness).map(name => name.model))];
  function begin(action: Action) {
    const available = action === "undo" ? entries.filter(row => row.canUndo) : entries;
    const first = available.length === 1 ? available[0] : undefined;
    setTarget("");
    setIntent({ action, key: first ? modelServiceKey(first) : "", correspondenceId: first?.correspondenceId ?? "", explanation: !!first });
  }
  function confirm() {
    if (lock || !entry || !intent || intent.action === "undo" && !entry.canUndo || intent.action === "map" && !target) return;
    const service = { harness: entry.harness, provider: entry.provider, model: entry.model };
    const change: ModelCorrespondenceChange = intent.action === "undo" ? { ...service, action: "undo", correspondenceId: entry.correspondenceId }
      : intent.action === "map" ? { ...service, action: "map", modelId: target }
        : { ...service, action: "generation", displayName: data.models.find(row => row.modelId === entry.modelId)!.displayName };
    editor.update(draft => stageModelCorrespondence(draft, change));
    setIntent(null);
  }
  return <div data-slot="evaluation-names" className="border-t py-4 text-xs">
    <div className="flex items-center gap-3"><span className="text-ink3">{t("evaluation.names")}</span><span className="min-w-0 flex-1 wrap-anywhere">{formatList(names)}</span>
      {pending.length > 0 && <UnsavedMark label={t("unsaved")} />}
      <DropdownMenu><DropdownMenuTrigger asChild><Button variant="ghost" size="sm" className="text-lg" aria-label={t("evaluation.modelActions")}>…</Button></DropdownMenuTrigger>
        <DropdownMenuContent>{(["generation", "map", "undo"] as const).map(action => <DropdownMenuItem key={action} disabled={!!lock || !entries.length || action === "undo" && !entries.some(row => row.canUndo)} onSelect={() => begin(action)}>{actionLabel(action)}</DropdownMenuItem>)}</DropdownMenuContent>
      </DropdownMenu>
    </div>
    {pending.map(change => <p key={modelServiceKey(change)} role="status" className="mt-2 text-ink2">{change.model} · {change.action === "undo" ? t("modelProfile.pendingUndo") : change.action === "generation" ? t("modelProfile.pendingGeneration", { name: change.displayName }) : t("modelProfile.pendingMap", { name: data.models.find(row => row.modelId === change.modelId)!.displayName })}</p>)}
    <Dialog open={intent !== null} onOpenChange={open => { if (!open) setIntent(null); }}><DialogContent>
      <DialogTitle>{intent ? actionLabel(intent.action) : ""}</DialogTitle>
      <DialogDescription>{intent?.explanation && entry ? t(`evaluation.explain.${intent.action}`, { name: entry.model, target: data.models.find(row => row.modelId === target)?.displayName ?? "" }) : t("evaluation.chooseName")}</DialogDescription>
      {!intent?.explanation && <fieldset className="space-y-2"><legend className="sr-only">{t("evaluation.chooseName")}</legend>{choices.map(row => <label key={modelServiceKey(row)} className="flex items-center gap-3 py-2 text-sm">
        <input type="radio" name="evaluation-name" checked={intent?.key === modelServiceKey(row)} onChange={() => setIntent(value => value && ({ ...value, key: modelServiceKey(row), correspondenceId: row.correspondenceId }))} />
        <span>{row.model}{choices.filter(other => other.model === row.model).length > 1 ? ` · ${row.provider}` : ""}</span>
      </label>)}</fieldset>}
      {intent?.action === "map" && <label className="space-y-2 text-xs"><span>{t("evaluation.targetModel")}</span><select aria-label={t("evaluation.targetModel")} className="h-9 w-full border bg-card px-2 text-sm" value={target} onChange={event => setTarget(event.target.value)}>
        <option value="">{t("evaluation.chooseModel")}</option>{data.models.filter(row => row.modelId !== evaluation.modelId).map(row => <option key={row.modelId} value={row.modelId}>{row.displayName}{data.models.some(other => other.modelId !== row.modelId && other.displayName === row.displayName) ? ` · ${row.modelId}` : ""}</option>)}
      </select></label>}
      <DialogFooter><Button variant="outline" onClick={() => setIntent(null)}>{t("evaluation.cancel")}</Button>
        <Hint text={lock}><Button disabled={!!lock || !entry || intent?.action === "map" && !target || intent?.action === "undo" && !entry.canUndo} onClick={() => intent?.explanation ? confirm() : setIntent(value => value && ({ ...value, explanation: true }))}>{intent?.explanation ? t("modelProfile.apply") : t("evaluation.continue")}</Button></Hint>
      </DialogFooter>
    </DialogContent></Dialog>
  </div>;
}
