import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { setLanguageChoice } from "../i18n";
import { reasonText } from "../i18n/reason";
import { DecisionPane } from "./DecisionPane";
import { EventPane } from "./EventPane";
import { RoutingPane } from "./RoutingPane";
import { parseTask } from "./parse";
import { buildSteps } from "./steps";
import { at, audit, event, row, routing, task, timeline, workflow } from "./task-detail.fixtures";
import type { Context } from "./types";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("program reasons alongside authored notes", () => {
  it("changes displayed reasons with zh, en and browser while preserving Host, user and Router notes", () => {
    const notes = { host: "Host 原话: continue exactly.", user: "用户原话: stop this work.", router: "Router 原话: choose this buddy." };
    const cause = { code: "router-input-too-large", params: { bytes: 12345, limit: 12000 } };
    const model = timeline();
    model.spans.find(span => span.kind === "routing")!.routing = { reason: cause, note: notes.router };
    model.events.push(event(20, "cancel", at("11:15"), { note: notes.user, state: "cancelled" }));
    const history = audit();
    history.continuations[0]!.note = notes.host;
    history.requests[0]!.summary = null;
    history.requests[0]!.reason = { code: "workspace-scope-violation", params: { conflictIds: ["conflict-1"], paths: ["src/a.ts", "src/" + "long/".repeat(40) + "b.ts"] } };
    const nowMs = Date.parse(at("11:30"));
    const ctx: Context = { view: parseTask(task(), "m1"), workflow: workflow(), audit: history, routing: [routing()], bases: {},
      inquiries: null, asked: {}, timeline: model, row: row(), nowMs, open: () => {} };
    const steps = buildSteps(model, "m1", nowMs).steps;
    setLanguageChoice("zh");
    render(<TooltipProvider>
      <RoutingPane ctx={ctx} step={steps.find(step => step.kind === "routing")!} />
      <DecisionPane ctx={ctx} step={steps.find(step => step.kind === "decision")!} />
      <DecisionPane ctx={ctx} step={steps.find(step => step.kind === "continue")!} />
      <EventPane ctx={ctx} step={steps.find(step => step.kind === "cancel")!} />
    </TooltipProvider>);
    const unchanged = () => { for (const note of Object.values(notes)) expect(document.body.textContent).toContain(note); };
    expect(screen.getByText(reasonText(cause), { exact: false })).toBeTruthy();
    expect(screen.getByText(reasonText(history.requests[0]!.reason!).slice(0, 80), { exact: false })).toBeTruthy();
    unchanged();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText(reasonText(cause), { exact: false }).textContent).toContain("Router input exceeds");
    unchanged();
    act(() => setLanguageChoice("browser"));
    expect(screen.getByText(reasonText(cause), { exact: false }).textContent).toContain("Router 输入超过上限");
    unchanged();
  });
});
