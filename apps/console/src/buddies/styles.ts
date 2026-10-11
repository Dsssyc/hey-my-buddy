/**
 * The class strings the catalogue's tables and their side columns share, so
 * that the models table and the harness table are drawn alike.
 */

/** A body cell and a column heading of a catalogue table. */
export const tableCell = "border-b px-3 py-2.5 align-middle";
export const tableHead = "border-b px-3 py-2.5 text-left text-xs font-normal whitespace-nowrap text-ink3";
/** The handle inside a cell that takes focus: a row's selector, a fold. */
export const tableHandle = "inline-flex items-center rounded-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50";
/** A selectable row: tinted while it is the one the side column edits, where the two stand side by side. */
export const tableRow = "cursor-pointer hover:bg-sunk xl:data-[selected=true]:bg-live-wash xl:data-[selected=true]:hover:bg-live-wash";

/** The heading line of a block in a side column: its name on the left, a state or a column name on the right. */
export const paneBlockHead = "flex items-center justify-between border-t px-5 pt-4 pb-1.5 text-xs text-ink3";
/** The grid of a block's fields: the name of a field, then its control. */
export const paneFields = "grid grid-cols-[76px_1fr] items-center gap-x-3 gap-y-2 px-5 pt-1 pb-4";
/** A text field of a side column. */
export const paneField = "h-8 rounded-md border border-input bg-card px-2.5 outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 disabled:opacity-50 aria-invalid:border-destructive";
