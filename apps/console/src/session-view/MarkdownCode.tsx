import { isValidElement } from "react";
import { useTranslation } from "react-i18next";
import type { ComponentProps } from "react";
import { CodeBlock, CodeBlockContent, CodeBlockLine } from "../components/ui/code-block";
import { useHighlightedCode } from "../hooks/use-highlighted-code";

export function HighlightedCode({ code, language = "text" }: { code: string; language?: string }) {
  const { t } = useTranslation("taskDetail");
  const { lines, failed } = useHighlightedCode(code, language);
  return <CodeBlock size="sm" className="rounded-none border-0 bg-sunk">{failed && <p role="alert" className="text-xs text-fail">{t("sessionView.highlightFailed")}</p>}<CodeBlockContent wrap className="m-0"><>{lines.map((line, index) => <CodeBlockLine key={index}>{line}</CodeBlockLine>)}</></CodeBlockContent></CodeBlock>;
}
export function MarkdownCode({ children, className, node: _node, ...props }: ComponentProps<"code"> & { node?: unknown }) {
  if (!("data-block" in props)) return <code className="bg-sunk px-1 font-mono text-[.9em]">{children}</code>;
  const nested = isValidElement<{ children?: unknown }>(children) ? children.props.children : undefined;
  const code = typeof children === "string" ? children : typeof nested === "string" ? nested : "";
  return <HighlightedCode code={code} language={className?.match(/language-([\w+-]+)/)?.[1]} />;
}
