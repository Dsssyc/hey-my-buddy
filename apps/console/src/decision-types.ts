import type { Decision, Buddy, Preference, RoutingBudget } from "./types";
import type { BuddyIdentity, RoutingBasis } from "./workflow-types";

export type DecisionModel = Partial<BuddyIdentity> & { reasoningEffort?: string };
export type DecisionAudit = Decision & {
  selectedBuddy?: Partial<BuddyIdentity> | null;
  /** False marks the program's direct selection of the sole legal candidate; null predates that path. */
  routerCalled?: boolean | null;
  routingBasis?: RoutingBasis | null;
  constraints?: Partial<BuddyIdentity>;
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
  requested?: { constraints?: Partial<BuddyIdentity>; requiredCapabilities?: string[] };
  input?: {
    buddy?: Partial<BuddyIdentity>;
    buddies: Buddy[];
    preferences: Preference[];
    [key: string]: unknown;
  } | null;
  output?: unknown;
  inputSha256?: string | null;
};

export const decisionStatus: Record<string, string> = {
  queued: "等待中", running: "处理中", completed: "已完成", "needs-host": "需要 Host 处理",
  failed: "执行失败", cancelled: "已取消", fenced: "结果已隔离", explicit: "Host 指定", abstention: "已放弃选择",
};
