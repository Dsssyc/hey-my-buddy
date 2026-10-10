import { afterEach, describe, expect, it, vi } from "vitest";

/** Loads the text afresh for a browser that reports these languages. */
async function load(languages: string[]) {
  vi.resetModules();
  Object.defineProperty(window.navigator, "languages", { value: languages, configurable: true });
  const { locale } = await import("./index");
  const { default: i18n } = await import("i18next");
  return { locale, tasks: i18n.t("shell:nav.tasks"), lang: document.documentElement.lang };
}

afterEach(() => { document.documentElement.removeAttribute("lang"); });

describe("the console's language", () => {
  it("follows the first browser language it is written in", async () => {
    expect(await load(["fr-FR", "en-GB", "zh-CN"])).toEqual({ locale: "en-GB", tasks: "Tasks", lang: "en" });
    expect(await load(["ja", "zh-CN", "en"])).toEqual({ locale: "zh-CN", tasks: "任务", lang: "zh-CN" });
  });

  it("writes Simplified Chinese for every Chinese locale and keeps that locale's dates", async () => {
    expect(await load(["zh-TW", "en"])).toEqual({ locale: "zh-TW", tasks: "任务", lang: "zh-CN" });
  });

  it("is English for a browser that lists neither language", async () => {
    expect(await load(["fr-FR", "de"])).toEqual({ locale: "en", tasks: "Tasks", lang: "en" });
  });
});
