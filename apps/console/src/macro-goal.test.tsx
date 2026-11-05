import { cleanup, render, screen, within, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { MacroTaskOverview } from "./MacroTaskOverview";
import { macroTaskTimelineFixture } from "./macro-task-fixtures";
import { TooltipProvider } from "./components/ui/tooltip";

const goal = { body: "当前目标\n\n## 范围\n\n**完成**这一批。", revision: 2, createdAt: "2026-11-01T04:00:00Z" };
const old = { body: "旧版目标\n\n保留最初的全文。", revision: 1, createdAt: "2026-10-31T03:00:00Z" };
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function harness(body = goal.body) {
  const current = { ...goal, body };
  const timeline = macroTaskTimelineFixture(); timeline.macroTask.goal = current;
  const readGoals = vi.fn(async () => ({ macroTaskId: timeline.macroTask.macroTaskId, goals: [current, old] }));
  const ui = (active = true) => <TooltipProvider><MacroTaskOverview timeline={timeline} summary={null} loading={false} stale={false} readGoals={readGoals} active={active} onBackToList={() => {}}/><button>外部按钮</button></TooltipProvider>;
  const view = render(ui());
  return { view, ui, timeline, readGoals, user: userEvent.setup() };
}
it("reads history only on click, renders Markdown, switches full revisions in the same card and restores focus on Escape", async () => {
  const { readGoals, timeline, user } = harness();
  const trigger = screen.getByRole("button", { name: "查看目标说明" });
  expect(readGoals).not.toHaveBeenCalled();
  await user.hover(trigger);
  expect(screen.queryByRole("dialog", { name: "目标说明" })).toBeNull();
  await user.click(trigger);
  const card = await screen.findByRole("dialog", { name: "目标说明" });
  expect(readGoals).toHaveBeenCalledWith(timeline.macroTask.macroTaskId, expect.any(AbortSignal));
  expect(document.activeElement).toBe(card);
  await waitFor(() => expect(card.querySelector('[data-streamdown="strong"]')?.textContent).toBe("完成"));
  expect(within(card).getByRole("heading", { name: "范围" })).toBeTruthy();
  await user.click(await within(card).findByRole("button", { name: /第 1 版/ }));
  await waitFor(() => expect(card.querySelector('[data-slot="macro-goal-body"]')?.textContent).toContain("保留最初的全文。"));
  expect(screen.getByRole("dialog", { name: "目标说明" })).toBe(card);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog", { name: "目标说明" })).toBeNull());
  expect(document.activeElement).toBe(trigger);
});
it("copies the identifier in place, closes on outside click, rereads on reopen and closes when inactive", async () => {
  const { user, readGoals, view, ui } = harness();
  const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
  const copy = screen.getByRole("button", { name: "复制标识 macro-1" });
  expect(copy.closest("dl")!.classList.contains("items-baseline")).toBe(true);
  expect(copy.closest("dd")!.parentElement!.classList.contains("items-baseline")).toBe(true);
  await user.click(copy);
  expect(write).toHaveBeenCalledWith("macro-1");
  expect(copy.querySelector(".lucide-check")).toBeTruthy();
  const trigger = screen.getByRole("button", { name: "查看目标说明" });
  await user.click(trigger);
  await screen.findByRole("dialog", { name: "目标说明" });
  await user.click(screen.getByRole("button", { name: "外部按钮" }));
  expect(screen.queryByRole("dialog", { name: "目标说明" })).toBeNull();
  await user.click(trigger);
  expect(readGoals).toHaveBeenCalledTimes(2);
  view.rerender(ui(false));
  expect(screen.queryByRole("dialog", { name: "目标说明" })).toBeNull();
});

it("uses the card's own width for the goal line origin and keeps a right-hand trigger over the card", async () => {
  vi.spyOn(Element.prototype, "getBoundingClientRect").mockImplementation(function (this: Element) {
    const rect = this.matches("header") ? { x: 100, y: 40, width: 1200, height: 90 }
      : this.matches('[data-slot="macro-goal-trigger"]') ? { x: 1100, y: 50, width: 28, height: 28 }
      : { x: 124, y: 82, width: 601, height: 300 };
    return DOMRect.fromRect(rect);
  });
  const { user, readGoals } = harness();
  readGoals.mockResolvedValueOnce({ macroTaskId: "macro-1", goals: [goal] });
  await user.click(screen.getByRole("button", { name: "查看目标说明" }));
  const card = await screen.findByRole("dialog", { name: "目标说明" });
  await waitFor(() => expect(card.querySelector('[data-slot="macro-goal-scroll"]')?.getAttribute("data-history")).toBe("false"));
  expect(card.querySelector(".goal-history")).toBeNull();
  expect(card.style.left).toBe("427px");
  expect(card.style.top).toBe("42px");
  expect(card.style.getPropertyValue("--goal-left")).toBe("573px");
  expect(card.style.getPropertyValue("--goal-right")).toBe("0px");
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "查看目标说明" }));
  await waitFor(() => expect(screen.getByRole("dialog").querySelector('[data-slot="macro-goal-scroll"]')?.getAttribute("data-history")).toBe("true"));
  expect(screen.getByRole("navigation", { name: "历史" })).toBeTruthy();
});
it("omits the goal button when the record has none", () => {
  const timeline = macroTaskTimelineFixture(); timeline.macroTask.goal = null;
  const { container } = render(<MacroTaskOverview readGoals={vi.fn()} timeline={timeline} summary={null} loading={false} stale={false} onBackToList={() => {}}/>, { wrapper: TooltipProvider });
  expect(container.querySelector('[data-slot="macro-goal-trigger"]')).toBeNull();
});

it("keeps single goal line breaks while rendering Markdown and the existing GFM extensions", async () => {
  const { user } = harness("第一行\n第二行\n\n**Markdown** 与 ~~旧内容~~\n\n| 名称 | 值 |\n| --- | --- |\n| 项目 | 目标 |");
  await user.click(screen.getByRole("button", { name: "查看目标说明" }));
  const card = await screen.findByRole("dialog", { name: "目标说明" });
  const body = card.querySelector('[data-slot="macro-goal-body"]')!;
  await waitFor(() => expect(body.querySelector("br")).not.toBeNull());
  expect(body.querySelector('[data-streamdown="strong"]')?.textContent).toBe("Markdown");
  expect(body.querySelector("del")?.textContent).toBe("旧内容");
  expect(body.querySelector("table")?.textContent).toContain("名称");
});
