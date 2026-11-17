import type { Buddy, Model, ModelCorrespondence } from "../types";
import { modelServiceKey, modelServices } from "../console-data";

/** Synthetic model facts matching the service's same-name normalization. */
export function modelFacts(buddies: Buddy[]): { models: Model[]; modelCorrespondences: ModelCorrespondence[] } {
  const services = modelServices(buddies).map(group => ({ harness: group.harness, provider: group.provider, model: group.buddies[0].model }));
  const names = [...new Set(services.map(service => service.model))];
  return {
    models: names.map(name => ({ modelId: name, displayName: name,
      names: services.filter(service => service.model === name).map(({ harness, provider, model }) => ({ harness, provider, model })),
      profile: null, latestProfileRun: null })),
    modelCorrespondences: services.map(service => ({ harness: service.harness, provider: service.provider, model: service.model,
      modelId: service.model, correspondenceId: modelServiceKey(service), createdAt: "2026-09-27T07:00:00Z",
      source: "same-name", evidenceUrl: null, evidenceDate: null, canUndo: false })),
  };
}
