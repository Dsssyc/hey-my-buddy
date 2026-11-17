import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { setLanguageChoice } from "../i18n";
import { reasonText } from "../i18n/reason";
import { Ask } from "./ask";
import { DecisionPane } from "./DecisionPane";
import { EventPane } from "./EventPane";
import { RoutingPane } from "./RoutingPane";
import { parseTask } from "./parse";
import { buildSteps } from "./steps";
import { at, audit, event, inquiry, row, routing, task, timeline, workflow } from "./task-detail.fixtures";
import { open, pane, ready } from "./task-detail.testing";
import type { Context } from "./types";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("program reasons alongside authored notes", () => {
  it("shows work pause, pause interruption and macro task end reasons in their corresponding panes in both languages", async () => {
    for (const [language, words] of [["zh", {
      route: "用户在控制台暂停了此工作。", pause: "用户在控制台暂停，回合已中断。", end: "用户在控制台结束了此工作。",
    }], ["en", {
      route: "The user paused this work in the console.", pause: "The user paused this work in the console; the turn was interrupted.", end: "The user ended this work in the console.",
    }]] as const) {
      setLanguageChoice(language);
      const pausedRoute = timeline();
      pausedRoute.spans.find(span => span.kind === "routing")!.routing!.reason = { code: "work-paused", params: {} };
      pausedRoute.spans.find(span => span.kind === "routing")!.routing!.note = null;
      open({ step: 2, timeline: pausedRoute });
      await ready();
      expect(pane().textContent).toContain(words.route);
      cleanup();

      open({ step: 7, replies: { task: task({ state: "cancelled", selectedAttempt: { result: { terminationReason: "pause" } } }), workflow: workflow({ state: "cancelled" }) } });
      await ready();
      expect(pane().textContent).toContain(words.pause);
      cleanup();

      const ended = timeline();
      ended.events.push(event(4, "conclude", at("11:07")));
      ended.rows = [row({ state: "concluded", status: "cancelled", conclusionStatus: "cancelled" })];
      open({ step: 8, timeline: ended, replies: { workflow: workflow({ state: "concluded", hostConclusion: {
        conclusionId: "c1", reason: { code: "macro-task-ended", params: {} }, executionStatus: "cancelled", note: null,
        artifactId: null, attemptId: "att-2", actor: { role: "console", id: "console" }, createdAt: at("11:07"),
      } }) } });
      await ready();
      expect(pane().textContent).toContain(words.end);
      cleanup();
    }
  });
  it("distinguishes a queued checkpoint delivery from both kinds of ended inquiry in both languages", () => {
    const queued = inquiry({ state: "queued", deliveryMode: "cooperative-checkpoint", deliveredAt: null });
    const beforeQueue = { code: "inquiry-root-turn-ended", params: {} };
    const afterDelivery = { code: "inquiry-delivered-at-attempt-end", params: {} };
    setLanguageChoice("zh");
    render(<TooltipProvider>
      <Ask inquiry={queued} />
      <Ask inquiry={inquiry({ state: "unavailable", reason: beforeQueue })} />
      <Ask inquiry={inquiry({ state: "unavailable", reason: afterDelivery })} />
    </TooltipProvider>);
    expect(screen.getByText("将在 Worker 的下一个检查点送达。")).toBeTruthy();
    expect(screen.getByText("此回合已结束，问询未排入。")).toBeTruthy();
    expect(screen.getByText("问询已送达，但 Worker 在回合结束前没有答复。")).toBeTruthy();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText("To be delivered at the Worker's next checkpoint.")).toBeTruthy();
    expect(screen.getByText("The turn had ended before the inquiry could be queued.")).toBeTruthy();
    expect(screen.getByText("The inquiry was delivered, but the Worker did not answer before the turn ended.")).toBeTruthy();
  });

  it("changes displayed reasons with zh, en and browser while preserving Host, user and Router notes", () => {
    const notes = { host: "Host 原话: continue exactly.", user: "用户原话: stop this work.", router: "Router 原话: choose this buddy." };
    const cause = { code: "router-input-too-large", params: { bytes: 12345, limit: 12000 } };
    const model = timeline();
    model.spans.find(span => span.kind === "routing")!.routing = { reason: cause, note: notes.router };
    model.events.push(event(20, "cancel", at("11:15"), { note: notes.user, state: "cancelled" }));
    const history = audit();
    history.continuations[0]!.note = notes.host;
    history.requests[0]!.summary = null;
    history.requests[0]!.reason = { code: "workspace-scope-violation", params: { conflictIds: ["conflict-1"] } };
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
