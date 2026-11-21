import { reasonText } from "./i18n/reason";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { ApiError, type ConsoleApi } from "./api";
import type { Preference, Buddy, BuddyPage, Snapshot } from "./types";
import {
  chip, choosePreference, effortCell, level, levelPreference, levelSwitch, listed, modelButton, modelRow, pane, queryModel, search, selectModel,
} from "./buddies/page.testing";

const flashOff = "dsh:deepseek-official:deepseek-flash:off";
const flashHigh = "dsh:deepseek-official:deepseek-flash:high";
const retiredMax = "dsh:deepseek-official:retired-model:max";
const retiredLow = "dsh:deepseek-official:retired-model:low";
const retiredModelService = { harness: "dsh", provider: "deepseek-official", model: "retired-model" };
const FLASH = "dsh / deepseek-official / deepseek-flash";
const RETIRED = "dsh / deepseek-official / retired-model";

function catalog(): Buddy[] {
  const base = { effortRank: null,
    harness: "dsh", provider: "deepseek-official", capabilities: ["execution:dsh", "decision"],
    contextWindow: null, source: "catalog:fixture", description: "",
  };
  return [
    { ...base, buddyId: flashOff, model: "deepseek-flash", effort: "off", alias: null, available: true, enabled: true },
    { ...base, buddyId: flashHigh, model: "deepseek-flash", effort: "high", alias: null, available: true, enabled: false },
    { ...base, buddyId: retiredMax, model: "retired-model", effort: "max", alias: null,
      available: false, enabled: true, unavailableReason: { code: "catalog.native-unavailable", params: {} } },
    { ...base, buddyId: retiredLow, model: "retired-model", effort: "low", alias: null,
      available: false, enabled: false, unavailableReason: { code: "catalog.native-unavailable", params: {} } },
  ];
}

/**
 * Mirrors the recorded snapshot: available buddies plus the current
 * decision buddy. Retired identities are only reachable through the paged
 * `buddy_list` history, exactly like the board exposes them.
 */
