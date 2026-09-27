import { useRef } from "react";
import type { ReactNode } from "react";
import type { ObjectiveTimeline } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { buildInspectorCard, type CardField, type CardLink, type InspectorCard, type InspectorSelection } from "./inspector-card";
import type { FriendlyProfile } from "./objective-display";

const MISSING_TITLE = "该记录不在当前读取范围内（可能已截断或被筛选），不能从这张卡片打开";

export type TimelineInspectorProps = {
  selection: InspectorSelection | null;
  /** Hover/focus item when it differs from the selection; never replaces the card. */
  previewItem: TimelineItem | null;
  previewClusterHead: string | null;
  timeline: ObjectiveTimeline | null;
  itemsByKey: ReadonlyMap<string, TimelineItem>;
  truncatedEvents: boolean;
  /** Snapshot profiles for friendly configuration names (0.16 0.3). */
  profiles?: readonly FriendlyProfile[] | null;
  onOpen: (item: TimelineItem) => void;
  onSelectItem: (item: TimelineItem) => void;
  onSelectRun: (runId: string) => void;
  onUnpin: () => void;
};

function selectionKey(selection: InspectorSelection | null): string {
  if (!selection) return "";
  return selection.type === "run" ? `run:${selection.runId}` : selection.key;
}

function LinkButton({ link, onSelectItem, onSelectRun }: {
  link: CardLink; onSelectItem: (item: TimelineItem) => void; onSelectRun: (runId: string) => void;
}) {
  if (link.kind === "item") {
    return <button type="button" className="inspector-link" onClick={() => onSelectItem(link.item)}>{link.item.head}</button>;
  }
  return <button type="button" className="inspector-link" onClick={() => onSelectRun(link.runId)}>{link.label}</button>;
}

function FieldView({ field, onSelectItem, onSelectRun }: {
  field: CardField;
  onSelectItem: (item: TimelineItem) => void;
  onSelectRun: (runId: string) => void;
}) {
  return <div className={"inspector-field" + (field.tone ? ` tone-${field.tone}` : "")}>
    <dt className="inspector-field-label">{field.label}</dt>
    <dd className="inspector-field-value" title={field.title ?? field.value}>
      {field.link ? <LinkButton link={field.link} onSelectItem={onSelectItem} onSelectRun={onSelectRun} /> : field.value}
    </dd>
  </div>;
}

function CardView({ card, truncatedEvents, openDisabled, openTitle, onOpen, onSelectItem, onSelectRun, onUnpin, missing }: {
  card: InspectorCard;
  truncatedEvents: boolean;
  openDisabled: boolean;
  openTitle: string | undefined;
  onOpen: (item: TimelineItem) => void;
  onSelectItem: (item: TimelineItem) => void;
  onSelectRun: (runId: string) => void;
  onUnpin: () => void;
  missing: boolean;
}) {
  return <div className="inspector-card" aria-live="polite">
    <div className="inspector-card-head">
      <strong>{card.head}</strong>
      <span className="inspector-actions">
        <button type="button" className="button small-button"
          aria-disabled={openDisabled || undefined} title={openTitle}
          onClick={() => { if (!openDisabled && card.openItem) onOpen(card.openItem); }}>打开详情</button>
        <button type="button" className="inspector-unpin" aria-label="取消固定" title="取消固定，回到预览"
          onClick={onUnpin}>×</button>
      </span>
    </div>
    {missing && <p className="inspector-missing">该记录不在当前读取范围内（可能已截断或被筛选）。</p>}
    <dl className="inspector-fields">
      {card.fields.map(field => <FieldView key={field.label} field={field} onSelectItem={onSelectItem} onSelectRun={onSelectRun} />)}
    </dl>
    {card.relatedEvents.length > 0 && <div className="inspector-group">
      <span className="inspector-group-label">相关 Host 事件</span>
      <span className="inspector-group-lines inspector-events">
        {card.relatedEvents.map(({ item }) => <span key={item.key} className="inspector-event">
          <button type="button" className="inspector-link" onClick={() => onSelectItem(item)}>{item.head} · {item.parts[1] ?? ""}</button>
          <button type="button" className="button small-button" aria-label={`打开 ${item.head}`}
            aria-disabled={missing || undefined} title={missing ? MISSING_TITLE : undefined}
            onClick={() => { if (!missing) onOpen(item); }}>打开</button>
        </span>)}
        {truncatedEvents && <span className="trunc-chip">Host 事件已截断，此列表可能不完整</span>}
      </span>
    </div>}
  </div>;
}

