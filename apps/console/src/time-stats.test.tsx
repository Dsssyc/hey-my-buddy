import { useRef, useState } from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveOverview } from "./ObjectiveOverview";
import { Popover } from "./Popover";
import { objectiveTimelineFixture } from "./objective-fixtures";

const timeline = objectiveTimelineFixture();
function Page({ hidden = false, second = false }: { hidden?: boolean; second?: boolean }) {
  const next = useRef<HTMLButtonElement>(null);
  return <>
    <section className="timeline-view" hidden={hidden}>
      <ObjectiveOverview timeline={timeline} summary={timeline.objective} loading={false} stale={false} onBackToList={() => {}} />
    </section>
    <button ref={next}>另一个入口</button>
    {second && <Popover anchor={next.current} label="后打开的浮层" onClose={() => {}}>后续内容</Popover>}
  </>;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

async function openStats() {
  const user = userEvent.setup();
  const trigger = screen.getByRole("button", { name: "时间统计" });
  await user.click(trigger);
  const dialog = screen.getByRole("dialog", { name: "时间统计" });
  return { user, trigger, dialog };
}

describe("U3 time statistics portal and dismissal", () => {
  it("starts closed and opens the honest statistics once in a portal", async () => {
    const { container } = render(<Page />);
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    const { trigger, dialog } = await openStats();
    expect(trigger.getAttribute("aria-expanded")).toBe("true");
    expect(trigger.getAttribute("aria-controls")).toBe(dialog.id);
    expect(container.contains(dialog)).toBe(false);
    expect(dialog.textContent).toContain("从开始到最近一次活动：");
    expect(dialog.textContent).toContain("其间至少有一项在运行的时间：");
    expect(dialog.textContent).toContain("结束未确认 1 回合 · 未计入");
    expect(dialog.textContent).toContain("来源 Host codex-desktop");
    expect(document.querySelector("details.time-stats")).toBeNull();
  });

  it.each(["outside", "Escape", "focus"])("closes on %s and returns focus to that statistics entry", async method => {
    render(<Page />);
    const { trigger, user } = await openStats();
    if (method === "outside") await user.click(screen.getByRole("button", { name: "另一个入口" }));
    else if (method === "Escape") fireEvent.keyDown(trigger, { key: "Escape" });
    else act(() => screen.getByRole("button", { name: "另一个入口" }).focus());
    await waitFor(() => {
      expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
      expect(document.activeElement).toBe(trigger);
      expect(trigger.getAttribute("aria-expanded")).toBe("false");
    });
  });

  it("closes on an outside non-focusable pointer press without relying on a focus change", async () => {
    render(<Page />);
    const { trigger } = await openStats();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    await waitFor(() => expect(document.activeElement).toBe(trigger));
  });

  it("does not close for internal focus or presses", async () => {
    render(<Page />);
    const { dialog } = await openStats();
    act(() => dialog.focus());
    fireEvent.pointerDown(dialog);
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(screen.getByRole("dialog", { name: "时间统计" })).toBe(dialog);
  });

  it("closes when hidden without stealing focus, and stays closed on reveal", async () => {
    const view = render(<Page />);
    await openStats();
    view.rerender(<Page hidden />);
    const next = screen.getByRole("button", { name: "另一个入口" });
    act(() => next.focus());
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull());
    expect(document.activeElement).toBe(next);
    view.rerender(<Page />);
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
  });

  it("does not restore focus if its view hides before a pending dismissal frame", async () => {
    const view = render(<Page />);
    const { trigger } = await openStats();
    const focus = vi.spyOn(trigger, "focus");
    fireEvent.pointerDown(document.body);
    view.rerender(<Page hidden />);
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(focus).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
  });

  it("returns to the correct entry and leaves a subsequently clicked statistics entry open", async () => {
    render(<><Page /><Page /></>);
    const user = userEvent.setup();
    const [first, second] = screen.getAllByRole("button", { name: "时间统计" });
    await user.click(first!);
    await user.click(second!);
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    const dialog = screen.getByRole("dialog", { name: "时间统计" });
    expect(second!.getAttribute("aria-controls")).toBe(dialog.id);
    expect(first!.getAttribute("aria-expanded")).toBe("false");
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second!, { key: "Escape" });
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    expect(document.activeElement).toBe(second);
  });

  it("cancels a queued old focus return when another popover opens", async () => {
    const view = render(<Page />);
    const { trigger } = await openStats();
    const next = screen.getByRole("button", { name: "另一个入口" });
    // Old dismissal queues focus restoration; its successor claims the slot
    // before that frame, so both restoration and old close checks are inert.
    fireEvent.pointerDown(next);
    view.rerender(<Page second />);
    act(() => next.focus());
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(screen.queryByRole("dialog", { name: "时间统计" })).toBeNull();
    expect(screen.getByRole("dialog", { name: "后打开的浮层" })).toBeTruthy();
    expect(document.activeElement).toBe(next);
    expect(document.activeElement).not.toBe(trigger);
  });

  it("replacement alone never returns focus to the old statistics entry", async () => {
    const view = render(<Page />);
    const { trigger } = await openStats();
    const focus = vi.spyOn(trigger, "focus");
    const next = screen.getByRole("button", { name: "另一个入口" });
    act(() => next.focus());
    view.rerender(<Page second />);
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(focus).not.toHaveBeenCalled();
    expect(document.activeElement).toBe(next);
    expect(screen.getByRole("dialog", { name: "后打开的浮层" })).toBeTruthy();
  });

  it("does not restore a removed trigger after its pending dismissal", async () => {
    const view = render(<Page />);
    const { trigger } = await openStats();
    const focus = vi.spyOn(trigger, "focus");
    fireEvent.pointerDown(document.body);
    view.unmount();
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(focus).not.toHaveBeenCalled();
  });

  it("does not return an old pending dismissal to the statistics trigger of a changed objective", async () => {
    const overview = (objectiveId: string) => <section className="timeline-view">
      <ObjectiveOverview timeline={{ ...timeline, objective: { ...timeline.objective, objectiveId } }}
        summary={null} loading={false} stale={false} onBackToList={() => {}} />
    </section>;
    const view = render(overview("first"));
    const { trigger } = await openStats();
    const focus = vi.spyOn(trigger, "focus");
    fireEvent.pointerDown(document.body);
    view.rerender(overview("second"));
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(focus).not.toHaveBeenCalled();
    expect(trigger.isConnected).toBe(false);
    expect(screen.getByRole("button", { name: "时间统计" })).not.toBe(trigger);
  });

  it("preserves default shared popover outside/focus semantics", async () => {
    function DefaultPage() {
      const trigger = useRef<HTMLButtonElement>(null);
      const [open, setOpen] = useState(false);
      return <><button ref={trigger} onClick={() => setOpen(true)}>默认入口</button><button>外部</button>
        {open && <Popover anchor={trigger.current} label="默认浮层" onClose={() => setOpen(false)}>内容</Popover>}</>;
    }
    render(<DefaultPage />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("button", { name: "默认入口" }));
    await user.click(screen.getByRole("button", { name: "外部" }));
    await act(async () => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "外部" }));
    expect(screen.queryByRole("dialog", { name: "默认浮层" })).toBeNull();
  });
});

