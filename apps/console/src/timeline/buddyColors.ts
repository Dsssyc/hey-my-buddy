import { buddyColorKey } from "../macro-task-display";
import type { TimelineSpan } from "../macro-task-types";
import { parseTimelineInstant } from "./instant";

const PALETTE = 6;

const registries = new Map<string, Map<string, string>>();

/**
 * The six low-saturation execution colours of one macro task, assigned by the
 * order a buddy's full identity first executes and then kept: polling, new
 * records and filtering never move a colour that was already assigned, and the
 * macro task's tracks and every micro task detail read the same registry.
 */
export function buddyColorRegistry(macroTaskId: string | undefined, spans: readonly TimelineSpan[]): ReadonlyMap<string, string> {
  const registry = registryFor(macroTaskId);
  const executions = spans
    .filter(span => span.kind === "execution" && parseTimelineInstant(span.startAt) !== null)
    .sort((left, right) => parseTimelineInstant(left.startAt)! - parseTimelineInstant(right.startAt)!);
  for (const span of executions) {
    const key = buddyColorKey(span.buddy);
    if (!registry.has(key)) registry.set(key, `var(--model-${registry.size % PALETTE + 1})`);
  }
  return registry;
}

function registryFor(macroTaskId: string | undefined): Map<string, string> {
  const key = macroTaskId ?? "";
  let registry = registries.get(key);
  if (!registry) { registry = new Map(); registries.set(key, registry); }
  return registry;
}
