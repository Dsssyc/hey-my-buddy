import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveTimeline } from "./ObjectiveTimeline";
import type { ObjectiveTimelineProps } from "./ObjectiveTimeline";
import { objectiveTimelineFixture } from "./objective-fixtures";
import type { ObjectiveTimeline as ObjectiveTimelineData, TimelineRow, TimelineSpan } from "./objective-types";

afterEach(() => cleanup());

const styles = () => readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
/**
 * Locate one exact rule (a selector that starts its own rule, never the tail of
 * a longer descendant selector) so a style contract can be asserted here.
 */
function cssRuleAt(source: string, selector: string): { start: number; body: string } {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = new RegExp(`(?:^|[\\n}])\\s*(${escaped})\\s*\\{`).exec(source);
  expect(match, `rule ${selector} exists`).not.toBeNull();
  const start = match!.index + match![0].indexOf(match![1]!);
  return { start, body: source.slice(start, source.indexOf("}", start)) };
}
const cssRule = (source: string, selector: string) => cssRuleAt(source, selector).body;

function baseProps(timeline: ObjectiveTimelineData | null, overrides: Record<string, unknown> = {}): ObjectiveTimelineProps {
  const props: ObjectiveTimelineProps = {
    summary: timeline?.objective ?? null, timeline, loading: false, error: "", stale: false,
    newRunIds: new Set<string>(), hidden: false, openedKey: null, openedRunId: null, selection: null,
    onSelectItem: vi.fn(), onOpenItem: vi.fn(), onSelectRun: vi.fn(), onOpenRun: vi.fn(),
    onClearSelection: vi.fn(), onRetry: vi.fn(), onBackToList: vi.fn(),
  };
  return { ...props, ...overrides } as ObjectiveTimelineProps;
}

describe("0.16 P1.7: the Host event row", () => {
  it("is collapsed by default with a 24px row, and expanding shows icon chips with counts", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const toggle = screen.getByRole("button", { name: /Host 事件（9）/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelector(".tl-row.markers.collapsed")).toBeTruthy();
    // No marker chips render while collapsed; other rows are unaffected.
    expect(container.querySelectorAll(".tl-track .mk").length).toBe(0);
    expect(container.querySelector('[data-key="span:s-r1-e"]')).toBeTruthy();
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelector(".tl-row.markers.collapsed")).toBeNull();
    // Single events show their kind glyph; the mixed cluster shows ≡ with a count.
    const single = container.querySelector<HTMLButtonElement>('[data-key="events:4"]');
    expect(single!.textContent).toBe("✓");
    const cluster = [...container.querySelectorAll<HTMLButtonElement>(".mk.cluster")][0]!;
    expect(cluster.textContent).toBe("≡2");
    expect(cluster.querySelector(".mk-count")!.textContent).toBe("2");
  });
});

describe("0.16 P1.8: the acceptance-wait dashed line", () => {
  /** r1: one confirmed execution ending 01:58, accepted 02:20 — a 22-minute wait. */
  function acceptedFixture(): ObjectiveTimelineData {
    return objectiveTimelineFixture();
  }

  it("draws an aria-hidden dashed line from the confirmed end to the flag with a wait tooltip", () => {
    const timeline = acceptedFixture();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const line = container.querySelector(".accept-wait-line") as HTMLElement;
    expect(line).toBeTruthy();
    expect(line.getAttribute("aria-hidden")).toBe("true");
    expect(line.getAttribute("title")).toMatch(/^等待验收 (22 分钟|不到 1 分钟)$/);
    // r6's unconfirmed end never draws a line.
    const r6Track = container.querySelector('[data-key="span:s-r6-e"]')!.parentElement!;
    expect(r6Track.querySelector(".accept-wait-line")).toBeNull();
  });

  it("never draws when the stop is unconfirmed, the end is missing or after the acceptance", () => {
    const uncertain = objectiveTimelineFixture();
    const r1 = uncertain.rows.find(row => row.runId === "r1")!;
    const exec = uncertain.spans.find(span => span.spanId === "s-r1-e")!;
    exec.uncertain = true;
    exec.state = "uncertain";
    void r1;
    const first = render(<ObjectiveTimeline {...baseProps(uncertain)} />);
    const r1Track = first.container.querySelector('[data-key="span:s-r1-e"]')!.parentElement!;
    expect(r1Track.querySelector(".accept-wait-line")).toBeNull();
    first.unmount();

    const noEnd = objectiveTimelineFixture();
    noEnd.spans.find(span => span.spanId === "s-r1-e")!.endAt = null;
    const second = render(<ObjectiveTimeline {...baseProps(noEnd)} />);
    const secondTrack = second.container.querySelector('[data-key="span:s-r1-e"]')!.parentElement!;
    expect(secondTrack.querySelector(".accept-wait-line")).toBeNull();
    second.unmount();

    const reversed = objectiveTimelineFixture();
    reversed.rows.find(row => row.runId === "r1")!.acceptedAt = "2026-09-26T01:30:00Z";
    const third = render(<ObjectiveTimeline {...baseProps(reversed)} />);
    const thirdTrack = third.container.querySelector('[data-key="span:s-r1-e"]')!.parentElement!;
    expect(thirdTrack.querySelector(".accept-wait-line")).toBeNull();
  });
});

