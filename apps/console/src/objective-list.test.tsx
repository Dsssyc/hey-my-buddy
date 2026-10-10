import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveList } from "./ObjectiveList";
import { listFixture } from "./objective-fixtures";
import type { ObjectiveSummary } from "./objective-types";

const rows = listFixture();
const props = () => ({
  rows, total: rows.length, loading: false, error: "", nextCursor: null, reorder: null,
  filter: "all" as const, query: "", projectId: "", hostId: "",
  choices: {
    projects: [{ id: "p1", label: "hey-my-buddy", path: "~/projects/hey-my-buddy" },
      { id: "p2", label: "dsh-harness", path: "~/projects/dsh-harness" }],
    hosts: ["codex-desktop", "dsh-host-02"],
  },
  selected: null, rail: false, onExpand: vi.fn(), collapsible: false, onCollapse: vi.fn(),
  onFilterChange: vi.fn(), onQueryChange: vi.fn(), onProjectChange: vi.fn(), onHostChange: vi.fn(),
  onSelect: vi.fn(), onRefresh: vi.fn(), onRetry: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
});

afterEach(() => cleanup());

describe("objective list (0.16 P1.4)", () => {
  it("shows only the supplied read verification time and explicitly names missing evidence", () => {
    const checked = Date.parse("2026-10-09T11:12:13Z");
    const view = render(<ObjectiveList {...props()} verifiedAtMs={checked} />);
    expect(view.container.querySelector(".local-read-state")!.textContent).toBe(`核对时间 ${new Date(checked).toLocaleString("zh-CN", { hour12: false })}`);
    view.rerender(<ObjectiveList {...props()} verifiedAtMs={null} />);
    expect(view.container.querySelector(".local-read-state")!.textContent).toBe("核对时间未记录");
  });

  it("groups by project name and count; standalone roots are 历史独立委派 and folded by default", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ObjectiveList {...p} />);
    // Project headings carry only the name and a small count; the path and the
    // loaded count live in the tooltip.
    const heading = screen.getByRole("button", { name: /▾ hey-my-buddy/ });
    expect(heading.textContent).toContain("2");
    expect(heading.textContent).not.toContain("已加载");
    expect(heading.getAttribute("title")).toContain("~/projects/hey-my-buddy");
    expect(heading.getAttribute("title")).toContain("已加载 2 个");
    // The standalone row is collapsed away by default with its own heading.
    expect(screen.queryByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeNull();
    const standaloneHeading = screen.getByRole("button", { name: /历史独立委派（1）/ });
    expect(standaloneHeading.getAttribute("aria-expanded")).toBe("false");
    await user.click(standaloneHeading);
    expect(screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeTruthy();
    // No join or archive affordance anywhere.
    expect(screen.queryByRole("button", { name: /归档到|移出|加入工作目标/ })).toBeNull();
  });

  it("renders two-line entries: title over state + progress + relative time, Host ID only in the tooltip", () => {
    const p = props();
    render(<ObjectiveList {...p} />);
    const first = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    // The state word and the counts-derived progress sentence share the second line.
    expect(within(first).getByText("进行中")).toBeTruthy();
    expect(within(first).getByText("5 个委派 · 进行中")).toBeTruthy();
    // The old count chips, summary line and visible Host row are gone.
    expect(within(first).queryByText(/待验收 1/)).toBeNull();
    expect(within(first).queryByText(/共 6 个委派/)).toBeNull();
    expect(within(first).queryByText(/结果：/)).toBeNull();
    expect(within(first).queryByText(/来源 Host：/)).toBeNull();
    // The Host ID stays available through the row tooltip.
    expect(first.getAttribute("title")).toContain("来源 Host：codex-desktop");
    expect(first.getAttribute("title")).toContain("最近活动");
    // The review group's progress names the waiting count; the host one says 等待 Host.
    const review = screen.getByRole("button", { name: /0\.13 控制台入口候选版收尾与文档/ });
    expect(within(review).getByText("等待验收")).toBeTruthy();
    expect(within(review).getByText("6 个委派 · 1 个等待验收")).toBeTruthy();
    const hostGroup = screen.getByRole("button", { name: /DSH 插件在 Windows 路径下的沙箱回归排查/ });
    expect(within(hostGroup).getByText("等待 Host")).toBeTruthy();
    expect(within(hostGroup).getByText("2 个委派 · 等待 Host")).toBeTruthy();
  });

  it("says 已完成 only when every root is accepted; other ended groups keep an acceptance count", () => {
    const allAccepted: ObjectiveSummary[] = [rows[0]!, rows[1]!].map(row => ({
      ...row, state: "ended" as const,
      counts: { ...row.counts, accepted: row.counts.roots, active: 0, host: 0, review: 0 },
    }));
    const view = render(<ObjectiveList {...{ ...props(), rows: allAccepted }} />);
    const done = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(within(done).getByText("已完成")).toBeTruthy();
    expect(within(done).getByText("5 个委派 · 全部已验收")).toBeTruthy();
    view.unmount();
    const partly = [{ ...rows[1]!, state: "ended" as const, counts: { ...rows[1]!.counts, accepted: 5, review: 0 } }];
    render(<ObjectiveList {...{ ...props(), rows: partly }} />);
    const ended = screen.getByRole("button", { name: /0\.13 控制台入口候选版收尾与文档/ });
    expect(within(ended).getByText("已结束")).toBeTruthy();
    expect(within(ended).getByText("6 个委派 · 5 个已验收")).toBeTruthy();
  });

  it("keeps the loaded-count summary in the heading tooltip and the load-more button", () => {
    const p = { ...props(), nextCursor: "older-page" };
    render(<ObjectiveList {...p} />);
    expect(screen.getByRole("heading", { name: "工作目标" }).getAttribute("title")).toBe("按最近活动排序 · 已加载 4 个");
    expect(screen.getByRole("button", { name: "加载更早工作目标" })).toBeTruthy();
    // The possibly-more note lives in the standalone heading tooltip.
    const standaloneHeading = screen.getByRole("button", { name: /历史独立委派（1）/ });
    expect(standaloneHeading.getAttribute("title")).toContain("已加载 1 个，还有更多");
    expect(screen.queryByText(/已加载 4 个记录 · 可能还有更多/)).toBeNull();
  });

  it("folds the project, host and show-kind selectors into one 筛选 popup", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ObjectiveList {...p} />);
    // No standalone project/host selects outside the popup.
    expect(screen.queryByLabelText("目标项目筛选")).toBeNull();
    expect(screen.queryByLabelText("目标委派方筛选")).toBeNull();
    expect(screen.getByRole("button", { name: "筛选" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "筛选" }));
    const dialog = screen.getByRole("dialog", { name: "筛选" });
    const show = within(dialog).getByLabelText("显示") as HTMLSelectElement;
    expect(show.value).toBe("objectives");
    await user.selectOptions(show, "standalone");
    expect(screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /工作目标时间轴：设计、接口与实现/ })).toBeNull();
    // Non-default filters badge the button with a count and clear in one step.
    expect(screen.getByRole("button", { name: /筛选 · 1/ })).toBeTruthy();
    await user.click(within(screen.getByRole("dialog", { name: "筛选" })).getByRole("button", { name: "清除筛选" }));
    expect(screen.getByRole("button", { name: "筛选" }).textContent).toBe("筛选");
  });

  it("moves the standalone explanation into the section tooltip", () => {
    render(<ObjectiveList {...props()} />);
    const heading = screen.getByRole("button", { name: /历史独立委派（1）/ });
    expect(heading.getAttribute("title")).toContain("未指定工作目标");
    expect(screen.queryByText("未指定工作目标")).toBeNull();
  });

  it("reorders silently at the top when the pointer is outside the visible list", () => {
    const p = props();
    const view = render(<ObjectiveList {...p} />);
    view.rerender(<ObjectiveList {...p} reorder={{ count: 1 }} />);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
    expect(view.container.querySelector(".new-records")).toBeNull();
  });

  it("defers while hovered and applies the heading marker without losing selection", async () => {
    const user = userEvent.setup();
    const p = { ...props(), selected: "obj-2" };
    const view = render(<ObjectiveList {...p} />);
    await user.click(screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ }));
    expect(p.onSelect).toHaveBeenCalledWith("obj-2");
    fireEvent.pointerEnter(screen.getByRole("region", { name: "工作目标列表" }));
    view.rerender(<ObjectiveList {...p} reorder={{ count: 1 }} />);
    expect(p.onApplyReorder).not.toHaveBeenCalled();
    const marker = screen.getByRole("button", { name: "有更新" });
    expect(marker.closest(".panel-toolbar")).not.toBeNull();
    await user.click(marker);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ }).getAttribute("aria-pressed")).toBe("true");
  });

  it("waits below the top, then applies when scrolling back to the top", () => {
    const p = props();
    const view = render(<ObjectiveList {...p} />);
    const scroll = screen.getByLabelText("工作目标条目");
    scroll.scrollTop = 80;
    view.rerender(<ObjectiveList {...p} reorder={{ count: null }} />);
    expect(p.onApplyReorder).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "有更新" })).toBeTruthy();
    scroll.scrollTop = 0;
    fireEvent.scroll(scroll);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
  });

  it("applies on pointer leave at the top, but never from a hidden list", () => {
    const p = props();
    const view = render(<ObjectiveList {...p} />);
    const region = screen.getByRole("region", { name: "工作目标列表" });
    fireEvent.pointerEnter(region);
    view.rerender(<ObjectiveList {...p} reorder={{ count: 2 }} />);
    expect(p.onApplyReorder).not.toHaveBeenCalled();
    fireEvent.pointerLeave(region);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
    p.onApplyReorder.mockClear();
    view.rerender(<ObjectiveList {...p} reorder={null} />);
    view.rerender(<ObjectiveList {...p} reorder={{ count: 2 }} visible={false} />);
    expect(screen.queryByRole("button", { name: "有更新" })).toBeNull();
    expect(p.onApplyReorder).not.toHaveBeenCalled();
    view.rerender(<ObjectiveList {...p} reorder={{ count: 2 }} visible />);
    expect(p.onApplyReorder).toHaveBeenCalledTimes(1);
  });

  it("pages older objectives through the load-more affordance", async () => {
    const user = userEvent.setup();
    const p = { ...props(), nextCursor: "older-page" };
    render(<ObjectiveList {...p} />);
    await user.click(screen.getByRole("button", { name: "加载更早工作目标" }));
    expect(p.onMore).toHaveBeenCalled();
  });

  it("shows the empty state only without filters and a matching state otherwise", () => {
    const empty = { ...props(), rows: [] as ObjectiveSummary[], total: 0 };
    const first = render(<ObjectiveList {...empty} />);
    expect(screen.getByText("还没有工作目标")).toBeTruthy();
    expect(screen.getByText("还没有工作目标")).toBeTruthy();
    first.unmount();
    render(<ObjectiveList {...empty} filter="host" query="任意" />);
    expect(screen.getByText("没有匹配的工作目标")).toBeTruthy();
  });

  it("switching objectives stays free while a detail is open — no navigation lock (U4)", () => {
    const p = { ...props(), selected: "obj-1" };
    render(<ObjectiveList {...p} />);
    const selected = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(selected).toHaveProperty("disabled", false);
    const other = screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ });
    expect(other).toHaveProperty("disabled", false);
  });

  it("renders the rail while keeping list content mounted and hidden", () => {
    const onToggleRail = vi.fn();
    const view = render(<ObjectiveList { ...{ ...props(), rail: true, onToggleRail } } />);
    const expand = screen.getByRole("button", { name: /工作目标/ });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(expand.textContent).toContain("›");
    // The rail shows only the strip: no filters, no rows.
    expect(view.container.querySelector(".list-filters")!.closest("[hidden]")).not.toBeNull();
    expect(view.container.querySelector(".task-list")!.closest("[hidden]")).not.toBeNull();
    expect(view.container.querySelector(".rail-state")).toBeNull();
    fireEvent.click(expand);
    expect(onToggleRail).toHaveBeenCalled();
    view.unmount();
  });

  it("shows a task first-line fallback as one ~40-character line with the 任务首行 note (U3)", async () => {
    const user = userEvent.setup();
    render(<ObjectiveList {...props()} />);
    await user.click(screen.getByRole("button", { name: /历史独立委派（1）/ }));
    const standalone = screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ });
    const title = standalone.querySelector("strong.task-title") as HTMLElement;
    expect(title.className).toContain("single-line");
    expect(title.textContent).toContain("任务首行");
    // One line of ~40 characters: the clipped label, not the whole task line.
    expect(title.textContent!.replace("任务首行", "").length).toBeLessThanOrEqual(45);
    expect(title.textContent).not.toBe(listFixture()[2]!.title);
    // §5: the tooltip carries the clipped line plus the pointer to detail.
    const tooltip = title.getAttribute("title")!;
    expect(tooltip).toBe(listFixture()[2]!.title);
    // An explicit objective title keeps its recorded bounded text and tooltip.
    const objective = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(objective.querySelector("strong.task-title")!.getAttribute("title")).toBe("工作目标时间轴：设计、接口与实现");
    expect(objective.querySelector(".title-source-note")).toBeNull();
  });
});
