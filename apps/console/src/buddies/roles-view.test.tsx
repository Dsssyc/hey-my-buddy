import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { TooltipProvider } from "@/components/ui/tooltip";
import { App } from "../App";
import { makeDraft } from "../draft";
import { formatShortDateTime } from "../i18n/format";
import type { Snapshot } from "../types";
import type { Editor } from "../use-editor";
import { RolesView } from "./RolesView";
import { defaultPreference, modelButton, selectModel } from "./page.testing";
import {
  FLASH, FLASH_ID, GLM, GLM_ID, HIGH, HIGH_ID, MEDIUM, MEDIUM_ID, PENDING, PENDING_ID, SOL, SOL_ID,
  addButton, addDialog, addRouter, budgetOption, choice, choices, fixture, health, intervalInput, line, listed, modeOption, moveDown, moveUp,
  openRoles, queryAddDialog, queryDraftBar, queryRouterList, remove, rolesRegion, saveButton, section, snapshot, unsavedIn,
} from "./roles.testing";

/**
 * The roles view as a user meets it: the Router list with its standing, its
 * add dialog and its three changes, the settings of the role, the records of
 * routing, and what a session that cannot write, a save in flight or settings
 * that need an upgrade leave usable. Every change goes into the draft and is
 * published by the save bar as a patch of the changed settings only.
 */

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.restoreAllMocks();
  window.location.hash = "";
});

