import { useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveTimelineFixture, LONG_TASK_LINE } from "./objective-fixtures";
import type { ObjectiveTimeline as ObjectiveTimelineData } from "./objective-types";
import type { InspectorSelection } from "./inspector-card";
import type { TimelineItem } from "./objective-display";

function baseProps(timeline: ObjectiveTimelineData | null, overrides: Record<string, unknown> = {}): ObjectiveTimelineProps {
  const props: ObjectiveTimelineProps = {
    summary: timeline?.objective ?? null, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    expandedGapIds: new Set<string>(), onToggleGap: vi.fn(), onSetExpanded: vi.fn(),
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
  };
  return { ...props, ...overrides } as ObjectiveTimelineProps;
}

/** Stateful harness so selection and folds flow through parent-owned state. */
function Harness({ timeline, selectionSource }: {
  timeline: ObjectiveTimelineData;
  selectionSource?: { get: () => unknown };
}) {
  const [expanded, setExpanded] = useState(new Set<string>());
  const [selection, setSelection] = useState<InspectorSelection | null>(null);
  if (selectionSource) selectionSource.get = () => selection;
  return <ObjectiveTimeline {...baseProps(timeline, {
    selection,
    expandedGapIds: expanded,
    onToggleGap: (gapId: string) => setExpanded(previous => {
      const next = new Set(previous);
      if (next.has(gapId)) next.delete(gapId); else next.add(gapId);
      return next;
    }),
    onSetExpanded: (ids: Set<string>) => setExpanded(ids),
    onSelectItem: (item: TimelineItem) => setSelection({ type: "item", key: item.key }),
    onSelectRun: (runId: string) => setSelection({ type: "run", runId }),
    onClearSelection: () => setSelection(null),
  })} />;
}

afterEach(() => cleanup());

const item = (key: string) => document.querySelector(`[data-key="${key}"]`) as HTMLButtonElement | null;
/** P1.7: the Host event row starts collapsed; tests expand it first. */
async function expandHostEvents(user: ReturnType<typeof userEvent.setup>) {
  const toggle = screen.getByRole("button", { name: /[▸▾] Host 事件/ });
  if (toggle.getAttribute("aria-expanded") === "false") await user.click(toggle);
}

