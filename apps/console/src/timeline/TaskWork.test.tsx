import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
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
    expect(props.onClose).not.toHaveBeenCalled();
    rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
    expect(first.getAttribute("aria-expanded")).toBe("false");
    expect(first.className).toContain("bg-live-wash");
    expect(document.querySelectorAll('[data-slot=selected-item]')).toHaveLength(0);
    expect(document.querySelector('[data-run-id="r1"] [data-slot=selected-row]')).toBeTruthy();
    expect(props.onClose).not.toHaveBeenCalled();
  });
  it("opens a row without an origin, and a segment with its exact identity; arrows move between rows", () => {
    const { props } = setup();
    fireEvent.click(row("r1"));
    expect(props.onOpen).not.toHaveBeenCalled();
    expect(row("r1").className).toContain("bg-live-wash");
    fireEvent.doubleClick(row("r1"));
    expect(props.onOpen).toHaveBeenCalledWith("r1");
    fireEvent.click(document.querySelector('[data-key="span:s-r2-e1"]')!);
    expect(props.onOpen).toHaveBeenCalledTimes(1);
    fireEvent.doubleClick(document.querySelector('[data-key="span:s-r2-e1"]')!);
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
    expect(row("r5").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBeUndefined();
    expect(row("r4").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBe("live");
    expect(row("r6").querySelector('[data-slot=row-state]')?.getAttribute("data-kind")).toBeUndefined();
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
    expect(status()).toContain("46 分");
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
    expect(status()).toContain("46 分");
    fireEvent.blur(span);
    fireEvent.focus(row("r6"));
    expect(status()).toContain("已等待");
  });
  it("writes the repaired task title and reveals the full title on hover", async () => {
    const timeline = macroTaskTimelineFixture();
    const target = "被修正的任务：" + "完整标题".repeat(20);
    Object.assign(timeline.rows[1]!, { fixes: "other-macro-task", fixesTitle: target, fixesTitleSource: "task" });
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


describe("timeline keyboard and screen reader navigation", () => {
  const fragments = (id: string) => Array.from(document.querySelectorAll<SVGElement>(`[data-slot=timeline-row][data-run-id="${id}"] [data-key]`))
    .sort((left, right) => Number(left.dataset.at) - Number(right.dataset.at));
  const tabbable = () => Array.from(document.querySelectorAll('[data-slot=timeline-row-name], [data-slot=timeline-track] [data-key]')).filter(element => element.getAttribute("tabindex") === "0");

  it("has one Tab stop, enters the first row and returns to the selected row on re-entry", async () => {
    const user = userEvent.setup();
    const { props, rerender } = setup();
    const after = document.body.appendChild(document.createElement("button")); after.textContent = "After timeline";
    expect(tabbable()).toEqual([row("r1")]);
    row("r1").focus();
    await user.keyboard("{ArrowDown}{ArrowRight}");
    expect(document.activeElement).toBe(fragments("r2")[0]);
    expect(tabbable()).toEqual([document.activeElement]);
    await user.tab(); expect(document.activeElement).toBe(after);
    await user.tab({ shift: true }); expect(document.activeElement).toBe(row("r1"));
    rerender(<TooltipProvider><TaskWork {...props} openedRunId="r2"/></TooltipProvider>);
    rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
    after.focus();
    await user.tab({ shift: true }); expect(document.activeElement).toBe(row("r2"));
    expect(tabbable()).toEqual([row("r2")]);
    after.remove();
  });

  it("moves within each row, returns left to its name, and opens the exact segment or event", async () => {
    const user = userEvent.setup();
    const { props } = setup();
    const items = fragments("r2");
    row("r2").focus();
    await user.keyboard("{ArrowRight}"); expect(document.activeElement).toBe(items[0]);
    expect(status()).toBe(items[0]!.getAttribute("aria-label"));
    expect(items[0]!.querySelector('[data-slot=fragment-focus]')).toBeTruthy();
    await user.keyboard("{ArrowLeft}"); expect(document.activeElement).toBe(row("r2"));
    await user.keyboard("{Home}"); expect(document.activeElement).toBe(items[0]);
    await user.keyboard("{ArrowRight}"); expect(document.activeElement).toBe(items[1]);
    await user.keyboard("{ArrowLeft}"); expect(document.activeElement).toBe(items[0]);
    await user.keyboard("{End}{ArrowRight}"); expect(document.activeElement).toBe(items.at(-1));
    expect(status()).toBe(items.at(-1)!.getAttribute("aria-label"));
    await user.keyboard("{Enter}"); expect(props.onOpen).toHaveBeenLastCalledWith("r2", items.at(-1)!.getAttribute("data-key"));
    await user.keyboard("{Home}{Enter}"); expect(props.onOpen).toHaveBeenLastCalledWith("r2", items[0]!.getAttribute("data-key"));
    await user.keyboard("{ArrowDown}"); expect(document.activeElement).toBe(row("r3"));
    await user.keyboard("{ArrowUp}"); expect(document.activeElement).toBe(row("r2"));
    await user.keyboard("{Enter}"); expect(props.onOpen).toHaveBeenLastCalledWith("r2");
    expect(screen.getByRole("region", { name: "时间轴" })).toBeTruthy();
  });

  it("clamps row navigation at the ends and keeps rows without fragments on their name", async () => {
    const user = userEvent.setup();
    const timeline = macroTaskTimelineFixture(); timeline.spans = []; timeline.events = [];
    setup({ timeline });
    row("r1").focus();
    await user.keyboard("{ArrowUp}{ArrowRight}{Home}{End}{ArrowLeft}"); expect(document.activeElement).toBe(row("r1"));
    const last = row(timeline.rows.at(-1)!.runId); last.focus();
    await user.keyboard("{ArrowDown}"); expect(document.activeElement).toBe(last);
  });

  it("opens adjacent micro tasks with vertical arrows while detail is expanded", async () => {
    const user = userEvent.setup();
    function Harness() {
      const [openedRunId, setOpened] = useState<string | null>("r2");
      const timeline = macroTaskTimelineFixture();
      return <TooltipProvider><TaskWork summary={timeline.macroTask} timeline={timeline} displayObservedAt={timeline.observedAt}
        loading={false} error="" stale={false} newRunIds={new Set()} active openedRunId={openedRunId}
        detail={() => <button>Detail control</button>} onOpen={setOpened} onClose={() => setOpened(null)} onRetry={() => {}} onBackToList={() => {}}/></TooltipProvider>;
    }
    render(<Harness/>);
    row("r2").focus();
    await user.keyboard("{ArrowDown}");
    expect(row("r3").getAttribute("aria-expanded")).toBe("true"); expect(document.activeElement).toBe(row("r3"));
    expect(row("r2").getAttribute("aria-expanded")).toBe("false");
    screen.getByRole("button", { name: "Detail control" }).focus();
    await user.keyboard("{ArrowUp}");
    expect(row("r2").getAttribute("aria-expanded")).toBe("true"); expect(document.activeElement).toBe(row("r2"));
  });
});

it("gives each name a three-pixel state edge, with no queue edge or hand/queue symbol", () => {
  setup();
  for (const id of ["r1", "r2", "r3", "r4", "r5", "r6"]) {
    const name = row(id);
    if (name.dataset.stateTone === "queued") expect(name.style.boxShadow).toBe("");
    else expect(name.style.boxShadow).toContain("inset 3px 0 var(--");
    expect(name.querySelector('[data-slot=row-state][data-kind=hand], [data-slot=row-state][data-kind=idle]')).toBeNull();
  }
});

it("selects empty track space without opening, and opens it on a double click", () => {
  const { props } = setup();
  const track = document.querySelector('[data-slot=timeline-row][data-run-id="r2"]')!;
  fireEvent.click(track);
  expect(props.onOpen).not.toHaveBeenCalled();
  expect(row("r2").className).toContain("bg-live-wash");
  fireEvent.doubleClick(track);
  expect(props.onOpen).toHaveBeenCalledWith("r2");
});

it("assigns colours by the first model execution and preserves them when a new model is appended", () => {
  const timeline = macroTaskTimelineFixture();
  const source = timeline.spans.find(span => span.kind === "execution")!;
  const start = Date.parse(source.startAt!);
  timeline.spans = ["gpt-6.1-sol", "claude-opus-5-5", "gpt-6.1-sol"].map((model, index) => ({ ...source, spanId: `model-${index}`, startAt: new Date(start + index * 120000).toISOString(), endAt: new Date(start + index * 120000 + 60000).toISOString(), state: "finished", uncertain: false, shutdownConfirmed: true, resultStatus: "ok", buddy: { ...source.buddy!, model } }));
  const { props, rerender } = setup({ timeline });
  const colors = () => [...document.querySelectorAll<SVGElement>('[data-slot=execution-bar]')].map(bar => bar.style.fill);
  expect(colors()).toEqual(["var(--model-1)", "var(--model-2)", "var(--model-1)"]);
  const next = { ...timeline, spans: [...timeline.spans, { ...timeline.spans[2]!, spanId: "later-model", startAt: new Date(start + 360000).toISOString(), endAt: new Date(start + 420000).toISOString(), buddy: { ...source.buddy!, model: "new-model" } }] };
  rerender(<TooltipProvider><TaskWork {...props} timeline={next}/></TooltipProvider>);
  expect(colors()).toEqual(["var(--model-1)", "var(--model-2)", "var(--model-1)", "var(--model-3)"]);
});

it("retains an inquiry's own readout after selecting it without opening", () => {
  const timeline = macroTaskTimelineFixture();
  const span = timeline.spans.find(span => span.kind === "execution")!;
  span.inquiries = [{ inquiryId: "selected-question", createdAt: span.endAt!, author: "host-select", state: "answered" }];
  const { props } = setup({ timeline });
  const mark = document.querySelector('[data-key="inquiry:selected-question"]')!;
  fireEvent.pointerEnter(mark);
  const hovered = status();
  fireEvent.click(mark);
  fireEvent.pointerLeave(mark);
  expect(props.onOpen).not.toHaveBeenCalled();
  expect(status()).toBe(hovered);
  expect(status()).toContain("host-select");
  expect(row(span.runId).className).toContain("bg-live-wash");
});

it("resolves a selected inquiry inside its own micro task when another task uses the same inquiry id", () => {
  const timeline = macroTaskTimelineFixture();
  const executions = timeline.spans.filter(span => span.kind === "execution");
  const first = executions[0]!, second = executions.find(span => span.runId !== first.runId)!;
  for (const [span, author] of [[first, "first-host"], [second, "second-host"]] as const) {
    span.inquiries = [{ inquiryId: "shared-question", createdAt: span.endAt!, author, state: "answered" }];
  }
  timeline.events = [];
  setup({ timeline });
  fireEvent.click(document.querySelector(`[data-slot=timeline-row][data-run-id="${second.runId}"] [data-key="inquiry:shared-question"]`)!);
  expect(status()).toContain("second-host");
  expect(status()).not.toContain("first-host");
});
