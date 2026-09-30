import type { BackupPreflight } from "./types";

const REASONS: Record<string, string> = {
  "not-evidence-directory": "未登记的证据目录（整树跳过）",
  "not-evidence-file": "未登记的证据文件",
  "linked-evidence": "证据路径是链接或重解析点",
  "linked-path": "备份路径是链接或重解析点",
  "linked-state-root": "状态目录是链接或重解析点",
  "nonregular-evidence": "证据不是普通文件",
  "nonregular-path": "备份路径不是普通文件",
  "wrong-evidence-type": "证据文件与目录类型不符",
  "non-directory-path": "记录目录类型不符",
  "unreadable-path": "无法读取路径",
  "unreadable-directory": "无法读取目录",
};

/** The ordinary snapshot carries this read; the notice has no mutation action. */
export function BackupAttention({ report }: { report?: BackupPreflight }) {
  if (!report?.needsAttention) return null;
  const entries = [...report.skipped.entries.map(row => ({ ...row, action: "跳过" })),
    ...report.rejected.entries.map(row => ({ ...row, action: "拒绝" }))];
  return <div className="banner guard-banner backup-attention" role="status" aria-label="备份预检提醒">
    <p>备份预检需要处理：将跳过 {report.skipped.count} 项，拒绝 {report.rejected.count} 项。请检查路径后再安装。</p>
    <details>
      <summary>查看路径与原因</summary>
      <ul>{entries.map(row => <li key={row.path}>
        <code>{row.path}</code>：{row.action}，{REASONS[row.reason] ?? row.reason}
      </li>)}</ul>
      <p className="small muted">每类最多显示 20 个路径；未登记目录按整树计为一项。</p>
    </details>
  </div>;
}
