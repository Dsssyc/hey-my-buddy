import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import enShell from "./en/shell";
import zhShell from "./zh/shell";

/** One namespace per page, so that pages can be written independently. */
const resources = {
  zh: { shell: zhShell },
  en: { shell: enShell },
};
type Language = keyof typeof resources;

declare module "i18next" {
  interface CustomTypeOptions {
    resources: (typeof resources)["zh"];
  }
}

/**
 * The console follows the browser's language list and has no switch of its own
 * (ADR-029). The first entry in a language the console is written in decides
 * the text, and its full tag decides how dates and numbers are written.
 */
function browserLanguage(): { language: Language; locale: string } {
  for (const tag of navigator.languages) {
    const language = tag.toLowerCase().split("-")[0];
    if (language === "zh" || language === "en") return { language, locale: tag };
  }
  return { language: "en", locale: "en" };
}

const { language, locale } = browserLanguage();

// The text is bundled, so the instance is ready before the first render:
// https://www.i18next.com/overview/configuration-options
void i18n.use(initReactI18next).init({
  lng: language,
  resources,
  initAsync: false,
  // React escapes what it renders: https://react.i18next.com/latest/using-with-hooks
  interpolation: { escapeValue: false },
});
// The text is Simplified Chinese whichever Chinese locale the browser asked for.
document.documentElement.lang = language === "zh" ? "zh-CN" : "en";

/** The browser locale that dates, times and numbers are written in. */
export { locale };
