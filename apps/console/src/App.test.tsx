import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Snapshot } from "./types";
import { modelFacts } from "./buddies/model-fixtures";
import { levelSwitch, listed, pane, selectModel } from "./buddies/page.testing";

const FLASH = "dsh / deepseek-official / deepseek-flash";

const initial = (): Snapshot => { const snapshot: Snapshot = ({
  csrfToken: "fixture-csrf", runtimeContentId: "fixture-runtime", eventCursor: 0,
  consoleSession: { id: "fixture-session" },

  configuration: { explorationEvery: 5, routerBuddyIds: ["flash-off"], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, modelResponseWaitSeconds: 60, profileBuddyId: null,
  },
  buddies: [
    { effortRank: null,
      buddyId: "flash-off",
      label: "Flash 决策",
      harness: "dsh",
      provider: "deepseek-official",
      model: "deepseek-flash",
      effort: "off",
      available: true,
      enabled: true,
      capabilities: ["text"],
      contextWindow: 1000000,
      source: "fixture",
      description: "",
    },
  ],

  preferences: [],
      models: [], modelCorrespondences: [],

  decisions: [],

  modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 0 }],
  tasks: { pendingCount: 0 },
  capabilities: { selection: false },
}); return { ...snapshot, ...modelFacts(snapshot.buddies) }; };

const emptyMacroTasks = () => ({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 0, changed: false });

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("console interactions", () => {
  it("keeps a local Buddy setting across pages without sending a write", async () => {
    const state = initial();
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(), task: vi.fn(), macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "还没有宏任务" });
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /^Buddy/ }));
    await listed(1, 1);
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await selectModel(user, FLASH, "DSH");
    await user.click(levelSwitch("off"));
    expect(screen.getByRole("region", { name: "未保存的修改" })).toBeTruthy();
    await user.click(screen.getByRole("link", { name: "设置" }));
    expect(screen.queryByRole("region", { name: "未保存的修改" })).toBeNull();
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /Buddy/ }));
    expect(levelSwitch("off").getAttribute("aria-checked")).toBe("false");
    expect(api.command).not.toHaveBeenCalled();
  });

  it("addresses each page by its own name and opens the tasks for any other hash", async () => {
    const state = initial();
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command: vi.fn(),
      task: vi.fn(),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    const first = render(<App suppliedApi={api} />);
    await listed(1, 1);
    expect(screen.getByRole("heading", { name: "Buddy" })).toBeTruthy();
    first.unmount();
    // A retired bookmark is a hash like any other the console does not know.
    window.location.hash = "#models";
    const second = render(<App suppliedApi={api} />);
    expect((await screen.findByRole("link", { name: "任务" })).getAttribute("aria-current")).toBe("page");
    second.unmount();
    window.location.hash = "#settings";
    render(<App suppliedApi={api} />);
    await screen.findByRole("heading", { name: "主题" });
    await user.click(within(screen.getByRole("navigation", { name: "主要导航" })).getByRole("link", { name: /^Buddy/ }));
    await listed(1, 1);
    await user.click(screen.getByRole("link", { name: "设置" }));
    await screen.findByRole("heading", { name: "主题" });
    expect(window.location.hash).toBe("#settings");
    expect(api.command).not.toHaveBeenCalled();
  });

  it("opens evaluation from the buddy's sample count without a write", async () => {
    const state = initial();
    const command = vi.fn(async (operation: string) => {
      throw new Error(`Unexpected command: ${operation}`);
    });
    const api = {
      runtimeVersion: vi.fn(async () => ({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })),
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={api} />);
    await listed(1, 1);
    await selectModel(user, FLASH, "DSH");
    expect(screen.queryByRole("button", { name: "记录待整理观察" })).toBeNull();
    expect(screen.queryByRole("button", { name: "评价维护" })).toBeNull();
    // The effort selector stays local; the sample count carries a stable evaluation address.
    expect(within(pane()).getAllByRole("button").filter(button => button.getAttribute("aria-label") !== null).map(button => button.getAttribute("aria-label"))).toEqual(["off"]);
    expect(within(pane()).queryByRole("button", { name: /评价与领域经验/ })).toBeNull();
    const samples = within(pane()).getByRole("link", { name: "0" });
    expect(samples.getAttribute("href")).toBe("#buddies/evaluation/dsh/deepseek-flash/off");
    await user.click(samples);
    expect(window.location.hash).toBe(samples.getAttribute("href"));
    expect(screen.getByRole("region", { name: "评价" })).toBeTruthy();
    expect(command).not.toHaveBeenCalled();
  });

  // D2-A1/A2: a hidden bootstrap leaves startup or shows the existing error UI.
  it.each([false, true])("finishes the hidden bootstrap through the existing App presentation (failure=%s)", async failure => {
    vi.spyOn(document, "visibilityState", "get").mockReturnValue("hidden");
    const api = {
      snapshot: failure ? vi.fn(async () => { throw new Error("fixture offline"); }) : vi.fn(async () => initial()),
      command: vi.fn(),
      macroTasks: vi.fn(async () => emptyMacroTasks()),
    } as unknown as ConsoleApi;
    render(<App suppliedApi={api} />);
    if (failure) {
      expect(await screen.findByRole("heading", { name: "暂时无法连接黑板" })).toBeTruthy();
      expect(screen.getByRole("alert").textContent).toBe("fixture offline");
      expect(screen.getByRole("button", { name: "重新连接" })).toBeTruthy();
      expect(screen.queryByRole("navigation", { name: "主要导航" })).toBeNull();
    } else {
      expect(await screen.findByRole("navigation", { name: "主要导航" })).toBeTruthy();
      expect(screen.queryByRole("alert")).toBeNull();
    }
    expect(screen.queryByRole("heading", { name: "正在连接本地黑板" })).toBeNull();
    expect(screen.queryByText("正在连接…")).toBeNull();
    expect(api.snapshot).toHaveBeenCalledTimes(1);
    expect(api.command).not.toHaveBeenCalled();
  });
});
