import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { DomainNode } from "../types";
import { DomainTree } from "./DomainTree";

afterEach(cleanup);

const nodes: DomainNode[] = [
  { nodeId: "root", name: "工程", definition: "工程工作", parentId: null, mergedIntoId: null, directCount: 2 },
  { nodeId: "child", name: "前端", definition: "浏览器界面", parentId: "root", mergedIntoId: null, directCount: 3 },
  { nodeId: "leaf", name: "排版", definition: "页面布局", parentId: "child", mergedIntoId: null, directCount: 4 },
  { nodeId: "other", name: "研究", definition: "科学研究", parentId: null, mergedIntoId: null, directCount: 1 },
  { nodeId: "old", name: "旧界面", definition: "旧分类", parentId: null, mergedIntoId: "older", directCount: 5 },
  { nodeId: "older", name: "旧前端", definition: "旧分类", parentId: null, mergedIntoId: "child", directCount: 6 },
];

function tree() {
  render(<DomainTree nodes={nodes} locked={false} command={vi.fn(async () => true)} error="" note="" clearFeedback={vi.fn()} />);
  return screen.getByRole("tree", { name: "领域树" });
}

describe("domain branch tree", () => {
  it("adds raw classifications through consecutive merges and then through descendants", async () => {
    const view = tree();
    expect(within(view).getByRole("treeitem", { name: /全部, 21 个微任务/ })).toBeTruthy();
    expect(within(view).getByRole("treeitem", { name: /工程, 20 个微任务/ })).toBeTruthy();
    expect(within(view).getByRole("treeitem", { name: /前端, 18 个微任务/ })).toBeTruthy();
    expect(within(view).queryByText("旧前端")).toBeNull();
    await userEvent.hover(within(view).getByRole("treeitem", { name: /前端, 18 个微任务/ }));
    expect(screen.getByText(/旧界面/)).toBeTruthy();
    expect(screen.getByText(/旧前端/)).toBeTruthy();
    await userEvent.hover(within(view).getByRole("treeitem", { name: /全部, 21 个微任务/ }));
    expect(screen.queryByRole("button", { name: "改名" })).toBeNull();
    expect(screen.queryByRole("button", { name: "合并" })).toBeNull();
  });

  it("keeps one column per depth with an arbitrarily deep branch", () => {
    const deep = Array.from({ length: 35 }, (_, index): DomainNode => ({ nodeId: `n${index}`, name: `Level ${index}`,
      definition: "", parentId: index ? `n${index - 1}` : null, mergedIntoId: null, directCount: 1 }));
    render(<DomainTree nodes={deep} locked command={vi.fn(async () => false)} error="" note="" clearFeedback={vi.fn()} />);
    const items = screen.getAllByRole("treeitem");
    expect(items).toHaveLength(36);
    expect(items[35]!.getAttribute("aria-level")).toBe("36");
    const xs = items.map(item => parseFloat((item as HTMLElement).style.left));
    expect(xs.every((x, index) => !index || x > xs[index - 1]!)).toBe(true);
  });

  it("folds with a visible child count while preserving the parent total and keyboard access", async () => {
    const view = tree();
    const user = userEvent.setup();
    const root = within(view).getByRole("treeitem", { name: /工程, 20 个微任务/ });
    await user.click(root);
    expect(root.getAttribute("aria-expanded")).toBe("false");
    expect(within(view).queryByRole("treeitem", { name: /前端/ })).toBeNull();
    expect(root.textContent).toContain("＋1");
    root.focus();
    expect(screen.getByRole("button", { name: "改名" })).toBeTruthy();
    await user.keyboard("{Tab}");
    expect(document.activeElement?.textContent).toBe("改名");
  });
});
