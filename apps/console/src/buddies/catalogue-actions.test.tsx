import { reasonText } from "../i18n/reason";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CatalogView } from "./CatalogView";
import { useHarnessActions } from "./use-harness-actions";
import { useEditor } from "../use-editor";
import { LOGIN_EXPIRED_ACTION_REFUSAL } from "../console-session";
import { CHECKED_AT, fixture, harness, harnesses, openHarness, snapshot } from "./harness.fixtures";
import {
  fieldValue, harnessPane, harnessSection, harnessTitle, paneBlock, toolbarAlert,
} from "./harness.testing";

/**
 * ADR-017 §15 on the Buddy page's harness sections: each harness shows its state
 * and cooldown summary in its head. Its sidebar card shows path, version,
 * source, last check, attempted locations, remedy and account.
 * Checking is an explicit click that goes through the existing
 * `capabilities` operation with `refresh: true` and never calls a model; saving
 * a path goes through `harness_set`.
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

describe("the harness sections", () => {
  it("shows one wide card per recorded harness with status and account groups", async () => {
    const f = fixture();
    await openHarness(f, "#buddies/harness");
    expect(document.querySelectorAll("[data-slot=harness-list] [data-slot=harness-card]")).toHaveLength(4);
    expect(document.querySelectorAll("[data-slot=harness-details]")).toHaveLength(4);
    expect(document.querySelectorAll("[data-slot=harness-card] [data-slot=harness-version]")[0].textContent).toBe("0.4.2");
    for (const [name, state] of [["ZCode", "未找到"], ["Codex", "未登录"], ["Claude Code", "未通过"]]) {
      expect(within(harnessSection(name)).getByText(state)).toBeTruthy();
    }
    expect(harnessSection("Codex").querySelector("[data-slot=harness-version]")?.textContent).toBe("0.157.0");
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
  });

  it("retains harness details when switching to and from Models", async () => {
    const user = userEvent.setup();
    await openHarness(fixture(), "#buddies/harness");
    expect(harnessPane("DSH")).toBeTruthy();
    expect(harnessPane("Codex")).toBeTruthy();
    await user.click(screen.getByRole("link", { name: "模型" }));
    await screen.findByRole("region", { name: "模型" });
    await user.click(screen.getByRole("link", { name: "Harness" }));
    expect(await screen.findByRole("region", { name: "Harness" })).toBeTruthy();
    expect(harnessPane("DSH")).toBeTruthy();
  });

  it("keeps check feedback below the Harness toolbar", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    await user.click(within(harnessSection("DSH")).getByRole("button", { name: "检测" }));
    const feedback = await screen.findByText("已检测 DSH：已找到");
    const line = feedback.closest("[data-slot=harness-feedback]")!;
    expect(line.className).not.toContain("hidden");
    expect(document.querySelector("[data-slot=harness-toolbar]")).toBeTruthy();
    await user.click(screen.getByRole("link", { name: "模型" }));
    await screen.findByRole("region", { name: "模型" });
    expect(document.querySelector("[data-slot=harness-feedback]")).toBeNull();
    await user.click(screen.getByRole("link", { name: "Harness" }));
    await screen.findByRole("region", { name: "Harness" });
    expect(await screen.findByText("已检测 DSH：已找到")).toBeTruthy();
  });
});

describe("models without harness observations", () => {
  it("retains the model catalogue and invents no harness account or health", async () => {
    const f = fixture({ harnesses: [] });
    await openHarness(f, "#buddies/models");
    expect(screen.getByRole("region", { name: "模型" })).toBeTruthy();
    expect(document.querySelectorAll("[data-slot=harness-details]")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "全部检测" })).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("the location block", () => {
  it("names a found harness's path, source and last check, and offers only the two actions", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const location = paneBlock("位置");
    expect(harnessSection("DSH").querySelector("[data-slot=harness-version]")?.textContent).toBe("0.4.2");
    expect(fieldValue(location, "路径").textContent).toBe("/usr/local/bin/dsh");
    expect(location.querySelector("[data-slot=harness-source]")?.textContent).toContain("版本管理器（nvm 默认版本）");
    expect(screen.getByText(`检测于 ${clock(CHECKED_AT)}`)).toBeTruthy();
    expect(within(harnessSection("DSH")).getByText("已找到")).toBeTruthy();
    // Nothing exceptional is said of a harness that is found.
    for (const word of ["原因", "已尝试的位置"]) expect(within(location).queryByText(word)).toBeNull();
    expect(within(harnessPane()).queryByText(/请先|(?<!已)安装/)).toBeNull();
    // The path can always be set, found or not.
    expect(within(harnessSection("DSH")).getByRole("button", { name: "检测" })).toBeTruthy();
    await user.click(within(harnessSection("DSH")).getByRole("button", { name: "指定路径" }));
    expect(await screen.findByRole("dialog", { name: "DSH 路径" })).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("says why a harness is not found, what to do and where it looked", async () => {
    const f = fixture();
    await openHarness(f);
    const location = paneBlock("位置", "ZCode");
    expect(within(harnessSection("ZCode")).getByText("未找到")).toBeTruthy();
    expect(fieldValue(location, "原因").textContent).toBe(reasonText({ code: "harness.not-found", params: { operation: "probe" } }));
    expect(within(harnessSection("ZCode")).getByText("未找到").getAttribute("aria-description")).toBe(reasonText({ code: "harness.not-found", params: { operation: "probe" } }));
    expect(fieldValue(location, "路径").textContent).toBe("未记录");
    const tried = within(location).getByText("已尝试的位置").parentElement!;
    expect([...tried.querySelectorAll("[data-slot=harness-path]")].map(path => path.textContent)).toEqual(["/Applications/ZCode.app/Contents/MacOS/zcode", "/opt/homebrew/bin/zcode"]);
    expect(within(tried).getByText("应用包 · " + reasonText({ code: "harness.not-found", params: { operation: "probe" } }))).toBeTruthy();
    expect(within(tried).getByText("Homebrew · 未找到")).toBeTruthy();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("says a manually stored path is manual and offers to restore detection", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    const location = paneBlock("位置", "Claude Code");
    expect(fieldValue(location, "路径").textContent).toBe("/Users/fixture/.claude/local/claude");
    expect(fieldValue(location, "来源").textContent).toContain("手动");
    await user.click(within(harnessSection("Claude Code")).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("textbox", { name: "可执行文件路径" })).toHaveProperty("value", "/Users/fixture/.claude/local/claude");
    expect(within(dialog).getByRole("button", { name: "恢复自动检测" })).toBeTruthy();
  });

  it("offers no restore where no path is stored", async () => {
    const user = userEvent.setup();
    await openHarness(fixture());
    await user.click(within(harnessSection("DSH")).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).queryByRole("button", { name: "恢复自动检测" })).toBeNull();
    expect(within(dialog).getByRole("textbox", { name: "可执行文件路径" })).toHaveProperty("value", "");
  });
});

describe("opening a harness by its address", () => {
  it("focuses the addressed harness title in the sidebar", async () => {
    await openHarness(fixture(), "#buddies/harness/codex");
    expect(harnessPane("Codex")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(harnessTitle("codex")));
    expect(harnessTitle("codex").closest("[data-slot=harness-card]")).toBeTruthy();
    expect(screen.queryByRole("navigation", { name: "模型的内容" })).toBeNull();
  });

  it("opens and focuses the addressed harness card", async () => {
    await openHarness(fixture(), "#buddies/harness/dsh");
    await waitFor(() => expect(document.activeElement).toBe(harnessTitle("dsh")));
    expect(harnessSection("DSH").getAttribute("data-slot")).toBe("harness-card");
  });

  it("keeps every harness card visible when the address names no harness", async () => {
    await openHarness(fixture(), "#buddies/harness/unknown");
    expect(document.querySelectorAll("[data-slot=harness-details]")).toHaveLength(4);
  });
});

describe("checking", () => {
  it("checks one harness through the existing capabilities operation and reloads the snapshot", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const reads = f.snapshotMock.mock.calls.length;
    await user.click(within(harnessSection("Codex")).getByRole("button", { name: "检测" }));
    await screen.findByText("已检测 Codex：已找到");
    // No model call and no new path: one named harness through /api/command.
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true, harness: "codex" } }]);
    await waitFor(() => expect(f.snapshotMock.mock.calls.length).toBeGreaterThan(reads));
    await waitFor(() => expect(fieldValue(paneBlock("位置", "Codex"), "路径").textContent).toBe("/opt/bin/codex"));
    expect(harnessSection("Codex").querySelector("[data-slot=harness-version]")?.textContent).toBe("9.9.9");
    // The others were not checked.
    expect(within(harnessSection("ZCode")).getByText("未找到")).toBeTruthy();
  });

  it("checks every harness when no one is named", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    await screen.findByText("已检测全部 harness：已找到 4 / 4");
    expect(f.calls).toEqual([{ operation: "capabilities", params: { refresh: true } }]);
    for (const name of ["DSH", "ZCode", "Codex", "Claude Code"]) expect(within(harnessSection(name)).queryByText(/未找到|未登录|未通过/)).toBeNull();
  });

  it("keeps a failed check retryable, and reports success only after a real reply", async () => {
    const user = userEvent.setup();
    const f = fixture({ failFirstCheck: true });
    await openHarness(f);
    await user.click(screen.getByRole("button", { name: "全部检测" }));
    await toolbarAlert();
    expect(screen.getByText("无法连接本地黑板。已有任务仍由后台管理。")).toBeTruthy();
    // The recorded state is untouched by the failure.
    expect(within(harnessSection("ZCode")).getByText("未找到")).toBeTruthy();
    expect(screen.queryByText(/已检测/)).toBeNull();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已检测全部 harness：已找到 4 / 4");
    expect(f.calls.map(call => call.operation)).toEqual(["capabilities", "capabilities"]);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("retries the same action: a failed check of one harness is checked again for that harness", async () => {
    const user = userEvent.setup();
    const f = fixture({ failFirstCheck: true });
    await openHarness(f);
    await user.click(within(harnessSection("ZCode")).getByRole("button", { name: "检测" }));
    await toolbarAlert();
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已检测 ZCode：已找到");
    expect(f.calls.map(call => call.params)).toEqual([{ refresh: true, harness: "zcode" }, { refresh: true, harness: "zcode" }]);
  });

  it("lets no second action start while one is under way", async () => {
    const user = userEvent.setup();
    let finish!: (value: unknown) => void;
    const f = fixture({ replies: { capabilities: () => new Promise(resolve => { finish = resolve; }) } });
    await openHarness(f);
    const checkAll = screen.getByRole("button", { name: "全部检测" });
    await user.click(checkAll);
    await waitFor(() => expect(f.command).toHaveBeenCalledTimes(1));
    for (const button of [screen.getByRole("button", { name: "全部检测" }), within(harnessSection("DSH")).getByRole("button", { name: "检测" })]) {
      expect(button.getAttribute("aria-disabled")).toBe("true");
      await user.click(button);
    }
    expect(f.command).toHaveBeenCalledTimes(1);
    // The toolbar button says why, and is still the button that was clicked.
    expect(screen.getByRole("button", { name: "全部检测" })).toBe(checkAll);
    expect(checkAll.getAttribute("aria-description")).toBe("检测进行中");
    await act(async () => { finish({ harnesses: harnesses() }); });
    await waitFor(() => expect(screen.getByRole("button", { name: "全部检测" }).getAttribute("aria-disabled")).toBeNull());
    expect(checkAll.getAttribute("aria-description")).toBeNull();
  });
});

describe("the path dialog", () => {
  async function openPath(user: ReturnType<typeof userEvent.setup>, name: string) {
    await user.click(within(harnessSection(name)).getByRole("button", { name: "指定路径" }));
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

  it("saves an absolute path without a revision, closes, and applies the returned row", async () => {
    const user = userEvent.setup();
    const f = fixture();
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    await screen.findByText("已保存路径 /opt/tools/zcode；ZCode 已找到");
    expect(f.calls).toEqual([{
      operation: "harness_set",
      params: { harness: "zcode", path: "/opt/tools/zcode" },
    }]);
    expect(screen.queryByRole("dialog")).toBeNull();
    await waitFor(() => expect(fieldValue(paneBlock("位置", "ZCode"), "路径").textContent).toBe("/opt/tools/zcode"));
    expect(fieldValue(paneBlock("位置", "ZCode"), "来源").textContent).toContain("手动");
    expect(within(harnessSection("ZCode")).getByText("已找到")).toBeTruthy();
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
      params: { harness: "claude", path: null },
    }]);
    // Automatic detection still fails here; the row says so, not a stale path.
    await waitFor(() => expect(within(harnessSection("Claude Code")).getByText("未找到")).toBeTruthy());
    expect(fieldValue(paneBlock("位置", "Claude Code"), "路径").textContent).toBe("未记录");
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

  it("reports a failed save and retries the same path", async () => {
    const user = userEvent.setup();
    const f = fixture({ failFirstSet: true });
    await openHarness(f);
    const dialog = await openPath(user, "ZCode");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    await screen.findByText("无法连接本地黑板。已有任务仍由后台管理。");
    await user.click(screen.getByRole("button", { name: "重试" }));
    await screen.findByText("已保存路径 /opt/tools/zcode；ZCode 已找到");
    expect(f.calls).toEqual([
      { operation: "harness_set", params: { harness: "zcode", path: "/opt/tools/zcode" } },
      { operation: "harness_set", params: { harness: "zcode", path: "/opt/tools/zcode" } },
    ]);
  });
});

describe("a session that cannot write", () => {
  it("leaves every action inert, says why, and sends nothing", async () => {
    const user = userEvent.setup();
    const f = fixture({ missingSession: true });
    await openHarness(f);
    const buttons = [screen.getByRole("button", { name: "全部检测" }), within(harnessSection("DSH")).getByRole("button", { name: "检测" })];
    for (const button of buttons) {
      expect(button.getAttribute("aria-disabled")).toBe("true");
      expect(button.hasAttribute("disabled")).toBe(false);
      expect(button.getAttribute("aria-description")).toBe(LOGIN_EXPIRED_ACTION_REFUSAL());
    }
    await user.click(buttons[0]);
    expect(await screen.findByText(LOGIN_EXPIRED_ACTION_REFUSAL())).toBeTruthy();
    await user.click(buttons[1]);
    expect(f.command).not.toHaveBeenCalled();
    // The dialogs open to be read, and their actions are inert too.
    await user.click(within(harnessSection("DSH")).getByRole("button", { name: "指定路径" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByRole("button", { name: "保存并检测" }).getAttribute("aria-disabled")).toBe("true");
    await user.type(within(dialog).getByRole("textbox", { name: "可执行文件路径" }), "/opt/tools/zcode");
    await user.click(within(dialog).getByRole("button", { name: "保存并检测" }));
    expect(f.command).not.toHaveBeenCalled();
    expect(screen.getByRole("dialog")).toBeTruthy();
  });


});

describe("a disconnected session", () => {
  it("says a lost connection in its own words", async () => {
    const user = userEvent.setup();
    const f = fixture();
    const state = snapshot();
    function View() {
      const editor = useEditor(f.api, state, async () => null, false, "连接中断或缺少写入资格");
      const actions = useHarnessActions({ snapshot: state, api: f.api, refresh: async () => null, mutationsAvailable: false, sessionWritable: editor.sessionWritable });
      return <TooltipProvider><CatalogView snapshot={state} editor={editor} api={f.api} refresh={async () => null} active
        mutationsAvailable={false} location={{ view: "models" }} onNavigate={() => {}} hidden={false} actions={actions} /></TooltipProvider>;
    }
    render(<View />);
    const button = screen.getByRole("button", { name: "发现模型" });
    expect(button.getAttribute("aria-disabled")).toBe("true");
    expect(button.getAttribute("aria-description")).toBe("连接中断或没有写入资格，暂时不能发现模型；草稿仍保留。");
    await user.click(button);
    expect(await screen.findByText("连接中断或没有写入资格，暂时不能发现模型；草稿仍保留。")).toBeTruthy();
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

  it("draws an account and a model's drawer only while the view is in front, and brings them back with it", async () => {
    const user = userEvent.setup();
    const f = fixture({ harnesses: [harness({ harness: "claude", status: "ready" })] });
    const state = snapshot([harness({ harness: "claude", status: "ready" })]);
    function View({ active }: { active: boolean }) {
      const editor = useEditor(f.api, state, async () => null, true, "");
      const actions = useHarnessActions({ snapshot: state, api: f.api, refresh: async () => null, mutationsAvailable: true, sessionWritable: editor.sessionWritable });
      return <TooltipProvider><CatalogView snapshot={state} editor={editor} api={f.api} refresh={async () => null} active={active}
        mutationsAvailable location={{ view: "models" }} onNavigate={() => {}} hidden={!active} actions={actions} /></TooltipProvider>;
    }
    const { rerender } = render(<View active />);
    await user.click(screen.getByRole("button", { name: "claude / anthropic / claude-sonnet-5" }));
    expect(document.querySelector("[data-slot=harness-details]")).toBeNull();
    expect(document.querySelector("[data-slot=model-drawer]")).toBeTruthy();
    rerender(<View active={false} />);
    expect(document.querySelector("[data-slot=harness-details]")).toBeNull();
    expect(document.querySelector("[data-slot=model-drawer]")).toBeNull();
    rerender(<View active />);
    expect(document.querySelector("[data-slot=harness-details]")).toBeNull();
    expect(document.querySelector("[data-slot=model-drawer]")).toBeTruthy();
  });

  it("keeps harness settings in the dedicated section", async () => {
    await openHarness(fixture({ harnesses: [harness({ harness: "claude", status: "unknown" })] }));
    expect(document.querySelector("[data-slot=harness-details]")).toBeTruthy();
  });
});

describe("the account block", () => {
  const account = { harness: "dsh", status: "ready", accountType: "chatgpt",
    checkedAt: "2026-09-29T09:00:00.000Z", reason: null };
  const billing = { openai: { kind: "subscription", source: "native", observedAt: CHECKED_AT } };
  const withAccount = (extra: object = {}) => [
    harness({ harness: "dsh", status: "ready", executable: "/usr/local/bin/dsh", version: "0.4.2", checkedAt: CHECKED_AT,
      account: { ...account, ...extra } as never, billingByProvider: billing as never }),
    harness({ harness: "zcode", status: "ready", executable: "/opt/bin/zcode", version: "0.4.1", checkedAt: CHECKED_AT }),
  ];

  it("writes its status, the billing of each provider and the guidance", async () => {
    await openHarness(fixture({ harnesses: withAccount({ status: "logged-out", accountType: null, checkedAt: null,
      reason: { code: "account.login-required", params: {} } }) }));
    const block = paneBlock("账户");
    expect(block.textContent).toContain("状态");
    expect(fieldValue(block, "状态").textContent).toBe("未登录");
    expect(fieldValue(block, "计费").textContent).toBe("openai 订阅");
    expect(within(block).getByText("未登录").getAttribute("aria-description")).toBe(reasonText({ code: "account.login-required", params: {} }));
    expect(within(block).queryByRole("button")).toBeNull();
  });

  it("adds the account's type and when it was checked to its status", async () => {
    await openHarness(fixture({ harnesses: withAccount() }));
    expect(fieldValue(paneBlock("账户"), "状态").textContent).toBe("已配置 · ChatGPT");
  });

  it("writes only that nothing is recorded where there is no account record, and offers nothing to manage", async () => {
    await openHarness(fixture({ harnesses: withAccount() }));
    expect(paneBlock("账户", "ZCode").textContent).toBe("状态未记录计费未知");
    expect(within(paneBlock("账户", "ZCode")).queryByRole("button")).toBeNull();
    expect(harnessSection("ZCode").querySelector("[data-slot=cooling-status]")).toBeNull();
  });

  it("keeps native login facts readable in a session that cannot write", async () => {
    const f = fixture({ harnesses: withAccount(), missingSession: true });
    await openHarness(f);
    expect(fieldValue(paneBlock("账户"), "状态").textContent).toContain("ChatGPT");
    expect(within(paneBlock("账户")).queryByRole("button")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});
