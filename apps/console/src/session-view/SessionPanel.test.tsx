import { fixtureCoverage } from "./test-fixtures";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { I18nextProvider } from "react-i18next";
import { createInstance } from "i18next";
import zh from "../i18n/zh/taskDetail";
import { TooltipProvider } from "../components/ui/tooltip";
import SessionPanel from "./SessionPanel";
import type { SessionClient } from "./use-session";
import { SESSION_PAGE_SIZE } from "./use-session";

// The panel's reads start inside the fake observer's callback, outside any
// testing-library interaction, so the React act environment is declared here.
(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

// jsdom has no IntersectionObserver. The panel needs one whose callback the test
// fires the way the browser fires it when the top sentinel comes into view; the
// sentinel is the only observed element marked aria-hidden.
type Trigger = { callback: (entries: IntersectionObserverEntry[]) => void; targets: Element[] };
const observers: Trigger[] = [];
class FakeIntersectionObserver {
  private readonly entry: Trigger;
  constructor(callback: () => void) { this.entry = { callback, targets: [] }; observers.push(this.entry); }
  observe(target: Element) { this.entry.targets.push(target); }
  unobserve() {}
  disconnect() { this.entry.targets.length = 0; }
}
const topObserver = () => observers.find(item => item.targets.some(target => target.getAttribute("aria-hidden") === "true"));
const reachTop = async () => {
  // The page text can commit before the passive effect registers its observer.
  // Wait for that actual readiness condition; no scroll event is lost because
  // IntersectionObserver reports the current intersection on observe().
  const panel = await waitFor(() => {
    const observed = topObserver();
    expect(observed, "the panel observes its top sentinel").toBeTruthy();
    return observed!;
  });
  await act(() => { panel.callback([{ isIntersecting: true } as IntersectionObserverEntry]); });
};

const event = (text: string) => ({ sessionId: "attempt", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } });
const page = (before: number, count: number, hasEarlier: boolean) => ({ state: "ended", stopped: null, coverage: fixtureCoverage, before, after: before + count, hasEarlier, events: Array.from({ length: count }, (_, index) => event(`第 ${before + index} 条记录`)) });

let i18n: ReturnType<typeof createInstance>;
beforeEach(async () => {
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  // jsdom has no layout. Publish the short initial content measurement so the
  // library can finish positioning before the panel observes earlier history.
  vi.stubGlobal("ResizeObserver", class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(target: Element) { queueMicrotask(() => this.callback([{ target, contentRect: { height: 0 } } as ResizeObserverEntry], this as unknown as ResizeObserver)); }
    unobserve() {}
    disconnect() {}
  });
  i18n = createInstance();
  await i18n.init({ lng: "zh", resources: { zh: { taskDetail: zh } } });
});
afterEach(() => { cleanup(); observers.length = 0; vi.unstubAllGlobals(); vi.restoreAllMocks(); });

function renderPanel(client: SessionClient) {
  return render(<TooltipProvider><I18nextProvider i18n={i18n}><SessionPanel client={client} taskId="task" attemptId="attempt" harness="zcode" taskText="任务" checkoutRoot="/project" active/></I18nextProvider></TooltipProvider>);
}

describe("reaching the top of the transcript", () => {
  it("reads the earlier page once, announces it quietly and stops at the beginning", async () => {
    let deliverEarlier!: (value: unknown) => void;
    const session = vi.fn().mockResolvedValueOnce(page(SESSION_PAGE_SIZE, 3, true))
      .mockImplementationOnce(() => new Promise(resolve => { deliverEarlier = resolve; }));
    renderPanel({ session });
    expect(await screen.findByText("第 258 条记录")).toBeTruthy();
    expect(screen.queryByText("正在读取更早内容…")).toBeNull();

    await reachTop();
    expect(session).toHaveBeenCalledTimes(2);
    expect(session.mock.calls[1][2]).toEqual({ before: SESSION_PAGE_SIZE, limit: SESSION_PAGE_SIZE });
    expect(screen.getByText("正在读取更早内容…").getAttribute("role")).toBe("status");
    // The read is in flight: reaching the top again must not start a second one.
    await reachTop();
    await reachTop();
    expect(session).toHaveBeenCalledTimes(2);

    await act(async () => { deliverEarlier(page(0, 3, false)); });
    expect(screen.queryByText("正在读取更早内容…")).toBeNull();
    expect(screen.getByText("第 0 条记录")).toBeTruthy();
    // The beginning has been reached: no page is earlier, so nothing more is read.
    await waitFor(() => expect(topObserver()).toBeUndefined());
    expect(session).toHaveBeenCalledTimes(2);
  });

  it("polls additions without observing history when no earlier page exists", async () => {
    const session = vi.fn().mockResolvedValueOnce({ ...page(0, 2, false), state: "running" }).mockResolvedValueOnce(page(2, 2, false));
    renderPanel({ session });
    expect(await screen.findByText("第 1 条记录")).toBeTruthy();
    // No earlier page can be read, so the top sentinel is not even attached and
    // no intersection can disturb the follow state.
    expect(observers.filter(item => item.targets.some(target => target.getAttribute("aria-hidden") === "true"))).toEqual([]);
    await screen.findByText("第 3 条记录");
    expect(session).toHaveBeenCalledTimes(2);
    expect(session.mock.calls[1][2]).toEqual({ after: 2, limit: SESSION_PAGE_SIZE });
  });
});
