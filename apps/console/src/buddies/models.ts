import type { TFunction } from "i18next";
import { modelServiceSearchText, harnessGroups } from "../buddy-display";
import { MODEL_SERVICE_CONCURRENCY_DEFAULT, concurrencyEntryFor, modelServicePending, modelServices } from "../console-data";
import type { ModelServiceGroup } from "../console-data";
import { effortText } from "../profile-display";
import type { ConsoleView, HarnessHealth, PreferenceMode, Profile, Snapshot } from "../types";

/** One buddy on the reasoning-effort scale of one model. */
export type BuddyChip = {
  profile: Profile;
  effort: string;
  /** This buddy's preference. */
  preference: PreferenceMode | null;
  /** The draft differs from what is published: the switch or the buddy's own preference. */
  unsaved: boolean;
};

/** One model row within a harness section. */
export type ModelRow = {
  key: string;
  modelService: ModelServiceGroup;
  chips: BuddyChip[];
  available: boolean;
  /** Why its buddies are unavailable, as recorded; empty when nothing was recorded. */
  reasons: string[];
  pending: boolean;
  pendingSince: string | null;
  fresh: boolean;
  /** The draft's limit; what the user typed stays here even while it is not a valid limit. */
  limit: number;
  /** Published occupancy; null while the board has not reported this model. */
  occupancy: number | null;
};

/** The rows of one harness, which fold into a single row. */
export type HarnessBlock = {
  adapter: string;
  name: string;
  /** Set only when the harness makes its buddies unavailable: its recorded state, or `unavailable`. */
  state: string | null;
  reasons: string[];
  folded: boolean;
  models: ModelRow[];
  providers: string[];
  enabled: number;
  total: number;
};

export type ModelFilters = { query: string; enabledOnly: boolean; showUnavailable: boolean };

/**
 * The models the two filters and the search leave. A model is listed when at
 * least one of its buddies passes: 只看已启用 keeps models with an enabled buddy,
 * and 显示不可用 adds the models that have no available buddy at all.
 */
export function listedModels(profiles: Profile[], { query, enabledOnly, showUnavailable }: ModelFilters): ModelServiceGroup[] {
  const needle = query.trim().toLowerCase();
  return modelServices(profiles).filter(modelService => {
    const shown = showUnavailable ? modelService.profiles : modelService.profiles.filter(profile => profile.available);
    return shown.length > 0
      && (!enabledOnly || shown.some(profile => profile.enabled))
      && (!needle || modelService.profiles.some(profile => modelServiceSearchText(profile).includes(needle)));
  });
}

function reasonsOf(profiles: Profile[]): string[] {
  return [...new Set(profiles.filter(profile => !profile.available).map(profile => profile.unavailableReason?.trim() ?? "").filter(Boolean))];
}

/**
 * The table's content. `data` is the view being edited and `recorded` what is
 * published, so that a difference between them is what is not saved yet. A
 * harness with no enabled buddy, or one that cannot work, starts folded until
 * the user says otherwise.
 */
export function harnessBlocks({ listed, data, recorded, harnesses, folded, fresh }: {
  listed: ModelServiceGroup[]; data: ConsoleView; recorded: Snapshot; harnesses: HarnessHealth[];
  folded: Record<string, boolean>; fresh: ReadonlySet<string>;
}): HarnessBlock[] {
  const blocks = harnessGroups(listed, data.profiles).map((group): HarnessBlock => {
    const health = harnesses.find(row => row.adapter === group.adapter);
    const models = group.modelServices.map((modelService): ModelRow => {
      const pending = modelServicePending(modelService.profiles);
      const limit = concurrencyEntryFor(data.modelServiceConcurrency, modelService.profiles[0])?.limit ?? MODEL_SERVICE_CONCURRENCY_DEFAULT;
      const active = concurrencyEntryFor(recorded.modelServiceConcurrency, modelService.profiles[0])?.active;
      return {
        key: modelService.key,
        modelService,
        chips: modelService.profiles.map(profile => {
          const published = recorded.profiles.find(entry => entry.profileId === profile.profileId);
          const own = (source: { preferences: ConsoleView["preferences"] }) =>
            source.preferences.find(entry => entry.profileId === profile.profileId);
          return {
            profile,
            effort: effortText(profile.effort),
            preference: data.preferences.find(entry => entry.profileId === profile.profileId)?.mode ?? null,
            unsaved: (!!published && published.enabled !== profile.enabled)
              || own(data)?.mode !== own(recorded)?.mode || (own(data)?.reason ?? "") !== (own(recorded)?.reason ?? ""),
          };
        }),
        available: modelService.profiles.some(profile => profile.available),
        reasons: reasonsOf(modelService.profiles),
        pending: pending.pending,
        pendingSince: pending.since,
        fresh: fresh.has(modelService.key),
        limit,
        occupancy: Number.isInteger(active) ? active! : null,
      };
    });
    const buddies = models.flatMap(model => model.chips);
    const enabled = buddies.filter(chip => chip.profile.enabled).length;
    const state = !group.unavailable ? null : health && health.status !== "ready" ? health.status : "unavailable";
    return {
      adapter: group.adapter,
      name: group.name,
      state,
      reasons: group.reasons,
      folded: folded[group.adapter] ?? (enabled === 0 || state !== null),
      models,
      providers: [...new Set(models.map(model => model.modelService.provider))],
      enabled,
      total: buddies.length,
    };
  });
  // The harnesses that can work come first.
  return [...blocks.filter(block => !block.state), ...blocks.filter(block => block.state)];
}

/** The state a harness is in when it keeps its buddies from working, in words. */
export function harnessStateText(state: string, t: TFunction<"buddies">): string {
  const known = ["missing", "login-required", "unhealthy", "unknown", "unavailable"] as const;
  const key = known.find(name => name === state);
  // A state this console does not know yet stays visible as the board recorded it.
  return key ? t(`models.harnessState.${key}`) : state;
}

/** The reasons the board records for a buddy that cannot work, by their name in the text. */
const REASONS: Record<string, "harness" | "gone"> = {
  HARNESS_UNHEALTHY: "harness",
  "not present in the latest confirmed native discovery": "gone",
  "not present in the latest complete native discovery": "gone",
};

/**
 * Recorded reasons in the reader's words, each once. A reason this console does
 * not know is shown as recorded. Where the harness's own state is already
 * written beside it, the reason that only repeats that state is left out.
 */
export function reasonTexts(reasons: string[], t: TFunction<"buddies">, harnessSaid = false): string[] {
  return [...new Set(reasons.flatMap(reason => {
    const known = REASONS[reason];
    return known === "harness" && harnessSaid ? [] : [known ? t(`models.reason.${known}`) : reason];
  }))];
}

/** The four parts of a buddy's identity without the last: `harness / provider / model`. */
export function modelIdentity(modelService: { adapter: string; provider: string; profiles: Profile[] }): string {
  return `${modelService.adapter} / ${modelService.provider} / ${modelService.profiles[0].model}`;
}
