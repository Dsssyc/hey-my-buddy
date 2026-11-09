import { cleanup, render as rawRender, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { TooltipProvider } from "@/components/ui/tooltip";
import { RoutingRecords } from "./RoutingRecords";
import { health, MEDIUM_ID } from "./roles.testing";
import { routingCodeText } from "./routing-code";

const render = (node: ReactNode) => rawRender(<TooltipProvider>{node}</TooltipProvider>);

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); });

describe("routing records", () => {
  it("keeps unavailable and absent records explicit and shows no invented table", () => {
    render(<RoutingRecords />); expect(screen.getByText("未记录")).toBeTruthy(); expect(screen.queryByRole("table")).toBeNull();
    cleanup(); render(<RoutingRecords health={health({ available: false, reason: { code: "router-not-configured", params: {} }, sampleCount: 0 })} />);
    expect(screen.getByText(/尚未设置 Router/)).toBeTruthy(); expect(screen.getByText("暂无记录")).toBeTruthy();
  });
  it("uses service counts and a shared segmented scale; each bar reveals counts on click", async () => {
    const user = userEvent.setup();
    render(<RoutingRecords health={health({ failureCount: 2, sampleCount: 8, routers: [{ buddyId: MEDIUM_ID, index: 0, eligible: true, identity: null, answeredCount: 6, noAnswerCount: 2, consecutiveNoAnswers: 1,
      lastAnsweredAt: "2026-11-09T01:01:00Z", lastError: { code: "router-not-responding", at: "2026-11-09T01:00:00Z" } }, { buddyId: "second", index: 1, eligible: true, answeredCount: 2, noAnswerCount: 0 }] })} />);
    const table = screen.getByRole("table", { name: "各 Router 的记录" });
    expect(within(table).getAllByRole("row")).toHaveLength(3);
    expect(within(table).getByText("模型未开始作答")).toBeTruthy();
    expect(table.textContent).not.toContain("router-not-responding");
    const plots = table.querySelectorAll('[data-slot="quant-bar"]');
    expect(plots[0].querySelector('[data-tone="pink"]')?.getAttribute("data-value")).toBe("2");
    expect(plots[1].querySelector('[data-tone="pink"]')).toBeNull();
    expect(Number(plots[1].querySelector('[data-tone="green"]')?.getAttribute("width"))).toBeLessThan(Number(plots[0].querySelector('[data-tone="green"]')?.getAttribute("width")));
    const first = table.querySelectorAll<HTMLButtonElement>('[data-slot="quant-bar-trigger"]')[0];
    expect(first.getAttribute("aria-expanded")).toBe("false");
    expect(table.textContent).not.toContain("应答 6 次");
    expect(first.querySelector('[data-kind="fail"]')).toBeTruthy();
    await user.click(first);
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(table.textContent).toContain("应答 6 次");
    first.focus();
    await user.keyboard("{Enter}");
    expect(first.getAttribute("aria-expanded")).toBe("false");
    await user.keyboard(" ");
    expect(first.getAttribute("aria-expanded")).toBe("true");
    expect(localStorage.getItem(`hey-my-buddy.console.view.quant-bar.routing.${MEDIUM_ID}`)).toBe("true");
  });
  it("links the recorded title through its exact macro group and copies the whole ID without commands", async () => {
    const user = userEvent.setup(); const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<RoutingRecords health={health({ recentFailures: [{ decisionId: "d", runId: "child/id", macroTaskId: "macro-parent", title: "Review parser", at: "2026-11-09T01:00:00Z", code: "router-out-of-bounds" }] })} />);
    expect(screen.getByRole("link", { name: "Review parser" }).getAttribute("href")).toBe("#tasks/macro-parent/child%2Fid");
    expect(screen.getByText("选择超出路由边界")).toBeTruthy();
    const button = screen.getByRole("button", { name: "复制编号" }); expect(button.textContent).toBe("");
    await user.click(button); expect(write).toHaveBeenCalledWith("child/id"); expect(screen.getByRole("status").textContent).toBe("已复制编号");
  });
  it("reports clipboard rejection visibly to assistive technology", async () => {
    const user = userEvent.setup(); vi.spyOn(navigator.clipboard, "writeText").mockRejectedValue(new Error("denied"));
    render(<RoutingRecords health={health({ recentFailures: [{ decisionId: "d", runId: "child", macroTaskId: "run:root", title: "Independent child", at: "2026-11-09T01:00:00Z", code: "router-no-answer" }] })} />);
    expect(screen.getByRole("link").getAttribute("href")).toBe("#tasks/run%3Aroot/child");
    await user.click(screen.getByRole("button", { name: "复制编号" })); expect(screen.getByRole("status").textContent).toBe("复制失败");
  });
  it("keeps every recent failure in a focusable newest-first scroll region", () => {
    const recentFailures = Array.from({ length: 9 }, (_, index) => ({ decisionId: `d${index}`, runId: `run-${index}`, macroTaskId: "macro", title: `Failure ${index}`,
      at: `2026-11-09T${String(index).padStart(2, "0")}:00:00Z`, code: "router-no-answer" }));
    render(<RoutingRecords health={health({ recentFailures })} />);
    const region = document.querySelector<HTMLElement>('[data-slot="routing-failures-region"]')!;
    expect(region.tabIndex).toBe(0);
    expect(region.classList.contains("record-region--routing")).toBe(true);
    expect(within(region).getAllByRole("link").map(link => link.textContent)).toEqual(recentFailures.map(row => row.title).reverse());
  });
  it("keeps unknown historical codes readable without guessing their meaning", () => {
    expect(routingCodeText("unknown-native-code")).toBe("本次执行失败，原因暂无法说明");
  });
});
