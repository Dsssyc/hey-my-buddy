import { useRef } from "react";
import type { ReactNode } from "react";
import type { ObjectiveTimeline } from "./objective-types";
import type { TimelineItem } from "./objective-display";
import { buildInspectorCard, type CardLink, type InspectorCard, type InspectorSelection } from "./inspector-card";

const LOCK_TITLE = "有结果未确认的操作：核对前不能打开其他委派的详情";
const MISSING_TITLE = "该记录不在当前读取范围内（可能已截断或被筛选），不能从这张卡片打开";

export type TimelineInspectorProps = {
  selection: InspectorSelection | null;
  /** Hover/focus item when it differs from the selection; never replaces the card. */
  previewItem: TimelineItem | null;
  previewClusterHead: string | null;
  timeline: ObjectiveTimeline | null;
  itemsByKey: ReadonlyMap<string, TimelineItem>;
  truncatedEvents: boolean;
  /** Locked with a detail open: opening another delegation's detail is disabled. */
  locked: boolean;
  lockedRunId: string | null;
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

function CardView({ card, truncatedEvents, openDisabled, openTitle, relatedDisabled, onOpen, onSelectItem, onSelectRun, onUnpin, missing }: {
  card: InspectorCard;
  truncatedEvents: boolean;
  openDisabled: boolean;
  openTitle: string | undefined;
  /** Per-run disable state for related-event navigation from this card. */
  relatedDisabled: (runId: string) => { disabled: boolean; title: string | undefined };
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
    {card.groups.map(group => <div key={group.label} className="inspector-group">
      <span className="inspector-group-label">{group.label}</span>
      <span className="inspector-group-lines">
        {group.lines.map((line, index) => <span key={index} className={line.tone ? `tone-${line.tone}` : undefined}>{line.text}</span>)}
        {group.link && <LinkButton link={group.link} onSelectItem={onSelectItem} onSelectRun={onSelectRun} />}
      </span>
    </div>)}
    {card.relatedEvents.length > 0 && <div className="inspector-group">
      <span className="inspector-group-label">相关 Host 事件</span>
      <span className="inspector-group-lines inspector-events">
        {card.relatedEvents.map(({ item }) => {
          const state = relatedDisabled(item.runId);
          return <span key={item.key} className="inspector-event">
          <button type="button" className="inspector-link" onClick={() => onSelectItem(item)}>{item.head} · {item.parts[1] ?? ""}</button>
          <button type="button" className="button small-button" aria-label={`打开 ${item.head}`}
            aria-disabled={state.disabled || undefined} title={state.title}
            onClick={() => { if (!state.disabled) onOpen(item); }}>打开</button>
        </span>;
        })}
        {truncatedEvents && <span className="trunc-chip">Host 事件已截断，此列表可能不完整</span>}
      </span>
    </div>}
  </div>;
}

/**
 * The fixed inspector (0.15 C1/C2): a preview row for hover/focus plus a card
 * that changes only with the selection. Refreshes, arrows and hover never
 * replace the card; when the selected record leaves the read, the last facts
 * stay with a warning and 打开详情 is disabled until it returns.
 */
export function TimelineInspector(props: TimelineInspectorProps) {
  const { selection, timeline, itemsByKey } = props;
  const key = selectionKey(selection);
  const resolved = timeline && selection ? buildInspectorCard(selection, timeline, itemsByKey) : null;
  // Keep the last resolvable card per selection so a truncated refresh cannot
  // silently clear pinned facts.
  const cache = useRef<{ key: string; card: InspectorCard }>({ key: "", card: null as unknown as InspectorCard });
  if (resolved) cache.current = { key, card: resolved };
  const card = cache.current.key === key ? cache.current.card : null;
  const missing = !!selection && !resolved && card !== null;

  const previewTarget = props.previewItem;
  const preview = props.previewClusterHead
    ?? (previewTarget ? `${previewTarget.head}，${previewTarget.parts.join("，")}` : null);
  const previewIsSelection = !props.previewClusterHead && (
    (selection?.type === "item" && previewTarget?.key === selection.key));

  let previewRow: ReactNode = null;
  if (!selection) {
    previewRow = preview
      ? <span className="inspector-preview"><strong>{previewTarget?.head ?? props.previewClusterHead}</strong><span>{previewTarget ? previewTarget.parts.join(" · ") : ""}</span></span>
      : <span className="muted">单击选中查看记录；Enter 或双击打开详情。</span>;
  } else if (preview && !previewIsSelection) {
    previewRow = <span className="inspector-preview muted">指向：{preview}</span>;
  }

  // A record that left the read can no longer be opened; a locked detail only
  // blocks other delegations' details. Every navigation action from a stale
  // card is disabled, each with its own accurate reason.
  const openDisabled = missing || (props.locked && !!card?.openItem && card.openItem.runId !== props.lockedRunId);
  const openTitle = missing ? MISSING_TITLE : openDisabled ? LOCK_TITLE : undefined;
  const relatedDisabled = (runId: string): { disabled: boolean; title: string | undefined } => {
    if (missing) return { disabled: true, title: MISSING_TITLE };
    if (props.locked && runId !== props.lockedRunId) return { disabled: true, title: LOCK_TITLE };
    return { disabled: false, title: undefined };
  };

  return <div className="inspector timeline-inspector">
    {previewRow && <div className="inspector-preview-row">{previewRow}
      <span className="hint">单击选中 · Enter 或双击打开详情</span>
    </div>}
    {card
      ? <CardView card={card} truncatedEvents={props.truncatedEvents} openDisabled={openDisabled} openTitle={openTitle}
        relatedDisabled={relatedDisabled}
        onOpen={props.onOpen} onSelectItem={props.onSelectItem} onSelectRun={props.onSelectRun} onUnpin={props.onUnpin}
        missing={missing} key={key} />
      : selection && !resolved
        ? <div className="inspector-card" aria-live="polite"><div className="inspector-card-head"><strong>选中的记录</strong></div>
          <p className="inspector-missing">该记录不在当前读取范围内（可能已截断或被筛选）。</p></div>
        : null}
  </div>;
}
