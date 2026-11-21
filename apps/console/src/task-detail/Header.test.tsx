import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
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
  it.each([...allCases.map(entry => ({ ...entry, loaded: true })), ...taskReadOnly.map(entry => ({ ...entry, loaded: false }))])("$state, task=$taskState, Lead-owned=$lead, workflow loaded=$loaded", async entry => {
    await i18n.changeLanguage(language);
    const record = task({ task: " ", state: "taskState" in entry ? entry.taskState : entry.state, workflow: { ...task().workflow, state: entry.state, title: null },
      lineage: { ...task().lineage, kind: "micro-task", parentRunId: null,
        rootLeadRunId: entry.lead ? "lead-1" : null } });
    const conclusion = "conclusion" in entry ? {
      conclusionId: "conclusion-1", executionStatus: entry.conclusion, note: null, reason: null,
      artifactId: null, attemptId: "att-2", actor: HOST, createdAt: at("11:05"),
    } : null;
    const view = render(<TooltipProvider><Header view={parseTask(record, "m1")}
      workflow={entry.loaded ? workflow({ state: entry.state, hostConclusion: conclusion }) : null}
      endMs={Date.parse(at("11:05"))}
      error="" busy={false} onRefresh={() => {}} onOpenRun={vi.fn()} onBack={() => {}}/></TooltipProvider>);
    const status = view.container.querySelector("[data-slot=detail-status]")!;
    expect(screen.getByRole("heading", { name: language === "zh" ? "未命名微任务" : "Unnamed micro task" })).toBeTruthy();
    expect(status.textContent).toBe(entry[language]);
    expect(!!screen.queryByText(language === "zh" ? "归属" : "Owner")).toBe(entry.lead);
    expect(status.querySelector("[data-slot=state-mark]")?.getAttribute("data-kind")).toBe(entry.mark);
    if (entry.mark === "review") {
      expect(status.querySelector("[data-slot=state-mark] path")?.getAttribute("fill")).toBe("none");
      expect(status.querySelector("[data-slot=state-mark] path")?.getAttribute("class")).toBe("stroke-hand-mark");
    }
    expect(status.classList.contains("text-fail")).toBe(entry.mark === "fail");
    expect(status.classList.contains("text-hand")).toBe(entry.mark === "hand" || entry.mark === "review");
    expect(screen.queryByRole("button", { name: /续做|结束|continue|conclude/i })).toBeNull();
  });

  it("explains the recorded queue blocker and names the full model service when it is full", async () => {
    await i18n.changeLanguage(language);
    const expected = language === "zh"
      ? { directory: "该工作目录正被其他微任务使用", model: "所选模型服务的并发名额已满", unknown: "等待调度" }
      : { directory: "Another micro task is using this working directory", model: "The selected model service is at capacity", unknown: "Waiting for admission" };
    const reason = (queueReason: string | null) => {
      const view = render(<TooltipProvider><Header view={parseTask(task({ state: "queued", queueReason }), "m1")}
        workflow={null} endMs={Date.parse(at("11:05"))} error="" busy={false}
        onRefresh={() => {}} onOpenRun={vi.fn()} onBack={() => {}}/></TooltipProvider>);
      const text = view.container.querySelector('[data-slot="detail-queue-reason"]')?.textContent;
      view.unmount();
      return text;
    };
    expect(reason("cwd-overlap")).toBe(expected.directory);
    expect(reason("model-capacity")).toBe(`${expected.model} · claude / anthropic / claude-opus-5-5`);
    expect(reason("paused")).toBeUndefined();
    expect(reason("future-blocker")).toBe(expected.unknown);
    expect(reason(null)).toBe(expected.unknown);
  });

  it.each([false, true])("shows the queued pause with matching stop evidence motion (%s)", async shutdownConfirmed => {
    await i18n.changeLanguage(language);
    const view = render(<TooltipProvider><Header view={parseTask(task({ state: "queued", queueReason: "paused", shutdownConfirmed }), "m1")}
      workflow={null} endMs={Date.parse(at("11:05"))} error="" busy={false}
      onRefresh={() => {}} onOpenRun={vi.fn()} onBack={() => {}}/></TooltipProvider>);
    const status = view.container.querySelector('[data-slot="detail-status"]')!;
    expect(status.textContent).toBe(language === "zh" ? "已暂停" : "Paused");
    const mark = status.querySelector('[data-slot="state-mark"]')!;
    expect(mark.getAttribute("data-kind")).toBe("paused");
    expect(mark.classList.contains("state-breathe")).toBe(!shutdownConfirmed);
    expect(view.container.querySelector('[data-slot="detail-queue-reason"]')).toBeNull();
  });
});

it.each([
  { title: "Prepare shared checkout", macroTaskId: "macro-2", text: "Prepare shared checkout" },
  { title: "Standalone checkout", macroTaskId: "run:holder-1", text: "Standalone checkout" },
])("links a queued blocker with its recorded macro or standalone group", ({ title, macroTaskId, text }) => {
  const runId = "run:123456789012345678901234";
  const view = parseTask(task({ state: "queued", queueReason: "cwd-overlap", blockingTask: { runId, title, macroTaskId } }), "m1");
  const onOpenRun = vi.fn();
  render(<TooltipProvider><Header view={view} workflow={null} endMs={Date.parse(at("11:05"))} error="" busy={false}
    onRefresh={vi.fn()} onBack={vi.fn()} onOpenRun={onOpenRun}/></TooltipProvider>);
  const button = screen.getByRole("button", { name: title });
  expect(button.textContent?.trim()).toBe(text);
  fireEvent.click(button);
  expect(onOpenRun).toHaveBeenCalledExactlyOnceWith(runId, macroTaskId);
  expect(screen.queryByText("归属")).toBeNull();
  expect(screen.queryByText("回合")).toBeNull();
  expect(screen.getByText("历时")).toBeTruthy();
});

it("renders a plain execution blocker without a link when it has no workflow group", () => {
  const runId = "run:123456789012345678901234";
  const view = parseTask(task({ state: "queued", queueReason: "cwd-overlap", blockingTask: { runId, title: null, macroTaskId: null } }), "m1");
  const onOpenRun = vi.fn();
  const rendered = render(<TooltipProvider><Header view={view} workflow={null} endMs={Date.parse(at("11:05"))} error="" busy={false}
    onRefresh={vi.fn()} onBack={vi.fn()} onOpenRun={onOpenRun}/></TooltipProvider>);
  expect(screen.queryByRole("button", { name: runId })).toBeNull();
  const blocker = rendered.container.querySelector('[data-slot="detail-blocking-task"]');
  expect(blocker?.tagName).toBe("SPAN");
  expect(blocker?.textContent?.trim()).toBe("run:123456789012…");
  expect(onOpenRun).not.toHaveBeenCalled();
});
