"""Version/platform-bound native review certificates and explicit probe admission.

Certificates use existing meta records. Paid probes are ordinary Worker attempts;
cache reads never run a probe, and a lost reply never admits another attempt.
"""
from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import sys
import uuid

from . import schemas
from .db import canonical_json
from .errors import BoardError

ADAPTER = "review-check"
CHECKS = ("requestIdentity", "nativePolicy", "forbiddenTools", "boundaryDenials", "internalRead",
          "inputUnchanged", "sentinelUnchanged", "shutdownConfirmed", "budgetConsistent")
PREFIX = "harness-review:"


@lru_cache(maxsize=1)
def certificates():
    return json.loads(Path(__file__).with_name("review-certificates.json").read_text())


def _key(adapter, version, platform):
    return PREFIX + canonical_json([adapter, version, platform])


def verification_view(connection, adapter, health, *, platform=None):
    platform = platform or sys.platform
    version = health.get("version")
    key = _key(adapter, version, platform)
    saved = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone() if connection else None
    record = json.loads(saved[0]) if saved else next((dict(item) for item in certificates()
        if (item["adapter"], item["version"], item["platform"]) == (adapter, version, platform)), None)
    if record is None:
        prior = any(item["adapter"] == adapter and item["platform"] == platform for item in certificates())
        if connection:
            prior = prior or any(json.loads(row[0]).get("adapter") == adapter and json.loads(row[0]).get("platform") == platform
                for row in connection.execute("SELECT value FROM meta WHERE key LIKE ?", (PREFIX + "%",)))
        record = {"adapter": adapter, "version": version, "platform": platform,
                  "status": "new-version" if prior and version else "unverified",
                  "reasonCode": "HARNESS_REVIEW_VERSION_UNVERIFIED" if prior and version else "HARNESS_REVIEW_UNVERIFIED"}
    if connection and record.get("status") in ("queued", "running") and record.get("runId"):
        task = connection.execute("SELECT state FROM tasks WHERE task_id=?", (record["runId"],)).fetchone()
        if task:
            record = {**record, "status": {"queued": "queued", "running": "running", "cancelling": "stopping",
                       "reconciliation-needed": "unconfirmed"}.get(task[0], "failed")}
    return {**record, "implemented": adapter == "codex", "verified": bool(
        health.get("status") == "ready" and record.get("status") == "verified"
        and record.get("adapter") == adapter and record.get("version") == version
        and record.get("platform") == platform
        and all((record.get("checks") or {}).get(check) is True for check in CHECKS))}


def verified(adapter, health):
    if not health:
        return False
    record = health.get("reviewVerification") or verification_view(None, adapter, health)
    return bool(health.get("status") == "ready" and record.get("verified") is True
                and record.get('adapter') == adapter
                and record.get("version") == health.get("version") and record.get("platform") == sys.platform
                and all((record.get("checks") or {}).get(check) is True for check in CHECKS))


