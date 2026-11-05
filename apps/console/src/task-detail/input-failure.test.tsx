import { act, cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { setLanguageChoice } from "../i18n";
import { ApiError } from "../api";
import { reasonText } from "../i18n/reason";
import { InputFailureFacts } from "./InputFailureFacts";
import { DecisionPane } from "./DecisionPane";
import { parseInputFailure } from "./input-failure";
import { parseAudit, parseTask, parseWorkflow } from "./parse";
import { buildSteps } from "./steps";
import { audit, inputFailure, row, task, timeline, workflow } from "./task-detail.fixtures";
import { open, pane, ready } from "./task-detail.testing";
import type { Context } from "./types";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });

describe("input rejection at the console response boundary", () => {
  it("reads the same frozen facts on task, compact/current/audit turns, and requests", () => {
    for (const known of [true, false]) {
      const failure = inputFailure(known), record = workflow(), history = audit();
      const reason = { code: "workflow.input-rejected", params: { harness: "codex" } };
      record.turns[0]!.inputFailure = failure;
      record.currentTurn!.inputFailure = failure;
      record.activeRequest = { ...history.requests[0]!, inputFailure: failure, reason };
      record.requests[0] = record.activeRequest;
      const parsed = parseWorkflow({ ...record, project: { kind: "git" }, task: { revision: record.taskRevision } }, "m1");
      expect(parsed.turns[0]!.inputFailure).toEqual(failure);
      expect(parsed.currentTurn!.inputFailure).toEqual(failure);
      expect(parsed.activeRequest!.inputFailure).toEqual(failure);
      expect(parsed.requests[0]!.inputFailure).toEqual(failure);
      expect(parsed.requests[0]!.reason).toEqual(reason);
      history.turns[0]!.inputFailure = failure;
      // Audit's wire form nests the request payload.
      const wire = { ...record, audit: { ...history, requests: [{ ...record.activeRequest, payload: {}, decision: null }] }, routingHistory: { entries: [], nextCursor: null } };
      const parsedAudit = parseAudit(wire, "m1").audit;
      expect(parsedAudit.turns[0]!.inputFailure).toEqual(failure);
      expect(parsedAudit.requests[0]!.inputFailure).toEqual(failure);
      const view = parseTask(task({ inputFailure: failure, workflow: { ...task().workflow, requestReason: reason } }), "m1");
      expect(view.inputFailure).toEqual(failure);
      expect(view.requestReason).toEqual(reason);
    }
  });

  it("requires every field of a present v1 package without filling missing facts", () => {
    for (const known of [true, false]) {
      const failure = inputFailure(known);
      for (const object of [failure, failure.harness, failure.limit, failure.measurement, failure.measurement.parts, ...(!known ? [failure.measurement.utf8Parts!] : [])]) {
        for (const key of Object.keys(object)) {
          if (object === failure && ["max_chars", "actual_chars", "nativeCode"].includes(key)) continue;
          const value = structuredClone(failure);
          const path = object === failure ? value : object === failure.harness ? value.harness : object === failure.limit ? value.limit
            : object === failure.measurement ? value.measurement : object === failure.measurement.parts ? value.measurement.parts : value.measurement.utf8Parts!;
          delete (path as unknown as Record<string, unknown>)[key];
          expect(() => parseInputFailure(value, "inputFailure"), key).toThrow(ApiError);
        }
      }
    }
  });

  it.each([
    (f: any) => { f.version = 2; },
    (f: any) => { f.stage = "native"; },
    (f: any) => { f.harness.name = "command"; },
    (f: any) => { f.harness.version = false; },
    (f: any) => { f.limit.form = "different"; },
    (f: any) => { f.limit.value = 0; },
    (f: any) => { f.limit.unit = "tokens"; },
    (f: any) => { f.limit.versions = []; },
    (f: any) => { f.measurement.unit = "utf-8-bytes"; },
    (f: any) => { f.measurement.total++; },
    (f: any) => { f.measurement.parts.role = -1; },
    (f: any) => { f.measurement.parts.task = "1000000"; },
    (f: any) => { f.measurement.parts.format = 1.5; },
    (f: any) => { f.measurement.parts.extra = 0; },
    (f: any) => { f.nativeRequestSent = true; },
    (f: any) => { f.modelStarted = null; },
    (f: any) => { f.message = "native message on a preflight"; },
    (f: any) => { f.extra = true; },
  ])("refuses malformed or contradictory known facts %#", mutate => {
    const failure = inputFailure(); mutate(failure);
    expect(() => parseTask(task({ inputFailure: failure }), "m1")).toThrow(ApiError);
  });

  it("refuses malformed unknown/native facts and bad request reasons", () => {
    for (const patch of [{ measurement: { ...inputFailure(false).measurement, utf8Bytes: 0 } },
      { limit: { ...inputFailure(false).limit, value: 0 } }, { max_chars: -1 }, { nativeCode: {} },
      { nativeRequestSent: "unknown" }, { message: "中".repeat(171) }]) {
      expect(() => parseInputFailure({ ...inputFailure(false), ...patch }, "inputFailure")).toThrow(ApiError);
    }
    expect(() => parseTask(task({ workflow: { ...task().workflow, requestReason: "bad" } }), "m1")).toThrow(ApiError);
    const record = workflow();
    expect(() => parseWorkflow({ ...record, requests: [{ ...record.requests[0], reason: "bad" }] }, "m1")).toThrow(ApiError);
  });
});

