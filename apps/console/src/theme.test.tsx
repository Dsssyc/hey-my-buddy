import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import { THEME_STORAGE_KEY, applyStoredTheme } from "./theme";
import type { Snapshot } from "./types";

function snapshot(): Snapshot {
  return {
    csrfToken: "csrf", tableRevision: 2,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, decisionProfileId: null },
    profiles: [], cards: [], preferences: [], annotations: [], evidence: [], decisions: [],
    sampleCounts: {}, modelConcurrency: [], tasks: { runs: [], total: 0 },
    capabilities: { evaluationWriteGate: true },
  };
}
function api(): ConsoleApi {
  return {
    snapshot: vi.fn(async () => snapshot()),
    command: vi.fn(),
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
  } as unknown as ConsoleApi;
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  cleanup();
  window.localStorage.clear();
  delete document.documentElement.dataset.theme;
  document.documentElement.style.colorScheme = "";
  vi.restoreAllMocks();
});

describe("light and dark themes", () => {
  it("defaults to light and marks the document color scheme", async () => {
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    const toggle = screen.getByRole("switch", { name: "深色主题" });
    expect(toggle.getAttribute("aria-checked")).toBe("false");
    await waitFor(() => expect(document.documentElement.dataset.theme).toBe("light"));
    expect(document.documentElement.style.colorScheme).toBe("light");
    await user.click(toggle);
    expect(screen.getByRole("switch", { name: "深色主题" }).getAttribute("aria-checked")).toBe("true");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(document.documentElement.style.colorScheme).toBe("dark");
  });

  it("persists only the theme choice and restores it on the next page", async () => {
    const user = userEvent.setup();
    const first = render(<App suppliedApi={api()} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    await user.click(screen.getByRole("switch", { name: "深色主题" }));
    expect(Object.keys(window.localStorage)).toEqual([THEME_STORAGE_KEY]);
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
    first.unmount();
    delete document.documentElement.dataset.theme;
    expect(applyStoredTheme()).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    render(<App suppliedApi={api()} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    expect(screen.getByRole("switch", { name: "深色主题" }).getAttribute("aria-checked")).toBe("true");
  });

  it("keeps the switch usable when storage is unavailable", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("storage disabled"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("storage disabled"); });
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    expect(document.documentElement.dataset.theme).toBe("light");
    await user.click(screen.getByRole("switch", { name: "深色主题" }));
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(screen.getByRole("switch", { name: "深色主题" }).getAttribute("aria-checked")).toBe("true");
  });

  it("toggles from the keyboard through the same switch role", async () => {
    const user = userEvent.setup();
    render(<App suppliedApi={api()} />);
    await screen.findByRole("heading", { name: "选择一项委派" });
    const toggle = screen.getByRole("switch", { name: "深色主题" });
    toggle.focus();
    expect(document.activeElement).toBe(toggle);
    await user.keyboard("{Enter}");
    expect(toggle.getAttribute("aria-checked")).toBe("true");
    await user.keyboard(" ");
    expect(toggle.getAttribute("aria-checked")).toBe("false");
  });

  it("defines every semantic token for both themes and keeps styles token-only", () => {
    const tokens = readFileSync(resolve(process.cwd(), "src/tokens.css"), "utf8");
    const light = tokens.slice(tokens.indexOf(":root {"), tokens.indexOf(":root[data-theme="));
    const dark = tokens.slice(tokens.indexOf(":root[data-theme="));
    const names = (block: string) => [...block.matchAll(/--[a-z-]+(?=:)/g)]
      .map(match => match[0])
      .filter(name => name !== "--radius");
    expect(names(light).length).toBeGreaterThan(20);
    for (const name of names(light)) expect(names(dark)).toContain(name);
    expect(light).toContain("color-scheme: light");
    expect(dark).toContain("color-scheme: dark");
    const styles = readFileSync(resolve(process.cwd(), "src/styles.css"), "utf8");
    expect(styles.match(/#[0-9a-fA-F]{3,8}\b/g)).toBeNull();
    expect(styles.match(/\b(rgb|hsl)a?\(/g)).toBeNull();
  });
});
