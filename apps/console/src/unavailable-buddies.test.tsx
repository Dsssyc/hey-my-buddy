import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { ModelServiceNote, Preference, Buddy, BuddyPage, Snapshot } from "./types";
import {
  chip, choosePreference, effortCell, level, levelPreference, levelSwitch, listed, modelButton, modelRow, note, pane, queryModel, search, selectModel,
} from "./buddies/page.testing";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const retiredLow = "dsh:deepseek-official:retired-model:low";
const retiredModelService = { harness: "dsh", provider: "deepseek-official", model: "retired-model" };
const FLASH = "dsh / deepseek-official / deepseek-flash";
const RETIRED = "dsh / deepseek-official / retired-model";

function catalog(): Buddy[] {
  const base = {
    harness: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, buddyId: flashOff, model: "deepseek-flash", effort: "off", label: "deepseek-flash · off", available: true, enabled: true },
    { ...base, buddyId: flashHigh, model: "deepseek-flash", effort: "high", label: "deepseek-flash · high", available: true, enabled: false },
    { ...base, buddyId: retiredMax, model: "retired-model", effort: "max", label: "retired-model · max",
      available: false, enabled: true, unavailableReason: "provider paused" },
    { ...base, buddyId: retiredLow, model: "retired-model", effort: "low", label: "retired-model · low",
      available: false, enabled: false, unavailableReason: "provider paused" },
  ];
}

/**
 * Mirrors the recorded snapshot: available buddies plus the current
 * decision buddy. Retired identities are only reachable through the paged
 * `buddy_list` history, exactly like the board exposes them.
 */
function fixture(options: { staleDecision?: boolean; stalePin?: boolean; pageSize?: number } = {}) {
  const buddies = catalog();
  const decisionId = options.staleDecision ? retiredMax : flashOff;
  const modelServiceNotes: ModelServiceNote[] = [{ ...retiredModelService, text: "用户保留的历史意见", updatedAt: null }];
  const preferences: Preference[] = options.stalePin
    ? [{ buddyId: retiredMax, mode: "pin" as const, reason: "以前固定" }]
    : [];

  const pages = (params: Record<string, any>): BuddyPage => {
    const includeUnavailable = params.includeUnavailable === true;
    const visible = includeUnavailable ? buddies : buddies.filter(p => p.available || p.buddyId === decisionId);
    const limit = typeof params.limit === "number" ? params.limit : 100;
    const after = typeof params.after === "string" ? params.after : "";
    const ordered = [...visible].sort((a, b) => a.buddyId.localeCompare(b.buddyId)).filter(p => p.buddyId > after);
    const slice = ordered.slice(0, limit);
    const ids = new Set(slice.map(p => p.buddyId));
    return {
      buddies: slice,

      modelServiceNotes: ids.has(retiredMax) || ids.has(retiredLow) ? modelServiceNotes : [],
      preferences: preferences.filter(p => ids.has(p.buddyId)),

      modelServiceConcurrency: [],
      nextCursor: ordered.length > slice.length ? slice[slice.length - 1].buddyId : null,
    };
  };
  const live = buddies.filter(p => p.available || p.buddyId === decisionId);
  let state: Snapshot = {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [decisionId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies: live,
    // `unavailableBuddyCount` counts every unavailable row in the table,
    // including a listed one such as the retained decision buddy; the console
    // subtracts what it has loaded and pages the rest.
    unavailableBuddyCount: buddies.filter(p => !p.available).length,

    modelServiceNotes: live.some(p => p.buddyId === retiredMax || p.buddyId === retiredLow) ? modelServiceNotes : [],
    preferences: preferences.filter(p => live.some(x => x.buddyId === p.buddyId)),
     decisions: [],

    modelServiceConcurrency: [],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
  };

  const operations: string[] = [];
  const published: Record<string, any>[] = [];
  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (operation === "buddy_list") {
      const page = pages({ ...params, limit: options.pageSize ?? params.limit });
      if (options.pageSize) page.nextCursor = page.nextCursor;
      return page;
    }

    if (operation === "user_policy_publish") {
      published.push(params);
      state = { ...state,
 };
      return {};
    }
    if (operation === "model_catalog_refresh") {
      // A completed program discovery publishes directory facts and a new
      // revision; the bounded snapshot still omits retained identities.
      return { discovered: true };
    }

    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, published, operations, snapshot: () => state };
}

const enabledOnly = () => screen.getByRole("checkbox", { name: "只看已启用" });
const showUnavailable = () => screen.getByRole("checkbox", { name: /^显示不可用/ });
const checked = (box: HTMLElement | null) => box?.getAttribute("aria-checked");

/** Shows the unavailable buddies and opens the retired model's settings. */
async function openRetired(user: ReturnType<typeof userEvent.setup>) {
  await listed(1, 2);
  await user.click(showUnavailable());
  await listed(2, 4);
  await selectModel(user, RETIRED, "DSH");
}

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
  document.documentElement.dataset.theme = "";
});

