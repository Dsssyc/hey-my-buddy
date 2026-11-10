import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { VIEW_PREFERENCE_PREFIX } from "../view-preferences";
import type { DomainNode } from "../types";
import { DomainTree } from "./DomainTree";

afterEach(() => { cleanup(); localStorage.clear(); });

const nodes: DomainNode[] = [
  { nodeId: "root", name: "工程", definition: "工程工作", parentId: null, mergedIntoId: null, directCount: 2 },
  { nodeId: "child", name: "前端", definition: "浏览器界面", parentId: "root", mergedIntoId: null, directCount: 3 },
  { nodeId: "leaf", name: "排版", definition: "页面布局", parentId: "child", mergedIntoId: null, directCount: 4 },
  { nodeId: "other", name: "研究", definition: "科学研究", parentId: null, mergedIntoId: null, directCount: 1 },
  { nodeId: "old", name: "旧界面", definition: "旧分类", parentId: null, mergedIntoId: "older", directCount: 5 },
  { nodeId: "older", name: "旧前端", definition: "旧分类", parentId: null, mergedIntoId: "child", directCount: 6 },
];

function tree(data = nodes, locked = false) {
  render(<DomainTree nodes={data} locked={locked} command={vi.fn(async () => true)} error="" note="" clearFeedback={vi.fn()} />);
  return screen.getByRole("tree", { name: "领域树" });
}

