import { cn } from "cn";
import type { ComponentProps, ReactNode } from "react";
import { memo } from "react";
import { Streamdown } from "streamdown";
import { MarkdownCode } from "./markdown-code";

export type MessageProps = ComponentProps<"div"> & { from: "user" | "assistant" };

export function Message({ from, className, ...props }: MessageProps) {
  return <div data-slot="session-message" data-from={from} className={cn("group flex min-w-0 flex-col gap-2", from === "user" && "items-end", className)} {...props} />;
}

export function MessageContent({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="session-message-content" className={cn("min-w-0 max-w-full break-words text-sm leading-relaxed", "group-data-[from=user]:bg-sunk group-data-[from=user]:px-3 group-data-[from=user]:py-2", className)} {...props} />;
}

// The transcript is model output. Only web links are active; local paths and
// arbitrary schemes stay text. Images never fetch a remote resource.
function isLocalHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/\.$/, "");
  if (!host.includes(".") || host.endsWith(".localhost") || host.endsWith(".local") || host.endsWith(".internal") || host.startsWith("[")) return true;
  const octets = host.split(".").map(Number);
  if (octets.length !== 4 || octets.some(Number.isNaN)) return false;
  const [first, second] = octets;
  return first === 0 || first === 10 || first === 127 || first >= 224 ||
    (first === 169 && second === 254) || (first === 172 && second >= 16 && second <= 31) ||
    (first === 192 && second === 168) || (first === 100 && second >= 64 && second <= 127);
}

const safeUrl = (url: string) => {
  try {
    const parsed = new URL(url);
    return (parsed.protocol === "https:" || parsed.protocol === "http:") && !isLocalHost(parsed.hostname) ? parsed.href : null;
  } catch {
    return null;
  }
};

function SafeLink(props: ComponentProps<"a"> | Record<string, unknown>) {
  const { href, children } = props;
  const safe = typeof href === "string" ? safeUrl(href) : null;
  if (!safe) return <span>{children as ReactNode}</span>;
  return <a href={safe} target="_blank" rel="noopener noreferrer" className="font-medium underline">{children as ReactNode}</a>;
}

export const MessageResponse = memo(function MessageResponse({ children }: { children: string }) {
  return <Streamdown
    className="min-w-0 break-words [&>*:first-child]:mt-0 [&>*:last-child]:mb-0"
    mode="static"
    controls={false}
    linkSafety={{ enabled: false }}
    skipHtml
    allowElement={(element) => element.tagName !== "img"}
    components={{ code: MarkdownCode, a: SafeLink }}
  >{children}</Streamdown>;
});
