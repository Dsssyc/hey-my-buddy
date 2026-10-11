import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import { App } from "../App";
import { CHECKED_AT, fixture, harness, harnesses, openHarness, snapshot } from "./harness.fixtures";
import {
  fieldValue, harnessButton, harnessCells, harnessPane, harnessRow, harnessTable, paneBlock, selectHarness, toolbarAlert,
} from "./harness.testing";
import { HarnessView } from "./HarnessView";

/**
 * ADR-017 §15 on the Buddy page's harness table: each harness shows its state,
 * version, account and quota in a row, and its path, source, last check, or its
 * attempted locations and remedy, in the side column. Checking is an explicit
 * click that goes through the existing `capabilities` operation with
 * `refresh: true` and never calls a model; saving a path goes through
 * `harness_set` with the recorded revision, so a concurrent change is a
 * reported conflict rather than an overwrite.
 */

const clock = (iso: string) => {
  const date = new Date(iso);
  const pad = (part: number) => String(part).padStart(2, "0");
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
};

afterEach(() => {
  cleanup();
  window.location.hash = "";
  sessionStorage.clear();
  document.documentElement.dataset.theme = "";
});

describe("the harness table", () => {
  it("shows each harness's recorded state, version and path without calling anything", async () => {
    const f = fixture();
    await openHarness(f);
    expect(within(harnessTable()).getAllByRole("columnheader").map(cell => cell.textContent))
      .toEqual(["Harness", "状态", "版本", "账户", "额度"]);
    // One row per harness, in the order the snapshot gives.
    expect(within(harnessTable()).getAllByRole("button").map(button => button.textContent)).toEqual(["DSH", "ZCode", "Codex", "Claude Code"]);
    expect(harnessCells("DSH").slice(1, 3).map(cell => cell.textContent)).toEqual(["已找到", "0.4.2"]);
    expect(harnessCells("ZCode")[1].textContent).toBe("未找到");
    expect(harnessCells("Codex").slice(1, 3).map(cell => cell.textContent)).toEqual(["未登录", "0.157.0"]);
    expect(harnessCells("Claude Code").slice(1, 3).map(cell => cell.textContent)).toEqual(["未通过", "2.0.1"]);
    // Reading the page is not an action: no command runs and no draft is created.
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
  });

  it("marks a found harness with a filled circle and any other with a hollow one, and writes the others lighter", async () => {
    await openHarness(fixture());
    const mark = (name: string) => harnessCells(name)[1].querySelector("[data-slot=state-mark]")!.getAttribute("data-kind");
    expect([mark("DSH"), mark("ZCode"), mark("Codex"), mark("Claude Code")]).toEqual(["live", "idle", "idle", "idle"]);
    expect(harnessRow("DSH").className).not.toContain("text-ink3");
    for (const name of ["ZCode", "Codex", "Claude Code"]) expect(harnessRow(name).className).toContain("text-ink3");
  });

  it("writes the account by its source, and by its state besides when it is not configured", async () => {
    const account = { source: "native", revision: 1, credentialRevision: 0, status: "ready", accountType: null, checkedAt: null,
      reasonCode: null, guidance: null, capabilities: { workerAccount: true, oauth: false, apiKey: false, logout: false, remove: false } };
    const rows = [
      harness({ adapter: "dsh", status: "ready", account: { ...account, adapter: "dsh" } as never }),
      harness({ adapter: "zcode", status: "ready", account: { ...account, adapter: "zcode", source: "worker", status: "logged-out" } as never }),
      harness({ adapter: "codex", status: "ready", account: { ...account, adapter: "codex", status: "unsupported" } as never }),
      harness({ adapter: "claude", status: "ready" }),
    ];
    await openHarness(fixture({ harnesses: rows }));
    expect(["DSH", "ZCode", "Codex", "Claude Code"].map(name => harnessCells(name)[3].textContent))
      .toEqual(["共用登录", "独立账户未登录", "共用登录未验证", "—"]);
  });

  it("selects a row, tints it and shows its side column; the first row is open before any is chosen", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    expect(harnessPane("DSH")).toBeTruthy();
    expect(harnessButton("DSH").getAttribute("aria-pressed")).toBe("true");
    expect(harnessRow("DSH").getAttribute("data-selected")).toBe("true");
    await selectHarness(user, "Codex");
    expect(harnessButton("Codex").getAttribute("aria-pressed")).toBe("true");
    expect(harnessButton("DSH").getAttribute("aria-pressed")).toBe("false");
    expect(harnessRow("DSH").getAttribute("data-selected")).toBe("false");
    // Clicking anywhere on the row selects it.
    await user.click(harnessCells("ZCode")[2]);
    await waitFor(() => harnessPane("ZCode"));
  });

  it("lets the side column take the table's place in a narrow window, and gives the table back", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    const table = () => harnessTable().closest("div.min-h-0")!.parentElement!;
    const column = () => table().parentElement!.querySelector<HTMLElement>("[data-slot=scroll-area]:has(aside)")!;
    expect(column().className).toContain("max-xl:hidden");
    await user.click(harnessButton("Codex"));
    expect(column().className).not.toContain("max-xl:hidden");
    await user.click(within(harnessPane("Codex")).getByRole("button", { name: "Harness" }));
    expect(column().className).toContain("max-xl:hidden");
  });
});

