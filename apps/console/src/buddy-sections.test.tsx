import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { ConsoleApi, HarnessHealth } from "./api";
import type { Profile, Snapshot } from "./types";

/**
 * Buddy 配置分区导航（docs/design/backlog.md 控制台第 1 条）：默认进入模型
 * 分区；#buddy/models、#buddy/router、#buddy/harness 三个分区互相切换；全局
 * 状态条在三个分区始终可见，提醒链接直接跳到能处理它的分区和条目；深链、
 * hashchange、浏览器返回与旧书签 #models、#settings 落到同一状态；跳转穿透
 * 搜索与筛选；窄屏是同一导航的顶部横排，切换可用且不丢状态。CSS 几何由
 * Host 的真实浏览器验收，这里只测组件行为。
 */

const sonnet = { adapter: "claude", provider: "anthropic", model: "claude-sonnet-5" };
const sol = { adapter: "codex", provider: "openai", model: "gpt-6-sol" };
const flash = { adapter: "dsh", provider: "deepseek-official", model: "deepseek-flash" };
const glm = { adapter: "zcode", provider: "zhipu", model: "glm-5" };

const mediumId = "claude:anthropic:claude-sonnet-5:medium";
const highId = "claude:anthropic:claude-sonnet-5:high";
const solMediumId = "codex:openai:gpt-6-sol:medium";
const flashMaxId = "dsh:deepseek-official:deepseek-flash:max";
const glmId = "zcode:zhipu:glm-5:default";
const CHECKED_AT = "2026-09-29T09:30:00.000Z";

function profile(entry: Partial<Profile> & Pick<Profile, "profileId" | "label" | "adapter" | "provider" | "model" | "effort">): Profile {
  return {
    available: true, enabled: false, capabilities: [], contextWindow: null,
    description: "", source: "catalog:fixture", ...entry,
  };
}

/** One enabled family per harness plus an offline family hidden by default. */
function profiles(): Profile[] {
  return [
    profile({ profileId: mediumId, label: "Claude Sonnet 5 · medium", ...sonnet, effort: "medium",
      enabled: true, capabilities: ["execution:claude", "routing:fast", "decision"], contextWindow: 200_000 }),
    profile({ profileId: highId, label: "Claude Sonnet 5 · high", ...sonnet, effort: "high",
      capabilities: ["execution:claude", "decision"], contextWindow: 200_000 }),
    profile({ profileId: solMediumId, label: "GPT-6 Sol · medium", ...sol, effort: "medium",
      enabled: true, capabilities: ["execution:codex"] }),
    profile({ profileId: flashMaxId, label: "DeepSeek Flash · max", ...flash, effort: "max",
      enabled: true, capabilities: ["execution:dsh", "routing:fast"] }),
    profile({ profileId: glmId, label: "GLM-5 · default", ...glm, effort: "default", available: false,
      unavailableReason: "本机未检测到 zcode CLI" }),
  ];
}

/** Codex lacks local review eligibility; DSH carries a near-limit quota window. */
function harnesses(): HarnessHealth[] {
  return [
    { adapter: "claude", status: "ready", available: true, revision: 2, manualPath: null,
      executable: "/usr/local/bin/claude", version: "2.0.1", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [] },
    { adapter: "codex", status: "ready", available: true, revision: 2, manualPath: null,
      executable: "/opt/bin/codex", version: "0.159.0", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      readOnlyStructured: { implemented: true, eligible: false, systemSandbox: true, sameAttemptContinuation: false, reasonCode: "READ_ONLY_RESOURCE_UNAVAILABLE", reason: "本地只读运行资源不可用" } },
    { adapter: "dsh", status: "ready", available: true, revision: 3, manualPath: null,
      executable: "/usr/local/bin/dsh", version: "0.4.2", source: "PATH",
      checkedAt: CHECKED_AT, candidates: [],
      quota: { observedAt: "2026-09-29T09:00:00.000Z", source: "dsh-native", provider: "deepseek-official",
        stale: false, windows: [
          { name: "5h", usedPercent: 93, resetsAt: "2026-09-29T13:00:00.000Z" },
          { name: "weekly", usedPercent: 41, resetsAt: null },
        ] } },
    { adapter: "zcode", status: "missing", available: false, revision: 1, manualPath: null,
      reasonCode: "HARNESS_NOT_FOUND", remedy: "安装 ZCode CLI，或填写可执行文件的绝对路径。",
      checkedAt: CHECKED_AT, candidates: [] },
  ];
}

type SnapshotWithHarnesses = Snapshot & { harnesses: HarnessHealth[] };

