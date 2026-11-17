import { Fragment, type ReactNode } from "react";
import type { Namespace } from "i18next";
import { Trans, useTranslation } from "react-i18next";
import { locale } from "./index";

function Value({ value }: { value: ReactNode }) { return <>{value}</>; }

/** Trans owns sentence order; interpolated values stay React nodes, never parsed user markup. */
export function Message({ ns, id, values = {}, literalKey = false }: {
  ns: Namespace; id: string; values?: Record<string, ReactNode>; literalKey?: boolean;
}) {
  const { i18n } = useTranslation();
  const entries = Object.entries(values);
  const components = Object.fromEntries(entries.map(([, value], index) => [`value${index}`, <Value value={value} />]));
  const interpolations = Object.fromEntries(entries.map(([key], index) => [key, `<value${index}/>`]));
  return <Trans i18n={i18n} ns={ns} i18nKey={id as never} values={interpolations} components={components}
    tOptions={{ ...(literalKey ? { keySeparator: false } : {}), interpolation: { escapeValue: false } }} />;
}

/** Separators join facts, while translated sentences use Message. */
export function Joined({ items, separator = " · " }: { items: ReactNode[]; separator?: string }) {
  return <>{items.map((item, index) => <Fragment key={index}>{index > 0 && separator}{item}</Fragment>)}</>;
}

/** The locale's own conjunction and punctuation, with elements in place of strings. */
export function MessageList({ items }: { items: ReactNode[] }) {
  useTranslation();
  if (locale.startsWith("zh")) return <Joined items={items} separator="、" />;
  const parts = new Intl.ListFormat(locale, { type: "conjunction" }).formatToParts(items.map((_, index) => String(index)));
  return <>{parts.map((part, index) => <Fragment key={index}>{part.type === "element" ? items[Number(part.value)] : part.value}</Fragment>)}</>;
}
