import { useEffect, useState } from "react";
import { Check, Copy } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "./ui/button";
import { Tooltip, TooltipContent, TooltipTrigger } from "./ui/tooltip";

/** An identifier, shortened where it is long, that copies whole. The hint says more of it on hover and focus. */
export function CopyableIdentifier({ value, hint }: { value: string; hint?: string }) {
  return <Identifier key={value} value={value} hint={hint} />;
}

function Identifier({ value, hint }: { value: string; hint?: string }) {
  const { t } = useTranslation("shell");
  const [open, setOpen] = useState(false);
  const [feedback, setFeedback] = useState<{ kind: "copied" | "failed" } | null>(null);
  useEffect(() => {
    if (!feedback) return;
    const timer = setTimeout(() => { setFeedback(null); setOpen(false); }, 2000);
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
  return <Tooltip open={!!feedback || open} onOpenChange={setOpen}>
    <TooltipTrigger asChild>
      <Button type="button" variant="ghost" size="sm" className="h-8 max-w-full justify-start -ml-2 font-mono text-xs font-normal text-muted-foreground"
        aria-label={t("identifier.copy", { value })} onClick={() => void copy()}>
        <span className="truncate">{shortened}</span>
        {feedback?.kind === "copied" ? <Check aria-hidden="true" /> : <Copy aria-hidden="true" />}
      </Button>
    </TooltipTrigger>
    <TooltipContent className="break-all">{message || (hint ? `${value} · ${hint}` : value)}</TooltipContent>
    <span className="sr-only" aria-live="polite" role={message ? "status" : undefined}>{message}</span>
  </Tooltip>;
}
