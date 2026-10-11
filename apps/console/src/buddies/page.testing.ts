import { screen, waitFor, within } from "@testing-library/react";
import { expect } from "vitest";
import type userEvent from "@testing-library/user-event";

/**
 * How the tests find things on the Buddy page's models view. They read what a
 * user reads: the table, its rows by the model's identity, the side column by
 * the same identity, and a level by its name.
 */
type User = ReturnType<typeof userEvent.setup>;

export const modelsTable = () => screen.getByRole("table", { name: "模型" });
/** The button that selects a model's row, named `harness / provider / model`. */
export const modelButton = (identity: string) => within(modelsTable()).getByRole("button", { name: identity });
export const queryModel = (identity: string) => within(modelsTable()).queryByRole("button", { name: identity });
export const modelRow = (identity: string) => modelButton(identity).closest("tr")!;
/** A buddy's chip in a model's row. */
export const chip = (identity: string, effort: string) => within(modelRow(identity)).getByText(effort);
/** The handle that folds a harness's rows away, whichever way it currently points. */
export const foldHandle = (name: string) => within(modelsTable()).getByRole("button", { name: new RegExp(`^(展开|收起) ${name}$`) });
export const queryFoldHandle = (name: string) => within(modelsTable()).queryByRole("button", { name: new RegExp(`^(展开|收起) ${name}$`) });
/** Waits until the toolbar counts this many enabled buddies among this many listed ones. */
export const listed = (enabled: number, total: number) => waitFor(() => {
  expect(screen.getAllByRole("status").map(item => item.textContent)).toContain(`启用 ${enabled} / ${total}`);
});
export const search = () => screen.getByRole("searchbox", { name: "搜索 buddy" });
/** The side column, which is named by the identity of the model it edits. */
export const pane = (identity?: string) => screen.getByRole("complementary", identity ? { name: identity } : undefined);
export const level = (effort: string) => within(pane()).getByRole("group", { name: effort });
export const levelSwitch = (effort: string) => within(pane()).getByRole("switch", { name: `启用 ${effort}` });
export const levelPreference = (effort: string) => within(pane()).getByRole("combobox", { name: `${effort} 的偏好` });
export const defaultPreference = (name: string) => within(within(pane()).getByRole("radiogroup", { name: "默认偏好" })).getByRole("radio", { name });
export const note = () => within(pane()).getByRole("textbox", { name: "备注" });

/** The notice above the page that a newer publication exists while a draft is open: the one that offers the reload. */
export const conflictNotice = () => screen.queryAllByRole("alert")
  .find(node => within(node).queryByRole("button", { name: "重新加载最新版本" }));

/** Selects a model's row, unfolding its harness first when the row is folded away. */
export async function selectModel(user: User, identity: string, harness: string) {
  if (!queryModel(identity)) await user.click(foldHandle(harness));
  await user.click(modelButton(identity));
  await waitFor(() => pane(identity));
}

/** Picks one option of a level's own preference. */
export async function choosePreference(user: User, effort: string, option: string) {
  await user.click(levelPreference(effort));
  await user.click(await screen.findByRole("option", { name: option }));
}
