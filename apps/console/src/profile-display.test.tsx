import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { RoutingPanel } from "./RoutingPanel";
import type { ConsoleApi } from "./api";
import type { Profile, Snapshot, WriterGrant } from "./types";
import type { Workflow } from "./workflow-types";
import { formatDate } from "./ui";
import {
  catalogStateText,
  effortText,
  profileName,
  profileTitle,
  profileTitleOr,
} from "./profile-display";

const flashOffId = "dsh:deepseek-official:deepseek-flash:off";
const proMaxId = "dsh:deepseek-official:deepseek-v4-pro:max";

/** Same shape buddy/catalog.py proposes: the label already ends in " · <effort>". */
const flashOff: Profile = {
  profileId: flashOffId,
  label: "DeepSeek-V41-Flash · off",
  adapter: "dsh",
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
const proMax: Profile = {
  ...flashOff,
  profileId: proMaxId,
  label: "DeepSeek-V4-Pro · max",
  model: "deepseek-v4-pro",
  effort: "max",
  capabilities: ["execution:dsh", "effort:max", "decision"],
};

function catalogSnapshot(): Snapshot {
  return {
    csrfToken: "fixture-csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 2,
    gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
    configuration: {
      revision: 1,
      routerProfileIds: [proMaxId], routerRetryIntervalSeconds: 600, defaultRoutingMode: "review" as const, routingBudget: "standard",
    },
    profiles: [flashOff, proMax],
    preferences: [],
    familyPreferences: [],
    preferenceOverrides: [],
    cards: [],
    familyAnnotations: [],
    evidence: [],
    decisions: [],
    sampleCounts: { [flashOffId]: 6 },
    modelConcurrency: [],
    tasks: { pendingCount: 0 },
    capabilities: { selection: false, maintenance: false, evaluationWriteGate: true },
  };
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("profile display", () => {
  it("drops the catalog's duplicate effort tail and shows the native off once", () => {
    expect(profileName(flashOff)).toBe("DeepSeek-V41-Flash");
    expect(profileTitle(flashOff)).toBe("DeepSeek-V41-Flash · off");
  });

  it("keeps a custom label and appends the effort exactly once", () => {
    expect(
      profileTitle({
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
    expect(profileTitle(proMax)).toBe("DeepSeek-V4-Pro · max");
    expect(
      profileTitle({
        label: "实验配置",
        model: "deepseek-flash",
        effort: "turbo",
      }),
    ).toBe("实验配置 · turbo");
  });

  it("keeps off inside a name and a tail that does not match the effort", () => {
    expect(
      profileTitle({
        label: "half-off 实验",
        model: "deepseek-flash",
        effort: "off",
      }),
    ).toBe("half-off 实验 · off");
    expect(
      profileTitle({
        label: "DeepSeek-V4-Pro · low",
        model: "deepseek-v4-pro",
        effort: "max",
      }),
    ).toBe("DeepSeek-V4-Pro · low · max");
  });

  it("falls back to the model id and never renders a dangling separator", () => {
    expect(
      profileTitle({ label: "", model: "deepseek-flash", effort: "off" }),
    ).toBe("deepseek-flash · off");
    expect(
      profileTitle({ label: "  ", model: "deepseek-flash", effort: "" }),
    ).toBe("deepseek-flash");
    expect(effortText(null)).toBe("");
    expect(effortText(undefined)).toBe("");
  });

  it("keeps the recorded id when the snapshot no longer holds the profile", () => {
    expect(profileTitleOr(undefined, "dsh:removed:model:off")).toBe(
      "dsh:removed:model:off",
    );
    expect(profileTitleOr(flashOff, "unused")).toBe(
      "DeepSeek-V41-Flash · off",
    );
  });
});

describe("catalog state text", () => {
  it("reads an explicit catalog state as recorded, never from availability", () => {
    // The board's own state wins even when harness health marks the
    // configuration unavailable right now.
    expect(catalogStateText({ available: false, catalogStatus: "available", pendingSince: null })).toBe("可用");
    expect(catalogStateText({ available: true, catalogStatus: "unavailable", pendingSince: null })).toBe("不可用");
  });

  it("keeps pending with the recorded time and stays honest when none was recorded", () => {
    expect(catalogStateText({ available: true, catalogStatus: "pending", pendingSince: "2026-10-07T01:23:45Z" }))
      .toBe(`待确认 · 自 ${formatDate("2026-10-07T01:23:45Z")}`);
    expect(catalogStateText({ available: true, catalogStatus: "pending", pendingSince: null })).toBe("待确认");
  });

  it("falls back to recorded availability only for fixtures without the field", () => {
    expect(catalogStateText({ available: true, pendingSince: null })).toBe("可用");
    expect(catalogStateText({ available: false, pendingSince: null })).toBe("不可用");
  });
});

describe("decision profile selector", () => {
  it("sets the Router from an effort tag menu, confirms a replacement, and publishes one configuration patch", async () => {
    let state = catalogSnapshot();
    let published: Record<string, any> | null = null;
    const grant: WriterGrant = {
      writerId: "fixture-writer",
      generation: 1,
      writerToken: "fixture-secret",
      phase: "writing",
      tableRevision: 2,
      expiresAt: new Date(Date.now() + 120000).toISOString(),
    };
    const command = vi.fn(async (operation: string, params: any) => {
      if (operation === "evaluation_write_begin") {
        state = {
          ...state,
          gate: {
            ...state.gate,
            phase: "writing",
            writer: { ...grant, kind: "human" },
          },
        };
        return grant;
      }
      if (operation === "user_policy_publish") {
        published = params;
        state = {
          ...state,
          tableRevision: 3,
          gate: { ...state.gate, phase: "open", writer: null },
        };
        return { tableRevision: 3 };
      }
      return {};
    });
    const api = {
      snapshot: vi.fn(async () => structuredClone(state)),
      command,
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    } as unknown as ConsoleApi;
    window.location.hash = "#buddy";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    // The Router is a role on an effort tag now: no "路由模型配置" selector exists.
    expect(screen.queryByLabelText("路由模型配置")).toBeNull();
    expect((await screen.findAllByText("DeepSeek-V4-Pro · max")).length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: /^DeepSeek-V41-Flash/ }));
    await user.click(screen.getByRole("button", { name: "off 档位菜单" }));
    await user.click(screen.getByRole("button", { name: "设为 Router" }));
    // An existing Router asks for confirmation before being replaced.
    expect(await screen.findByRole("heading", { name: "替换 Router" })).toBeTruthy();
    expect(screen.getByText(/将替换当前 Router DeepSeek-V4-Pro · max，改为 DeepSeek-V41-Flash · off/)).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "替换" }));
    expect((await screen.findAllByText("DeepSeek-V41-Flash · off")).length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "保存" }));
    await screen.findByText("已发布新版本");
    expect(published!.configuration.routerProfileIds?.[0]).toBe(flashOffId);
    expect(published!.configuration).not.toHaveProperty("autoMaintain");
    // Only the decision selector changed, so nothing else is sent: no full table,
    // no provider/model/effort/available field and no card.
    expect(published).not.toHaveProperty("profiles");
    expect(published).not.toHaveProperty("cards");
    expect(published).not.toHaveProperty("preferenceChanges");
    expect(published).not.toHaveProperty("familyAnnotationChanges");
    expect(Object.keys(published!).sort()).toEqual([
      "commandId", "configuration", "expectedRevision", "generation", "writerId", "writerToken",
    ]);
  });
});