function snapshot(routerProfileId: string | null = solMediumId): SnapshotWithHarnesses {
  const base: Snapshot = {
    csrfToken: "csrf",
    consoleSession: { id: "fixture-session", canWrite: true, reason: null },
    tableRevision: 4,
    gate: { phase: "open", readers: 0, waitingWriters: 0, writer: null },
    configuration: { revision: 1, routerProfileIds: routerProfileId ? [routerProfileId] : [], routerRetryIntervalSeconds: 600,
      defaultRoutingMode: "review" as const, routingBudget: "standard" },
    profiles: profiles(),
    preferences: [], familyPreferences: [], preferenceOverrides: [], familyAnnotations: [],
    cards: [], evidence: [], decisions: [], sampleCounts: {},
    modelConcurrency: [
      { ...sonnet, limit: 2, active: 0 }, { ...sol, limit: 2, active: 0 },
      { ...flash, limit: 2, active: 0 }, { ...glm, limit: 2, active: 0 },
    ],
    tasks: { runs: [], total: 0 },
    capabilities: { selection: true, maintenance: true, evaluationWriteGate: true },
  };
  return { ...base, harnesses: harnesses() };
}

function fixture(routerProfileId: string | null = solMediumId) {
  let state = snapshot(routerProfileId);
  const command = vi.fn(async (operation: string, _params: Record<string, any>, _csrfToken?: string) => {
    if (operation === "model_profiles") {
      // Only the checked 显示不可用配置 asks for this page; the fixture keeps
      // every recorded row on one bounded page.
      return { profiles: state.profiles, cards: state.cards, familyAnnotations: state.familyAnnotations,
        preferences: state.preferences, preferenceOverrides: state.preferenceOverrides,
        familyPreferences: state.familyPreferences, sampleCounts: state.sampleCounts,
        modelConcurrency: state.modelConcurrency, tableRevision: state.tableRevision, nextCursor: null };
    }
    throw new Error(`Unexpected command: ${operation}`);
  });
  const api = {
    snapshot: vi.fn(async () => structuredClone(state)),
    command,
    task: vi.fn(),
    tasks: vi.fn(async () => ({ runs: [], total: 0, nextCursor: null })),
    objectives: vi.fn(async () => ({ objectives: [], total: 0, nextCursor: null, cursor: 0, changed: false })),
  } as unknown as ConsoleApi;
  return { api, command };
}

function renderBuddy(hash: string, f: ReturnType<typeof fixture>) {
  window.location.hash = hash;
  render(<App suppliedApi={f.api} />);
}

const sectionsNav = () => screen.getByRole("navigation", { name: "Buddy 配置分区" });
const sectionLink = (name: string) => within(sectionsNav()).getByRole("link", { name });
const statusStrip = () => screen.getByRole("region", { name: "全局状态" });
const currentSection = () =>
  within(sectionsNav()).getAllByRole("link").filter(link => link.getAttribute("aria-current") === "page")
    .map(link => link.textContent);

afterEach(() => {
  cleanup();
  window.location.hash = "";
  sessionStorage.clear();
  document.documentElement.dataset.theme = "";
  Object.defineProperty(window, "innerWidth", { value: 1024, configurable: true });
});

describe("section navigation", () => {
  it("enters the model section by default and switches through the section nav", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    expect(currentSection()).toEqual(["模型"]);
    expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Router 分区" })).toBeNull();
    expect(screen.queryByRole("region", { name: "Harness 分区" })).toBeNull();

    await user.click(sectionLink("Router"));
    expect(window.location.hash).toBe("#buddy/router");
    await waitFor(() => expect(screen.getByRole("region", { name: "Router 分区" })).toBeTruthy());
    expect(screen.queryByRole("region", { name: "模型分区" })).toBeNull();
    expect(currentSection()).toEqual(["Router"]);
    // The Router section is the whole expanded routing page, not a folded line.
    expect(screen.getByRole("region", { name: "路由状态" })).toBeTruthy();
    expect(screen.getByRole("radiogroup", { name: "审阅预算" })).toBeTruthy();

    await user.click(sectionLink("Harness"));
    expect(window.location.hash).toBe("#buddy/harness");
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness 状态" })).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Router 分区" })).toBeNull();
    expect(currentSection()).toEqual(["Harness"]);

    await user.click(sectionLink("模型"));
    await waitFor(() => expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Harness 分区" })).toBeNull();
    expect(currentSection()).toEqual(["模型"]);
  });

  it("keeps the selected family, the search filter and the draft across section switches", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.type(screen.getByLabelText("搜索模型"), "gpt");
    await screen.findByRole("heading", { name: "模型 1" });
    await user.click(screen.getByRole("button", { name: /^GPT-6 Sol/ }));
    await screen.findByRole("heading", { name: "GPT-6 Sol" });
    await user.type(screen.getByLabelText("家族备注"), "分区间保留");
    expect(screen.getByText("1 项未保存")).toBeTruthy();

    await user.click(sectionLink("Router"));
    await waitFor(() => expect(screen.getByRole("region", { name: "Router 分区" })).toBeTruthy());
    await user.click(sectionLink("Harness"));
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness 状态" })).toBeTruthy());

    await user.click(sectionLink("模型"));
    expect(screen.getByLabelText("搜索模型")).toHaveProperty("value", "gpt");
    expect(screen.getByRole("heading", { name: "模型 1" })).toBeTruthy();
    expect(screen.getByRole("heading", { name: "GPT-6 Sol" })).toBeTruthy();
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "分区间保留");
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });

  it("keeps the global status strip visible in all three sections", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    const alerts = () => [
      "Router 需处理，去处理", "Harness 可用 3/4", "额度提醒：DSH",
    ].map(name => within(statusStrip()).getByRole("link", { name }));
    expect(alerts()).toHaveLength(3);

    await user.click(sectionLink("Router"));
    expect(statusStrip()).toBeTruthy();
    expect(alerts()).toHaveLength(3);
    await user.click(sectionLink("Harness"));
    expect(statusStrip()).toBeTruthy();
    expect(alerts()).toHaveLength(3);
  });
});

