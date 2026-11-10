import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { formatShortDateTime } from "../i18n/format";
import type { Snapshot } from "../types";
import { levelPreference, modelButton, selectModel } from "./page.testing";
import {
  FLASH, FLASH_ID, GLM, GLM_ID, HIGH, HIGH_ID, MEDIUM, MEDIUM_ID, PENDING, PENDING_ID, SOL, SOL_ID,
  addButton, addDialog, addRouter, responseWaitInput, choice, choices, fixture, health, intervalInput, line, listed, modeOption, moveDown, moveUp,
  openRoles, queryAddDialog, queryDraftBar, queryRouterList, remove, rolesRegion, saveButton, section, snapshot, unsavedIn,
} from "./roles.testing";

/**
 * The roles view as a user meets it: the Router list with its standing, its
 * add dialog and its three changes, the settings of the role, the records of
 * routing, and what a session that cannot write or a save in flight leaves
 * usable. Every change goes into the draft and is
 * published by the save bar as a patch of the changed settings only.
 */

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
});

const IDENTITY = new Set(["commandId"]);
/** What a publication carries besides its idempotency key. */
const patchOf = (params: Record<string, any>) => Object.fromEntries(Object.entries(params).filter(([key]) => !IDENTITY.has(key)));
const interval = 45;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