describe("input rejection presentation", () => {
  it("names local/native admission and unknown declarations in both languages, with all six parts", () => {
    for (const language of ["zh", "en"] as const) for (const known of [true, false]) {
      setLanguageChoice(language);
      render(<InputFailureFacts failure={inputFailure(known)} />);
      expect(screen.getByText(known ? language === "zh" ? "本地核对拒收" : "Rejected by local preflight"
        : language === "zh" ? "原生拒收" : "Rejected by native admission")).toBeTruthy();
      expect(screen.getByText(/codex · cli-app-server · synthetic-1.0/)).toBeTruthy();
      expect(screen.getByRole("table").querySelectorAll("tbody tr")).toHaveLength(6);
      expect(screen.getByRole("table").textContent).toContain("1,048,577");
      if (!known) {
        expect(screen.getByText(language === "zh" ? "未取得数值" : "No numeric value obtained")).toBeTruthy();
        expect(screen.getByRole("table").textContent).toContain("2,097,154");
        expect(screen.getByText(language === "zh" ? "原生报告上限" : "Native reported limit")).toBeTruthy();
        expect(screen.getByText(language === "zh" ? "未知" : "Unknown")).toBeTruthy();
      }
      expect(reasonText({ code: "workflow.input-rejected", params: { harness: "codex" } })).toContain(language === "zh" ? "拒收输入" : "rejected the input");
      cleanup();
    }
  });

  it("shows the selected turn once, using its own failure before the task's newer facts", async () => {
    const failure = inputFailure(), record = workflow(), history = audit();
    record.turns.find(turn => turn.turnIndex === 1)!.inputFailure = failure;
    history.turns[0]!.inputFailure = failure;
    open({ step: 3, replies: { workflow: record, audit: history, task: task({ inputFailure: inputFailure(false) }) } });
    await ready();
    const facts = pane().querySelectorAll("[data-slot=input-failure]");
    expect(facts).toHaveLength(1);
    expect(within(facts[0] as HTMLElement).getByText("本地核对拒收")).toBeTruthy();
    expect(within(pane()).queryByText("原生拒收")).toBeNull();
  });

  it("names the input-rejection stage in the turn's cause line in both languages", async () => {
    for (const language of ["zh", "en"] as const) for (const known of [true, false]) {
      setLanguageChoice(language);
      const failure = inputFailure(known), record = workflow();
      record.turns[0]!.inputFailure = failure;
      open({ step: 7, replies: { workflow: record, task: task({ state: "failed", inputFailure: failure,
        selectedAttempt: { result: { terminationReason: "harness-error" }, error: "Synthetic input rejection" } }) } });
      await ready();
      const cause = pane().querySelector("[data-slot=pane-title] > p")!;
      expect(cause.textContent).toBe(known ? language === "zh" ? "本地核对拒收" : "Rejected by local preflight"
        : language === "zh" ? "原生拒收" : "Rejected by native admission");
      cleanup();
    }
  });

  it("shows the request's frozen failure once while waiting for Host, without repeating the summary", () => {
    const history = audit(), record = workflow(), failure = inputFailure(false);
    const request = { ...history.requests[0]!, state: "open", reason: { code: "workflow.input-rejected", params: { harness: "codex" } }, inputFailure: failure };
    record.activeRequest = request; record.requests = [request];
    const model = timeline(), nowMs = Date.parse("2026-09-26T11:30:00Z");
    const ctx: Context = { view: parseTask(task(), "m1"), workflow: record, audit: { ...history, requests: [request] }, routing: [], bases: {},
      inquiries: null, asked: {}, timeline: model, row: row(), nowMs, open: () => {} };
    render(<DecisionPane ctx={ctx} step={buildSteps(model, "m1", nowMs).steps.find(step => step.kind === "decision")!} />);
    expect(screen.getAllByText("原生拒收")).toHaveLength(1);
    expect(screen.queryByText(request.summary!)).toBeNull();
    act(() => setLanguageChoice("en"));
    expect(screen.getByText("No numeric value obtained")).toBeTruthy();
  });
});
