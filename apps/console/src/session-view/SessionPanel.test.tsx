import { act, cleanup, render, screen } from "@testing-library/react";
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
  constructor(callback: () => void) { observers.push({ callback, targets: [] }); }
  observe(target: Element) { observers[observers.length - 1].targets.push(target); }
  unobserve() {}
  disconnect() {}
}
const reachTop = () => act(() => {
  const panel = observers.filter(item => item.targets.some(target => target.getAttribute("aria-hidden") === "true")).at(-1);
  expect(panel, "the panel observes its top sentinel").toBeTruthy();
  panel!.callback([{ isIntersecting: true } as IntersectionObserverEntry]);
});

const event = (text: string) => ({ sessionId: "attempt", update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } } });
const page = (before: number, count: number, hasEarlier: boolean) => ({ state: "ended", stopped: null, omitted: [], before, after: before + count, hasEarlier, events: Array.from({ length: count }, (_, index) => event(`第 ${before + index} 条记录`)) });

let i18n: ReturnType<typeof createInstance>;
beforeEach(async () => {
  vi.stubGlobal("IntersectionObserver", FakeIntersectionObserver);
  i18n = createInstance();
  await i18n.init({ lng: "zh", resources: { zh: { taskDetail: zh } } });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

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
    await reachTop();
    expect(session).toHaveBeenCalledTimes(2);
  });

  it("keeps the reading position when the earlier page is inserted above", async () => {
    const session = vi.fn().mockResolvedValueOnce(page(3, 2, true)).mockResolvedValueOnce(page(0, 3, false));
    renderPanel({ session });
    const blocks = await screen.findByText("第 4 条记录").then(() => document.querySelector('[data-slot="session-blocks"]')!);
    const viewport = document.querySelector('[data-slot="message-scroller-viewport"]') as HTMLElement;
    // One rendered row measures 100px of transcript: the anchor sees the transcript
    // grow from two rows to five and shifts scrollTop by exactly that difference.
    vi.spyOn(blocks, "getBoundingClientRect").mockImplementation(function (this: HTMLElement) {
      const rows = this.querySelectorAll("[data-session-block]").length;
      return { x: 0, y: 0, width: 800, height: rows * 100, top: 0, left: 0, right: 800, bottom: rows * 100 } as DOMRect;
    });
    viewport.scrollTop = 120;
    await reachTop();
    await act(async () => { await Promise.resolve(); });
    expect(viewport.scrollTop).toBe(120 + 3 * 100);
  });
});
