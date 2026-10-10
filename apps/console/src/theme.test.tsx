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
    csrfToken: "csrf", consoleSession: { id: "fixture-session", canWrite: true, reason: null }, tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: [], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard"},
    profiles: [], cards: [], preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { pendingCount: 0 },
    capabilities: { evaluationWriteGate: true },
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
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
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
    expect(dark).toHaveProperty("checked", false);
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(document.documentElement.style.colorScheme).toBe("light");
    await user.click(dark);
    expect(screen.getByRole("radio", { name: "深色" })).toHaveProperty("checked", true);
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
    expect(await screen.findByRole("radio", { name: "深色" })).toHaveProperty("checked", true);
  });

  it("follows the system scheme and keeps tracking it while the page stays open", async () => {
    const system = stubSystemScheme(true);
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await user.click(await screen.findByRole("link", { name: "设置" }));
    const follow = await screen.findByRole("radio", { name: "跟随系统" });
    expect(follow).toHaveProperty("checked", false);
    await user.click(follow);
    expect(screen.getByRole("radio", { name: "跟随系统" })).toHaveProperty("checked", true);
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
    expect(await screen.findByRole("radio", { name: "跟随系统" })).toHaveProperty("checked", true);
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
    expect(screen.getByRole("radio", { name: "深色" })).toHaveProperty("checked", true);
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
    expect(dark).toHaveProperty("checked", true);
    await user.click(light);
    expect(light).toHaveProperty("checked", true);
    expect(dark).toHaveProperty("checked", false);
  });

  it("defines every token for both themes and keeps the old stylesheet token-only", () => {
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
    // The old names are aliases of the palette; only the colours that have no
    // place in it carry values of their own, and those exist for both themes.
    const legacy = themes("src/legacy-tokens.css");
    const own = [...pairs(legacy.light)].filter(([, value]) => !value.startsWith("var(") && !/^\d+px$/.test(value));
    expect(own.length).toBeGreaterThan(0);
    for (const [name] of own) expect(pairs(legacy.dark).has(name), name).toBe(true);
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    expect(styles.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull();
    expect(styles.match(/\b(rgb|hsl)a?\(/g)).toBeNull();
  });
});
