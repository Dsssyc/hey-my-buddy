import { cleanup, render as baseRender, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { emptyMacroTaskTimelineFixture, macroTaskTimelineFixture } from "./macro-task-fixtures";
import { TooltipProvider } from "./components/ui/tooltip";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const render = (ui: React.ReactElement) => baseRender(ui, { wrapper: ({ children }: { children: React.ReactNode }) => <TooltipProvider delayDuration={0}>{children}</TooltipProvider> });

describe("the macro task header", () => {
  it("shows the title, context and state counts, with recorded details under elapsed time", async () => {
    const timeline = macroTaskTimelineFixture();
    const onBack = vi.fn();
    render(<section className="timeline-view"><MacroTaskOverview readGoals={vi.fn()} summary={null} timeline={timeline} loading={false}
      stale={false} onBackToList={onBack} listToggle={<button>列表开关</button>} headerActions={<button>停止</button>} /></section>);
    const head = screen.getByRole("heading", { name: timeline.macroTask.title! }).closest("header")!;
    expect(head.querySelector("h2")?.hasAttribute("title")).toBe(false);
    expect(within(head).getByText("hey-my-buddy")).toBeTruthy();
    expect(within(head).queryByText("codex-desktop")).toBeNull();
    expect([...head.querySelectorAll('[data-slot="macro-overview-facts"] dt')].map(node => node.textContent)).toEqual(["仓库", "历时", "ID"]);
    expect(head.querySelector('[data-slot="macro-overview-counts"]')?.textContent).toContain("1进行中");
    expect(head.querySelector('[data-slot="macro-overview-counts"]')?.textContent).toContain("2已验收");
    expect(within(head).getByRole("button", { name: "列表开关" })).toBeTruthy();
    expect(within(head).getByRole("button", { name: "停止" })).toBeTruthy();
    expect(head.querySelector('[data-slot="macro-goal-trigger"]')?.getAttribute("aria-label")).toBe("查看目标说明");
    expect(screen.queryByRole("dialog", { name: "目标说明" })).toBeNull();
    const user = userEvent.setup();
    await user.click(within(head).getByRole("button", { name: "‹ 宏任务" }));
    expect(onBack).toHaveBeenCalledOnce();
    await user.click(within(head).getByRole("button", { name: "时间统计" }));
    const details = screen.getByRole("dialog", { name: "时间统计" });
    expect(details.textContent).not.toContain(timeline.macroTask.goal!.body);
    expect(details.textContent).toContain("从开始到最近活动");
  });

  it("uses the complete fallback title without a hover tooltip", async () => {
    const summary = { ...macroTaskTimelineFixture().macroTask, titleSource: "task" as const };
    render(<MacroTaskOverview readGoals={vi.fn()} summary={summary} timeline={null} loading={false}
      stale={false} onBackToList={() => {}} />);
    const title = screen.getByRole("heading", { name: summary.title! });
    expect(title.hasAttribute("title")).toBe(false);
    await userEvent.setup().hover(title);
    expect(title.hasAttribute("aria-description")).toBe(false);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });

  it("shows failed members with the failed state mark and keeps the other ended count separate", () => {
    const original = macroTaskTimelineFixture();
    const timeline = { ...original, macroTask: { ...original.macroTask,
      counts: { ...original.macroTask.counts, failed: 1 } } };
    render(<MacroTaskOverview readGoals={vi.fn()} summary={null} timeline={timeline} loading={false}
      stale={false} onBackToList={() => {}} />);
    const failed = document.querySelector('[data-slot="macro-overview-count-failed"]')!;
    expect(failed.textContent).toContain("1失败");
    expect(failed.querySelector('[data-slot="state-mark"]')?.getAttribute("data-kind")).toBe("fail");
    expect(document.querySelector('[data-slot="macro-overview-count-ended"]')?.textContent).toContain("1已结束");
  });

  it("distinguishes Host decisions from acceptance in the state counts", () => {
    const original = macroTaskTimelineFixture();
    const timeline = { ...original, macroTask: { ...original.macroTask,
      counts: { ...original.macroTask.counts, host: 1, review: 2 } } };
    render(<MacroTaskOverview readGoals={vi.fn()} summary={null} timeline={timeline} loading={false}
      stale={false} onBackToList={() => {}} />);
    const host = document.querySelector('[data-slot="macro-overview-count-host"]')!;
    const review = document.querySelector('[data-slot="macro-overview-count-review"]')!;
    expect(host.querySelector('[data-slot="state-mark"]')?.getAttribute("data-kind")).toBe("hand");
    expect(review.querySelector('[data-slot="state-mark"]')?.getAttribute("data-kind")).toBe("review");
    expect(review.querySelector("path")?.getAttribute("fill")).toBe("none");
    expect(review.querySelector("path")?.getAttribute("class")).toBe("stroke-hand-mark");
  });
});


it("shows the empty macro task's folder and zero members with its goal", () => {
  const timeline = emptyMacroTaskTimelineFixture();
  render(<MacroTaskOverview readGoals={vi.fn()} summary={null} timeline={timeline} loading={false}
    stale={false} onBackToList={() => {}} />);
  const header = document.querySelector("header")!;
  expect(within(header).getByText("文件夹")).toBeTruthy();
  expect(within(header).getByText("Field notes")).toBeTruthy();
  expect(within(header).getByText("0 个成员")).toBeTruthy();
  expect(header.querySelector('[data-slot="macro-goal-trigger"]')).toBeTruthy();
  expect(header.querySelectorAll('[data-slot^="macro-overview-count-"]')).toHaveLength(0);
  expect(within(header).queryByRole("button", { name: "时间统计" })).toBeNull();
  expect(within(header).queryByText("Host")).toBeNull();
});
