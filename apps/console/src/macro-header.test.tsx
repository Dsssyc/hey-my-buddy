import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { TooltipProvider } from "./components/ui/tooltip";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("the macro task header", () => {
  it("shows the title, context and state counts, with recorded details under elapsed time", async () => {
    const timeline = macroTaskTimelineFixture();
    const onBack = vi.fn();
    render(<section className="timeline-view"><MacroTaskOverview summary={null} timeline={timeline} loading={false}
      stale={false} onBackToList={onBack} listToggle={<button>列表开关</button>} headerActions={<button>停止</button>} /></section>);
    const head = screen.getByRole("heading", { name: timeline.macroTask.title }).closest("header")!;
    expect(head.querySelector("h2")?.hasAttribute("title")).toBe(false);
    expect(within(head).getByText("hey-my-buddy")).toBeTruthy();
    expect(within(head).getByText("codex-desktop")).toBeTruthy();
    expect(head.querySelector('[data-slot="macro-overview-facts"]')?.textContent).toMatch(/开始.*\d{2}:\d{2}.*历时/);
    expect(head.querySelector('[data-slot="macro-overview-counts"]')?.textContent).toContain("1进行中");
    expect(head.querySelector('[data-slot="macro-overview-counts"]')?.textContent).toContain("2已验收");
    expect(within(head).getByRole("button", { name: "列表开关" })).toBeTruthy();
    expect(within(head).getByRole("button", { name: "停止" })).toBeTruthy();
    expect(head.textContent).not.toContain(timeline.macroTask.description);
    const user = userEvent.setup();
    await user.click(within(head).getByRole("button", { name: "‹ 宏任务" }));
    expect(onBack).toHaveBeenCalledOnce();
    await user.click(within(head).getByRole("button", { name: "时间统计" }));
    const details = screen.getByRole("dialog", { name: "时间统计" });
    expect(details.textContent).toContain(timeline.macroTask.description);
    expect(details.textContent).toContain("从开始到最近一次活动");
  });

  it("keeps fallback title provenance in an accessible hint without a browser title tooltip", async () => {
    const summary = { ...macroTaskTimelineFixture().macroTask, titleSource: "task" as const };
    render(<TooltipProvider><MacroTaskOverview summary={summary} timeline={null} loading={false}
      stale={false} onBackToList={() => {}} /></TooltipProvider>);
    const title = screen.getByRole("heading", { name: summary.title });
    expect(title.hasAttribute("title")).toBe(false);
    await userEvent.setup().hover(title);
    expect((await screen.findAllByText(/取自任务首行/))[0]).toBeTruthy();
  });

  it("shows failed members with the failed state mark and keeps the other ended count separate", () => {
    const original = macroTaskTimelineFixture();
    const timeline = { ...original, macroTask: { ...original.macroTask,
      counts: { ...original.macroTask.counts, failed: 1 } } };
    render(<MacroTaskOverview summary={null} timeline={timeline} loading={false}
      stale={false} onBackToList={() => {}} />);
    const failed = document.querySelector('[data-slot="macro-overview-count-failed"]')!;
    expect(failed.textContent).toContain("1失败");
    expect(failed.querySelector('[data-slot="state-mark"]')?.getAttribute("data-kind")).toBe("fail");
    expect(document.querySelector('[data-slot="macro-overview-count-ended"]')?.textContent).toContain("1已结束");
  });
});
