import { render, screen, waitFor, within } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";
import { createElement } from "react";
import { expect, vi } from "vitest";
import { App } from "../App";
import type { ConsoleApi, HarnessHealth } from "../api";
import type { Configuration, Buddy, RoutingHealth, Snapshot } from "../types";

/**
 * How the tests build the roles view's world and find things in it. They read
 * what a user reads: the section by its name, a line of the list by the four
 * parts of its buddy, a button by what it does to that buddy.
 */
type User = ReturnType<typeof userEvent.setup>;

export const FLASH_ID = "dsh:deepseek-official:deepseek-flash:max";
export const MEDIUM_ID = "claude:anthropic:claude-sonnet-5:medium";
export const HIGH_ID = "claude:anthropic:claude-sonnet-5:high";
export const SOL_ID = "codex:openai:gpt-6-sol:medium";
export const GLM_ID = "zcode:zhipu:glm-5:default";
export const PENDING_ID = "claude:anthropic:claude-haiku-4:low";
export const FLASH = "dsh / deepseek-official / deepseek-flash / max";
export const MEDIUM = "claude / anthropic / claude-sonnet-5 / medium";
export const HIGH = "claude / anthropic / claude-sonnet-5 / high";
export const SOL = "codex / openai / gpt-6-sol / medium";
export const GLM = "zcode / zhipu / glm-5 / default";
export const PENDING = "claude / anthropic / claude-haiku-4 / low";

function buddy(buddyId: string, capabilities: string[], extra: Partial<Buddy> = {}): Buddy {
  const [harness, provider, model, effort] = buddyId.split(":");
  return { buddyId, label: `${model} · ${effort}`, harness, provider, model, effort, available: true, enabled: true,
    capabilities, contextWindow: null, description: "", source: "catalog:fixture", ...extra };
}

/**
 * A catalogue with one buddy of each kind: two that can route in both modes (one
 * of them off), one that can only route fast, one whose harness cannot route
 * at all, one that is gone and one whose model is waiting to be confirmed.
 */
export function buddies(): Buddy[] {
  const routes = ["execution:claude", "routing:fast", "decision"];
  return [
    buddy(MEDIUM_ID, routes),
    buddy(HIGH_ID, routes, { enabled: false }),
    buddy(FLASH_ID, ["execution:dsh", "routing:fast"]),
    buddy(SOL_ID, ["execution:codex"]),
    buddy(GLM_ID, ["execution:zcode", "decision"], { available: false, unavailableReason: "本机未检测到 zcode CLI" }),
    buddy(PENDING_ID, routes, { catalogStatus: "pending", pendingSince: "2026-09-29T08:00:00.000Z" }),
  ];
}

/** Health facts of a window that went well; `routers` and the rest are given by the test. */
export function health(extra: Partial<RoutingHealth> = {}): RoutingHealth {
  return {
    windowSize: 20, sampleCount: 5, failureCount: 0, consecutiveFailures: 0,
    abstentionCount: 0, cancelledCount: 0,
    lastSuccessAt: "2026-09-27T07:00:00.000Z", lastSuccessDecisionId: null, recentFailures: [],
    ...extra,
  };
}

export type RolesOptions = {
  routers?: string[];
  configuration?: Partial<Configuration> | null;
  routingHealth?: RoutingHealth;
  canWrite?: boolean;
  extra?: Partial<Snapshot>;
};

const harnesses: HarnessHealth[] = ["claude", "codex", "dsh", "zcode"].map(harness =>
  ({ harness, status: "ready", available: true, revision: 1, manualPath: null, checkedAt: "2026-09-29T09:30:00.000Z", candidates: [] }) as HarnessHealth);

