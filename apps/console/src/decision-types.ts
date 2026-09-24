import type { Decision, Profile, Preference, Evidence, Card } from "./types";
import type { ExecutionConfiguration } from "./workflow-types";

export type DecisionPage = { decisions: Decision[]; nextCursor: number | null; total: number };
export type DecisionModel = Partial<ExecutionConfiguration> & { reasoningEffort?: string };
export type DecisionAudit = Decision & {
  selectedProfile?: Profile | null;
  decisionModel?: { requested: DecisionModel | null; resolved: DecisionModel | null; observed: unknown };
  configurationRevision?: number;
  publishedRevision?: number | null;
  pendingEvidenceRemaining?: number;
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
  failed: "执行失败", cancelled: "已取消", stale: "结果已失效", fenced: "结果已隔离", explicit: "Host 指定",
};
