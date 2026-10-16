import { afterEach, describe, expect, it, vi } from "vitest";

/** Loads the text afresh, including the persisted preference, as a new page would. */
async function load(languages: string[]) {
  vi.resetModules();
  Object.defineProperty(window.navigator, "languages", { value: languages, configurable: true });
  const language = await import("./index");
  const { default: i18n } = await import("i18next");
  return { language, i18n, locale: language.locale, tasks: i18n.t("shell:nav.tasks"), lang: document.documentElement.lang };
}

afterEach(() => {
  vi.restoreAllMocks();
  localStorage.clear();
  document.documentElement.removeAttribute("lang");
});

describe("the console's language", () => {
  it("follows the first supported browser language by default", async () => {
    const english = await load(["fr-FR", "en-GB", "zh-CN"]);
    expect([english.locale, english.tasks, english.lang, english.language.getLanguageChoice()]).toEqual(["en-GB", "Tasks", "en", "browser"]);
    const chinese = await load(["ja", "zh-CN", "en"]);
    expect([chinese.locale, chinese.tasks, chinese.lang]).toEqual(["zh-CN", "任务", "zh-CN"]);
  });

  it("writes Simplified Chinese for every Chinese browser locale and keeps its formatting", async () => {
    const current = await load(["zh-TW", "en"]);
    expect([current.locale, current.tasks, current.lang]).toEqual(["zh-TW", "任务", "zh-CN"]);
  });

  it("uses English when neither browser language is supported", async () => {
    const current = await load(["fr-FR", "de"]);
    expect([current.locale, current.tasks, current.lang]).toEqual(["en", "Tasks", "en"]);
  });

  it("changes text and formatting immediately, retains the choice after reload and can return to the browser", async () => {
    const current = await load(["zh-TW", "en"]);
    current.language.setLanguageChoice("en");
    expect(current.i18n.t("shell:nav.tasks")).toBe("Tasks");
    expect(current.language.locale).toBe("en");
    expect(document.documentElement.lang).toBe("en");
    const reloaded = await load(["zh-TW"]);
    expect(reloaded.tasks).toBe("Tasks");
    expect(reloaded.language.getLanguageChoice()).toBe("en");
    reloaded.language.setLanguageChoice("zh");
    expect(reloaded.language.locale).toBe("zh-CN");
    reloaded.language.setLanguageChoice("browser");
    expect(reloaded.language.locale).toBe("zh-TW");
    expect(reloaded.language.getLanguageChoice()).toBe("browser");
    expect((await load(["en-GB"])).locale).toBe("en-GB");
  });

  it("ignores an unrecognized stored preference", async () => {
    localStorage.setItem("hey-my-buddy.language", "invalid");
    expect((await load(["zh-CN"])).tasks).toBe("任务");
  });

  it("keeps language changes usable when browser storage rejects reading and writing", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new DOMException("Full", "QuotaExceededError"); });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => { throw new DOMException("Blocked", "SecurityError"); });
    const current = await load(["zh-CN"]);
    expect(current.tasks).toBe("任务");
    expect(() => current.language.setLanguageChoice("en")).not.toThrow();
    expect(current.i18n.t("shell:nav.tasks")).toBe("Tasks");
    expect(current.language.getLanguageChoice()).toBe("en");
    expect(() => current.language.setLanguageChoice("browser")).not.toThrow();
    expect(current.i18n.t("shell:nav.tasks")).toBe("任务");
  });
});
