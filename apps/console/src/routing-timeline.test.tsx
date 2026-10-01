import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline, type ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveTimelineFixture, OPUS } from "./objective-fixtures";
import { buildInspectorCard } from "./inspector-card";
import { spanFacts, spanOutcome } from "./objective-display";
import type { TimelineSpan } from "./objective-types";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";

afterEach(cleanup);

function fixture(overrides: Partial<TimelineSpan> = {}) {
  const timeline = objectiveTimelineFixture();
  const span = timeline.spans.find(item => item.spanId === "s-r2-r")!;
  Object.assign(span, overrides);
  const row = timeline.rows.find(item => item.runId === span.runId)!;
  return { timeline, span, row };
}

function props(timeline: ReturnType<typeof objectiveTimelineFixture>): ObjectiveTimelineProps {
  return {
    summary: timeline.objective, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    expandedGapIds: new Set(), onToggleGap: vi.fn(), onSetExpanded: vi.fn(),
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
  };
}

function card(overrides: Partial<TimelineSpan> = {}) {
  const { timeline, span, row } = fixture(overrides);
  const item = spanFacts(span, row, Date.parse(timeline.observedAt)).item;
  return buildInspectorCard({ type: "item", key: item.key }, timeline, new Map([[item.key, item]]))!;
}

describe("recorded routing facts", () => {
  it("shows recorded fast, review fallback and historic review without current settings", () => {
    const fallback = { from: "review" as const, to: "fast" as const, code: "REVIEW_UNAVAILABLE", reason: "审阅配置不可用" };
    const fast = card({ routing: { routingMode: "fast", requestedRoutingMode: "review", fallback } });
    const fields = Object.fromEntries(fast.fields.map(field => [field.label, field.value]));
    expect(fields).toMatchObject({ 请求模式: "审阅", 实际模式: "快速", 模式降级: "已从审阅降级为快速：审阅配置不可用（REVIEW_UNAVAILABLE）" });
    const historic = Object.fromEntries(card({ routing: undefined }).fields.map(field => [field.label, field.value]));
    expect(historic).toMatchObject({ 请求模式: "审阅（历史记录）", 实际模式: "审阅（历史记录）", 模式降级: "未降级" });
  });
  it("shows the selected target, preference deviation and measured usage without the internal task id", () => {
    const result = card({ configuration: { ...OPUS, model: "router-own-model" }, routing: {
      selectedProfile: OPUS, reason: "读取接口后选择更适合的模型", policyCheck: {
        userPreference: "matched",
      }, usage: { elapsedMs: 1250, toolCalls: 0, bytesRead: 2048 },
    } });
    const fields = Object.fromEntries(result.fields.map(field => [field.label, field.value]));
    expect(fields["已选配置"]).toBe("Claude Opus 5.5 · high");
    expect(fields["理由"]).toBe("读取接口后选择更适合的模型");
    expect(fields["偏好结果"]).toBe("用户：符合偏好");
    expect(fields["用时（elapsedMs）"]).toBe("1250 ms");
    expect(fields["工具调用（toolCalls）"]).toBe("0 次");
    expect(fields["读取量（bytesRead）"]).toBe("2048 bytes");
    expect(JSON.stringify(fields)).not.toContain("run-d02b");
    expect(JSON.stringify(fields)).not.toContain("router-own-model");
  });

  it("keeps omitted historic facts unrecorded instead of borrowing the current execution configuration or timestamps", () => {
    const result = card({ routing: undefined, decisionId: undefined });
    const fields = Object.fromEntries(result.fields.map(field => [field.label, field.value]));
    for (const label of ["已选配置", "理由", "偏好结果", "用时（elapsedMs）", "工具调用（toolCalls）", "读取量（bytesRead）"]) {
      expect(fields[label]).toBe("未记录");
    }
    expect(result.openItem).toEqual(expect.objectContaining({ runId: "r2", section: "routing", decisionId: undefined }));
  });

  it("does not count abstention as failure and preserves unconfirmed-stop precedence", () => {
    const { span } = fixture({ state: "finished", disposition: "abstention", resultStatus: "ok", error: "未选择配置的原因" });
    expect(spanOutcome(span)).toBe("abstention");
    expect(card({ ...span }).fields.find(field => field.label === "结果")!.value).toBe("已放弃选择");
    expect(spanOutcome({ ...span, uncertain: true })).toBe("unknown");
  });
});

