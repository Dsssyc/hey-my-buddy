import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";

afterEach(cleanup);

describe("macro task time statistics", () => {
  it("opens recorded metrics from elapsed time and closes with Escape", async () => {
    const timeline = macroTaskTimelineFixture();
    const { container } = render(<section className="timeline-view">
      <MacroTaskOverview timeline={timeline} summary={null} loading={false} stale={false} onBackToList={() => {}} />
    </section>);
    const trigger = screen.getByRole("button", { name: "时间统计" });
    expect(trigger.textContent).toMatch(/\d+ 时 \d+ 分/);
    const user = userEvent.setup();
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "时间统计" });
    expect(container.contains(dialog)).toBe(false);
    expect(dialog.textContent).toContain("从开始到最近一次活动：");
    expect(dialog.textContent).toContain("其间至少有一项在运行的时间：");
    expect(dialog.textContent).toContain("结束未确认 1 回合 · 未计入");
    expect(dialog.textContent).toContain("来源 Host codex-desktop");
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull());
    expect(document.activeElement).toBe(trigger);
  });

  it("uses the current macro task's facts after the selected task changes", async () => {
    const first = macroTaskTimelineFixture();
    const second = { ...first, macroTask: { ...first.macroTask, macroTaskId: "macro-second", description: "第二个宏任务" } };
    const overview = (timeline: typeof first) => <section className="timeline-view">
      <MacroTaskOverview timeline={timeline} summary={null} loading={false} stale={false} onBackToList={() => {}} />
    </section>;
    const view = render(overview(first));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "时间统计" }));
    view.rerender(overview(second));
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "时间统计" }));
    expect(screen.getByRole("dialog", { name: "时间统计" }).textContent).toContain("第二个宏任务");
  });

  it("dismisses on outside press and stays closed while the page is inactive", async () => {
    const timeline = macroTaskTimelineFixture();
    const overview = (active: boolean) => <><section className="timeline-view" hidden={!active}>
      <MacroTaskOverview timeline={timeline} summary={null} loading={false} stale={false} active={active} onBackToList={() => {}} />
    </section><button>其他控件</button></>;
    const view = render(overview(true));
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "时间统计" }));
    await user.click(screen.getByRole("button", { name: "其他控件" }));
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "时间统计" }));
    view.rerender(overview(false));
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    view.rerender(overview(true));
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "时间统计" }));
    expect(screen.getAllByRole("dialog", { name: "时间统计" })).toHaveLength(1);
  });

  it("updates the open panel when polling refreshes the selected task", async () => {
    const first = macroTaskTimelineFixture();
    const view = render(<MacroTaskOverview timeline={first} summary={null} loading={false} stale={false} onBackToList={() => {}} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "时间统计" }));
    const dialog = screen.getByRole("dialog", { name: "时间统计" });
    const revised = { ...first, macroTask: { ...first.macroTask, description: "轮询后的记录" } };
    view.rerender(<MacroTaskOverview timeline={revised} summary={null} loading={false} stale={false} onBackToList={() => {}} />);
    expect(screen.getByRole("dialog", { name: "时间统计" })).toBe(dialog);
    expect(dialog.textContent).toContain("轮询后的记录");
  });
});
