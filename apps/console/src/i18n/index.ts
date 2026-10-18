import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import enBuddies from "./en/buddies";
import enCommon from "./en/common";
import enReasons from "./en/reasons";
import enConsole from "./en/console";
import enMacroHeader from "./en/macroHeader";
import enShell from "./en/shell";
import enSettings from "./en/settings";
import enSettingsStorage from "./en/settings-storage";
import enTaskDetail from "./en/taskDetail";
import { tasks as enTasks } from "./en/tasks";
import zhBuddies from "./zh/buddies";
import zhCommon from "./zh/common";
import zhReasons from "./zh/reasons";
import zhConsole from "./zh/console";
import zhMacroHeader from "./zh/macroHeader";
import zhShell from "./zh/shell";
import zhSettings from "./zh/settings";
import zhSettingsStorage from "./zh/settings-storage";
import zhTaskDetail from "./zh/taskDetail";
import { tasks as zhTasks } from "./zh/tasks";

/** One namespace per page, so that pages can be written independently. */
const resources = {
  zh: { reasons: zhReasons, shell: zhShell, buddies: zhBuddies, taskDetail: zhTaskDetail, tasks: zhTasks,
    macroHeader: zhMacroHeader, common: zhCommon, console: zhConsole, settingsStorage: zhSettingsStorage, settings: zhSettings },
  en: { reasons: enReasons, shell: enShell, buddies: enBuddies, taskDetail: enTaskDetail, tasks: enTasks,
    macroHeader: enMacroHeader, common: enCommon, console: enConsole, settingsStorage: enSettingsStorage, settings: enSettings },
};
type Language = keyof typeof resources;
export type LanguageChoice = "browser" | Language;
const LANGUAGE_STORAGE_KEY = "hey-my-buddy.language";

declare module "i18next" {
  interface CustomTypeOptions {
    resources: (typeof resources)["zh"];
  }
}

/** The first supported browser language supplies both the text and formatting locale. */
function browserLanguage(): { language: Language; locale: string } {
  for (const tag of navigator.languages) {
    const language = tag.toLowerCase().split("-")[0];
    if (language === "zh" || language === "en") return { language, locale: tag };
  }
  return { language: "en", locale: "en" };
}

function savedLanguageChoice(): LanguageChoice {
  try {
    const saved = localStorage.getItem(LANGUAGE_STORAGE_KEY);
    if (saved === "zh" || saved === "en") return saved;
  } catch { /* Browser storage may be unavailable; the in-memory choice still works. */ }
  return "browser";
}

let choice = savedLanguageChoice();
function resolvedLanguage(value: LanguageChoice): { language: Language; locale: string } {
  return value === "browser" ? browserLanguage() : { language: value, locale: value === "zh" ? "zh-CN" : "en" };
}
const initial = resolvedLanguage(choice);
/** The active locale for dates, times and numbers, including explicit language choices. */
export let locale = initial.locale;

// Bundled text is ready before the first render.
void i18n.use(initReactI18next).init({
  lng: initial.language,
  resources,
  initAsync: false,
  interpolation: { escapeValue: false },
});
document.documentElement.lang = initial.language === "zh" ? "zh-CN" : "en";

export function getLanguageChoice(): LanguageChoice { return choice; }

/** Changes every mounted view immediately; persistence is best effort. */
export function setLanguageChoice(value: LanguageChoice): void {
  choice = value;
  const next = resolvedLanguage(value);
  locale = next.locale;
  document.documentElement.lang = next.language === "zh" ? "zh-CN" : "en";
  void i18n.changeLanguage(next.language);
  try {
    if (value === "browser") localStorage.removeItem(LANGUAGE_STORAGE_KEY);
    else localStorage.setItem(LANGUAGE_STORAGE_KEY, value);
  } catch { /* The current window keeps working when browser storage is blocked. */ }
}
