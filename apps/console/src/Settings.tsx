import { useTranslation } from "react-i18next";
import type { ConsoleApi } from "./api";
import type { ThemeChoice } from "./theme";
import type { ConsoleAccess, ReminderSettings } from "./types";
import type { LanguageChoice } from "./i18n";
import { ScrollArea } from "./components/ui/scroll-area";
import { Segmented, SegmentedItem } from "./components/ui/segmented";
import { Section } from "./settings/Section";
import { AccessSection } from "./settings/AccessSection";
import { ReminderSection } from "./settings/ReminderSection";
import { VersionSection } from "./settings/VersionSection";
import { StorageSection } from "./settings/StorageSection";

/** Each setting belongs to its named section; visiting other pages preserves drafts. */
export function Settings({ api, csrfToken, connectionError = "", writesAvailable = true, access, reminders,
  refresh = async () => {}, theme, onTheme, language, onLanguage, active = true }: {
  api: ConsoleApi; csrfToken: string; connectionError?: string; writesAvailable?: boolean;
  access?: ConsoleAccess; reminders?: ReminderSettings; refresh?: () => Promise<unknown>;
  theme: ThemeChoice; onTheme: (choice: ThemeChoice) => void;
  language: LanguageChoice; onLanguage: (choice: LanguageChoice) => void; active?: boolean;
}) {
  const { t } = useTranslation("settings");
  return <ScrollArea className="h-full min-h-0 bg-card" viewportProps={{ "aria-label": t("title"), tabIndex: 0 }}>
    <div data-slot="settings-page" className="min-w-0 text-sm">
      <Section title={t("theme")}>
        <Segmented value={theme} onValueChange={value => onTheme(value as ThemeChoice)} aria-label={t("theme")}>
          {(["system", "light", "dark"] as const).map(choice => <SegmentedItem key={choice} value={choice}>{t(choice)}</SegmentedItem>)}
        </Segmented>
      </Section>
      <Section title={t("language")}>
        <Segmented value={language} onValueChange={value => onLanguage(value as LanguageChoice)} aria-label={t("language")}>
          {(["browser", "zh", "en"] as const).map(choice => <SegmentedItem key={choice} value={choice}>{t(choice === "zh" ? "chinese" : choice === "en" ? "english" : "browser")}</SegmentedItem>)}
        </Segmented>
      </Section>
      <ReminderSection api={api} csrfToken={csrfToken} settings={reminders} refresh={refresh} unavailable={!!connectionError || !writesAvailable} />
      <AccessSection api={api} csrfToken={csrfToken} access={access} refresh={refresh} unavailable={!!connectionError || !writesAvailable} />
      <VersionSection api={api} active={active} />
      <StorageSection api={api} csrfToken={csrfToken} connectionError={connectionError} />
    </div>
  </ScrollArea>;
}
