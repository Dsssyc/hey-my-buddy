import { TooltipProvider } from "./components/ui/tooltip";
import type { ReactElement } from "react";
import { cleanup, fireEvent, render as baseRender, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskList } from "./MacroTaskList";
import { listFixture, macroTaskGroups } from "./macro-task-fixtures";
import type { MacroTaskListProps } from "./MacroTaskList";
import type { MacroTaskSummary } from "./macro-task-types";
import i18n from "i18next";
import * as formatting from "./i18n/format";

const props = (): MacroTaskListProps => ({
  sort: "activity-desc", onSortChange: vi.fn(),
  groups: macroTaskGroups(listFixture()), totals: { all: 12, active: 2, host: 1, review: 3 }, loading: false, error: "", reorder: null,
  filter: "all", query: "", selected: null,
  onCollapse: vi.fn(), onFilterChange: vi.fn(), onQueryChange: vi.fn(), onSelect: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });
const render = (ui: ReactElement) => baseRender(ui, { wrapper: TooltipProvider });
const row = (p: MacroTaskListProps, id = "macro-1") => p.groups.flatMap(group => group.macroTasks).find(item => item.macroTaskId === id)!;

describe("macro task list", () => {
  it("retains entry rendering across loading changes and uses the latest selection action", () => {
    const date = vi.spyOn(formatting, "formatDateTime");
    const p = props();
    const view = render(<MacroTaskList {...p}/>);
    const calls = date.mock.calls.length;
    expect(calls).toBeGreaterThan(0);
    const select = vi.fn();
    view.rerender(<MacroTaskList {...p} loading onSelect={select}/>);
    expect(date).toHaveBeenCalledTimes(calls);
    fireEvent.click(document.querySelector('[data-slot="macro-entry"]')!);
    expect(select).toHaveBeenCalledWith("macro-1");
  });

  it("shows project folders and the standalone group with authoritative totals", async () => {
    const p = props();
    p.groups[0] = { ...p.groups[0]!, total: 17 };
    render(<MacroTaskList {...p}/>);
    const headings = [...document.querySelectorAll<HTMLButtonElement>('[data-slot="macro-group-heading"]')];
    expect(headings.map(heading => heading.textContent)).toEqual(expect.arrayContaining([expect.stringContaining("hey-my-buddy17"), expect.stringContaining("dsh-harness")]));
    expect(headings.at(-1)?.textContent).toContain("独立微任务");
    expect(headings.at(-1)?.getAttribute("aria-expanded")).toBe("false");
    expect(headings[0]?.querySelector("svg")).toBeTruthy();
    expect(document.querySelector('[data-macro-task-id="run:3f0e"]')).toBeNull();
    await userEvent.click(headings.at(-1)!);
    expect(document.querySelector('[data-macro-task-id="run:3f0e"]')).toBeTruthy();
    await userEvent.click(screen.getByRole("button", { name: "全部折叠" }));
    expect(document.querySelectorAll("[data-macro-task-id]")).toHaveLength(0);
    await userEvent.click(headings[0]!);
    expect(headings[0]?.getAttribute("aria-expanded")).toBe("true");
  });

  it("keeps a real project whose id is standalone distinct from the standalone group", () => {
    const p = props();
    const projectRow = { ...row(p), project: { id: "standalone", label: "独立项目", path: null } };
    const standaloneRow = row(p, "run:3f0e");
    p.groups = macroTaskGroups([projectRow, standaloneRow]);
    render(<MacroTaskList {...p}/>);
    expect(p.groups.map(group => group.id)).toEqual(["project:standalone", "standalone"]);
    expect(screen.getByRole("button", { name: /独立项目/ }).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button", { name: /独立微任务/ }).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeTruthy();
  });

  it("filters, searches, selects and names the tabs with their counts", async () => {
    const p = props(); render(<MacroTaskList {...p}/>);
    expect(screen.getByRole("heading", { name: "宏任务 12" }).querySelector("[data-slot=macro-list-total]")?.textContent).toBe("12");
    for (const name of ["进行中 2", "等待 Host 1", "等待验收 3"]) expect(screen.getByRole("tab", { name })).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "等待 Host 1" }));
    expect(p.onFilterChange).toHaveBeenCalledWith("host");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "原文" } });
    expect(p.onQueryChange).toHaveBeenCalledWith("原文");
    const entry = screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    expect(entry.querySelector("time")).toBeNull();
    expect(entry.getAttribute("aria-description")).toContain(`最后活动：${formatting.formatDateTime(Date.parse(row(p).lastActivityAt!))}`);
    await userEvent.click(entry);
    expect(p.onSelect).toHaveBeenCalledWith("macro-1");
  });

  it("translates tab names while keeping their full accessible names", async () => {
    await i18n.changeLanguage("en");
    try {
      render(<MacroTaskList {...props()}/>);
      expect(screen.getByRole("heading", { name: "Macro tasks 12" })).toBeTruthy();
      expect(screen.getByRole("tab", { name: "Waiting for Host 1" }).textContent).toBe("Host1");
      expect(screen.getByRole("tab", { name: "Awaiting acceptance 3" }).textContent).toBe("Acceptance3");
    } finally { await i18n.changeLanguage("zh"); }
  });

  it("draws one priority state mark per entry, never an edge or count strip", () => {
    const p = props();
    const variants = [
      { host: 1, review: 2, active: 1, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 1, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 0, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 0, failed: 0, accepted: 1 },
    ];
    const rows: MacroTaskSummary[] = variants.map((counts, index) => ({ ...row(p), macroTaskId: `macro-${index}`, title: `标题 ${index}`,
      counts: { ...row(p).counts, roots: 1, ended: counts.active ? 0 : 1, ...counts } }));
    p.groups = macroTaskGroups(rows);
    render(<MacroTaskList {...p}/>);
    const entries = [...document.querySelectorAll<HTMLButtonElement>('[data-slot="macro-entry"]')];
    expect(entries.map(entry => entry.querySelector('[data-slot="state-mark"]')?.getAttribute("data-kind") ?? null)).toEqual(["hand", "live", "fail", null]);
    expect(entries.every(entry => ![...entry.classList].some(name => name.startsWith("before:")))).toBe(true);
    expect(entries.every(entry => !entry.querySelector('[data-slot="macro-entry-counts"]'))).toBe(true);
    expect(entries.every(entry => entry.classList.contains("h-7"))).toBe(true);
    expect(entries.every(entry => entry.querySelector("time") === null)).toBe(true);
    expect(entries.every(entry => entry.querySelector('[data-slot="macro-entry-title"]')?.classList.contains("sidebar-label"))).toBe(true);
    expect(within(entries[0]!).getByText("等待 Host", { selector: ".sr-only" })).toBeTruthy();
  });

  it("marks the selected row and explains the sort time with the full title", () => {
    const p = props();
    const view = render(<MacroTaskList {...p}/>);
    const entry = screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    expect(entry.getAttribute("aria-description")).toContain(`${row(p).title} · 最后活动：`);
    view.rerender(<MacroTaskList {...p} sort="created-desc" selected="macro-1"/>);
    const sorted = screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    expect(sorted.getAttribute("aria-description")).toContain(`开始：${formatting.formatDateTime(Date.parse(row(p).createdAt!))}`);
    expect(sorted.getAttribute("aria-pressed")).toBe("true");
    expect(sorted.classList.contains("bg-selection")).toBe(true);
  });

  it("handles unnamed and nullable standalone facts", async () => {
    const p = props();
    p.groups = macroTaskGroups([{ ...row(p), kind: "standalone", titleSource: "none", title: null, project: null, createdAt: null, lastActivityAt: null }]);
    render(<MacroTaskList {...p}/>);
    await userEvent.click(screen.getByRole("button", { name: /独立微任务/ }));
    expect(screen.getByText("未命名")).toBeTruthy();
    expect(document.querySelector('[data-slot="macro-entry"]')?.getAttribute("aria-description")).toContain("最后活动：未记录");
  });

  it("shows empty and unmatched states and retains groups after read failure", () => {
    const p = props(); const view = render(<MacroTaskList {...p} groups={[]} totals={{all:0, active:0, host:0, review:0}}/>);
    expect(screen.getByText("没有宏任务")).toBeTruthy();
    view.rerender(<MacroTaskList {...p} groups={[]} query="找不到"/>);
    expect(screen.getByText("没有匹配的宏任务")).toBeTruthy();
    view.rerender(<MacroTaskList {...p} error="offline"/>);
    expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeTruthy();
  });

  it("pages only the selected group and disables all group pagination while loading", async () => {
    const p = props();
    p.groups = macroTaskGroups(listFixture(), "older");
    const view = render(<MacroTaskList {...p}/>);
    const buttons = [...document.querySelectorAll<HTMLButtonElement>('[data-slot="macro-group-more"]')];
    expect(buttons).toHaveLength(2);
    await userEvent.click(buttons[1]!);
    expect(p.onMore).toHaveBeenCalledWith("project:p2");
    view.rerender(<MacroTaskList {...p} loading/>);
    expect(buttons.every(button => button.disabled)).toBe(true);
    await userEvent.click(screen.getByRole("button", { name: "收起宏任务列表" }));
    expect(p.onCollapse).toHaveBeenCalledTimes(1);
  });

  it("reveals each appended response as one batch and resets after sorting or filtering", async () => {
    const p = props();
    const template = row(p);
    const rows = Array.from({ length: 24 }, (_, index) => ({ ...template, macroTaskId: `batch-${index}`, title: `标题 ${index}` }));
    p.groups = [{ ...p.groups[0]!, macroTasks: rows.slice(0, 7), total: 24, nextCursor: "older" }];
    const view = render(<MacroTaskList {...p}/>);
    expect(document.querySelectorAll('[data-slot="macro-entry"]')).toHaveLength(7);
    p.groups = [{ ...p.groups[0]!, macroTasks: rows.slice(0, 10) }];
    view.rerender(<MacroTaskList {...p}/>);
    await waitFor(() => expect(document.querySelectorAll('[data-slot="macro-entry"]')).toHaveLength(10));
    p.groups = [{ ...p.groups[0]!, macroTasks: rows }];
    view.rerender(<MacroTaskList {...p}/>);
    await waitFor(() => expect(document.querySelectorAll('[data-slot="macro-entry"]')).toHaveLength(24));
    view.rerender(<MacroTaskList {...p} sort="created-desc"/>);
    expect(document.querySelectorAll('[data-slot="macro-entry"]')).toHaveLength(24);
    view.rerender(<MacroTaskList {...p} sort="created-desc" filter="active"/>);
    expect(document.querySelectorAll('[data-slot="macro-entry"]')).toHaveLength(24);
  });

  it("defers a reorder under the pointer or scrolling, then applies it once", () => {
    const p = props(); const view = render(<MacroTaskList {...p}/>);
    const region = screen.getByRole("region", { name: "宏任务列表" });
    fireEvent.pointerEnter(region);
    view.rerender(<MacroTaskList {...p} reorder={{count:1}}/>);
    expect(p.onApplyReorder).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name:"有新的活动，更新排序"}));
    fireEvent.pointerLeave(region);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
  });
});
