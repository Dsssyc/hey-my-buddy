import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

/** Copy feedback replaces the identifier in the same information-row cell. */
export function HeaderIdentifier({ value }: { value: string }) {
  const { t } = useTranslation("shell");
  const [feedback, setFeedback] = useState<"copied" | "failed" | null>(null);
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(null), 2000);
    return () => clearTimeout(timer);
  }, [feedback]);
  async function copy() {
    try { await navigator.clipboard.writeText(value); setFeedback("copied"); }
    catch { setFeedback("failed"); }
  }
  const prefix = value.startsWith("macro-") ? 14 : value.startsWith("run:") ? 12 : 8;
  return <button type="button" onClick={() => void copy()} aria-label={t("identifier.copy", { value })} className="relative max-w-full truncate border-b border-dotted border-ink3 font-mono text-xs text-ink outline-none transition-colors duration-150 hover:border-ink focus-visible:ring-[3px] focus-visible:ring-ring/50 motion-reduce:transition-none">
    <span className={feedback ? "invisible" : undefined}>{value.length > 20 ? `${value.slice(0, prefix)}…${value.slice(-4)}` : value}</span>
    <span aria-live="polite" className="absolute inset-0 truncate text-left">{feedback ? t(`identifier.${feedback}`) : ""}</span>
  </button>;
}
