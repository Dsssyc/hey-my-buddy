import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { Legend } from "./Legend";
import { buddyColorPrefix } from "./buddyColors";

afterEach(cleanup);

describe("the timeline legend", () => {
  it("opens one dialog with the execution palette and Host inquiry explanation", async () => {
    const user = userEvent.setup();
    render(<Legend/>);
    const trigger = screen.getByRole("button", { name: "图例" });
    expect(document.querySelector('[data-slot=timeline-legend]')).toBeNull();
    await user.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "图例" });
    const samples = [...dialog.querySelectorAll<HTMLElement>('[data-slot=legend-execution-color]')];
    expect(samples).toHaveLength(3);
    expect(samples.map(sample => sample.style.backgroundColor)).toEqual(buddyColorPrefix(3).map(color => {
      const element = document.createElement("span"); element.style.backgroundColor = color; return element.style.backgroundColor;
    }));
    expect(dialog.querySelector('[data-kind=execution]')?.textContent).toContain("颜色区分 buddy");
    expect(dialog.querySelector('[data-kind=inquiry]')?.textContent).toContain("Host 向运行中的回合提出的问题。");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog", { name: "图例" })).toBeNull();
    expect(document.activeElement).toBe(trigger);
  });
  it("shows all ten marks with their distinct plain meanings", async () => {
    const user = userEvent.setup();
    render(<Legend/>);
    await user.click(screen.getByRole("button", { name: "图例" }));
    const dialog = screen.getByRole("dialog", { name: "图例" });
    const items = [...dialog.querySelectorAll<HTMLElement>('[data-slot="legend-item"]')];
    expect(items).toHaveLength(10);
    expect(items.map(item => item.getAttribute("data-kind"))).toEqual([
      "execution", "wait", "waiting", "silent", "inquiry", "event", "accepted", "failed", "cluster", "idle",
    ]);
    for (const item of items) {
      expect(item.hasAttribute("tabindex")).toBe(false);
      expect(item.textContent?.length).toBeGreaterThan(10);
    }
    expect(items.find(item => item.dataset.kind === "waiting")?.textContent).toContain("Host 或 Lead");
    expect(items.find(item => item.dataset.kind === "cluster")?.textContent).toContain("挨近的记号合成一个，数字是个数，点开看每一项。");
    expect(items.find(item => item.dataset.kind === "idle")?.textContent).toContain("折叠显示");
    expect(items.find(item => item.dataset.kind === "accepted")?.querySelector('[data-slot="conclusion-glyph"][data-kind="accepted"]')).toBeTruthy();
    expect(items.find(item => item.dataset.kind === "failed")?.querySelector('[data-slot="conclusion-glyph"][data-kind="failed"]')).toBeTruthy();
  });
  it("uses the timeline's numbered SVG mark and theme line for the folded interval", async () => {
    const user = userEvent.setup();
    render(<Legend/>);
    await user.click(screen.getByRole("button", { name: "图例" }));
    const dialog = screen.getByRole("dialog", { name: "图例" });
    const cluster = dialog.querySelector('[data-kind="cluster"] [data-slot="cluster-mark"]')!;
    expect(cluster.closest("svg")?.getAttribute("aria-hidden")).toBe("true");
    expect(cluster.querySelector("circle")?.getAttribute("r")).toBe("8");
    expect(cluster.querySelector("circle")?.getAttribute("class")).toBe("fill-card stroke-past");
    expect(cluster.querySelector("text")?.textContent).toBe("2");
    expect(dialog.querySelectorAll("i")).toHaveLength(0);
    const pattern = dialog.querySelector('[data-kind="idle"] [data-slot="idle-pattern"]')!;
    expect(pattern.getAttribute("width")).toBe("14");
    expect(pattern.querySelector("path")?.getAttribute("stroke-opacity")).toBe("0.4");
    expect(dialog.querySelector('[data-kind="waiting"] [data-slot="waiting-diamond"]')?.getAttribute("class")).toBe("fill-hand-mark");
    expect(dialog.querySelector('[data-kind="waiting"] line')?.getAttribute("stroke-dasharray")).toBe("5 4");
  });
});