describe("status reminders jump to the entry that can handle them", () => {
  it("jumps an ineligible Router reminder to its model effort", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.click(within(statusStrip()).getByRole("link", { name: "Router 需处理，去处理" }));
    expect(window.location.hash).toBe(`#buddy/models/${encodeURIComponent(solMediumId)}`);
    await screen.findByRole("heading", { name: "GPT-6 Sol" });
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById(`effort-tag-${solMediumId}`)));
    expect(screen.queryByLabelText("审阅验证配置")).toBeNull();

  });

  it("jumps the quota reminder to the opened and focused DSH row", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.click(within(statusStrip()).getByRole("link", { name: "额度提醒：DSH" }));
    expect(window.location.hash).toBe("#buddy/harness/dsh");
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness 状态" })).toBeTruthy());
    const dshRow = document.getElementById("harness-dsh")!;
    expect(screen.getByRole("button", { name: "DSH 收起详情" })).toBeTruthy();
    expect(within(dshRow).getByText("使用率 93%")).toBeTruthy();
    expect(within(dshRow).getByText("接近上限")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(dshRow));
  });
});

describe("deep links", () => {
  it("opens the Codex harness row focused from #buddy/harness/codex", async () => {
    const f = fixture();
    renderBuddy("#buddy/harness/codex", f);
    await screen.findByRole("region", { name: "Harness 状态" });
    expect(screen.queryByRole("region", { name: "模型分区" })).toBeNull();
    const codexRow = document.getElementById("harness-codex")!;
    expect(await screen.findByRole("button", { name: "Codex 收起详情" })).toBeTruthy();
    expect(within(codexRow).getByText("本地只读运行资源不可用")).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(codexRow));
    expect(currentSection()).toEqual(["Harness"]);
  });

  it("opens and focuses the encoded profile from #buddy/models/<profileId>", async () => {
    const f = fixture();
    renderBuddy(`#buddy/models/${encodeURIComponent(mediumId)}`, f);
    await screen.findByRole("heading", { name: "模型 3" });
    expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Harness 分区" })).toBeNull();
    // The family is selected so the effort tag is on screen and focused.
    expect(await screen.findByRole("heading", { name: "Claude Sonnet 5" })).toBeTruthy();
    const tag = document.getElementById(`effort-tag-${mediumId}`)!;
    await waitFor(() => expect(document.activeElement).toBe(tag));
    expect(currentSection()).toEqual(["模型"]);
  });

  it("focuses the named Router line from a #buddy/router target", async () => {
    const f = fixture();
    renderBuddy("#buddy/router/current", f);
    await screen.findByRole("region", { name: "路由状态" });
    expect(screen.getByRole("region", { name: "Router 分区" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "模型分区" })).toBeNull();
    const line = document.getElementById("router-current")!;
    expect(line.textContent).toContain("Router");
    await waitFor(() => expect(document.activeElement).toBe(line));
  });

  it("follows hashchange and the browser back action between sections", async () => {
    const f = fixture();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await act(async () => { window.location.hash = "#buddy/harness"; });
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness 状态" })).toBeTruthy());
    expect(currentSection()).toEqual(["Harness"]);
    await act(async () => { window.history.back(); });
    await waitFor(() => expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy());
    expect(screen.queryByRole("region", { name: "Harness 分区" })).toBeNull();
    expect(currentSection()).toEqual(["模型"]);
  });

  it.each(["#models", "#settings"])("lands the legacy %s bookmark in the model section", async hash => {
    const f = fixture();
    renderBuddy(hash, f);
    await screen.findByRole("heading", { name: "模型 3" });
    expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Router 分区" })).toBeNull();
    expect(currentSection()).toEqual(["模型"]);
  });

  it("jumps from the Router section to the router's family and focuses its effort", async () => {
    const f = fixture();
    const user = userEvent.setup();
    renderBuddy("#buddy/router", f);
    await screen.findByRole("region", { name: "路由状态" });
    const reviewLine = document.getElementById("router-current")!;
    await user.click(within(reviewLine).getByRole("button", { name: "查看所在家族" }));
    expect(window.location.hash).toBe(`#buddy/models/${encodeURIComponent(solMediumId)}`);
    await waitFor(() => expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy());
    expect(await screen.findByRole("heading", { name: "GPT-6 Sol" })).toBeTruthy();
    const tag = document.getElementById(`effort-tag-${solMediumId}`)!;
    await waitFor(() => expect(document.activeElement).toBe(tag));
  });

  it("cuts through search and the enabled filter when a Router reminder jumps to its effort", async () => {
    // The review Router is a disabled effort, so the strip offers 需处理 and the
    // jump must clear whatever hid that family.
    const f = fixture(highId);
    const user = userEvent.setup();
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.type(screen.getByLabelText("搜索模型"), "gpt");
    await screen.findByRole("heading", { name: "模型 1" });
    expect(screen.queryByRole("button", { name: /^Claude Sonnet 5/ })).toBeNull();
    await user.click(screen.getByRole("checkbox", { name: "只看已启用" }));

    await user.click(within(statusStrip()).getByRole("link", { name: "Router 需处理，去处理" }));
    expect(window.location.hash).toBe(`#buddy/models/${encodeURIComponent(highId)}`);
    await waitFor(() => expect(screen.getByLabelText("搜索模型")).toHaveProperty("value", ""));
    expect((screen.getByRole("checkbox", { name: "只看已启用" }) as HTMLInputElement).checked).toBe(false);
    await screen.findByRole("heading", { name: "模型 3" });
    expect(await screen.findByRole("heading", { name: "Claude Sonnet 5" })).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(document.getElementById(`effort-tag-${highId}`)));
    // The jump is a local view change: the snapshot already holds the family.
    expect(f.command).not.toHaveBeenCalled();
  });

  it("turns on the unavailable filter when a deep link names an offline effort", async () => {
    const f = fixture();
    renderBuddy(`#buddy/models/${encodeURIComponent(glmId)}`, f);
    await screen.findByRole("heading", { name: "模型 3" });
    const box = screen.getByRole("checkbox", { name: "显示不可用配置（1）" }) as HTMLInputElement;
    await waitFor(() => expect(box.checked).toBe(true));
    await screen.findByRole("heading", { name: "模型 4" });
    expect(screen.getByRole("button", { name: /^GLM-5，已启用 0\/1，不可用/ })).toBeTruthy();
    const tag = document.getElementById(`effort-tag-${glmId}`)!;
    await waitFor(() => expect(document.activeElement).toBe(tag));
    expect(f.command).not.toHaveBeenCalled();
  });
});

