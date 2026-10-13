import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import i18n from "i18next";
import { createElement } from "react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { App } from "../App";
import type { HarnessHealth } from "../api";
import type { Buddy } from "../types";
import { fixture, health, snapshot } from "./roles.testing";

/**
 * The Buddy page and the bar above it, read in English: nothing a reader sees,
 * hears or is told on hover, not even in a dialog, is Chinese. Every attribute
 * that a screen reader or a tooltip takes its words from is read with the text.
 * The data here is English, because what the board records is its own matter;
 * what the console words itself is what this reads. The messages of the data and
 * editing layer (`api`, `draft`, `policy`, `use-editor`, `console-session`) are
 * the console's own words as well, but they sit outside what this batch changes,
 * so no scenario here raises one: the audit record lists them.
 */

const CJK = /[　-〿一-鿿＀-￯]/;
const ATTRIBUTES = ["aria-label", "aria-description", "placeholder", "title", "alt", "aria-valuetext"];

/** Everything on the page, in the words it is read in, that is Chinese. */
function chinese(): string[] {
  const found: string[] = [];
  for (const element of document.body.querySelectorAll("*")) {
    for (const name of ATTRIBUTES) {
      const value = element.getAttribute(name);
      if (value && CJK.test(value)) found.push(`<${element.tagName.toLowerCase()} ${name}> ${value}`);
    }
  }
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    if (CJK.test(node.textContent ?? "")) found.push(`text: ${node.textContent}`);
  }
  return found;
}

const AT = "2026-09-29T09:30:00.000Z";
const LATER = "2026-09-29T13:00:00.000Z";
const account = (harness: string) => ({
  harness, status: "ready", accountType: null, checkedAt: AT, reasonCode: null, guidance: null,
});
const harnesses = [
  { harness: "dsh", status: "ready", available: true, revision: 1, manualPath: null, executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH", checkedAt: AT, candidates: [],
    account: account("dsh"), billingByProvider: { "deepseek-official": { kind: "metered", source: "native", observedAt: AT } },
    quota: { source: "dsh-native", observedAt: AT, balanceZero: true, windows: [] } },
  { harness: "codex", status: "ready", available: true, revision: 2, manualPath: null, executable: "/opt/bin/codex", version: "0.157.0", source: "PATH", checkedAt: AT, candidates: [],
    account: account("codex"),
    readOnlyStructured: { implemented: true, eligible: false, systemSandbox: false, sameAttemptContinuation: false, reasonCode: "READ_ONLY_RESOURCE_UNAVAILABLE", reason: "The local read-only resources are unavailable" },
    quota: { source: "codex-native", observedAt: AT, provider: "openai", windows: [{ name: "5h", usedPercent: 93, resetsAt: LATER }, { name: "weekly", usedPercent: 41, resetsAt: null }] },
    quotaRouting: [{ provider: "openai", limitId: "5h", code: "HARNESS_QUOTA_EXHAUSTED", source: "codex-native", observedAt: AT, resetsAt: null, blocked: true,
      retry: { eligibleAt: LATER, open: false, pendingManual: false, manualAt: null, consumedAt: null, consumedBy: null } }] },
  { harness: "claude", status: "login-required", available: false, revision: 3, manualPath: "/home/user/bin/claude", source: "manual", checkedAt: AT,
    reasonCode: "HARNESS_LOGIN_REQUIRED", remedy: "Log in to Claude Code, then check again.",
    candidates: [{ path: "/home/user/bin/claude", source: "manual", status: "failed", reasonCode: "handshake_failed" }] },
  { harness: "zcode", status: "missing", available: false, revision: 1, manualPath: null, checkedAt: AT,
    reasonCode: "HARNESS_NOT_FOUND", remedy: "Install the ZCode CLI, or give the absolute path of its executable.",
    candidates: [{ path: "/opt/homebrew/bin/zcode", source: "Homebrew", status: "missing", reasonCode: "not_found" }] },
] as unknown as HarnessHealth[];

function buddy(harness: string, provider: string, model: string, effort: string, extra: Partial<Buddy> = {}): Buddy {
  return { buddyId: `${harness}:${provider}:${model}:${effort}`, label: `${model} · ${effort}`, harness, provider, model, effort,
    available: true, enabled: false, capabilities: [`execution:${harness}`, "routing:fast", "decision"], contextWindow: null, description: "", source: "catalog:fixture", ...extra };
}
const buddies = [
  buddy("dsh", "deepseek-official", "deepseek-flash", "off", { enabled: true }),
  buddy("dsh", "deepseek-official", "deepseek-flash", "max"),
  buddy("codex", "openai", "gpt-6-sol", "medium", { enabled: true }),
  buddy("codex", "openai", "gpt-6-sol", "low", { available: false, unavailableReason: "HARNESS_UNHEALTHY" }),
  buddy("claude", "anthropic", "claude-sonnet-5", "medium", { available: false, unavailableReason: "provider paused" }),
  buddy("zcode", "zhipu", "glm-5", "default", { available: false, unavailableReason: "not present in the latest complete native discovery" }),
];

function world(canWrite = true) {
  return fixture(snapshot({
    routers: [buddies[0].buddyId], canWrite,
    routingHealth: health({ consecutiveFailures: 2, failureCount: 3, abstentionCount: 1, boundsRejectedCount: 1,
      recentFailures: [{ decisionId: "decision-1", runId: "run-1", at: AT, code: "routing-bounds-rejected" }] }),
    extra: { buddies, modelServiceConcurrency: [{ harness: "dsh", provider: "deepseek-official", model: "deepseek-flash", limit: 2, active: 1 }], harnesses, unavailableBuddyCount: 3 },
  }));
}

