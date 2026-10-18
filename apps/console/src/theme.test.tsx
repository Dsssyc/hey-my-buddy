import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { THEME_STORAGE_KEY, applyStoredTheme } from "./theme";
import type { RuntimeVersionInfo, Snapshot } from "./types";

function snapshot(): Snapshot {
  return {
    csrfToken: "csrf", eventCursor: 0, consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    buddies: [], preferences: [], modelServiceNotes: [], decisions: [],
 modelServiceConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: {},
  };
}
function api(): ConsoleApi {
  return {
    snapshot: vi.fn(async () => snapshot()),
    // This source fixture has no recorded runtime or installation version facts.
    runtimeVersion: vi.fn(async (): Promise<RuntimeVersionInfo> => ({
      running: { mode: "source", softwareVersion: null, contractVersion: null,
        schemaVersion: null, sourceCommit: null, installedAt: null },
      installed: null,
    })),
    command: vi.fn(),
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    macroTasks: vi.fn(async () => ({ macroTasks: [], totals: { all: 0, active: 0, host: 0, review: 0 }, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** A controllable `prefers-color-scheme` media query for the system choice. */
function stubSystemScheme(dark: boolean) {
  const state = { dark };
  const listeners = new Set<() => void>();
  vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
    // A real MediaQueryList updates `matches` in place; a snapshot value would
    // freeze the resolved theme at the moment the query was created.
    get matches() { return query === "(prefers-color-scheme: dark)" ? state.dark : false; },
    media: query,
    addEventListener: (_type: string, listener: () => void) => { listeners.add(listener); },
    removeEventListener: (_type: string, listener: () => void) => { listeners.delete(listener); },
  })));
  return { state, listeners, change(next: boolean) {
    state.dark = next;
    act(() => { for (const listener of [...listeners]) listener(); });
  } };
}

describe("light and dark themes", () => {
  it("defaults to light and marks the document color scheme", async () => {
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    const dark = await screen.findByRole("radio", { name: "深色" });
    expect(dark.getAttribute("aria-checked")).toBe("false");
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(document.documentElement.style.colorScheme).toBe("light");
    await user.click(dark);
    expect(screen.getByRole("radio", { name: "深色" }).getAttribute("aria-checked")).toBe("true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("persists only the theme choice and restores it on the next page", async () => {
    const user = userEvent.setup();
    const first = render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    await user.click(await screen.findByRole("radio", { name: "深色" }));
    expect(Object.keys(window.localStorage)).toEqual([THEME_STORAGE_KEY]);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    first.unmount();
    delete document.documentElement.dataset.theme;
    expect(applyStoredTheme()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    expect((await screen.findByRole("radio", { name: "深色" })).getAttribute("aria-checked")).toBe("true");
  });

  it("follows the system scheme and keeps tracking it while the page stays open", async () => {
    const system = stubSystemScheme(true);
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    const follow = await screen.findByRole("radio", { name: "跟随系统" });
    expect(follow.getAttribute("aria-checked")).toBe("false");
    await user.click(follow);
    expect(screen.getByRole("radio", { name: "跟随系统" }).getAttribute("aria-checked")).toBe("true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
    // Only the choice is persisted, and it stays "system" rather than the
    // currently resolved colour.
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("system");
    await waitFor(() => expect(system.listeners.size).toBe(1));
    // The operating system switching to light is followed without a reload.
    system.change(false);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(document.documentElement.style.colorScheme).toBe("light");
    // A reload restores the stored system choice and resolves it again.
    cleanup();
    delete document.documentElement.dataset.theme;
    const reloaded = stubSystemScheme(true);
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    expect((await screen.findByRole("radio", { name: "跟随系统" })).getAttribute("aria-checked")).toBe("true");
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("dark"));
    await waitFor(() => expect(reloaded.listeners.size).toBe(1));
  });

  it("falls back to addListener/removeListener when the query has no addEventListener", async () => {
    const state = { dark: true };
    const added: Array<() => void> = [];
    const removed: Array<() => void> = [];
    vi.stubGlobal("matchMedia", vi.fn((query: string) => ({
      get matches() { return query === "(prefers-color-scheme: dark)" ? state.dark : false; },
      media: query,
      addListener: (listener: () => void) => { added.push(listener); },
      removeListener: (listener: () => void) => { removed.push(listener); },
    })));
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    await user.click(await screen.findByRole("radio", { name: "跟随系统" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    await waitFor(() => expect(added).toHaveLength(1));
    // The deprecated listener pair still tracks the system without a reload.
    act(() => { state.dark = false; for (const listener of [...added]) listener(); });
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    // Leaving the system choice detaches the legacy listener too.
    await user.click(screen.getByRole("radio", { name: "浅色" }));
    await waitFor(() => expect(removed).toHaveLength(1));
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("stops tracking the system once an explicit theme is chosen", async () => {    const system = stubSystemScheme(true);
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    await user.click(await screen.findByRole("radio", { name: "跟随系统" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    await user.click(screen.getByRole("radio", { name: "浅色" }));
    expect(document.documentElement.dataset.theme).toBe("light");
    system.change(true);
    await waitFor(() => expect(system.listeners.size).toBe(0));
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
  });

  it("keeps the switch usable when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("storage disabled"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    await screen.findByRole("radio", { name: "深色" });
    expect(document.documentElement.dataset.theme).toBe("light");
    await user.click(screen.getByRole("radio", { name: "深色" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("radio", { name: "深色" }).getAttribute("aria-checked")).toBe("true");
  });

  it("toggles from the keyboard through the same radio group", async () => {
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    const dark = await screen.findByRole("radio", { name: "深色" });
    const light = screen.getByRole("radio", { name: "浅色" });
    dark.focus();
    expect(document.activeElement).toBe(dark);
    await user.keyboard(" ");
    expect(dark.getAttribute("aria-checked")).toBe("true");
    await user.click(light);
    expect(light.getAttribute("aria-checked")).toBe("true");
    expect(dark.getAttribute("aria-checked")).toBe("false");
  });

  it("defines every token for both themes", () => {
    const pairs = (block: string) => new Map([...block.matchAll(/(--[a-z0-9-]+):\s*([^;]+);/g)].map(match => [match[1], match[2]]));
    const themes = (file: string) => {
      const text = readFileSync(resolve(process.cwd(), file), "utf8");
      const split = text.indexOf(":root[data-theme=");
      return { light: text.slice(text.indexOf(":root {"), split), dark: text.slice(split) };
    };
    const palette = themes("src/tokens.css");
    expect(pairs(palette.light).size).toBeGreaterThan(20);
    for (const name of pairs(palette.light).keys()) expect(pairs(palette.dark).has(name), name).toBe(true);
    expect(palette.light).toContain("color-scheme: light");
    expect(palette.dark).toContain("color-scheme: dark");
  });

  it("keeps every text colour readable on every background it sits on, in both themes", () => {
    const text = readFileSync(resolve(process.cwd(), "src/tokens.css"), "utf8");
    const split = text.indexOf(":root[data-theme=");
    const luminance = (hex: string) => {
      const [r, g, b] = [1, 3, 5].map(at => parseInt(hex.slice(at, at + 2), 16) / 255)
        .map(share => share <= 0.03928 ? share / 12.92 : ((share + 0.055) / 1.055) ** 2.4);
      return 0.2126 * r + 0.7152 * g + 0.0722 * b;
    };
    const contrast = (first: string, second: string) => {
      const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
      return (lighter + 0.05) / (darker + 0.05);
    };
    for (const block of [text.slice(0, split), text.slice(split)]) {
      const value = (name: string) => block.match(new RegExp(`--${name}: (#[0-9a-f]{6});`))![1];
      const pairs = [
        ...["ink", "ink2", "ink3", "live", "hand", "fail", "past"].flatMap(ink => ["paper", "surface", "sunk", "live-wash"].map(ground => [ink, ground])),
        // ink4, the model-and-effort line's tier, sits on the resting grounds;
        // the current row keeps ink3, because on live-wash even ink3 is at the floor.
        ...["ink4"].flatMap(ink => ["paper", "surface", "sunk"].map(ground => [ink, ground])),
        ["ink", "hand-wash"], ["ink2", "hand-wash"], ["ink3", "hand-wash"], ["hand", "hand-wash"],
        ["ink", "fail-wash"], ["ink2", "fail-wash"], ["ink3", "fail-wash"], ["fail", "fail-wash"],
        ...[1, 2, 3, 4, 5, 6].map(index => ["ink", `model-${index}`]),
        ["surface", "ink"], ["surface", "fail"],
        ["bar-ink", "bar"], ["bar-ink2", "bar"], ["bar-hand", "bar"], ["bar-fail", "bar"], ["bar", "bar-hand"],
      ];
      for (const [ink, ground] of pairs) {
        expect(contrast(value(ink), value(ground)), `${ink} on ${ground}`).toBeGreaterThanOrEqual(4.5);
      }
    }
  });
});
