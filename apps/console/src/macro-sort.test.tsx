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
  { language: "zh-CN", trigger: "排序", basisLabel: "排序依据", orderLabel: "顺序", basis: ["最近活动", "创建时间"], direction: ["降序", "升序"] },
  { language: "en-US", trigger: "Sort", basisLabel: "Sort by", orderLabel: "Order", basis: ["Last activity", "Created"], direction: ["Descending", "Ascending"] },
] as const;

describe("macro sort menu", () => {
  for (const copy of copies) it(`chooses basis and direction separately in ${copy.language}`, async () => {
    await i18n.changeLanguage(copy.language);
    const user = userEvent.setup(); render(<ControlledSort/>);
    const trigger = screen.getByRole("button", { name: copy.trigger });
    expect(document.querySelectorAll('[data-slot="macro-sort-button"]')).toHaveLength(1);
    expect(trigger.querySelector(".lucide-arrow-down-wide-narrow")).toBeTruthy();
    expect(trigger.getAttribute("aria-description")).toBe(copy.trigger);

    trigger.focus();
    await user.keyboard("{Enter}");
    let menu = screen.getByRole("menu", { name: copy.trigger });
    expect(within(menu).getByText(copy.basisLabel)).toBeTruthy();
    expect(within(menu).getByText(copy.orderLabel)).toBeTruthy();
    expect(within(menu).getAllByRole("menuitemradio").map(item => item.textContent?.trim())).toEqual([...copy.basis, ...copy.direction]);
    expect(within(menu).getByRole("menuitemradio", { name: copy.basis[0] }).getAttribute("aria-checked")).toBe("true");
    expect(within(menu).getByRole("menuitemradio", { name: copy.direction[0] }).getAttribute("aria-checked")).toBe("true");
    await user.click(within(menu).getByRole("menuitemradio", { name: copy.basis[1] }));
    expect(screen.queryByRole("menu")).toBeNull();

    await user.click(trigger);
    menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitemradio", { name: copy.basis[1] }).getAttribute("aria-checked")).toBe("true");
    expect(within(menu).getByRole("menuitemradio", { name: copy.direction[0] }).getAttribute("aria-checked")).toBe("true");
    await user.click(within(menu).getByRole("menuitemradio", { name: copy.direction[1] }));
    expect(screen.queryByRole("menu")).toBeNull();

    await user.click(trigger);
    menu = screen.getByRole("menu");
    expect(within(menu).getByRole("menuitemradio", { name: copy.basis[1] }).getAttribute("aria-checked")).toBe("true");
    expect(within(menu).getByRole("menuitemradio", { name: copy.direction[1] }).getAttribute("aria-checked")).toBe("true");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.click(trigger);
    await user.click(screen.getByRole("button", { name: "Outside" }));
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("supports keyboard selection and closes after choosing", async () => {
    const user = userEvent.setup(); render(<ControlledSort/>);
    const trigger = screen.getByRole("button", { name: "排序" });
    trigger.focus();
    await user.keyboard("{Enter}");
    const created = screen.getByRole("menuitemradio", { name: "创建时间" });
    created.focus();
    await user.keyboard(" ");
    expect(screen.queryByRole("menu")).toBeNull();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("menuitemradio", { name: "创建时间" }).getAttribute("aria-checked")).toBe("true");
    await user.keyboard("{Escape}");
    await user.hover(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});
