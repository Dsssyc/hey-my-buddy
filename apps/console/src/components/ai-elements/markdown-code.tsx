import { cn } from "cn";
import { CheckIcon, ChevronRightIcon, CopyIcon } from "lucide-react";
import type { ComponentProps } from "react";
import { isValidElement, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { CodeBlock } from "./code-block";

// Streamdown marks fenced code with data-block. The override leaves inline
// code inline, and mounts the highlighter only while the fence is expanded.
export function MarkdownCode({ children, className, node: _node, ...props }: ComponentProps<"code"> & { node?: unknown }) {
  const { t } = useTranslation("taskDetail");
  const [open, setOpen] = useState(false);
  const [copied, setCopied] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const contentId = useId();

  if (!("data-block" in props)) return <code className={cn("bg-sunk px-1 font-mono text-[.9em]", className)}>{children}</code>;
  const nested = isValidElement<{ children?: unknown }>(children) ? children.props.children : undefined;
  const code = typeof children === "string" ? children : typeof nested === "string" ? nested : "";
  const language = className?.match(/(?:^|\s)language-([\w+-]+)/)?.[1] ?? "text";
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setCopyFailed(false);
    } catch {
      setCopyFailed(true);
    }
  };

  return <div data-slot="session-markdown-code" className="my-3 min-w-0 border-t border-line2">
    <div className="flex min-w-0 items-center justify-between gap-2 py-1.5">
      <button type="button" aria-expanded={open} aria-controls={contentId} onClick={() => setOpen(!open)} className="flex min-w-0 items-center gap-1 text-left text-xs text-ink2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        <ChevronRightIcon size={14} aria-hidden="true" className={cn("shrink-0 transition-transform", open && "rotate-90")} />
        <span>{open ? t("sessionView.collapseCode") : t("sessionView.expandCode")}</span>
        <span className="truncate font-mono text-ink3">{language === "text" ? t("sessionView.code") : language}</span>
      </button>
      <button type="button" onClick={copy} aria-label={t("sessionView.copy")} className="flex shrink-0 items-center gap-1 text-xs text-ink2 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
        {copied ? <CheckIcon size={14} aria-hidden="true" /> : <CopyIcon size={14} aria-hidden="true" />}
        {copied ? t("sessionView.copied") : t("sessionView.copy")}
      </button>
    </div>
    {copyFailed && <span role="alert" className="text-xs text-fail">{t("sessionView.copyFailed")}</span>}
    {open && <div id={contentId}><CodeBlock code={code} language={language} /></div>}
  </div>;
}