const IDENTITY = new Set(["commandId", "writerId", "generation", "writerToken", "expectedRevision"]);
/** What a publication carries besides who publishes and against which revision. */
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
        currentRouterProfileId: MEDIUM_ID,
        routers: [
          { profileId: MEDIUM_ID, index: 0, identity: null, eligible: true },
          { profileId: FLASH_ID, index: 1, identity: null, eligible: true },
          { profileId: SOL_ID, index: 2, identity: null, eligible: false, code: "router-not-eligible" },
          { profileId: HIGH_ID, index: 3, identity: null, eligible: false, code: "router-skip-window", inSkipWindow: true, retryAt: "2026-09-27T08:00:00.000Z" },
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
    expect(screen.getByRole("heading", { name: "Router", level: 2 })).toBeTruthy();
  });

  it("names the buddy the service says holds the role, not the head of the list", async () => {
    const f = fixture(snapshot({
      routers: [SOL_ID, MEDIUM_ID],
      routingHealth: health({ currentRouterProfileId: MEDIUM_ID, routers: [
        { profileId: SOL_ID, index: 0, identity: null, eligible: false, code: "router-not-eligible" },
        { profileId: MEDIUM_ID, index: 1, identity: null, eligible: true },
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
    expect(link.getAttribute("href")).toBe(`#buddies/models/${encodeURIComponent(MEDIUM_ID)}`);
    await user.click(link);
    expect(window.location.hash).toBe(`#buddies/models/${encodeURIComponent(MEDIUM_ID)}`);
    await waitFor(() => expect(modelButton("claude / anthropic / claude-sonnet-5").getAttribute("aria-pressed")).toBe("true"));
  });

  it("says that there is no Router yet and how to add one, and draws no list", async () => {
    const f = fixture(snapshot());
    await openRoles(f);
    expect(screen.getByText("还没有 Router")).toBeTruthy();
    expect(screen.getByText(/点“添加”/)).toBeTruthy();
    expect(queryRouterList()).toBeNull();
    expect(within(rolesRegion()).queryByRole("status")).toBeNull();
  });

  it("sends no command when the view is entered", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(f.command).not.toHaveBeenCalled();
  });

  it("keeps a large decision history out of the roles page", async () => {
    const decision = (id: string) => ({
      decisionId: id, kind: "select" as const, status: "completed", task: "不应出现在配置页的长任务说明。".repeat(200),
      profileId: MEDIUM_ID, selectedProfile: null, tableRevision: 3, configurationRevision: 2, reason: "", evidence: [],
      createdAt: "2026-09-24T08:00:00Z", runId: id, decisionModel: null, requested: {}, input: {}, inputSha256: id, output: {},
    });
    const f = fixture(snapshot({ routers: [MEDIUM_ID], extra: { decisions: Array.from({ length: 50 }, (_, i) => decision(`history-${i}`)) } as unknown as Partial<Snapshot> }));
    await openRoles(f);
    expect(budgetOption("标准").getAttribute("aria-checked")).toBe("true");
    expect(screen.queryByRole("checkbox", { name: "自动采纳常规整理结果" })).toBeNull();
    expect(screen.queryByRole("heading", { name: "最近决策" })).toBeNull();
    expect(screen.queryByRole("button", { name: "请求整理" })).toBeNull();
    expect(screen.queryByText(/不应出现在配置页的长任务说明/)).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("what the list says needs the user", () => {
  const note = () => within(section("列表")).queryByRole("status");

  it("says nothing while a buddy that can serve holds the role", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(note()).toBeNull();
  });

  it("says that no buddy of the list can serve when the service names none", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ available: false, reasonCode: "router-skip-window", currentRouterProfileId: null }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("列表里没有一个 buddy 现在能担任 Router。");
  });

  it("names the first buddy and why the default mode refuses it, in the words of the refusal", async () => {
    const f = fixture(snapshot({ routers: [SOL_ID, MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe(`列表第一项 ${SOL}：所属 Harness 尚不具备本地只读路由资格。`);
  });

  it("says that the first buddy is no longer in the catalogue", async () => {
    const f = fixture(snapshot({ routers: ["gone:p:m:e"], routingHealth: health({ currentRouterProfileId: "gone:p:m:e" }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("列表第一项 gone:p:m:e：已不在目录中。");
  });

  it("says that routing keeps failing, without calling it unavailable", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID, available: true, failureCount: 4, consecutiveFailures: 4 }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("路由连续失败 4 次。");
  });

  it("says that the Router is unavailable, with the recorded code", async () => {
    const f = fixture(snapshot({ routers: [], routingHealth: health({ available: false, reasonCode: "ROUTER_UNAVAILABLE", currentRouterProfileId: null }) }));
    await openRoles(f);
    expect(note()!.textContent).toBe("Router 当前不可用（ROUTER_UNAVAILABLE）。");
  });

  it("is worked out from the draft: the first buddy is named as soon as the default mode refuses it, and not once it does not", async () => {
    const f = fixture(snapshot({ routers: [FLASH_ID], configuration: { defaultRoutingMode: "fast" }, routingHealth: health({ currentRouterProfileId: FLASH_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    expect(note()).toBeNull();
    await user.click(modeOption("审阅"));
    expect(note()!.textContent).toBe(`列表第一项 ${FLASH}：所属 Harness 尚不具备本地只读路由资格。`);
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
    await screen.findByText("已发布新版本");
    expect(f.published).toHaveLength(1);
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [MEDIUM_ID] } });
  });

  it("adds after the buddies already in the list and leaves their order", async () => {
    const f = fixture(snapshot({ routers: [FLASH_ID, MEDIUM_ID], routingHealth: health({ currentRouterProfileId: FLASH_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    await addRouter(user, PENDING);
    expect(listed()).toEqual([FLASH, MEDIUM, PENDING]);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [FLASH_ID, MEDIUM_ID, PENDING_ID] } });
  });

  it("adds a model that waits to be confirmed as readily as any other that is enabled", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    expect(choice(PENDING).getAttribute("aria-disabled")).toBeNull();
    await user.click(choice(PENDING));
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [PENDING_ID] } });
  });

  it("lists the buddies outside the list, those that can serve in the default mode first and the rest with their reason", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    const rows = choices();
    expect(rows.map(row => row.textContent)).toEqual([
      PENDING,
      `${HIGH}该档位未启用：先打开它的开关`,
      `${FLASH}所属 Harness 尚不具备本地只读路由资格`,
      `${SOL}所属 Harness 尚不具备本地只读路由资格`,
      `${GLM}该档位当前不可用`,
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
    expect(within(choice(FLASH)).getByText("所属 Harness 尚不具备本地只读路由资格")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "取消" }));
    await user.click(modeOption("快速"));
    await user.click(addButton());
    expect(choice(FLASH).getAttribute("aria-disabled")).toBeNull();
    expect(choice(SOL).getAttribute("aria-disabled")).toBe("true");
    expect(within(choice(SOL)).getByText("所属 Harness 尚不支持无工具路由调用")).toBeTruthy();
  });

  it("does not add a buddy whose Harness is not eligible in review mode, says why in its row and changes nothing", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(addButton());
    await user.click(choice(SOL));
    // The dialog stays open on the reason, and the draft is untouched.
    expect(addDialog()).toBeTruthy();
    expect(within(choice(SOL)).getByText("所属 Harness 尚不具备本地只读路由资格")).toBeTruthy();
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
    expect(within(choice(HIGH)).getByText("该档位未启用：先打开它的开关")).toBeTruthy();
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
    expect(within(choice(GLM)).getByText("该档位当前不可用")).toBeTruthy();
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
    expect(choices().map(row => row.textContent)).toEqual([`${FLASH}所属 Harness 尚不具备本地只读路由资格`]);
    await user.clear(search);
    await user.type(search, "high");
    expect(choices().map(row => row.textContent)).toEqual([`${HIGH}该档位未启用：先打开它的开关`]);
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
  const three = () => snapshot({ routers: [MEDIUM_ID, FLASH_ID, PENDING_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) });

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
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [FLASH_ID, MEDIUM_ID, PENDING_ID] } });
  });

  it("moves a buddy up and publishes the whole list in the new order", async () => {
    const f = fixture(three());
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(moveUp(PENDING));
    expect(listed()).toEqual([MEDIUM, PENDING, FLASH]);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [MEDIUM_ID, PENDING_ID, FLASH_ID] } });
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
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [MEDIUM_ID, PENDING_ID] } });
  });

  it("publishes an empty list when the last buddy is removed, and lets the draft go to bring it back", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(remove(MEDIUM));
    expect(queryRouterList()).toBeNull();
    expect(screen.getByText("还没有 Router")).toBeTruthy();
    await user.click(within(screen.getByRole("region", { name: "未保存的修改" })).getByRole("button", { name: "放弃" }));
    await screen.findByText("草稿已放弃");
    expect(listed()).toEqual([MEDIUM]);
    expect(f.command).not.toHaveBeenCalled();
    await user.click(remove(MEDIUM));
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [] } });
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
      routingHealth: health({ currentRouterProfileId: MEDIUM_ID, routers: [
        { profileId: MEDIUM_ID, index: 0, identity: null, eligible: true },
        { profileId: SOL_ID, index: 1, identity: null, eligible: false, code: "router-not-eligible" },
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
  it("leaves every control of the section out of use when the settings need an upgrade, says so, and changes nothing", async () => {
    const f = fixture(snapshot({ configuration: null, routingHealth: health() }));
    const user = userEvent.setup();
    await openRoles(f);
    const alert = within(rolesRegion()).getByRole("alert");
    expect(alert.textContent).toBe("Router 设置需要升级：请先升级设置");
    // No list and no empty state: nothing is known of the list.
    expect(queryRouterList()).toBeNull();
    expect(screen.queryByText("还没有 Router")).toBeNull();
    const reason = "Router 设置需要升级后才能修改";
    expect(addButton().getAttribute("aria-disabled")).toBe("true");
    expect(addButton().getAttribute("aria-description")).toBe(reason);
    for (const option of [modeOption("快速"), modeOption("审阅"), budgetOption("简要"), budgetOption("标准"), budgetOption("深入")]) {
      expect(option.getAttribute("aria-disabled")).toBe("true");
      expect(option.getAttribute("aria-checked")).toBe("false");
    }
    expect(modeOption("快速").getAttribute("aria-description")).toContain(reason);
    expect(intervalInput().getAttribute("aria-disabled")).toBe("true");
    expect(intervalInput().getAttribute("aria-description")).toBe(reason);
    expect(intervalInput().getAttribute("aria-invalid")).toBeNull();
    expect(within(section("重试间隔")).queryByText(/需要 1–2147483647/)).toBeNull();
    await user.click(addButton());
    await user.click(modeOption("快速"));
    await user.click(budgetOption("深入"));
    await user.type(intervalInput(), "5");
    expect(queryAddDialog()).toBeNull();
    expect(queryDraftBar()).toBeNull();
    // The records are read apart from the settings and stay.
    expect(within(section("记录")).getByText("失败")).toBeTruthy();
  });

  it("does not show a retained draft of the list as usable once the settings need an upgrade", () => {
    const state = snapshot({ routers: [MEDIUM_ID], configuration: null, routingHealth: health() });
    const retained = makeDraft(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    const update = vi.fn();
    const editor = { view: { ...state, ...retained }, draft: retained, editing: true, sessionWritable: true, update } as unknown as Editor;
    render(<TooltipProvider><RolesView snapshot={state} editor={editor} active location={{ view: "roles" }} onNavigate={vi.fn()} hidden={false} /></TooltipProvider>);
    expect(within(rolesRegion()).getByRole("alert").textContent).toContain("请先升级设置");
    expect(queryRouterList()).toBeNull();
    expect(screen.queryByText("当前")).toBeNull();
    expect(unsavedIn("列表")).toHaveLength(0);
    expect(budgetOption("标准").getAttribute("aria-disabled")).toBe("true");
    expect(update).not.toHaveBeenCalled();
  });

  it("keeps the preferences of the catalogue editable and publishes them without the Router settings", async () => {
    const f = fixture(snapshot({ configuration: null }));
    const user = userEvent.setup();
    window.location.hash = "#buddies/models";
    render(<App suppliedApi={f.api} />);
    await screen.findByRole("table", { name: "模型" });
    await selectModel(user, "claude / anthropic / claude-sonnet-5", "Claude Code");
    await user.click(defaultPreference("优先"));
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(f.published).toHaveLength(1);
    expect(f.published[0]).not.toHaveProperty("configuration");
    expect(f.published[0].familyPreferenceChanges).toEqual([
      { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5", mode: "prefer", reason: "" },
    ]);
  });

  it("leaves the controls out of use in a session that cannot write, with the reason, and still shows the records", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID, FLASH_ID], canWrite: false, routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    const user = userEvent.setup();
    await openRoles(f);
    const reason = "登录已失效，重新登录后可以修改";
    for (const control of [addButton(), moveDown(MEDIUM), moveUp(FLASH), remove(MEDIUM), intervalInput()]) {
      expect(control.getAttribute("aria-disabled")).toBe("true");
      expect(control.getAttribute("aria-description")).toBe(reason);
    }
    expect(budgetOption("简要").getAttribute("aria-disabled")).toBe("true");
    expect(modeOption("快速").getAttribute("aria-description")).toContain(reason);
    await user.click(addButton());
    await user.click(remove(MEDIUM));
    await user.click(moveDown(MEDIUM));
    await user.click(modeOption("快速"));
    await user.click(budgetOption("简要"));
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
    saving.resolve({ published: true, tableRevision: 5 });
    await screen.findByText("已发布新版本");
    expect(addButton().getAttribute("aria-disabled")).toBeNull();
  });
});

describe("the settings of the role", () => {
  it("chooses the default mode, tells where the data goes in each choice and publishes the one setting", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    expect(modeOption("审阅").getAttribute("aria-checked")).toBe("true");
    expect(modeOption("快速").getAttribute("aria-checked")).toBe("false");
    // What is sent, and to whom, is said where the choice is made.
    expect(modeOption("快速").getAttribute("aria-description")).toBe("需要 Router 判断时，任务的文字会发给 Router 的模型提供方");
    const review = modeOption("审阅").getAttribute("aria-description")!;
    expect(review).toContain("任务的文字会发给 Router 的模型提供方");
    expect(review).toContain("还会发送一份冻结的代码副本");
    expect(review).toContain("DSH 与 ZCode 没有系统沙盒，不能保证阻止副本以外的读取或外传");
    expect(within(section("默认模式")).getByText("默认模式").getAttribute("aria-description")).toBe("只有一个候选时，由程序直接选定，不经过 Router");
    expect(unsavedIn("默认模式")).toHaveLength(0);
    await user.click(modeOption("快速"));
    expect(modeOption("快速").getAttribute("aria-checked")).toBe("true");
    expect(unsavedIn("默认模式")).toHaveLength(1);
    expect(unsavedIn("列表")).toHaveLength(0);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { defaultRoutingMode: "fast" } });
  });

  it("chooses the review budget, writes the limits recorded for the level and publishes the one setting", async () => {
    const f = fixture(snapshot({ configuration: { routingBudgetLimits: { preset: "standard", timeoutSeconds: 300, toolCalls: 24, bytesRead: 524288 } } }));
    const user = userEvent.setup();
    await openRoles(f);
    expect(["简要", "标准", "深入"].map(name => budgetOption(name).getAttribute("aria-checked"))).toEqual(["false", "true", "false"]);
    const budget = within(section("审阅预算"));
    expect(budget.getByText("300 秒")).toBeTruthy();
    expect(budget.getByText("24 次工具调用")).toBeTruthy();
    expect(budget.getByText("524,288 字节")).toBeTruthy();
    expect(budget.getByText("审阅预算").getAttribute("aria-description")).toBe("审阅预算只用于之后的路由，快速路由固定 60 秒");
    // Limits are recorded for one level only; another level has none to show.
    await user.click(budgetOption("简要"));
    expect(budgetOption("简要").getAttribute("aria-checked")).toBe("true");
    expect(budget.queryByText("300 秒")).toBeNull();
    expect(unsavedIn("审阅预算")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routingBudget: "brief" } });
  });

  it("writes no limits when none are recorded", async () => {
    const f = fixture(snapshot());
    await openRoles(f);
    expect(within(section("审阅预算")).queryByText(/秒|工具调用|字节/)).toBeNull();
  });

  it("sets the retry interval in seconds, with the explanation on its name, and publishes the one setting", async () => {
    const f = fixture(snapshot());
    const user = userEvent.setup();
    await openRoles(f);
    expect(intervalInput()).toHaveProperty("value", "600");
    expect(intervalInput().getAttribute("aria-invalid")).toBeNull();
    expect(within(section("重试间隔")).getByText("秒")).toBeTruthy();
    expect(within(section("重试间隔")).getByText("重试间隔").getAttribute("aria-description"))
      .toBe("一个 Router 没有给出答案之后，过了这段时间才会再用它，其间列表里后面的项补位");
    await user.clear(intervalInput());
    await user.type(intervalInput(), String(interval));
    expect(intervalInput()).toHaveProperty("value", String(interval));
    expect(unsavedIn("重试间隔")).toHaveLength(1);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
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
    expect(within(section("重试间隔")).getByText("需要 1–2147483647 的整数")).toBeTruthy();
    // Leaving the field does not publish an earlier valid prefix.
    await user.tab();
    expect(intervalInput()).toHaveProperty("value", value);
    await user.click(saveButton());
    expect(f.published).toHaveLength(0);
    expect(f.operations).not.toContain("evaluation_write_begin");
    await user.clear(intervalInput());
    await user.type(intervalInput(), "7");
    expect(intervalInput().getAttribute("aria-invalid")).toBeNull();
    expect(within(section("重试间隔")).queryByText(/需要 1–2147483647/)).toBeNull();
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerRetryIntervalSeconds: 7 } });
  });

  it("marks a section only while its own setting differs from the published one, and publishes every changed setting together", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    const user = userEvent.setup();
    await openRoles(f);
    await user.click(modeOption("快速"));
    await user.click(budgetOption("深入"));
    await user.click(remove(MEDIUM));
    expect([unsavedIn("列表").length, unsavedIn("默认模式").length, unsavedIn("审阅预算").length, unsavedIn("重试间隔").length]).toEqual([1, 1, 1, 0]);
    await user.click(modeOption("审阅"));
    expect([unsavedIn("列表").length, unsavedIn("默认模式").length, unsavedIn("审阅预算").length]).toEqual([1, 0, 1]);
    await user.click(saveButton());
    await screen.findByText("已发布新版本");
    expect(patchOf(f.published[0])).toEqual({ configuration: { routerProfileIds: [], routingBudget: "deep" } });
  });
});

