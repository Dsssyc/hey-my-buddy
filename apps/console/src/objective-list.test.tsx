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
    projects: [{ id: "p1", label: "hey-my-buddy", path: "~/Desktop/codespace/mememe/hey-my-buddy" },
      { id: "p2", label: "dsh-harness", path: "~/Desktop/codespace/dsh-harness" }],
    hosts: ["codex-desktop", "dsh-host-02"],
  },
  selected: null, rail: false, onExpand: vi.fn(), collapsible: false, onCollapse: vi.fn(),
  onFilterChange: vi.fn(), onQueryChange: vi.fn(), onProjectChange: vi.fn(), onHostChange: vi.fn(),
  onSelect: vi.fn(), onRefresh: vi.fn(), onRetry: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
});

afterEach(() => cleanup());

describe("objective list (0.16 P1.4)", () => {
  it("groups by project name and count; standalone roots are 历史独立委派 and folded by default", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ObjectiveList {...p} />);
    // Project headings carry only the name and a small count; the path and the
    // loaded count live in the tooltip.
    const heading = screen.getByRole("button", { name: /▾ hey-my-buddy/ });
    expect(heading.textContent).toContain("2");
    expect(heading.textContent).not.toContain("已加载");
    expect(heading.getAttribute("title")).toContain("~/Desktop/codespace/mememe/hey-my-buddy");
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
    expect(standaloneHeading.getAttribute("title")).toContain("已加载 1 个，可能还有更多");
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
    expect(heading.getAttribute("title")).toContain("这些委派提交时没有指定工作目标，按记录单独显示。");
    expect(screen.queryByText("这些委派提交时没有指定工作目标，按记录单独显示。")).toBeNull();
  });

  it("selects an objective, and a pending reorder keeps the selection while offering the notice", async () => {
    const user = userEvent.setup();
    const p = props();
    const first = render(<ObjectiveList {...p} />);
    await user.click(screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ }));
    expect(p.onSelect).toHaveBeenCalledWith("obj-2");
    first.unmount();
    const reordered = { ...p, selected: "obj-2", reorder: { count: 1 }, rows: [...rows].reverse() };
    render(<ObjectiveList {...reordered} />);
    const notice = screen.getByRole("button", { name: "有 1 个工作目标有新活动 · 按最近活动重新排序" });
    await user.click(notice);
    expect(reordered.onApplyReorder).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ }).getAttribute("aria-pressed")).toBe("true");
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
    expect(screen.getByText("Host 提交委派后，这里按项目列出工作目标；旧记录各自显示为历史独立委派。")).toBeTruthy();
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

  it("renders the 48px rail strip with the expand control and the state icon (U1)", () => {
    const onToggleRail = vi.fn();
    const view = render(<ObjectiveList { ...{ ...props(), rail: true, railState: rows[0], onToggleRail } } />);
    const expand = screen.getByRole("button", { name: /工作目标/ });
    expect(expand.getAttribute("aria-expanded")).toBe("false");
    expect(expand.textContent).toContain("›");
    // The rail shows only the strip: no filters, no rows.
    expect(view.container.querySelector(".list-filters")).toBeNull();
    expect(view.container.querySelector(".task-list")).toBeNull();
    expect(view.container.querySelector(".rail-state")!.getAttribute("aria-label")).toBe("当前工作目标：进行中");
    fireEvent.click(expand);
    expect(onToggleRail).toHaveBeenCalled();
    view.unmount();
  });

  it("shows a task first-line fallback as one ~40-character line with the 取自任务首行 note (U3)", async () => {
    const user = userEvent.setup();
    render(<ObjectiveList {...props()} />);
    await user.click(screen.getByRole("button", { name: /历史独立委派（1）/ }));
    const standalone = screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ });
    const title = standalone.querySelector("strong.task-title") as HTMLElement;
    expect(title.className).toContain("single-line");
    expect(title.textContent).toContain("取自任务首行");
    // One line of ~40 characters: the clipped label, not the whole task line.
    expect(title.textContent!.replace("取自任务首行", "").length).toBeLessThanOrEqual(45);
    expect(title.textContent).not.toBe(listFixture()[2]!.title);
    // §5: the tooltip carries the clipped line plus the pointer to detail.
    const tooltip = title.getAttribute("title")!;
    expect(tooltip).not.toBe(listFixture()[2]!.title);
    expect(tooltip.endsWith("（完整任务见详情）")).toBe(true);
    // An explicit objective title keeps its recorded bounded text and tooltip.
    const objective = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(objective.querySelector("strong.task-title")!.getAttribute("title")).toBe("工作目标时间轴：设计、接口与实现");
    expect(objective.querySelector(".title-source-note")).toBeNull();
  });
});
