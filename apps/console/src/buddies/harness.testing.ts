import { screen, within } from "@testing-library/react";

export const harnessSection = (name: string) => screen.getByRole("region", { name });
export const harnessTitle = (harness: string) => document.getElementById(`harness-title-${harness}`)!;
export const harnessPane = (name?: string) => name
  ? screen.getByRole("group", { name: `${name} 设置` })
  : document.querySelector<HTMLElement>("[data-slot=harness-details]")!;
export const paneBlock = (title: "位置" | "账户", name?: string) => within(harnessPane(name)).getByRole("group", { name: title });
export const fieldValue = (block: HTMLElement, name: string) => within(block).getByText(name, { selector: "span" }).nextElementSibling as HTMLElement;
export const toolbarAlert = () => screen.findByRole("alert");
