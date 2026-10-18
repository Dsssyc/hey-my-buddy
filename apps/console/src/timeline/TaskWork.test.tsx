import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useState } from "react";
import { TooltipProvider } from "../components/ui/tooltip";
import { macroTaskTimelineFixture } from "../macro-task-fixtures";
import type { TimelineBuddy, TimelineRow } from "../macro-task-types";
import { RowName } from "./RowName";
import type { TaskWorkProps } from "./TaskWork";
import { TaskWork } from "./TaskWork";
import { groupLanes, laneSummary, ownedWaits } from "./lanes";


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

describe("the lanes and the detail workspace", () => {
  it("replaces the lanes with the detail workspace: a plain micro task list beside the detail", () => {
    const { rerender, props } = setup();
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeTruthy();
    expect(document.querySelector('[data-slot=task-names]')).toBeNull();
    rerender(<TooltipProvider><TaskWork {...props} openedRunId="r1"/></TooltipProvider>);
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeNull();
    const list = document.querySelector('[data-slot=task-names]')!;
    expect([...list.querySelectorAll("[data-slot=timeline-row-name]")].map(node => node.getAttribute("data-run-id"))).toEqual(props.timeline!.rows.map(entry => entry.runId));
    expect(row("r1").getAttribute("aria-current")).toBe("true");
    expect(screen.getByText("Detail")).toBeTruthy();
    expect(document.querySelector("[data-slot=timeline-status]")).toBeNull();
    fireEvent.click(row("r2"));
    expect(props.onOpen).toHaveBeenLastCalledWith("r2");
    rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
    expect(document.querySelector("[data-slot=timeline-tracks]")).toBeTruthy();
    expect(row("r1").className).not.toContain("bg-live-wash");
    expect(row("r2").className).toContain("bg-live-wash");
    expect(document.querySelector('[data-slot=timeline-row][data-run-id="r2"] [data-slot=selected-row]')).toBeTruthy();
    expect(document.querySelectorAll('[data-slot=selected-item]')).toHaveLength(0);
    expect(props.onClose).not.toHaveBeenCalled();
  });
  it("opens a row by one click without an origin, and a segment with its exact identity; arrows move between rows", () => {
    const { props } = setup();
    fireEvent.click(row("r1"));
    expect(props.onOpen).toHaveBeenCalledWith("r1");
    expect(row("r1").className).toContain("bg-live-wash");
    fireEvent.click(document.querySelector('[data-key="span:s-r2-e1"]')!);
    expect(document.querySelector('[data-key="span:s-r2-e1"] [data-slot=selected-item]')).toBeTruthy();
    expect(document.querySelectorAll('[data-slot=selected-item]')).toHaveLength(1);
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
  it("writes the state word only while someone must act or something runs; the settled band speaks alone", () => {
    setup();
    // Settled terminal states: the word is gone from the row and stays in the accessible name.
    for (const [id, word] of [["r1", "已验收"], ["r2", "已验收"], ["r3", "执行完成"], ["r5", "已取消"]] as const) {
      expect(within(row(id)).queryByText(word, { exact: true })).toBeNull();
      expect(row(id).getAttribute("aria-label")).toContain(word);
    }
    // What needs a person or is still running keeps its word.
    for (const [id, word] of [["r4", "执行中"], ["r6", "等待验收"]] as const) {
      expect(within(row(id)).getByText(word, { exact: true })).toBeTruthy();
    }
    // Queued keeps its word too: it is the one state that draws no band at all.
    const timeline = macroTaskTimelineFixture();
    timeline.rows[4] = { ...timeline.rows[4]!, state: "queued", status: "queued", category: "ended" };
    cleanup();
    setup({ timeline });
    expect(within(row("r4")).getByText("排队中", { exact: true })).toBeTruthy();
    expect(row("r4").getAttribute("data-state-tone")).toBe("queued");
  });
  it("fades the model-and-effort line one step below the state word, and restores it on the current row", () => {
    const { props, rerender } = setup();
    const secondLine = (id: string) => row(id).querySelector(":scope > span + span")!;
    expect(secondLine("r2").className).toContain("text-ink4");
    rerender(<TooltipProvider><TaskWork {...props} openedRunId="r2"/></TooltipProvider>);
    rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
    expect(secondLine("r2").className).toContain("text-ink3");
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
  it("writes the repaired task title and reveals the full title with the buddy's identity on hover", async () => {
    const timeline = macroTaskTimelineFixture();
    const target = "被修正的任务：" + "完整标题".repeat(20);
    Object.assign(timeline.rows[1]!, { fixes: "other-macro-task", fixesTitle: target, fixesTitleSource: "task" });
    timeline.rows[0]!.fixedBy = ["r2"];
    setup({ timeline });
    expect(row("r2").querySelector('[data-slot=fix-link]')?.textContent).toBe(`↪ ${target}`);
    expect(row("r1").querySelector('[data-slot=fixed-by]')?.textContent).toBe("↩ 1");
    expect(row("r2").getAttribute("aria-label")).toContain(target);
    expect(row("r2").querySelector("img, [data-slot=harness-logo]")).toBeTruthy();
    await userEvent.hover(row("r2"));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain(target);
    expect(tooltip.textContent).toContain("codex · openai · gpt-5-codex · max");
  });
  it("explains incomplete reads and retains frozen source facts with retry", () => {
    const timeline = macroTaskTimelineFixture({ filtered: true });
    const { props } = setup({ timeline, error: "offline", stale: true });
    expect(status()).toContain("记录没有读全");
    expect(screen.getByRole("alert").textContent).toContain("时间冻结");
    fireEvent.click(screen.getByRole("button", { name: "重试连接" }));
    expect(props.onRetry).toHaveBeenCalledOnce();
    expect(document.querySelector('[data-slot=timeline-axis]')).toBeTruthy();
  });
});

describe("Lead lanes from recorded ownership", () => {
  const laneFixture = () => {
    const timeline = macroTaskTimelineFixture();
    const lead = timeline.rows.find(entry => entry.runId === "r2")!;
    lead.delegationAuthority = true;
    lead.rootLeadRunId = "r2";
    for (const runId of ["r3", "r6"]) {
      const member = timeline.rows.find(entry => entry.runId === runId)!;
      member.rootLeadRunId = "r2";
    }
    // Two recorded executions carry native usage; the cache is inside the input and never counted twice.
    timeline.spans.find(span => span.spanId === "s-r2-e1")!.tokenUsage = { inputTokens: 100, cachedInputTokens: 40, outputTokens: 10, source: "codex-native", scope: "attempt", inputBasis: "includes-cached" };
    timeline.spans.find(span => span.spanId === "s-r3-e1")!.tokenUsage = { inputTokens: 50, cachedInputTokens: 0, outputTokens: 5, source: "codex-native", scope: "attempt", inputBasis: "includes-cached" };
    return timeline;
  };
  it("groups one Lead with the micro tasks it owns and keeps the rest ordinary", () => {
    const timeline = laneFixture();
    setup({ timeline });
    const lanes = document.querySelectorAll('[data-slot=timeline-lane]');
    expect(lanes).toHaveLength(1);
    const lane = lanes[0]!;
    expect(lane.querySelector('[data-slot=timeline-lane-lead]')?.getAttribute("data-run-id")).toBe("r2");
    expect(lane.textContent).toContain("Lead");
    expect([...lane.querySelectorAll("[data-slot=timeline-row-name]")].map(node => node.getAttribute("data-run-id"))).toEqual(["r2", "r3", "r6"]);
    expect(document.querySelector('[data-slot=timeline-lane-lead] [data-slot=lane-toggle]')?.getAttribute("aria-controls")).toBe("lane-r2");
    // The ordinary rows follow under their own heading, outside the lane; the Lead's own name is the header's row.
    expect(document.querySelectorAll('[data-slot=timeline-row] [data-run-id="r1"], [data-slot=timeline-row] [data-run-id="r4"], [data-slot=timeline-row] [data-run-id="r5"]')).toHaveLength(3);
    expect(document.querySelectorAll("[data-slot=timeline-row-name]")).toHaveLength(6);
    expect(document.body.textContent).toContain("其他微任务");
  });
  it("summarizes the lane on the Lead row's hover: members, elapsed, execution and split usage", async () => {
    const user = userEvent.setup();
    const timeline = laneFixture();
    setup({ timeline });
    await user.hover(row("r2"));
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain("3 个微任务");
    expect(tooltip.textContent).toMatch(/历时/);
    expect(tooltip.textContent).toMatch(/执行/);
    // Cache is inside the input and named beside it; the routing run and turns without a record mark the total partial.
    expect(tooltip.textContent).toContain("输入 150（含缓存 40） · 输出 15 · 部分记录");
    // A read where most executions have no recorded usage is honest about it.
    timeline.spans.find(span => span.spanId === "s-r3-e1")!.tokenUsage = undefined;
    cleanup();
    setup({ timeline });
    await user.hover(row("r2"));
    expect((await screen.findByRole("tooltip")).textContent).toContain("输入 100（含缓存 40） · 输出 10 · 部分记录");
  });
  it("collapses one lane without touching the others, keeping the Lead's own header", async () => {
    const timeline = laneFixture();
    const otherLead = timeline.rows.find(entry => entry.runId === "r1")!;
    otherLead.delegationAuthority = true;
    otherLead.rootLeadRunId = "r1";
    setup({ timeline });
    const user = userEvent.setup();
    const toggleOf = (lane: string) => document.querySelector<HTMLElement>(`[data-slot=lane-toggle][aria-controls="lane-${lane}"]`)!;
    expect(document.querySelectorAll('[data-slot=lane-toggle]')).toHaveLength(2);
    const lanes = document.querySelectorAll("[data-slot=timeline-lane]");
    expect(lanes[0]!.className).not.toContain("mt-6");
    expect(lanes[1]!.className).toContain("mt-6");
    await user.click(toggleOf("r2"));
    expect(toggleOf("r2").getAttribute("aria-expanded")).toBe("false");
    expect(toggleOf("r1").getAttribute("aria-expanded")).toBe("true");
    expect(document.querySelector("#lane-r2")).toBeNull();
    await user.click(toggleOf("r2"));
    expect(within(document.querySelector("#lane-r2")!).getByText("补充 schema 升级离线副本的验证测试")).toBeTruthy();
  });
  it("preserves a native usage record's completeness in the lane total without counting cached input twice", () => {
    const timeline = laneFixture();
    const lead = timeline.rows.find(row => row.runId === "r2")!;
    const execution = timeline.spans.find(span => span.spanId === "s-r2-e1")!;
    timeline.spans = [execution];
    execution.tokenUsage = { inputTokens: 100, cachedInputTokens: 40, outputTokens: 10, source: "native", scope: "attempt", inputBasis: "includes-cached", completeness: "complete" };
    expect(laneSummary({ lead, members: [] }, timeline).usage).toEqual({ input: 100, cached: 40, output: 10, partial: false });
    execution.tokenUsage = { ...execution.tokenUsage as object, completeness: "partial" };
    expect(laneSummary({ lead, members: [] }, timeline).usage).toEqual({ input: 100, cached: 40, output: 10, partial: true });
  });
  it("includes recorded Router usage and distinguishes missing counts from recorded zeros", () => {
    const timeline = laneFixture();
    const lead = timeline.rows.find(row => row.runId === "r2")!;
    const execution = timeline.spans.find(span => span.spanId === "s-r2-e1")!;
    execution.tokenUsage = { inputTokens: 100, cachedInputTokens: 40, outputTokens: 10, source: "native", scope: "attempt", inputBasis: "includes-cached", completeness: "complete" };
    const routing = { ...execution, kind: "routing" as const, spanId: "router-usage", tokenUsage: {
      inputTokens: 10, cachedInputTokens: 5, outputTokens: 2, source: "native", scope: "attempt", inputBasis: "excludes-cached", completeness: "complete",
    } };
    timeline.spans = [execution, routing];
    expect(laneSummary({ lead, members: [] }, timeline).usage).toEqual({ input: 115, cached: 45, output: 12, partial: false });
    const unknown = { ...execution, tokenUsage: { source: "native", scope: "attempt", completeness: "unknown" } };
    timeline.spans = [unknown];
    expect(laneSummary({ lead, members: [] }, timeline).usage).toEqual({ input: null, cached: null, output: null, partial: false });
    timeline.spans = [execution, unknown];
    expect(laneSummary({ lead, members: [] }, timeline).usage).toEqual({ input: 100, cached: 40, output: 10, partial: true });
  });
  it("gives the Lead's own row the member rows' identity cell: one keyboard path and the full tuple on hover", async () => {
    const timeline = laneFixture();
    setup({ timeline });
    // The Lead's name is the lane header's own name button, on the same registry as every row.
    const leadName = row("r2");
    expect(leadName).toBeTruthy();
    expect(leadName.getAttribute("data-key")).toBe("row:r2");
    expect(leadName.getAttribute("aria-label")).toContain("Lead");
    expect(leadName.className).toContain("h-[60px]");
    expect(row("r3").className).toContain("h-[52px]");
    // The Lead row ends in a divider that crosses the whole row; its members sit one indent step inside it.
    const header = document.querySelector<HTMLElement>('[data-slot=timeline-lane-lead]')!;
    expect(header.className).toContain("border-b-2 border-ink2");
    expect(leadName.style.paddingLeft).toBe("13px");
    expect(row("r3").style.paddingLeft).toBe("49px");
    expect(row("r6").style.paddingLeft).toBe("31px");
    await userEvent.hover(leadName);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain("codex · openai · gpt-5-codex · max");
    // One keyboard path walks the Lead, its members and the ordinary rows in order.
    leadName.focus();
    fireEvent.keyDown(leadName, { key: "ArrowDown" });
    expect(document.activeElement).toBe(row("r3"));
    fireEvent.keyDown(row("r3"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(leadName);
  });

  it("keeps collapse and open apart: the toggle is the name's sibling and collapsing opens nothing", async () => {
    const timeline = laneFixture();
    const { props } = setup({ timeline });
    const user = userEvent.setup();
    const toggle = document.querySelector<HTMLElement>('[data-slot=lane-toggle][aria-controls="lane-r2"]')!;
    const name = row("r2");
    // Two sibling native buttons: the collapse control is not inside the row's own button.
    expect(toggle.tagName).toBe("BUTTON");
    expect(name.tagName).toBe("BUTTON");
    expect(name.contains(toggle)).toBe(false);
    expect(toggle.closest("header")).toBe(name.closest("header"));
    const opens = () => (props.onOpen as ReturnType<typeof vi.fn>).mock.calls.length;
    const before = opens();
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector("#lane-r2")).toBeNull();
    expect(opens()).toBe(before);
    await user.click(toggle);
    expect(within(document.querySelector("#lane-r2")!).getByText("补充 schema 升级离线副本的验证测试")).toBeTruthy();
    expect(opens()).toBe(before);
    // The name itself is what opens the Lead's own detail.
    await user.click(name);
    expect(opens()).toBe(before + 1);
    expect(props.onOpen).toHaveBeenLastCalledWith("r2");
  });

  it("walks only the rows that are there: a collapsed lane's unmounted members are skipped", async () => {
    const timeline = laneFixture();
    const user = userEvent.setup();
    setup({ timeline });
    await user.click(document.querySelector<HTMLElement>('[data-slot=lane-toggle][aria-controls="lane-r2"]')!);
    row("r2").focus();
    fireEvent.keyDown(row("r2"), { key: "ArrowDown" });
    // The lane is collapsed: the next visible row is the first ordinary one.
    expect(document.activeElement).toBe(row("r1"));
    fireEvent.keyDown(row("r1"), { key: "ArrowUp" });
    expect(document.activeElement).toBe(row("r2"));
  });

  it("derives a Lead's waits for owned micro tasks only from a recorded waiting disposition and the next wakeup", () => {
    const timeline = laneFixture();
    const lead = timeline.rows.find(entry => entry.runId === "r2")!;
    const waiting = timeline.spans.find(span => span.spanId === "s-r2-e1")!;
    waiting.disposition = "waiting";
    const derived = ownedWaits(lead, timeline.spans, Date.parse(timeline.observedAt));
    expect(derived).toEqual([{ startMs: Date.parse("2026-09-26T02:10:00Z"), endMs: Date.parse("2026-09-26T03:00:00Z"), open: false }]);
    // Without a recorded waiting disposition nothing is invented, and an open wait runs only while the row waits.
    waiting.disposition = undefined;
    expect(ownedWaits(lead, timeline.spans, Date.parse(timeline.observedAt))).toEqual([]);
    const openLead = { ...lead, state: "waiting-owned" } as TimelineRow;
    const openWaiting = timeline.spans.find(span => span.spanId === "s-r2-e2")!;
    openWaiting.disposition = "waiting";
    timeline.spans = timeline.spans.filter(span => span.spanId !== "s-r2-w");
    const now = Date.parse(timeline.observedAt);
    expect(ownedWaits(openLead, timeline.spans, now)).toEqual([{ startMs: Date.parse("2026-09-26T03:10:00Z"), endMs: now, open: true }]);
    expect(groupLanes([lead]).lanes[0]!.members).toEqual([]);
  });
});

describe("timeline keyboard and screen reader navigation", () => {
  const fragments = (id: string) => Array.from(document.querySelectorAll<SVGElement>(`[data-slot=timeline-row][data-run-id="${id}"] [data-slot=timeline-track] [data-key]`))
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
    expect(screen.getByRole("region", { name: "这个宏任务的微任务" })).toBeTruthy();
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

  it("opens adjacent micro tasks with vertical arrows while the detail is open", async () => {
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
    expect(row("r3").getAttribute("aria-current")).toBe("true"); expect(document.activeElement).toBe(row("r3"));
    expect(row("r2").getAttribute("aria-current")).toBeNull();
    screen.getByRole("button", { name: "Detail control" }).focus();
    await user.keyboard("{ArrowUp}");
    expect(row("r2").getAttribute("aria-current")).toBe("true"); expect(document.activeElement).toBe(row("r2"));
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

it("assigns colours by the buddy's full identity and keeps them across a later poll", () => {  const timeline = macroTaskTimelineFixture();
  const source = timeline.spans.find(span => span.kind === "execution")!;
  const start = Date.parse(source.startAt!);
  const base = source.buddy!;
  const buddy = (overrides: Partial<TimelineBuddy>): TimelineBuddy => ({ ...base, ...overrides });
  // The same full identity shares one colour; the same model under another effort is a different buddy.
  const identities = [buddy({}), buddy({ effort: "low" }), buddy({})];
  timeline.spans = identities.map((identity, index) => ({ ...source, spanId: `identity-${index}`, startAt: new Date(start + index * 120000).toISOString(), endAt: new Date(start + index * 120000 + 60000).toISOString(), state: "finished", uncertain: false, shutdownConfirmed: true, resultStatus: "ok", buddy: identity }));
  // One registry per macro task: a fresh id keeps this test's assignment independent of the file's earlier renders.
  timeline.macroTask = { ...timeline.macroTask, macroTaskId: "macro-colours" };
  const { props, rerender } = setup({ timeline });
  const colors = () => [...document.querySelectorAll<SVGElement>('[data-slot=execution-bar]')].map(bar => bar.style.fill);
  expect(colors()).toEqual(["var(--model-1)", "var(--model-2)", "var(--model-1)"]);
  const next = { ...timeline, spans: [...timeline.spans, { ...timeline.spans[2]!, spanId: "later-buddy", startAt: new Date(start + 360000).toISOString(), endAt: new Date(start + 420000).toISOString(), buddy: buddy({ harness: "codex" }) }] };
  rerender(<TooltipProvider><TaskWork {...props} timeline={next}/></TooltipProvider>);
  expect(colors()).toEqual(["var(--model-1)", "var(--model-2)", "var(--model-1)", "var(--model-3)"]);
  // A poll that drops the first record keeps every remaining colour where it was; the freed colour is not reassigned.
  const filtered = { ...next, spans: next.spans.filter(span => span.spanId !== "identity-0"), filtered: true };
  rerender(<TooltipProvider><TaskWork {...props} timeline={filtered}/></TooltipProvider>);
  expect(colors()).toEqual(["var(--model-2)", "var(--model-1)", "var(--model-3)"]);
});

it("shares one zoom control group with the detail, in the fixed order", () => {
  setup();
  const zoom = document.querySelector('[data-slot=timeline-zoom]')!;
  const labels = [...zoom.querySelectorAll("button")].map(button => button.getAttribute("aria-label"));
  expect(labels).toEqual(["缩小（-）", "放大（+）", "适应窗口（0）"]);
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

describe("one shared scroll area", () => {
  // jsdom reports no overflow, so Radix never mounts the hover-gated bars here;
  // their looks and hover behaviour are checked in the real browser.

  it("holds the names and the tracks in one viewport, so they cannot drift apart", () => {
    setup();
    const tracks = document.querySelector<HTMLElement>('[data-slot="timeline-tracks"]')!;
    expect(tracks.querySelectorAll("[data-slot=timeline-row-name]").length).toBeGreaterThan(0);
    expect(tracks.querySelector("[data-slot=timeline-canvas] [data-slot=timeline-ruler]")).toBeTruthy();
    // The detail's list is the only other scroller, and it lives outside the lanes.
    expect(document.querySelectorAll("[data-slot=scroll-area-viewport]").length).toBeLessThanOrEqual(2);
  });

  it("carries no lanes viewport while a detail is open", () => {
    const { props, rerender } = setup();
    rerender(<TooltipProvider><TaskWork {...props} openedRunId="r1"/></TooltipProvider>);
    expect(document.querySelector('[data-slot=timeline-tracks]')).toBeNull();
    expect(document.querySelector('[data-slot=task-names] [data-slot=timeline-row-name]')).toBeTruthy();
  });
});

it("reveals the complete author title on hover when the shown one is cut", async () => {
  const timeline = macroTaskTimelineFixture();
  const fromTask = "从任务首行取得的很长标题：".repeat(6);
  const longAuthor = "作者写下的很长标题，鼠标或键盘聚焦时应能看到全文：".repeat(4);
  const rows = [
    { ...timeline.rows[1]!, runId: "r9", titleSource: "task" as const, title: fromTask },
    { ...timeline.rows[1]!, runId: "r10", title: longAuthor },
  ];
  render(<TooltipProvider><RowName row={rows[0]!} rows={rows} tabIndex={0} current={false} fresh={false}
    onClick={() => {}} onEnter={() => {}} onLeave={() => {}} onFocus={() => {}} onBlur={() => {}}/></TooltipProvider>);
  await userEvent.hover(document.querySelector('[data-slot=timeline-row-name]')!);
  let tooltip = await screen.findByRole("tooltip");
  expect(tooltip.textContent).toContain(fromTask);
  cleanup();
  // An explicit author title that the cell cuts: the same hook that reports the cut
  // at runtime decides the tooltip here too.
  const described = Object.getOwnPropertyDescriptor(Element.prototype, "scrollWidth")!;
  Object.defineProperty(Element.prototype, "scrollWidth", { configurable: true, get() { return 600; } });
  try {
    render(<TooltipProvider><RowName row={rows[1]!} rows={rows} tabIndex={0} current={false} fresh={false}
      onClick={() => {}} onEnter={() => {}} onLeave={() => {}} onFocus={() => {}} onBlur={() => {}}/></TooltipProvider>);
    await userEvent.hover(document.querySelector('[data-slot=timeline-row-name]')!);
    tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain(longAuthor);
  } finally {
    Object.defineProperty(Element.prototype, "scrollWidth", described);
  }
});

it("marks the row's harness beside its model and effort, and names the full identity on hover", async () => {
  const timeline = macroTaskTimelineFixture();
  const named = timeline.rows[1]!;
  const unnamed = { ...timeline.rows[1]!, runId: "r9", buddy: { harness: null, provider: null, model: null, effort: null } };
  for (const subject of [named, unnamed]) {
    render(<TooltipProvider><RowName row={subject} rows={timeline.rows} tabIndex={0} current={false} fresh={false}
      onClick={() => {}} onEnter={() => {}} onLeave={() => {}} onFocus={() => {}} onBlur={() => {}}/></TooltipProvider>);
    const button = document.querySelector('[data-slot=timeline-row-name]')!;
    expect(Boolean(button.querySelector("img, [data-slot=harness-logo]"))).toBe(Boolean(subject.buddy!.harness));
    if (subject.buddy!.harness) expect(button.textContent).toContain("gpt-5-codex · max");
    await userEvent.hover(button);
    const tooltip = await screen.findByRole("tooltip");
    expect(tooltip.textContent).toContain(subject.buddy!.harness
      ? "codex · openai · gpt-5-codex · max"
      : "未记录 · 未记录 · 未记录 · 未记录");
    cleanup();
  }
});


it("returns to the lanes where the reader left them: scroll and zoom survive a detail round trip", () => {
  vi.stubGlobal("matchMedia", () => ({ matches: true, addEventListener: () => {}, removeEventListener: () => {} }));
  const { props, rerender } = setup();
  const before = document.querySelector<HTMLElement>('[data-slot="timeline-tracks"]')!;
  fireEvent.keyDown(row("r1"), { key: "+" });
  const ppm = document.querySelector("[data-slot=timeline-canvas]")!.getAttribute("data-ppm");
  before.scrollLeft = 130;
  before.scrollTop = 40;
  fireEvent.scroll(before);
  rerender(<TooltipProvider><TaskWork {...props} openedRunId="r1"/></TooltipProvider>);
  rerender(<TooltipProvider><TaskWork {...props} openedRunId={null}/></TooltipProvider>);
  const back = document.querySelector<HTMLElement>('[data-slot="timeline-tracks"]')!;
  expect(back).toBeTruthy();
  expect(back.scrollLeft).toBe(130);
  expect(back.scrollTop).toBe(40);
  expect(document.querySelector("[data-slot=timeline-canvas]")!.getAttribute("data-ppm")).toBe(ppm);
});

it("sums a lane's elapsed and execution time from its own rows alone", () => {
  const timeline = macroTaskTimelineFixture();
  const lead = { ...timeline.rows[1]!, delegationAuthority: true, rootLeadRunId: "r2" } as TimelineRow;
  const helper = { ...timeline.rows[2]!, rootLeadRunId: "r2" };
  const layout = groupLanes([lead, helper, timeline.rows[0]!]);
  expect(layout.lanes).toHaveLength(1);
  expect(layout.lanes[0]!.members.map(row => row.runId)).toEqual(["r3"]);
  expect(layout.ordinary.map(row => row.runId)).toEqual(["r1"]);
  const summary = laneSummary(layout.lanes[0]!, timeline);
  // r2's two executions and its Router run, plus r3's two helper executions, each counted; elapsed covers the lane's own span.
  expect(summary.elapsedMs).toBeGreaterThan(0);
  expect(summary.executionMs).toBe((46 + 10 + 3 + 22 + 26) * 60_000);
  expect(summary.usage).toEqual({ input: null, cached: null, output: null, partial: false });
});