describe("unavailable buddies", () => {
  it("hides them by default and reveals them with the 显示不可用 filter, which says how many there are", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    // The snapshot keeps retired identities as a count, and the unchecked filter
    // hides their model; nothing is fetched until the user asks for them.
    expect(screen.getByRole("checkbox", { name: "显示不可用 2" })).toBeTruthy();
    expect(queryModel(RETIRED)).toBeNull();
    expect(f.operations).not.toContain("buddy_list");

    await user.click(showUnavailable());
    await listed(2, 4);
    expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100 }, "csrf");
    // The retired model joins its harness's rows. Its harness works, so the
    // row says itself that it cannot, with the recorded reason as explanation.
    const state = within(modelRow(RETIRED)).getByText("不可用");
    expect(state.getAttribute("aria-description")).toBe("provider paused");
    expect(chip(RETIRED, "max").textContent).toBe("max");
    expect(within(modelRow(RETIRED)).queryByText("low")).toBeNull();
    expect(within(modelRow(FLASH)).queryByText("不可用")).toBeNull();
    await user.click(modelButton(RETIRED));
    // The side column says it once for the model and keeps the note.
    expect(within(pane(RETIRED)).getByText("不可用").getAttribute("aria-description")).toBe("provider paused");
    expect(within(pane()).getAllByText("不可用")).toHaveLength(1);
    expect(note()).toHaveProperty("value", "用户保留的历史意见");
  });

  it("counts a listed offline Router once and pages the rest in by itself", async () => {
    const f = fixture({ staleDecision: true, pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    // The listed Router is unavailable too, so its model is hidden by default;
    // the count is the table-wide 2, never 3.
    await listed(1, 2);
    expect(queryModel(RETIRED)).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 2" }));
    await listed(2, 4);
    // The snapshot's own row is shown first; the bounded second page then joins
    // it without any manual "load more" control.
    await waitFor(() => expect(f.operations.filter(op => op === "buddy_list")).toHaveLength(2));
    expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100, after: expect.any(String) }, "csrf");
    await user.click(modelButton(RETIRED));
    expect(levelSwitch("max")).toBeTruthy();
    await user.click(effortCell("low"));
    expect(levelSwitch("low")).toBeTruthy();
  });

  it("reads the remaining unavailable pages through the server cursor on its own", async () => {
    const f = fixture({ pageSize: 2 });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 2" }));
    // Both retired buddies are readable and the cursor is exhausted, so the
    // chain of bounded reads stops there.
    await listed(2, 4);
    expect(modelButton(RETIRED)).toBeTruthy();
    await waitFor(() => expect(f.operations.filter(op => op === "buddy_list")).toHaveLength(2));
    expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100, after: expect.any(String) }, "csrf");
    expect(f.operations.filter(op => op === "buddy_list")).toHaveLength(2);
  });

  it("combines 只看已启用 with 显示不可用", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    // 只看已启用 alone still hides the model: none of its available buddies exist.
    await user.click(enabledOnly());
    await listed(1, 2);
    expect(queryModel(RETIRED)).toBeNull();
    // With both boxes on, the retired model qualifies through its enabled
    // buddy (max) and appears; the filters combine.
    await user.click(showUnavailable());
    await listed(2, 4);
    expect(within(modelRow(RETIRED)).getByText("不可用")).toBeTruthy();
    // Turning the unavailable filter off hides the model again.
    await user.click(showUnavailable());
    await listed(1, 2);
    expect(queryModel(RETIRED)).toBeNull();
  });

  it("allows disabling a retired buddy but never re-enabling it", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    // "max" is currently enabled, so the user may turn it off.
    expect(levelSwitch("max").hasAttribute("aria-disabled")).toBe(false);
    await user.click(levelSwitch("max"));
    expect(levelSwitch("max").getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/1 项未保存/)).toBeTruthy();
    // "low" is off and unavailable: it cannot be switched on at all, and says why.
    await user.click(effortCell("low"));
    expect(levelSwitch("low").getAttribute("aria-disabled")).toBe("true");
    expect(levelSwitch("low").getAttribute("aria-description")).toBe("已不可用，不能启用");
    await user.click(levelSwitch("low"));
    expect(levelSwitch("low").getAttribute("aria-checked")).toBe("false");
    expect(screen.getByText(/1 项未保存/)).toBeTruthy();
    // Once "max" is off it is in the same position: off for good until it is discarded.
    await user.click(effortCell("max"));
    expect(levelSwitch("max").getAttribute("aria-disabled")).toBe("true");
    expect(f.operations).not.toContain("user_policy_publish");
  });

  it("publishes a disable and a note as patches without program fields", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    await user.click(levelSwitch("max"));
    await user.type(note(), "，仍可用于历史对照");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.operations.slice(0, 2)).toEqual(["buddy_list", "user_policy_publish"]);
    expect(f.published[0]).toEqual({
      commandId: expect.any(String),
      buddySettings: [{ buddyId: retiredMax, enabled: false }],
      modelServiceNoteChanges: [{ ...retiredModelService, text: "用户保留的历史意见，仍可用于历史对照" }],
    });
    const wire = JSON.stringify(f.published[0]);
    // Availability, provider and model are program-owned and are never uploaded.
    expect(wire).not.toContain("available");
    expect(wire).not.toContain("cards");
  });

  it("allows a reason-only edit of a retired pin while a new pin on an unavailable buddy stays blocked", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    await screen.findByText(/固定选择指向.*请启用或改为/);
    // The existing pin stays a reachable value on its own level; only its reason changes.
    expect(within(levelPreference("max")).getByRole("radio", { name: "固定" }).getAttribute("aria-checked")).toBe("true");
    const reason = within(level("max")).getByRole("textbox", { name: "max 的偏好理由" });
    expect(reason).toHaveProperty("value", "以前固定");
    await user.clear(reason);
    await user.type(reason, "仍然适用");
    // "low" has no pin, is unavailable and off: a new pin there is not offered.
    await user.click(effortCell("low"));
    const pin = within(levelPreference("low")).getByRole("radio", { name: /^固定/ });
    expect(pin.getAttribute("aria-disabled")).toBe("true");
    expect(pin.getAttribute("aria-description")).toBe("这个 buddy 不可用或未启用，不能固定");
    await user.click(pin);
    expect(pin.getAttribute("aria-checked")).toBe("false");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].preferenceChanges).toEqual([
      { buddyId: retiredMax, mode: "pin", reason: "仍然适用" },
    ]);
    expect(f.published[0]).not.toHaveProperty("buddySettings");
  });

  it("keeps a retained disable through discovery and publishes it", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    await user.click(levelSwitch("max"));
    await user.click(screen.getByRole("button", { name: "发现模型" }));
    await screen.findByText("没有新的 buddy");
    expect(levelSwitch("max").getAttribute("aria-checked")).toBe("false");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].buddySettings).toEqual([{ buddyId: retiredMax, enabled: false }]);
  });

  it("does not resurrect a preference the user removed when a page is re-read", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    await choosePreference(user, "max", "不设");
    // A same-revision retained page still contains the published pin. Reading it
    // (here a search-filter reload) must not silently re-add the removed row.
    await user.type(search(), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
    await waitFor(() => expect(f.operations.filter(op => op === "buddy_list").length).toBeGreaterThan(1));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].preferenceChanges).toEqual([
      { buddyId: retiredMax, mode: null, reason: "" },
    ]);
  });

  it("sends the search text to the request for unavailable buddies", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    await user.click(screen.getByRole("checkbox", { name: "显示不可用 2" }));
    await listed(2, 4);
    expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100 }, "csrf");
    // A search is a bounded server query, so matching rows past the display
    // budget are reachable instead of a local-only filter.
    await user.type(search(), "retired");
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("buddy_list",
      { includeUnavailable: true, limit: 100, query: "retired" }, "csrf"));
  });
});

