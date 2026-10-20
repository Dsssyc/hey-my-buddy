import { cleanup, render as baseRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { TooltipProvider } from "./components/ui/tooltip";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { setLanguageChoice } from "./i18n";

afterEach(() => { cleanup(); setLanguageChoice("browser"); });
const render = (ui: React.ReactElement) => baseRender(ui, { wrapper: TooltipProvider });

describe("macro task time statistics", () => {
  it.each([
    { language: "zh" as const, locale: "zh-CN", title: "时间统计", started: "开始", last: "最后活动", missing: "未记录" },
    { language: "en" as const, locale: "en", title: "Time statistics", started: "Started", last: "Last activity", missing: "Not recorded" },
  ])("puts both dated recorded instants first for an active task in $language and names absent records", async ({ language, locale, title, started, last, missing }) => {
    setLanguageChoice(language);
    const timeline = macroTaskTimelineFixture();
    timeline.macroTask.createdAt = new Date(2026, 8, 26, 9, 5, 7).toISOString();
    timeline.macroTask.lastActivityAt = new Date(2026, 8, 26, 10, 6, 8).toISOString();
    const nowMs = new Date(2026, 8, 26, 12, 0, 0).getTime();
    expect(timeline.macroTask.counts.active).toBeGreaterThan(0);
    const view = render(<MacroTaskOverview timeline={timeline} summary={null} nowMs={nowMs} loading={false} stale={false} onBackToList={() => {}} />);
    await userEvent.click(screen.getByRole("button", { name: title }));
    const dialog = screen.getByRole("dialog", { name: title });
    const first = dialog.querySelector('[data-slot="time-stats-body"] > :first-child')!;
    expect(first.getAttribute("data-slot")).toBe("time-stats-instants");
    expect([...first.querySelectorAll("dt")].map(node => node.textContent)).toEqual([started, last]);
    const recorded = [timeline.macroTask.createdAt, timeline.macroTask.lastActivityAt];
    const formatter = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeStyle: "medium", hourCycle: "h23" });
    expect([...first.querySelectorAll("time")].map(node => ({ recorded: node.getAttribute("datetime"), text: node.textContent })))
      .toEqual(recorded.map(value => ({ recorded: value, text: formatter.format(new Date(value)) })));
    expect(first.textContent).not.toContain(formatter.format(nowMs));
    const summary = { ...timeline.macroTask, createdAt: null, lastActivityAt: null };
    view.rerender(<MacroTaskOverview timeline={null} summary={summary} nowMs={nowMs} loading={false} stale={false} onBackToList={() => {}} />);
    expect(first.querySelectorAll("time")).toHaveLength(0);
    expect(within(first as HTMLElement).getAllByText(missing)).toHaveLength(2);
  });

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
    const values = dialog.querySelector('[data-slot="time-stats-values"]')!;
    expect([...values.querySelectorAll("dt")].map(node => node.textContent)).toEqual(["跨度", "运行", "累计"]);
    expect([...values.querySelectorAll("dt")].map(node => node.firstElementChild?.getAttribute("aria-description")))
      .toEqual(["从开始到最近一次活动：", "其间至少有一项在运行的时间：", "回合累计："]);
    expect(values.querySelectorAll("dd")).toHaveLength(3);
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
