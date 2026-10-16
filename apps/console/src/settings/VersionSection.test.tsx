import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { VersionSection } from "./VersionSection";
import { TooltipProvider } from "../components/ui/tooltip";
import type { ConsoleApi } from "../api";
import type { RuntimeVersionInfo } from "../types";
import { formatDateTime } from "../i18n/format";
import "../i18n";

const facts = { softwareVersion: "0.29.0", contractVersion: "0.fixture", schemaVersion: 15, sourceCommit: "installed-commit", installedAt: "2026-10-09T00:00:00Z" };
const runtime: RuntimeVersionInfo = { running: { ...facts, mode: "runtime" }, installed: facts };
function apiFor(info: RuntimeVersionInfo = runtime) {
  return { runtimeVersion: vi.fn(async () => info), command: vi.fn() } as unknown as ConsoleApi;
}
function viewFor(api: ConsoleApi, active = true) {
  const renderView = (visible: boolean, client = api) => <StrictMode><TooltipProvider><VersionSection api={client} active={visible} /></TooltipProvider></StrictMode>;
  const view = render(renderView(active));
  return { ...view, update: (visible: boolean, client?: ConsoleApi) => view.rerender(renderView(visible, client)) };
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("reads once on first visibility through StrictMode, rerenders and hide/show", async () => {
  const api = apiFor();
  const view = viewFor(api, false);
  expect(api.runtimeVersion).not.toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toBe("尚未读取版本");
  view.update(true);
  await screen.findByText(facts.softwareVersion);
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  for (let n = 0; n < 3; n++) view.update(true, { ...api });
  view.update(false); view.update(true);
  await act(async () => {});
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  expect(api.command).not.toHaveBeenCalled();
  expect(screen.getByText("安装时间").nextElementSibling?.textContent).toBe(formatDateTime(Date.parse(facts.installedAt)));
  expect(screen.queryByText("契约版本")).toBeNull();
});

it("separates source facts from the installed runtime and records missing fields honestly", async () => {
  const api = apiFor({ running: { ...facts, mode: "source", softwareVersion: "source-version", sourceCommit: null, installedAt: null }, installed: facts });
  viewFor(api);
  await screen.findByText("正在运行（源码）");
  expect(screen.getByText("source-version")).toBeTruthy();
  expect(screen.getByText("已安装运行时")).toBeTruthy();
  expect(screen.getAllByRole("button", { name: "详细信息" })).toHaveLength(2);
  const user = userEvent.setup();
  for (const button of screen.getAllByRole("button", { name: "详细信息" })) await user.click(button);
  expect(screen.getAllByText("未记录")).toHaveLength(2);
  expect(screen.getAllByText("installed-commit")).toHaveLength(1);
});

it("does not invent a version or an installation when neither is recorded", async () => {
  viewFor(apiFor({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null }));
  await screen.findByText("正在运行（源码）");
  expect(screen.getAllByText("未记录")).toHaveLength(3);
  await userEvent.setup().click(screen.getByRole("button", { name: "详细信息" }));
  expect(screen.getAllByText("未记录")).toHaveLength(6);
  expect(screen.queryByText("0.1.0")).toBeNull();
});

it("reports read failures and does not retry on subsequent visits", async () => {
  const api = apiFor();
  vi.mocked(api.runtimeVersion).mockRejectedValue(new Error("read failed"));
  const view = viewFor(api);
  expect((await screen.findByRole("alert")).textContent).toContain("无法读取版本：read failed");
  view.update(false); view.update(true);
  await waitFor(() => expect(api.runtimeVersion).toHaveBeenCalledTimes(1));
  expect(api.command).not.toHaveBeenCalled();
});

it("reuses a pending read after hiding and ignores its reply until visible", async () => {
  let resolve!: (info: RuntimeVersionInfo) => void;
  const api = apiFor();
  vi.mocked(api.runtimeVersion).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = viewFor(api);
  view.update(false);
  await act(async () => { resolve(runtime); });
  expect(screen.queryByText(facts.softwareVersion)).toBeNull();
  view.update(true); await screen.findByText(facts.softwareVersion);
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
});

it("ignores a pending reply after unmount", async () => {
  let resolve!: (info: RuntimeVersionInfo) => void;
  const api = apiFor();
  vi.mocked(api.runtimeVersion).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = viewFor(api); view.unmount();
  await act(async () => { resolve(runtime); });
  expect(screen.queryByText(facts.softwareVersion)).toBeNull();
});

it.each(["runtime", "source"] as const)("uses independent collapsed Radix disclosures for %s facts", async mode => {
  const api = apiFor({ running: { ...facts, mode }, installed: facts });
  viewFor(api);
  const buttons = await screen.findAllByRole("button", { name: "详细信息" });
  expect(buttons).toHaveLength(mode === "source" ? 2 : 1);
  expect(screen.queryByText("契约版本")).toBeNull();
  const user = userEvent.setup();
  for (const button of buttons) {
    expect(button.getAttribute("aria-expanded")).toBe("false");
    button.focus(); await user.keyboard("{Enter}");
    expect(button.getAttribute("aria-expanded")).toBe("true");
    const content = document.getElementById(button.getAttribute("aria-controls")!)!;
    for (const [label, value] of [["契约版本", facts.contractVersion], ["schema", "15"], ["来源提交", facts.sourceCommit]]) expect(within(content).getByText(label).nextElementSibling?.textContent).toBe(value);
    for (const other of buttons.filter(value => value !== button)) expect(other.getAttribute("aria-expanded")).toBe("false");
    await user.keyboard("{Enter}"); expect(button.getAttribute("aria-expanded")).toBe("false");
  }
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  expect(api.command).not.toHaveBeenCalled();
});