function rect(left: number, top: number, width: number, height: number): DOMRect {
  return { left, top, right: left + width, bottom: top + height, width, height, x: left, y: top, toJSON() {} };
}

describe("U3 panel-bound placement (structural, not browser clipping evidence)", () => {
  it.each([
    { name: "wide", width: 1200, height: 900, panel: rect(100, 60, 700, 650), anchor: rect(160, 120, 60, 25) },
    { name: "narrow", width: 480, height: 750, panel: rect(180, 60, 280, 650), anchor: rect(200, 120, 60, 25) },
    { name: "wrapped", width: 600, height: 700, panel: rect(140, 60, 440, 580), anchor: rect(200, 210, 60, 25) },
    { name: "short above", width: 900, height: 400, panel: rect(100, 20, 700, 350), anchor: rect(180, 310, 60, 25) },
  ])("opens rightward and fits the panel/viewport intersection: $name", async fixture => {
    vi.stubGlobal("innerWidth", fixture.width);
    vi.stubGlobal("innerHeight", fixture.height);
    let panelRect = fixture.panel;
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      if (this.classList.contains("timeline-view")) return panelRect;
      if (this.classList.contains("time-stats")) return fixture.anchor;
      if (this.classList.contains("shared-popover")) return rect(0, 0, 500, 220);
      return rect(0, 0, 0, 0);
    });
    render(<Page />);
    const { dialog } = await openStats();
    const left = Number.parseFloat(dialog.style.left);
    const top = Number.parseFloat(dialog.style.top);
    const width = Number.parseFloat(dialog.style.maxWidth);
    const height = Number.parseFloat(dialog.style.maxHeight);
    expect(left).toBe(fixture.anchor.left);
    expect(left + width).toBeLessThanOrEqual(Math.min(fixture.panel.right, fixture.width) - 8);
    expect(top).toBeGreaterThanOrEqual(Math.max(fixture.panel.top, 0) + 8);
    expect(top + Math.min(220, height)).toBeLessThanOrEqual(Math.min(fixture.panel.bottom, fixture.height) - 8);
    // A panel resize replays the same bounded placement without a remount.
    panelRect = rect(panelRect.left, panelRect.top, panelRect.width - 20, panelRect.height);
    act(() => window.dispatchEvent(new Event("resize")));
    expect(Number.parseFloat(dialog.style.maxWidth)).toBe(width - 20);
  });
});