describe("the Router list", () => {
  it("lists the buddies in the order they serve, each with its number, its four parts and where it stands", async () => {
    const f = fixture(snapshot({
      routers: [MEDIUM_ID, FLASH_ID, SOL_ID, HIGH_ID],
      routingHealth: health({
        currentRouterBuddyId: MEDIUM_ID,
        routers: [
          { buddyId: MEDIUM_ID, index: 0, identity: null, eligible: true },
          { buddyId: FLASH_ID, index: 1, identity: null, eligible: true },
          { buddyId: SOL_ID, index: 2, identity: null, eligible: false, code: "router-not-eligible" },
          { buddyId: HIGH_ID, index: 3, identity: null, eligible: false, code: "router-skip-window", inSkipWindow: true, retryAt: "2026-09-27T08:00:00.000Z" },
        ],
      }),
    }));
    await openRoles(f);
    expect(listed()).toEqual([MEDIUM, FLASH, SOL, HIGH]);
    expect(within(line(MEDIUM)).getByText("1")).toBeTruthy();
    expect(within(line(HIGH)).getByText("4")).toBeTruthy();
    const standing = (quad: string) => line(quad).querySelector("[data-slot=state-mark]")!.getAttribute("data-kind");
    // The filled circle is the buddy serving now; every other standing is hollow.
    expect(within(line(MEDIUM)).getByText("当前")).toBeTruthy();
    expect(standing(MEDIUM)).toBe("live");
    expect(within(line(FLASH)).getByText("候补")).toBeTruthy();
    expect(standing(FLASH)).toBe("idle");
    // The recorded code explains the word "不可用".
    expect(within(line(SOL)).getByText("不可用").getAttribute("aria-description")).toBe("router-not-eligible");
    expect(standing(SOL)).toBe("idle");
    const waiting = within(line(HIGH));
    expect(waiting.getByText("等待重试")).toBeTruthy();
    expect(line(HIGH).querySelector("time")!.textContent).toBe(formatShortDateTime(Date.parse("2026-09-27T08:00:00.000Z")));
    expect(standing(HIGH)).toBe("idle");
    expect(within(screen.getByRole("navigation", { name: "Router 的工作" })).getByRole("link", { name: "路由" }).getAttribute("aria-current")).toBe("page");
  });

  it("names the buddy the service says holds the role, not the head of the list", async () => {
    const f = fixture(snapshot({
      routers: [SOL_ID, MEDIUM_ID],
      routingHealth: health({ currentRouterBuddyId: MEDIUM_ID, routers: [
        { buddyId: SOL_ID, index: 0, identity: null, eligible: false, code: "router-not-eligible" },
        { buddyId: MEDIUM_ID, index: 1, identity: null, eligible: true },
      ] }),
    }));
    await openRoles(f);
    expect(within(line(MEDIUM)).getByText("当前")).toBeTruthy();
    expect(within(line(SOL)).queryByText("当前")).toBeNull();
  });

  it("writes the first buddy as current and nothing on the others when the snapshot has no health facts", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID, FLASH_ID] }));
    await openRoles(f);
    expect(within(line(MEDIUM)).getByText("当前")).toBeTruthy();
    expect(line(FLASH).querySelector("[data-slot=state-mark]")).toBeNull();
    expect(within(line(FLASH)).queryByText(/当前|候补|不可用|等待重试/)).toBeNull();
  });

  it("writes the id of a buddy the catalogue no longer has, without a link to a model", async () => {
    const f = fixture(snapshot({ routers: ["gone:provider:model:low", MEDIUM_ID] }));
    await openRoles(f);
    expect(listed()).toEqual(["gone:provider:model:low", MEDIUM]);
    expect(within(line("gone:provider:model:low")).getByText("gone:provider:model:low")).toBeTruthy();
    expect(within(line("gone:provider:model:low")).queryByRole("link")).toBeNull();
    expect(within(line(MEDIUM)).getByRole("link", { name: MEDIUM })).toBeTruthy();
  });

  it("goes to the model of a buddy when its four parts are chosen", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    const link = within(line(MEDIUM)).getByRole("link", { name: MEDIUM });
    expect(link.getAttribute("href")).toBe(`#buddies/catalog/buddy/${encodeURIComponent(MEDIUM_ID)}`);
    await user.click(link);
    expect(window.location.hash).toBe(`#buddies/catalog/buddy/${encodeURIComponent(MEDIUM_ID)}`);
    await waitFor(() => expect(modelButton("claude / anthropic / claude-sonnet-5").getAttribute("aria-expanded")).toBe("true"));
  });

  it("says that there is no Router yet and how to add one, and draws no list", async () => {
    const f = fixture(snapshot());
    await openRoles(f);
    expect(screen.getByText("暂无 Router")).toBeTruthy();
    expect(screen.getByText(/使用“添加”/)).toBeTruthy();
    expect(queryRouterList()).toBeNull();
    expect(within(section("列表")).queryByRole("status")).toBeNull();
  });

  it("sends no command when the view is entered", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterBuddyId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps a large decision history out of the roles page", async () => {
    const decision = (id: string) => ({
      decisionId: id, status: "completed", task: "不应出现在Buddy 页的长任务说明。".repeat(200),
      buddyId: MEDIUM_ID, selectedBuddy: null, reason: "", evidence: [],
      createdAt: "2026-09-24T08:00:00Z", runId: id, decisionModel: null, requested: {}, input: {}, output: {},
    });
    const f = fixture(snapshot({ routers: [MEDIUM_ID], extra: { decisions: Array.from({ length: 50 }, (_, i) => decision(`history-${i}`)) } as unknown as Partial<Snapshot> }));
    await openRoles(f);
    expect(responseWaitInput()).toHaveProperty("value", "60");
    expect(responseWaitInput()).toHaveProperty("max", "86400");
    expect(screen.queryByRole("checkbox", { name: "自动采纳常规整理结果" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "最近决策" })).toBeNull();
    expect(screen.queryByRole("button", { name: "请求整理" })).toBeNull();
    expect(screen.queryByText(/不应出现在Buddy 页的长任务说明/)).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("what the list says needs the user", () => {
  const note = () => within(section("列表")).queryByRole("status");

  it("says nothing while a buddy that can serve holds the role", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterBuddyId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(note()).toBeNull();
  });

  it("says that no buddy of the list can serve when the service names none", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ available: false, reason: { code: "router-skip-window", params: {} }, currentRouterBuddyId: null }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("列表中没有可担任 Router 的 buddy。");
  });

  it("names the first buddy and why the default mode refuses it, in the words of the refusal", async () => {
    const f = fixture(snapshot({ routers: [SOL_ID, MEDIUM_ID], routingHealth: health({ currentRouterBuddyId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe(`列表第一项 ${SOL}：所属 harness 尚不具备本地只读路由资格。`);
  });

  it("says that the first buddy is no longer in the catalogue", async () => {
    const f = fixture(snapshot({ routers: ["gone:p:m:e"], routingHealth: health({ currentRouterBuddyId: "gone:p:m:e" }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("列表第一项 gone:p:m:e：已不在目录中。");
  });

  it("says that routing keeps failing, without calling it unavailable", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterBuddyId: MEDIUM_ID, available: true, failureCount: 4, consecutiveFailures: 4 }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("路由连续失败 4 次。");
  });

  it("renders the recorded Router reason", async () => {
    const f = fixture(snapshot({ routers: [], routingHealth: health({ available: false, reason: { code: "router-all-unavailable", params: {} }, currentRouterBuddyId: null }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("列表中的 Router 当前均不可用。");
  });

  it("is worked out from the draft: the first buddy is named as soon as the default mode refuses it, and not once it does not", async () => {
    const f = fixture(snapshot({ routers: [FLASH_ID], configuration: { defaultRoutingMode: "fast" }, routingHealth: health({ currentRouterBuddyId: FLASH_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    expect(note()).toBeNull();
    await user.click(modeOption("审阅"));
    expect(note()!.textContent).toBe(`列表第一项 ${FLASH}：所属 harness 尚不具备本地只读路由资格。`);
    await user.click(modeOption("快速"));
    expect(note()).toBeNull();
  });
});

describe("adding to the list", () => {
  it("adds a buddy to the empty list and publishes a patch of the list alone", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await addRouter(user, MEDIUM);
    expect(listed()).toEqual([MEDIUM]);
    expect(unsavedIn("列表")).toHaveLength(1);
    // A buddy that is only in the draft has no health facts, so it is not written as current.
    expect(within(line(MEDIUM)).queryByText(/当前|候补|不可用|等待重试/)).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(f.published).toHaveLength(1);
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [MEDIUM_ID] } });
  });

  it("adds after the buddies already in the list and leaves their order", async () => {
    const f = fixture(snapshot({ routers: [FLASH_ID, MEDIUM_ID], routingHealth: health({ currentRouterBuddyId: FLASH_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    await addRouter(user, PENDING);
    expect(listed()).toEqual([FLASH, MEDIUM, PENDING]);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [FLASH_ID, MEDIUM_ID, PENDING_ID] } });
  });

  it("adds a model that waits to be confirmed as readily as any other that is enabled", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(choice(PENDING).getAttribute("aria-disabled")).toBeNull();
    await user.click(choice(PENDING));
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [PENDING_ID] } });
  });

  it("lists the buddies outside the list, those that can serve in the default mode first and the rest with their reason", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    const rows = choices();
    expect(rows.map(row => row.textContent)).toEqual([
      PENDING,
      `${HIGH}该推理强度未启用，请先启用`,
      `${FLASH}所属 harness 尚不具备本地只读路由资格`,
      `${SOL}所属 harness 尚不具备本地只读路由资格`,
      `${GLM}该推理强度当前不可用`,
    ]);
    expect(rows.map(row => row.getAttribute("aria-disabled"))).toEqual([null, "true", "true", "true", "true"]);
    expect(within(addDialog()).queryByText(MEDIUM)).toBeNull();
  });

  it("judges by the default mode of the draft: fast routing admits what review refuses", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(choice(FLASH).getAttribute("aria-disabled")).toBe("true");
    expect(within(choice(FLASH)).getByText("所属 harness 尚不具备本地只读路由资格")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(modeOption("快速"));
    await user.click(addButton());
    expect(choice(FLASH).getAttribute("aria-disabled")).toBeNull();
    expect(choice(SOL).getAttribute("aria-disabled")).toBe("true");
    expect(within(choice(SOL)).getByText("所属 harness 尚不支持无工具路由调用")).toBeTruthy();
  });

  it("does not add a buddy whose Harness is not eligible in review mode, says why in its row and changes nothing", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    await user.click(choice(SOL));
    // The dialog stays open on the reason, and the draft is untouched.
    expect(addDialog()).toBeTruthy();
    expect(within(choice(SOL)).getByText("所属 harness 尚不具备本地只读路由资格")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(queryRouterList()).toBeNull();
    expect(queryDraftBar()).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("does not add a buddy that is not enabled and says to switch it on first", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(within(choice(HIGH)).getByText("该推理强度未启用，请先启用")).toBeTruthy();
    await user.click(choice(HIGH));
    expect(queryAddDialog()).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(queryRouterList()).toBeNull();
    expect(queryDraftBar()).toBeNull();
  });

  it("does not add a buddy that is not available", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(within(choice(GLM)).getByText("该推理强度当前不可用")).toBeTruthy();
    await user.click(choice(GLM));
    expect(queryAddDialog()).toBeTruthy();
    expect(queryDraftBar()).toBeNull();
  });

  it("narrows the list to the buddies whose four parts contain what is typed, wherever it is", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    const search = within(addDialog()).getByRole("searchbox", { name: "搜索 buddy" });
    await user.type(search, "DEEPSEEK");
    expect(choices().map(row => row.textContent)).toEqual([`${FLASH}所属 harness 尚不具备本地只读路由资格`]);
    await user.clear(search);
    await user.type(search, "high");
    expect(choices().map(row => row.textContent)).toEqual([`${HIGH}该推理强度未启用，请先启用`]);
    await user.clear(search);
    await user.type(search, "no-such-buddy");
    expect(within(addDialog()).getByText("没有匹配的 buddy")).toBeTruthy();
    expect(choices()).toHaveLength(0);
  });

  it("says that nothing is left to add when the list holds the whole catalogue", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID, HIGH_ID, FLASH_ID, SOL_ID, GLM_ID, PENDING_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(within(addDialog()).getByText("没有可添加的 buddy")).toBeTruthy();
  });

  it("closes on 取消 and on Escape, and starts from an empty search when it opens again", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    await user.type(within(addDialog()).getByRole("searchbox"), "deepseek");
    await user.click(screen.getByRole("button", { name: "取消" }));
    expect(queryAddDialog()).toBeNull();
    await user.click(addButton());
    expect(within(addDialog()).getByRole("searchbox")).toHaveProperty("value", "");
    await user.keyboard("{Escape}");
    expect(queryAddDialog()).toBeNull();
    expect(queryDraftBar()).toBeNull();
  });
});

describe("moving and removing", () => {
  const three = () => snapshot({ routers: [MEDIUM_ID, FLASH_ID, PENDING_ID], routingHealth: health({ currentRouterBuddyId: MEDIUM_ID }) });

  it("leaves the first line's move up and the last line's move down out of use", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    expect(moveUp(MEDIUM).getAttribute("aria-disabled")).toBe("true");
    expect(moveDown(PENDING).getAttribute("aria-disabled")).toBe("true");
    expect(moveUp(FLASH).getAttribute("aria-disabled")).toBeNull();
    expect(moveDown(MEDIUM).getAttribute("aria-disabled")).toBeNull();
    // They are not `disabled`: they stay reachable and a click does nothing.
    expect(moveUp(MEDIUM)).toHaveProperty("disabled", false);
    await user.click(moveUp(MEDIUM));
    await user.click(moveDown(PENDING));
    expect(listed()).toEqual([MEDIUM, FLASH, PENDING]);
    expect(queryDraftBar()).toBeNull();
  });

  it("moves a buddy down and publishes the whole list in the new order", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveDown(MEDIUM));
    expect(listed()).toEqual([FLASH, MEDIUM, PENDING]);
    expect(unsavedIn("列表")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [FLASH_ID, MEDIUM_ID, PENDING_ID] } });
  });

  it("moves a buddy up and publishes the whole list in the new order", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveUp(PENDING));
    expect(listed()).toEqual([MEDIUM, PENDING, FLASH]);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [MEDIUM_ID, PENDING_ID, FLASH_ID] } });
  });

  it("numbers the lines again after a move, and with no health facts the one now first is written as current", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID, FLASH_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveUp(FLASH));
    expect(within(line(FLASH)).getByText("1")).toBeTruthy();
    expect(within(line(MEDIUM)).getByText("2")).toBeTruthy();
    expect(within(line(FLASH)).getByText("当前")).toBeTruthy();
    expect(within(line(MEDIUM)).queryByText("当前")).toBeNull();
  });

  it("removes a buddy without asking, publishes the list without it and leaves the others in order", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(remove(FLASH));
    expect(listed()).toEqual([MEDIUM, PENDING]);
    expect(screen.queryByRole("alertdialog")).toBeNull();
    expect(queryAddDialog()).toBeNull();
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [MEDIUM_ID, PENDING_ID] } });
  });

  it("publishes an empty list when the last buddy is removed, and lets the draft go to bring it back", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(remove(MEDIUM));
    expect(queryRouterList()).toBeNull();
    expect(screen.getByText("暂无 Router")).toBeTruthy();
    await user.click(within(screen.getByRole("region", { name: "未保存的修改" })).getByRole("button", { name: "放弃" }));
    await screen.findByText("草稿已放弃");
    expect(listed()).toEqual([MEDIUM]);
    expect(f.command).not.toHaveBeenCalled();
    await user.click(remove(MEDIUM));
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [] } });
  });

  it("is no change at all once the list is back in the order it was published in", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveDown(MEDIUM));
    expect(queryDraftBar()).toBeTruthy();
    await user.click(moveUp(MEDIUM));
    expect(listed()).toEqual([MEDIUM, FLASH, PENDING]);
    expect(unsavedIn("列表")).toHaveLength(0);
    expect(queryDraftBar()).toBeNull();
  });

  it("keeps the standing of a buddy with the buddy when the list is reordered", async () => {
    const f = fixture(snapshot({
      routers: [MEDIUM_ID, SOL_ID],
      routingHealth: health({ currentRouterBuddyId: MEDIUM_ID, routers: [
        { buddyId: MEDIUM_ID, index: 0, identity: null, eligible: true },
        { buddyId: SOL_ID, index: 1, identity: null, eligible: false, code: "router-not-eligible" },
      ] }),
    }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveDown(MEDIUM));
    expect(listed()).toEqual([SOL, MEDIUM]);
    expect(within(line(MEDIUM)).getByText("当前")).toBeTruthy();
    expect(within(line(SOL)).getByText("不可用")).toBeTruthy();
  });
});

