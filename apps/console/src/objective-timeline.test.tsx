import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";

function baseProps(timeline: ObjectiveTimelineData | null, overrides: Partial<ObjectiveTimelineProps> = {}): ObjectiveTimelineProps {
  return {
    summary: timeline?.objective ?? null, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, locked: false,
    expandedGapIds: new Set<string>(), onToggleGap: vi.fn(), onSetExpanded: vi.fn(),
    onOpenItem: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(), ...overrides,
  };
}

/** Stateful harness so fold/expand flows through the parent-owned set. */
function FoldHarness({ timeline, onSetExpanded }: { timeline: ObjectiveTimelineData; onSetExpanded?: (ids: Set<string>) => void }) {
  const [expanded, setExpanded] = useState(new Set<string>());
  return <ObjectiveTimeline {...baseProps(timeline, {
    expandedGapIds: expanded,
    onToggleGap: gapId => setExpanded(previous => {
      const next = new Set(previous);
      if (next.has(gapId)) next.delete(gapId); else next.add(gapId);
      return next;
    }),
    onSetExpanded: ids => { setExpanded(ids); onSetExpanded?.(ids); },
  })} />;
}

afterEach(() => cleanup());

const item = (key: string) => document.querySelector(`[data-key="${key}"]`) as HTMLButtonElement | null;

