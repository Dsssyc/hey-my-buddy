import { screen, waitFor, within } from "@testing-library/react";
import type userEvent from "@testing-library/user-event";

type User = ReturnType<typeof userEvent.setup>;

/** A harness section keeps its independently expanded settings below its head. */
export const harnessSection = (name: string) => screen.getByRole("region", { name });
export const harnessButton = (name: string) => within(harnessSection(name)).getByRole("button", { name: `${name} 账户` });
export const harnessPane = (name?: string) => name
  ? screen.getByRole("group", { name: `${name} 设置` })
  : document.querySelector<HTMLElement>("[data-slot=harness-details]")!;
export const paneBlock = (title: "位置" | "账户" | "冷却", name?: string) => within(harnessPane(name)).getByRole("group", { name: title });
export const fieldValue = (block: HTMLElement, name: string) => within(block).getByText(name, { selector: "span" }).nextElementSibling as HTMLElement;
export const toolbarAlert = () => screen.findByRole("alert");

export async function selectHarness(user: User, name: string) {
  if (harnessButton(name).getAttribute("aria-expanded") !== "true") await user.click(harnessButton(name));
  await waitFor(() => harnessPane(name));
}
