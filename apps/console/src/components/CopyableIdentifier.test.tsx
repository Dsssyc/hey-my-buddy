import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import i18n from "i18next";
import { CopyableIdentifier } from "./CopyableIdentifier";

afterEach(async () => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); await i18n.changeLanguage("zh"); });

const id = "macro-79771ce9-890b-4cab-867b-4e05c68791da";

describe("copyable identifiers", () => {
  it("shows a shortened value, exposes the complete value on hover and copies it with the keyboard", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    render(<CopyableIdentifier value={id} />);
    expect(screen.getByText("macro-79771ce9…91da").getAttribute("title")).toBe(id);
    const button = screen.getByRole("button", { name: `复制标识 ${id}` });
    await user.hover(button);
    expect((await screen.findByRole("tooltip")).textContent).toBe(id);
    await user.tab();
    expect(document.activeElement).toBe(button);
    await user.keyboard("{Enter}");
    expect(write).toHaveBeenCalledWith(id);
    expect(screen.getByRole("status").textContent).toBe("已复制");
  });

  it("clears feedback after a brief interval and when the selected identifier changes", async () => {
    userEvent.setup();
    vi.spyOn(navigator.clipboard, "writeText").mockResolvedValue();
    vi.useFakeTimers();
    const view = render(<CopyableIdentifier value={id} />);
    await act(async () => { fireEvent.click(screen.getByRole("button")); });
    expect(screen.getByRole("status").textContent).toBe("已复制");
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(screen.queryByRole("status")).toBeNull();
    await act(async () => { fireEvent.click(screen.getByRole("button")); });
    view.rerender(<CopyableIdentifier value="run:another-run" />);
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.getByRole("button", { name: "复制标识 run:another-run" })).toBeTruthy();
  });

  it("reports clipboard denial and successful retry in English", async () => {
    const user = userEvent.setup();
    const write = vi.spyOn(navigator.clipboard, "writeText")
      .mockRejectedValueOnce(new Error("denied")).mockResolvedValue();
    await i18n.changeLanguage("en");
    render(<CopyableIdentifier value={id} />);
    const button = screen.getByRole("button", { name: `Copy identifier ${id}` });
    await user.click(button);
    expect(screen.getByRole("status").textContent).toBe("Copy failed; try again");
    await user.click(button);
    expect(write).toHaveBeenLastCalledWith(id);
    expect(screen.getByRole("status").textContent).toBe("Copied");
  });
});
