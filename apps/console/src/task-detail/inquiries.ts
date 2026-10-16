import { parseTimelineInstant } from "../macro-task-timeline-layout";
import type { Steps } from "./steps";
import type { Inquiry } from "./types";

/**
 * Where each inquiry is shown: in the turn it was put to. One that names no
 * attempt, or an attempt the steps do not hold, goes to the turn that was
 * executing when it was asked, else to the first turn after, else to the
 * delegation. None is dropped.
 */
export function placeInquiries(inquiries: Inquiry[], result: Steps, nowMs: number): Record<string, Inquiry[]> {
  const turns = result.steps.filter(step => step.kind === "turn" && step.span);
  const placed: Record<string, Inquiry[]> = {};
  for (const inquiry of inquiries) {
    const named = inquiry.attemptId === null ? undefined : turns.find(step => step.span!.attemptId === inquiry.attemptId);
    const at = parseTimelineInstant(inquiry.createdAt) ?? 0;
    const during = turns.find(step => step.at !== null && step.at <= at && at <= (parseTimelineInstant(step.span!.endAt) ?? nowMs));
    const after = turns.find(step => step.at !== null && step.at > at);
    const key = (named ?? during ?? after)?.key ?? "delegation";
    (placed[key] ??= []).push(inquiry);
  }
  return placed;
}
