import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Section } from "@/components/section";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { reasonText } from "../i18n/reason";
import type { Configuration, RoutingBudget, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { buddyQuad, withConfiguration } from "./roles";

const BUDGETS = ["brief", "standard", "deep"] as const satisfies readonly RoutingBudget[];

/** Settings for the user-selected buddy that builds public model profiles. */
export function ProfileSettingsView({ snapshot, editor }: { snapshot: Snapshot; editor: Editor }) {
  const { t } = useTranslation("buddies");
  const [choosing, setChoosing] = useState(false);
  const [query, setQuery] = useState("");
  const config = snapshot.configuration === null ? null : editor.view.configuration;
  const locked = !config ? t("roles.upgrade.locked")
    : !editor.sessionWritable ? t("pane.locked.session")
      : !editor.editing ? t("pane.locked.saving") : undefined;
  const selected = editor.view.buddies.find(buddy => buddy.buddyId === config?.profileBuddyId);
  const candidates = editor.view.buddies.filter(buddy => buddy.enabled && buddy.available &&
    snapshot.harnesses?.some(harness => harness.harness === buddy.harness && harness.permissions?.web === "enforced"));
  const dirtyBuddy = !!config && !!snapshot.configuration && config.profileBuddyId !== snapshot.configuration.profileBuddyId;
  const dirtyBudget = !!config && !!snapshot.configuration && config.profileBudget !== snapshot.configuration.profileBudget;

  function change(patch: Partial<Configuration>) {
    if (!locked) editor.update(draft => withConfiguration(draft, patch));
  }

  return <div data-slot="profile-plane">
    <h3 className="px-6 pt-6 pb-2 text-base font-semibold">{t("profile.title")}</h3>
    <Section name={t("profile.buddy")} unsaved={dirtyBuddy ? t("unsaved") : undefined}>
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 font-mono text-xs wrap-anywhere">{selected ? buddyQuad(selected) : config?.profileBuddyId ?? t("profile.none")}</span>
        <Hint text={locked}><Button variant="outline" size="sm" disabled={!!locked} onClick={() => setChoosing(true)}>{t("profile.choose")}</Button></Hint>
      </div>
      {snapshot.configuration?.profileUnavailableReason && <p role="status" className="mt-2 text-xs text-ink2">{reasonText(snapshot.configuration.profileUnavailableReason)}</p>}
      <p data-slot="profile-consequence" className="mt-2 text-xs text-ink3">{t("profile.dataFlow")}</p>
    </Section>
    <Section name={t("profile.budget")} unsaved={dirtyBudget ? t("unsaved") : undefined}>
      <Segmented aria-label={t("profile.budget")} value={config?.profileBudget ?? ""}
        onValueChange={value => change({ profileBudget: value as RoutingBudget })}>
        {BUDGETS.map(budget => <Hint key={budget} text={locked}>
          <SegmentedItem value={budget} aria-disabled={!!locked || undefined}>{t(`roles.budget.${budget}`)}</SegmentedItem>
        </Hint>)}
      </Segmented>
    </Section>
    <Dialog open={choosing} onOpenChange={setChoosing}><DialogContent aria-describedby={undefined} className="sm:max-w-xl">
      <DialogTitle>{t("profile.choose")}</DialogTitle>
      <Input type="search" aria-label={t("profile.search")} value={query} onChange={event => setQuery(event.target.value)} />
      <ScrollArea className="h-72"><ul className="divide-y divide-line">
        <li><button className="w-full px-2 py-3 text-left hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none"
          onClick={() => { change({ profileBuddyId: null }); setChoosing(false); }}>{t("profile.none")}</button></li>
        {candidates.filter(buddy => buddyQuad(buddy).toLowerCase().includes(query.trim().toLowerCase())).map(buddy =>
          <li key={buddy.buddyId}><button className="w-full px-2 py-3 text-left font-mono text-xs wrap-anywhere hover:bg-sunk transition-colors duration-150 motion-reduce:transition-none"
            onClick={() => { change({ profileBuddyId: buddy.buddyId }); setChoosing(false); }}>{buddyQuad(buddy)}</button></li>)}
        {candidates.length === 0 && <li className="px-2 py-3 text-xs text-ink3">{t("profile.noCandidates")}</li>}
      </ul></ScrollArea>
    </DialogContent></Dialog>
  </div>;
}