describe("stale settings and unrelated saves", () => {
  it("shows a stale Router as a mark on the roles section and still saves a note elsewhere", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    // The unavailable Router's model stays hidden behind the filter.
    await listed(1, 2);
    // The stale review Router is no longer a stacked detail page: the roles
    // section carries the mark.
    const sections = screen.getByRole("navigation", { name: "Buddy 的分区" });
    expect(within(sections).getByRole("link", { name: /^角色\s*Router 不可用$/ })).toBeTruthy();

    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "只改这条意见");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    // The stale selector stays untouched and does not block the unrelated patch.
    expect(f.published[0].configuration).toBeUndefined();
    expect(f.published[0].preferenceChanges).toBeUndefined();
    expect(f.published[0].modelServiceNoteChanges).toEqual([
      { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "只改这条意见" },
    ]);
  });

  it("keeps a stale pin visible without requiring a repair before saving", async () => {
    const f = fixture({ stalePin: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await openRetired(user);
    await screen.findByText(/固定选择指向.*请启用或改为/);
    expect(chip(RETIRED, "max").textContent).toBe("max固定");
    await selectModel(user, FLASH, "DSH");
    await user.type(note(), "，补充说明");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0]).not.toHaveProperty("preferenceChanges");
    expect(f.published[0].modelServiceNoteChanges).toEqual([
      { harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", text: "，补充说明" },
    ]);
  });
});

