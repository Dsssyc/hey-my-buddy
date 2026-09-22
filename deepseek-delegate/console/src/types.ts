export type Profile = {
  profileId: string;
  label: string;
  adapter: string;
  provider: string;
  model: string;
  effort: string;
  available: boolean;
  enabled: boolean;
  capabilities: string[];
  contextWindow: number | null;
  description: string;
  source: string;
  unavailableReason?: string;
};
export type Preference = {
  profileId: string;
  mode: "prefer" | "pin" | "exclude";
  reason: string;
};
export type Card = {
  profileId: string;
  revision: number;
  summary: string;
  strengths: string[];
  limitations: string[];
  risks: string[];
  evidenceIds: string[];
  sampleCount: number;
  updatedAt: string | null;
};
export type Evidence = {
  evidenceId: string;
  profileId: string;
  kind: string;
  summary: string;
  project: string | null;
  conditions: string[];
  source: string;
  runId: string | null;
  createdAt: string;
};
export type Decision = {
  decisionId: string;
  kind?: "select" | "maintain";
  runId?: string | null;
  requestId?: string;
  status: string;
  task: string;
  profileId: string | null;
  tableRevision: number;
  reason: string;
  evidenceIds: string[];
  createdAt: string;
  error?: string | null;
};
export type Task = {
  runId: string;
  task: string;
  status: string;
  owner: string;
  cwd: string;
  revision: number;
  createdAt: string;
  acceptedAt: string | null;
  acceptanceVerdict: string | null;
  queueReason?: string | null;
  spec?: Record<string, unknown>;
  workflow?: {
    state: string;
    awaitingHost: boolean;
    hostId: string;
    ownerGeneration: number;
    revision: number;
    requestSummary?: string;
  };
  [key: string]: unknown;
};
export type Gate = {
  phase: "open" | "draining" | "writing";
  readers: number;
  waitingWriters: number;
  writer: null | {
    writerId: string;
    kind: string;
    generation: number;
    expiresAt: string;
  };
};
export type Configuration = {
  revision: number;
  decisionProfileId: string | null;
  autoMaintain: boolean;
};
export type Snapshot = {
  csrfToken: string;
  tableRevision: number;
  gate: Gate;
  configuration: Configuration;
  profiles: Profile[];
  preferences: Preference[];
  cards: Card[];
  evidence: Evidence[];
  decisions: Decision[];
  pendingEvidence: number;
  tasks: { runs: Task[]; total: number };
  capabilities: Record<string, boolean>;
};
export type WriterGrant = {
  writerId: string;
  generation: number;
  writerToken: string;
  phase: string;
  expiresAt: string;
  tableRevision: number;
};
export type Draft = Pick<
  Snapshot,
  "profiles" | "preferences" | "cards" | "configuration"
> & { tableRevision: number };
