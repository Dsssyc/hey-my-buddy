import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "../components/ui/tooltip";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import type { TaskWorkProps } from "./TaskWork";
import { TaskWork } from "./TaskWork";

afterEach(cleanup);
const setup = (overrides: Partial<TaskWorkProps> = {}) => {
  const timeline = macroTaskTimelineFixture();
  const props: TaskWorkProps = { summary: timeline.macroTask, timeline, displayObservedAt: timeline.observedAt, loading: false, error: "", stale: false,
    newRunIds: new Set(["r4"]), active: true, openedRunId: null, detail: () => <p>Detail</p>, onOpen: vi.fn(), onClose: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(), ...overrides };
  const view = render(<TooltipProvider><TaskWork {...props}/></TooltipProvider>);
  return { ...view, props, timeline };
};
const row = (id: string) => document.querySelector<HTMLButtonElement>(`[data-slot=timeline-row-name][data-key="row:${id}"]`)!;
const status = () => document.querySelector('[data-slot=timeline-status]')!.textContent;

describe("persistent task rows and basic SVG tracks", () => {
  it("keeps the same name column and row elements when detail opens; the rail target sits below the selected row", () => {
    const { rerender, props } = setup();
    const names = document.querySelector('[data-slot=task-names]');
    const first = row("r1");
    rerender(<TooltipProvider><TaskWork {...props} openedRunId="r1"/></TooltipProvider>);
    expect(document.querySelector('[data-slot=task-names]')).toBe(names);
    expect(row("r1")).toBe(first);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(first.parentElement!.querySelector('[data-slot=task-steps-drawer]')).toBeTruthy();
    expect(document.querySelector('[data-slot=timeline-status]')).toBeNull();
    fireEvent.click(first);
    rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
    expect(first.className).toContain("bg-live-wash");
    expect(document.querySelectorAll('[data-key="span:s-r1-e"] [data-slot=selected-span]')).toHaveLength(1);
    expect(props.onClose).toHaveBeenCalledOnce();
  });
  it("opens a row without an origin, and a segment with its exact identity; arrows move between rows", () => {
    const { props } = setup();
    fireEvent.click(row("r1"));
    expect(props.onOpen).toHaveBeenCalledWith("r1");
    fireEvent.click(document.querySelector('[data-key="span:s-r2-e1"]')!);
    expect(props.onOpen).toHaveBeenLastCalledWith("r2", "span:s-r2-e1");
    row("r1").focus();
    fireEvent.keyDown(row("r1"), { key: "ArrowDown" });
    expect(document.activeElement).toBe(row("r2"));
    fireEvent.keyDown(row("r2"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(row("r1"));
  });
  it("marks only unfinished work, preserves new-task marks and tree order", () => {
    const timeline = macroTaskTimelineFixture();
    timeline.rows[3] = { ...timeline.rows[3]!, state: "executing", status: "queued", category: "active" };
    setup({ timeline });
    expect(row("r1").querySelector('[data-slot=row-state]')).toBeNull();
    expect(row("r2").querySelector('[data-slot=row-state]')).toBeNull();
    expect(row("r5").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBe("idle");
    expect(row("r4").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBe("live");
    expect(row("r6").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBe("hand");
    expect(within(row("r4")).getByLabelText("新出现的微任务")).toBeTruthy();
    expect([...document.querySelectorAll('[data-slot=timeline-row-name]')].map(node => node.getAttribute("data-run-id"))).toEqual(timeline.rows.map(entry => entry.runId));
    expect(row("r3").style.paddingLeft).toBe("31px");
  });
  it("shows range by default, row state/wait/turns on row focus, and exact span facts on hover or focus", () => {
    const timeline = macroTaskTimelineFixture();
    timeline.spans = timeline.spans.map(span => span.spanId === "s-r6-e" ? { ...span, state: "finished", uncertain: false, shutdownConfirmed: true } : span);
    setup({ timeline });
    expect(status()).toContain("6 项微任务");
    fireEvent.focus(row("r4"));
    expect(status()).toContain("执行中");
    expect(status()).toContain("共 1 回合");
    fireEvent.blur(row("r4"));
    const span = document.querySelector('[data-key="span:s-r2-e1"]')!;
    fireEvent.pointerEnter(span);
    expect(status()).toContain("46 分钟");
    expect(status()).toContain("gpt-5-codex");
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.pointerLeave(span);
    expect(status()).toContain("6 项微任务");
    fireEvent.focus(row("r4"));
    fireEvent.pointerEnter(row("r4"));
    fireEvent.pointerLeave(row("r4"));
    expect(status()).toContain("执行中");
    fireEvent.blur(row("r4"));
    fireEvent.focus(span);
    expect(status()).toContain("46 分钟");
    fireEvent.blur(span);
    fireEvent.focus(row("r6"));
    expect(status()).toContain("已等待");
  });
  it("writes the repaired task title and reveals the full title on hover", async () => {
    const timeline = macroTaskTimelineFixture();
    const target = "被修正的任务：" + "完整标题".repeat(20);
    timeline.rows[0]!.title = target;
    timeline.rows[1]!.fixes = "r1";
    timeline.rows[0]!.fixedBy = ["r2"];
    setup({ timeline });
    expect(row("r2").querySelector('[data-slot=fix-link]')?.textContent).toBe(`↪ ${target}`);
    expect(row("r1").querySelector('[data-slot=fixed-by]')?.textContent).toBe("↩ 1");
    expect(row("r2").getAttribute("aria-label")).toContain(target);
    await userEvent.hover(row("r2"));
    expect((await screen.findByRole("tooltip")).textContent).toBe(target);
  });
  it("explains incomplete reads and retains frozen source facts with retry", () => {
    const timeline = macroTaskTimelineFixture({ filtered: true });
    const { props } = setup({ timeline, error: "offline", stale: true });
    expect(status()).toContain("时间不压缩");
    expect(screen.getByRole("alert").textContent).toContain("时间冻结");
    fireEvent.click(screen.getByRole("button", { name: "重试连接" }));
    expect(props.onRetry).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-slot=timeline-axis]')).toBeTruthy();
  });
});