describe("what leaves the roles view out of use", () => {
  it("publishes a catalogue preference without changing Router settings", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    window.location.hash = "#buddies/catalog";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("region", { name: "目录" });
    await selectModel(user, "claude / anthropic / claude-sonnet-5", "Claude Code");
    await user.click(within(levelPreference("medium")).getByRole("radio", { name: "优先" }));
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(f.published).toHaveLength(1);
    expect(f.published[0]).not.toHaveProperty("configuration");
    expect(f.published[0].preferenceChanges).toEqual([
      { buddyId: MEDIUM_ID, mode: "prefer", note: null },
    ]);
  });

  it("leaves the controls out of use in a session that cannot write, with the reason, and still shows the records", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID, FLASH_ID], sessionPresent: false, routingHealth: health({ currentRouterBuddyId: MEDIUM_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    const reason = "浏览器授权已失效，重新授权后可以修改";
    for (const control of [addButton(), moveDown(MEDIUM), moveUp(FLASH), remove(MEDIUM), intervalInput(), responseWaitInput()]) {
      expect(control.getAttribute("aria-disabled")).toBe("true");
      expect(control.getAttribute("aria-description")).toBe(reason);
    }
    expect(modeOption("快速").getAttribute("aria-description")).toContain(reason);
    await user.click(addButton());
    await user.click(remove(MEDIUM));
    await user.click(moveDown(MEDIUM));
    await user.click(modeOption("快速"));
    await user.click(responseWaitInput());
    expect(queryAddDialog()).toBeNull();
    expect(listed()).toEqual([MEDIUM, FLASH]);
    expect(modeOption("审阅").getAttribute("aria-checked")).toBe("true");
    expect(queryDraftBar()).toBeNull();
    // A control that is out of use is not tried: the page raises no refusal for it.
    expect(screen.queryAllByRole("status").filter(node => node.textContent === reason)).toHaveLength(0);
    expect(within(section("记录")).getByText("失败")).toBeTruthy();
  });

  it("leaves the controls out of use while a save is in flight, with the reason", async () => {
    const saving = deferred<unknown>();
    const f = fixture(snapshot({ routers: [MEDIUM_ID, FLASH_ID] }), { user_policy_publish: () => saving.promise });
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(remove(FLASH));
    await user.click(saveButton());
    const reason = "正在保存或等待核对，暂时不能修改";
    await waitFor(() => expect(addButton().getAttribute("aria-disabled")).toBe("true"));
    for (const control of [addButton(), moveDown(MEDIUM), remove(MEDIUM), intervalInput()]) {
      expect(control.getAttribute("aria-disabled")).toBe("true");
      expect(control.getAttribute("aria-description")).toBe(reason);
    }
    await user.click(remove(MEDIUM));
    await user.click(modeOption("快速"));
    expect(listed()).toEqual([MEDIUM]);
    expect(modeOption("审阅").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByText("正在保存或读取目录；请稍后修改。")).toBeNull();
    saving.resolve({ published: true,});
    await screen.findByText("设置已保存");
    expect(addButton().getAttribute("aria-disabled")).toBeNull();
  });
});

