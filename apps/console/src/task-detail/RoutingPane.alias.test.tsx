import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BuddyNamesProvider } from "../components/AliasTag";
import { snapshot } from "../buddies/roles.testing";
import { RoutingPane } from "./RoutingPane";
import { at, routing, task, timeline, workflow } from "./task-detail.fixtures";
import { buildSteps } from "./steps";
import { parseTask } from "./parse";
import type { Context } from "./types";

afterEach(cleanup);
for (const buddyId of ["codex:team-remote:model-rev:max", "codex:team-remote:model-rev:max:4ed098"]) it(`names the preferred buddy from its fields for ${buddyId}`, async () => {
  const preferred = { ...snapshot().buddies[0], buddyId, harness: "codex", provider: "team/remote", model: "model:rev", effort: "max", alias: "Review desk" };
  const recorded = timeline();
  const step = buildSteps(recorded, "m1", Date.parse(at("11:30"))).steps.find(entry => entry.kind === "routing")!;
  const ctx: Context = { view: parseTask(task(), "m1"), workflow: workflow(), audit: null,
    routing: [routing({ exploration: { quota: true, reselected: true, preferredBuddyId: buddyId } })],
    bases: {}, inquiries: null, asked: {}, timeline: recorded, row: recorded.rows[0], nowMs: Date.parse(at("11:30")), open: vi.fn() };
  render(<BuddyNamesProvider buddies={[preferred]}><TooltipProvider delayDuration={0}><RoutingPane step={step} ctx={ctx} /></TooltipProvider></BuddyNamesProvider>);
  const mark = screen.getByText("探索", { exact: true });
  expect(mark.getAttribute("aria-description")).toBe("首选：codex / team/remote / model:rev / max [Review desk]");
  await userEvent.setup().hover(mark);
  expect((await screen.findByRole("tooltip")).textContent).toContain("codex / team/remote / model:rev / max [Review desk]");
});