describe("the two filters as browser habits", () => {
  it("restores both filters across unmount/remount and a reload with one bounded history read", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    let view = render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    await user.click(enabledOnly());
    await user.click(showUnavailable());
    await listed(2, 4);
    expect(f.command).toHaveBeenCalledWith("buddy_list", { includeUnavailable: true, limit: 100 }, "csrf");
    view.unmount();
    const reloaded = fixture();
    view = render(<App suppliedApi={reloaded.api} />);
    await waitFor(() => expect(checked(screen.queryByRole("checkbox", { name: "只看已启用" }))).toBe("true"));
    expect(checked(showUnavailable())).toBe("true");
    await listed(2, 4);
    await user.click(showUnavailable());
    await user.click(showUnavailable());
    view.rerender(<App suppliedApi={reloaded.api} />);
    await new Promise(resolve => setTimeout(resolve, 450));
    expect(reloaded.operations).toEqual(["buddy_list"]);
  });

  it("keeps the stored unavailable habit when the filter is hidden and still reads once", async () => {
    window.localStorage.setItem("hey-my-buddy.console.view.models.showUnavailable", "true");
    const f = fixture();
    const state = f.snapshot();
    state.unavailableBuddyCount = 0;
    f.command.mockImplementation(async () => ({ buddies: state.buddies, preferences: [], modelServiceConcurrency: [], nextCursor: null }));
    window.location.hash = "#buddies";
    const view = render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    await waitFor(() => expect(f.command).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole("checkbox", { name: /^显示不可用/ })).toBeNull();
    expect(window.localStorage.getItem("hey-my-buddy.console.view.models.showUnavailable")).toBe("true");
    view.rerender(<App suppliedApi={f.api} />);
    await new Promise(resolve => setTimeout(resolve, 450));
    expect(f.command).toHaveBeenCalledExactlyOnceWith("buddy_list", { includeUnavailable: true, limit: 100 }, "csrf");
  });

  it("uses unchecked defaults with broken storage and still lets the view change", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    expect(checked(enabledOnly())).toBe("false");
    expect(checked(showUnavailable())).toBe("false");
    await user.click(enabledOnly());
    expect(checked(enabledOnly())).toBe("true");
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("an address that names a buddy keeps the filters set by hand", () => {
  const enabledKey = "hey-my-buddy.console.view.models.enabledOnly";
  const unavailableKey = "hey-my-buddy.console.view.models.showUnavailable";
  function jump(buddyId: string) {
    act(() => {
      window.location.hash = `#buddies/catalog/buddy/${encodeURIComponent(buddyId)}`;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
  }

  it("reveals a disabled target without overwriting the manually checked enabled-only filter", async () => {
    const f = fixture();
    for (const buddy of f.snapshot().buddies) buddy.enabled = false;
    const user = userEvent.setup();
    window.location.hash = "#buddies/catalog";
    let view = render(<App suppliedApi={f.api} />);
    await listed(0, 2);
    await user.click(enabledOnly());
    await user.click(showUnavailable());
    await waitFor(() => expect(f.command).toHaveBeenCalledWith("buddy_list", { includeUnavailable: true, limit: 100 }, "csrf"));
    await user.click(showUnavailable());
    expect(localStorage.getItem(enabledKey)).toBe("true");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
    jump(flashHigh);
    await waitFor(() => pane(FLASH));
    expect(levelSwitch("high").getAttribute("aria-checked")).toBe("false");
    // The address lands on the buddy it names.
    await waitFor(() => expect(document.activeElement).toBe(level("high")));
    expect(checked(enabledOnly())).toBe("false");
    expect(localStorage.getItem(enabledKey)).toBe("true");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
    view.unmount();
    window.location.hash = "#buddies/catalog";
    view = render(<App suppliedApi={f.api} />);
    await waitFor(() => expect(checked(screen.queryByRole("checkbox", { name: "只看已启用" }))).toBe("true"));
    expect(checked(showUnavailable())).toBe("false");
    expect(localStorage.getItem(enabledKey)).toBe("true");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
  });

  it("reveals an unavailable Router target without overwriting the manually unchecked filter", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies/catalog";
    let view = render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    await user.click(enabledOnly());
    await user.click(enabledOnly());
    await user.click(showUnavailable());
    await listed(2, 4);
    await user.click(showUnavailable());
    expect(localStorage.getItem(enabledKey)).toBe("false");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
    jump(retiredMax);
    await waitFor(() => pane(RETIRED));
    expect(levelSwitch("max").getAttribute("aria-checked")).toBe("true");
    expect(checked(showUnavailable())).toBe("true");
    expect(localStorage.getItem(enabledKey)).toBe("false");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
    view.unmount();
    window.location.hash = "#buddies/catalog";
    view = render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    expect(checked(enabledOnly())).toBe("false");
    expect(checked(showUnavailable())).toBe("false");
    expect(localStorage.getItem(enabledKey)).toBe("false");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
  });
});