describe("routing timeline interactions", () => {
  it("keeps a one-millisecond failed fallback readable without changing its recorded duration", () => {
    const { timeline, span } = fixture({
      endAt: "2026-09-26T01:21:00.001Z", resultStatus: "failed",
      routing: { routingMode: "fast", requestedRoutingMode: "review",
        fallback: { from: "review", to: "fast", code: "NO_REVIEW", reason: "审阅配置不可用" } },
    });
    const recordedTimes = [span.startAt, span.endAt];
    const { container } = render(<ObjectiveTimeline {...props(timeline)} />);
    const bar = container.querySelector<HTMLButtonElement>('.tl-scroll [data-key="span:s-r2-r"]')!;
    const cross = bar.querySelector<HTMLElement>(".routing-cross")!;
    const mark = bar.querySelector<HTMLElement>(".routing-fallback-mark")!;
    expect(bar.classList.contains("failed")).toBe(true);
    expect(bar.classList.contains("fallback")).toBe(true);
    expect([span.startAt, span.endAt]).toEqual(recordedTimes);
    expect(bar.style.width).toBe("0.3%");
    const stylesheet = document.createElement("style");
    stylesheet.textContent = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    document.head.append(stylesheet);
    expect(getComputedStyle(bar).minWidth).toBe("24px");
    expect(getComputedStyle(cross).right).toBe("12px");
    expect(getComputedStyle(mark).zIndex).toBe("2");
    stylesheet.remove();
  });
  it("uses separate textures and marks a review-to-fast fallback accessibly", () => {
    const fallback = { from: "review" as const, to: "fast" as const, code: "NO_REVIEW", reason: "审阅配置不可用" };
    const fast = fixture({ routing: { routingMode: "fast", requestedRoutingMode: "review", fallback } });
    const fastView = render(<ObjectiveTimeline {...props(fast.timeline)} />);
    const fastBar = fastView.container.querySelector<HTMLButtonElement>('.tl-scroll [data-key="span:s-r2-r"]')!;
    expect(fastBar.classList.contains("fast")).toBe(true);
    expect(fastBar.classList.contains("fallback")).toBe(true);
    expect(fastBar.querySelector(".routing-fallback-mark")?.textContent).toBe("↘");
    expect(fastBar.getAttribute("aria-label")).toContain("从审阅降级为快速：审阅配置不可用");
    fireEvent.click(fastView.getByRole("button", { name: "列表" }));
    const listEntry = fastView.container.querySelector<HTMLButtonElement>('.tl-entry[data-key="span:s-r2-r"]')!;
    expect(listEntry.querySelector(".swatch.routing.fast")).toBeTruthy();
    expect(listEntry.textContent).toContain("已降级");
    fastView.unmount();
    const review = fixture({ routing: { routingMode: "review", requestedRoutingMode: "review", fallback: null } });
    const reviewView = render(<ObjectiveTimeline {...props(review.timeline)} />);
    const reviewBar = reviewView.container.querySelector<HTMLButtonElement>('.tl-scroll [data-key="span:s-r2-r"]')!;
    expect(reviewBar.classList.contains("review")).toBe(true);
    expect(reviewBar.querySelector(".routing-fallback-mark")).toBeNull();
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    expect(styles).toContain(".sp.routing.fast {");
    expect(styles).toContain(".sp.routing.review {");
  });
  it.each([
    { name: "completed", overrides: { state: "finished", resultStatus: "ok" }, label: "已完成", css: null },
    { name: "running", overrides: { state: "executing", endAt: null, shutdownConfirmed: false }, label: "路由中", css: "running" },
    { name: "failed", overrides: { state: "finished", resultStatus: "failed" }, label: "路由失败", css: "failed" },
    { name: "cancelled", overrides: { state: "finished", resultStatus: "cancelled" }, label: "已取消", css: "cancelled" },
    { name: "unknown", overrides: { state: "uncertain", uncertain: true }, label: "结束未确认", css: "unknown" },
    { name: "abstention", overrides: { state: "abstention", resultStatus: "ok", error: "没有合适候选" }, label: "已放弃选择", css: null },
  ])("renders a text-free $name fragment and opens the routed delegation's exact decision", ({ overrides, label, css }) => {
    const { timeline, span } = fixture(overrides as Partial<TimelineSpan>);
    const originalTimes = [span.startAt, span.endAt];
    const callbacks = props(timeline);
    const { container } = render(<ObjectiveTimeline {...callbacks} />);
    const button = container.querySelector<HTMLButtonElement>('.tl-scroll [data-key="span:s-r2-r"]')!;
    expect(button.textContent).toBe("");
    expect(button.title).toBe(`审阅（历史记录）路由 · 已选 Claude Opus 5.5 · high · ${label}`);
    expect(button.getAttribute("aria-label")).toBe(button.title);
    if (css) expect(button.classList.contains(css)).toBe(true);
    expect(!!button.querySelector(".routing-cross")).toBe(css === "failed");
    expect([span.startAt, span.endAt]).toEqual(originalTimes);
    fireEvent.click(button);
    expect(callbacks.onSelectItem).toHaveBeenCalledWith(expect.objectContaining({ runId: "r2" }));
    expect(callbacks.onOpenItem).not.toHaveBeenCalled();
    fireEvent.keyDown(button, { key: "Enter" });
    expect(callbacks.onOpenItem).toHaveBeenCalledWith(expect.objectContaining({
      runId: "r2", section: "routing", decisionId: "decision-r2-first",
    }));
    fireEvent.doubleClick(button);
    expect(callbacks.onOpenItem).toHaveBeenCalledTimes(2);
  });
});
