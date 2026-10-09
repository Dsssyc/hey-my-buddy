import { StrictMode } from "react";
import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { RuntimeVersion } from "./RuntimeVersion";
import { Settings } from "./Settings";
import type { ConsoleApi } from "./api";
import type { RuntimeVersionInfo } from "./types";

const facts = { softwareVersion: "0.29.0", contractVersion: "0.fixture", schemaVersion: 15, sourceCommit: "installed-commit", installedAt: "2026-10-09T00:00:00Z" };
const runtime: RuntimeVersionInfo = { running: { ...facts, mode: "runtime" }, installed: facts };
function apiFor(info: RuntimeVersionInfo = runtime) {
  return { runtimeVersion: vi.fn(async () => info), command: vi.fn(), backupPreflight: vi.fn(async () => ({ ok: true, policy: "fixture", copied: { count: 0, paths: [], entries: [] }, skipped: { count: 0, paths: [], entries: [] }, rejected: { count: 0, paths: [], entries: [] } })) } as unknown as ConsoleApi;
}
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.useRealTimers(); });
it("does not read hidden settings and reads once when shown across refreshes and hide/show", async () => {
  const api = apiFor();
  const props = { api, csrfToken: "csrf", theme: "light" as const, onTheme: vi.fn() };
  const view = render(<StrictMode><Settings {...props} active={false} /></StrictMode>);
  expect(api.runtimeVersion).not.toHaveBeenCalled();
  expect(api.backupPreflight).not.toHaveBeenCalled();
  view.rerender(<StrictMode><Settings {...props} active /></StrictMode>);
  await screen.findByText("正在运行（已安装运行时）");
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  const panel = screen.getByRole("region", { name: "版本" });
  for (const [label, value] of [["软件版本", facts.softwareVersion], ["契约版本", facts.contractVersion], ["schema", "15"], ["来源提交", facts.sourceCommit], ["安装时间", facts.installedAt]]) {
    expect(within(panel).getByText(label).nextElementSibling?.textContent).toBe(value);
  }
  vi.useFakeTimers();
  for (let n = 0; n < 3; n++) view.rerender(<StrictMode><Settings {...props} active refresh={async () => n} /></StrictMode>);
  await act(async () => { vi.advanceTimersByTime(12_000); });
  view.rerender(<StrictMode><Settings {...props} active={false} /></StrictMode>);
  view.rerender(<StrictMode><Settings {...props} active /></StrictMode>);
  await act(async () => {});
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  expect(api.command).not.toHaveBeenCalled();
  expect(within(panel).queryByRole("button")).toBeNull();
});
it("separates running source facts from an installed runtime and marks missing fields", async () => {
  const api = apiFor({ running: { ...facts, mode: "source", softwareVersion: "source-version", sourceCommit: null, installedAt: null }, installed: facts });
  render(<RuntimeVersion api={api} active />);
  await screen.findByText("正在运行（源码）");
  const panel = screen.getByRole("region", { name: "版本" });
  expect(within(panel).getByText("source-version")).toBeTruthy();
  expect(within(panel).getByText("已安装运行时")).toBeTruthy();
  expect(within(panel).getAllByText("未记录")).toHaveLength(2);
  expect(within(panel).getAllByText("installed-commit")).toHaveLength(1);
});
it("displays no recorded installation and no invented product version", async () => {
  render(<RuntimeVersion api={apiFor({ running: { mode: "source", softwareVersion: null, contractVersion: null, schemaVersion: null, sourceCommit: null, installedAt: null }, installed: null })} active />);
  await screen.findByText("正在运行（源码）");
  expect(screen.getAllByText("未记录")).toHaveLength(6);
  expect(screen.queryByText("0.1.0")).toBeNull();
});
it("reports read failures without retrying on subsequent settings renders", async () => {
  const api = apiFor();
  vi.mocked(api.runtimeVersion).mockRejectedValue(new Error("read failed"));
  const view = render(<RuntimeVersion api={api} active />);
  await waitFor(() => expect(screen.queryByRole("alert")?.textContent).toContain("read failed"));
  view.rerender(<RuntimeVersion api={api} active={false} />);
  view.rerender(<RuntimeVersion api={api} active />);
  await waitFor(() => expect(api.runtimeVersion).toHaveBeenCalledTimes(1));
  expect(api.command).not.toHaveBeenCalled();
  expect(api.backupPreflight).not.toHaveBeenCalled();
});
it("reuses a pending read after hiding and ignores replies after unmount", async () => {
  let resolve!: (info: RuntimeVersionInfo) => void;
  const api = apiFor();
  vi.mocked(api.runtimeVersion).mockReturnValue(new Promise(done => { resolve = done; }));
  const view = render(<RuntimeVersion api={api} active />);
  view.rerender(<RuntimeVersion api={api} active={false} />);
  await act(async () => { resolve(runtime); });
  expect(screen.queryByText("正在运行（已安装运行时）")).toBeNull();
  view.rerender(<RuntimeVersion api={api} active />);
  await screen.findByText("正在运行（已安装运行时）");
  expect(api.runtimeVersion).toHaveBeenCalledTimes(1);
  view.unmount();
});
