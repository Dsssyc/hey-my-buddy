import type { TFunction } from "i18next";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { Section } from "@/components/section";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { ROUTER_RETRY_INTERVAL_MAX, ROUTER_RETRY_INTERVAL_MIN, retryIntervalSeconds } from "../console-data";
import { routerListEqual } from "../draft";
import { locale } from "../i18n";
import type { Configuration, Profile, RoutingBudget, RoutingMode, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { RolesRecords } from "./RolesRecords";
import { RouterList, routerLineId } from "./RouterList";
import type { BuddyLocation } from "./navigation";
import { addRouter, buddyQuad, moveRouter, removeRouter, routerLines, routerNote, withConfiguration } from "./roles";
import type { RouterNote } from "./roles";

const MODES = ["fast", "review"] as const satisfies readonly RoutingMode[];
const BUDGETS = ["brief", "standard", "deep"] as const satisfies readonly RoutingBudget[];

/** The sections an address can land on. */
const LIST_ID = "router-list";
const RECORDS_ID = "router-records";

/** The note under the list, written out. */
function noteText(note: RouterNote | null, profiles: Profile[], t: TFunction<"buddies">): string | undefined {
  switch (note?.kind) {
    case undefined: return undefined;
    case "nobody": return t("roles.list.attention.none");
    case "head": {
      const profile = profiles.find(candidate => candidate.profileId === note.profileId);
      return t("roles.list.attention.head", {
        name: profile ? buddyQuad(profile) : note.profileId, reason: note.refusal ?? t("roles.list.attention.gone"),
      });
    }
    case "unavailable": return note.code ? t("roles.list.attention.unavailableCode", { code: note.code }) : t("roles.list.attention.unavailable");
    case "failing": return t("roles.list.attention.failing", { n: note.count });
  }
}

/**
 * The roles of the Buddy page. It is one block, Router: the ordered list of
 * buddies that serve in that role, the settings of the role, and what the
 * snapshot records of routing. Every change goes into the page's draft, which
 * the save bar publishes; the records are read only.
 */
export function RolesView({ snapshot, editor, active, location, onNavigate, hidden }: {
  snapshot: Snapshot; editor: Editor;
  /** The page is in front and this is the view it shows. */
  active: boolean; location: BuddyLocation; onNavigate: (next: BuddyLocation) => void; hidden: boolean;
}) {
  const { t } = useTranslation("buddies");
  const data = editor.view;
  const recorded = snapshot.configuration;
  // Settings that cannot be read leave nothing to show, whatever a retained draft still holds.
  const configuration = recorded === null ? null : data.configuration;
  const lines = routerLines(configuration?.routerProfileIds ?? [], recorded?.routerProfileIds ?? [], data.profiles, snapshot.routingHealth);
  const locked = !configuration ? t("roles.upgrade.locked")
    : !editor.sessionWritable ? t("pane.locked.session")
      : !editor.editing ? t("pane.locked.saving") : undefined;
  const changed = !!editor.draft && !!recorded && !!configuration;
  const unsaved = {
    list: changed && !routerListEqual(recorded.routerProfileIds, configuration.routerProfileIds),
    mode: changed && recorded.defaultRoutingMode !== configuration.defaultRoutingMode,
    budget: changed && recorded.routingBudget !== configuration.routingBudget,
    interval: changed && recorded.routerRetryIntervalSeconds !== configuration.routerRetryIntervalSeconds,
  };
  const mark = (is: boolean) => is ? t("unsaved") : undefined;
  /** What a control says when it cannot be used, with its own words after. */
  const explain = (own?: string) => locked ? (own ? `${locked}。${own}` : locked) : own;
  const limits = recorded?.routingBudgetLimits;
  const interval = configuration?.routerRetryIntervalSeconds;
  const intervalInvalid = !!configuration && retryIntervalSeconds(interval) === null;

  const handledLocation = useRef<BuddyLocation | null>(null);
  // An address that names a part of this view lands on it: the line of the buddy serving now, or the list when none does.
  useEffect(() => {
    if (!active || !location.target || handledLocation.current === location) return;
    const current = lines.find(line => line.status?.kind === "current");
    const target = document.getElementById(location.target === "health" ? RECORDS_ID : current ? routerLineId(current.profileId) : LIST_ID);
    target?.focus(); target?.scrollIntoView?.({ block: "nearest" });
    handledLocation.current = location;
  }, [location, active]);

  function change(patch: Partial<Configuration>) {
    if (!locked) editor.update(draft => withConfiguration(draft, patch));
  }
  return <section hidden={hidden} aria-label={t("sections.roles")} className="flex min-h-0 flex-1 flex-col bg-card">
    <ScrollArea className="min-h-0 flex-1">
      <h2 className="px-6 pt-5 pb-2 text-base font-semibold">{t("roles.heading")}</h2>
      {!configuration && <p className="mx-6 mb-3 border-l-2 border-fail pl-3 text-xs text-fail" role="alert">
        {t("roles.upgrade.title")}{snapshot.configurationError?.message && `：${snapshot.configurationError.message}`}</p>}

      <Section name={t("roles.list.name")} unsaved={mark(unsaved.list)} id={LIST_ID} tabIndex={-1}>
        <RouterList lines={lines} profiles={data.profiles} mode={configuration?.defaultRoutingMode} locked={locked}
          note={configuration ? noteText(routerNote(data, snapshot.routingHealth), data.profiles, t) : undefined}
          onShow={profileId => onNavigate({ view: "models", target: profileId })}
          onAdd={profileId => editor.update(draft => addRouter(draft, profileId))}
          onMove={(profileId, by) => editor.update(draft => moveRouter(draft, profileId, by))}
          onRemove={profileId => editor.update(draft => removeRouter(draft, profileId))} />
      </Section>

      <Section name={t("roles.mode.name")} hint={t("roles.mode.nameHint")} unsaved={mark(unsaved.mode)}>
        <Segmented aria-label={t("roles.mode.label")} value={configuration?.defaultRoutingMode ?? ""}
          onValueChange={value => change({ defaultRoutingMode: value as RoutingMode })}>
          {MODES.map(mode => <Hint key={mode} text={explain(t(`roles.mode.${mode}Hint`))}>
            <SegmentedItem value={mode} aria-disabled={!!locked || undefined}>{t(`roles.mode.${mode}`)}</SegmentedItem>
          </Hint>)}
        </Segmented>
      </Section>

      <Section name={t("roles.budget.name")} hint={t("roles.budget.nameHint")} unsaved={mark(unsaved.budget)}>
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <Segmented aria-label={t("roles.budget.label")} value={configuration?.routingBudget ?? ""}
            onValueChange={value => change({ routingBudget: value as RoutingBudget })}>
            {BUDGETS.map(budget => <Hint key={budget} text={explain()}>
              <SegmentedItem value={budget} aria-disabled={!!locked || undefined}>{t(`roles.budget.${budget}`)}</SegmentedItem>
            </Hint>)}
          </Segmented>
          {/* The limits are recorded for one level only; another level chosen in the draft has none to show. */}
          {limits && limits.preset === configuration?.routingBudget && <span className="flex flex-wrap gap-x-3 text-xs text-ink2 tabular-nums">
            <span>{t("roles.budget.seconds", { n: limits.timeoutSeconds.toLocaleString(locale) })}</span>
            <span>{t("roles.budget.toolCalls", { n: limits.toolCalls.toLocaleString(locale) })}</span>
            <span>{t("roles.budget.bytes", { n: limits.bytesRead.toLocaleString(locale) })}</span>
          </span>}
        </div>
      </Section>

      <Section name={t("roles.retry.name")} hint={t("roles.retry.nameHint")} unsaved={mark(unsaved.interval)}>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Hint text={explain()}>
            <Input type="number" inputMode="numeric" className="h-8 w-[84px] bg-card px-2.5 tabular-nums shadow-none"
              min={ROUTER_RETRY_INTERVAL_MIN} max={ROUTER_RETRY_INTERVAL_MAX} step={1}
              value={interval === undefined || Number.isNaN(interval) ? "" : interval}
              readOnly={!!locked} aria-disabled={!!locked || undefined}
              aria-label={t("roles.retry.label")} aria-invalid={intervalInvalid || undefined}
              onChange={event => change({ routerRetryIntervalSeconds: event.target.value === "" ? NaN : Number(event.target.value) })} />
          </Hint>
          <span className="text-ink2">{t("roles.retry.unit")}</span>
          <span className="text-xs text-fail" aria-live="polite">
            {intervalInvalid && t("roles.retry.range", { min: ROUTER_RETRY_INTERVAL_MIN, max: ROUTER_RETRY_INTERVAL_MAX })}
          </span>
        </div>
      </Section>

      <Section name={t("roles.records.name")} id={RECORDS_ID} tabIndex={-1}>
        <RolesRecords health={snapshot.routingHealth} />
      </Section>
    </ScrollArea>
  </section>;
}
