import { cleanup, render, screen, within } from "@testing-library/react";
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
  selected: null, locked: false,
  onFilterChange: vi.fn(), onQueryChange: vi.fn(), onProjectChange: vi.fn(), onHostChange: vi.fn(),
  onSelect: vi.fn(), onRefresh: vi.fn(), onRetry: vi.fn(), onMore: vi.fn(), onApplyReorder: vi.fn(),
});

afterEach(() => cleanup());

describe("objective list", () => {
  it("shows only work objectives by default; standalone delegations fold into their own loaded-only section", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ObjectiveList {...p} />);
    // p1's main heading counts the two objectives, not the standalone row.
    const headings = screen.getAllByRole("button", { name: /已加载/ }).map(node => node.textContent?.replace(/\s+/g, " "));
    expect(headings).toEqual(["▾ hey-my-buddy已加载 2 个工作目标", "▾ dsh-harness已加载 1 个工作目标", "▸ 未归档委派（已加载 1）"]);
    const first = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(within(first).getByText("进行中 1")).toBeTruthy();
    expect(within(first).getByText("待验收 1")).toBeTruthy();
    expect(within(first).getByText("已结束 4")).toBeTruthy();
    expect(within(first).queryByText(/待决定/)).toBeNull();
    expect(within(first).getByText("共 6 个委派")).toBeTruthy();
    expect(within(first).getByText("来源 Host：codex-desktop")).toBeTruthy();
    // The standalone row is collapsed away by default.
    expect(screen.queryByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeNull();
    // Expanding shows it with the honest loaded-only label and note.
    await user.click(screen.getByRole("button", { name: /未归档委派（已加载 1）/ }));
    const standalone = screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ });
    expect(within(standalone).getByText("未归档委派")).toBeTruthy();
    expect(screen.getByText("这些委派提交时没有指定工作目标，按记录单独显示。")).toBeTruthy();
    // No join or archive affordance anywhere (the section heading itself is
    // not a write control; it only toggles the folded view).
    expect(screen.queryByRole("button", { name: /归档到|移出|加入工作目标/ })).toBeNull();
  });

  it("marks the loaded count as possibly incomplete while a next page exists", () => {
    const p = { ...props(), nextCursor: "older-page" };
    render(<ObjectiveList {...p} />);
    expect(screen.getByText(/已加载 4 个记录 · 可能还有更多 · 按最近活动排序/)).toBeTruthy();
    expect(screen.getByRole("button", { name: /未归档委派（已加载 1 · 可能还有更多）/ })).toBeTruthy();
  });

  it("uses the recorded intent title with the nullable summary as a second line", () => {
    const summarized: ObjectiveSummary[] = [{
      ...rows[0]!, summary: "已验证 0.15 交互切片的回归测试",
    }];
    render(<ObjectiveList {...{ ...props(), rows: summarized }} />);
    const row = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(within(row).getByText(/^结果：已验证 0\.15 交互切片的回归测试$/)).toBeTruthy();
    // The intent title keeps the titleSource meaning; no summary-as-title rule.
    expect(within(row).getAllByText(/工作目标时间轴：设计、接口与实现/).length).toBeGreaterThan(0);
  });

  it("filters the display by kind through the 显示 dropdown", async () => {
    const user = userEvent.setup();
    const p = props();
    render(<ObjectiveList {...p} />);
    const select = screen.getByLabelText("显示类别") as HTMLSelectElement;
    expect(select.value).toBe("objectives");
    await user.click(select);
    await user.selectOptions(select, "standalone");
    expect(screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /工作目标时间轴：设计、接口与实现/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /未归档委派（已加载/ })).toBeNull();
    await user.selectOptions(select, "all");
    expect(screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ })).toBeTruthy();
    expect(screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ })).toBeTruthy();
  });

  it("offers loading older records when the loaded page has only standalone delegations", async () => {
    const user = userEvent.setup();
    const standaloneOnly = [rows[2]!];
    const p = { ...props(), rows: standaloneOnly, total: 9, nextCursor: "older-page" };
    render(<ObjectiveList {...p} />);
    expect(screen.getByText("已加载的记录中暂无工作目标")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "加载更早记录" }));
    expect(p.onMore).toHaveBeenCalled();
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
    expect(screen.getByText("Host 提交委派后，这里按项目列出工作目标；旧记录各自显示为未归档委派。")).toBeTruthy();
    first.unmount();
    render(<ObjectiveList {...empty} filter="host" query="任意" />);
    expect(screen.getByText("没有匹配的工作目标")).toBeTruthy();
  });

  it("disables switching objectives while a detail holds an unconfirmed operation, keeping the selected one live", () => {
    const p = { ...props(), selected: "obj-1", locked: true };
    render(<ObjectiveList {...p} />);
    const selected = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(selected).toHaveProperty("disabled", false);
    const other = screen.getByRole("button", { name: /0.13 控制台入口候选版收尾与文档/ });
    expect(other).toHaveProperty("disabled", true);
    expect(other.getAttribute("title")).toContain("核对前不切换");
  });
});