describe("the toolbar", () => {
  it("says when the harnesses were last checked, from the latest of them", async () => {
    const rows = harnesses();
    rows[1] = { ...rows[1], checkedAt: "2026-09-29T08:00:00.000Z" };
    await openHarness(fixture({ harnesses: rows }));
    expect(screen.getByText("上次检测").parentElement!.textContent).toBe(`上次检测${clock("2026-09-29T08:00:00.000Z")}`);
    expect(screen.getByRole("button", { name: "全部检测" })).toBeTruthy();
  });

  it("writes a dash while no harness was checked", async () => {
    await openHarness(fixture({ harnesses: [harness({ adapter: "dsh", status: "unknown" })] }));
    expect(screen.getByText("上次检测").parentElement!.textContent).toBe("上次检测—");
  });
});

describe("an empty snapshot", () => {
  it("has only the toolbar and an empty state, and invents no health", async () => {
    const f = fixture({ harnesses: [] });
    await openHarness(f);
    expect(screen.getByText("没有 Harness 记录")).toBeTruthy();
    expect(screen.getByText("点“全部检测”，查找本机的 Harness。")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    expect(screen.queryByRole("complementary")).toBeNull();
    expect(screen.getByRole("navigation", { name: "目录的内容" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "全部检测" })).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("the side column", () => {
  it("names a found harness's path, source and last check, and offers only the two actions", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const location = paneBlock("位置");
    expect(fieldValue(location, "路径").textContent).toBe("/usr/local/bin/dsh");
    expect(fieldValue(location, "来源").textContent).toBe("版本管理器（nvm 默认版本）");
    expect(fieldValue(location, "检测").textContent).toBe(clock(CHECKED_AT));
    expect(within(location).getByText("已找到")).toBeTruthy();
    // Nothing exceptional is said of a harness that is found.
    for (const word of ["原因", "试过的位置"]) expect(within(location).queryByText(word)).toBeNull();
    expect(within(harnessPane()).queryByText(/请先|安装/)).toBeNull();
    // The path can always be set, found or not.
    expect(within(location).getByRole("button", { name: "检测" })).toBeTruthy();
    await user.click(within(location).getByRole("button", { name: "指定路径" }));
    expect(await screen.findByRole("dialog", { name: "DSH 路径" })).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("says why a harness is not found, what to do and where it looked", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    await selectHarness(user, "ZCode");
    const location = paneBlock("位置");
    expect(within(location).getByText("未找到")).toBeTruthy();
    expect(fieldValue(location, "原因").textContent).toBe("未找到可执行文件");
    expect(within(location).getByText("安装 ZCode CLI，或填写可执行文件的绝对路径。")).toBeTruthy();
    expect(fieldValue(location, "路径").textContent).toBe("未记录");
    const tried = within(location).getByText("试过的位置").parentElement!;
    expect(within(tried).getByText("/Applications/ZCode.app/Contents/MacOS/zcode")).toBeTruthy();
    expect(within(tried).getByText("应用包 · 未找到")).toBeTruthy();
    expect(within(tried).getByText("/opt/homebrew/bin/zcode")).toBeTruthy();
    expect(within(tried).getByText("Homebrew · 未找到")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("says a manually stored path is manual and offers to restore detection", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    await selectHarness(user, "Claude Code");
    const location = paneBlock("位置");
    expect(fieldValue(location, "路径").textContent).toBe("/Users/fixture/.claude/local/claude");
    expect(fieldValue(location, "来源").textContent).toBe("手动");
    await user.click(within(location).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("textbox", { name: "可执行文件路径" })).toHaveProperty("value", "/Users/fixture/.claude/local/claude");
    expect(within(dialog).getByRole("button", { name: "恢复自动检测" })).toBeTruthy();
  });

  it("offers no restore where no path is stored", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    await user.click(within(paneBlock("位置")).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "恢复自动检测" })).toBeNull();
    expect(within(dialog).getByRole("textbox", { name: "可执行文件路径" })).toHaveProperty("value", "");
  });
});

describe("opening a harness by its address", () => {
  const rows = () => [
    harness({ adapter: "claude", status: "ready", executable: "/usr/local/bin/claude", version: "2.0.1", source: "PATH", checkedAt: CHECKED_AT }),
    harness({ adapter: "codex", status: "ready", executable: "/opt/bin/codex", version: "0.159.0", source: "PATH", checkedAt: CHECKED_AT,
      readOnlyStructured: { implemented: true, eligible: false, systemSandbox: true, sameAttemptContinuation: false,
        reasonCode: "READ_ONLY_RESOURCE_UNAVAILABLE", reason: "本地只读运行资源不可用" } }),
    harness({ adapter: "dsh", status: "ready", executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH", checkedAt: CHECKED_AT,
      quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official", stale: false, windows: [
        { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" }, { name: "weekly", usedPercent: 41, resetsAt: null }] } }),
  ];

  it("opens the Codex row focused from #buddies/harness/codex", async () => {
    await openHarness(fixture({ harnesses: rows() }), "#buddies/harness/codex");
    expect(screen.queryByRole("table", { name: "模型" })).toBeNull();
    expect(harnessButton("Codex").getAttribute("aria-pressed")).toBe("true");
    expect(harnessPane("Codex")).toBeTruthy();
    expect(within(paneBlock("位置")).getByText("本地只读运行资源不可用")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(harnessButton("Codex")));
    const sections = screen.getByRole("navigation", { name: "Buddy 的分区" });
    expect(within(sections).getAllByRole("link").filter(link => link.getAttribute("aria-current") === "page").map(link => link.firstChild?.textContent)).toEqual(["目录"]);
    const tables = screen.getByRole("navigation", { name: "目录的内容" });
    expect(within(tables).getAllByRole("link").filter(link => link.getAttribute("aria-current") === "page").map(link => link.firstChild?.textContent)).toEqual(["Harness"]);
  });

  it("opens the DSH row with its quota from #buddies/harness/dsh", async () => {
    await openHarness(fixture({ harnesses: rows() }), "#buddies/harness/dsh");
    expect(harnessPane("DSH")).toBeTruthy();
    const quota = paneBlock("额度");
    expect(within(quota).getByText("93%")).toBeTruthy();
    expect(within(quota).getByText("接近上限")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(harnessButton("DSH")));
  });

  it("leaves the table as it was for an address that names no harness", async () => {
    await openHarness(fixture({ harnesses: rows() }), "#buddies/harness/unknown");
    expect(harnessPane("DSH")).toBeTruthy();
    expect(document.activeElement).not.toBe(harnessButton("DSH"));
  });
});

describe("checking", () => {
  it("checks one harness through the existing capabilities operation and reloads the snapshot", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    await selectHarness(user, "Codex");
    const reads = f.snapshotMock.mock.calls.length;
    await user.click(within(paneBlock("位置")).getByRole("button", { name: "检测" }));
    await screen.findByText("已检测 Codex：已找到");
    // No model call and no new path: one named adapter through /api/command.
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true, adapter: "codex" } }]);
    await waitFor(() => expect(f.snapshotMock.mock.calls.length).toBeGreaterThan(reads));
    await waitFor(() => expect(fieldValue(paneBlock("位置"), "路径").textContent).toBe("/opt/bin/codex"));
    expect(harnessCells("Codex").slice(1, 3).map(cell => cell.textContent)).toEqual(["已找到", "9.9.9"]);
    // The others were not checked.
    expect(harnessCells("ZCode")[1].textContent).toBe("未找到");
  });

  it("checks every harness when no one is named", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    await screen.findByText("已检测全部 Harness：已找到 4 / 4");
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true } }]);
    for (const name of ["DSH", "ZCode", "Codex", "Claude Code"]) expect(harnessCells(name)[1].textContent).toBe("已找到");
  });

  it("keeps a failed check retryable, and reports success only after a real reply", async () => {
    const user = userEvent.setup();
    const f = fixture({ failFirstCheck: true });
    await openHarness(f);
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    await toolbarAlert();
    expect(screen.getByText("无法连接本地黑板。已有任务仍由后台管理。")).toBeTruthy();
    // The recorded state is untouched by the failure.
    expect(harnessCells("ZCode")[1].textContent).toBe("未找到");
    expect(screen.queryByText(/已检测/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已检测全部 Harness：已找到 4 / 4");
    expect(f.calls.map(call => call.operation)).toEqual(["capabilities", "capabilities"]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("retries the same action: a failed check of one harness is checked again for that harness", async () => {
    const user = userEvent.setup();
    const f = fixture({ failFirstCheck: true });
    await openHarness(f);
    await selectHarness(user, "ZCode");
    await user.click(within(paneBlock("位置")).getByRole("button", { name: "检测" }));
    await toolbarAlert();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已检测 ZCode：已找到");
    expect(f.calls.map(call => call.params)).toEqual([{ refresh: true, adapter: "zcode" }, { refresh: true, adapter: "zcode" }]);
  });

  it("lets no second action start while one is under way", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    const f = fixture({ replies: { capabilities: () => new Promise(resolve => { finish = resolve; }) } });
    await openHarness(f);
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    await waitFor(() => expect(f.command).toHaveBeenCalledTimes(1));
    for (const button of [screen.getByRole("button", { name: "全部检测" }), within(paneBlock("位置")).getByRole("button", { name: "检测" })]) {
      expect(button.getAttribute("aria-disabled")).toBe("true");
      await user.click(button);
    }
    expect(f.command).toHaveBeenCalledTimes(1);
    await act(async () => { finish({ harnesses: harnesses() }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "全部检测" }).getAttribute("aria-disabled")).toBeNull());
  });
});

describe("the path dialog", () => {
  async function openPath(user: ReturnType<typeof userEvent.setup>, name: string) {
    await selectHarness(user, name);
    await user.click(within(paneBlock("位置")).getByRole("button", { name: "指定路径" }));
    return await screen.findByRole("dialog");
  }

  it("refuses a relative path in the dialog and sends nothing", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    expect((await within(dialog).findByRole("alert")).textContent).toBe("请输入可执行文件的绝对路径。");
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("takes a Windows drive path as absolute", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "C:\\tools\\zcode.exe");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    await waitFor(() => expect(f.calls).toHaveLength(1));
    expect(f.calls[0].params.path).toBe("C:\\tools\\zcode.exe");
  });

  it("saves an absolute path with the recorded revision, closes, and applies the returned row", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    await screen.findByText("已保存路径 /opt/tools/zcode；ZCode 已找到");
    expect(f.calls).toEqual([{
      operation: "harness_set",
      params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 1 },
    }]);
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(fieldValue(paneBlock("位置"), "路径").textContent).toBe("/opt/tools/zcode"));
    expect(fieldValue(paneBlock("位置"), "来源").textContent).toBe("手动");
    expect(harnessCells("ZCode")[1].textContent).toBe("已找到");
  });

  it("clears the stored path back to automatic detection with null", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "Claude Code");
    await user.click(within(dialog).getByRole("button", { name: "恢复自动检测" }));
    await screen.findByText("已恢复自动检测；Claude Code 未找到");
    expect(f.calls).toEqual([{
      operation: "harness_set",
      params: { adapter: "claude", path: null, expectedRevision: 5 },
    }]);
    // Automatic detection still fails here; the row says so, not a stale path.
    await waitFor(() => expect(harnessCells("Claude Code")[1].textContent).toBe("未找到"));
    expect(fieldValue(paneBlock("位置"), "路径").textContent).toBe("未记录");
  });

  it("closes without sending anything when it is cancelled", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("reports a revision conflict, rereads the record and retries against the new revision", async () => {
    const user = userEvent.setup();
    const f = fixture({ conflictOnce: true });
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    await screen.findByText("记录已更新；请刷新核对后重试。");
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已保存路径 /opt/tools/zcode；ZCode 已找到");
    expect(f.calls).toEqual([
      { operation: "harness_set", params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 1 } },
      { operation: "harness_set", params: { adapter: "zcode", path: "/opt/tools/zcode", expectedRevision: 4 } },
    ]);
  });
});