describe("a narrow viewport", () => {
  it("keeps the same section nav usable and the draft intact at a 390px width", async () => {
    const f = fixture();
    const user = userEvent.setup();
    Object.defineProperty(window, "innerWidth", { value: 390, configurable: true });
    renderBuddy("#buddy", f);
    await screen.findByRole("heading", { name: "模型 3" });
    await user.click(screen.getByRole("button", { name: /^Claude Sonnet 5/ }));
    await user.type(await screen.findByLabelText("家族备注"), "窄屏草稿");
    // The narrow layout folds the same nav into a top strip: one entry per
    // section, still driving the same hidden-section switch.
    expect(within(sectionsNav()).getAllByRole("link").map(link => link.textContent)).toEqual(["模型", "Router", "Harness"]);
    await user.click(sectionLink("Harness"));
    await waitFor(() => expect(screen.getByRole("region", { name: "Harness 状态" })).toBeTruthy());
    await user.click(sectionLink("模型"));
    await waitFor(() => expect(screen.getByRole("region", { name: "模型分区" })).toBeTruthy());
    expect(screen.getByRole("heading", { name: "Claude Sonnet 5" })).toBeTruthy();
    expect(screen.getByLabelText("家族备注")).toHaveProperty("value", "窄屏草稿");
    expect(screen.getByText("1 项未保存")).toBeTruthy();
  });
});
