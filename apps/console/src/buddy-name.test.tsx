import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { RoutingPanel } from "./RoutingPanel";
import type { ConsoleApi } from "./api";
import type { Buddy, Snapshot } from "./types";
import type { Workflow } from "./workflow-types";
import { catalogue, chip, listed, modelButton, pane } from "./buddies/page.testing";
import {
  effortText,
  buddyName,
  buddyTitle,
  buddyTitleOr,
} from "./buddy-name";

const flashOffId = "dsh:deepseek-official:deepseek-flash:off";
const proMaxId = "dsh:deepseek-official:deepseek-v4-pro:max";

/** Same shape buddy/catalog.py proposes: the label already ends in " · <effort>". */
const flashOff: Buddy = {
  buddyId: flashOffId,
  label: "DeepSeek-V41-Flash · off",
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
  label: "DeepSeek-V4-Pro · max",
  model: "deepseek-v4-pro",
  effort: "max",
  capabilities: ["execution:dsh", "effort:max", "decision"],
};

function catalogSnapshot(): Snapshot {
  return {
    csrfToken: "fixture-csrf", eventCursor: 0,
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },

    configuration: { routerBuddyIds: [proMaxId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard",
    },
    buddies: [flashOff, proMax],
    preferences: [],

    modelServiceNotes: [],

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

describe("buddy display", () => {
  it("drops the catalog's duplicate effort tail and shows the native off once", () => {
    expect(buddyName(flashOff)).toBe("DeepSeek-V41-Flash");
    expect(buddyTitle(flashOff)).toBe("DeepSeek-V41-Flash · off");
  });

  it("keeps a custom label and appends the effort exactly once", () => {
    expect(
      buddyTitle({
        label: "我的稳定配置",
        model: "deepseek-flash",
        effort: "off",
      }),
    ).toBe("我的稳定配置 · off");
  });

  it("preserves native and unknown efforts instead of translating them", () => {
    expect(effortText("off")).toBe("off");
    expect(effortText("default")).toBe("default");
    expect(effortText("max")).toBe("max");
    expect(effortText("high")).toBe("high");
    expect(effortText("low")).toBe("low");
    expect(effortText("turbo")).toBe("turbo");
    expect(buddyTitle(proMax)).toBe("DeepSeek-V4-Pro · max");
    expect(
      buddyTitle({
        label: "实验配置",
        model: "deepseek-flash",
        effort: "turbo",
      }),
    ).toBe("实验配置 · turbo");
  });

  it("keeps off inside a name and a tail that does not match the effort", () => {
    expect(
      buddyTitle({
        label: "half-off 实验",
        model: "deepseek-flash",
        effort: "off",
      }),
    ).toBe("half-off 实验 · off");
    expect(
      buddyTitle({
        label: "DeepSeek-V4-Pro · low",
        model: "deepseek-v4-pro",
        effort: "max",
      }),
    ).toBe("DeepSeek-V4-Pro · low · max");
  });

  it("falls back to the model id and never renders a dangling separator", () => {
    expect(
      buddyTitle({ label: "", model: "deepseek-flash", effort: "off" }),
    ).toBe("deepseek-flash · off");
    expect(
      buddyTitle({ label: "  ", model: "deepseek-flash", effort: "" }),
    ).toBe("deepseek-flash");
    expect(effortText(null)).toBe("");
    expect(effortText(undefined)).toBe("");
  });

  it("keeps the recorded id when the snapshot no longer holds the buddy", () => {
    expect(buddyTitleOr(undefined, "dsh:removed:model:off")).toBe(
      "dsh:removed:model:off",
    );
    expect(buddyTitleOr(flashOff, "unused")).toBe(
      "DeepSeek-V41-Flash · off",
    );
  });
});

describe("the catalogue's names", () => {
  it("names a row by its identity and writes the native reasoning effort on its enabled entry", async () => {
    const api = {
      snapshot: vi.fn(async () => catalogSnapshot()),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      macroTasks: vi.fn(async () => ({ macroTasks: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    } as unknown as ConsoleApi;
    window.location.hash = "#buddies";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    await listed(2, 2);
    // A row shows the model's own id, the fourth part of the identity is the chip.
    expect(modelButton("dsh / deepseek-official / deepseek-flash").textContent).toContain("deepseek-flash");
    expect(modelButton("dsh / deepseek-official / deepseek-v4-pro").textContent).toContain("deepseek-v4-pro");
    // `off` keeps its native name on the chip, exactly like `max`; the catalogue's
    // display label with the level in it is never repeated in the table.
    expect(chip("dsh / deepseek-official / deepseek-flash", "off").textContent).toBe("off");
    expect(chip("dsh / deepseek-official / deepseek-v4-pro", "max").textContent).toBe("max");
    expect(screen.queryByText("非思考")).toBeNull();
    expect(within(catalogue()).queryByText(/DeepSeek-V41-Flash/)).toBeNull();
    await user.click(modelButton("dsh / deepseek-official / deepseek-v4-pro"));
    expect(within(pane("dsh / deepseek-official / deepseek-v4-pro")).getByRole("group", { name: "max" })).toBeTruthy();
  });
});

describe("read-only routing panel (0.15.1 U4)", () => {
  it("shows the recorded buddy and routing facts without any retry form", () => {
    const workflow: Workflow = {
            runId: "parent",
      hostId: "host-a",
      ownerGeneration: 2,
      revision: 7,
      state: "waiting-host",
      awaitingHost: true,
      waitReason: "routing",
      continuationCount: 0,
      buddy: {
        harness: "dsh",
        provider: "deepseek-official",
        model: "deepseek-flash",
        effort: "off",
      },
      routing: { status: "needs-host", reason: "缺少执行配置" },
      shutdown: {
        selfConfirmed: true,
        descendantsConfirmed: true,
        unconfirmedRunIds: [],
        unconfirmedCount: 0,
        truncated: false,
      },
      workspace: {
        path: "/repo",
        kind: "existing",
        access: "write",
        inputCommit: "commit-a",
        manifestSha256: "manifest-a",
      },
      currentTurn: null,
      activeRequest: null,
      children: [],
      artifacts: [],
      integrations: [],
      finalArtifactId: null,
      finalAttemptId: null,
      task: {
        runId: "parent",
        task: "完成状态内核",
        status: "waiting-host",
        owner: "host-a",
        cwd: "/repo",
        revision: 4,
        createdAt: "2026-09-22",
        acceptedAt: null,
        acceptanceVerdict: null,
      },
    };
    render(<RoutingPanel value={workflow} />);
    expect(
      screen.getByText("dsh / deepseek-official / deepseek-flash / off"),
    ).toBeTruthy();
    expect(screen.getByText("needs-host")).toBeTruthy();
    expect(
      screen.getByText(/路由需要 Host 补充配置/),
    ).toBeTruthy();
    expect(screen.queryByLabelText("填入已启用配置")).toBeNull();
    expect(screen.queryByRole("button", { name: "使用此配置接续" })).toBeNull();
    expect(screen.queryByRole("button", { name: "重试同一目标的路由" })).toBeNull();
  });
});
