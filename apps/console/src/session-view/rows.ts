import type { SessionBlock } from "./blocks";
import type { ToolUpdate } from "./types";

export type ToolBlock = Extract<SessionBlock, { type: "tool" }>;
type MessageBlock = Extract<SessionBlock, { type: "message" }>;
export type ToolKind = NonNullable<ToolUpdate["kind"]>;
export type CallGroup = { type: "calls"; key: string; items: (ToolBlock | MessageBlock)[]; counts: Partial<Record<ToolKind, number>>; failed: number };
export type SessionRow = CallGroup | { type: "tool"; key: string; tool: ToolBlock; markers: MessageBlock[] }
  | { type: "blackboard"; key: string; action: "checkpoint" | "answer_inquiry" | "finish_turn"; tool: ToolBlock }
  | { type: "task" | "note" | "answer" | "reasoning" | "marker"; key: string; message: MessageBlock }
  | Extract<SessionBlock, { type: "plan" }>;

const blackboardActions = ["checkpoint", "answer_inquiry", "finish_turn"] as const;
export function blackboardAction(title = "") {
  return blackboardActions.find(action => title.endsWith(`hey_my_buddy_${action}`));
}
export function typeOnlyMarker(block: MessageBlock): boolean {
  const text = block.update.content.text.trim();
  return !!block.update._meta?.marker && (!text || text === block.update._meta.nativeType || /^\[[^\]\n]+\]$/.test(text));
}
/** View-only grouping: activity markers do not interrupt adjacent calls; real prose does. */
export function sessionRows(blocks: SessionBlock[]): SessionRow[] {
  const rows: SessionRow[] = [];
  const lastAnswer = [...blocks].reverse().find(block => block.type === "message" && block.update.sessionUpdate === "agent_message_chunk" && !block.update._meta?.marker);
  let group: CallGroup | null = null;
  const flush = () => {
    if (!group) return;
    const tools = group.items.filter(item => item.type === "tool");
    rows.push(tools.length === 1 ? { type: "tool", key: tools[0].key, tool: tools[0], markers: group.items.filter(item => item.type === "message") } : group);
    group = null;
  };
  for (const block of blocks) {
    if ((block.type === "tool" && !blackboardAction(block.update.title)) || (block.type === "message" && typeOnlyMarker(block))) {
      group ??= { type: "calls", key: `calls:${block.key}`, items: [], counts: {}, failed: 0 };
      group.items.push(block);
      if (block.type === "tool") {
        const kind = block.update.kind ?? "other";
        group.counts[kind] = (group.counts[kind] ?? 0) + 1;
        if (block.update.status === "failed") group.failed++;
      }
      continue;
    }
    flush();
    if (block.type === "tool") rows.push({ type: "blackboard", key: block.key, action: blackboardAction(block.update.title)!, tool: block });
    else if (block.type === "plan") rows.push(block);
    else rows.push({ type: block.update._meta?.marker ? "marker" : block.update.sessionUpdate === "user_message_chunk" ? "task"
      : block.update.sessionUpdate === "agent_thought_chunk" ? "reasoning" : block === lastAnswer ? "answer" : "note", key: block.key, message: block });
  }
  flush();
  return rows;
}

/** Inputs are already JSON at the session HTTP boundary. No schema validation belongs here. */
export function toolInput(tool: ToolUpdate): Record<string, unknown> {
  return tool.rawInput !== null && typeof tool.rawInput === "object" && !Array.isArray(tool.rawInput) ? tool.rawInput : tool.rawInput === undefined ? {} : { input: tool.rawInput };
}
export function displayPath(path: string, checkoutRoot: string): string {
  const root = checkoutRoot.replace(/[\\/]+$/, "");
  if (root && (path === root || path.startsWith(root + "/") || path.startsWith(root + "\\"))) return path.slice(root.length + 1) || ".";
  return path.replace(/^\/Users\/[^/]+(?=\/|$)|^\/home\/[^/]+(?=\/|$)|^[A-Za-z]:\\Users\\[^\\]+(?=\\|$)/, "~");
}
export function unwrappedCommand(command: string): string {
  const shell = command.match(/^(?:\S*\/)?(?:bash|zsh|sh)\s+-(?:l?c|cl)\s+(['"])([\s\S]*)\1$/);
  return (shell ? shell[2].replace(/'\\''/g, "'") : command).split(/\r?\n/, 1)[0];
}
export function toolObject(tool: ToolUpdate, checkoutRoot: string): string | undefined {
  const input = toolInput(tool);
  const text = (key: string) => typeof input[key] === "string" ? input[key] as string : undefined;
  switch (tool.kind) {
    case "read": case "edit": case "delete": case "move": {
      const path = text("file_path") ?? text("path") ?? text("notebook_path");
      if (path) return displayPath(path, checkoutRoot);
      break;
    }
    case "execute": if (text("command")) return unwrappedCommand(text("command")!); break;
    case "search": if (text("pattern")) return [text("pattern"), text("glob")].filter(Boolean).join(" · "); break;
    case "fetch": if (text("url")) return text("url"); break;
  }
  return tool.locations?.[0] ? displayPath(tool.locations[0].path, checkoutRoot) : tool.title;
}

export function taskExcerpt(task: string): { title: string; opening: string } {
  const [title = "", ...rest] = task.trim().split(/\r?\n/);
  return { title: title.replace(/^#+\s*/, ""), opening: rest.filter(line => line.trim()).slice(0, 2).join("\n") };
}
