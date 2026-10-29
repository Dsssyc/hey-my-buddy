"""Frozen schema 30 → 31: definitions and backfill from bad89c7b.

This step has run on a real board. Never import current storage or statistics
here; a later stored form belongs in the next step.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
import json
import math

ROUTER_MAINTENANCE_COLUMNS = (
    "maintenance_buddy_id TEXT",
    "maintenance_interval_seconds INTEGER NOT NULL DEFAULT 3600 CHECK (maintenance_interval_seconds >= 1)",
)

DOMAIN_EVALUATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS maintenance_runs (
    maintenance_run_id TEXT NOT NULL PRIMARY KEY,
    trigger_type       TEXT NOT NULL CHECK (trigger_type IN ('interval','manual')),
    buddy_id           TEXT NOT NULL,
    task_id            TEXT NOT NULL UNIQUE REFERENCES tasks(task_id) ON DELETE RESTRICT,
    state              TEXT NOT NULL CHECK (state IN ('running','completed','failed')),
    reason_code        TEXT,
    reason_params_json TEXT,
    started_at         TEXT NOT NULL,
    finished_at        TEXT,
    CHECK ((state='running') = (finished_at IS NULL)),
    CHECK ((reason_code IS NULL AND reason_params_json IS NULL)
        OR (reason_code IS NOT NULL AND reason_params_json IS NOT NULL
            AND json_valid(reason_params_json) AND json_type(reason_params_json)='object')),
    CHECK (state!='failed' OR reason_code IS NOT NULL),
    CHECK (finished_at IS NULL OR finished_at >= started_at)
);
CREATE UNIQUE INDEX IF NOT EXISTS maintenance_runs_running_unique
    ON maintenance_runs(state) WHERE state='running';

CREATE TABLE IF NOT EXISTS domain_nodes (
    node_id            TEXT NOT NULL PRIMARY KEY,
    name               TEXT NOT NULL CHECK (length(trim(name)) > 0),
    definition         TEXT NOT NULL CHECK (length(trim(definition)) > 0),
    parent_id          TEXT REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    merged_into_id     TEXT REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    maintenance_run_id TEXT NOT NULL REFERENCES maintenance_runs(maintenance_run_id) ON DELETE RESTRICT,
    CHECK (parent_id IS NULL OR parent_id != node_id),
    CHECK (merged_into_id IS NULL OR merged_into_id != node_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS domain_nodes_active_name_unique
    ON domain_nodes(name) WHERE merged_into_id IS NULL;
CREATE INDEX IF NOT EXISTS domain_nodes_parent_idx ON domain_nodes(parent_id);

CREATE TABLE IF NOT EXISTS micro_task_classifications (
    run_id             TEXT NOT NULL PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    domain_node_id     TEXT NOT NULL REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    task_type          TEXT NOT NULL CHECK (task_type IN ('create','change','debug','verify','review','research','write')),
    maintenance_run_id TEXT NOT NULL REFERENCES maintenance_runs(maintenance_run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS kind_summaries (
    harness            TEXT NOT NULL,
    model              TEXT NOT NULL,
    effort             TEXT NOT NULL,
    text               TEXT NOT NULL,
    maintenance_run_id TEXT NOT NULL REFERENCES maintenance_runs(maintenance_run_id) ON DELETE RESTRICT,
    PRIMARY KEY (harness, model, effort)
);
CREATE TABLE IF NOT EXISTS kind_summary_evidence (
    harness TEXT NOT NULL,
    model   TEXT NOT NULL,
    effort  TEXT NOT NULL,
    run_id  TEXT NOT NULL REFERENCES workflow_runs(run_id) ON DELETE RESTRICT,
    cited   INTEGER NOT NULL CHECK (cited IN (0, 1)),
    PRIMARY KEY (harness, model, effort, run_id),
    FOREIGN KEY (harness, model, effort) REFERENCES kind_summaries(harness, model, effort) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS kind_summary_evidence_run_idx ON kind_summary_evidence(run_id);

CREATE TABLE IF NOT EXISTS routing_judgments (
    decision_id    TEXT NOT NULL PRIMARY KEY REFERENCES evaluation_decisions(decision_id) ON DELETE RESTRICT,
    domain_node_id TEXT REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    task_type      TEXT NOT NULL CHECK (task_type IN ('create','change','debug','verify','review','research','write'))
);
"""

STATISTICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS kind_statistics (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),
    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),
    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),
    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),
    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),
    PRIMARY KEY (harness, model, effort),
    CHECK (successes + failures = samples)
);
CREATE TABLE IF NOT EXISTS kind_cell_statistics (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    domain_node_id TEXT NOT NULL REFERENCES domain_nodes(node_id) ON DELETE RESTRICT,
    task_type TEXT NOT NULL CHECK (task_type IN ('create','change','debug','verify','review','research','write')),
    samples INTEGER NOT NULL CHECK (typeof(samples)='integer' AND samples > 0),
    successes INTEGER NOT NULL CHECK (typeof(successes)='integer' AND successes >= 0),
    first_passes INTEGER NOT NULL CHECK (typeof(first_passes)='integer' AND first_passes >= 0 AND first_passes <= successes),
    rework_rounds INTEGER NOT NULL CHECK (typeof(rework_rounds)='integer' AND rework_rounds >= 0),
    failures INTEGER NOT NULL CHECK (typeof(failures)='integer' AND failures >= 0),
    PRIMARY KEY (harness, model, effort, domain_node_id, task_type),
    CHECK (successes + failures = samples)
);
CREATE TABLE IF NOT EXISTS kind_statistics_buckets (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('delivery_seconds','input_tokens','cached_input_tokens','output_tokens')),
    sign INTEGER NOT NULL CHECK (typeof(sign)='integer' AND sign IN (-1,0,1)),
    bucket INTEGER NOT NULL CHECK (typeof(bucket)='integer'),
    count INTEGER NOT NULL CHECK (typeof(count)='integer' AND count > 0),
    CHECK (sign != 0 OR bucket = 0),
    CHECK (sign != -1 OR metric = 'delivery_seconds'),
    PRIMARY KEY (harness, model, effort, metric, sign, bucket),
    FOREIGN KEY (harness, model, effort) REFERENCES kind_statistics(harness, model, effort) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS kind_cell_statistics_buckets (
    harness TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    domain_node_id TEXT NOT NULL,
    task_type TEXT NOT NULL,
    metric TEXT NOT NULL CHECK (metric IN ('delivery_seconds','input_tokens','cached_input_tokens','output_tokens')),
    sign INTEGER NOT NULL CHECK (typeof(sign)='integer' AND sign IN (-1,0,1)),
    bucket INTEGER NOT NULL CHECK (typeof(bucket)='integer'),
    count INTEGER NOT NULL CHECK (typeof(count)='integer' AND count > 0),
    CHECK (sign != 0 OR bucket = 0),
    CHECK (sign != -1 OR metric = 'delivery_seconds'),
    PRIMARY KEY (harness, model, effort, domain_node_id, task_type, metric, sign, bucket),
    FOREIGN KEY (harness, model, effort, domain_node_id, task_type)
        REFERENCES kind_cell_statistics(harness, model, effort, domain_node_id, task_type) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS buddy_infrastructure_failures (
    harness TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    effort TEXT NOT NULL,
    count INTEGER NOT NULL CHECK (typeof(count)='integer' AND count > 0),
    last_at TEXT NOT NULL,
    PRIMARY KEY (harness, provider, model, effort)
);
"""

def turn_buddy(input_json):
    return json.loads(input_json)["context"]["buddy"] if input_json is not None else None

NEITHER = frozenset({"service-stop", "console-stop", "transport-error",
                     "workspace-error", "unknown-stop", "interrupted"})

PROVIDER_REJECTIONS = frozenset({"overloaded", "quota-exceeded", "rate-limited"})

IDENTITY = ("harness", "provider", "model", "effort")

KIND = ("harness", "model", "effort")

USAGE_FIELDS = ("inputTokens", "cachedInputTokens", "outputTokens")

METRICS = ("delivery_seconds", "input_tokens", "cached_input_tokens", "output_tokens")

COUNTS = ("samples", "successes", "first_passes", "rework_rounds", "failures")

ENDED_STATES = "('returned','failed','rejected','interrupted')"

BUCKET_RATIO = 1.2

def key(buddy, fields):
    return tuple(buddy[field] for field in fields)

def time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))

def round_classification(receipt: dict | None) -> str:
    """Product/user termination wins over provider rejection; no receipt is neither."""
    if receipt is None or receipt["terminationReason"] in NEITHER:
        return "neither"
    result = receipt["result"] or {}
    rejection = result.get("providerRejection") or {}
    if result.get("code") == "harness-unavailable" or rejection.get("code") in PROVIDER_REJECTIONS:
        return "infrastructure"
    return "ability"

def ended_turns(connection, run_id=None):
    where, parameters = ("", ()) if run_id is None else (" AND w.run_id=?", (run_id,))
    for row in connection.execute(
        "SELECT w.run_id,w.turn_index,w.state,w.input_json,a.result_json,"
        " a.attempt_id,a.started_at,a.finished_at FROM workflow_turns w"
        " JOIN attempts a ON a.attempt_id=w.attempt_id"
        " WHERE w.state IN " + ENDED_STATES + where + " ORDER BY w.run_id,w.turn_index", parameters
    ):
        receipt = json.loads(row["result_json"]) if row["result_json"] is not None else None
        result = (receipt["result"] or {}) if receipt is not None else {}
        turn = result.get("turn") or {}
        yield {**dict(row), "buddy": turn_buddy(row["input_json"]),
               "category": round_classification(receipt), "usage": result.get("tokenUsage"),
               "delivered": row["state"] == "returned" and turn["outcome"]["disposition"] == "completed"}

def samples_for_run(connection, run_id):
    """The original read-time sample rule, restricted to the ending micro task."""
    run = connection.execute(
        "SELECT r.state,r.final_attempt_id,c.execution_status FROM workflow_runs r"
        " LEFT JOIN workflow_host_conclusions c ON c.run_id=r.run_id"
        " WHERE r.run_id=? AND r.state IN ('accepted','concluded')", (run_id,)
    ).fetchone()
    if run is None:
        return {}
    groups = defaultdict(list)
    for turn in ended_turns(connection, run_id):
        groups[key(turn["buddy"], KIND)].append(turn)
    continuations = list(connection.execute(
        "SELECT c.created_at,w.turn_index FROM workflow_continuations c"
        " LEFT JOIN workflow_turns w ON w.turn_id=c.turn_id WHERE c.kind='manual' AND c.run_id=?", (run_id,)))
    integrations = [json.loads(row[0]) for row in connection.execute(
        "SELECT i.verification_json FROM workflow_integrations i JOIN workflow_runs r"
        " ON r.run_id=i.run_id AND r.final_artifact_id=i.artifact_id WHERE r.run_id=? AND r.state='accepted'", (run_id,))]
    samples = {}
    for kind, turns in groups.items():
        ability = [turn for turn in turns if turn["category"] == "ability"]
        if not ability:
            continue
        success = run["state"] == "accepted" and any(
            turn["attempt_id"] == run["final_attempt_id"] for turn in turns)
        deliveries = [turn for turn in turns if turn["delivered"]]
        if run["execution_status"] == "cancelled" and not deliveries:
            continue
        if not success and turns[-1]["category"] != "ability":
            continue
        # Acceptance owns the frozen turn's delivery, including an explicitly
        # completed assistance/attention or an adopted workspace-conflict seal.
        if success:
            accepted = next(turn for turn in turns if turn["attempt_id"] == run["final_attempt_id"])
            if not accepted["delivered"]:
                deliveries.append(accepted)
                deliveries.sort(key=lambda turn: turn["turn_index"])
        rework = 0
        if deliveries:
            first = deliveries[0]
            rework = sum(
                (item["turn_index"] > first["turn_index"] if item["turn_index"] is not None
                 else time(item["created_at"]) >= time(first["finished_at"]))
                for item in continuations)
        first_pass = False
        if success:
            unchanged = not any(integrations[-1].get(field) for field in ("differingPaths", "missingPaths", "hostPaths"))
            first_pass = deliveries[0] is accepted and rework == 0 and unchanged
        usage = None
        if all((turn["usage"] or {}).get("completeness") == "complete" for turn in ability):
            usage = tuple(sum(turn["usage"][field] for turn in ability) for field in USAGE_FIELDS)
        samples[kind] = {
            "success": success, "first_pass": first_pass, "rework": rework,
            "durations": [(time(turn["finished_at"]) - time(turn["started_at"])).total_seconds()
                          for turn in deliveries], "usage": usage,
        }
    return samples

def bucket(value):
    """Sign and log-magnitude; (0,0) is zero, |value| lies in [1.2**k,1.2**(k+1)).

    Wall-clock timestamps can move backwards, so delivery durations retain their
    sign just as the original reader did. Usage is normalized nonnegative input.
    """
    if value == 0:
        return (0, 0)
    return (1 if value > 0 else -1, math.floor(math.log(abs(value)) / math.log(BUCKET_RATIO)))

def _add_sample(connection, kind, sample, cell):
    table = "kind_statistics" if cell is None else "kind_cell_statistics"
    fields = KIND if cell is None else (*KIND, "domain_node_id", "task_type")
    values = kind if cell is None else (*kind, *cell)
    counts = (1, int(sample["success"]), int(sample["first_pass"]), sample["rework"], int(not sample["success"]))
    connection.execute(
        f"INSERT INTO {table}({','.join((*fields, *COUNTS))}) VALUES({','.join('?' for _ in (*values, *counts))})"
        f" ON CONFLICT({','.join(fields)}) DO UPDATE SET " +
        ",".join(f"{field}={field}+excluded.{field}" for field in COUNTS), (*values, *counts))
    bins = Counter((METRICS[0], bucket(value)) for value in sample["durations"])
    if sample["usage"] is not None:
        bins.update((metric, bucket(value)) for metric, value in zip(METRICS[1:], sample["usage"]))
    connection.executemany(
        f"INSERT INTO {table}_buckets({','.join((*fields, 'metric', 'sign', 'bucket', 'count'))})"
        f" VALUES({','.join('?' for _ in range(len(values) + 4))})"
        f" ON CONFLICT({','.join((*fields, 'metric', 'sign', 'bucket'))}) DO UPDATE SET count=count+excluded.count",
        [(*values, metric, sign, exponent, count) for (metric, (sign, exponent)), count in bins.items()])

def accumulate_overall(connection, run_id):
    for kind, sample in samples_for_run(connection, run_id).items():
        _add_sample(connection, kind, sample, None)

def accumulate_infrastructure(connection, buddy, receipt, finished_at, *, new_round=True):
    if round_classification(receipt) != "infrastructure":
        return
    previous = connection.execute(
        "SELECT last_at FROM buddy_infrastructure_failures WHERE harness=? AND provider=? AND model=? AND effort=?",
        key(buddy, IDENTITY)).fetchone()
    latest = (finished_at if previous is None or time(finished_at) > time(previous["last_at"])
              else previous["last_at"])
    if not new_round:
        # A later stop proof settles this same turn again and advances its
        # finished_at. It refreshes the latest time, not the number of rounds.
        connection.execute(
            "UPDATE buddy_infrastructure_failures SET last_at=? WHERE harness=? AND provider=? AND model=? AND effort=?",
            (latest, *key(buddy, IDENTITY)))
        return
    connection.execute(
        "INSERT INTO buddy_infrastructure_failures(harness,provider,model,effort,count,last_at) VALUES(?,?,?,?,1,?)"
        " ON CONFLICT(harness,provider,model,effort) DO UPDATE SET count=count+1,last_at=excluded.last_at",
        (*key(buddy, IDENTITY), latest))

def backfill(connection):
    for row in connection.execute("SELECT run_id FROM workflow_runs WHERE state IN ('accepted','concluded')").fetchall():
        accumulate_overall(connection, row["run_id"])
    for turn in ended_turns(connection):
        receipt = json.loads(turn["result_json"]) if turn["result_json"] is not None else None
        accumulate_infrastructure(connection, turn["buddy"], receipt, turn["finished_at"])


def upgrade(connection):
    for ddl in (DOMAIN_EVALUATION_SCHEMA + STATISTICS_SCHEMA).split(";"):
        if ddl.strip():
            connection.execute(ddl)
    for column in ROUTER_MAINTENANCE_COLUMNS:
        connection.execute("ALTER TABLE router_settings ADD COLUMN " + column)
    backfill(connection)
    connection.execute("UPDATE meta SET value='31' WHERE key='schema_version'")
