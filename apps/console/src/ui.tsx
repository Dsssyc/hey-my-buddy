import type { ReactNode } from "react";

export function Icon({
  name,
  size = 18,
}: {
  name:
    "tasks" | "models" | "settings" | "refresh" | "arrow" | "check" | "clock";
  size?: number;
}) {
  const paths = {
    tasks: "M4 5h16M4 12h16M4 19h10",
    models: "m12 3 9 5-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5",
    settings: "M4 7h16M4 17h16M8 4v6M16 14v6",
    refresh:
      "M20 7v5h-5M4 17v-5h5M6.1 7a7 7 0 0 1 11.5-2L20 8M4 16l2.4 3A7 7 0 0 0 18 17",
    arrow: "M5 12h14m-6-6 6 6-6 6",
    check: "m5 12 4 4L19 6",
    clock: "M12 8v5l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0",
  };
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d={paths[name]} />
    </svg>
  );
}

export function Badge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "green" | "amber" | "red";
}) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function Empty({
  title,
  children,
  action,
}: {
  title: string;
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="empty-state">
      <div className="empty-mark" aria-hidden="true">
        ＋
      </div>
      <h2>{title}</h2>
      <p>{children}</p>
      {action}
    </div>
  );
}

export function formatDate(value: string | number | null | undefined) {
  if (!value) return "未记录";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? "未记录"
    : new Intl.DateTimeFormat("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      }).format(date);
}

export function display(value: unknown) {
  return typeof value === "string" || typeof value === "number"
    ? String(value)
    : "未记录";
}

export const statusLabels: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  cancelling: "正在取消",
  completed: "执行完成",
  cancelled: "已取消",
  failed: "执行失败",
  "reconciliation-needed": "等待核对",
  "waiting-assistance": "等待协助",
  "waiting-decision": "等待决定",
  "waiting-host": "等待 Host",
  "awaiting-host": "等待 Host",
  "waiting-helpers": "协助执行中",
  executing: "执行中",
  delivered: "等待验收",
  accepted: "已验收",
  yielded: "回合已结束",
};
export function Status({ status }: { status: string }) {
  return (
    <Badge
      tone={
        status === "running" || status === "completed"
          ? "green"
          : status === "failed"
            ? "red"
            : status === "queued" ||
                status.includes("waiting") ||
                status === "reconciliation-needed"
              ? "amber"
              : "neutral"
      }
    >
      {statusLabels[status] || status}
    </Badge>
  );
}
