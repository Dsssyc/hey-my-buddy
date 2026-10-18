import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import { Segmented, SegmentedItem } from "@/components/ui/segmented";
import { getLanguageChoice, setLanguageChoice } from "./i18n";
import type { LanguageChoice } from "./i18n";
import { reasonText } from "./i18n/reason";

/** The launch response uses the main bundle, before a console client is created. */
export function EntryUnavailable() {
  const { t } = useTranslation(["shell", "settings"]);
  const title = t("startup.entryUnavailable");
  useEffect(() => { document.title = title; }, [title]);
  return <main className="flex min-h-dvh flex-col items-center justify-center gap-5 bg-card p-6 text-center text-foreground">
    <h1 className="text-lg font-semibold">{title}</h1>
    <p className="max-w-xl text-ink2" role="alert">{reasonText({ code: "console.entry-unavailable", params: {} })}</p>
    <code className="select-all font-mono text-sm">{t("startup.reopenCommand")}</code>
    <Segmented value={getLanguageChoice()} onValueChange={value => setLanguageChoice(value as LanguageChoice)} aria-label={t("settings:language")}>
      {(["browser", "zh", "en"] as const).map(choice => <SegmentedItem key={choice} value={choice}>{t(`settings:${choice === "zh" ? "chinese" : choice === "en" ? "english" : "browser"}`)}</SegmentedItem>)}
    </Segmented>
  </main>;
}
