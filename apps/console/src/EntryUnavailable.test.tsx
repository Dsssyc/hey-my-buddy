import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import * as api from "./api";
import { App } from "./App";
import { setLanguageChoice } from "./i18n";

afterEach(() => {
  cleanup();
  document.querySelector('meta[name="hey-my-buddy-reason"]')?.remove();
  setLanguageChoice("browser");
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("an unavailable console entry", () => {
  for (const language of ["zh", "en"] as const) it(`renders the launch reason in ${language} before any API is created or called`, async () => {
    const meta = document.createElement("meta");
    meta.name = "hey-my-buddy-reason";
    meta.content = "console.entry-unavailable";
    document.head.append(meta);
    const client = vi.spyOn(api, "createApi");
    const fetch = vi.fn();
    vi.stubGlobal("fetch", fetch);
    setLanguageChoice(language);
    render(<App />);
    const title = language === "zh" ? "控制台入口已失效或暂不可用" : "The console entry has expired or is unavailable";
    expect(screen.getByRole("heading", { name: title })).toBeTruthy();
    expect(document.title).toBe(title);
    expect(screen.getByRole("alert").textContent).toContain(language === "zh" ? "后台任务不受影响" : "Background tasks are unaffected");
    expect(screen.getByText("hey-my-buddy console")).toBeTruthy();
    await userEvent.setup().click(screen.getByRole("radio", { name: language === "zh" ? "English" : "中文" }));
    expect(screen.getByRole("alert").textContent).toContain(language === "zh" ? "Background tasks are unaffected" : "后台任务不受影响");
    expect(client).not.toHaveBeenCalled();
    expect(fetch).not.toHaveBeenCalled();
  });
});
