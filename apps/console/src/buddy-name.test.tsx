import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import type { ConsoleApi } from "./api";
import type { Buddy, Snapshot } from "./types";
import { catalogue, chip, listed, modelButton, pane } from "./buddies/page.testing";
import { effortText } from "./buddy-name";

const flashOffId = "dsh:deepseek-official:deepseek-flash:off";
const proMaxId = "dsh:deepseek-official:deepseek-v4-pro:max";

/** Current catalogue identities carry a nullable user alias. */
const flashOff: Buddy = { effortRank: null,
  buddyId: flashOffId,
  alias: null,
  harness: "dsh",
  provider: "deepseek-official",
  model: "deepseek-flash",
  effort: "off",
  available: true,
  enabled: true,
  capabilities: ["execution:dsh", "effort:off", "decision"],
  contextWindow: 1000000,
  source: "catalog:fixture",
  description: "",
};
const proMax: Buddy = {
  ...flashOff,
  buddyId: proMaxId,
  alias: null,
  model: "deepseek-v4-pro",
  effort: "max",
  capabilities: ["execution:dsh", "effort:max", "decision"],
};

function catalogSnapshot(): Snapshot {
  return {
    csrfToken: "fixture-csrf", runtimeIdentity: "fixture-runtime", eventCursor: 0,
    consoleSession: { id: "fixture-session" },

    configuration: { explorationEvery: 5, routerBuddyIds: [proMaxId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, modelResponseWaitSeconds: 60, profileBuddyId: null,
    },
    buddies: [flashOff, proMax],
    preferences: [],

    models: [], modelCorrespondences: [],

    decisions: [],

    modelServiceConcurrency: [],
    tasks: { pendingCount: 0 },
    capabilities: { selection: false },
  };
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("native efforts", () => {
  it("preserves native and unknown efforts, and omits absent efforts", () => {
    for (const effort of ["off", "default", "max", "high", "low", "turbo"]) {
      expect(effortText(effort)).toBe(effort);
    }
    expect(effortText(null)).toBe("");
    expect(effortText(undefined)).toBe("");
  });
});

describe("the catalogue's names", () => {
  it("names a row by its identity and writes the native reasoning effort on its enabled entry", async () => {
    const api = {
      snapshot: vi.fn(async () => catalogSnapshot()),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => ({ groups: [], totals: { all: 0, active: 0, host: 0, review: 0 }, defaults: { firstLimit: 5, groupLimit: 20 }, cursor: 0, changed: false })),
    } as unknown as ConsoleApi;
    window.location.hash = "#buddies";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await listed(2, 2);
    // A row shows the model's own id, the effort part of the complete name is the chip.
    expect(modelButton("dsh / deepseek-official / deepseek-flash").textContent).toContain("deepseek-flash");
    expect(modelButton("dsh / deepseek-official / deepseek-v4-pro").textContent).toContain("deepseek-v4-pro");
    // `off` keeps its native name on the chip, exactly like `max`.
    expect(chip("dsh / deepseek-official / deepseek-flash", "off").textContent).toBe("off");
    expect(chip("dsh / deepseek-official / deepseek-v4-pro", "max").textContent).toBe("max");
    expect(screen.queryByText("非思考")).toBeNull();
    await user.click(modelButton("dsh / deepseek-official / deepseek-v4-pro"));
    expect(within(pane("dsh / deepseek-official / deepseek-v4-pro")).getByRole("group", { name: "max" })).toBeTruthy();
  });
});
