# Buddy service reference index

<a id="buddy-c-two-service"></a>

This page was the full service manual. It is now a short index: each section keeps its original heading so existing links and bookmarks still resolve, and points to the focused page that now owns the topic. Start from [usage.md](usage.md) for the practical path, [cli.md](cli.md) for the complete command contract, and [architecture.md](architecture.md) for how the service works.

## Ownership

Who owns authoritative state, child processes and supervision: [architecture.md](architecture.md#process-topology) and [workers.md](workers.md).

## Default blocking delegation

The default `start` → `await` workflow, `run` as the one-call alternative, and queued admission: [usage.md](usage.md#the-common-path) and [cli.md](cli.md#run-and-wait).

## Lifetimes: execution deadline, wait window, process lifetime

The three independent limits and their defaults: [cli.md](cli.md#defaults-and-bounds) and [usage.md](usage.md#when-the-wait-ends-before-the-task).

## CLI wait window and upgrade ordering

Wait-window defaults and what happens when a wait ends first: [cli.md](cli.md#runawait-envelope). Runtime installation, cache replacement and `restart` versus `stop`: [operations.md](operations.md#runtime-lifecycle-and-upgrade).

## Long jobs

A deadline longer than one wait window, and how to keep waiting on the same durable run: [usage.md](usage.md#when-the-wait-ends-before-the-task).

## Cancellation and recovery

Durable cancel intent, daemon restart, uncertainty, receipts and retry rules: [operations.md](operations.md#cancellation-and-recovery) and [architecture.md](architecture.md#uncertainty-and-recovery).

## Independent workers

Worker processes, supervisors, receipts and the `worker-*` contract: [workers.md](workers.md) and [cli.md](cli.md#workers).

## Adapters

The `dsh`, `command` and `external` adapters with their capabilities and honest limits: [workers.md](workers.md#adapters).

## External workers and the public client

`BoardClient`, the runnable external worker example, and identity/receipt rules: [workers.md](workers.md#a-runnable-external-worker).

## Events and bounded waits

Committed events, cursors, bounded `wait`/`watch`, the dedicated wait resource and `WAIT_OVERLOAD`: [cli.md](cli.md#events-and-bounded-waits) and [architecture.md](architecture.md#wait-separation).

## Inquiry: bounded progress and correlated questions

The inquiry contract, its bounds and its honest failure modes: [cli.md](cli.md#messages-and-inquiry).

### What a no-question inquiry reports

The durable observation, the estimated deadline and `live.unavailable`: [cli.md](cli.md#messages-and-inquiry).

### What a question does

Durable recording before injection, the same live agent, message states and the correlated reply tool: [usage.md](usage.md#progress-and-questions-while-it-runs).

### Transport and ownership

The Node bridge, the private token, the idempotent journal import and the failure modes: [architecture.md](architecture.md#inquiry).

## CLI commands

The complete command surface, defaults, bounds, error codes and envelopes: [cli.md](cli.md#commands).

## Persistence and recovery

The state directory layout, one-daemon ownership, cold-start attachment and recovery semantics: [operations.md](operations.md#private-state-and-environment).

### Environment variables

Every `BUDDY_*` variable with its default and meaning: [operations.md](operations.md#private-state-and-environment).

## Dependencies and validation

uv-managed locked dependencies, the Python and Node requirements, the stable runtime and the source-leak report: [architecture.md](architecture.md#runtime-packaging) and [operations.md](operations.md#runtime-lifecycle-and-upgrade). From the `deepseek-delegate/` directory in a full repository checkout, `uv run --frozen python -m buddy.checks` runs the full check. Staged plugins omit test suites.

## Packaging, migration and unrelated App tools

The stable runtime, legacy import and old-MCP cleanup: [operations.md](operations.md#runtime-lifecycle-and-upgrade), [operations.md](operations.md#legacy-import) and [operations.md](operations.md#removing-the-old-mcp-path).