describe("domain outline", () => {
  it("sorts roots by aggregate count and divides each bar into own and direct child counts", async () => {
    const view = tree();
    await userEvent.click(screen.getByRole("button", { name: "全部展开" }));
    expect(within(view).queryByRole("treeitem", { name: /全部/ })).toBeNull();
    const rows = within(view).getAllByRole("treeitem");
    expect(rows.map(row => row.getAttribute("data-node"))).toEqual(["root", "child", "leaf", "other"]);
    expect(rows.map(row => row.getAttribute("aria-level"))).toEqual(["1", "2", "3", "1"]);
    expect(rows[0]!.getAttribute("aria-label")).toContain("20 个微任务");
    expect(rows[1]!.getAttribute("aria-label")).toContain("18 个微任务");
    expect(screen.getByText("2 个顶层领域 · 4 个节点 · 21 个微任务")).toBeTruthy();
    const rootBar = rows[0]!.querySelector(".domain-tree-bar")!;
    expect([...rootBar.querySelectorAll(".domain-tree-segment")].map(segment => segment.getAttribute("data-segment"))).toEqual(["root", "child"]);
    expect((rootBar.querySelector('[data-segment="root"]') as HTMLElement).style.flexGrow).toBe("2");
    expect((rootBar.querySelector('[data-segment="child"]') as HTMLElement).style.flexGrow).toBe("18");
    expect((rootBar.querySelector('[data-segment="child"]') as HTMLElement).style.backgroundColor).toBe("var(--buddy-domain-4)");
    expect((rootBar.querySelector('[data-segment="root"]') as HTMLElement).style.backgroundColor).toBe("var(--buddy-domain-1)");
    const nestedLeaf = rows[1]!.querySelector('[data-segment="leaf"]') as HTMLElement;
    const leafRow = rows[2]!.querySelector('[data-segment="leaf"]') as HTMLElement;
    expect(nestedLeaf.style.backgroundColor).toBe(leafRow.style.backgroundColor);
    expect(nestedLeaf.style.backgroundColor).toBe("var(--buddy-domain-2)");
    await userEvent.hover(rows[1]!);
    expect(rootBar.querySelector('[data-segment="child"]')!.getAttribute("data-highlight")).toBe("true");
    await userEvent.click(within(rows[1]!).getByRole("button", { name: "前端 的详情" }));
    const details = document.querySelector<HTMLElement>('[data-slot="domain-details"]')!;
    expect(within(details).getByText("浏览器界面")).toBeTruthy();
    expect(within(details).getByText(/旧界面/)).toBeTruthy();
    expect(within(details).getByText(/旧前端/)).toBeTruthy();
    expect(rootBar.querySelector('[data-segment="child"]')!.getAttribute("data-highlight")).toBe("true");
  });

  it("expands and collapses all, retains the chosen default, and keeps keyboard actions", async () => {
    const view = tree(); const user = userEvent.setup();
    const root = within(view).getByRole("treeitem", { name: /工程, 20 个微任务/ });
    expect(root.getAttribute("aria-expanded")).toBe("false");
    await user.click(root);
    expect(root.getAttribute("aria-expanded")).toBe("true");
    expect(within(view).getByRole("treeitem", { name: /前端/ })).toBeTruthy();
    expect(localStorage.getItem(VIEW_PREFERENCE_PREFIX + "domain-tree-node:root")).toBe("expanded");
    cleanup(); tree();
    expect(screen.getByRole("treeitem", { name: /工程/ }).getAttribute("aria-expanded")).toBe("true");
    await user.click(screen.getByRole("button", { name: "全部展开" }));
    expect(screen.getAllByRole("treeitem")).toHaveLength(4);
    await user.click(screen.getByRole("button", { name: "全部收起" }));
    expect(screen.getAllByRole("treeitem")).toHaveLength(2);
    expect(localStorage.getItem(VIEW_PREFERENCE_PREFIX + "domain-tree-expanded")).toBe("false");
    const collapsedRoot = screen.getByRole("treeitem", { name: /工程/ });
    expect(collapsedRoot.tagName).toBe("DIV");
    expect(collapsedRoot.tabIndex).toBe(0);
    collapsedRoot.focus();
    await user.keyboard("{Enter}");
    expect(collapsedRoot.getAttribute("aria-expanded")).toBe("true");
    await user.keyboard(" ");
    expect(collapsedRoot.getAttribute("aria-expanded")).toBe("false");
    await user.tab();
    expect(document.activeElement).toBe(within(collapsedRoot).getByRole("button", { name: "工程 的详情" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("treeitem", { name: /研究/ }));
    cleanup(); tree();
    expect(screen.getByRole("tree").querySelectorAll('[role="treeitem"]')).toHaveLength(2);
  });

  it("opens details from Info without folding the row and returns focus after Escape", async () => {
    const view = tree(); const user = userEvent.setup();
    const root = within(view).getByRole("treeitem", { name: /工程/ });
    const info = within(root).getByRole("button", { name: "工程 的详情" });
    expect(root.getAttribute("aria-expanded")).toBe("false");
    await user.click(info);
    expect(root.getAttribute("aria-expanded")).toBe("false");
    expect(root.getAttribute("data-details-open")).toBe("true");
    const details = document.querySelector<HTMLElement>('[data-slot="domain-details"]')!;
    expect(details.classList.contains("domain-tree-popover")).toBe(true);
    expect(within(details).getByText("工程工作")).toBeTruthy();
    expect(within(details).getByRole("button", { name: "重命名" })).toBeTruthy();
    await user.keyboard("{Escape}");
    expect(document.querySelector('[data-slot="domain-details"]')).toBeNull();
    expect(document.activeElement).toBe(info);
    expect(root.getAttribute("data-details-open")).not.toBe("true");
    await user.click(root);
    expect(root.getAttribute("aria-expanded")).toBe("true");
  });

  it("supports arbitrarily deep data and locks edits", async () => {
    const deep = Array.from({ length: 35 }, (_, index): DomainNode => ({ nodeId: `n${index}`, name: `Level ${index}`,
      definition: "", parentId: index ? `n${index - 1}` : null, mergedIntoId: null, directCount: 1 }));
    const view = tree(deep, true);
    await userEvent.click(screen.getByRole("button", { name: "全部展开" }));
    const rows = within(view).getAllByRole("treeitem");
    expect(rows).toHaveLength(35);
    expect(rows[34]!.getAttribute("aria-level")).toBe("35");
    expect((rows[34] as HTMLElement).style.getPropertyValue("--depth")).toBe("34");
    await userEvent.click(within(rows[0]!).getByRole("button", { name: "Level 0 的详情" }));
    expect((screen.getByRole("button", { name: "重命名" }) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole("button", { name: "合并" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
