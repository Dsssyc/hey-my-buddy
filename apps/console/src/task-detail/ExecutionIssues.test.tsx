import { act, cleanup, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { at, task, timeline, workflow } from "./task-detail.fixtures";
import { open, pane, ready } from "./task-detail.testing";

afterEach(cleanup);

const issue = { stage: "connection", code: "CONNECTION_LOST", message: "Native connection closed during recovery.", observedAt: at("11:00") };

for (const [language, name, confirming] of [
  ["zh", "执行问题", "正在确认停止"],
  ["en", "Execution issues", "Confirming stop"],
]) describe(`execution diagnostics in ${language}`, () => {
  afterEach(async () => { await act(async () => { await i18n.changeLanguage("zh"); }); });

  async function show(attemptId: string, finalizing = false) {
    await act(async () => { await i18n.changeLanguage(language); });
    const live = timeline();
    live.spans = live.spans.map(span => span.attemptId === attemptId ? { ...span, endAt: null, state: finalizing ? "finalizing" : "executing", shutdownConfirmed: false } : span);
    open({ origin: attemptId === "att-r1" ? "span:r1" : "span:e2", timeline: live, replies: {
      task: task({ state: "running", activeAttemptId: attemptId,
        activity: { phase: "tool-running", toolName: "Bash", observedAt: at("11:00"), lastToolActivityAt: at("11:00"), counts: { modelTurns: 2, toolCalls: 3 } }, selectedAttempt: { attemptId, executionState: finalizing ? "finalizing" : "executing", shutdownConfirmed: false,
        issues: finalizing ? [{ ...issue, stage: "shutdown", code: "EXECUTION_UNKNOWN", message: "Native session identity is no longer verifiable." }] : [issue] } }),
      workflow: workflow({ state: "executing" }),
    } });
    await ready();
  }

  for (const attemptId of ["att-2", "att-r1"]) {
    it(`shows current issues and the confirming-stop state on ${attemptId}`, async () => {
      await show(attemptId, true);
      expect(document.querySelector("[data-slot=detail-status]")!.textContent).toBe(confirming);
      expect(document.querySelector("[data-slot=detail-status] [data-slot=state-mark]")!.getAttribute("data-kind")).toBe("live");
      expect(pane().querySelector("[data-slot=pane-title]")!.textContent).toContain(confirming);
      expect(pane().querySelector("[data-slot=detail-live]")).toBeNull();
      const issues = within(pane()).getByRole("group", { name });
      expect(issues.textContent).toContain("Native session identity is no longer verifiable.");
      expect(issues.textContent).not.toContain("EXECUTION_UNKNOWN");
      expect(issues.querySelector("time")!.dateTime).toBe(new Date(issue.observedAt).toISOString());
      expect(issues.querySelector("li > p")!.className).toBe("text-hand");
    });
  }

  it("shows a running connection issue without attaching it to earlier attempts", async () => {
    await show("att-2");
    expect(pane().querySelector("[data-slot=detail-live]")!.textContent).toContain("Bash");
    expect(within(pane()).getByRole("group", { name }).textContent).toContain(issue.message);
    cleanup();
    open({ step: 3, replies: { task: task({ selectedAttempt: { issues: [issue] } }) } });
    await ready();
    expect(within(pane()).queryByRole("group", { name })).toBeNull();
  });

  it("keeps issues visible when the selected attempt has no drawn step", async () => {
    await act(async () => { await i18n.changeLanguage(language); });
    open({ replies: { task: task({ selectedAttempt: { attemptId: "unplaced", issues: [issue] } }) } });
    await ready();
    expect(screen.getByRole("group", { name }).textContent).toContain(issue.message);
  });

  it("does not call a confirmed shutdown pending confirmation", async () => {
    await act(async () => { await i18n.changeLanguage(language); });
    open({ replies: { task: task({ selectedAttempt: { executionState: "finalizing", shutdownConfirmed: true } }) } });
    await ready();
    expect(document.querySelector("[data-slot=detail-status]")!.textContent).not.toBe(confirming);
    expect(screen.queryByRole("group", { name })).toBeNull();
  });

  it("retains the recorded diagnosis alongside a localized error-code explanation", async () => {
    await act(async () => { await i18n.changeLanguage(language); });
    open({ replies: { task: task({ selectedAttempt: { issues: [{ ...issue, code: "SHUTDOWN_UNCONFIRMED" }] } }) } });
    await ready();
    const issues = within(pane()).getByRole("group", { name });
    expect(issues.textContent).toContain(i18n.t("common:error.codes.SHUTDOWN_UNCONFIRMED"));
    expect(issues.textContent).toContain(issue.message);
  });
});
