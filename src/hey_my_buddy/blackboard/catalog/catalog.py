"""Bounded, harness-neutral model discovery and legal buddy validation.

Harnesses own native discovery. This module validates public metadata, preserves
harness/provider/model identity, and proposes disabled buddies. Discovery never
calls a model or publishes an evaluation card.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from contextlib import nullcontext
import json
import os
import re
import sqlite3
import threading
from pathlib import Path
from typing import Any, Callable

from ...errors import BoardError
from ...protocol.reasons import REGISTERED_REASON_PARAMETERS, reason

MAX_MODELS = 200
MAX_PROVIDERS = 32
MAX_TEXT = 2000
MAX_CATALOG_BYTES = 2 * 1024 * 1024
CATALOG_FILE_ENV = "HMB_MODEL_CATALOG_FILE"
BUDDY_FIELDS = ("harness", "provider", "model", "effort")

#: ADR-027 rule 1: the account-status fact every harness reports per discovery.
#: The blackboard, not the harness, decides whether a reading is trusted; a
#: missing or malformed fact reads as ``unknown`` and cannot replace the catalog.
ACCOUNT_STATUSES = ("confirmed", "unknown", "not-applicable")
TRUSTED_ACCOUNT_STATUSES = ("confirmed", "not-applicable")

#: ADR-027 rule 2: one confirmed absence starts a confirmation window; only a
#: further confirmed absence after this window marks a model unavailable.
CONFIRMATION_SECONDS = 3600

#: ADR-027 rule 4: a harness catalog without a confirmed reading for this long
#: is re-read at the next health check. Unknown readings do not extend it.
CATALOG_SHELF_SECONDS = 6 * 3600

#: ADR-027 rule 2: the unavailable reason of a model whose absence was confirmed.
CONFIRMED_ABSENCE_REASON = "catalog.model-absent"

#: The unavailable reason of one retired effort whose model remains in the reading.
RETIRED_EFFORT_REASON = "catalog.effort-retired"

#: Unavailable reasons that are catalog facts, never undone by health recovery.
#: A 0 left behind by a harness health failure is a health fact, not evidence
#: that the native model disappeared (ADR-027 rules 1 and 2).
CATALOG_FACT_REASONS = (CONFIRMED_ABSENCE_REASON, RETIRED_EFFORT_REASON, "catalog.native-unavailable")

#: ADR-027 rule 3: how an operator refreshes harness catalogs and their readings.

#: The one reason every catalog-caused refusal carries. The user's independent
#: enabled gate keeps its own separate reason.
CATALOG_UNAVAILABLE_REASON = "catalog-unavailable"

# The discovery receipt crosses a process boundary. These are the reasons the
# registered native readers and this module can emit at its two reason sites.
_HEALTH_REASONS = frozenset({
    "harness.account-read-failed", "harness.auth-unverified", "harness.interpreter-missing",
    "harness.launch-failed", "harness.login-required", "harness.node-missing",
    "harness.not-checked", "harness.not-found", "harness.output-limit",
    "harness.pipe-failed", "harness.probe-failed", "harness.scan-timeout",
    "harness.shutdown-unverified", "harness.timeout", "harness.version-unknown",
})
_DISCOVERY_REASONS = frozenset({
    "config-options-shape-unreadable", "config-shape-unreadable",
    "model-list-shape-unreadable", "model-cursor-shape-unreadable",
    "catalog.model-shape-unreadable", "native-metadata-wait-expired",
    "catalog.discovery-failed", *_HEALTH_REASONS,
})
_WARNING_REASONS = frozenset({
    "catalog.model-identity-omitted", "catalog.model-identity-or-effort-omitted",
    "catalog.effort-directory-omitted", "catalog.model-list-unreadable",
    "catalog.initialize-incomplete", "catalog.acp-initialize-unreadable",
    "catalog.model-shape-unreadable", "catalog.native-warning",
    "catalog.discovery-failed", *_HEALTH_REASONS,
})

#: The service's bounded native re-read uses its existing harness execution path.
_catalog_reread: dict[str, Callable[[str], None]] = {}
CATALOG_REREAD_JOIN_SECONDS = 55
_catalog_reread_gate = threading.Lock()


class _CatalogRereadFlight:
    def __init__(self) -> None:
        self.finished = threading.Event()


_catalog_reread_flights: dict[tuple[str, str], _CatalogRereadFlight] = {}


def register_catalog_reread(directory: Path | str, hook: Callable[[str], None] | None) -> None:
    if hook is None:
        _catalog_reread.pop(str(directory), None)
    else:
        _catalog_reread[str(directory)] = hook


def coordinate_catalog_reread(directory: Path | str, harness: str, *, reader: Callable[[], None],
                              wait: bool, timeout: float | None = None) -> str:
    """Claim or join one bounded harness read shared by validation and health."""
    from ..store.db import Database
    from ..service.harness_health import SCAN_SECONDS, _later
    if timeout is None:
        timeout = CATALOG_REREAD_JOIN_SECONDS
    key = (str(directory), harness)
    with _catalog_reread_gate:
        with Database(directory).write() as db:
            flight = _catalog_reread_flights.get(key)
            joined = flight is not None
            if flight is None:
                row = db.execute('SELECT scan_after FROM harness_health WHERE harness=?', (harness,)).fetchone()
                window = _parse_time(row['scan_after']) if row else None
                if window is not None and datetime.now(timezone.utc) < window:
                    return 'window'
                from ...json_codec import canonical_json
                db.execute('INSERT INTO harness_health(harness,scan_after,record_json) VALUES(?,?,?) '
                           'ON CONFLICT(harness) DO UPDATE SET scan_after=excluded.scan_after',
                           (harness, _later(SCAN_SECONDS),
                            canonical_json({'reason': reason('harness.not-checked', operation='probe')})))
                flight = _CatalogRereadFlight()
                _catalog_reread_flights[key] = flight
    if joined:
        if not wait:
            return 'inflight'
        return 'joined' if flight.finished.wait(timeout) else 'timeout'
    try:
        reader()
        return 'started'
    finally:
        with _catalog_reread_gate:
            _catalog_reread_flights.pop(key)
        flight.finished.set()


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def _model_service_key(provider: str, model: str) -> str:
    return provider + "\x1f" + model


def _pending_map(db, harness: str) -> dict:
    return {_model_service_key(row["provider"], row["model"]):
            {"since": row["since"], "efforts": json.loads(row["efforts_json"])}
            for row in db.execute("SELECT provider,model,since,efforts_json FROM catalog_pending WHERE harness=?",
                                  (harness,))}


def _write_pending_map(db, harness: str, pending: dict) -> None:
    from ...json_codec import canonical_json

    db.execute("DELETE FROM catalog_pending WHERE harness=?", (harness,))
    db.executemany("INSERT INTO catalog_pending(harness,provider,model,since,efforts_json) VALUES(?,?,?,?,?)",
                   [(harness, *key.split("\x1f"), value["since"], canonical_json(value["efforts"]))
                    for key, value in pending.items()])


def pending_model_services(db) -> dict[tuple[str, str, str], str]:
    """Pending model services and their first absence, scoped to the harness."""
    return {(row["harness"], row["provider"], row["model"]): row["since"]
            for row in db.execute("SELECT harness,provider,model,since FROM catalog_pending")}


def pending_model_efforts(db, harness: str, provider: str, model: str) -> list[str] | None:
    """The legal efforts frozen at first absence, or None when not pending."""
    value = _pending_map(db, harness).get(_model_service_key(provider, model))
    return value["efforts"] if value is not None else None


def catalog_read_at(db, harness: str) -> str | None:
    """When an actual trusted catalog reading was applied."""
    row = db.execute("SELECT read_at FROM catalog_current WHERE harness=?", (harness,)).fetchone()
    return row['read_at'] if row is not None else None


def identities_from_payload(payload_json: Any, harness: str) -> set[tuple[str, str, str]]:
    """Legal harness/provider/model/effort identities a retained catalog lists.

    A model the retained reading itself declared unavailable contributes none
    of its efforts: that unavailability is a native fact, not a health artifact
    (ADR-027 rule 1).
    """
    identities: set[tuple[str, str, str]] = set()
    if not isinstance(payload_json, str) or not payload_json:
        return identities
    try:
        view = CatalogView.from_payload(json.loads(payload_json))
    except (ValueError, BoardError):
        return identities
    for entry in view.payload["providers"]:
        if entry["harness"] != harness:
            continue
        for model in entry["models"]:
            if model["available"]:
                identities.update((entry["provider"], model["id"], effort) for effort in model["efforts"])
    return identities


def model_services_from_payload(payload_json: Any, harness: str) -> set[tuple[str, str]]:
    """Model services a retained confirmed catalog still lists as available."""
    return {(provider, model) for provider, model, _effort in identities_from_payload(payload_json, harness)}


def restore_retained_availability(db, harness: str) -> int:
    """Restore the model availability of the retained catalog after health noise.

    ADR-027 separates health availability from adopted catalog facts, at the
    effort level: the 0s a harness health failure left on ``buddies``
    are never evidence that a native buddy disappeared. A row returns
    only when the retained confirmed catalog still lists its exact
    harness/provider/model/effort identity, or when it is one of the last legal
    efforts recorded for a model service still awaiting disappearance confirmation. A
    confirmed absence, a retired effort and a native unavailable declaration are
    catalog facts and are never restored here.
    """
    row = db.execute(
        "SELECT d.payload_json FROM catalog_current c"
        " LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.harness=?",
        (harness,)).fetchone()
    identities = identities_from_payload(row[0] if row is not None else None, harness)
    pending_efforts = {}
    for key in _pending_map(db, harness):
        provider, _, model = key.partition("\x1f")
        pending_efforts[(provider, model)] = pending_model_efforts(db, harness, provider, model) or []
    restored = 0
    for buddy in db.execute(
            "SELECT buddy_id, provider, model, effort, available, unavailable_code"
            " FROM buddies WHERE harness=?",
            (harness,)).fetchall():
        if buddy["available"] or buddy["unavailable_code"] in CATALOG_FACT_REASONS:
            continue
        model_service = (buddy["provider"], buddy["model"])
        identity = (*model_service, buddy["effort"])
        legal_pending = pending_efforts.get(model_service)
        if identity in identities or (legal_pending is not None and buddy["effort"] in legal_pending):
            db.execute("UPDATE buddies SET available=1,unavailable_code=NULL,unavailable_params_json=NULL,unavailable_content=NULL WHERE buddy_id=?",
                       (buddy["buddy_id"],))
            restored += 1
    return restored


def catalog_account_status(db, harness: str) -> str:
    """The account-status fact of the last trusted reading; unknown without one."""
    row = db.execute("SELECT account_status FROM catalog_current WHERE harness=?", (harness,)).fetchone()
    return row['account_status'] if row is not None else "unknown"


def note_confirmed_read(db, harness: str, *, now: str, account_status: str) -> None:
    """Persist the fact that a trusted reading was applied for this harness."""
    db.execute("UPDATE catalog_current SET read_at=?,account_status=? WHERE harness=?",
               (now, account_status, harness))


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _identity(value: Any, field: str, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise BoardError("CATALOG_INVALID", f"{field} must be a nonempty string of at most {limit} characters")
    return value.strip()


def _strings(value: Any, *, limit: int = 32) -> list[str]:
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(item.strip()[:128] for item in value if isinstance(item, str) and item.strip()))[:limit]


def _receipt_reason(value: Any, *, allowed: frozenset[str], field: str,
                    harness: str | None = None) -> dict:
    if not isinstance(value, dict) or set(value) != {"code", "params"}:
        raise BoardError("CATALOG_INVALID", f"{field} must have code and params")
    code, params = value["code"], value["params"]
    if not isinstance(code, str) or code not in allowed or not isinstance(params, dict):
        raise BoardError("CATALOG_INVALID", f"{field} has an unsupported reason")
    if set(params) != set(REGISTERED_REASON_PARAMETERS[code]) or any(
            type(item) is not str or len(item) > MAX_TEXT for item in params.values()):
        raise BoardError("CATALOG_INVALID", f"{field} has invalid reason parameters")
    if harness is not None and "harness" in params and params["harness"] != harness:
        raise BoardError("CATALOG_INVALID", f"{field} names another harness")
    return value


def canonical_payload(value: Any) -> dict:
    """Validate a discovery receipt once, then keep only bounded metadata."""
    if not isinstance(value, dict):
        raise BoardError("CATALOG_INVALID", "The discovered catalog must be a JSON object")
    discoveries = value.get("discoveries", [])
    if not isinstance(discoveries, list):
        raise BoardError("CATALOG_INVALID", "discoveries must be bounded per-harness observations")
    for item in discoveries:
        if isinstance(item, dict) and item.get("reason") is not None:
            _receipt_reason(item["reason"], allowed=_DISCOVERY_REASONS,
                            field="discovery.reason", harness=item.get("harness"))
    warnings = value.get("warnings") or []
    if not isinstance(warnings, list) or len(warnings) > 16:
        raise BoardError("CATALOG_INVALID", "warnings must be a list of at most 16 entries")
    for item in warnings:
        if isinstance(item, dict) and "reason" in item:
            _receipt_reason(item["reason"], allowed=_WARNING_REASONS,
                            field="warning.reason", harness=item.get("harness"))
    return _normalized_payload(value)


def _normalized_payload(value: dict) -> dict:
    """Normalize an already checked receipt or an assembly of checked receipts."""
    source = _identity(value.get("source"), "source", 1024)
    raw_providers = value.get("providers")
    if not isinstance(raw_providers, list) or len(raw_providers) > MAX_PROVIDERS:
        raise BoardError("CATALOG_INVALID", f"A catalog may contain at most {MAX_PROVIDERS} providers")
    providers, seen_providers = [], set()
    model_count = 0
    for entry in raw_providers:
        if not isinstance(entry, dict):
            raise BoardError("CATALOG_INVALID", "Each provider must be an object")
        harness = _identity(entry.get("harness"), "harness", 32)
        provider = _identity(entry.get("provider"), "provider")
        if (harness, provider) in seen_providers:
            raise BoardError("CATALOG_INVALID", f"Repeated catalog provider {harness}/{provider}")
        seen_providers.add((harness, provider))
        provider_efforts = _strings(entry.get("efforts"))
        raw_models = entry.get("models")
        if not isinstance(raw_models, list):
            raise BoardError("CATALOG_INVALID", f"{harness}/{provider} models must be a list")
        model_count += len(raw_models)
        if model_count > MAX_MODELS:
            raise BoardError("CATALOG_INVALID", f"A catalog may contain at most {MAX_MODELS} models")
        models, seen_models = [], set()
        for model in raw_models:
            if not isinstance(model, dict):
                raise BoardError("CATALOG_INVALID", "Each model must be an object")
            model_id = _identity(model.get("id"), "model.id")
            if model_id in seen_models:
                raise BoardError("CATALOG_INVALID", f"Repeated model {harness}/{provider}/{model_id}")
            seen_models.add(model_id)
            efforts = _strings(model.get("efforts")) if "efforts" in model else provider_efforts
            if not efforts:
                raise BoardError("CATALOG_INVALID", f"No legal efforts declared for {harness}/{provider}/{model_id}")
            context = model.get("contextWindow")
            if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
                context = None
            available = model.get("available", True) is True
            native_content = model.get("unavailableContent")
            if native_content is None and isinstance(model.get("unavailableReason"), str):
                native_content = model["unavailableReason"]
            native_reason = model.get("unavailableReason")
            if not available and not isinstance(native_reason, dict):
                native_reason = reason("catalog.native-unavailable")
            models.append({
                "id": model_id,
                "name": _text(model.get("name"), 200) or model_id,
                "description": _text(model.get("description")),
                "contextWindow": context,
                "inputModalities": _strings(model.get("inputModalities")) or ["text"],
                "efforts": efforts,
                "available": available,
                "unavailableReason": None if available else native_reason,
                "unavailableContent": None if available else native_content,
            })
        providers.append({
            "harness": harness, "provider": provider,
            "displayName": _text(entry.get("displayName"), 200) or provider,
            "packageName": _text(entry.get("packageName"), 200),
            "packageVersion": _text(entry.get("packageVersion"), 64),
            **({"accessType": entry["accessType"]} if harness == "zcode" and
               entry.get("accessType") in ("api-key", "zhipu-coding-plan-api-key") else {}),
            "efforts": list(dict.fromkeys(effort for model in models for effort in model["efforts"])),
            "models": models,
        })
    raw_discoveries = value.get("discoveries", [{"harness": name, "status": "complete"} for name in sorted({p["harness"] for p in providers})])
    if not isinstance(raw_discoveries, list) or len(raw_discoveries) > MAX_PROVIDERS:
        raise BoardError("CATALOG_INVALID", "discoveries must be bounded per-harness observations")
    discoveries, seen = [], set()
    for result in raw_discoveries:
        if not isinstance(result, dict) or result.get("status") not in ("complete", "unknown"):
            raise BoardError("CATALOG_INVALID", "discovery status must be complete or unknown")
        name = _identity(result.get("harness"), "discovery.harness", 32)
        if name in seen:
            raise BoardError("CATALOG_INVALID", "Repeated harness observation")
        seen.add(name)
        # The native reading keeps its own status; the account-status fact is
        # normalized, never invented: a missing or malformed fact stays unknown.
        account_status = result.get("accountStatus")
        native_reason = result.get("reason")
        native_content = result.get("content")
        discoveries.append({"harness": name, "status": result["status"],
                            "accountStatus": account_status if account_status in ACCOUNT_STATUSES else "unknown",
                            "reason": native_reason if result["status"] == "unknown" else None,
                            "content": native_content})
    if any(p["harness"] not in seen for p in providers):
        raise BoardError("CATALOG_INVALID", "Every provider must have a harness observation")
    raw_warnings = value.get("warnings") or []
    if not isinstance(raw_warnings, list):
        raise BoardError("CATALOG_INVALID", "warnings must be a list")
    warnings = []
    for item in raw_warnings[:16]:
        if not isinstance(item, dict) or "reason" not in item or set(item) - {
                "reason", "harness", "content", "diagnostic"}:
            raise BoardError("CATALOG_INVALID", "Each warning must have a reason")
        warning_harness = item.get("harness")
        if warning_harness is not None:
            warning_harness = _identity(warning_harness, "warning.harness", 32)
        for key in ("content", "diagnostic"):
            if key in item and (not isinstance(item[key], str) or len(item[key]) > MAX_TEXT):
                raise BoardError("CATALOG_INVALID", f"warning.{key} must be bounded text")
        warnings.append(item)
    return {
        "source": source,
        "harnessVersion": _text(value.get("harnessVersion"), 256) or None,
        "providerVersion": _text(value.get("providerVersion"), 512) or None,
        "discoveredAt": _text(value.get("discoveredAt"), 64) or None,
        "providers": providers,
        "discoveries": discoveries,
        "warnings": warnings,
    }


def _override() -> dict | None:
    value = os.environ.get(CATALOG_FILE_ENV)
    if not value:
        return None
    path = Path(value).expanduser()
    try:
        with path.open("rb") as stream:
            raw = stream.read(MAX_CATALOG_BYTES + 1)
        if len(raw) > MAX_CATALOG_BYTES:
            raise BoardError("CATALOG_INVALID", "The catalog override exceeds the metadata size bound")
        document = json.loads(raw)
        payload = canonical_payload(document)
    except OSError as error:
        raise BoardError("CATALOG_UNAVAILABLE", "The catalog override could not be read") from error
    except ValueError as error:
        raise BoardError("CATALOG_INVALID", "The catalog override is not valid JSON") from error
    # ADR-027 governs native readings, not this operator pin: the file replaces
    # discovery entirely, so the operator vouches for the account state the way
    # a harness's confirmed discovery fact would. An explicitly declared legal
    # fact (even unknown) keeps its declared meaning.
    observations = document.get("discoveries") if isinstance(document, dict) else None
    declared = {item.get("harness"): item.get("accountStatus") for item in observations
                if isinstance(item, dict)} if isinstance(observations, list) else {}
    payload["discoveries"] = [{**entry, "accountStatus": declared[entry["harness"]]
                              if declared.get(entry["harness"]) in ACCOUNT_STATUSES else "confirmed"}
                             for entry in payload["discoveries"]]
    payload["source"] = f"file:{path}"
    return payload


def discovery_available() -> bool:
    if os.environ.get(CATALOG_FILE_ENV):
        return Path(os.environ[CATALOG_FILE_ENV]).expanduser().is_file()
    from ...buddy.harnesses.registry import adapters
    return any(item.model_discovery and item.discovery_available()[0] for item in adapters().values())


def discover(*, directory: Path, harnesses=None) -> dict:
    override = _override()
    if override is not None:
        return override
    from ...buddy.harnesses.registry import adapters
    providers, warnings, sources, versions, discoveries = [], [], [], [], []
    for name, instance in adapters().items():
        if not instance.model_discovery:
            continue
        from ...buddy.harnesses.runtime_selection import selected, bound
        selected_health = (next((item for item in harnesses if item['harness'] == name), None)
                           if harnesses is not None else selected(name))
        try:
            environment = dict(os.environ)
            with bound([selected_health], environment=environment) if selected_health else nullcontext():
                usable, _availability_detail = instance.discovery_available()
                if not usable:
                    health_reason = selected_health.get("reason") if selected_health else None
                    unavailable = health_reason or reason("harness.not-checked", operation="probe")
                    warnings.append({"harness": name, "reason": unavailable})
                    discoveries.append({"harness": name, "status": "unknown", "accountStatus": "unknown",
                                        "reason": unavailable})
                    continue
                payload = instance.discover_models()
            payload = canonical_payload(payload)
            if any(entry["harness"] != name for entry in payload["providers"]):
                raise BoardError("CATALOG_INVALID", "A harness advertised another harness identity")
        except BoardError as error:
            failure = reason("catalog.discovery-failed", harness=name, errorCode=error.code)
            warnings.append({"harness": name, "reason": failure})
            discoveries.append({"harness": name, "status": "unknown", "accountStatus": "unknown", "reason": failure})
            continue
        providers.extend(payload["providers"])
        # Preserve this native session's reading status, reason and account fact.
        native_fact = next((item for item in payload["discoveries"] if item["harness"] == name), None)
        discoveries.append({"harness": name, "status": native_fact["status"] if native_fact else "complete",
                            "accountStatus": (native_fact or {}).get("accountStatus", "unknown"),
                            **({"reason": native_fact["reason"]} if native_fact and native_fact.get("reason") is not None else {}),
                            **({"content": native_fact["content"]} if native_fact and native_fact.get("content") is not None else {})})
        warnings.extend({**item, "harness": name} for item in payload["warnings"])
        sources.append(payload["source"])
        if payload.get("harnessVersion"):
            versions.append(f"{name}:{payload['harnessVersion']}")
    return _normalized_payload({
        "source": "; ".join(sources) or "native-discovery", "harnessVersion": "; ".join(versions),
        "providers": providers, "warnings": warnings, "discoveries": discoveries,
    })


def _reject_unavailable(selected: dict, *, read_at: str | None) -> BoardError:
    details = {"buddy": selected, "reason": reason("catalog-unavailable")}
    if read_at:
        details["catalogReadAt"] = read_at
    return BoardError("BUDDY_UNAVAILABLE", "The requested native model route is not available", **details)


def validate_buddy(buddy: dict, *, directory: Path) -> dict:
    """Validate an explicit native tuple, without selection or inference.

    A buddy absent from the recorded catalog but still awaiting disappearance
    confirmation stays acceptable (ADR-027 rule 2). When the route is missing or
    unavailable, the board borrows the service's own bounded native refresh for
    that one harness and re-checks once before rejecting (rule 3); that re-read
    shares the harness's ``scan_after`` window and single flight with
    the health scan, a concurrent request boundedly waits for an in-flight read
    and re-judges on its facts, and every outcome — including a window hit or a
    join that times out — re-judges on the facts as they are then recorded, so
    a legally published catalog is never shadowed by the stale first rejection.
    """
    if not isinstance(buddy, dict) or set(buddy) != set(BUDDY_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "buddy requires exactly harness, provider, model and effort")
    selected = {key: _identity(buddy[key], key) for key in BUDDY_FIELDS}
    from ...buddy.harnesses.registry import adapter
    instance = adapter(selected["harness"])
    if not instance.model_discovery:
        raise BoardError("UNSUPPORTED_HARNESS", "This harness does not declare a model execution catalog")
    payload = _override()
    if payload is not None:
        return _check_recorded(selected, CatalogView.from_payload(payload), pending_efforts=None)
    from ..store.db import Database
    from ..service.harness_health import read_health
    from . import catalog_store

    def judge():
        """One judgment over the currently recorded catalog state."""
        with Database(directory).read() as db:
            health = read_health(db, selected["harness"])
            recorded = catalog_store.current(db)
            read_at = catalog_read_at(db, selected["harness"])
            pending_efforts = pending_model_efforts(db, selected["harness"], selected["provider"], selected["model"])
        if not health["available"]:
            raise BoardError("HARNESS_UNAVAILABLE", "The harness is unavailable", harness=health,
                             reason=health["reason"])
        if recorded is None:
            raise BoardError("CATALOG_UNAVAILABLE", "No native model catalog is recorded",
                             reason=reason("catalog-unavailable"))
        try:
            return _check_recorded(selected, CatalogView.from_payload(recorded.payload),
                                   pending_efforts=pending_efforts)
        except BoardError as error:
            if error.code != "BUDDY_UNAVAILABLE":
                raise
            raise _reject_unavailable(selected, read_at=read_at) from None

    try:
        return judge()
    except BoardError as error:
        if error.code not in ("CATALOG_UNAVAILABLE", "BUDDY_UNAVAILABLE"):
            raise
        first = error
    hook = _catalog_reread.get(str(directory))
    if hook is None:
        raise first

    def run_reread() -> None:
        try:
            hook(selected['harness'])
        except (BoardError, OSError, ValueError, sqlite3.Error):
            pass  # A failed bounded re-read leaves the rejection to speak for itself.

    coordinate_catalog_reread(directory, selected["harness"], reader=run_reread, wait=True)
    # Every outcome re-judges on the facts as they are now recorded. The open
    # window and the bounded join timeout suppress only a second native read:
    # a catalog another caller legally published after this caller's first
    # judgment is already the board's current fact, and the stale first
    # rejection must not shadow it.
    try:
        return judge()
    except BoardError as error:
        if error.code in ("CATALOG_UNAVAILABLE", "BUDDY_UNAVAILABLE", "INVALID_ARGUMENT"):
            raise
        # The re-read itself broke the harness (for example the native refresh
        # invalidated health). The fresh error and its diagnostics lead, while
        # rule 3's rejection context — catalog read time and refresh method —
        # still rides along instead of being lost at this boundary.
        raise BoardError(error.code, error.message,
                         **{**first.details, **error.details}) from error


def _check_recorded(selected: dict, view: "CatalogView", *, pending_efforts: list[str] | None) -> dict:
    model = view.lookup(selected["harness"], selected["provider"], selected["model"])
    if model is None:
        if pending_efforts:
            if selected["effort"] not in pending_efforts:
                raise BoardError("INVALID_ARGUMENT", "The requested effort is not supported by this model",
                                 legalEfforts=pending_efforts, buddy=selected)
            return selected
        raise BoardError("BUDDY_UNAVAILABLE", "The requested native model route is not available",
                         buddy=selected, reason=reason("catalog-unavailable"))
    if not model["available"]:
        raise BoardError("BUDDY_UNAVAILABLE", "The requested native model route is not available",
                         buddy=selected, reason=reason("catalog-unavailable"))
    if selected["effort"] not in model["efforts"]:
        raise BoardError("INVALID_ARGUMENT", "The requested effort is not supported by this model",
                         legalEfforts=model["efforts"], buddy=selected)
    return selected


def permission_capabilities(name: str) -> list[str]:
    """The permission facts copied into catalog rows from this source's harness."""
    from ...buddy.harnesses.registry import worker_permission_capabilities
    return sorted(worker_permission_capabilities(name))