function fixture(options: {
  staleDecision?: boolean; stalePin?: boolean; pageSize?: number;
  removal?: Record<string, Buddy["removal"]>; removalFailure?: ApiError;
} = {}) {
  let buddies = catalog().map(buddy => ({ ...buddy, removal: options.removal?.[buddy.buddyId] }));
  const decisionId = options.staleDecision ? retiredMax : flashOff;
  const models = ["deepseek-flash", "retired-model"].map(model => ({ modelId: model, displayName: model, names: [{ harness: "dsh", provider: "deepseek-official", model }], profile: null, latestProfileRun: null }));
  const correspondences = ["deepseek-flash", "retired-model"].map(model => ({ harness: "dsh", provider: "deepseek-official", model, correspondenceId: model, modelId: model, createdAt: "2026-11-01T00:00:00Z", source: "same-name" as const, evidenceUrl: null, evidenceDate: null, canUndo: false }));
  const preferences: Preference[] = options.stalePin
    ? [{ buddyId: retiredMax, mode: "pin" as const, note: "以前固定" }]
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

      models, modelCorrespondences: correspondences.filter(row => [...slice].some(buddy => buddy.model === row.model)),
      preferences: preferences.filter(p => ids.has(p.buddyId)),

      modelServiceConcurrency: [],
      nextCursor: ordered.length > slice.length ? slice[slice.length - 1].buddyId : null,
    };
  };
  const live = buddies.filter(p => p.available || p.buddyId === decisionId);
  let state: Snapshot = {
    csrfToken: "csrf", runtimeIdentity: "fixture-runtime", eventCursor: 0, consoleSession: { id: "fixture-session" },

    configuration: { explorationEvery: 5, routerBuddyIds: [decisionId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, modelResponseWaitSeconds: 60, profileBuddyId: null},
    buddies: live,
    // `unavailableBuddyCount` counts every unavailable row in the table,
    // including a listed one such as the retained decision buddy; the console
    // subtracts what it has loaded and pages the rest.
    unavailableBuddyCount: buddies.filter(p => !p.available).length,

    models, modelCorrespondences: correspondences.filter(row => live.some(buddy => buddy.model === row.model)),
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
      state = { ...state, buddies: state.buddies.map(buddy => {
        const change = params.buddySettings?.find((entry: { buddyId: string }) => entry.buddyId === buddy.buddyId);
        return change ? { ...buddy, ...change } : buddy;
      }) };
      return {};
    }
    if (operation === "buddy_remove" && options.removal) {
      if (options.removalFailure) throw options.removalFailure;
      const ids = new Set<string>(params.buddyIds);
      buddies = buddies.filter(buddy => !ids.has(buddy.buddyId));
      const removedModelServices = buddies.some(buddy => buddy.model === retiredModelService.model) ? [] : [retiredModelService];
      state = { ...state, eventCursor: state.eventCursor + 1,
        buddies: state.buddies.filter(buddy => !ids.has(buddy.buddyId)),
        unavailableBuddyCount: buddies.filter(buddy => !buddy.available).length };
      return { removedBuddyIds: params.buddyIds, removedModelServices, duplicate: false };
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
    macroTasks: vi.fn(async () => ({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, published, operations, snapshot: () => state };
}

const enabledOnly = () => screen.getByRole("checkbox", { name: "仅显示已启用" });
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

describe("removing retained buddies", () => {
  function open(f: ReturnType<typeof fixture>) {
    window.location.hash = "#buddies/models";
    render(<App suppliedApi={f.api} />);
  }

  it("removes one effort, selects the surviving effort, and keeps unrelated edits publishable", async () => {
    const f = fixture({ stalePin: true, removal: { [retiredMax]: { allowed: true } } });
    const user = userEvent.setup();
    open(f);
    await openRetired(user);
    await user.type(within(pane()).getByLabelText("别名"), "old-alias");
    expect(within(pane()).queryByRole("button", { name: "移除全部档位" })).toBeNull();
    await selectModel(user, FLASH, "DSH");
    await user.type(within(pane()).getByLabelText("别名"), "keep-alias");
    await selectModel(user, RETIRED, "DSH");
    await user.click(within(pane()).getByRole("button", { name: "移除" }));

    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("old-alias")).toBeTruthy();
    expect(dialog.getByText("固定")).toBeTruthy();
    expect(dialog.getByText(/历史评价样本、总结和过往微任务记录会保留/)).toBeTruthy();
    expect(f.operations).not.toContain("buddy_remove");
    await user.click(dialog.getByRole("button", { name: "移除" }));
    await screen.findByText("已移除 1 个档位。");
    expect(f.command).toHaveBeenCalledWith("buddy_remove", { commandId: expect.any(String), buddyIds: [retiredMax] }, "csrf");
    await waitFor(() => expect(effortCell("low").getAttribute("aria-pressed")).toBe("true"));
    expect(within(pane()).queryByRole("button", { name: "max" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].buddySettings).toEqual([{ buddyId: flashOff, alias: "keep-alias" }]);
  });

  it("removes all efforts in one command and clears the drawer location", async () => {
    const f = fixture({ removal: { [retiredMax]: { allowed: true }, [retiredLow]: { allowed: true } } });
    const user = userEvent.setup();
    open(f);
    await openRetired(user);
    await user.click(within(pane()).getByRole("button", { name: "移除全部档位" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByRole("heading").textContent).toContain("全部 2 个档位");
    await user.click(dialog.getByRole("button", { name: "移除全部档位" }));
    await screen.findByText("已移除 2 个档位。");
    expect(f.command.mock.calls.filter(([operation]) => operation === "buddy_remove")).toEqual([
      ["buddy_remove", { commandId: expect.any(String), buddyIds: [retiredLow, retiredMax] }, "csrf"],
    ]);
    expect(window.location.hash).toBe("#buddies/models");
    await waitFor(() => expect(queryModel(RETIRED)).toBeNull());
    expect(pane()).toBeNull();
  });

  it("shows the reason on both blocked actions and never opens confirmation", async () => {
    const reason = { code: "buddy-removal.in-router-list", params: {} };
    const f = fixture({ removal: { [retiredMax]: { allowed: false, reason }, [retiredLow]: { allowed: true } } });
    const user = userEvent.setup();
    open(f);
    await openRetired(user);
    for (const name of ["移除", "移除全部档位"]) {
      const button = within(pane()).getByRole("button", { name });
      expect(button.getAttribute("aria-disabled")).toBe("true");
      expect(button.getAttribute("aria-description")).toBe(reasonText(reason));
      await user.click(button);
      expect(screen.queryByRole("dialog")).toBeNull();
    }
    expect(f.operations).not.toContain("buddy_remove");
  });

  it.each([
    ["BUDDY_IN_USE", "buddy-removal.has-maintenance-runs", "维护运行记录引用了此 buddy。"],
    ["BUDDY_NOT_REMOVABLE", "buddy-removal.not-retired", "仅可移除档位已退休或模型已确认消失的 buddy。"],
  ])("shows the current reason after a %s conflict", async (code, reasonCode, message) => {
    const f = fixture({ removal: { [retiredMax]: { allowed: true } },
      removalFailure: new ApiError(code, "Conflict", { reason: { code: reasonCode, params: {} } }) });
    const user = userEvent.setup();
    open(f);
    await openRetired(user);
    await user.click(within(pane()).getByRole("button", { name: "移除" }));
    const dialog = within(screen.getByRole("dialog"));
    await user.click(dialog.getByRole("button", { name: "移除" }));
    expect((await dialog.findByRole("alert")).textContent).toContain(message);
    await user.click(dialog.getByRole("button", { name: "取消" }));
    expect(within(pane()).getByRole("button", { name: "移除" })).toBeTruthy();
  });
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
    expect(state.getAttribute("aria-description")).toBe(reasonText({ code: "catalog.native-unavailable", params: {} }));
    expect(chip(RETIRED, "max").textContent).toBe("max");
    expect(within(modelRow(RETIRED)).queryByText("low")).toBeNull();
    expect(within(modelRow(FLASH)).queryByText("不可用")).toBeNull();
    await user.click(modelButton(RETIRED));
    // The side column says it once for the model and keeps the note.
    expect(within(pane(RETIRED)).getByText("不可用").getAttribute("aria-description")).toBe(reasonText({ code: "catalog.native-unavailable", params: {} }));
    expect(within(pane()).getAllByText("不可用")).toHaveLength(1);
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

  it("combines 仅显示已启用 with 显示不可用", async () => {
    const f = fixture();
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    // 仅显示已启用 alone still hides the model: none of its available buddies exist.
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
    expect(pin.getAttribute("aria-description")).toBe("该 buddy 不可用或未启用，不能固定");
    await user.click(pin);
    expect(pin.getAttribute("aria-checked")).toBe("false");
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0].preferenceChanges).toEqual([
      { buddyId: retiredMax, mode: "pin", note: "仍然适用" },
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
      { buddyId: retiredMax, mode: null, note: null },
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
  it("shows a stale Router as a mark on the roles section and still saves an unrelated Buddy setting", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies";
    render(<App suppliedApi={f.api} />);
    // The unavailable Router's model stays hidden behind the filter.
    await listed(1, 2);
    // The stale review Router is no longer a stacked detail page: the roles
    // section carries the mark.
    const sections = screen.getByRole("navigation", { name: "Buddy 的分区" });
    expect(within(sections).getByRole("link", { name: /^Router\s*Router 不可用$/ })).toBeTruthy();

    await selectModel(user, FLASH, "DSH");
    await user.click(effortCell("high"));
    await user.click(levelSwitch("high"));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    // The stale selector stays untouched and does not block the unrelated patch.
    expect(f.published[0].configuration).toBeUndefined();
    expect(f.published[0].preferenceChanges).toBeUndefined();
    expect(f.published[0].buddySettings).toEqual([{ buddyId: flashHigh, enabled: true }]);
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
    await user.click(effortCell("high"));
    await user.click(levelSwitch("high"));
    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("设置已保存");
    expect(f.published[0]).not.toHaveProperty("preferenceChanges");
    expect(f.published[0].buddySettings).toEqual([{ buddyId: flashHigh, enabled: true }]);
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
    await waitFor(() => expect(checked(screen.queryByRole("checkbox", { name: "仅显示已启用" }))).toBe("true"));
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
    f.command.mockImplementation(async () => ({ buddies: state.buddies, preferences: [], models: state.models,
      modelCorrespondences: state.modelCorrespondences, modelServiceConcurrency: [], nextCursor: null }));
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
      window.location.hash = `#buddies/models/buddy/${encodeURIComponent(buddyId)}`;
      window.dispatchEvent(new HashChangeEvent("hashchange"));
    });
  }

  it("reveals a disabled target without overwriting the manually checked enabled-only filter", async () => {
    const f = fixture();
    for (const buddy of f.snapshot().buddies) buddy.enabled = false;
    const user = userEvent.setup();
    window.location.hash = "#buddies/models";
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
    window.location.hash = "#buddies/models";
    view = render(<App suppliedApi={f.api} />);
    await waitFor(() => expect(checked(screen.queryByRole("checkbox", { name: "仅显示已启用" }))).toBe("true"));
    expect(checked(showUnavailable())).toBe("false");
    expect(localStorage.getItem(enabledKey)).toBe("true");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
  });

  it("reveals an unavailable Router target without overwriting the manually unchecked filter", async () => {
    const f = fixture({ staleDecision: true });
    const user = userEvent.setup();
    window.location.hash = "#buddies/models";
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
    window.location.hash = "#buddies/models";
    view = render(<App suppliedApi={f.api} />);
    await listed(1, 2);
    expect(checked(enabledOnly())).toBe("false");
    expect(checked(showUnavailable())).toBe("false");
    expect(localStorage.getItem(enabledKey)).toBe("false");
    expect(localStorage.getItem(unavailableKey)).toBe("false");
  });
});
