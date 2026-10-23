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

const copies = [
  { language: "zh-CN", basis: ["最近更新", "创建时间"], direction: ["降序", "升序"], basisLabel: (basis: string) => `排序：${basis}` },
  { language: "en-US", basis: ["Last updated", "Created"], direction: ["Descending", "Ascending"], basisLabel: (basis: string) => `Sort: ${basis}` },
] as const;

describe("macro sort buttons", () => {
  for (const copy of copies) it(`keeps the basis and the direction in their own buttons (${copy.language})`, async () => {
    await i18n.changeLanguage(copy.language);
    const user = userEvent.setup(); render(<ControlledSort/>);
    const basis = screen.getByRole("button", { name: copy.basisLabel(copy.basis[0]) });
    const direction = screen.getByRole("button", { name: copy.direction[0] });
    expect(basis.getAttribute("aria-description")).toBe(copy.basisLabel(copy.basis[0]));
    expect(direction.getAttribute("aria-description")).toBe(copy.direction[0]);
    expect(basis.hasAttribute("data-emphasized")).toBe(false);
    expect(direction.hasAttribute("data-emphasized")).toBe(false);
    expect(basis.querySelector(".lucide-history")).toBeTruthy();
    expect(direction.querySelector(".lucide-arrow-down-wide-narrow")).toBeTruthy();
    await user.hover(basis);
    expect((await screen.findByRole("tooltip")).textContent).toBe(copy.basisLabel(copy.basis[0]));
    await user.unhover(basis);
    await user.hover(direction);
    expect((await screen.findByRole("tooltip")).textContent).toBe(copy.direction[0]);

    await user.click(basis);
    const popup = screen.getByRole("dialog");
    expect(within(popup).getAllByRole("radio").map(item => item.textContent)).toEqual([...copy.basis]);
    expect(within(popup).queryByRole("separator")).toBeNull();
    expect(popup.querySelector("[data-slot=segmented]")).toBeNull();
    expect(within(popup).getByRole("radio", { name: copy.basis[0] }).getAttribute("aria-checked")).toBe("true");
    expect(within(popup).getByRole("radio", { name: copy.basis[0] }).querySelector(".lucide-check")).toBeTruthy();
    await user.click(within(popup).getByRole("radio", { name: copy.basis[1] }));
    expect(screen.getByRole("dialog")).toBe(popup);
    expect(basis.getAttribute("aria-label")).toBe(copy.basisLabel(copy.basis[1]));
    expect(basis.getAttribute("data-emphasized")).toBe("true");
    expect(basis.querySelector(".lucide-calendar-plus")).toBeTruthy();
    expect(direction.hasAttribute("data-emphasized")).toBe(false);
    expect(within(popup).getByRole("radio", { name: copy.basis[1] }).querySelector(".lucide-check")).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(direction);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(direction.getAttribute("aria-label")).toBe(copy.direction[1]);
    expect(direction.getAttribute("aria-description")).toBe(copy.direction[1]);
    expect(direction.querySelector(".lucide-arrow-up-narrow-wide")).toBeTruthy();
    expect(direction.getAttribute("data-emphasized")).toBe("true");
    expect(basis.getAttribute("data-emphasized")).toBe("true");
    await user.click(direction);
    expect(direction.getAttribute("aria-label")).toBe(copy.direction[0]);
    expect(direction.hasAttribute("data-emphasized")).toBe(false);
    expect(basis.getAttribute("data-emphasized")).toBe("true");

    await user.click(basis);
    await user.click(screen.getByRole("radio", { name: copy.basis[0] }));
    expect(basis.hasAttribute("data-emphasized")).toBe(false);
    expect(direction.hasAttribute("data-emphasized")).toBe(false);
    expect(basis.className).toContain("text-muted-foreground");
    expect(basis.className).toContain("data-[emphasized=true]:text-foreground");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(basis); await user.click(basis);
    expect(screen.queryByRole("dialog")).toBeNull();
    await user.click(basis); await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("uses arrow keys and Space for the basis choices", async () => {
    const user = userEvent.setup(); render(<ControlledSort/>);
    await user.click(screen.getByRole("button", { name: "排序：最近更新" }));
    const activity = screen.getByRole("radio", { name: "最近更新" });
    activity.focus(); await user.keyboard("{ArrowDown} ");
    expect(screen.getByRole("radio", { name: "创建时间" }).getAttribute("aria-checked")).toBe("true");
    expect(screen.getByRole("button", { name: "排序：创建时间" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "降序" })).toBeTruthy();
  });
});