def request(board, params):
    schemas.reject_unknown(params, {"adapter", "profileId", "requestId", "execute", "expectedRevision"}, "harness.verify")
    name = schemas.required_string(params, "adapter", max_length=32)
    if name != "codex":
        raise BoardError("UNSUPPORTED", "This harness has no implemented native review verifier")
    profile_id = schemas.required_string(params, "profileId", max_length=128)
    execute = schemas.optional_bool(params, "execute", False)
    request_id = schemas.required_string(params, "requestId", max_length=128) if execute else None
    expected = params.get("expectedRevision")
    if expected is not None and (type(expected) is not int or expected < 0):
        raise BoardError("INVALID_ARGUMENT", "expectedRevision must be a nonnegative integer")
    task_request = "harness-review:" + (request_id or "")
    with board.db.write() as connection:
        # Recover an admitted call before considering changed health or profiles.
        previous = connection.execute("SELECT * FROM tasks WHERE request_id=?", (task_request,)).fetchone() if execute else None
        if previous:
            spec = json.loads(previous["spec_json"])
            intent = spec.get("reviewCheck") or {}
            if intent.get("adapter") != name or intent.get("profileId") != profile_id or intent.get("expectedRevision") != expected:
                raise BoardError("CONFLICT", "This requestId belongs to another review verification")
            return {"runId": previous["task_id"], "status": previous["state"], "duplicate": True}
        from .harness_health import read_health
        health = read_health(connection, name)
        if expected is not None and expected != health["revision"]:
            raise BoardError("REVISION_CONFLICT", "Harness changed; refresh before verifying")
        profile = connection.execute("SELECT * FROM evaluation_profiles WHERE profile_id=?", (profile_id,)).fetchone()
        if not (profile and profile["adapter"] == name and profile["enabled"] and profile["available"]
                and health["available"] and health.get("version") not in (None, "unknown")
                and health.get("command") and all(profile[key] for key in ("provider", "model", "effort"))):
            raise BoardError("CONFIGURATION_UNAVAILABLE", "Choose an enabled configuration in a healthy harness with a known version")
        selected = {key: profile[key] for key in schemas.CONFIGURATION_FIELDS}
        plan = {"adapter": name, "version": health["version"], "platform": sys.platform,
                "profileId": profile_id, "configuration": selected, "checks": list(CHECKS),
                "budget": {"preset": "standard", "timeoutSeconds": 300, "toolCalls": 24, "bytesRead": 524288},
                "maxNativeTurns": 2, "formatCorrectionOnly": True, "modelCall": True}
        if not execute:
            return {"plan": plan, "started": False}
        pending = connection.execute("SELECT task_id FROM tasks t WHERE adapter=? AND json_extract(spec_json,'$.reviewCheck.adapter')=?"
            " AND (state IN ('queued','running','cancelling','reconciliation-needed') OR EXISTS(SELECT 1 FROM attempts a"
            " WHERE a.task_id=t.task_id AND (a.execution_state IN ('starting','executing','finalizing','uncertain') OR a.shutdown_confirmed=0 AND a.result_json IS NOT NULL))) LIMIT 1", (ADAPTER, name)).fetchone()
        if pending:
            raise BoardError('HARNESS_REVIEW_BUSY', 'A verification for this harness is pending or its stop is unconfirmed; inspect its run')
        key = _key(name, health["version"], sys.platform)
        current = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        if current:
            previous_run = json.loads(current[0]).get("runId")
            task = connection.execute("SELECT * FROM tasks WHERE task_id=?", (previous_run,)).fetchone()
            attempt = board._selected_attempt(connection, task) if task else None
            if task and (task["state"] in ("queued", "running", "cancelling", "reconciliation-needed")
                         or attempt is not None and not attempt["shutdown_confirmed"]):
                raise BoardError("HARNESS_REVIEW_BUSY", "A review verification is pending or its stop is unconfirmed; inspect its run")
        task_id, now = str(uuid.uuid4()), board.now()
        intent = {**plan, "expectedRevision": expected,
                  "harness": {key: health.get(key) for key in ("adapter", "version", "command", "locationFingerprint")}}
        spec = {"adapter": ADAPTER, "cwd": str(board.directory / "review-checks" / task_id),
                "task": "Verify native read-only review boundaries.", "timeoutSeconds": 310,
                "workspace": False, "requiredCapabilities": [ADAPTER], "exclusiveResources": [], "reviewCheck": intent}
        connection.execute("INSERT INTO tasks(task_id,request_id,owner,spec_json,spec_canonical_json,input_fingerprint,"
            "fingerprint_version,adapter,required_capabilities,cwd,exclusive_resources,timeout_seconds,state,queue_reason,revision,created_at,updated_at)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (task_id, task_request, "harness-review", canonical_json(spec), canonical_json(spec),
            schemas.spec_fingerprint(spec), schemas.FINGERPRINT_VERSION_CURRENT, ADAPTER, canonical_json([ADAPTER]), spec["cwd"],
            "[]", 310, "queued", board._admission_blocker(connection, spec) or "awaiting-worker", 1, now, now))
        record = {"adapter": name, "version": health["version"], "platform": sys.platform,
                  "status": "queued", "runId": task_id, "checkedAt": now, "source": "native-probe"}
        connection.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, canonical_json(record)))
        board._append_event(connection, "harness.review_requested", task_id=task_id, payload=record)
        head = board._head_of(connection)
    board._notify(head)
    return {"runId": task_id, "status": "queued", "plan": plan, "duplicate": False}


def complete(board, connection, task, attempt, *, result, status, shutdown_confirmed, now):
    if task["adapter"] != ADAPTER:
        return
    intent = json.loads(task["spec_json"])["reviewCheck"]
    key = _key(intent["adapter"], intent["version"], intent["platform"])
    current = connection.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    if not current or json.loads(current[0]).get("runId") != task["task_id"]:
        return
    from .harness_health import read_health
    health = read_health(connection, intent["adapter"])
    checks = result.get("checks") if isinstance(result, dict) else None
    binding = health.get('status') == 'ready' and all(health.get(k) == intent['harness'].get(k) for k in ('version', 'command', 'locationFingerprint'))
    passed = bool(status == "ok" and shutdown_confirmed and isinstance(checks, dict)
                  and all(checks.get(check) is True for check in CHECKS)
                  and result.get("version") == intent["version"] and result.get("platform") == intent["platform"]
                  and binding)
    record = {"adapter": intent["adapter"], "version": intent["version"], "platform": intent["platform"],
              "status": "verified" if passed else "failed", "checkedAt": now, "source": "native-probe",
              "runId": task["task_id"], "attemptId": attempt["attempt_id"],
              "checks": {check: bool(isinstance(checks, dict) and checks.get(check) is True) for check in CHECKS},
              "reasonCode": None if passed else 'HARNESS_REVIEW_BINDING_CHANGED' if not binding else
                  'HARNESS_REVIEW_SHUTDOWN_UNCONFIRMED' if not shutdown_confirmed else "HARNESS_REVIEW_CHECK_FAILED"}
    from .usage import identifier
    if not passed and isinstance(result, dict) and identifier(result.get('reasonCode')):
        record['nativeReasonCode'] = result['reasonCode']
    if isinstance(result, dict) and isinstance(result.get('evidenceSha256'), str) and schemas.SHA256_PATTERN.fullmatch(result['evidenceSha256']):
        record['evidenceSha256'] = result['evidenceSha256']
    if isinstance(result, dict):
        record["failedChecks"] = [check for check in CHECKS if record["checks"][check] is not True]
    connection.execute("UPDATE meta SET value=? WHERE key=?", (canonical_json(record), key))
    board._append_event(connection, "harness.review_verified" if passed else "harness.review_failed", task_id=task["task_id"], payload=record)
