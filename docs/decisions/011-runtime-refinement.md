# ADR-011: Shared model concurrency and verified native interaction

## Status

Implementation authorized by the user on 2026-09-25. Experiments and implementation remain unverified until recorded in acceptance evidence. This decision supersedes the separate business and decision capacity lanes.

## Model capacity

A model family is identified by the exact adapter, provider and model tuple. Effort variants share one concurrent-task limit, default 2, bounded to integers 1–32. This counts Buddy-owned unresolved attempts, including routing decisions using that model and attempts whose shutdown remains unconfirmed. It cannot count requests made by other applications or a harness's internal parallel calls. Equal provider labels across different harnesses do not prove that they use the same account; cross-harness quota aliasing is outside this change.

The authenticated user edits this setting on a model card. Hosts, workers and assessment maintenance cannot write it. It follows the existing local draft, short writer grant, revision and idempotent publication protocol. The setting remains when discovery marks a model unavailable. A raise affects subsequent claims immediately; a decrease never terminates existing attempts and only holds new claims until occupancy is below the limit.

There is one machine-wide concurrent-attempt ceiling, configured by BUDDY_MAX_CONCURRENT (default 8, range 1–32). Routing and execution share that ceiling and the same model counters. The separate BUDDY_MAX_DECISIONS setting and reserved lane are removed. Admission must skip full model families before a bounded candidate window can starve unrelated runnable work, retain arrival-order fairness among eligible work, and preserve all workspace, lease and ownership checks. Command/external work without a model has only the machine-wide ceiling.

## Publication interface

The console snapshot and each model-profile page include modelConcurrency, an array of {adapter, provider, model, limit, active}. It contains the model families represented in the response, including defaults for families without an explicit override. active counts unresolved attempts and is observation only. The user_policy_publish operation accepts a modelConcurrency patch array of {adapter, provider, model, limit}; it rejects active and other derived fields. A draft never publishes occupancy. Effort selection does not change which model setting is edited. Save/rebase/recovery preserve unrelated fields and conflicting user edits.

The backend owns one current schema with persistent family settings and a frozen model identity on each claimed attempt. Startup does not migrate older databases. Deployment requires a separately verified offline copy and a stopped/idle cutover; the running board remains usable during development.

## Native interactions and retained artifacts

ZCode requestUserInput and private PostToolUse hooks are separate experiments. A successful native probe must prove same-turn identity, actual model receipt, isolated configuration and owned process shutdown. No capability is declared solely from bundle inspection. Host-initiated injection must never stop or open a new turn. Native questions remain distinct from permissions, and a Host answer cannot impersonate the user's approval.

The requestUserInput experiment established a same-turn answer path; the inspected native question tool has a fixed 30-second timeout. That path cannot provide an arbitrary-duration Host decision boundary. The hooks experiment established a private plugin path for CLI context injection, but did not establish its app-server integration. The implementation therefore uses the existing session-private MCP channel for cooperative inquiry checkpoints and finish-time delivery, subject to a real app-server probe before declaring support. A signed MCP response must also match the actual root session/turn/tool events before it can mark a question delivered or answered; child tool calls cannot answer on the root's behalf. Pending questions at settlement become unavailable, and no inquiry opens another native turn. Arbitrary-duration assistance and approvals continue to use the governed end-of-turn Host boundary.

Artifact acceptance verifies immutable Git objects and the whole governed goal's output, including prior continuation work. Missing old per-path summaries may be reconstructed from proven retained commit/tree identities into a new verification record; old receipts and artifact manifests remain immutable. Missing or mismatched objects fail explicitly. No runtime schema importer or alternate old-contract client is introduced.
