import { useTranslation } from "react-i18next";
import i18n from "i18next";
import { useRef } from "react";
import type { MacroTaskTimeline } from "./macro-task-types";
import type { TimelineItem } from "./macro-task-display";
import { buildInspectorCard, type CardField, type CardLink, type InspectorCard, type InspectorSelection } from "./inspector-card";
import type { FriendlyBuddy } from "./macro-task-display";
import { CopyableIdentifier } from "./components/CopyableIdentifier";

const MISSING_TITLE = () => i18n.t("console:thisRecordIsOutsideTheCurrentScope");

export type TimelineInspectorProps = {
  selection: InspectorSelection | null;
  /** Last selected facts retained by the parent while this drawer was folded. */
  cachedCard?: InspectorCard | null;
  timeline: MacroTaskTimeline | null;
  itemsByKey: ReadonlyMap<string, TimelineItem>;
  truncatedEvents: boolean;
  /** Snapshot buddies for friendly buddy names (0.16 0.3). */
  buddies?: readonly FriendlyBuddy[] | null;
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
  useTranslation("console");
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
  useTranslation("console");
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
  useTranslation("console");
  return <div className="inspector-card" aria-live="polite">
    <div className="inspector-card-head">
      <strong>{card.head}</strong>
      {card.openItem && <CopyableIdentifier value={card.openItem.runId} />}
      <span className="inspector-actions">
        <button type="button" className="button small-button inspector-action"
          aria-disabled={openDisabled || undefined} title={openTitle}
          onClick={() => { if (!openDisabled && card.openItem) onOpen(card.openItem); }}>{i18n.t("console:openDetails")}</button>
        <button type="button" className="inspector-unpin inspector-action" aria-label={i18n.t("console:unpin")} title={i18n.t("console:unpin")}
          onClick={onUnpin}>×</button>
      </span>
    </div>
    {missing && <p className="inspector-missing">{i18n.t("console:recordOutsideTheCurrentScope")}</p>}
    <dl className="inspector-fields">
      {card.fields.map(field => <FieldView key={field.label} field={field} onSelectItem={onSelectItem} onSelectRun={onSelectRun} />)}
    </dl>
    {card.relatedEvents.length > 0 && <div className="inspector-group">
      <span className="inspector-group-label">{i18n.t("console:relatedHostEvents")}</span>
      <span className="inspector-group-lines inspector-events">
        {card.relatedEvents.map(({ item }) => <span key={item.key} className="inspector-event">
          <button type="button" className="inspector-link" onClick={() => onSelectItem(item)}>{item.head} · {item.parts[1] ?? ""}</button>
          <button type="button" className="button small-button" aria-label={i18n.t("console:openItem", { v0: item.head })}
            aria-disabled={missing || undefined} title={missing ? MISSING_TITLE() : undefined}
            onClick={() => { if (!missing) onOpen(item); }}>{i18n.t("console:open")}</button>
        </span>)}
        {truncatedEvents && <span className="trunc-chip">{i18n.t("console:hostEventsTruncated")}</span>}
      </span>
    </div>}
  </div>;
}

/**
 * The pinned inspector inside the timeline's expandable bottom drawer (0.15
 * C1/C2, 0.16 P1.6/P2.1): one structured six-field card that changes only
 * with the selection. Refreshes and hover
 * never replace the card; when the selected record leaves the read, the last
 * facts stay with a warning and 打开详情 is disabled until it returns.
 */
export function TimelineInspector(props: TimelineInspectorProps) {
  useTranslation("console");
  const { selection, timeline, itemsByKey } = props;
  const key = selectionKey(selection);
  const resolved = timeline && selection ? buildInspectorCard(selection, timeline, itemsByKey, props.buddies) : null;
  // Keep the last resolvable card per selection so a truncated refresh cannot
  // silently clear pinned facts.
  const cache = useRef<{ key: string; card: InspectorCard }>({ key: "", card: null as unknown as InspectorCard });
  if (resolved) cache.current = { key, card: resolved };
  const cached = cache.current.key === key ? cache.current.card : props.cachedCard ?? null;
  const card = resolved ?? cached?.refreshCopy?.() ?? cached;
  const missing = !!selection && !resolved && card !== null;

  // A record that left the read can no longer be opened; record browsing
  // itself is never locked (0.15.1 U4), so every navigation action from a
  // live card stays available.
  const openDisabled = missing;
  const openTitle = missing ? MISSING_TITLE() : undefined;

  return <div className="inspector timeline-inspector">
    {card
      ? <CardView card={card} truncatedEvents={props.truncatedEvents} openDisabled={openDisabled} openTitle={openTitle}
        onOpen={props.onOpen} onSelectItem={props.onSelectItem} onSelectRun={props.onSelectRun} onUnpin={props.onUnpin}
        missing={missing} key={key} />
      : selection && !resolved
        ? <div className="inspector-card" aria-live="polite"><div className="inspector-card-head"><strong>{i18n.t("console:selectedRecord")}</strong></div>
          <p className="inspector-missing">{i18n.t("console:recordOutsideTheCurrentScope")}</p></div>
        : null}
  </div>;
}
