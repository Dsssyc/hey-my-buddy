import type { HarnessHealth } from "./types";
import { parseReadOnlyStructured } from "./api";

/** Local eligibility is a free check of the shipped mechanism, not native-run evidence. */
export function HarnessReview({ row }: { row: HarnessHealth }) {
  const eligibility = parseReadOnlyStructured(row.readOnlyStructured);
  return <div className="harness-review">
    <span className="harness-review-status">审阅资格：{eligibility ? eligibility.eligible ? "符合本地检查" : "不可用" : "未知"}</span>
    {eligibility?.reason && <p className="small" role="status">{eligibility.reason}</p>}
    <p className="small muted">本地资格检查不调用模型，不代表原生调用已验证。</p>
    {eligibility && <p className="small muted">{eligibility.systemSandbox
      ? "具有原生系统沙盒；实际策略由每次运行核对。"
      : "无系统沙盒，不能保证阻止副本外读取或外传；黑板的事后判定只覆盖上报的工具事件。"}</p>}
  </div>;
}
