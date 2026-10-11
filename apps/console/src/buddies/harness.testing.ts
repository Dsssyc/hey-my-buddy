import { screen, waitFor, within } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";

/**
 * How the tests find things on the Buddy page's harness view. They read what a
 * user reads: the table, its rows by the harness's name, the side column by the
 * same name, and its three blocks by their own.
 */
type User = ReturnType<typeof userEvent.setup>;

export const harnessTable = () => screen.getByRole("table", { name: "Harness" });
/** The button that selects a harness's row, named by the harness. */
export const harnessButton = (name: string) => within(harnessTable()).getByRole("button", { name });
export const harnessRow = (name: string) => harnessButton(name).closest("tr")!;
/** The cells of a harness's row, in order: name, status, version, account, quota. */
export const harnessCells = (name: string) => [...harnessRow(name).cells];
/** The side column, which is named by the harness it shows. */
export const harnessPane = (name?: string) => screen.getByRole("complementary", name ? { name } : undefined);
/** A block of the side column: 位置, 账户 or 额度. */
export const paneBlock = (title: "位置" | "账户" | "额度") => within(harnessPane()).getByRole("group", { name: title });
/** The value of a field in a block: whatever stands right after its name. */
export const fieldValue = (block: HTMLElement, name: string) => within(block).getByText(name, { selector: "span" }).nextElementSibling as HTMLElement;
/** The notice under the toolbar. */
export const toolbarAlert = () => screen.findByRole("alert");

/** Selects a harness's row. */
export async function selectHarness(user: User, name: string) {
  await user.click(harnessButton(name));
  await waitFor(() => harnessPane(name));
}
