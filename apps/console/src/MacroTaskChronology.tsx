import { useTranslation } from "react-i18next";
import i18n from "i18next";
import type { CSSProperties } from "react";
import type { TimelineSpan } from "./macro-task-types";
import type { ChronoEntry, BuddyStyle, SpanOutcome, TimelineItem } from "./macro-task-display";
import { clockTime, buddyLabel, displayTitle, durationText, eventVocab, outcomeLabel, paletteIndex, titleLineTooltip } from "./macro-task-display";

const RESULT_GLYPH: Partial<Record<SpanOutcome, string>> = { failed: "✕", cancelled: "⊘", unknown: "?", running: "▸" };

/**
 * The chronological column: narrow screens and the desktop 以列表查看 option.
 * Spans and Host events merge into one start-time-ordered list; idle stretches
 * appear as separators only when the frozen layout trusts the scope, and the
 * list never compresses time, so there is nothing to expand. A single click
 * pins the inspector selection; Enter or a double click opens the detail.
 */
export function MacroTaskChronology({ entries, palette, onSelectItem, onOpenItem, selectedKey, openedKey }: {
  entries: ChronoEntry[];
  palette: BuddyStyle[];
  onSelectItem: (item: TimelineItem) => void;
  onOpenItem: (item: TimelineItem) => void;
  selectedKey: string | null;
  openedKey: string | null;
}) {
  useTranslation("console");
  if (!entries.length) return <p className="tl-state">{i18n.t("console:theseRecordsHaveNoUsableTimeAnd")}</p>;
  return <div className="tl-list" aria-label={i18n.t("console:recordsInTimeOrder")}>
    {entries.map((entry, index) => {
      if (entry.kind === "gap") {
        const idle = durationText(entry.endMs - entry.startMs);
        return <div key={`gap-${entry.startMs}-${index}`} className="tl-gap">
          {i18n.t("console:idle")}{idle}{i18n.t("console:openParen")}{clockTime(entry.startMs)}–{clockTime(entry.endMs)}{i18n.t("console:closeParen")}</div>;
      }
      if (entry.kind === "event") {
        const vocab = eventVocab(entry.event.kind);
        const helper = entry.row?.kind === "helper" ? i18n.t("console:helperArrowPrefix") : "";
        // Task first-line fallbacks stay one ~40-character line here too, and
        // the full text never leaks into the tooltip (0.15.1 U3).
        const line = entry.row ? displayTitle(entry.row.titleSource, entry.row.title) : null;
        const title = line ? line.text : i18n.t("console:microTaskWithId", { v0: entry.event.runId });
        return <button key={entry.item.key} data-key={entry.item.key}
          className={"tl-entry" + (selectedKey === entry.item.key ? " selected" : "") + (openedKey === entry.item.key ? " opened" : "")}
          onClick={() => onSelectItem(entry.item)}
          onDoubleClick={() => onOpenItem(entry.item)}
          onKeyDown={event => {
            if (event.key === "Enter") {
              event.preventDefault();
              onOpenItem(entry.item);
            }
          }}
          aria-label={entry.item.head + i18n.t("console:comma") + entry.item.parts.join(i18n.t("console:comma"))}>
          <time>{clockTime(entry.atMs)}</time>
          <span className="glyph" aria-hidden="true">{vocab.glyph}</span>
          <span className="e-title single-line" title={line ? titleLineTooltip(line) : title}>Host {entry.event.label || vocab.label} · {helper}{title}</span>
          <span className="e-meta"><span>{entry.item.parts.slice(1).join(" · ")}</span></span>
        </button>;
      }
      const span: TimelineSpan = entry.span;
      const style = paletteIndex(palette, span.buddy);
      const outcome = entry.outcome;
      const resultGlyph = RESULT_GLYPH[outcome];
      const helper = entry.row.kind === "helper" ? i18n.t("console:helperArrowPrefix") : "";
      const swatch = span.kind === "execution" && style
        ? <span className="swatch" style={{ "--c": `var(--cfg-${style.color})` } as CSSProperties} aria-hidden="true" />
        : <span className={"swatch " + (span.kind === "queue" ? "queue" : span.kind === "routing"
          ? `routing ${span.routing?.routingMode ?? ""} ${outcome}` : "wait")} aria-hidden="true">
          {span.kind === "routing" && outcome === "failed" && <i className="routing-cross" />}
        </span>;
      const duration = durationText(entry.endMs !== null ? entry.endMs - entry.atMs : null);
      const titleLine = displayTitle(entry.row.titleSource, entry.row.title);
      return <button key={entry.item.key} data-key={entry.item.key}
        className={"tl-entry" + (selectedKey === entry.item.key ? " selected" : "") + (openedKey === entry.item.key ? " opened" : "")}
        onClick={() => onSelectItem(entry.item)}
        onDoubleClick={() => onOpenItem(entry.item)}
        onKeyDown={event => {
          if (event.key === "Enter") {
            event.preventDefault();
            onOpenItem(entry.item);
          }
        }}
        aria-label={entry.item.ariaLabel ?? entry.item.head + i18n.t("console:comma") + entry.item.parts.join(i18n.t("console:comma"))} title={entry.item.ariaLabel}>
        <time>{clockTime(entry.atMs)}</time>
        {swatch}
        <span className="e-title single-line" title={titleLineTooltip(titleLine)}>{entry.item.head} · {helper}{titleLine.text}</span>
        <span className="e-meta">
          {span.kind === "execution" && <span>{buddyLabel(span.buddy)}</span>}
          {duration && <span>{duration}{span.endAt == null ? i18n.t("console:untilNow") : ""}</span>}
          {span.kind === "routing" ? <span>{outcomeLabel(span, outcome)}</span>
            : resultGlyph && <span>{resultGlyph} {outcomeLabel(span, outcome)}</span>}
        </span>
      </button>
    })}
  </div>;
}
