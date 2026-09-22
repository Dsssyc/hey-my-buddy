import type { Shutdown, Task } from "./types";

export type WorkflowRequest = {
  requestId: string;
  kind: string;
  state: string;
  summary: string;
  attempted: string;
  neededWork: string[];
  expectedArtifacts: string[];
  acceptance: string;
  childTaskId?: string | null;
};
export type Workflow = {
  governed: true;
  runId: string;
  hostId: string;
  ownerGeneration: number;
  revision: number;
  state: string;
  awaitingHost: boolean;
  waitReason: string;
  continuationCount: number;
  shutdown?: Shutdown;
  workspace: null | {
    path: string;
    kind: string;
    access: string;
    inputCommit: string;
    manifestSha256: string;
  };
  currentTurn: null | {
    turnId: string;
    turnIndex: number;
    attemptId: string;
    resumeMode: string;
    sessionId?: string;
    previousSessionId?: string;
    summary?: string;
    summaryTruncated?: boolean;
    remaining?: string[];
  };
  activeRequest: WorkflowRequest | null;
  children: {
    taskId: string;
    state: string;
    role: string;
    requestId: string;
  }[];
  artifacts: {
    artifactId: string;
    attemptId: string;
    sourceTaskId: string;
    kind: string;
    manifestSha256: string;
    snapshotSha256?: string;
    commit?: string;
    outputCommit?: string;
    diffPath?: string;
  }[];
  finalArtifactId: string | null;
  finalAttemptId: string | null;
  task: Task;
};

export type HelperDraft = {
  id: string;
  profileId: string;
  task: string;
  cwd: string;
  kind: "worktree" | "existing";
  access: "write" | "read";
  writeScope: string;
  includeUntracked: string;
};
