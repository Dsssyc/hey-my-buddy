import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "./App";
import { HelperForm } from "./HelperForm";
import { RoutingPanel } from "./RoutingPanel";
import type { ConsoleApi } from "./api";
import type { Profile, Snapshot, WriterGrant } from "./types";
import type { HelperDraft, Workflow } from "./workflow-types";
import {
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
    tableRevision: 2,
    gate: { phase: "open", readers: 0, writer: null, waitingWriters: 0 },
    configuration: {
      revision: 1,
      decisionProfileId: proMaxId,
    },
    profiles: [flashOff, proMax],
    preferences: [],
    cards: [],
    annotations: [],
    evidence: [],
    decisions: [],
    sampleCounts: { [flashOffId]: 6 },
    modelConcurrency: [],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: false, maintenance: false, evaluationWriteGate: true },
  };
}

function optionEntries(select: HTMLElement) {
  return Array.from(select.querySelectorAll("option")).map((option) => [
    option.value,
    option.textContent,
  ]);
}

afterEach(() => {
  cleanup();
  window.location.hash = "";
});

describe("profile display", () => {
  it("drops the catalog's duplicate effort tail and localizes off once", () => {
    expect(profileName(flashOff)).toBe("DeepSeek-V41-Flash");
    expect(profileTitle(flashOff)).toBe("DeepSeek-V41-Flash · 非思考");
  });

  it("keeps a custom label and appends the effort exactly once", () => {
    expect(
      profileTitle({
        label: "我的稳定配置",
        model: "deepseek-flash",
        effort: "off",
      }),
    ).toBe("我的稳定配置 · 非思考");
  });

  it("preserves native and unknown efforts instead of translating them", () => {
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
    ).toBe("half-off 实验 · 非思考");
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
    ).toBe("deepseek-flash · 非思考");
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
      "DeepSeek-V41-Flash · 非思考",
    );
  });
});

describe("decision profile selector", () => {
  it("offers only decision-capable candidates and publishes one configuration patch", async () => {
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
    } as unknown as ConsoleApi;
    window.location.hash = "#settings";
    const user = userEvent.setup();
    render(<App suppliedApi={api} />);
    expect(
      optionEntries(await screen.findByLabelText("决策模型配置")),
    ).toEqual([
      ["", "尚未配置"],
      [flashOffId, "DeepSeek-V41-Flash · 非思考"],
      [proMaxId, "DeepSeek-V4-Pro · max"],
    ]);
    expect(screen.getByText("deepseek-official / max")).toBeTruthy();

    await user.click(screen.getByRole("switch", { name: "编辑模式" }));
    await waitFor(() =>
      expect(screen.getByLabelText("决策模型配置")).toHaveProperty(
        "disabled",
        false,
      ),
    );
    await user.selectOptions(screen.getByLabelText("决策模型配置"), flashOffId);
    expect(await screen.findByText("deepseek-official / 非思考")).toBeTruthy();
    expect(screen.getByText(/草稿中的决策模型/)).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "保存更改" }));
    await screen.findByText("已发布新版本。正在执行的任务继续使用原配置。");
    expect(published!.configuration.decisionProfileId).toBe(flashOffId);
    expect(published!.configuration).not.toHaveProperty("autoMaintain");
    // Only the decision selector changed, so nothing else is sent: no full table,
    // no provider/model/effort/available field and no card.
    expect(published).not.toHaveProperty("profiles");
    expect(published).not.toHaveProperty("cards");
    expect(published).not.toHaveProperty("preferenceChanges");
    expect(published).not.toHaveProperty("annotationChanges");
    expect(Object.keys(published!).sort()).toEqual([
      "commandId", "configuration", "expectedRevision", "generation", "writerId", "writerToken",
    ]);
  });
});

describe("model list display", () => {
  it("shows the catalog name once and localizes the effort badge", async () => {
    const api = {
      snapshot: vi.fn(async () => catalogSnapshot()),
      command: vi.fn(),
      task: vi.fn(),
      tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    } as unknown as ConsoleApi;
    window.location.hash = "#models";
    render(<App suppliedApi={api} />);
    expect(
      await screen.findByRole("button", { name: /^DeepSeek-V41-Flash/ }),
    ).toBeTruthy();
    expect(screen.getByRole("button", { name: /^DeepSeek-V4-Pro/ })).toBeTruthy();
    expect(screen.getByText("非思考")).toBeTruthy();
    expect(screen.queryByText("DeepSeek-V41-Flash · off")).toBeNull();
  });
});

describe("helper and routing selectors", () => {
  it("labels helper options once and keeps the raw profile id as the value", () => {
    const helper: HelperDraft = {
      id: "helper-1",
      profileId: "",
      task: "",
      cwd: "/repo",
      kind: "worktree",
      access: "write",
      writeScope: "",
      includeUntracked: "",
    };
    render(
      <HelperForm
        helper={helper}
        index={0}
        profiles={[flashOff, proMax]}
        onChange={vi.fn()}
        onRemove={vi.fn()}
      />,
    );
    expect(optionEntries(screen.getByLabelText("执行配置"))).toEqual([
      ["", "自动路由：由固定决策 Buddy 选择"],
      [flashOffId, "dsh · DeepSeek-V41-Flash · 非思考"],
      [proMaxId, "dsh · DeepSeek-V4-Pro · max"],
    ]);
  });

  it("fills the routing form from the same display while keeping raw effort values", async () => {
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
    const command = vi.fn(async () => undefined);
    const user = userEvent.setup();
    render(
      <RoutingPanel
        value={workflow}
        profiles={[flashOff, proMax]}
        locked={false}
        command={command}
      />,
    );
    expect(
      screen.getByText("dsh / deepseek-official / deepseek-flash / 非思考"),
    ).toBeTruthy();
    const select = screen.getByLabelText("填入已启用配置");
    expect(
      optionEntries(select).find(([value]) => value === flashOffId),
    ).toEqual([flashOffId, "dsh · DeepSeek-V41-Flash · 非思考"]);

    await user.selectOptions(select, flashOffId);
    expect(screen.getByLabelText("Thinking effort")).toHaveProperty(
      "value",
      "off",
    );
    await user.click(screen.getByRole("button", { name: "使用此配置接续" }));
    expect(command).toHaveBeenCalledWith(
      "workflow_continue",
      expect.objectContaining({
        configuration: {
          adapter: "dsh",
          provider: "deepseek-official",
          model: "deepseek-flash",
          effort: "off",
        },
      }),
    );
  });
});
