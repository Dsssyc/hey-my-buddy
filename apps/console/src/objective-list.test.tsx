import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ObjectiveList } from "./ObjectiveList";
import { listFixture } from "./objective-fixtures";
import type { ObjectiveSummary } from "./objective-types";

const rows = listFixture();
const noop = () => {};
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
  it("groups by source project, keeps activity order and shows fixed-order counts with zeros omitted", () => {
    render(<ObjectiveList {...props()} />);
    const headings = screen.getAllByRole("button", { name: /已加载/ }).map(node => node.textContent?.replace(/\s+/g, " "));
    // p1 (newest activity 08:12) comes before p2 (08:11); within p1 seq descends.
    expect(headings).toEqual(["▾ hey-my-buddy 已加载 3 个工作目标", "▾ dsh-harness 已加载 1 个工作目标"].map(text => text.replace(" 已加载", "已加载")));
    const first = screen.getByRole("button", { name: /工作目标时间轴：设计、接口与实现/ });
    expect(within(first).getByText("进行中 1")).toBeTruthy();
    expect(within(first).getByText("待验收 1")).toBeTruthy();
    expect(within(first).getByText("已结束 4")).toBeTruthy();
    expect(within(first).queryByText(/待决定/)).toBeNull();
    expect(within(first).getByText("共 6 个委派")).toBeTruthy();
    expect(within(first).getByText("来源 Host：codex-desktop")).toBeTruthy();
    expect(screen.getByText("已加载 4 / 4 个工作目标 · 按最近活动排序 · 项目选项来自已加载记录")).toBeTruthy();
  });

  it("marks standalone roots with the neutral label and an absolute tooltip time", () => {
    render(<ObjectiveList {...props()} />);
    const standalone = screen.getByRole("button", { name: /修复标题回退在 CRLF 输入下的显示/ });
    expect(within(standalone).getByText("独立委派")).toBeTruthy();
    expect(within(standalone).getByText("已结束 1")).toBeTruthy();
    const time = standalone.querySelector("time")!;
    expect(time.getAttribute("dateTime")).toBe("2026-09-25T10:22:00Z");
    // The absolute fallback keeps the design's MM-DD HH:MM shape in local time.
    expect(time.getAttribute("title")).toMatch(/^\d{2}-\d{2} \d{2}:\d{2}$/);
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
    expect(screen.getByText("Host 提交委派后，这里按项目列出工作目标；旧记录各自显示为独立委派。")).toBeTruthy();
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
