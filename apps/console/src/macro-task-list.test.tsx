import { TooltipProvider } from "./components/ui/tooltip";
import type { ReactElement } from "react";
import { cleanup, fireEvent, render as baseRender, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MacroTaskList } from "./MacroTaskList";
import { listFixture } from "./macro-task-fixtures";
import type { MacroTaskListProps } from "./MacroTaskList";

const props = (): MacroTaskListProps => ({
  rows: listFixture(), totals: { all: 12, active: 2, host: 1, review: 3 }, loading: false, error: "", nextCursor: null, reorder: null,
  filter: "all", query: "", selected: null, listNavigation: <span>宏任务</span>,
  onCollapse: vi.fn(), onFilterChange: vi.fn(), onQueryChange: vi.fn(), onSelect: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
});
afterEach(cleanup);
describe("macro task list", () => {
  it("groups by project with loaded counts and puts collapsed standalone micro tasks last", async () => {
    const p = props(); render(<MacroTaskList {...p}/>);
    const groups = [...document.querySelectorAll('[data-slot="macro-group-heading"]')];
    expect(groups[0].textContent).toContain("hey-my-buddy");
    expect(groups[0].textContent).toContain("2");
    expect(groups.at(-1)?.textContent).toContain("独立微任务");
    expect(groups.at(-1)?.getAttribute("aria-expanded")).toBe("false");
    expect(document.querySelector(`[data-macro-task-id="${p.rows.find(row => row.kind === "standalone")!.macroTaskId}"]`)).toBeNull();
    await userEvent.click(groups.at(-1)!);
    expect(document.querySelector(`[data-macro-task-id="${p.rows.find(row => row.kind === "standalone")!.macroTaskId}"]`)).toBeTruthy();
    await userEvent.click(groups[0]);
    expect(screen.queryByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeNull();
    expect(document.querySelectorAll("[title]")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: /筛选|归档|移出|加入/ })).toBeNull();
  });
  it("keeps a project named like the independent group expanded", () => {
    const p=props(); p.rows=[{...p.rows[0],project:{id:"standalone",label:"独立项目",path:null}},p.rows.find(row => row.kind === "standalone")!];
    render(<MacroTaskList {...p}/>);
    expect(screen.getByRole("button",{name:/独立项目/}).getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("button",{name:/独立微任务/}).getAttribute("aria-expanded")).toBe("false");
    expect(screen.getByRole("button",{name:/宏任务时间轴：设计、接口与实现/})).toBeTruthy();
  });
  it("shows server totals on four tabs and sends search and selection without write controls", async () => {
    const p = props(); render(<MacroTaskList {...p}/>);
    expect(screen.getByRole("tab", { name: "全部 12" }).getAttribute("aria-selected")).toBe("true");
    for (const name of ["进行中 2", "等待 Host 1", "等待验收 3"]) expect(screen.getByRole("tab", { name })).toBeTruthy();
    await userEvent.click(screen.getByRole("tab", { name: "等待 Host 1" })); expect(p.onFilterChange).toHaveBeenCalledWith("host");
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "原文" } }); expect(p.onQueryChange).toHaveBeenCalledWith("原文");
    const row = screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ });
    expect(row.querySelector("time")?.getAttribute("datetime")).toBe(p.rows[0].lastActivityAt);
    expect(within(row).getByLabelText("进行中 1")).toBeTruthy();
    expect(within(row).getByLabelText("已验收 2")).toBeTruthy();
    await userEvent.click(row); expect(p.onSelect).toHaveBeenCalledWith(p.rows[0].macroTaskId);
  });
  it("draws only actionable state edges, names the state and keeps the count marks", () => {
    const p = props();
    const variants = [
      { host: 1, review: 2, active: 1, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 1, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 0, failed: 1, accepted: 0 },
      { host: 0, review: 0, active: 0, failed: 0, accepted: 1 },
      { host: 0, review: 0, active: 0, failed: 0, accepted: 0 },
    ];
    p.rows = variants.map((counts, index) => ({ ...p.rows[0], macroTaskId: `macro-${index}`, title: `标题 ${index}`, counts: { ...p.rows[0].counts, roots: 1, ended: counts.active ? 0 : 1, ...counts } }));
    render(<MacroTaskList {...p}/>);
    const rows = [...document.querySelectorAll('[data-slot="macro-entry"]')];
    expect(rows.map(row => row.classList.contains("before:bg-hand-mark"))).toEqual([true, false, false, false, false]);
    expect(rows.map(row => row.classList.contains("before:bg-live"))).toEqual([false, true, false, false, false]);
    expect(rows.map(row => row.classList.contains("before:w-[3px]"))).toEqual([true, true, false, false, false]);
    expect(rows.every(row => !row.querySelector(":scope > [data-slot=state-mark]"))).toBe(true);
    for (const [index, status] of ["等人接手", "进行中", "失败", "已验收", "已结束"].entries()) {
      expect(screen.getByRole("button", { name: `标题 ${index} · ${status}` })).toBe(rows[index]);
    }
    expect(rows[2].querySelector('[data-slot="macro-entry-counts"] [data-kind="fail"] path')).toBeTruthy();
    expect(within(rows[0] as HTMLElement).getByLabelText("等人接手 3")).toBeTruthy();
    expect(rows[0].querySelectorAll('[data-slot="macro-entry-counts"] [data-kind="hand"]')).toHaveLength(1);
    expect(within(rows[2] as HTMLElement).getByLabelText("失败 1")).toBeTruthy();
    expect(rows[0].hasAttribute("aria-description")).toBe(false);
    expect(screen.getByRole("tab", { name: "等待 Host 1" }).hasAttribute("aria-description")).toBe(false);
    expect(screen.getByRole("textbox").getAttribute("data-slot")).toBe("macro-search");
  });
  it("selects with a wash while retaining only the state's edge and protecting it on hover", () => {
    const p = props(); p.rows = [{ ...p.rows[0], counts: { ...p.rows[0].counts, host: 0, review: 0, active: 1 } }];
    const view = render(<MacroTaskList {...p} selected={null}/>);
    const row = document.querySelector('[data-slot="macro-entry"]')!;
    const edge = [...row.classList].filter(name => name.startsWith("before:"));
    view.rerender(<MacroTaskList {...p} selected={p.rows[0].macroTaskId}/>);
    expect(row.getAttribute("aria-pressed")).toBe("true");
    expect(row.classList.contains("bg-live-wash")).toBe(true);
    expect(row.classList.contains("aria-pressed:hover:bg-live-wash")).toBe(true);
    expect([...row.classList].filter(name => name.startsWith("before:"))).toEqual(edge);
    expect(row.classList.contains("transition-colors")).toBe(true);
    expect(row.classList.contains("motion-reduce:transition-none")).toBe(true);
  });
  it("uses first line with provenance in Hint, clips it at 40 characters and names missing titles", () => {
    const p = props(); p.rows = [{ ...p.rows[0], titleSource: "task", title: "任".repeat(80) }, { ...p.rows[1], titleSource: "none", title: "" }];
    render(<MacroTaskList {...p}/>);
    const row = screen.getByRole("button", { name: new RegExp("任".repeat(40)) });
    expect(row.getAttribute("aria-description")).toContain("取自首行");
    expect(row.querySelector('[data-slot="macro-entry-title"]')?.textContent).toBe("任".repeat(40));
    expect(screen.getByText("未命名")).toBeTruthy();
  });
  it("distinguishes empty and unmatched and preserves entries after read failure", () => {
    const p = props(); const f = render(<MacroTaskList {...p} rows={[]} totals={{all:0, active:0, host:0, review:0}}/>);
    expect(screen.getByText("没有宏任务")).toBeTruthy();
    f.rerender(<MacroTaskList {...p} rows={[]} query="找不到"/>); expect(screen.getByText("没有匹配的宏任务")).toBeTruthy();
    f.rerender(<MacroTaskList {...p} error="offline"/>); expect(screen.getByRole("button", { name: /宏任务时间轴：设计、接口与实现/ })).toBeTruthy();
  });
  it("renders the nullable fields the service can return without inventing a time", async () => {
    const p=props(); p.rows=[{...p.rows[0],kind:"standalone",project:null,createdAt:null,lastActivityAt:null}];
    render(<MacroTaskList {...p}/>); await userEvent.click(screen.getByRole("button",{name:/独立微任务/}));
    expect(document.querySelector('[data-slot="macro-entry"] time')?.textContent).toBe("未记录");
    expect(document.querySelector('[data-slot="macro-entry"] time')?.hasAttribute("datetime")).toBe(false);
  });
  it("loads earlier only when available and idle, and collapses through one button", async () => {
    const p=props(); const f=render(<MacroTaskList {...p} nextCursor="older"/>);
    await userEvent.click(screen.getByRole("button", {name:"更早"})); expect(p.onMore).toHaveBeenCalledTimes(1);
    f.rerender(<MacroTaskList {...p} nextCursor="older" loading/>);
    await userEvent.click(screen.getByRole("button", {name:"读取中"})); expect(p.onMore).toHaveBeenCalledTimes(1);
    await userEvent.click(screen.getByRole("button", {name:"收起宏任务列表"})); expect(p.onCollapse).toHaveBeenCalledTimes(1);
  });
  it("defers activity reorder under pointer, then applies on leaving or explicit marker, never twice", () => {
    const p=props(); const f=render(<MacroTaskList {...p}/>); const region=screen.getByRole("region", {name:"宏任务列表"});
    fireEvent.pointerEnter(region);
    f.rerender(<MacroTaskList {...p} reorder={{count:1}}/>); expect(p.onApplyReorder).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name:"有新的活动，按最近活动排序"})); expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
    fireEvent.pointerLeave(region); expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
  });
  it("defers while scrolled, invisible or inactive; reaching top applies when pointer is outside", () => {
    const p=props(); const f=render(<MacroTaskList {...p}/>);
    const viewport=document.querySelector("[data-slot=scroll-area-viewport]")!;
    Object.defineProperty(viewport,"scrollTop",{configurable:true,writable:true,value:120});
    f.rerender(<MacroTaskList {...p} reorder={{count:1}}/>); expect(p.onApplyReorder).not.toHaveBeenCalled();
    Object.defineProperty(viewport,"scrollTop",{value:0}); fireEvent.scroll(viewport); expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
    f.rerender(<MacroTaskList {...p} reorder={null}/>); p.onApplyReorder=vi.fn();
    f.rerender(<MacroTaskList {...p} visible={false} reorder={{count:1}}/>); expect(p.onApplyReorder).not.toHaveBeenCalled();
  });
});

function render(ui: ReactElement) { return baseRender(ui, { wrapper: TooltipProvider }); }