class CatalogView:
    def __init__(self, payload: dict, *, discovery_id: str | None = None, recorded_at: str | None = None):
        self.payload, self.discovery_id, self.recorded_at = payload, discovery_id, recorded_at

    @classmethod
    def from_payload(cls, payload: Any, row: Any | None = None) -> "CatalogView":
        canonical = _normalized_payload(payload)
        return cls(canonical, discovery_id=row["discovery_id"] if row is not None else None,
                   recorded_at=row["discovered_at"] if row is not None else canonical.get("discoveredAt"))

    canonical_payload = staticmethod(canonical_payload)

    @property
    def source(self) -> str:
        return self.payload["source"]

    def lookup(self, harness: str, provider: str, model: str) -> dict | None:
        for entry in self.payload["providers"]:
            if (entry["harness"], entry["provider"]) == (harness, provider):
                return next((item for item in entry["models"] if item["id"] == model), None)
        return None

    def efforts_for(self, harness: str, provider: str, model: str) -> list[str]:
        entry = self.lookup(harness, provider, model)
        return list(entry["efforts"]) if entry else []

    def effort_ranks(self) -> dict[tuple[str, str, str], dict[str, int]]:
        """Each model's native effort order, indexed by its execution identity."""
        return {
            (entry["harness"], entry["provider"], model["id"]): {
                effort: rank for rank, effort in enumerate(model["efforts"])
            }
            for entry in self.payload["providers"]
            for model in entry["models"]
        }

    def metadata(self) -> dict:
        return {**self.payload, "discoveryId": self.discovery_id,
                "discoveredAt": self.recorded_at or self.payload.get("discoveredAt")}

    def proposed_buddies(self) -> list[dict]:
        from ...buddy.harnesses.registry import adapters
        registry = adapters()
        proposals = []
        for entry in self.payload["providers"]:
            native = registry.get(entry['harness'])
            review_eligible = native is not None and native.local_read_only_check()['eligible']
            for model in entry["models"]:
                for effort in model["efforts"]:
                    identity = ":".join((entry["harness"], entry["provider"], model["id"], effort))
                    safe_identity = re.sub(r"[^A-Za-z0-9._:-]", "-", identity)
                    buddy_id = identity if safe_identity == identity and len(identity) <= 128 else safe_identity[:95] + ":" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                    capabilities = ([f"execution:{entry['harness']}", f"effort:{effort}"]
                                    + [f"input:{item}" for item in model["inputModalities"]][:30])
                    if native is not None:
                        if review_eligible:
                            capabilities.append("decision")
                        capabilities.extend(permission_capabilities(entry["harness"]))
                    proposals.append({
                        "buddyId": buddy_id, "label": f"{model['name']} · {effort}"[:200],
                        "harness": entry["harness"], "provider": entry["provider"], "model": model["id"], "effort": effort,
                        "available": model["available"], "unavailableReason": model["unavailableReason"],
                        "unavailableContent": model["unavailableContent"], "enabled": False,
                        "capabilities": capabilities,
                        "contextWindow": model["contextWindow"],
                        "description": (model["description"] or f"{model['name']} via {entry['displayName']}")[:4000],
                        "source": f"catalog:{self.discovery_id or self.source}"[:256],
                    })
        return proposals
