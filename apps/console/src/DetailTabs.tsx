export function DetailTabs({ id, label, value, items, onChange }: {
  id: string; label: string; value: string; items: readonly (readonly [string, string])[];
  onChange: (value: string) => void;
}) {
  return <div className="detail-tabs" role="tablist" aria-label={label}>
    {items.map(([key, title], index) => <button key={key} type="button" role="tab"
      id={`${id}-${key}-tab`} aria-controls={`${id}-${key}`} aria-selected={value === key}
      tabIndex={value === key ? 0 : -1} onClick={() => onChange(key)} onKeyDown={event => {
        let next = index;
        if (event.key === "ArrowRight") next = (index + 1) % items.length;
        else if (event.key === "ArrowLeft") next = (index + items.length - 1) % items.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = items.length - 1;
        else return;
        event.preventDefault(); onChange(items[next][0]);
        event.currentTarget.parentElement?.querySelectorAll<HTMLButtonElement>("[role=tab]")[next]?.focus();
      }}>{title}</button>)}
  </div>;
}
