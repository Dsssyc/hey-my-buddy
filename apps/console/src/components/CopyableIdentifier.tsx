import { useEffect, useState } from "react";
import { Check, Copy, CircleX } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "./ui/button";

/** A middle-shortened identifier that copies whole, with feedback in its icon. */
export function CopyableIdentifier({ value }: { value: string }) {
  return <Identifier key={value} value={value} />;
}

function Identifier({ value }: { value: string }) {
  const { t } = useTranslation("shell");
  const [feedback, setFeedback] = useState<{ kind: "copied" | "failed" } | null>(null);
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => setFeedback(null), 2000);
    return () => clearTimeout(timer);
  }, [feedback]);
  const prefixLength = value.startsWith("macro-") ? 6 : value.startsWith("run:") ? 4 : 0;
  const shortened = value.length > 20 ? `${value.slice(0, prefixLength + 8)}…${value.slice(-4)}` : value;
  const message = feedback?.kind === "copied" ? t("identifier.copied") : feedback?.kind === "failed" ? t("identifier.failed") : "";
  async function copy() {
    try {
      await navigator.clipboard.writeText(value);
      setFeedback({ kind: "copied" });
    } catch {
      setFeedback({ kind: "failed" });
    }
  }
  return <>
      <Button type="button" variant="ghost" size="sm" data-slot="copyable-identifier" data-feedback={feedback?.kind}
        className="h-5 max-w-full shrink-0 justify-start gap-1 px-0 has-[>svg]:px-0 align-baseline font-mono text-xs leading-5 font-normal text-ink"
        aria-label={t("identifier.copy", { value })} onClick={() => void copy()}>
        <span className="truncate">{shortened}</span>
        {feedback?.kind === "copied" ? <Check aria-hidden="true" /> : feedback?.kind === "failed" ? <CircleX aria-hidden="true" className="text-fail"/> : <Copy aria-hidden="true" className="text-ink3"/>}
      </Button>
    <span className="sr-only" aria-live="polite" role={message ? "status" : undefined}>{message}</span>
  </>;
}
