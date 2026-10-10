import { useEffect, useState } from "react";

/** One paragraph switches between the excerpt and the full recorded text. */
export function InlineText({ text, recordId, className = "read-text", limit = 360 }: {
  text: string; recordId: string; className?: string; limit?: number;
}) {
  const [opened, setOpened] = useState<{ recordId: string; text: string } | null>(null);
  useEffect(() => { setOpened(null); }, [recordId, text]);
  const expanded = opened?.recordId === recordId && opened.text === text;
  const long = text.length > limit;
  return <p className={className}>{long && !expanded ? text.slice(0, limit) + "…" : text}
    {long && <> <button type="button" className="button small-button" aria-expanded={expanded}
      onClick={() => setOpened(expanded ? null : { recordId, text })}>{expanded ? "收起" : "展开"}</button></>}
  </p>;
}