describe("the settings of the role", () => {
  it("explains each setting through its help control without changing the draft", async () => {
    const f = fixture(snapshot()); const user = userEvent.setup();
    await openRoles(f);
    const explanations = [
      ["默认模式", /冻结的代码副本/], ["开始作答时限", /开始作答后不限时/],
      ["重试间隔", /间隔过后，新路由/], ["探索名额", /样本更少的 buddy/],
    ] as const;
    for (const [name, meaning] of explanations) {
      const button = within(section(name)).getByRole("button", { name: `关于${name}` });
      expect(button.getAttribute("aria-expanded")).toBe("false");
      await user.click(button);
      expect(button.getAttribute("aria-expanded")).toBe("true");
      expect(screen.getByRole("dialog", { name: `关于${name}` }).textContent).toMatch(meaning);
      await user.click(button);
      expect(button.getAttribute("aria-expanded")).toBe("false");
      expect(screen.queryByRole("dialog", { name: `关于${name}` })).toBeNull();
    }
    const waitHelp = screen.getByRole("button", { name: "关于开始作答时限" });
    waitHelp.focus();
    await user.keyboard("{Enter}");
    expect(waitHelp.getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByRole("dialog", { name: "关于开始作答时限" }).textContent).toContain("本轮维护记为失败并由下一轮重试");
    expect(screen.getByRole("dialog", { name: "关于开始作答时限" }).textContent).toContain("画像构建记为失败、可重新发起");
    await user.keyboard("{Escape}");
    expect(waitHelp.getAttribute("aria-expanded")).toBe("false");
    waitHelp.focus();
    await user.keyboard(" ");
    expect(waitHelp.getAttribute("aria-expanded")).toBe("true");
    waitHelp.focus();
    await user.keyboard(" ");
    expect(waitHelp.getAttribute("aria-expanded")).toBe("false");
    await user.keyboard("{Enter}");
    expect(waitHelp.getAttribute("aria-expanded")).toBe("true");
    await user.click(responseWaitInput());
    expect(waitHelp.getAttribute("aria-expanded")).toBe("false");
    expect(responseWaitInput()).toHaveProperty("value", "60");
    expect(queryDraftBar()).toBeNull();
    expect(f.published).toHaveLength(0);
  });

  it("chooses the default mode, tells where the data goes in each choice and publishes the one setting", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    expect(modeOption("审阅").getAttribute("aria-checked")).toBe("true");
    expect(modeOption("快速").getAttribute("aria-checked")).toBe("false");
    const modeHelp = within(section("默认模式")).getByRole("button", { name: "关于默认模式" });
    expect(section("默认模式").querySelector('[data-slot="routing-mode-consequence"]')).toBeNull();
    await user.click(modeHelp);
    expect(screen.getByRole("dialog", { name: "关于默认模式" }).textContent).toMatch(/^需要 Router 判断时，任务的文字会发给 Router 的模型提供方，还会发送一份冻结的代码副本/);
    expect(screen.getByRole("dialog", { name: "关于默认模式" }).textContent).toContain("DSH 与 ZCode 没有系统沙盒");
    await user.click(modeHelp);
    expect(modeOption("快速").getAttribute("aria-description")).toBeNull();
    expect(modeOption("审阅").getAttribute("aria-description")).toBeNull();
    expect(within(section("默认模式")).getByText("默认模式").getAttribute("aria-description")).toBeNull();
    expect(unsavedIn("默认模式")).toHaveLength(0);
    await user.click(modeOption("快速"));
    expect(modeOption("快速").getAttribute("aria-checked")).toBe("true");
    await user.click(modeHelp);
    expect([...screen.getByRole("dialog", { name: "关于默认模式" }).querySelectorAll("p")].map(paragraph => paragraph.textContent)).toEqual([
      "需要 Router 判断时，任务的文字会发给 Router 的模型提供方。",
      expect.stringMatching(/^快速模式依据任务文字/),
    ]);
    await user.click(modeHelp);
    expect(unsavedIn("默认模式")).toHaveLength(1);
    expect(unsavedIn("列表")).toHaveLength(0);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { defaultRoutingMode: "fast" } });
  });

  it("sets the model response wait and publishes the one setting", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    expect(responseWaitInput()).toHaveProperty("value", "60");
    await user.clear(responseWaitInput());
    await user.type(responseWaitInput(), "30");
    expect(responseWaitInput()).toHaveProperty("value", "30");
    expect(unsavedIn("开始作答时限")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { modelResponseWaitSeconds: 30 } });
  });

  it.each(["0", "86401", "2.5", ""])("keeps an invalid response wait %j in the draft and blocks publication", async value => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.clear(responseWaitInput());
    if (value) await user.type(responseWaitInput(), value);
    expect(responseWaitInput()).toHaveProperty("value", value);
    expect(responseWaitInput().getAttribute("aria-invalid")).toBe("true");
    await user.click(saveButton());
    expect(f.published).toHaveLength(0);
  });

  it("sets the retry interval in seconds and publishes the one setting", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    expect(intervalInput()).toHaveProperty("value", "600");
    expect(intervalInput().getAttribute("aria-invalid")).toBeNull();
    expect(within(section("重试间隔")).getByText("秒")).toBeTruthy();
    expect(within(section("重试间隔")).getByText("重试间隔").getAttribute("aria-description"))
      .toBeNull();
    await user.clear(intervalInput());
    await user.type(intervalInput(), String(interval));
    expect(intervalInput()).toHaveProperty("value", String(interval));
    expect(unsavedIn("重试间隔")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerRetryIntervalSeconds: interval } });
  });

  it.each(["0", "2147483648", "2.5", ""])("keeps %j in the draft as it was typed, marks it invalid with the range and does not save it", async value => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.clear(intervalInput());
    if (value) await user.type(intervalInput(), value);
    expect(intervalInput()).toHaveProperty("value", value);
    expect(intervalInput().getAttribute("aria-invalid")).toBe("true");
    expect(within(section("重试间隔")).getByText("须为 1–2147483647 的整数")).toBeTruthy();
    // Leaving the field does not publish an earlier valid prefix.
    await user.tab();
    expect(intervalInput()).toHaveProperty("value", value);
    await user.click(saveButton());
    expect(f.published).toHaveLength(0);
    await user.clear(intervalInput());
    await user.type(intervalInput(), "7");
    expect(intervalInput().getAttribute("aria-invalid")).toBeNull();
    expect(within(section("重试间隔")).queryByText(/须为 1–2147483647/)).toBeNull();
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerRetryIntervalSeconds: 7 } });
  });

  it("marks a section only while its own setting differs from the published one, and publishes every changed setting together", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(modeOption("快速"));
    await user.clear(responseWaitInput());
    await user.type(responseWaitInput(), "120");
    await user.click(remove(MEDIUM));
    expect([unsavedIn("列表").length, unsavedIn("默认模式").length, unsavedIn("开始作答时限").length, unsavedIn("重试间隔").length]).toEqual([1, 1, 1, 0]);
    await user.click(modeOption("审阅"));
    expect([unsavedIn("列表").length, unsavedIn("默认模式").length, unsavedIn("开始作答时限").length]).toEqual([1, 0, 1]);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerBuddyIds: [], modelResponseWaitSeconds: 120 } });
  });
});

describe("exploration quota", () => {
  const input = () => within(section("探索名额")).getByRole("spinbutton", { name: "探索间隔微任务数" });
  it.each([null, 7])("publishes exploration %s as its own setting", async every => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    expect(input()).toHaveProperty("value", "5");
    if (every === null) await user.click(within(section("探索名额")).getByRole("radio", { name: "关闭" }));
    else { await user.clear(input()); await user.type(input(), String(every)); }
    expect(unsavedIn("探索名额")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { explorationEvery: every } });
  });
  it.each(["", "1", "2.5"])("blocks an invalid quota %j until corrected", async value => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.clear(input());
    if (value) await user.type(input(), value);
    await user.tab();
    expect(input().getAttribute("aria-invalid")).toBe("true");
    await user.click(saveButton());
    expect(f.published).toHaveLength(0);
    await user.clear(input()); await user.type(input(), "2");
    await user.click(saveButton());
    await screen.findByText("设置已保存");
    expect(patchOf(f.published[0])).toEqual({ configuration: { explorationEvery: 2 } });
  });
});