describe("model list display", () => {
  it("shows the family name once and the native effort on its tag", async () => {
    const api = {
      snapshot: vi.fn(async () => catalogSnapshot()),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
      objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
    } as unknown as ConsoleApi;
    window.location.hash = "#buddy";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    expect(
      await screen.findByRole("button", { name: /^DeepSeek-V41-Flash/ }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /^DeepSeek-V4-Pro/ })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: /^DeepSeek-V41-Flash/ }));
    // `off` keeps its native name on the effort tag, exactly like `max`/`low`;
    // the family list itself never repeats the effort inside the family name.
    expect(screen.getAllByText("off").length).toBeGreaterThan(0);
    expect(screen.queryByText("非思考")).toBeNull();
    await user.click(screen.getByRole("button", { name: /^DeepSeek-V4-Pro/ }));
    expect(screen.getAllByText("max").length).toBeGreaterThan(0);
    expect(screen.queryByText("DeepSeek-V41-Flash · off")).toBeNull();
  });
});

describe("read-only routing panel (0.15.1 U4)", () => {
  it("shows the recorded configuration and routing facts without any retry form", () => {
    const workflow: Workflow = {
      governed: true,
      runId: "parent",
      hostId: "host-a",
      ownerGeneration: 2,
      revision: 7,
      state: "waiting-host",
      awaitingHost: true,
      waitReason: "routing",
      continuationCount: 0,
      executionConfiguration: {
        adapter: "dsh",
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