describe("objective timeline rendering", () => {
  it("shows receipt cancellation even with an error reason and no cancelled turn disposition", () => {
    const timeline = objectiveTimelineFixture();
    const cancelled = timeline.spans.find(span => span.spanId === "s-r5-e")!;
    cancelled.disposition = undefined;
    cancelled.resultStatus = "cancelled";
    cancelled.error = "Host cancelled the attempt";
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(item("span:s-r5-e")!.getAttribute("aria-label")).toContain("已取消");
    expect(item("span:s-r5-e")!.className).not.toContain("failed");
  });

  it("shows the recorded header facts and the archival note", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const head = document.querySelector(".tl-head") as HTMLElement;
    expect(within(head).getByText("工作目标时间轴：设计、接口与实现")).toBeTruthy();
    expect(within(head).getByText("来源 Host：codex-desktop")).toBeTruthy();
    expect(within(head).getByText(/共 6 个委派（含 1 个协助任务）/)).toBeTruthy();
    expect(within(head).getByText(/最近活动/)).toBeTruthy();
    expect(within(head).getByText("进行中 1")).toBeTruthy();
    expect(within(head).getByText(/工作目标只用于归档与浏览，不调度任务，也不作为验收条件/)).toBeTruthy();
  });

  it("labels a standalone root honestly and keeps its note distinct", () => {
    const timeline = objectiveTimelineFixture();
    timeline.objective = { ...timeline.objective, kind: "standalone", title: "修复标题回退在 CRLF 输入下的显示" };
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(screen.getByText("独立委派")).toBeTruthy();
    expect(screen.getByText(/这条旧记录没有工作目标，按字段为空的规则单独显示，不与其他记录合并/)).toBeTruthy();
  });

  it("renders every span kind and terminal state with non-colour markers", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(item("span:s-r1-q")!.className).toContain("sp queue");
    const routing = item("span:s-r2-r")!;
    expect(routing.className).toContain("routing");
    expect(routing.getAttribute("aria-label")).toContain("路由，实现 objectives 表、objective_list 与只读时间轴接口，内部路由计算 run-d02b");
    const failed = item("span:s-r3-e1")!;
    expect(failed.className).toContain("failed");
    expect(failed.querySelector(".end-mark.bad")!.textContent).toBe("✕");
    expect(failed.getAttribute("aria-label")).toContain("执行失败");
    expect(failed.getAttribute("aria-label")).toContain("测试命令超时");
    const cancelled = item("span:s-r5-e")!;
    expect(cancelled.className).toContain("cancelled");
    expect(cancelled.querySelector(".end-mark.muted")!.textContent).toBe("⊘");
    expect(cancelled.getAttribute("aria-label")).toContain("已取消");
    const running = item("span:s-r4-e")!;
    expect(running.className).toContain("running");
    expect(running.querySelector(".pulse")).toBeTruthy();
    expect(running.getAttribute("aria-label")).toContain("执行中");
    const unknown = item("span:s-r6-e")!;
    expect(unknown.className).toContain("unknown");
    expect(unknown.querySelector(".end-mark.warn")!.textContent).toBe("?");
    expect(unknown.getAttribute("aria-label")).toContain("结束未确认");
    const wait = item("span:s-r2-w")!;
    expect(wait.className).toContain("wait");
    expect(wait.getAttribute("aria-label")).toContain("已批准");
    // 结束未确认 never renders as a stopped claim anywhere.
    expect(document.body.textContent).not.toContain("已停止");
  });

  it("draws the now line while an open tail reaches the observation instant", () => {
    const timeline = objectiveTimelineFixture();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(container.querySelector(".now-line")).toBeTruthy();
    expect(container.querySelector(".now-chip")!.textContent).toMatch(/^现在 \d{2}:\d{2}$/);
  });

  it("assigns configuration colours in first-appearance order for the legend", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const legend = screen.getByLabelText("执行配置图例");
    const items = [...legend.querySelectorAll(".legend-item")].map(node => node.textContent);
    expect(items).toEqual(["claude / claude-opus-5-5", "codex / gpt-5-codex", "zcode / glm-5", "claude / claude-sonnet-5"]);
    const stateLegend = screen.getByLabelText("状态图例");
    expect(stateLegend.textContent).toContain("排队");
    expect(stateLegend.textContent).toContain("路由");
    expect(stateLegend.textContent).toContain("等待 Host");
    expect(stateLegend.textContent).toContain("结束未确认");
  });

  it("renders rows in tree order with helper indentation and a full row label", () => {
    const timeline = objectiveTimelineFixture();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const labels = [...container.querySelectorAll(".tl-row[data-nav] .tl-label")].slice(1).map(node => node.textContent);
    expect(labels[0]).toContain("设计工作目标时间轴视图与交互规范");
    expect(labels[2]).toContain("↳");
    expect(labels[2]).toContain("补充 schema 升级离线副本的验证测试");
    const helper = container.querySelectorAll(".tl-row[data-nav] .tl-label")[3]! as HTMLButtonElement;
    expect(helper.getAttribute("aria-label")).toContain("委派，补充 schema 升级离线副本的验证测试，层级 2，协助任务");
    expect(helper.getAttribute("aria-label")).toContain("标题来源：Host 标题");
  });

  it("folds long idle stretches, expands one break and offers expand/collapse all", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onSetExpanded = vi.fn();
    render(<FoldHarness timeline={timeline} onSetExpanded={onSetExpanded} />);
    const folds = screen.getAllByRole("button", { name: /已折叠，展开/ });
    expect(folds.length).toBe(2);
    await user.click(folds[0]!);
    expect(await screen.findByRole("button", { name: /^收起空闲/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /已折叠，展开/ }).length).toBe(1);
    await user.click(screen.getByRole("button", { name: "展开全部空闲" }));
    expect(onSetExpanded).toHaveBeenCalledWith(expect.any(Set));
    expect(screen.queryByRole("button", { name: /已折叠，展开/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "折叠空闲" }));
    expect(screen.getAllByRole("button", { name: /已折叠，展开/ }).length).toBe(2);
  });

  it("refuses automatic folding when the read is truncated or filtered and says so in the banner", () => {
    const truncated = objectiveTimelineFixture({
      totals: { rows: 6, spans: 20, events: 12, allRows: 6 },
      truncated: { rows: false, spans: true, events: true },
      scopeComplete: false,
    });
    const first = render(<ObjectiveTimeline {...baseProps(truncated)} />);
    expect(screen.queryByRole("button", { name: /已折叠，展开/ })).toBeNull();
    const banner = screen.getByRole("status");
    expect(banner.textContent).toContain("执行片段已截断：显示 15 / 20 段");
    expect(banner.textContent).toContain("Host 事件已截断：显示 9 / 12 条");
    expect(first.container.querySelector(".trunc-chip")!.textContent).toContain("‹ 3 条事件未返回");
    first.unmount();
    const filtered = objectiveTimelineFixture({ filtered: true, scopeComplete: false, totals: { rows: 2, spans: 15, events: 9, allRows: 6 } });
    const second = render(<ObjectiveTimeline {...baseProps(filtered)} />);
    expect(second.getByRole("status").textContent).toContain("已按筛选显示 6 / 6 个委派");
    expect(second.queryByRole("button", { name: /已折叠，展开/ })).toBeNull();
  });

  it("merges nearby Host markers into one numbered cluster with per-event opens", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    const cluster = [...container.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    expect(cluster.textContent).toBe("2");
    expect(cluster.getAttribute("aria-label")).toContain("Host 事件：决定");
    expect(cluster.getAttribute("aria-label")).toContain("Host 事件：续接");
    act(() => { cluster.focus(); });
    expect(container.querySelectorAll(".cluster-line").length).toBe(2);
    const opens = [...container.querySelectorAll<HTMLButtonElement>(".cluster-line .button")];
    expect(opens.length).toBe(2);
    await user.click(opens[0]!);
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ section: "assistance" }));
    const single = container.querySelector<HTMLButtonElement>('[data-key="events:4"]')!;
    await user.click(single);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r1", section: "artifacts" }));
  });

  it("keeps the inspector honest: facts for the focused item, a hint when nothing is focusable", () => {
    const timeline = objectiveTimelineFixture();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    // The default focus item is the first delegation row's first execution span.
    const inspector = container.querySelector(".inspector")!;
    expect(inspector.textContent).toContain("执行片段");
    expect(inspector.textContent).toContain("设计工作目标时间轴视图与交互规范");
    expect(inspector.textContent).toContain("第 1 轮");
    expect(inspector.textContent).toContain("claude / claude-opus-5-5");
    expect(inspector.textContent).toContain("执行完成");
    expect(inspector.textContent).toContain("Enter 打开委派详情");
    const execution = item("span:s-r3-e1")!;
    act(() => { execution.focus(); });
    expect(container.querySelector(".inspector")!.textContent).toContain("测试命令超时");
  });

  it("moves keyboard focus within and across rows, and Escape returns to the toolbar", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const label = item("row:r1")!;
    act(() => { label.focus(); });
    expect(document.activeElement).toBe(label);
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(document.activeElement!.getAttribute("data-key")).toBe("span:s-r1-q");
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(document.activeElement!.getAttribute("data-key")).toBe("span:s-r1-e");
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(document.activeElement).toBe(label);
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    const last = document.activeElement!;
    expect(last.getAttribute("data-key")).toBe("settle:r1");
    fireEvent.keyDown(last, { key: "ArrowDown" });
    const below = document.activeElement!;
    expect(below).not.toBe(last);
    expect(below.tagName).toBe("BUTTON");
    fireEvent.keyDown(below, { key: "Escape" });
    expect(document.activeElement!.textContent).toContain("以列表查看");
  });

  it("falls back to the inspector hint when no item can be focused", () => {
    const timeline = objectiveTimelineFixture({ rows: [], spans: [], events: [], totals: { rows: 0, spans: 0, events: 0, allRows: 0 } });
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(container.querySelector(".inspector")!.textContent).toContain("悬停或聚焦片段查看记录");
  });

  it("shows skeleton, error and stale states without inventing data", async () => {
    const user = userEvent.setup();
    const loading = render(<ObjectiveTimeline {...baseProps(null, { loading: true })} />);
    expect(loading.getByRole("status").textContent).toContain("正在读取时间轴…");
    expect(loading.container.querySelectorAll(".skeleton").length).toBe(3);
    loading.unmount();
    const failed = render(<ObjectiveTimeline {...baseProps(null, { error: "网络中断" })} />);
    expect(failed.getByRole("alert").textContent).toContain("读取时间轴失败");
    await user.click(failed.getByRole("button", { name: "重试读取" }));
    failed.unmount();
    const retry = vi.fn();
    const stale = objectiveTimelineFixture();
    const kept = render(<ObjectiveTimeline {...baseProps(stale, { error: "网络中断", stale: true, onRetry: retry })} />);
    expect(kept.getByText(/显示的是 \d{2}:\d{2}:\d{2} 的数据/)).toBeTruthy();
    expect(kept.container.querySelector(".tl-grid")).toBeTruthy();
    await user.click(kept.getAllByRole("button", { name: "重试读取" })[0]!);
    expect(retry).toHaveBeenCalled();
  });

  it("offers the chronological list with idle separators and honest results", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    const toggle = screen.getByRole("button", { name: "以列表查看" })!;
    await user.click(toggle);
    expect(toggle.getAttribute("aria-pressed")).toBe("true");
    expect(container.querySelector(".timeline-body")!.className).toContain("as-list");
    const list = container.querySelector(".tl-list")!;
    const gaps = [...list.querySelectorAll(".tl-gap")];
    expect(gaps.length).toBe(2);
    expect(gaps[0]!.textContent).toMatch(/^空闲 \d+ (小时|分钟|分)/);
    expect(gaps[0]!.textContent).toContain("没有任何片段或事件");
    const entries = [...list.querySelectorAll(".tl-entry")];
    expect(entries.length).toBe(timeline.spans.length + timeline.events.length);
    const helperEntry = entries.find(node => node.textContent!.includes("补充 schema 升级离线副本的验证测试"))!;
    expect(helperEntry.textContent).toContain("↳ 协助 · ");
    const helperExecution = entries.find(node => node.getAttribute("data-key") === "span:s-r3-e1")!;
    expect(helperExecution.textContent).toContain("zcode / glm-5");
    const failedEntry = entries.find(node => node.textContent!.includes("执行失败"))!;
    expect(failedEntry.textContent).toContain("✕");
    const uncertainEntry = entries.find(node => node.getAttribute("data-key") === "span:s-r6-e")!;
    expect(uncertainEntry.textContent).toContain("结束未确认");
    await user.click(entries.find(node => node.getAttribute("data-key") === "span:s-r2-r")!);
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ runId: "run-d02b", section: "routing" }));
  });

  it("opens spans and settle flags into existing details with their section mapping", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    await user.click(item("span:s-r4-e")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r4", section: "execution" }));
    await user.click(item("span:s-r2-w")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r2", section: "assistance" }));
    await user.click(item("span:s-r1-q")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r1", section: "overview" }));
    await user.click(item("settle:r1")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r1", section: "artifacts" }));
    await user.click(item("row:r3")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r3", section: "overview" }));
  });

  it("opens a merged cluster's first recorded event on click while keeping per-event links", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    const cluster = [...container.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    await user.click(cluster);
    expect(onOpenItem).toHaveBeenCalledTimes(1);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r2", section: "assistance", key: "event:5" }));
    // Per-event inspector links still open each individual event.
    act(() => { cluster.focus(); });
    const opens = [...container.querySelectorAll<HTMLButtonElement>(".cluster-line .button")];
    await user.click(opens[1]!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "event:6", section: "execution" }));
  });

  it("seeds keyboard focus on a rendered marker key when only events exist", () => {
    const events = objectiveTimelineFixture().events;
    const timeline = objectiveTimelineFixture({ rows: [], spans: [], events, totals: { rows: 0, spans: 0, events: events.length, allRows: 0 } });
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const tabbable = [...container.querySelectorAll<HTMLButtonElement>(".tl-item")].filter(node => node.tabIndex === 0);
    expect(tabbable).toHaveLength(1);
    expect(tabbable[0]!.dataset.key).toMatch(/^events:\d+$/);
  });

  it("measures the viewport after the async canvas mounts, resizes and keeps breaks at 64px", async () => {
    const observers: Array<{ observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn>; callback: ResizeObserverCallback }> = [];
    vi.stubGlobal("ResizeObserver", class {
      observe = vi.fn();
      disconnect = vi.fn();
      unobserve = vi.fn();
      constructor(public callback: ResizeObserverCallback) {
        observers.push(this as unknown as { observe: ReturnType<typeof vi.fn>; disconnect: ReturnType<typeof vi.fn>; callback: ResizeObserverCallback });
      }
    });
    const widthOriginal = (() => {
      let target: object | null = HTMLElement.prototype;
      while (target) {
        const descriptor = Object.getOwnPropertyDescriptor(target, "clientWidth");
        if (descriptor) return { target, descriptor };
        target = Object.getPrototypeOf(target);
      }
      return null;
    })();
    let stubbedWidth = 900;
    Object.defineProperty(HTMLElement.prototype, "clientWidth", { configurable: true, get: () => stubbedWidth });
    try {
      const timeline = objectiveTimelineFixture();
      // First render is a skeleton: no canvas, no observer yet.
      const view = render(<ObjectiveTimeline {...baseProps(null, { loading: true })} />);
      expect(observers.length).toBe(0);
      expect(document.querySelector(".tl-grid")).toBeNull();
      view.rerender(<ObjectiveTimeline {...baseProps(timeline)} />);
      // The canvas mounts with the data; the observer attaches then.
      const grid = document.querySelector(".tl-grid") as HTMLElement;
      expect(observers.length).toBe(1);
      expect(observers[0]!.observe).toHaveBeenCalled();
      // 900px viewport minus the 240px fallback label column = 660px track.
      expect(grid.style.width).toBe("calc(var(--label-w) + 660px)");
      const band = document.querySelector(".fold-band") as HTMLElement;
      const bandPercent = Number.parseFloat(band.style.width);
      expect(bandPercent / 100 * 660).toBeCloseTo(64, 5);
      // A viewport resize remaps the same recorded facts without feedback.
      stubbedWidth = 1400;
      act(() => { observers[0]!.callback([], observers[0]! as unknown as ResizeObserver); });
      await waitFor(() => expect(grid.style.width).toBe("calc(var(--label-w) + 1160px)"));
      const remapped = Number.parseFloat((document.querySelector(".fold-band") as HTMLElement).style.width);
      expect(remapped / 100 * 1160).toBeCloseTo(64, 5);
    } finally {
      delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
      if (widthOriginal) Object.defineProperty(widthOriginal.target, "clientWidth", widthOriginal.descriptor);
      vi.unstubAllGlobals();
    }
  });

  it("treats unknown shutdown as outranking a recorded failure, keeping the failure text", () => {
    const timeline = objectiveTimelineFixture({
      rows: [{
        runId: "r7", parentRunId: null, rootRunId: "r7", title: "失败但停止未确认的委派", titleSource: "title", summary: null,
        createdAt: "2026-09-26T07:00:00Z", state: "failed", status: "failed", category: "ended",
        shutdownConfirmed: false, depth: 0, kind: "goal", configuration: null, acceptedAt: null, acceptanceVerdict: null,
      }],
      spans: [{
        spanId: "s-r7", runId: "r7", kind: "execution", startAt: "2026-09-26T07:00:00Z", endAt: "2026-09-26T07:20:00Z",
        state: "uncertain", attemptId: "att-r7", turnId: null, turnIndex: 1, requestId: null, configuration: null,
        shutdownConfirmed: false, uncertain: true, clockSkew: false, disposition: "failed", error: "执行失败的记录原因",
      }],
      events: [],
      totals: { rows: 1, spans: 1, events: 0, allRows: 1 },
    });
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const span = item("span:s-r7")!;
    expect(span.className).toContain("unknown");
    expect(span.className).not.toContain("failed");
    expect(span.querySelector(".end-mark.warn")!.textContent).toBe("?");
    expect(span.getAttribute("aria-label")).toContain("结束未确认");
    expect(span.getAttribute("aria-label")).toContain("执行失败的记录原因");
    expect(document.body.textContent).not.toContain("已停止");
  });

  it("says the end time is missing for a terminal span instead of borrowing now", () => {
    const timeline = objectiveTimelineFixture({
      rows: [{
        runId: "r8", parentRunId: null, rootRunId: "r8", title: "结束时间缺失的委派", titleSource: "task", summary: null,
        createdAt: "2026-09-26T07:00:00Z", state: "failed", status: "failed", category: "ended",
        shutdownConfirmed: true, depth: 0, kind: "goal", configuration: null, acceptedAt: null, acceptanceVerdict: null,
      }],
      spans: [{
        spanId: "s-r8", runId: "r8", kind: "execution", startAt: "2026-09-26T07:00:00Z", endAt: null,
        state: "finished", attemptId: "att-r8", turnId: null, turnIndex: 1, requestId: null, configuration: null,
        shutdownConfirmed: true, uncertain: false, clockSkew: false, disposition: "failed", error: "命令以失败结束",
      }],
      events: [],
      totals: { rows: 1, spans: 1, events: 0, allRows: 1 },
    });
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const label = document.querySelector('[data-key="row:r8"]') as HTMLButtonElement;
    expect(label.querySelector(".trunc-chip")!.textContent).toContain("1 段时间缺失");
    const span = item("span:s-r8")!;
    expect(span.getAttribute("aria-label")).toContain("结束时间缺失");
    expect(span.getAttribute("aria-label")).not.toContain("至 现在");
  });

  it("restores both scroll axes and refocuses the visible item, including the chronology", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    const view = render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    const scroll = document.querySelector(".tl-scroll") as HTMLElement;
    scroll.scrollTop = 130;
    scroll.scrollLeft = 240;
    // List view: open from a chronology entry so its key owns the focus.
    await user.click(screen.getByRole("button", { name: "以列表查看" })!);
    const entry = document.querySelector(".tl-list [data-key='span:s-r6-e']") as HTMLButtonElement;
    await user.click(entry);
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ key: "span:s-r6-e" }));
    // Hide (detail opens) and return: the chronology entry is the visible item.
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem, hidden: true })} />);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    expect(document.activeElement).toBe(entry);
    expect(scroll.scrollTop).toBe(130);
    expect(scroll.scrollLeft).toBe(240);
    // Back in canvas mode, the same key refocuses its canvas span.
    await user.click(screen.getByRole("button", { name: "以列表查看" })!);
    const canvasSpan = item("span:s-r6-e")!;
    await user.click(canvasSpan);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem, hidden: true })} />);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    expect(document.activeElement).toBe(canvasSpan);
    view.unmount();
  });

  it("keeps assigned configuration colours across refreshes and stripes overflow on the bars", () => {
    const first = objectiveTimelineFixture();
    const view = render(<ObjectiveTimeline {...baseProps(first)} />);
    const legendColors = () => [...screen.getByLabelText("执行配置图例").querySelectorAll(".legend-item .swatch")]
      .map(node => (node as HTMLElement).style.getPropertyValue("--c"));
    expect(legendColors()).toEqual(["var(--cfg-1)", "var(--cfg-2)", "var(--cfg-3)", "var(--cfg-4)"]);
    // A refresh adds a new configuration in an earlier row: existing ones keep
    // their slots and the newcomer takes the next one.
    const second = objectiveTimelineFixture();
    second.spans = [
      {
        ...second.spans[0], spanId: "s-r1-x", kind: "execution" as const, startAt: "2026-09-26T01:12:00Z", endAt: "2026-09-26T01:13:00Z",
        configuration: { adapter: "dsh", provider: "deepseek-official", model: "deepseek-v4-flash", effort: "high" },
      },
      ...second.spans,
    ];
    view.rerender(<ObjectiveTimeline {...baseProps(second)} />);
    const colors = legendColors();
    expect(colors[0]).toBe("var(--cfg-1)"); // claude-opus keeps its slot
    expect(colors.some(entry => entry === "var(--cfg-5)")).toBe(true); // the newcomer takes the next slot
    expect(colors).toHaveLength(5);
    view.unmount();
    // Beyond six configurations the execution bar itself gains the stripe; a
    // fresh mount starts a new per-objective assignment cache.
    const many = objectiveTimelineFixture();
    const configs = ["a", "b", "c", "d", "e", "f", "g"].map(model => ({ adapter: "zcode", provider: "bigmodel-api", model, effort: "high" }));
    many.rows = [many.rows[0]];
    many.spans = configs.map((configuration, index) => ({
      ...many.spans[0], spanId: `s-c${index}`, kind: "execution" as const, configuration,
      startAt: `2026-09-26T01:${String(10 + index).padStart(2, "0")}:00Z`, endAt: `2026-09-26T01:${String(11 + index).padStart(2, "0")}:00Z`,
    }));
    many.events = [];
    const overflow = render(<ObjectiveTimeline {...baseProps(many)} />);
    expect([...document.querySelectorAll(".sp.exec.striped")].length).toBe(1);
    expect(screen.getByLabelText("执行配置图例").querySelectorAll(".legend-item .swatch.striped").length).toBe(1);
    overflow.unmount();
  });
});
