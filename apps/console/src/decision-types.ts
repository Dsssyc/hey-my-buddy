import type { Decision, Profile, Preference, Evidence, Card, RoutingBudget } from "./types";
import type { ExecutionConfiguration, RoutingBasis } from "./workflow-types";

export type DecisionModel = Partial<ExecutionConfiguration> & { reasoningEffort?: string };
export type DecisionAudit = Decision & {
  selectedProfile?: Partial<ExecutionConfiguration> | null;
  /** False marks the program's direct selection of the sole legal candidate; null predates that path. */
  routerCalled?: boolean | null;
  routingBasis?: RoutingBasis | null;
  constraints?: Partial<ExecutionConfiguration>;
  requiredCapabilities?: string[];
  policyCheck?: {
    userPreference?: string | null;
    [key: string]: unknown;
  } | null;
  budget?: { preset?: RoutingBudget | "quick"; timeoutSeconds?: number | null; toolCalls?: number | null; bytesRead?: number | null } | null;
  usage?: { elapsedMs?: number | null; toolCalls?: number | null; bytesRead?: number | null } | null;
  nativeIdentity?: unknown;
  stopEvidence?: unknown;
  inputVerification?: unknown;
  decisionModel?: { requested: DecisionModel | null; resolved: DecisionModel | null; observed: unknown };
  configurationRevision?: number;
  publishedRevision?: number | null;
  noOp?: boolean;
  requested?: { constraints?: Partial<ExecutionConfiguration>; requiredCapabilities?: string[] };
  input?: {
    profile?: Partial<ExecutionConfiguration>;
    profiles: Profile[];
    cards: Card[];
    preferences: Preference[];
    evidence: Evidence[];
    [key: string]: unknown;
  } | null;
  proposal?: unknown;
  output?: unknown;
  inputSha256?: string | null;
};

export const decisionStatus: Record<string, string> = {
  queued: "等待中", running: "处理中", completed: "已完成", "needs-host": "需要 Host 处理",
  failed: "执行失败", cancelled: "已取消", stale: "结果已失效", fenced: "结果已隔离", explicit: "Host 指定", abstention: "已放弃选择",
};
