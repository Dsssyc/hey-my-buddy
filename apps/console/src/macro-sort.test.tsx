import { useState } from "react";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import i18n from "i18next";
import { MacroSort } from "./MacroSort";
import type { MacroTaskSort } from "./macro-task-types";
import { TooltipProvider } from "./components/ui/tooltip";

function ControlledSort() {
  const [sort, setSort] = useState<MacroTaskSort>("activity-desc");
  return <TooltipProvider delayDuration={0}><MacroSort sort={sort} onChange={setSort}/><button>Outside</button></TooltipProvider>;
}
afterEach(async () => { cleanup(); await i18n.changeLanguage("zh-CN"); });

describe("macro sort popover", () => {
  for (const [language, defaultLabel, createdLabel, oldestLabel, basis, order] of [
    ["zh-CN", "排序：最近更新 · 新的在前", "排序：创建时间 · 新的在前", "排序：创建时间 · 旧的在前", ["最近更新", "创建时间"], ["新的在前", "旧的在前"]],
    ["en-US", "Sort: Last updated · Newest first", "Sort: Created · Newest first", "Sort: Created · Oldest first", ["Last updated", "Created"], ["Newest first", "Oldest first"]],
  ] as const) it(`selects both choices immediately and keeps the popover open (${language})`, async () => {
    await i18n.changeLanguage(language);
    const user = userEvent.setup(); render(<ControlledSort/>);
    const trigger = screen.getByRole("button", { name: defaultLabel });
    expect(trigger.getAttribute("aria-description")).toBe(defaultLabel);
    expect(trigger.hasAttribute("data-emphasized")).toBe(false);
    expect(trigger.querySelector(".lucide-arrow-down-wide-narrow")).toBeTruthy();
    await user.hover(trigger);
    expect((await screen.findByRole("tooltip")).textContent).toBe(defaultLabel);
    await user.click(trigger);
    const popup = screen.getByRole("dialog");
    expect(within(popup).getAllByRole("radio").map(item => item.textContent)).toEqual([...basis, ...order]);
    expect(within(popup).getByRole("radio", { name: basis[0] }).getAttribute("aria-checked")).toBe("true");
    expect(within(popup).getByRole("radio", { name: basis[0] }).querySelector(".lucide-check")).toBeTruthy();
    await user.click(within(popup).getByRole("radio", { name: basis[1] }));
    expect(screen.getByRole("dialog")).toBe(popup);
    expect(trigger.getAttribute("aria-label")).toBe(createdLabel);
    expect(trigger.getAttribute("data-emphasized")).toBe("true");
    expect(within(popup).getByRole("radio", { name: basis[1] }).querySelector(".lucide-check")).toBeTruthy();
    await user.click(within(popup).getByRole("radio", { name: order[1] }));
    expect(trigger.getAttribute("aria-label")).toBe(oldestLabel);
    expect(trigger.getAttribute("aria-description")).toBe(oldestLabel);
    expect(trigger.querySelector(".lucide-arrow-up-narrow-wide")).toBeTruthy();
    expect(screen.getByRole("dialog")).toBe(popup);
    await user.click(within(popup).getByRole("radio", { name: basis[0] }));
    expect(trigger.getAttribute("data-emphasized")).toBe("true");
    await user.click(within(popup).getByRole("radio", { name: order[0] }));
    expect(trigger.getAttribute("aria-label")).toBe(defaultLabel);
    expect(trigger.hasAttribute("data-emphasized")).toBe(false);
    expect(trigger.className).toContain("text-muted-foreground");
    expect(trigger.className).toContain("data-[emphasized=true]:text-foreground");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(trigger); await user.click(trigger);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(trigger); await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("uses arrow keys and Space for the basis and direction radio groups", async () => {
    const user = userEvent.setup(); render(<ControlledSort/>);
    await user.click(screen.getByRole("button", { name: "排序：最近更新 · 新的在前" }));
    const activity = screen.getByRole("radio", { name: "最近更新" });
    activity.focus(); await user.keyboard("{ArrowDown} ");
    expect(screen.getByRole("radio", { name: "创建时间" }).getAttribute("aria-checked")).toBe("true");
    await user.tab(); await user.keyboard("{ArrowRight} ");
    expect(screen.getByRole("radio", { name: "旧的在前" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("button", { name: "排序：创建时间 · 旧的在前" })).toBeTruthy();
  });
});
