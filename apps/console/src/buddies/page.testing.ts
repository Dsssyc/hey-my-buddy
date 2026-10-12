import { screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";
import type userEvent from "@testing-library/user-event";

type User = ReturnType<typeof userEvent.setup>;

/** The inline catalogue: identities belong to rows, efforts to the scale. */
export const catalogue = () => screen.getByRole("region", { name: "目录" });
export const modelButton = (identity: string) => within(catalogue()).getByRole("button", { name: identity });
export const queryModel = (identity: string) => within(catalogue()).queryByRole("button", { name: identity });
export const modelRow = (identity: string) => modelButton(identity).closest<HTMLElement>("[data-slot=model-row]")!;
export const chip = (identity: string, effort: string) => within(modelRow(identity)).getByText(effort).closest<HTMLElement>("[data-slot=enabled-effort]")!;
export const foldHandle = (name: string) => within(catalogue()).getByRole("button", { name: new RegExp(`^(展开|收起) ${name}$`) });
export const queryFoldHandle = (name: string) => within(catalogue()).queryByRole("button", { name: new RegExp(`^(展开|收起) ${name}$`) });
export const listed = (enabled: number, total: number) => waitFor(() => {
  expect(screen.getAllByRole("status").map(item => item.textContent)).toContain(`启用 ${enabled} / ${total}`);
});
export const search = () => screen.getByRole("searchbox", { name: "搜索 buddy" });
export const pane = (identity?: string) => identity
  ? screen.getByRole("region", { name: identity })
  : document.querySelector<HTMLElement>("[data-slot=model-drawer]")!;
export const level = (effort: string) => within(pane()).getByRole("group", { name: effort });
export const effortCell = (effort: string) => within(pane()).getByRole("button", { name: effort });
export const levelSwitch = (effort: string) => within(pane()).getByRole("switch", { name: `启用 ${effort}` });
export const levelPreference = (effort: string) => within(pane()).getByRole("radiogroup", { name: `${effort} 的偏好` });
export const defaultPreference = (name: string) => within(within(pane()).getByRole("radiogroup", { name: "默认偏好" })).getByRole("radio", { name });
export const note = () => within(pane()).getByRole("textbox", { name: "备注" });
export const conflictNotice = () => screen.queryAllByRole("alert")
  .find(node => within(node).queryByRole("button", { name: "重新加载最新版本" }));

export async function selectModel(user: User, identity: string, harness: string) {
  if (!queryModel(identity)) await user.click(foldHandle(harness));
  if (modelButton(identity).getAttribute("aria-expanded") !== "true") await user.click(modelButton(identity));
  await waitFor(() => pane(identity));
}

export async function choosePreference(user: User, effort: string, option: string) {
  if (effortCell(effort).getAttribute("aria-pressed") !== "true") await user.click(effortCell(effort));
  await user.click(within(levelPreference(effort)).getByRole("radio", { name: option }));
}