describe("a session that cannot write", () => {
  it("leaves every action inert, says why, and sends nothing", async () => {
    const user = userEvent.setup();
    const f = fixture({ readOnly: true });
    await openHarness(f);
    const buttons = [screen.getByRole("button", { name: "全部检测" }), within(paneBlock("位置")).getByRole("button", { name: "检测" })];
    for (const button of buttons) {
      expect(button.getAttribute("aria-disabled")).toBe("true");
      expect(button.hasAttribute("disabled")).toBe(false);
      expect(button.getAttribute("aria-description")).toBe(LOGIN_EXPIRED_ACTION_REFUSAL);
    }
    await user.click(buttons[0]);
    expect((await screen.findByRole("status", { name: "" })).textContent).toBe(LOGIN_EXPIRED_ACTION_REFUSAL);
    await user.click(buttons[1]);
    expect(f.command).not.toHaveBeenCalled();
    // The dialogs open to be read, and their actions are inert too.
    await user.click(within(paneBlock("位置")).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "保存并检测" }).getAttribute("aria-disabled")).toBe("true");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });

  it("says a lost connection in its own words", async () => {
    const user = userEvent.setup();
    const f = fixture();
    render(<TooltipProvider><HarnessView snapshot={snapshot()} api={f.api} refresh={async () => null} active mutationsAvailable={false} sessionWritable
      location={{ view: "harness" }} attention={{ roles: null, quota: [] }} onNavigate={() => {}} hidden={false} /></TooltipProvider>);
    const button = screen.getByRole("button", { name: "全部检测" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-description")).toBe("连接中断或缺少写入资格");
    await user.click(button);
    expect((await screen.findByRole("status", { name: "" })).textContent).toBe("连接中断或缺少写入资格");
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("entering the view", () => {
  it("makes no request of its own and keeps no draft", async () => {
    const f = fixture();
    await openHarness(f);
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByText(/未保存/)).toBeNull();
  });

  it("is reached from the models table, and opening it calls nothing and makes no draft", async () => {
    const user = userEvent.setup();
    const f = fixture();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("region", { name: "模型" });
    await user.click(within(screen.getByRole("navigation", { name: "目录的内容" })).getByRole("link", { name: /^Harness/ }));
    await screen.findByRole("table", { name: "Harness" });
    expect(harnessCells("DSH")[1].textContent).toBe("已找到");
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
  });
});

describe("the account block", () => {
  const account = { adapter: "dsh", source: "native", revision: 4, credentialRevision: 1, status: "ready", accountType: "chatgpt",
    checkedAt: "2026-09-29T09:00:00.000Z", reasonCode: null, guidance: null,
    capabilities: { workerAccount: true, oauth: true, apiKey: true, logout: true, remove: true } };
  const billing = { openai: { kind: "subscription", source: "native", observedAt: CHECKED_AT } };
  const withAccount = (extra: object = {}) => [
    harness({ adapter: "dsh", status: "ready", executable: "/usr/local/bin/dsh", version: "0.4.2", checkedAt: CHECKED_AT,
      account: { ...account, ...extra } as never, billingByProvider: billing as never }),
    harness({ adapter: "zcode", status: "ready", executable: "/opt/bin/zcode", version: "0.4.1", checkedAt: CHECKED_AT }),
  ];

  it("writes the source on its head, then its status, the billing of each provider and the guidance", async () => {
    await openHarness(fixture({ harnesses: withAccount({ status: "logged-out", accountType: null, checkedAt: null,
      reasonCode: "ACCOUNT_LOGIN_REQUIRED", guidance: "log in" }) }));
    const block = paneBlock("账户");
    expect(block.firstElementChild!.textContent).toBe("账户共用登录");
    expect(fieldValue(block, "状态").textContent).toBe("未登录");
    expect(fieldValue(block, "计费").textContent).toBe("openai 订阅");
    expect(within(block).getByText("完成当前来源的登录后检测。")).toBeTruthy();
    expect(within(block).getByRole("button", { name: "管理账户" })).toBeTruthy();
    expect(harnessCells("DSH")[3].textContent).toBe("共用登录未登录");
  });

  it("adds the account's type and when it was checked to its status", async () => {
    await openHarness(fixture({ harnesses: withAccount() }));
    expect(fieldValue(paneBlock("账户"), "状态").textContent).toBe(`已配置 · ChatGPT · ${clock("2026-09-29T09:00:00.000Z")}`);
    expect(harnessCells("DSH")[3].textContent).toBe("共用登录");
  });

  it("writes only that nothing is recorded where there is no account record, and offers nothing to manage", async () => {
    const user = userEvent.setup();
    await openHarness(fixture({ harnesses: withAccount() }));
    await selectHarness(user, "ZCode");
    expect(paneBlock("账户").textContent).toBe("账户未记录");
    expect(within(paneBlock("账户")).queryByRole("button")).toBeNull();
    expect(harnessCells("ZCode")[3].textContent).toBe("—");
  });

  it("changes the source in the dialog, and the table and the column follow the record read again", async () => {
    const user = userEvent.setup();
    const rows = withAccount();
    const f = fixture({ harnesses: rows, replies: { account_set: params => {
      rows[0] = { ...rows[0], account: { ...account, source: params.source, revision: params.expectedRevision + 1 } as never };
      return {};
    } } });
    await openHarness(f);
    await user.click(within(paneBlock("账户")).getByRole("button", { name: "管理账户" }));
    const dialog = await screen.findByRole("dialog", { name: "DSH 账户" });
    await user.click(within(dialog).getByRole("radio", { name: "独立账户" }));
    expect(f.calls).toEqual([{ operation: "account_set", params: { adapter: "dsh", expectedRevision: 4, source: "worker" } }]);
    await waitFor(() => expect(within(dialog).getByRole("radio", { name: "独立账户" }).getAttribute("aria-checked")).toBe("true"));
    // The separate account's own controls appear, and the column behind the dialog is already updated.
    expect(within(dialog).getByRole("button", { name: "登录" })).toBeTruthy();
    await user.click(within(dialog).getByRole("button", { name: "关闭" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(paneBlock("账户").firstElementChild!.textContent).toBe("账户独立账户");
    expect(harnessCells("DSH")[3].textContent).toBe("独立账户");
  });

  it("opens the dialog to be read in a session that cannot write, with every change inert", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: withAccount({ source: "worker" }), readOnly: true });
    await openHarness(f);
    await user.click(within(paneBlock("账户")).getByRole("button", { name: "管理账户" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "登录" }).getAttribute("aria-disabled")).toBe("true");
    await user.click(within(dialog).getByRole("radio", { name: "共用登录" }));
    await user.click(within(dialog).getByRole("button", { name: "登录" }));
    expect(f.command).not.toHaveBeenCalled();
  });
});
