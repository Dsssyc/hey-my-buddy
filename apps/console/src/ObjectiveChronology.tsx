import type { CSSProperties } from "react";
import type { TimelineSpan } from "./objective-types";
import type { ChronoEntry, ConfigurationStyle, SpanOutcome, TimelineItem } from "./objective-display";
import { clockTime, configurationLabel, displayTitle, durationText, eventVocab, outcomeLabel, paletteIndex } from "./objective-display";

const RESULT_GLYPH: Partial<Record<SpanOutcome, string>> = { failed: "✕", cancelled: "⊘", unknown: "?", running: "▸" };

/**
 * The chronological column: narrow screens and the desktop 以列表查看 option.
 * Spans and Host events merge into one start-time-ordered list; idle stretches
 * appear as separators only when the frozen layout trusts the scope, and the
 * list never compresses time, so there is nothing to expand. A single click
 * pins the inspector selection; Enter or a double click opens the detail.
 */
export function ObjectiveChronology({ entries, palette, onSelectItem, onOpenItem, selectedKey, openedKey }: {
  entries: ChronoEntry[];
  palette: ConfigurationStyle[];
  onSelectItem: (item: TimelineItem) => void;
  onOpenItem: (item: TimelineItem) => void;
  selectedKey: string | null;
  openedKey: string | null;
}) {
  if (!entries.length) return <p className="tl-state">没有可按时间列出的记录；缺少可用的时间信息。</p>;
  return <div className="tl-list" aria-label="按时间排序的记录">
    {entries.map((entry, index) => {
      if (entry.kind === "gap") {
        const idle = durationText(entry.endMs - entry.startMs);
        return <div key={`gap-${entry.startMs}-${index}`} className="tl-gap">
          空闲 {idle}（{clockTime(entry.startMs)}–{clockTime(entry.endMs)}，没有任何片段或事件）
        </div>;
      }
      if (entry.kind === "event") {
        const vocab = eventVocab(entry.event.kind);
        const helper = entry.row?.kind === "helper" ? "↳ 协助 · " : "";
        // Task first-line fallbacks stay one ~40-character line here too, and
        // the full text never leaks into the tooltip (0.15.1 U3).
        const line = entry.row ? displayTitle(entry.row.titleSource, entry.row.title) : null;
        const title = line ? line.text : `委派 ${entry.event.runId}`;
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
          aria-label={entry.item.head + "，" + entry.item.parts.join("，")}>
          <time>{clockTime(entry.atMs)}</time>
          <span className="glyph" aria-hidden="true">{vocab.glyph}</span>
          <span className="e-title single-line" title={line?.fromTask ? `${title}（完整任务见详情）` : title}>Host {entry.event.label || vocab.label} · {helper}{title}</span>
          <span className="e-meta"><span>{entry.item.parts.slice(1).join(" · ")}</span></span>
        </button>;
      }
      const span: TimelineSpan = entry.span;
      const style = paletteIndex(palette, span.configuration);
      const outcome = entry.outcome;
      const resultGlyph = RESULT_GLYPH[outcome];
      const helper = entry.row.kind === "helper" ? "↳ 协助 · " : "";
      const swatch = span.kind === "execution" && style
        ? <span className="swatch" style={{ "--c": `var(--cfg-${style.color})` } as CSSProperties} aria-hidden="true" />
        : <span className={"swatch " + (span.kind === "queue" ? "queue" : span.kind === "routing" ? "routing" : "wait")} aria-hidden="true" />;
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
        aria-label={entry.item.head + "，" + entry.item.parts.join("，")}>
        <time>{clockTime(entry.atMs)}</time>
        {swatch}
        <span className="e-title single-line" title={titleLine.fromTask ? `${titleLine.text}（完整任务见详情）` : titleLine.text}>{entry.item.head} · {helper}{titleLine.text}</span>
        <span className="e-meta">
          {span.kind === "execution" && <span>{configurationLabel(span.configuration)}</span>}
          {duration && <span>{duration}{span.endAt == null ? "（至今）" : ""}</span>}
          {resultGlyph && <span>{resultGlyph} {outcomeLabel(span, outcome)}</span>}
        </span>
      </button>
    })}
  </div>;
}