describe("0.16 P2.3: timeline zoom controls", () => {
  it("offers −/+/适应窗口 with the fit level pressed and − disabled there", () => {
    const timeline = objectiveTimelineFixture();
    render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const group = screen.getByRole("group", { name: "时间轴缩放" });
    const out = within(group).getByRole("button", { name: "缩小" }) as HTMLButtonElement;
    const inButton = within(group).getByRole("button", { name: "放大" }) as HTMLButtonElement;
    const fit = within(group).getByRole("button", { name: "适应窗口" }) as HTMLButtonElement;
    expect(out.disabled).toBe(true);
    expect(fit.getAttribute("aria-pressed")).toBe("true");
    expect(inButton.disabled).toBe(false);
  });

  it("grows the canvas on +, keeps fit pressed off, and returns to fit with 0 or the button", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const grid = container.querySelector(".tl-grid") as HTMLElement;
    const widthBefore = Number.parseFloat(grid.style.width.replace(/[^\d.]/g, "")) || 0;
    await user.click(screen.getByRole("button", { name: "放大" }));
    await waitFor(() => {
      const widthAfter = Number.parseFloat(grid.style.width.replace(/[^\d.]/g, "")) || 0;
      expect(widthAfter).toBeGreaterThan(widthBefore);
    });
    expect(screen.getByRole("button", { name: "适应窗口" }).getAttribute("aria-pressed")).toBe("false");
    // The − control becomes available once zoomed in.
    expect((screen.getByRole("button", { name: "缩小" }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.keyDown(grid.querySelector('[data-key="span:s-r1-e"]')!, { key: "0" });
    expect(screen.getByRole("button", { name: "适应窗口" }).getAttribute("aria-pressed")).toBe("true");
  });
});

describe("0.16 P2.1: the inspector dock", () => {
  it("keeps the inspector visible as a bottom drawer with a keyboard separator", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline)} />);
    const dock = container.querySelector(".inspector-dock") as HTMLElement;
    expect(dock).toBeTruthy();
    expect(dock.className).toContain("collapsed");
    await user.click(within(dock).getByRole("button", { name: "展开检查器" }));
    expect(container.querySelector(".timeline-inspector")).toBeTruthy();
    const separator = container.querySelector(".inspector-separator") as HTMLElement;
    expect(separator.getAttribute("role")).toBe("separator");
    expect(separator.getAttribute("aria-orientation")).toBe("horizontal");
    const valueNow = () => Number.parseInt(separator.getAttribute("aria-valuenow")!, 10);
    const before = valueNow();
    fireEvent.keyDown(separator, { key: "ArrowUp" });
    await waitFor(() => expect(valueNow()).toBeGreaterThan(before));
    fireEvent.keyDown(separator, { key: "Home" });
    await waitFor(() => expect(valueNow()).toBe(44));
    fireEvent.keyDown(separator, { key: "End" });
    await waitFor(() => expect(valueNow()).toBeGreaterThan(100));
    fireEvent.keyDown(separator, { key: "Enter" });
    await waitFor(() => expect(valueNow()).toBe(before));
    // The collapse control shrinks the dock to its title row and back.
    await user.click(within(dock).getByRole("button", { name: "收起检查器" }));
    expect(dock.className).toContain("collapsed");
    expect(container.querySelector(".timeline-inspector")).toBeNull();
    await user.click(within(dock).getByRole("button", { name: "展开检查器" }));
    expect(container.querySelector(".timeline-inspector")).toBeTruthy();
  });

  it("lets the wheel scroll the drawer: the card is not a nested scroll boundary", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { selection: { type: "run", runId: "r1" } })} />);
    await user.click(screen.getByRole("button", { name: "展开检查器" }));
    const body = container.querySelector(".inspector-dock-body") as HTMLElement;
    const card = container.querySelector(".inspector-dock-body .timeline-inspector") as HTMLElement;
    expect(body).toBeTruthy();
    expect(card).toBeTruthy();
    // A wheel over the card's content keeps travelling to the drawer body, the
    // element that scrolls; nothing cancels it on the way.
    const seen: WheelEvent[] = [];
    body.addEventListener("wheel", event => seen.push(event as WheelEvent));
    fireEvent.wheel(card.querySelector(".inspector-card")!, { deltaY: 120 });
    expect(seen).toHaveLength(1);
    expect(seen[0]!.defaultPrevented).toBe(false);
    const source = styles();
    const bodyRule = cssRule(source, ".inspector-dock-body");
    expect(bodyRule).toMatch(/overflow-y:\s*auto/);
    // Reaching the drawer's end never chains the wheel into the timeline.
    expect(bodyRule).toMatch(/overscroll-behavior:\s*contain/);
    // The card inside must not be an overflow/overscroll boundary of its own:
    // with `contain` there the wheel dies inside the drawer (the reported bug).
    const cardRule = cssRule(source, ".timeline-inspector");
    expect(cardRule).toMatch(/overflow:\s*visible/);
    expect(cardRule).toMatch(/overscroll-behavior:\s*auto/);
    // The override sits after the shared `.inspector` rule, so it wins the tie.
    expect(cssRuleAt(source, ".timeline-inspector").start).toBeGreaterThan(cssRuleAt(source, ".inspector").start);
  });

  it("gives 打开详情 and the × close control one identical size spec", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { selection: { type: "run", runId: "r1" } })} />);
    await user.click(screen.getByRole("button", { name: "展开检查器" }));
    const head = container.querySelector(".inspector-card-head") as HTMLElement;
    const open = within(head).getByRole("button", { name: "打开详情" });
    const close = within(head).getByRole("button", { name: "取消固定" });
    expect(open.classList.contains("inspector-action")).toBe(true);
    expect(close.classList.contains("inspector-action")).toBe(true);
    const source = styles();
    const action = cssRule(source, ".inspector-action");
    expect(action).toMatch(/height:\s*28px/);
    expect(action).toMatch(/min-height:\s*28px/);
    expect(action).toMatch(/align-items:\s*center/);
    // One size for both: the × is square at the same 28px and carries no extra
    // horizontal padding that would shift its glyph off centre.
    const unpin = cssRule(source, ".inspector-unpin");
    expect(unpin).toMatch(/width:\s*28px/);
    expect(unpin).toMatch(/padding:\s*0/);
    expect(cssRule(source, ".inspector-actions")).toMatch(/align-items:\s*center/);
  });
});

describe("0.16 P2.1: the delegation band", () => {
  it("collapses to one toggle row and back, keeping selection working", async () => {
    const timeline = objectiveTimelineFixture();
    const user = userEvent.setup();
    const onSelectRun = vi.fn();
    const { container } = render(<ObjectiveTimeline {...baseProps(timeline, { onSelectRun })} />);
    const toggle = screen.getByRole("button", { name: /委派（5）/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    expect(container.querySelectorAll(".delegation-card").length).toBe(4);
    await user.click(toggle);
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    expect(container.querySelectorAll(".delegation-card").length).toBe(0);
    await user.click(toggle);
    const firstCard = container.querySelectorAll<HTMLButtonElement>(".delegation-card")[0]!;
    await user.click(firstCard);
    expect(onSelectRun).toHaveBeenCalledWith(expect.any(String));
  });
});
