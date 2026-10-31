import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { TooltipProvider } from "@/components/ui/tooltip";
import { Header } from "./Header";
import { at, HOST, task, workflow } from "./task-detail.fixtures";
import { parseTask } from "./parse";

afterEach(async () => { cleanup(); await i18n.changeLanguage("zh"); });

const cases = [
  { state: "failed", lead: false, zh: "失败，等待 Host", en: "Failed, waiting for Host", mark: "hand" },
  { state: "cancelled", lead: false, zh: "已取消，等待 Host", en: "Cancelled, waiting for Host", mark: "hand" },
  { state: "failed", lead: true, zh: "失败，等待 Lead", en: "Failed, waiting for Lead", mark: "hand" },
  { state: "cancelled", lead: true, zh: "已取消，等待 Lead", en: "Cancelled, waiting for Lead", mark: "hand" },
  { state: "concluded", conclusion: "failed", lead: false, zh: "失败", en: "Failed", mark: "fail" },
  { state: "concluded", conclusion: "cancelled", lead: false, zh: "已结束", en: "Ended", mark: "idle" },
] as const;
const taskReadOnly = cases.filter(entry => entry.state !== "concluded" && !entry.lead);

for (const language of ["zh", "en"] as const) describe(`micro task state in ${language}`, () => {
  it.each([...cases.map(entry => ({ ...entry, loaded: true })), ...taskReadOnly.map(entry => ({ ...entry, loaded: false }))])("$state, Lead-owned=$lead, workflow loaded=$loaded", async entry => {
    await i18n.changeLanguage(language);
    const record = task({ state: entry.state, workflow: { ...task().workflow, state: entry.state },
      lineage: { ...task().lineage, rootLeadRunId: entry.lead ? "lead-1" : null } });
    const conclusion = "conclusion" in entry ? {
      conclusionId: "conclusion-1", executionStatus: entry.conclusion, note: null, reason: null,
      artifactId: null, attemptId: "att-2", actor: HOST, createdAt: at("11:05"), runRevision: 9,
    } : null;
    const view = render(<TooltipProvider><Header view={parseTask(record, "m1")}
      workflow={entry.loaded ? workflow({ state: entry.state, hostConclusion: conclusion }) : null} turns={2}
      endMs={Date.parse(at("11:05"))} nowMs={Date.parse(at("11:30"))} verifiedAtMs={null}
      error="" busy={false} onRefresh={() => {}} onBack={() => {}}/></TooltipProvider>);
    const status = view.container.querySelector("[data-slot=detail-status]")!;
    expect(status.textContent).toBe(entry[language]);
    expect(status.querySelector("[data-slot=state-mark]")?.getAttribute("data-kind")).toBe(entry.mark);
    expect(status.classList.contains("text-fail")).toBe(entry.mark === "fail");
    expect(status.classList.contains("text-hand")).toBe(entry.mark === "hand");
    expect(screen.queryByRole("button", { name: /续做|结束|continue|conclude/i })).toBeNull();
  });
});
