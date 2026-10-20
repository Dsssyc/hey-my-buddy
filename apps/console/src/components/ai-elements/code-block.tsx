import { cn } from "cn";
import type { ComponentProps, CSSProperties } from "react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { HighlighterCore, ThemedToken } from "shiki/core";

// Each grammar is a separate chunk. Text and unknown languages remain plain.
const grammars = {
  bash: () => import("@shikijs/langs/bash").then(module => module.default),
  css: () => import("@shikijs/langs/css").then(module => module.default),
  diff: () => import("@shikijs/langs/diff").then(module => module.default),
  go: () => import("@shikijs/langs/go").then(module => module.default),
  html: () => import("@shikijs/langs/html").then(module => module.default),
  javascript: () => import("@shikijs/langs/javascript").then(module => module.default),
  json: () => import("@shikijs/langs/json").then(module => module.default),
  markdown: () => import("@shikijs/langs/markdown").then(module => module.default),
  python: () => import("@shikijs/langs/python").then(module => module.default),
  rust: () => import("@shikijs/langs/rust").then(module => module.default),
  sql: () => import("@shikijs/langs/sql").then(module => module.default),
  tsx: () => import("@shikijs/langs/tsx").then(module => module.default),
  typescript: () => import("@shikijs/langs/typescript").then(module => module.default),
  yaml: () => import("@shikijs/langs/yaml").then(module => module.default),
};
type Grammar = keyof typeof grammars;
const aliases: Record<string, Grammar> = { js: "javascript", ts: "typescript", py: "python", sh: "bash", shell: "bash", md: "markdown", yml: "yaml" };
const highlighters = new Map<Grammar, Promise<HighlighterCore>>();
type Tokens = { light: ThemedToken[][]; dark: ThemedToken[][] };
type Highlighted = { code: string; language: string; tokens?: Tokens; failed?: boolean };

async function highlighterFor(language: Grammar): Promise<HighlighterCore> {
  let pending = highlighters.get(language);
  if (!pending) {
    pending = Promise.all([
      import("shiki/core"), import("shiki/engine/javascript"),
      import("@shikijs/themes/github-light"), import("@shikijs/themes/github-dark"), grammars[language](),
    ]).then(([core, engine, light, dark, grammar]) => core.createHighlighterCore({
      engine: engine.createJavaScriptRegexEngine(), langs: [grammar], themes: [light.default, dark.default], warnings: false,
    })).catch((error: unknown) => {
      highlighters.delete(language);
      throw error;
    });
    highlighters.set(language, pending);
  }
  return pending;
}

function canonicalLanguage(language: string | undefined): Grammar | null {
  if (!language || language === "text" || language === "plain") return null;
  const normalized = language.toLowerCase();
  if (Object.hasOwn(grammars, normalized)) return normalized as Grammar;
  return aliases[normalized] ?? null;
}

async function highlight(code: string, language: Grammar): Promise<Tokens> {
  const highlighter = await highlighterFor(language);
  return {
    light: highlighter.codeToTokens(code, { lang: language, theme: "github-light" }).tokens,
    dark: highlighter.codeToTokens(code, { lang: language, theme: "github-dark" }).tokens,
  };
}

function TokenLines({ lines, className }: { lines: ThemedToken[][]; className: string }) {
  return <pre className={cn("m-0 font-mono text-xs leading-relaxed", className)}><code>{lines.map((line, index) => <span key={index} className="block min-h-[1lh]">{line.map((token, tokenIndex) => <span key={tokenIndex} style={{ color: token.color, backgroundColor: token.bgColor, fontStyle: token.fontStyle && token.fontStyle & 1 ? "italic" : undefined, fontWeight: token.fontStyle && token.fontStyle & 2 ? "bold" : undefined, textDecoration: token.fontStyle && token.fontStyle & 4 ? "underline" : undefined } as CSSProperties}>{token.content}</span>)}</span>)}</code></pre>;
}

export function CodeBlock({ code, language, className, ...props }: Omit<ComponentProps<"div">, "children"> & { code: string; language?: string }) {
  const { t } = useTranslation("taskDetail");
  const [highlighted, setHighlighted] = useState<Highlighted | null>(null);
  const [visible, setVisible] = useState(() => typeof IntersectionObserver === "undefined");
  const container = useRef<HTMLDivElement>(null);
  const grammar = canonicalLanguage(language);

  useEffect(() => {
    if (!container.current || typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(entries => {
      if (entries.some(entry => entry.isIntersecting)) { setVisible(true); observer.disconnect(); }
    });
    observer.observe(container.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    if (!grammar || !visible) return;
    let active = true;
    highlight(code, grammar).then(tokens => {
      if (active) setHighlighted({ code, language: grammar, tokens });
    }).catch(() => {
      if (active) setHighlighted({ code, language: grammar, failed: true });
    });
    return () => { active = false; };
  }, [code, grammar, visible]);

  const current = highlighted?.code === code && highlighted.language === grammar ? highlighted : null;
  return <div ref={container} data-slot="session-code" data-language={language} className={cn("min-w-0 overflow-x-auto bg-sunk p-3 text-foreground", className)} {...props}>
    {current?.failed && <p role="alert" className="mb-2 text-xs text-fail">{t("sessionView.highlightFailed")}</p>}
    {current?.tokens ? <><TokenLines lines={current.tokens.light} className="dark:hidden" /><TokenLines lines={current.tokens.dark} className="hidden dark:block" /></> : <pre className="m-0 font-mono text-xs leading-relaxed"><code>{code}</code></pre>}
  </div>;
}