/**
 * The pinned inspector as the timeline's always-visible bottom drawer (0.15
 * C1/C2, 0.16 P1.6/P2.1): one structured six-field card that changes only
 * with the selection, plus a single preview line. Refreshes, arrows and hover
 * never replace the card; when the selected record leaves the read, the last
 * facts stay with a warning and 打开详情 is disabled until it returns.
 */
export function TimelineInspector(props: TimelineInspectorProps) {
  const { selection, timeline, itemsByKey } = props;
  const key = selectionKey(selection);
  const resolved = timeline && selection ? buildInspectorCard(selection, timeline, itemsByKey, props.profiles) : null;
  // Keep the last resolvable card per selection so a truncated refresh cannot
  // silently clear pinned facts.
  const cache = useRef<{ key: string; card: InspectorCard }>({ key: "", card: null as unknown as InspectorCard });
  if (resolved) cache.current = { key, card: resolved };
  const card = cache.current.key === key ? cache.current.card : null;
  const missing = !!selection && !resolved && card !== null;

  const previewTarget = props.previewItem;
  const previewCard = timeline && previewTarget
    ? buildInspectorCard({ type: "item", key: previewTarget.key }, timeline, itemsByKey, props.profiles)
    : null;
  // Derive facts by field identity; free-text titles can themselves contain
  // “时间” or “至” and must never be mistaken for another timing field.
  const previewLine = props.previewClusterHead
    ?? (previewCard ? [previewCard.head, ...["委派", "时间"].map(label =>
      previewCard.fields.find(field => field.label === label)?.value)].filter(Boolean).join(" · ")
      : previewTarget?.head ?? null);
  const previewIsSelection = !props.previewClusterHead && (
    (selection?.type === "item" && previewTarget?.key === selection.key));

  let previewRow: ReactNode = null;
  if (!selection) {
    // One preview line plus the single selection hint (P1.6).
    previewRow = <>
      {previewLine && <span className="inspector-preview" title={previewLine}><strong>{previewLine}</strong></span>}
      <span className="hint">单击选中 · Enter 或双击打开详情</span>
    </>;
  } else if (previewLine && !previewIsSelection) {
    previewRow = <span className="inspector-preview muted" title={previewLine}>预览：{previewLine}</span>;
  }

  // A record that left the read can no longer be opened; record browsing
  // itself is never locked (0.15.1 U4), so every navigation action from a
  // live card stays available.
  const openDisabled = missing;
  const openTitle = missing ? MISSING_TITLE : undefined;

  return <div className="inspector timeline-inspector">
    {previewRow && <div className="inspector-preview-row">{previewRow}</div>}
    {card
      ? <CardView card={card} truncatedEvents={props.truncatedEvents} openDisabled={openDisabled} openTitle={openTitle}
        onOpen={props.onOpen} onSelectItem={props.onSelectItem} onSelectRun={props.onSelectRun} onUnpin={props.onUnpin}
        missing={missing} key={key} />
      : selection && !resolved
        ? <div className="inspector-card" aria-live="polite"><div className="inspector-card-head"><strong>选中的记录</strong></div>
          <p className="inspector-missing">该记录不在当前读取范围内（可能已截断或被筛选）。</p></div>
        : null}
  </div>;
}
