import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Header } from "./Header";
import { at, HOST, task, workflow } from "./task-detail.fixtures";
import { parseTask } from "./parse";

afterEach(async () => { cleanup(); await i18n.changeLanguage("zh"); });

const cases = [
  { state: "awaiting-host", taskState: "completed", lead: false, zh: "等待 Host", en: "Waiting for Host", mark: "hand" },
  { state: "delivered", taskState: "completed", lead: false, zh: "等待验收", en: "Awaiting acceptance", mark: "review" },
  { state: "failed", lead: false, zh: "失败，等待 Host", en: "Failed, waiting for Host", mark: "hand" },
  { state: "cancelled", lead: false, zh: "已取消，等待 Host", en: "Cancelled, waiting for Host", mark: "hand" },
  { state: "failed", lead: true, zh: "失败，等待 Lead", en: "Failed, waiting for Lead", mark: "hand" },
  { state: "cancelled", lead: true, zh: "已取消，等待 Lead", en: "Cancelled, waiting for Lead", mark: "hand" },
  { state: "concluded", conclusion: "failed", lead: false, zh: "失败", en: "Failed", mark: "fail" },
  { state: "concluded", conclusion: "cancelled", lead: false, zh: "已结束", en: "Ended", mark: "idle" },
  { state: "failed", helper: true, lead: false, zh: "失败", en: "Failed", mark: "fail" },
  { state: "cancelled", helper: true, lead: false, zh: "已结束", en: "Ended", mark: "idle" },
  { state: "failed", helper: true, lead: true, zh: "失败", en: "Failed", mark: "fail" },
  { state: "cancelled", helper: true, lead: true, zh: "已结束", en: "Ended", mark: "idle" },
] as const;
const activeCases = (["failed", "cancelled"] as const).flatMap(state => [
  { state, taskState: "queued", lead: false, zh: "排队", en: "Queued", mark: "idle" },
  { state, taskState: "running", lead: false, zh: "进行中", en: "Running", mark: "live" },
  { state, taskState: "cancelling", lead: false, zh: "正在停止", en: "Stopping", mark: "live" },
  { state, taskState: "reconciliation-needed", lead: false, zh: "结束未确认", en: "Stop not confirmed", mark: "live" },
]);
const allCases = [...cases, ...activeCases];
const taskReadOnly = allCases.filter(entry => entry.state !== "concluded" && !entry.lead);

for (const language of ["zh", "en"] as const) describe(`micro task state in ${language}`, () => {
  it.each([...allCases.map(entry => ({ ...entry, loaded: true })), ...taskReadOnly.map(entry => ({ ...entry, loaded: false }))])("$state, task=$taskState, helper=$helper, Lead-owned=$lead, workflow loaded=$loaded", async entry => {
    await i18n.changeLanguage(language);
    const helper = "helper" in entry;
    const record = task({ task: " ", state: "taskState" in entry ? entry.taskState : entry.state, workflow: { ...task().workflow, state: entry.state, title: null },
      lineage: { ...task().lineage, kind: helper ? "helper" : "micro-task", parentRunId: helper ? "parent-1" : null,
        rootLeadRunId: entry.lead ? "lead-1" : null } });
    const conclusion = "conclusion" in entry ? {
      conclusionId: "conclusion-1", executionStatus: entry.conclusion, note: null, reason: null,
      artifactId: null, attemptId: "att-2", actor: HOST, createdAt: at("11:05"), runRevision: 9,
    } : null;
    const view = render(<TooltipProvider><Header view={parseTask(record, "m1")}
      workflow={entry.loaded ? workflow({ state: entry.state, hostConclusion: conclusion }) : null} turns={2}
      endMs={Date.parse(at("11:05"))} nowMs={Date.parse(at("11:30"))} verifiedAtMs={null}
      error="" busy={false} onRefresh={() => {}} onBack={() => {}}/></TooltipProvider>);
    const status = view.container.querySelector("[data-slot=detail-status]")!;
    expect(screen.getByRole("heading", { name: language === "zh" ? "未命名微任务" : "Unnamed micro task" })).toBeTruthy();
    expect(status.textContent).toBe(entry[language]);
    expect(status.querySelector("[data-slot=state-mark]")?.getAttribute("data-kind")).toBe(entry.mark);
    if (entry.mark === "review") {
      expect(status.querySelector("[data-slot=state-mark] path")?.getAttribute("fill")).toBe("none");
      expect(status.querySelector("[data-slot=state-mark] path")?.getAttribute("class")).toBe("stroke-hand-mark");
    }
    expect(status.classList.contains("text-fail")).toBe(entry.mark === "fail");
    expect(status.classList.contains("text-hand")).toBe(entry.mark === "hand" || entry.mark === "review");
    expect(screen.queryByRole("button", { name: /续做|结束|continue|conclude/i })).toBeNull();
  });
});
