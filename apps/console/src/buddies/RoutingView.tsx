import { reasonText } from "../i18n/reason";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { Hint } from "@/components/hint";
import { RouterSection, RouterField } from "./RouterSection";
import { Input } from "@/components/ui/input";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { MODEL_RESPONSE_WAIT_MAX, ROUTER_RETRY_INTERVAL_MAX, ROUTER_RETRY_INTERVAL_MIN, modelResponseWaitSeconds, retryIntervalSeconds, validExplorationEvery } from "../console-data";
import { routerListEqual } from "../draft";
import type { Configuration, Buddy, RoutingMode, Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { RoutingRecords } from "./RoutingRecords";
import { RouterList } from "./RouterList";
import type { BuddyLocation } from "./navigation";
import { addRouter, buddyQuad, moveRouter, removeRouter, routerLines, routerNote, withConfiguration } from "./roles";
import type { RouterNote } from "./roles";

const MODES = ["fast", "review"] as const satisfies readonly RoutingMode[];


/** The note under the list, written out. */
function noteText(note: RouterNote | null, buddies: Buddy[], t: TFunction<"buddies">): string | undefined {
  switch (note?.kind) {
    case undefined: return undefined;
    case "nobody": return t("roles.list.attention.none");
    case "head": {
      const buddy = buddies.find(candidate => candidate.buddyId === note.buddyId);
      return t("roles.list.attention.head", {
        name: buddy ? buddyQuad(buddy) : note.buddyId, reason: note.refusal ? t(`roles.refusal.${note.refusal}`) : t("roles.list.attention.gone"),
      });
    }
    case "unavailable": return note.reason ? reasonText(note.reason) : t("roles.list.attention.unavailable");
    case "failing": return t("roles.list.attention.failing", { n: note.count });
  }
}

/** Ordered routing buddies and the shared routing settings, all published through the page draft. */
export function RoutingView({ snapshot, editor, onNavigate }: {
  snapshot: Snapshot; editor: Editor; onNavigate: (next: BuddyLocation) => void;
}) {
  const { t } = useTranslation("buddies");
  const data = editor.view;
  const recorded = snapshot.configuration;
  // Settings that cannot be read leave nothing to show, whatever a retained draft still holds.
  const configuration = recorded === null ? null : data.configuration;
  const lines = routerLines(configuration?.routerBuddyIds ?? [], recorded?.routerBuddyIds ?? [], data.buddies, snapshot.routingHealth);
  const locked = !configuration ? t("roles.upgrade.locked")
    : !editor.sessionWritable ? t("pane.locked.session")
      : !editor.editing ? t("pane.locked.saving") : undefined;
  const changed = !!editor.draft && !!recorded && !!configuration;
  const unsaved = {
    list: changed && !routerListEqual(recorded.routerBuddyIds, configuration.routerBuddyIds),
    mode: changed && recorded.defaultRoutingMode !== configuration.defaultRoutingMode,
    responseWait: changed && recorded.modelResponseWaitSeconds !== configuration.modelResponseWaitSeconds,
    exploration: changed && recorded.explorationEvery !== configuration.explorationEvery,
    interval: changed && recorded.routerRetryIntervalSeconds !== configuration.routerRetryIntervalSeconds,
  };
  const mark = (is: boolean) => is ? t("unsaved") : undefined;
  const responseWait = configuration?.modelResponseWaitSeconds;
  const responseWaitInvalid = !!configuration && modelResponseWaitSeconds(responseWait) === null;
  const interval = configuration?.routerRetryIntervalSeconds;
  const intervalInvalid = !!configuration && retryIntervalSeconds(interval) === null;

  const every = configuration?.explorationEvery;
  const explorationInvalid = !!configuration && !validExplorationEvery(every);

  function change(patch: Partial<Configuration>) {
    if (!locked) editor.update(draft => withConfiguration(draft, patch));
  }
  return <div data-slot="routing-work" className="space-y-4">
      {!configuration && <p className="mb-3 text-xs text-fail" role="alert">
        {t("roles.upgrade.title")}</p>}

        <RouterList records={snapshot.routingHealth?.routers} unsaved={mark(unsaved.list)} lines={lines} buddies={data.buddies} mode={configuration?.defaultRoutingMode} locked={locked}
          note={configuration ? noteText(routerNote(data, snapshot.routingHealth), data.buddies, t) : undefined}
          onShow={buddyId => onNavigate({ view: "models", target: buddyId })}
          onAdd={buddyId => editor.update(draft => addRouter(draft, buddyId))}
          onMove={(buddyId, by) => editor.update(draft => moveRouter(draft, buddyId, by))}
          onRemove={buddyId => editor.update(draft => removeRouter(draft, buddyId))} />

      <RouterSection name={t("routerWork.settings")}><div className="router-settings"><RouterField name={t("roles.mode.name")} help={configuration ? <><p>{t(`roles.mode.${configuration.defaultRoutingMode}Consequence`)}</p><p className="mt-3">{t("roles.mode.explanation")}</p></> : t("roles.mode.explanation")} unsaved={mark(unsaved.mode)}>
        <Segmented aria-label={t("roles.mode.label")} value={configuration?.defaultRoutingMode ?? ""}
          onValueChange={value => change({ defaultRoutingMode: value as RoutingMode })}>
          {MODES.map(mode => <Hint key={mode} text={locked}>
            <SegmentedItem value={mode} aria-disabled={!!locked || undefined}>{t(`roles.mode.${mode}`)}</SegmentedItem>
          </Hint>)}
        </Segmented>
      </RouterField>

      <RouterField name={t("roles.responseWait.name")} help={t("roles.responseWait.explanation")} unsaved={mark(unsaved.responseWait)}>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Hint text={locked}>
            <Input type="number" inputMode="numeric" className="h-8 w-[84px] bg-card px-2.5 tabular-nums shadow-none"
              min={1} max={MODEL_RESPONSE_WAIT_MAX} step={1}
              value={responseWait === undefined || Number.isNaN(responseWait) ? "" : responseWait}
              readOnly={!!locked} aria-disabled={!!locked || undefined}
              aria-label={t("roles.responseWait.name")} aria-invalid={responseWaitInvalid || undefined}
              onChange={event => change({ modelResponseWaitSeconds: event.target.value === "" ? NaN : Number(event.target.value) })} />
          </Hint>
          <span className="text-xs text-ink2">{t("roles.retry.unit")}</span>
          <span className="text-xs text-fail" aria-live="polite">{responseWaitInvalid && t("roles.responseWait.invalid")}</span>
        </div>
      </RouterField>

      <RouterField name={t("roles.retry.name")} help={t("roles.retry.explanation")} unsaved={mark(unsaved.interval)}>
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1">
          <Hint text={locked}>
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
      </RouterField>

      <RouterField name={t("roles.exploration.name")} help={t("roles.exploration.explanation")} unsaved={mark(unsaved.exploration)}>
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <Segmented aria-label={t("roles.exploration.name")} value={!configuration ? "" : every === null ? "off" : "on"}
            onValueChange={value => change({ explorationEvery: value === "off" ? null : 5 })}>
            {(["on", "off"] as const).map(value => <Hint key={value} text={locked}>
              <SegmentedItem value={value} aria-disabled={!!locked || undefined}>{t(`roles.exploration.${value}`)}</SegmentedItem>
            </Hint>)}
          </Segmented>
          <span className="text-xs text-ink2">{t("routerWork.everyBefore")}</span>
          <Hint text={locked ?? (every === null ? t("roles.exploration.disabled") : undefined)}>
            <Input type="number" inputMode="numeric" min={2} step={1}
              className="h-8 w-[84px] bg-card px-2.5 tabular-nums shadow-none"
              value={every === undefined || every === null || Number.isNaN(every) ? "" : every}
              disabled={every === null} readOnly={!!locked} aria-disabled={!!locked || every === null || undefined}
              aria-label={t("roles.exploration.interval")} aria-invalid={explorationInvalid || undefined}
              onChange={event => change({ explorationEvery: event.target.value === "" ? NaN : Number(event.target.value) })} />
          </Hint>
          <span className="text-xs text-ink2">{t("routerWork.everyAfter")}</span>
          <span className="text-xs text-fail" aria-live="polite">{explorationInvalid && t("roles.exploration.invalid")}</span>
        </div>
      </RouterField>

      </div></RouterSection>
      <RouterSection name={t("roles.records.name")}>
        <RoutingRecords health={snapshot.routingHealth} />
      </RouterSection>
  </div>;
}