export function snapshot({ routers = [], configuration = {}, routingHealth, canWrite = true, extra = {} }: RolesOptions = {}): Snapshot & { harnesses: HarnessHealth[] } {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite, reason: null },

    configuration: configuration === null ? null
      : { routerBuddyIds: routers, routerRetryIntervalSeconds: 600, defaultRoutingMode: "review", routingBudget: "standard", ...configuration },
    configurationError: configuration === null ? { code: "router-settings-upgrade-required", message: "请先升级设置",} : null,
    buddies: buddies(),
    preferences: [], modelServiceNotes: [],
 decisions: [],
    modelServiceConcurrency: [],
    tasks: { pendingCount: 0 },
    capabilities: { selection: true },
    routingHealth,
    harnesses,
    ...extra,
  };
}

type Reply = (params: Record<string, any>) => unknown;

/** The board behind the page: it grants a write, takes a publication into its state and refuses every other command. */
export function fixture(state: Snapshot = snapshot(), replies: Record<string, Reply> = {}) {
  const published: Record<string, any>[] = [];
  const operations: string[] = [];

  const command = vi.fn(async (operation: string, params: Record<string, any>) => {
    operations.push(operation);
    if (replies[operation]) return replies[operation](params);

    if (operation === "user_policy_publish") {
      published.push(params);
      state = { ...state,
        configuration: state.configuration && { ...state.configuration, ...(params.configuration ?? {}) } };
      return { published: true,};
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)), command, task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command, published, operations };
}

/** Opens the page at an address and waits until the roles section is drawn. */
export async function openRoles(f: ReturnType<typeof fixture>, hash = "#buddies/roles") {
  window.location.hash = hash;
  render(createElement(App, { suppliedApi: f.api }));
  await screen.findByRole("region", { name: "角色" });
}

export const rolesRegion = () => screen.getByRole("region", { name: "角色" });
export const section = (name: string) => within(rolesRegion()).getByRole("group", { name });
export const routerList = () => within(rolesRegion()).getByRole("list", { name: "Router 列表" });
export const queryRouterList = () => within(rolesRegion()).queryByRole("list", { name: "Router 列表" });
/** The four-part names of the list's buddies, in the order of the list. */
export const listed = () => (queryRouterList() ? within(routerList()).getAllByRole("listitem") : []).map(item => item.getAttribute("aria-label"));
export const line = (quad: string) => within(rolesRegion()).getByRole("listitem", { name: quad });
export const moveUp = (quad: string) => within(rolesRegion()).getByRole("button", { name: `上移 ${quad}` });
export const moveDown = (quad: string) => within(rolesRegion()).getByRole("button", { name: `下移 ${quad}` });
export const remove = (quad: string) => within(rolesRegion()).getByRole("button", { name: `移除 ${quad}` });
export const addButton = () => within(rolesRegion()).getByRole("button", { name: "添加" });
export const addDialog = () => screen.getByRole("dialog", { name: "添加 Router" });
export const queryAddDialog = () => screen.queryByRole("dialog", { name: "添加 Router" });
/** The dialog's rows: the four-part name of each buddy, with the reason where it cannot be added. */
export const choices = () => within(addDialog()).getAllByRole("button").filter(button => button.textContent !== "取消");
export const choice = (quad: string) => within(addDialog()).getByRole("button", { name: new RegExp(`^${quad.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}`) });
export const modeOption = (name: string) => within(section("默认模式")).getByRole("radio", { name });
export const budgetOption = (name: string) => within(section("审阅预算")).getByRole("radio", { name });
export const intervalInput = () => within(section("重试间隔")).getByRole("spinbutton", { name: "重试间隔秒数" });
export const saveButton = () => within(screen.getByRole("region", { name: "未保存的修改" })).getByRole("button", { name: /^(保存|重试同一保存)$/ });
export const queryDraftBar = () => screen.queryByRole("region", { name: "未保存的修改" });
/** The marks of a change that is not saved, in a section. */
export const unsavedIn = (name: string) => within(section(name)).queryAllByRole("img", { name: "未保存" });

/** Adds a buddy through the dialog. */
export async function addRouter(user: User, quad: string) {
  await user.click(addButton());
  await user.click(choice(quad));
  await waitFor(() => expect(queryAddDialog()).toBeNull());
}