describe("objective timeline rendering", () => {
  it("keeps routing failure, cancellation, abstention and unknown stop distinct", () => {
    const timeline = objectiveTimelineFixture();
    const route = timeline.spans.find(span => span.spanId === "s-r2-r")!;
    route.resultStatus = "failed";
    const props = baseProps(timeline);
    const view = render(<ObjectiveTimeline {...props} />);
    expect(item("span:s-r2-r")!.querySelector(".routing-cross")).toBeTruthy();
    expect(item("span:s-r2-r")!.getAttribute("title")).toBe(item("span:s-r2-r")!.getAttribute("aria-label"));
    route.resultStatus = "cancelled";
    route.disposition = "abstention";
    view.rerender(<ObjectiveTimeline {...baseProps(structuredClone(timeline))} />);
    expect(item("span:s-r2-r")!.className).toContain("cancelled");
    expect(item("span:s-r2-r")!.querySelector(".routing-cross")).toBeNull();
    route.resultStatus = "ok";
    view.rerender(<ObjectiveTimeline {...baseProps(structuredClone(timeline))} />);
    expect(item("span:s-r2-r")!.getAttribute("aria-label")).toContain("已放弃选择");
    expect(item("span:s-r2-r")!.className).not.toContain("failed");
    route.uncertain = true;
    route.shutdownConfirmed = false;
    view.rerender(<ObjectiveTimeline {...baseProps(structuredClone(timeline))} />);
    expect(item("span:s-r2-r")!.className).toContain("unknown");
    expect(item("span:s-r2-r")!.getAttribute("aria-label")).toContain("结束未确认");
  });

  it("opens the exact routing decision with double click and Enter", () => {
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    fireEvent.doubleClick(item("span:s-r2-r")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r2", section: "routing", decisionId: "decision-r2-first" }));
    fireEvent.keyDown(item("span:s-r2-r")!, { key: "Enter" });
    expect(onOpenItem).toHaveBeenCalledTimes(2);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ decisionId: "decision-r2-first" }));
  });

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

  it("shows the vitals header, folded time statistics and the archival note", async () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const head = document.querySelector(".tl-head") as HTMLElement;
    expect(within(head).getByText("工作目标时间轴：设计、接口与实现")).toBeTruthy();
    // U2: the header beneath the title shows only status, accepted progress
    // and latest activity; the description sits right under the title.
    expect(within(head).getByText(/按用户认可的第 X 节设计工作目标时间轴/)).toBeTruthy();
    const vitals = head.querySelector(".objective-vitals") as HTMLElement;
    expect(vitals.textContent).toContain("进行中");
    expect(vitals.textContent).toContain("2 / 5 个委派已验收");
    expect(vitals.textContent).toMatch(/最近活动 \d{2}:\d{2}（/);
    // The plain-sentence durations and the record source fold into 时间统计 (§3).
    const stats = head.querySelector(".time-stats") as HTMLElement;
    expect(within(stats).getByText("时间统计")).toBeTruthy();
    const body = stats.querySelector(".time-stats-body") as HTMLElement;
    expect(body.textContent).toMatch(/从开始到最近一次活动：\S+/);
    expect(body.textContent).toMatch(/其间至少有一项在运行的时间：\S+/);
    // The fixture has overlapping executions, so the cumulative sum exceeds
    // the union and gets its own plain sentence.
    expect(body.textContent).toMatch(/各回合运行时间相加：.+（有回合同时运行，所以更长）/);
    expect(body.textContent).toContain("有 1 个回合没确认何时结束，没有计入");
    expect(body.textContent).toContain("还有 1 个回合在运行");
    expect(body.textContent).toMatch(/记录来源：项目 hey-my-buddy/);
    expect(body.textContent).toContain("来源 Host codex-desktop");
    expect(body.textContent).toContain("当前 Host codex-desktop");
    // T2: exactly one hierarchy entry — a "?" button in the vitals line; the
    // breadcrumb-like text and the duplicate legend are gone.
    expect(document.body.textContent).not.toContain("工作目标 › 委派 › 协助任务 › 回合");
    const help = within(head).getByRole("button", { name: "层级说明" });
    expect(help.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(help);
    const popover = await screen.findByRole("dialog", { name: "层级说明" });
    expect(popover.textContent).toContain("工作目标是一项议程；委派是交给 Worker、单独验收的一项工作；协助任务是委派派生的子工作；回合是一次执行。");
    fireEvent.click(help);
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "层级说明" })).toBeNull());
  });

  it("labels a standalone root honestly and keeps its note distinct", () => {
    const timeline = objectiveTimelineFixture();
    timeline.objective = { ...timeline.objective, kind: "standalone", title: "修复标题回退在 CRLF 输入下的显示" };
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(screen.getByText("历史独立委派")).toBeTruthy();
  });

  it("renders every span kind and terminal state with non-colour markers", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(item("span:s-r1-q")!.className).toContain("sp queue");
    const routing = item("span:s-r2-r")!;
    expect(routing.className).toContain("routing");
    expect(routing.getAttribute("aria-label")).toContain("路由 · 已选 Claude Opus 5.5 · high · 已完成");
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
    // 0.16 0.3: friendly names; unknown ids stay raw.
    expect(items).toEqual(["Claude Opus 5.5 · high", "gpt-5-codex · max", "glm-5 · high", "Claude Sonnet 5 · low"]);
    expect(screen.getAllByTitle("claude / anthropic / claude-opus-5-5 / high").length).toBeGreaterThan(0);
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
    expect(helper.getAttribute("aria-label")).toContain("委派，补充 schema 升级离线副本的验证测试");
    expect(helper.getAttribute("aria-label")).toContain("标题来源：Host 标题");
  });

  it("caps a task first-line title at ~40 characters, annotates it and keeps the tooltip clean", () => {
    const timeline = objectiveTimelineFixture({
      rows: [{
        ...objectiveTimelineFixture().rows[0]!, runId: "r-task", title: LONG_TASK_LINE, titleSource: "task",
      }],
      spans: [], events: [],
      totals: { rows: 1, spans: 0, events: 0, allRows: 1 },
    });
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const label = container.querySelector('[data-key="row:r-task"]') as HTMLButtonElement;
    // ~40 characters on one line with the 取自任务首行 note, never the full text.
    expect(label.querySelector(".lbl-name")!.textContent!.length).toBeLessThanOrEqual(41);
    expect(label.querySelector(".lbl-name")!.textContent).not.toBe(LONG_TASK_LINE);
    expect(label.textContent).toContain("取自任务首行");
    expect(label.getAttribute("title")).not.toBe(LONG_TASK_LINE);
    // Host-revised 0.16: the tooltip carries the COMPLETE first line plus the
    // pointer to detail, while the label stays at ~40 characters.
    expect(label.getAttribute("title")).toBe(`${LONG_TASK_LINE}（完整任务见详情）`);
    expect(label.getAttribute("title")!.length).toBeGreaterThan(50);
    // T3: the card title is the clipped line itself, with the note in the meta row.
    const single = container.querySelector(".delegation-card.single") as HTMLElement;
    expect(single.textContent).not.toContain("做什么");
    expect(single.textContent).toContain("取自任务首行");
    expect(single.textContent).not.toContain(LONG_TASK_LINE);
  });

  it("folds long idle stretches, expands one break and offers expand/collapse all", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onSetExpanded = vi.fn();
    const view = render(<Harness timeline={timeline} selectionSource={{ get: () => null }} />);
    void onSetExpanded;
    const folds = screen.getAllByRole("button", { name: /已折叠，展开/ });
    expect(folds.length).toBe(2);
    await user.click(folds[0]!);
    expect(await screen.findByRole("button", { name: /^收起空闲/ })).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /已折叠，展开/ }).length).toBe(1);
    await user.click(screen.getByRole("button", { name: "展开全部空闲" }));
    expect(screen.queryByRole("button", { name: /已折叠，展开/ })).toBeNull();
    await user.click(screen.getByRole("button", { name: "折叠空闲" }));
    expect(screen.getAllByRole("button", { name: /已折叠，展开/ }).length).toBe(2);
    view.unmount();
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
    // The overview marks the duration numbers as recorded-part only.
    const warn = first.container.querySelector(".metric-warn") as HTMLElement;
    expect(warn.textContent).toContain("不完整");
    expect(warn.getAttribute("title")).toContain("执行片段已截断");
    first.unmount();
    const filtered = objectiveTimelineFixture({ filtered: true, scopeComplete: false, totals: { rows: 2, spans: 15, events: 9, allRows: 6 } });
    const second = render(<ObjectiveTimeline {...baseProps(filtered)} />);
    expect(second.getByRole("status").textContent).toContain("已按筛选显示 6 / 6 个委派");
    expect(second.queryByRole("button", { name: /已折叠，展开/ })).toBeNull();
  });

  it("keeps the overview cards in creation order with the three single-line rows", () => {
    const timeline = objectiveTimelineFixture();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const strip = container.querySelector(".delegation-strip") as HTMLElement;
    const cards = [...strip.querySelectorAll<HTMLElement>(".delegation-card")];
    expect(cards.length).toBe(5);
    // T3: bold two-line title, top-right status, one-line result with the
    // Worker 自述 label, and no 做什么/目标摘要 labels.
    const first = cards[0]!;
    expect(first.querySelector(".delegation-card-title")!.textContent).toBe("设计工作目标时间轴视图与交互规范");
    expect(first.querySelector(".delegation-card-state")!.textContent).toContain("已验收");
    expect(first.textContent).toContain("结果：已完成时间轴视图与交互规范设计并交付审阅。");
    expect(within(first).getByText("Worker 自述")).toBeTruthy();
    expect(first.textContent).not.toContain("做什么");
    expect(first.textContent).not.toContain("目标摘要");
    const executing = cards.find(card => card.textContent!.includes("时间轴界面带截图的视觉与交互审查"))!;
    expect(executing.textContent).toContain("结果：暂无");
    expect(within(executing).queryByText("Worker 自述")).toBeNull();
    expect(cards.at(-1)!.textContent).toContain("修正折叠区间展开后的键盘焦点顺序");
  });

  it("replaces the card strip with one expanded card for a single root, helpers included", () => {
    const timeline = objectiveTimelineFixture({
      rows: [objectiveTimelineFixture().rows[0]!, objectiveTimelineFixture().rows[2]!],
      spans: objectiveTimelineFixture().spans.filter(span => ["s-r1-q", "s-r1-e"].includes(span.spanId)),
      events: [],
      objective: { ...objectiveTimelineFixture().objective, counts: { roots: 1, helpers: 1, accepted: 0, active: 0, host: 0, review: 0, ended: 2 } },
    });
    timeline.rows[1] = { ...timeline.rows[1]!, parentRunId: "r1", rootRunId: "r1", depth: 1, kind: "helper" };
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    // The single root keeps one expanded card inside the collapsible band.
    expect(container.querySelectorAll(".delegation-strip .delegation-card:not(.single)")).toHaveLength(0);
    const single = container.querySelector(".delegation-card.single") as HTMLElement;
    expect(single).toBeTruthy();
    expect(single.textContent).toContain("已验收");
    expect(within(single).getByRole("button", { name: "打开详情" })).toBeTruthy();
    expect(single.textContent).not.toContain("第 1 轮");
    expect(single.textContent).not.toContain("待决");
  });

  it("shows skeleton, error and stale states without inventing data", async () => {
    const user = userEvent.setup();
    const loading = render(<ObjectiveTimeline {...baseProps(null, { loading: true })} />);
    expect(loading.getByRole("status").textContent).toContain("正在读取时间轴…");
    expect(loading.container.querySelectorAll(".skeleton").length).toBeGreaterThanOrEqual(3);
    loading.unmount();
    const failed = render(<ObjectiveTimeline {...baseProps(null, { error: "网络中断" })} />);
    expect(failed.getByRole("alert").textContent).toContain("读取时间轴失败");
    await user.click(failed.getByRole("button", { name: "重试读取" }));
    failed.unmount();
    const retry = vi.fn();
    const stale = objectiveTimelineFixture();
    const kept = render(<ObjectiveTimeline {...baseProps(stale, { error: "网络中断", stale: true, onRetry: retry })} />);
    expect(kept.getByText(/读取失败 · 显示 \d{2}:\d{2} 的数据/)).toBeTruthy();
    expect(kept.container.querySelector(".tl-grid")).toBeTruthy();
    await user.click(kept.getAllByRole("button", { name: "重试读取" })[0]!);
    expect(retry).toHaveBeenCalled();
  });

  it("treats unknown shutdown as outranking a recorded failure, keeping the failure text", () => {
    const timeline = objectiveTimelineFixture({
      rows: [{
        runId: "r7", parentRunId: null, rootRunId: "r7", title: "失败但停止未确认的委派", titleSource: "title", taskSummary: null, summary: null,
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
        runId: "r8", parentRunId: null, rootRunId: "r8", title: "结束时间缺失的委派", titleSource: "task", taskSummary: null, summary: null,
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
      // First render is a skeleton: no canvas yet; only the root-height
      // observer (for the inspector drawer bounds) exists.
      const view = render(<ObjectiveTimeline {...baseProps(null, { loading: true })} />);
      expect(document.querySelector(".tl-grid")).toBeNull();
      const before = observers.length;
      view.rerender(<ObjectiveTimeline {...baseProps(timeline)} />);
      // The canvas mounts with the data; the viewport observer attaches then.
      const grid = document.querySelector(".tl-grid") as HTMLElement;
      expect(observers.length).toBeGreaterThan(before);
      const viewportObserver = observers[observers.length - 1]!;
      // 900px viewport minus the 240px fallback label column = 660px canvas:
      // exactly the viewport (适应窗口 never overflows), of which the time
      // track is 648px and 12px stay after the last instant.
      expect(grid.style.width).toBe("calc(var(--label-w) + 660px)");
      const band = document.querySelector(".fold-band") as HTMLElement;
      const bandPercent = Number.parseFloat(band.style.width);
      expect(bandPercent / 100 * 648).toBeCloseTo(64, 5);
      // A viewport resize remaps the same recorded facts without feedback.
      stubbedWidth = 1400;
      act(() => { viewportObserver.callback([], viewportObserver as unknown as ResizeObserver); });
      await waitFor(() => expect(grid.style.width).toBe("calc(var(--label-w) + 1160px)"));
      const remapped = Number.parseFloat((document.querySelector(".fold-band") as HTMLElement).style.width);
      expect(remapped / 100 * 1148).toBeCloseTo(64, 5);
    } finally {
      delete (HTMLElement.prototype as { clientWidth?: number }).clientWidth;
      if (widthOriginal) Object.defineProperty(widthOriginal.target, "clientWidth", widthOriginal.descriptor);
      vi.unstubAllGlobals();
    }
  });
});

describe("timeline selection, inspector and popover (C1–C3)", () => {
  it("single clicks only pin the inspector; hover, arrows and refresh never replace the card", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    const view = render(<Harness timeline={timeline} selectionSource={source} />);
    const first = item("span:s-r1-e")!;
    await user.click(first);
    const inspector = document.querySelector(".timeline-inspector") as HTMLElement;
    expect(inspector.querySelector(".inspector-card")).toBeTruthy();
    expect(inspector.textContent).toContain("执行片段");
    expect(inspector.textContent).toContain("设计工作目标时间轴视图与交互规范");
    expect(inspector.textContent).toContain("Claude Opus 5.5 · high");
    // Hover and focus elsewhere only change the preview row, never the card.
    await user.hover(item("span:s-r3-e1")!);
    act(() => { item("span:s-r3-e1")!.focus(); });
    const after = document.querySelector(".timeline-inspector") as HTMLElement;
    expect(after.textContent).toContain("设计工作目标时间轴视图与交互规范");
    expect(after.textContent).toContain("Claude Opus 5.5 · high");
    expect(after.textContent).toContain("预览：");
    // A refresh with the same facts keeps the pinned card.
    view.rerender(<Harness timeline={objectiveTimelineFixture()} selectionSource={source} />);
    expect(document.querySelector(".timeline-inspector")!.textContent).toContain("Claude Opus 5.5 · high");
    view.unmount();
  });

  it("keeps the last facts and disables every navigation action when a refresh drops the selected record", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    const view = render(<Harness timeline={timeline} selectionSource={source} />);
    // Pin the whole run so the card carries related Host events.
    await user.click(item("row:r3")!);
    const card = document.querySelector(".inspector-card") as HTMLElement;
    expect(card.textContent).toContain("整项委派");
    // A filtered refresh that no longer returns the helper's rows or spans.
    const dropped = objectiveTimelineFixture({
      rows: objectiveTimelineFixture().rows.filter(row => row.runId !== "r3"),
      spans: objectiveTimelineFixture().spans.filter(span => span.runId !== "r3"),
      events: objectiveTimelineFixture().events.filter(event => event.runId !== "r3"),
      filtered: true, scopeComplete: false,
      totals: { rows: 5, spans: 13, events: 8, allRows: 6 },
    });
    view.rerender(<Harness timeline={dropped} selectionSource={source} />);
    const kept = document.querySelector(".inspector-card") as HTMLElement;
    expect(kept.textContent).toContain("该记录不在当前读取范围内（可能已截断或被筛选）");
    const open = within(kept).getByRole("button", { name: "打开详情" });
    expect(open.getAttribute("aria-disabled")).toBe("true");
    expect(open.getAttribute("title")).toContain("不在当前读取范围内");
    // Cached related-event opens are navigation from stale data: all disabled.
    const relatedOpens = within(kept).getAllByRole("button", { name: /打开 Host 事件/ });
    expect(relatedOpens.length).toBeGreaterThan(0);
    for (const button of relatedOpens) {
      expect(button.getAttribute("aria-disabled")).toBe("true");
      fireEvent.click(button);
    }
    view.unmount();
  });

  it("shows the preview facts when nothing is pinned and a hint on empty canvases", () => {
    const timeline = objectiveTimelineFixture();
    const title = "修正时间轴上的重复预览标题";
    timeline.rows = timeline.rows.map(row => ({ ...row, title, titleSource: "title" }));
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const inspector = container.querySelector(".timeline-inspector")!;
    // Nothing is pinned yet: the row shows one preview line and the hint.
    expect(inspector.textContent).toContain("执行片段");
    const preview = inspector.querySelector(".inspector-preview")!;
    expect(preview.textContent!.split(title)).toHaveLength(2);
    expect(preview.getAttribute("title")).toBe(preview.textContent);
    expect(inspector.textContent).toContain("单击选中 · Enter 或双击打开详情");
    const empty = objectiveTimelineFixture({ rows: [], spans: [], events: [], totals: { rows: 0, spans: 0, events: 0, allRows: 0 } });
    const second = render(<ObjectiveTimeline {...baseProps(empty)} />);
    expect(second.container.querySelector(".timeline-inspector")!.textContent).toContain("单击选中 · Enter 或双击打开详情");
  });

  it("Enter and double-click open details; selection follows the opened item", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    const span = item("span:s-r4-e")!;
    act(() => { span.focus(); });
    fireEvent.keyDown(span, { key: "Enter" });
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ runId: "r4", section: "execution" }));
    await user.dblClick(item("span:s-r2-w")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r2", section: "assistance" }));
    await user.dblClick(item("settle:r1")!);
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ runId: "r1", section: "artifacts" }));
  });

  it("selecting a whole run outlines every span and flag and shows rounds, result, acceptance and pending", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    render(<Harness timeline={timeline} selectionSource={source} />);
    await user.click(item("row:r2")!);
    // Every span of the row gets the thin run outline; the settle flag of r1 does not.
    expect(item("span:s-r2-q")!.className).toContain("run-member");
    expect(item("span:s-r2-r")!.className).toContain("run-member");
    expect(item("span:s-r2-e1")!.className).toContain("run-member");
    expect(item("span:s-r2-w")!.className).toContain("run-member");
    expect(item("span:s-r2-e2")!.className).toContain("run-member");
    expect(item("span:s-r1-e")!.className).not.toContain("run-member");
    // P1.6: the fixed six-field card for a whole delegation.
    const inspector = document.querySelector(".timeline-inspector") as HTMLElement;
    expect(inspector.textContent).toContain("类型");
    expect(inspector.textContent).toContain("委派");
    expect(inspector.textContent).toContain("回合");
    expect(inspector.textContent).toContain("共 2 轮");
    expect(inspector.textContent).toContain("配置");
    expect(inspector.textContent).toContain("时间");
    expect(inspector.textContent).toContain("结果");
    expect(inspector.textContent).toContain("已验收");
  });

  it("selects the focused item with Space and moves focus with arrows without changing the pin", () => {
    const timeline = objectiveTimelineFixture();
    const onSelectItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem })} />);
    const label = item("row:r1")!;
    act(() => { label.focus(); });
    fireEvent.keyDown(document.activeElement!, { key: "ArrowRight" });
    expect(document.activeElement!.getAttribute("data-key")).toBe("span:s-r1-q");
    fireEvent.keyDown(document.activeElement!, { key: " " });
    expect(onSelectItem).toHaveBeenCalledWith(expect.objectContaining({ key: "span:s-r1-q" }));
    fireEvent.keyDown(document.activeElement!, { key: "Home" });
    expect(document.activeElement).toBe(label);
    fireEvent.keyDown(document.activeElement!, { key: "End" });
    expect(document.activeElement!.getAttribute("data-key")).toBe("settle:r1");
    fireEvent.keyDown(document.activeElement!, { key: "Escape" });
    expect(document.activeElement!.textContent).toContain("以列表查看");
  });

  it("opens merged markers as a popover only: no automatic first selection, rows select individually and stay open", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    const onSelectItem = vi.fn();
    const view = render(<Harness timeline={timeline} selectionSource={source} />);
    void onSelectItem;
    await expandHostEvents(user);
    const cluster = [...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    // P1.7: mixed kinds render ≡ with a small count.
    expect(cluster.textContent).toBe("≡2");
    expect(cluster.getAttribute("aria-label")).toBe("Host 事件 2 条：决定、续接，按 Enter 列出");
    await user.click(cluster);
    const popover = document.querySelector(".marker-popover") as HTMLElement;
    expect(popover).toBeTruthy();
    expect(cluster.getAttribute("aria-expanded")).toBe("true");
    // Nothing was selected or opened by opening the popover.
    expect(source.get()).toBeNull();
    const rows = [...popover.querySelectorAll(".marker-popover-main")] as HTMLButtonElement[];
    expect(rows.length).toBe(2);
    // P1.7: natural-language sentences name the delegation.
    expect(rows[0]!.textContent).toMatch(/Host 批准 \/ 拒绝了「实现 objectives 表、objective_list 与只读时间轴接口」的请求/);
    // A single row click pins that event and the popover stays open.
    await user.click(rows[0]!);
    expect(source.get()).toEqual({ type: "item", key: "event:5" });
    expect(document.querySelector(".marker-popover")).toBeTruthy();
    // Esc closes the popover and returns focus to the marker.
    fireEvent.keyDown(rows[0]!, { key: "Escape" });
    expect(document.querySelector(".marker-popover")).toBeNull();
    expect(document.activeElement).toBe(cluster);
    view.unmount();
  });

  it("moves keyboard focus between popover row buttons; aria-controls names the popover id", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    const view = render(<Harness timeline={timeline} selectionSource={source} />);
    await expandHostEvents(user);
    const cluster = [...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    await user.click(cluster);
    const popover = document.querySelector(".marker-popover") as HTMLElement;
    const rows = [...popover.querySelectorAll<HTMLButtonElement>("[data-row-index]")];
    expect(rows.length).toBe(2);
    // Focus enters the popover on open without selecting or opening anything.
    expect(document.activeElement).toBe(rows[0]);
    expect(source.get()).toBeNull();
    expect(cluster.getAttribute("aria-controls")).toBe(popover.getAttribute("id"));
    fireEvent.keyDown(rows[0]!, { key: "ArrowDown" });
    expect(document.activeElement).toBe(rows[1]);
    fireEvent.keyDown(rows[1]!, { key: "Home" });
    expect(document.activeElement).toBe(rows[0]);
    fireEvent.keyDown(rows[0]!, { key: "End" });
    expect(document.activeElement).toBe(rows[1]);
    fireEvent.keyDown(rows[1]!, { key: "ArrowUp" });
    expect(document.activeElement).toBe(rows[0]);
    view.unmount();
  });

  it.each([{ hidden: true }, { active: false }])("removes the portalled popover when its view is hidden or inactive: %j", async change => {
    const timeline = objectiveTimelineFixture();
    const view = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const hiddenUser = userEvent.setup();
    await expandHostEvents(hiddenUser);
    await hiddenUser.click(document.querySelector<HTMLButtonElement>(".mk.cluster")!);
    expect(document.querySelector(".marker-popover")).toBeTruthy();
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, change)} />);
    expect(document.querySelector(".marker-popover")).toBeNull();
    view.rerender(<ObjectiveTimeline {...baseProps(timeline)} />);
    expect(document.querySelector(".marker-popover")).toBeNull();
    view.unmount();
  });

  it("Enter on a row label opens the run detail with run-level selection semantics", () => {
    const timeline = objectiveTimelineFixture();
    const onSelectRun = vi.fn(), onOpenRun = vi.fn(), onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onSelectRun, onOpenRun, onOpenItem })} />);
    const label = item("row:r2")!;
    act(() => { label.focus(); });
    fireEvent.keyDown(label, { key: "Enter" });
    expect(onOpenRun).toHaveBeenCalledWith("r2");
    expect(onOpenItem).not.toHaveBeenCalled();
  });

  it("opens an event detail from the popover row's open button and by Enter", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onOpenItem })} />);
    await expandHostEvents(user);
    const cluster = [...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    await user.click(cluster);
    const rows = [...document.querySelectorAll(".marker-popover-row")] as HTMLElement[];
    await user.click(within(rows[1]!).getByRole("button", { name: /打开 Host 事件/ }));
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "event:6", section: "execution" }));
    // Reopen and press Enter on the first row to open that event directly.
    await expandHostEvents(user);
    await user.click([...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!);
    fireEvent.keyDown(document.querySelectorAll(".marker-popover-main")[0]!, { key: "Enter" });
    expect(onOpenItem).toHaveBeenLastCalledWith(expect.objectContaining({ key: "event:5", section: "assistance" }));
  });

  it("single markers select directly and open on double click", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onSelectItem = vi.fn(), onOpenItem = vi.fn();
    render(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem, onOpenItem })} />);
    await expandHostEvents(user);
    const single = item("events:4")!;
    await user.click(single);
    expect(onSelectItem).toHaveBeenCalledWith(expect.objectContaining({ key: "event:4", runId: "r1", section: "artifacts" }));
    expect(onOpenItem).not.toHaveBeenCalled();
    await user.dblClick(single);
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ key: "event:4" }));
  });

  it("closes the popover when the canvas scrolls or a refresh changes its membership", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const source = { get: () => null };
    const view = render(<Harness timeline={timeline} selectionSource={source} />);
    await expandHostEvents(user);
    const cluster = [...document.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    await user.click(cluster);
    expect(document.querySelector(".marker-popover")).toBeTruthy();
    const scroll = document.querySelector(".tl-scroll") as HTMLElement;
    fireEvent.scroll(scroll);
    expect(document.querySelector(".marker-popover")).toBeNull();
    // Reopen, then a refresh that regroups the events closes it with a notice.
    await user.click(cluster);
    const changed = objectiveTimelineFixture();
    changed.events = changed.events.filter(event => event.seq !== 6);
    changed.totals = { ...changed.totals, events: changed.events.length };
    view.rerender(<Harness timeline={changed} selectionSource={source} />);
    expect(document.querySelector(".marker-popover")).toBeNull();
    expect(document.querySelector(".trunc-chip")!.textContent).toContain("事件分组已随刷新更新");
    view.unmount();
  });

  it("offers the chronological list: click selects, Enter opens, idle separators stay honest", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onSelectItem = vi.fn(), onOpenItem = vi.fn();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem, onOpenItem })} />);
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
    expect(helperExecution.textContent).toContain("glm-5 · high");
    const failedEntry = entries.find(node => node.textContent!.includes("执行失败"))!;
    expect(failedEntry.textContent).toContain("✕");
    const uncertainEntry = entries.find(node => node.getAttribute("data-key") === "span:s-r6-e")!;
    expect(uncertainEntry.textContent).toContain("结束未确认");
    // The inspector stays visible in list mode and single clicks pin it.
    expect(container.querySelector(".timeline-inspector")).toBeTruthy();
    await user.click(entries.find(node => node.getAttribute("data-key") === "span:s-r2-r")!);
    expect(onSelectItem).toHaveBeenCalledWith(expect.objectContaining({ key: "span:s-r2-r" }));
    expect(onOpenItem).not.toHaveBeenCalled();
    fireEvent.keyDown(entries.find(node => node.getAttribute("data-key") === "span:s-r2-r")!, { key: "Enter" });
    expect(onOpenItem).toHaveBeenCalledWith(expect.objectContaining({ runId: "r2", section: "routing", decisionId: "decision-r2-first" }));
  });

  it("restores both scroll axes and refocuses the visible item, including the chronology", async () => {
    const user = userEvent.setup();
    const timeline = objectiveTimelineFixture();
    const onSelectItem = vi.fn();
    const view = render(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem })} />);
    const scroll = document.querySelector(".tl-scroll") as HTMLElement;
    scroll.scrollTop = 130;
    scroll.scrollLeft = 240;
    // List view: select from a chronology entry so its key owns the focus.
    await user.click(screen.getByRole("button", { name: "以列表查看" })!);
    const entry = document.querySelector(".tl-list [data-key='span:s-r6-e']") as HTMLButtonElement;
    await user.click(entry);
    expect(onSelectItem).toHaveBeenCalledWith(expect.objectContaining({ key: "span:s-r6-e" }));
    // Hide (detail opens) and return: the chronology entry is the visible item.
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem, hidden: true })} />);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem })} />);
    expect(document.activeElement).toBe(entry);
    expect(scroll.scrollTop).toBe(130);
    expect(scroll.scrollLeft).toBe(240);
    // Back in canvas mode, the same key refocuses its canvas span.
    await user.click(screen.getByRole("button", { name: "以列表查看" })!);
    const canvasSpan = item("span:s-r6-e")!;
    await user.click(canvasSpan);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem, hidden: true })} />);
    view.rerender(<ObjectiveTimeline {...baseProps(timeline, { onSelectItem })} />);
    expect(document.activeElement).toBe(canvasSpan);
    view.unmount();
  });

  it("restores focus to the CSS-selected narrow chronology without a list-toggle click", async () => {
    const previous = Object.getOwnPropertyDescriptor(window, "matchMedia");
    Object.defineProperty(window, "matchMedia", { configurable: true, value: () => ({ matches: true }) });
    try {
      const timeline = objectiveTimelineFixture();
      const view = render(<ObjectiveTimeline {...baseProps(timeline)} />);
      const entry = document.querySelector(".tl-list [data-key='span:s-r6-e']") as HTMLButtonElement;
      await userEvent.setup().click(entry);
      view.rerender(<ObjectiveTimeline {...baseProps(timeline, { hidden: true })} />);
      view.rerender(<ObjectiveTimeline {...baseProps(timeline)} />);
      expect(document.activeElement).toBe(entry);
      view.unmount();
    } finally {
      if (previous) Object.defineProperty(window, "matchMedia", previous);
      else Reflect.deleteProperty(window, "matchMedia");
    }
  });

  it("keeps assigned configuration colours across refreshes and stripes overflow on the bars", () => {
    const first = objectiveTimelineFixture();
    const view = render(<ObjectiveTimeline {...baseProps(first)} />);
    const legendColors = () => [...screen.getByLabelText("执行配置图例").querySelectorAll(".legend-item .swatch")]
      .map(node => (node as HTMLElement).style.getPropertyValue("--c"));
    expect(legendColors()).toEqual(["var(--cfg-1)", "var(--cfg-2)", "var(--cfg-3)", "var(--cfg-4)"]);
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
      startAt: `2026-09-26T01:${String(10 + index).padStart(2, "0")}:00Z`, endAt: `2026-09-26T01:${String(11 + index).padEnd(2, "0")}:00Z`,
    }));
    many.events = [];
    const overflow = render(<ObjectiveTimeline {...baseProps(many)} />);
    expect([...document.querySelectorAll(".sp.exec.striped")].length).toBe(1);
    expect(screen.getByLabelText("执行配置图例").querySelectorAll(".legend-item .swatch.striped").length).toBe(1);
    overflow.unmount();
  });
});