describe("the records of routing", () => {
  const records = () => within(section("记录"));
  /** One fact of the window: its name and its number, as they read. */
  const fact = (name: string) => records().getByText(name).closest("dl")!.textContent;
  const time = (iso: string) => formatShortDateTime(Date.parse(iso));

  it("says that nothing is recorded when the snapshot carries no health facts, and writes no failures", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID] }));
    await openRoles(f);
    expect(records().getByText("未记录")).toBeTruthy();
    expect(records().queryByText("失败")).toBeNull();
    expect(records().queryByRole("table")).toBeNull();
  });

  it("says that there are no records for an empty window, rather than zero failures", async () => {
    const f = fixture(snapshot({ routingHealth: health({ sampleCount: 0, lastSuccessAt: null }) }));
    await openRoles(f);
    expect(records().getByText("暂无记录")).toBeTruthy();
    expect(records().queryByText("失败")).toBeNull();
    expect(records().queryByText("最近成功")).toBeNull();
  });

  it("writes the window's counts as names with numbers, leaves out what is zero, and does not work out successes", async () => {
    const f = fixture(snapshot({ routingHealth: health({
      sampleCount: 18, failureCount: 4, consecutiveFailures: 2, abstentionCount: 3, cancelledCount: 1, staleCount: 2,
      budgetExhaustedCount: 0, boundsRejectedCount: 2,
      lastSuccessAt: "2026-09-25T08:02:00.000Z", lastSuccessDecisionId: "dec-41",
    }) }));
    await openRoles(f);
    expect(fact("失败")).toBe("失败4 / 18");
    expect(fact("连续失败")).toBe("连续失败2");
    expect(fact("弃权")).toBe("弃权3");
    expect(fact("取消")).toBe("取消1");
    expect(fact("过期")).toBe("过期2");
    expect(fact("越界")).toBe("越界2");
    expect(fact("最近成功")).toBe(`最近成功${time("2026-09-25T08:02:00.000Z")}`);
    // A zero, and a count the service does not carry, are not written; nor is a success rate.
    expect(records().queryByText("预算耗尽")).toBeNull();
    expect(records().queryByText("输入变化")).toBeNull();
    expect(records().queryByText(/成功 \d/)).toBeNull();
    // Abstentions, cancellations and stale results are not failures; this is the hint of the word.
    expect(records().getByText("失败").getAttribute("aria-description")).toBe("弃权、取消与过期不算失败");
  });

  it("writes the recorded outcomes of the Router beside the failures: budget, bounds and changed input", async () => {
    const f = fixture(snapshot({ routingHealth: health({
      sampleCount: 12, failureCount: 2, budgetExhaustedCount: 3, boundsRejectedCount: 2, inputChangedCount: 1, lastSuccessAt: null,
    }) }));
    await openRoles(f);
    expect(fact("失败")).toBe("失败2 / 12");
    expect(fact("预算耗尽")).toBe("预算耗尽3");
    expect(fact("越界")).toBe("越界2");
    expect(fact("输入变化")).toBe("输入变化1");
    expect(fact("最近成功")).toBe("最近成功无");
    expect(records().queryByText("连续失败")).toBeNull();
  });

  it("writes the reason code of a Router that is unavailable, as recorded", async () => {
    const f = fixture(snapshot({ routingHealth: health({ available: false, reasonCode: "router-skip-window", sampleCount: 3, failureCount: 3, consecutiveFailures: 3 }) }));
    await openRoles(f);
    expect(fact("不可用")).toBe("不可用router-skip-window");
  });

  it("gives each buddy of the list a line of its own records, with a dash where nothing is recorded", async () => {
    const f = fixture(snapshot({
      routers: [MEDIUM_ID, FLASH_ID],
      routingHealth: health({ currentRouterProfileId: MEDIUM_ID, routers: [
        { profileId: MEDIUM_ID, index: 0, eligible: true, identity: { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5", effort: "medium" },
          answeredCount: 7, noAnswerCount: 0, consecutiveNoAnswers: 0, lastAnsweredAt: "2026-09-27T07:00:00.000Z",
          lastError: { code: "router-out-of-bounds", at: "2026-09-26T06:30:00.000Z" } },
        { profileId: FLASH_ID, index: 1, eligible: true, identity: null },
      ] }),
    }));
    await openRoles(f);
    const table = records().getByRole("table", { name: "各 Router 的记录" });
    expect(within(table).getAllByRole("columnheader").map(cell => cell.textContent))
      .toEqual(["Buddy", "应答", "未应答", "连续未应答", "最近应答", "最近错误"]);
    const [, first, second] = within(table).getAllByRole("row");
    expect(within(first).getAllByRole("cell").map(cell => cell.textContent)).toEqual([
      MEDIUM, "7", "0", "0", time("2026-09-27T07:00:00.000Z"), `router-out-of-bounds${time("2026-09-26T06:30:00.000Z")}`,
    ]);
    // Nothing recorded is a dash, never a zero; the buddy without a recorded identity is named by its id.
    expect(within(second).getAllByRole("cell").map(cell => cell.textContent)).toEqual([FLASH_ID, "—", "—", "—", "—", "—"]);
  });

  it("lists the latest failures with their time, their code and the micro task, as recorded", async () => {
    const f = fixture(snapshot({ routingHealth: health({
      sampleCount: 18, failureCount: 3, consecutiveFailures: 3,
      recentFailures: [
        { decisionId: "dec-48", runId: "run-a", at: "2026-09-27T09:41:00.000Z", code: "call-failed" },
        { decisionId: "dec-47", runId: null, at: "2026-09-27T09:12:00.000Z", code: "needs-host" },
      ],
    }) }));
    await openRoles(f);
    const table = records().getByRole("table", { name: "最近的失败" });
    expect(within(table).getAllByRole("columnheader").map(cell => cell.textContent)).toEqual(["时间", "结果", "微任务"]);
    const rows = within(table).getAllByRole("row").slice(1).map(row => within(row).getAllByRole("cell").map(cell => cell.textContent));
    expect(rows).toEqual([[time("2026-09-27T09:41:00.000Z"), "call-failed", "run-a"], [time("2026-09-27T09:12:00.000Z"), "needs-host", "未记录"]]);
    // The code is set in monospace; no title is invented for a micro task.
    expect(within(table).getByText("call-failed").tagName).toBe("CODE");
    expect(records().queryByRole("button")).toBeNull();
    expect(f.command).not.toHaveBeenCalled();
  });

  it("writes no table of failures when none are recorded", async () => {
    const f = fixture(snapshot({ routingHealth: health() }));
    await openRoles(f);
    expect(records().queryByRole("table", { name: "最近的失败" })).toBeNull();
  });
});

describe("addresses of the view", () => {
  it("focuses the line of the buddy serving now from #buddies/roles/current", async () => {
    const f = fixture(snapshot({ routers: [SOL_ID, MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f, "#buddies/roles/current");
    await waitFor(() => expect(document.activeElement).toBe(line(MEDIUM)));
  });

  it("focuses the list when nobody serves, from #buddies/roles/current", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ available: false, currentRouterProfileId: null }) }));
    await openRoles(f, "#buddies/roles/current");
    await waitFor(() => expect(document.activeElement).toBe(section("列表")));
  });

  it("focuses the records from #buddies/roles/health", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f, "#buddies/roles/health");
    await waitFor(() => expect(document.activeElement).toBe(section("记录")));
  });

  it("opens the view without moving focus from #buddies/roles", async () => {
    const f = fixture(snapshot({ routers: [MEDIUM_ID], routingHealth: health({ currentRouterProfileId: MEDIUM_ID }) }));
    await openRoles(f);
    expect(rolesRegion().contains(document.activeElement)).toBe(false);
  });
});