async function openCatalog(f: ReturnType<typeof world>, hash = "#buddies") {
  window.location.hash = hash;
  render(createElement(App, { suppliedApi: f.api }));
  return await screen.findByRole("region", { name: "Catalog" });
}

beforeEach(async () => { await i18n.changeLanguage("en"); });
afterEach(async () => {
  cleanup();
  window.localStorage.clear();
  window.location.hash = "";
  await i18n.changeLanguage("zh");
});

describe("the catalogue in English", () => {
  it("says nothing in Chinese through sections, account blocks, model rows, drawers and dialogs", async () => {
    const user = userEvent.setup();
    const catalog = await openCatalog(world());
    expect(chinese()).toEqual([]);
    // Every section open on its account, location and quota; every section's models unfolded.
    for (const fold of [...catalog.querySelectorAll<HTMLElement>("[data-slot=fold-handle]")]) if (fold.getAttribute("aria-expanded") === "false") await user.click(fold);
    for (const summary of [...catalog.querySelectorAll<HTMLElement>("[data-slot=account-summary]")]) await user.click(summary);
    expect(catalog.querySelectorAll("[data-slot=harness-details]").length).toBeGreaterThan(0);
    expect(chinese()).toEqual([]);
    // Every model row open in turn, with every effort of its strip selected.
    for (const row of [...catalog.querySelectorAll<HTMLElement>("[data-slot=model-row] > button")]) {
      await user.click(row);
      for (const stop of [...document.querySelectorAll<HTMLElement>("[data-slot=buddy-stop]")]) {
        await user.click(stop);
        expect(chinese(), `${row.getAttribute("aria-label")} / ${stop.getAttribute("aria-label")}`).toEqual([]);
      }
    }
    // The model's own settings and the buddy's: an edit raises the save bar, with its marks.
    await user.click(within(document.querySelector<HTMLElement>("[data-slot=model-drawer]")!).getAllByRole("switch")[0]);
    expect(screen.getByRole("region", { name: "Unsaved changes" })).toBeTruthy();
    expect(chinese()).toEqual([]);
    // The dialogs the sections open.
    for (const name of [/^Set path$/]) {
      await user.click(within(catalog).getAllByRole("button", { name })[0]);
      await screen.findByRole("dialog");
      expect(chinese(), String(name)).toEqual([]);
      await user.keyboard("{Escape}");
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    }
  });

  it("says nothing in Chinese for a session that cannot write: every refusal and lock", async () => {
    const user = userEvent.setup();
    const catalog = await openCatalog(world(false));
    await user.click(within(catalog).getByRole("button", { name: "Discover models" }));
    expect(await within(catalog).findByText(/login has expired/i)).toBeTruthy();
    await user.click(within(catalog).getByRole("button", { name: "Check all" }));
    for (const summary of [...catalog.querySelectorAll<HTMLElement>("[data-slot=account-summary]")]) await user.click(summary);
    await user.click(catalog.querySelector<HTMLElement>("[data-slot=model-row] > button")!);
    expect(chinese()).toEqual([]);
    expect(within(catalog).queryByRole("button", { name: "Manage account" })).toBeNull();
  });

  it("says nothing in Chinese in the save bar and its marks while a draft is held", async () => {
    const user = userEvent.setup();
    const catalog = await openCatalog(world());
    await user.click(catalog.querySelector<HTMLElement>("[data-slot=model-row] > button")!);
    await user.click(within(document.querySelector<HTMLElement>("[data-slot=model-drawer]")!).getAllByRole("switch")[0]);
    const bar = screen.getByRole("region", { name: "Unsaved changes" });
    expect(within(bar).getByRole("button", { name: "Save" })).toBeTruthy();
    expect(within(bar).getByRole("button", { name: "Discard" })).toBeTruthy();
    expect(chinese()).toEqual([]);
  });
});

describe("the roles in English", () => {
  it("says nothing in Chinese in the list, its notes, its records and the dialog that adds", async () => {
    const user = userEvent.setup();
    const f = world();
    await openCatalog(f);
    await user.click(screen.getByRole("link", { name: /^Roles/ }));
    const roles = await screen.findByRole("region", { name: "Roles" });
    expect(chinese()).toEqual([]);
    await user.click(within(roles).getByRole("button", { name: "Add" }));
    const dialog = await screen.findByRole("dialog");
    // The buddies that cannot serve in the default mode keep the reason in the page's words.
    expect(within(dialog).getAllByText(/unavailable now|not enabled/i).length).toBeGreaterThan(0);
    expect(chinese()).toEqual([]);
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The first buddy of the list refused in the mode: the note names the reason.
    expect(chinese()).toEqual([]);
  });

  it("says in English why the first Router is refused", async () => {
    const f = fixture(snapshot({ routers: [buddies[3].buddyId], extra: { buddies, harnesses } }));
    window.location.hash = "#buddies/roles";
    render(createElement(App, { suppliedApi: f.api }));
    const roles = await screen.findByRole("region", { name: "Roles" });
    expect(within(roles).getByText(/This reasoning effort is unavailable now/)).toBeTruthy();
    expect(chinese()).toEqual([]);
  });
});

describe("the bar above the page in English", () => {
  it("names its tabs, its state and what a session that cannot save says, in English", async () => {
    const f = world(false);
    await openCatalog(f);
    const bar = screen.getByRole("banner");
    expect(within(bar).getByRole("link", { name: /^Buddies/ })).toBeTruthy();
    expect(chinese()).toEqual([]);
  });
});
