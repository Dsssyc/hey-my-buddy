"""Bounded, harness-neutral model discovery and legal configuration validation.

Adapters own native discovery. This module validates public metadata, preserves
adapter/provider/model identity, and proposes disabled profiles. Discovery never
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
from pathlib import Path
from typing import Any, Callable

from ...errors import BoardError

MAX_MODELS = 200
MAX_PROVIDERS = 32
MAX_TEXT = 2000
MAX_CATALOG_BYTES = 2 * 1024 * 1024
CATALOG_FILE_ENV = "BUDDY_MODEL_CATALOG_FILE"
CONFIGURATION_FIELDS = ("adapter", "provider", "model", "effort")

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
CONFIRMED_ABSENCE_REASON = "not present in the latest confirmed native discovery"

#: The unavailable reason of one retired effort whose model remains in the reading.
RETIRED_EFFORT_REASON = "not present in the latest complete native discovery"

#: Unavailable reasons that are catalog facts, never undone by health recovery.
#: A 0 left behind by a harness health failure is a health fact, not evidence
#: that the native model disappeared (ADR-027 rules 1 and 2).
CATALOG_FACT_REASONS = (CONFIRMED_ABSENCE_REASON, RETIRED_EFFORT_REASON)

#: ADR-027 rule 3: how an operator refreshes harness catalogs and their readings.
CATALOG_REMEDY = "Run buddy adapters with refresh:true"

_PENDING_PREFIX = "catalog-pending:"
_READ_AT_PREFIX = "catalog-read-at:"
_ACCOUNT_STATUS_PREFIX = "catalog-account-status:"

#: ADR-027 rule 3: per-directory hooks the service registers so an explicitly
#: rejected configuration can borrow the service's own bounded native refresh
#: (its environment, account binding and subprocess deadlines) exactly once.
_catalog_reread: dict[str, Callable[[str], None]] = {}


def register_catalog_reread(directory: Path | str, hook: Callable[[str], None] | None) -> None:
    """Register (or clear) this state directory's single-harness catalog re-read."""
    if hook is None:
        _catalog_reread.pop(str(directory), None)
    else:
        _catalog_reread[str(directory)] = hook


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.astimezone(timezone.utc) if stamp.tzinfo else None
    except ValueError:
        return None


def _family_key(provider: str, model: str) -> str:
    return provider + "\x1f" + model


def _pending_map(db, adapter: str) -> dict:
    row = db.execute("SELECT value FROM meta WHERE key=?", (_PENDING_PREFIX + adapter,)).fetchone()
    if row is None:
        return {}
    try:
        saved = json.loads(row[0])
    except ValueError:
        return {}
    return saved if isinstance(saved, dict) else {}


def _pending_entry(value: Any) -> dict:
    """One pending entry: the first absence plus the last legal efforts.

    The legal efforts are the ones the last trusted reading still declared when
    the model first went absent; a later health reason is never native metadata
    (ADR-027 rule 2). A legacy plain timestamp keeps its time, without efforts.
    """
    if isinstance(value, dict):
        efforts = value.get("efforts")
        return {"since": value.get("since") if isinstance(value.get("since"), str) else None,
                "efforts": sorted({item for item in efforts if isinstance(item, str)}) if isinstance(efforts, list) else None}
    if isinstance(value, str):
        return {"since": value, "efforts": None}
    return {"since": None, "efforts": None}


def _write_pending_map(db, adapter: str, pending: dict) -> None:
    from ...json_codec import canonical_json

    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (_PENDING_PREFIX + adapter, canonical_json(pending)))


def pending_families(db) -> dict[tuple[str, str, str], str]:
    """Model families awaiting disappearance confirmation, with their first absence.

    ADR-027 rule 2 keys the pending state by adapter/provider/model identity, so
    an effort-only change is never a model disappearance.
    """
    families: dict[tuple[str, str, str], str] = {}
    for row in db.execute("SELECT key FROM meta WHERE key LIKE ?", (_PENDING_PREFIX + "%",)):
        adapter = row["key"][len(_PENDING_PREFIX):]
        for name, value in _pending_map(db, adapter).items():
            provider, _, model = name.partition("\x1f")
            since = _pending_entry(value)["since"]
            if since:
                families[(adapter, provider, model)] = since
    return families


def pending_model_efforts(db, adapter: str, provider: str, model: str) -> list[str] | None:
    """Legal efforts retained for a pending model family, or None when not pending.

    The recorded last legal efforts of the window lead; the retained profiles
    are only a defensive fallback, so explicit validation keeps working through
    health noise without adopting a health reason as native metadata.
    """
    value = _pending_map(db, adapter).get(_family_key(provider, model))
    if value is None:
        return None
    efforts = _pending_entry(value)["efforts"]
    if efforts:
        return list(efforts)
    rows = db.execute(
        "SELECT effort FROM evaluation_profiles WHERE adapter=? AND provider=? AND model=?"
        " AND available=1 ORDER BY effort",
        (adapter, provider, model)).fetchall()
    return [row["effort"] for row in rows] or None


def catalog_read_at(db, adapter: str) -> str | None:
    """When this harness catalog was last confirmed (trusted) reading, if ever.

    ADR-027 legacy boards recorded trusted catalogs before the confirmed-read
    keys existed. Their read time comes from the existing catalog record: the
    row's own time while the adopted reading is current, otherwise the time of
    the discovery it still retains. An unknown reading never masquerades as a
    successful one (it keeps the retained discovery and its time), a switched
    account binding never inherits the old account's time, and a board without
    an adopted discovery stays without a read time (cold start).
    """
    row = db.execute("SELECT value FROM meta WHERE key=?", (_READ_AT_PREFIX + adapter,)).fetchone()
    if row is not None:
        return row[0]
    current = db.execute(
        "SELECT c.status, c.updated_at, c.discovery_id, d.discovered_at FROM catalog_current c"
        " LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.adapter=?",
        (adapter,)).fetchone()
    if current is None or current["discovery_id"] is None or current["discovered_at"] is None:
        return None
    from .accounts import identity, selection
    saved = db.execute("SELECT value FROM meta WHERE key=?", ("catalog-account:" + adapter,)).fetchone()
    account = json.loads(saved[0]) if saved else {"source": "native", "credentialRevision": 0}
    if identity(account) != identity(selection(db, adapter)):
        return None
    return current["updated_at"] if current["status"] == "complete" else current["discovered_at"]


def identities_from_payload(payload_json: Any, adapter: str) -> set[tuple[str, str, str]]:
    """Legal adapter/provider/model/effort identities a retained catalog lists.

    A model the retained reading itself declared unavailable contributes none
    of its efforts: that unavailability is a native fact, not a health artifact
    (ADR-027 rule 1).
    """
    identities: set[tuple[str, str, str]] = set()
    if not isinstance(payload_json, str) or not payload_json:
        return identities
    try:
        providers = json.loads(payload_json).get("providers")
    except ValueError:
        return identities
    if not isinstance(providers, list):
        return identities
    for entry in providers:
        if not isinstance(entry, dict) or entry.get("adapter") != adapter:
            continue
        for model in entry.get("models") or []:
            if isinstance(model, dict) and model.get("available", True):
                for effort in model.get("efforts") or []:
                    if isinstance(effort, str) and effort:
                        identities.add((str(entry.get("provider")), str(model.get("id")), effort))
    return identities


def families_from_payload(payload_json: Any, adapter: str) -> set[tuple[str, str]]:
    """Model families a retained confirmed catalog still lists as available."""
    return {(provider, model) for provider, model, _effort in identities_from_payload(payload_json, adapter)}


def restore_retained_availability(db, adapter: str) -> int:
    """Restore the model availability of the retained catalog after health noise.

    ADR-027 separates health availability from adopted catalog facts, at the
    effort level: the 0s a harness health failure left on ``evaluation_profiles``
    are never evidence that a native configuration disappeared. A row returns
    only when the retained confirmed catalog still lists its exact
    adapter/provider/model/effort identity, or when it is one of the last legal
    efforts recorded for a family still awaiting disappearance confirmation. A
    confirmed absence, a retired effort and a native unavailable declaration are
    catalog facts and are never restored here.
    """
    row = db.execute(
        "SELECT d.payload_json FROM catalog_current c"
        " LEFT JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.adapter=?",
        (adapter,)).fetchone()
    identities = identities_from_payload(row[0] if row is not None else None, adapter)
    pending_efforts = {}
    for key, value in _pending_map(db, adapter).items():
        provider, _, model = key.partition("\x1f")
        pending_efforts[(provider, model)] = _pending_entry(value)["efforts"]
    restored = 0
    for profile in db.execute(
            "SELECT profile_id, provider, model, effort, available, unavailable_reason"
            " FROM evaluation_profiles WHERE adapter=?",
            (adapter,)).fetchall():
        if profile["available"] or profile["unavailable_reason"] in CATALOG_FACT_REASONS:
            continue
        family = (profile["provider"], profile["model"])
        identity = (*family, profile["effort"])
        legal_pending = pending_efforts.get(family)
        if identity in identities or (family in pending_efforts and (legal_pending is None or profile["effort"] in legal_pending)):
            db.execute("UPDATE evaluation_profiles SET available=1,unavailable_reason=NULL WHERE profile_id=?",
                       (profile["profile_id"],))
            restored += 1
    return restored


def catalog_account_status(db, adapter: str) -> str:
    """The account-status fact of the last trusted reading; unknown without one."""
    row = db.execute("SELECT value FROM meta WHERE key=?", (_ACCOUNT_STATUS_PREFIX + adapter,)).fetchone()
    return row[0] if row is not None and row[0] in ACCOUNT_STATUSES else "unknown"


def note_confirmed_read(db, adapter: str, *, now: str, account_status: str) -> None:
    """Persist the fact that a trusted reading was applied for this harness."""
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (_READ_AT_PREFIX + adapter, now))
    db.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (_ACCOUNT_STATUS_PREFIX + adapter, account_status if account_status in ACCOUNT_STATUSES else "unknown"))


def clear_confirmed_read(db, adapter: str) -> None:
    """Drop the confirmed-read and pending facts of a replaced account binding."""
    _write_pending_map(db, adapter, {})
    db.execute("DELETE FROM meta WHERE key IN (?,?)", (_READ_AT_PREFIX + adapter, _ACCOUNT_STATUS_PREFIX + adapter))


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


def canonical_payload(value: Any) -> dict:
    """Keep only bounded metadata; never persist unknown native settings/secrets."""
    if not isinstance(value, dict):
        raise BoardError("CATALOG_INVALID", "The discovered catalog must be a JSON object")
    source = _identity(value.get("source"), "source", 1024)
    raw_providers = value.get("providers")
    if not isinstance(raw_providers, list) or len(raw_providers) > MAX_PROVIDERS:
        raise BoardError("CATALOG_INVALID", f"A catalog may contain at most {MAX_PROVIDERS} providers")
    providers, seen_providers = [], set()
    model_count = 0
    for entry in raw_providers:
        if not isinstance(entry, dict):
            raise BoardError("CATALOG_INVALID", "Each provider must be an object")
        adapter = _identity(entry.get("adapter"), "adapter", 32)
        provider = _identity(entry.get("provider"), "provider")
        if (adapter, provider) in seen_providers:
            raise BoardError("CATALOG_INVALID", f"Repeated catalog provider {adapter}/{provider}")
        seen_providers.add((adapter, provider))
        provider_efforts = _strings(entry.get("efforts"))
        raw_models = entry.get("models")
        if not isinstance(raw_models, list):
            raise BoardError("CATALOG_INVALID", f"{adapter}/{provider} models must be a list")
        model_count += len(raw_models)
        if model_count > MAX_MODELS:
            raise BoardError("CATALOG_INVALID", f"A catalog may contain at most {MAX_MODELS} models")
        models, seen_models = [], set()
        for model in raw_models:
            if not isinstance(model, dict):
                raise BoardError("CATALOG_INVALID", "Each model must be an object")
            model_id = _identity(model.get("id"), "model.id")
            if model_id in seen_models:
                raise BoardError("CATALOG_INVALID", f"Repeated model {adapter}/{provider}/{model_id}")
            seen_models.add(model_id)
            efforts = _strings(model.get("efforts")) if "efforts" in model else provider_efforts
            if not efforts:
                raise BoardError("CATALOG_INVALID", f"No legal efforts declared for {adapter}/{provider}/{model_id}")
            context = model.get("contextWindow")
            if isinstance(context, bool) or not isinstance(context, int) or context <= 0:
                context = None
            available = model.get("available", True) is True
            models.append({
                "id": model_id,
                "name": _text(model.get("name"), 200) or model_id,
                "description": _text(model.get("description")),
                "contextWindow": context,
                "inputModalities": _strings(model.get("inputModalities")) or ["text"],
                "efforts": efforts,
                "available": available,
                "unavailableReason": None if available else _text(model.get("unavailableReason")) or "native route is unavailable",
            })
        providers.append({
            "adapter": adapter, "provider": provider,
            "displayName": _text(entry.get("displayName"), 200) or provider,
            "packageName": _text(entry.get("packageName"), 200),
            "packageVersion": _text(entry.get("packageVersion"), 64),
            **({"accessType": entry["accessType"]} if adapter == "zcode" and
               entry.get("accessType") in ("api-key", "zhipu-coding-plan-api-key", "zhipu-account") else {}),
            "efforts": list(dict.fromkeys(effort for model in models for effort in model["efforts"])),
            "models": models,
        })
    raw_discoveries = value.get("discoveries", [{"adapter": name, "status": "complete"} for name in sorted({p["adapter"] for p in providers})])
    if not isinstance(raw_discoveries, list) or len(raw_discoveries) > MAX_PROVIDERS:
        raise BoardError("CATALOG_INVALID", "discoveries must be bounded per-harness observations")
    discoveries, seen = [], set()
    for result in raw_discoveries:
        if not isinstance(result, dict) or result.get("status") not in ("complete", "unknown"):
            raise BoardError("CATALOG_INVALID", "discovery status must be complete or unknown")
        name = _identity(result.get("adapter"), "discovery.adapter", 32)
        if name in seen:
            raise BoardError("CATALOG_INVALID", "Repeated harness observation")
        seen.add(name)
        # The native reading keeps its own status; the account-status fact is
        # normalized, never invented: a missing or malformed fact stays unknown.
        account_status = result.get("accountStatus")
        discoveries.append({"adapter": name, "status": result["status"],
                            "accountStatus": account_status if account_status in ACCOUNT_STATUSES else "unknown",
                            "reason": _text(result.get("reason")) or None})
    if any(p["adapter"] not in seen for p in providers):
        raise BoardError("CATALOG_INVALID", "Every provider must have a harness observation")
    return {
        "source": source,
        "harnessVersion": _text(value.get("harnessVersion"), 256) or None,
        "providerVersion": _text(value.get("providerVersion"), 512) or None,
        "discoveredAt": _text(value.get("discoveredAt"), 64) or None,
        "providers": providers,
        "discoveries": discoveries,
        "warnings": [_text(item) for item in (value.get("warnings") or []) if isinstance(item, str)][:16],
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
    declared = {item.get("adapter"): item.get("accountStatus") for item in observations
                if isinstance(item, dict)} if isinstance(observations, list) else {}
    payload["discoveries"] = [{**entry, "accountStatus": declared[entry["adapter"]]
                              if declared.get(entry["adapter"]) in ACCOUNT_STATUSES else "confirmed"}
                             for entry in payload["discoveries"]]
    payload["source"] = f"file:{path}"
    return payload


def discovery_available() -> bool:
    if os.environ.get(CATALOG_FILE_ENV):
        return Path(os.environ[CATALOG_FILE_ENV]).expanduser().is_file()
    from ...buddy.harnesses.registry import adapters
    return any(item.model_discovery and item.discovery_available()[0] for item in adapters().values())


def discover(*, directory=None, database=None) -> dict:
    override = _override()
    if override is not None:
        return override
    from ...buddy.harnesses.registry import adapters
    providers, warnings, sources, versions, discoveries = [], [], [], [], []
    for name, instance in adapters().items():
        if not instance.model_discovery:
            continue
        from ...buddy.harnesses.runtime_selection import selected, bound
        selected_health = selected(name)
        try:
            environment = dict(os.environ)
            if selected_health and selected_health.get('account'):
                from .accounts import execution_environment
                state = directory or environment.get('BUDDY_STATE_DIR')
                if not state and selected_health['account']['source'] == 'worker':
                    raise BoardError('ACCOUNT_CAPABILITY_UNVERIFIED', 'Account model discovery requires the private state root')
                environment = execution_environment(state, selected_health['account'], environment, purpose='catalog')
            with bound([selected_health], environment=environment) if selected_health else nullcontext():
                usable, reason = instance.discovery_available()
                if not usable:
                    warnings.append(f"{name}: {reason or 'harness unavailable'}")
                    discoveries.append({"adapter": name, "status": "unknown", "accountStatus": "unknown",
                                        "reason": reason or "harness unavailable"})
                    continue
                from .accounts import native_operation
                with native_operation(database, selected_health['account'], 'catalog') if database and selected_health and selected_health.get('account') else nullcontext() as stop:
                    payload = canonical_payload(instance.discover_models())
                    if stop is not None:
                        stop['shutdownConfirmed'] = True
            if any(entry["adapter"] != name for entry in payload["providers"]):
                raise BoardError("CATALOG_INVALID", "A harness advertised another adapter identity")
        except BoardError as error:
            warnings.append(f"{name}: {error.code}")
            discoveries.append({"adapter": name, "status": "unknown", "accountStatus": "unknown", "reason": error.code})
            continue
        providers.extend(payload["providers"])
        # The harness's own observation carries the account-status fact of this
        # native session; the aggregate never hardcodes it over that fact.
        native_fact = next((item for item in payload["discoveries"] if item["adapter"] == name), None)
        discoveries.append({"adapter": name, "status": "complete",
                            "accountStatus": (native_fact or {}).get("accountStatus", "unknown")})
        warnings.extend(payload["warnings"])
        sources.append(payload["source"])
        if payload.get("harnessVersion"):
            versions.append(f"{name}:{payload['harnessVersion']}")
    return canonical_payload({
        "source": "; ".join(sources) or "native-discovery", "harnessVersion": "; ".join(versions),
        "providers": providers, "warnings": warnings, "discoveries": discoveries,
    })


def _reject_unavailable(selected: dict, *, read_at: str | None, remedy: str) -> BoardError:
    details = {"configuration": selected, "remedy": remedy}
    if read_at:
        details["catalogReadAt"] = read_at
    return BoardError("CONFIGURATION_UNAVAILABLE", "The requested native model route is not available", **details)


def validate_configuration(configuration: dict, *, directory: Path) -> dict:
    """Validate an explicit native tuple, without selection or inference.

    A buddy absent from the recorded catalog but still awaiting disappearance
    confirmation stays acceptable (ADR-027 rule 2). When the route is missing or
    unavailable, the board borrows the service's own bounded native refresh for
    that one harness and re-checks once before rejecting (rule 3).
    """
    if not isinstance(configuration, dict) or set(configuration) != set(CONFIGURATION_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "configuration requires exactly adapter, provider, model and effort")
    selected = {key: _identity(configuration[key], key) for key in CONFIGURATION_FIELDS}
    from ...buddy.harnesses.registry import adapter
    instance = adapter(selected["adapter"])
    if not instance.model_discovery:
        raise BoardError("UNSUPPORTED_ADAPTER", "This adapter does not declare a model execution catalog")
    payload = _override()
    if payload is not None:
        return _check_recorded(selected, CatalogView.from_payload(payload), pending_efforts=None)
    from ..store.db import Database
    from ..service.harness_health import read_health
    from . import catalog_store

    def judge():
        """One judgment over the currently recorded catalog state."""
        with Database(directory).read() as db:
            health = read_health(db, selected["adapter"])
            recorded = catalog_store.current(db)
            read_at = catalog_read_at(db, selected["adapter"])
            pending_efforts = pending_model_efforts(db, selected["adapter"], selected["provider"], selected["model"])
        if not health["available"]:
            raise BoardError("ADAPTER_UNAVAILABLE", health.get("remedy") or CATALOG_REMEDY, harness=health)
        if recorded is None:
            raise BoardError("CATALOG_UNAVAILABLE",
                             f"No native model catalog is recorded; {CATALOG_REMEDY}", remedy=CATALOG_REMEDY)
        try:
            return _check_recorded(selected, CatalogView.from_payload(recorded.payload),
                                   pending_efforts=pending_efforts)
        except BoardError as error:
            if error.code != "CONFIGURATION_UNAVAILABLE":
                raise
            raise _reject_unavailable(selected, read_at=read_at, remedy=CATALOG_REMEDY) from None

    try:
        return judge()
    except BoardError as error:
        if error.code not in ("CATALOG_UNAVAILABLE", "CONFIGURATION_UNAVAILABLE"):
            raise
        first = error
    hook = _catalog_reread.get(str(directory))
    if hook is None:
        raise first
    try:
        # ADR-027 rule 3: one bounded re-read of exactly this harness, borrowing
        # the service's own refresh (environment, account binding, deadlines).
        hook(selected["adapter"])
    except (BoardError, OSError, ValueError, sqlite3.Error):
        pass  # A failed bounded re-read leaves the rejection to speak for itself.
    try:
        return judge()
    except BoardError as error:
        if error.code in ("CATALOG_UNAVAILABLE", "CONFIGURATION_UNAVAILABLE", "INVALID_ARGUMENT"):
            raise
        # The re-read itself broke the harness (for example the native refresh
        # invalidated health). The fresh error and its diagnostics lead, while
        # rule 3's rejection context — catalog read time and refresh method —
        # still rides along instead of being lost at this boundary.
        raise BoardError(error.code, error.message,
                         **{**first.details, **error.details}) from error


def _check_recorded(selected: dict, view: "CatalogView", *, pending_efforts: list[str] | None) -> dict:
    model = view.lookup(selected["adapter"], selected["provider"], selected["model"])
    if model is None:
        if pending_efforts:
            if selected["effort"] not in pending_efforts:
                raise BoardError("INVALID_ARGUMENT", "The requested effort is not supported by this model",
                                 legalEfforts=pending_efforts, configuration=selected)
            return selected
        raise BoardError("CONFIGURATION_UNAVAILABLE", "The requested native model route is not available",
                         configuration=selected)
    if not model["available"]:
        raise BoardError("CONFIGURATION_UNAVAILABLE", "The requested native model route is not available",
                         configuration=selected)
    if selected["effort"] not in model["efforts"]:
        raise BoardError("INVALID_ARGUMENT", "The requested effort is not supported by this model",
                         legalEfforts=model["efforts"], configuration=selected)
    return selected


class CatalogView:
    def __init__(self, payload: dict, *, discovery_id: str | None = None, recorded_at: str | None = None):
        self.payload, self.discovery_id, self.recorded_at = payload, discovery_id, recorded_at

    @classmethod
    def from_payload(cls, payload: Any, row: Any | None = None) -> "CatalogView":
        canonical = canonical_payload(payload)
        return cls(canonical, discovery_id=row["discovery_id"] if row is not None else None,
                   recorded_at=row["discovered_at"] if row is not None else canonical.get("discoveredAt"))

    canonical_payload = staticmethod(canonical_payload)

    @property
    def source(self) -> str:
        return self.payload["source"]

    def lookup(self, adapter: str, provider: str, model: str) -> dict | None:
        for entry in self.payload["providers"]:
            if (entry["adapter"], entry["provider"]) == (adapter, provider):
                return next((item for item in entry["models"] if item["id"] == model), None)
        return None

    def efforts_for(self, adapter: str, provider: str, model: str) -> list[str]:
        entry = self.lookup(adapter, provider, model)
        return list(entry["efforts"]) if entry else []

    def metadata(self) -> dict:
        return {**self.payload, "discoveryId": self.discovery_id,
                "discoveredAt": self.recorded_at or self.payload.get("discoveredAt")}

    def proposed_profiles(self) -> list[dict]:
        from ...buddy.harnesses.registry import adapters
        registry = adapters()
        proposals = []
        for entry in self.payload["providers"]:
            native = registry.get(entry['adapter'])
            review_eligible = native is not None and native.local_read_only_check()['eligible']
            for model in entry["models"]:
                for effort in model["efforts"]:
                    identity = ":".join((entry["adapter"], entry["provider"], model["id"], effort))
                    safe_identity = re.sub(r"[^A-Za-z0-9._:-]", "-", identity)
                    profile_id = identity if safe_identity == identity and len(identity) <= 128 else safe_identity[:95] + ":" + hashlib.sha256(identity.encode()).hexdigest()[:32]
                    proposals.append({
                        "profileId": profile_id, "label": f"{model['name']} · {effort}"[:200],
                        "adapter": entry["adapter"], "provider": entry["provider"], "model": model["id"], "effort": effort,
                        "available": model["available"], "unavailableReason": model["unavailableReason"], "enabled": False,
                        "capabilities": [f"execution:{entry['adapter']}", f"effort:{effort}"] + [f"input:{item}" for item in model["inputModalities"]][:30] + (["decision"] if review_eligible else []),
                        "contextWindow": model["contextWindow"],
                        "description": (model["description"] or f"{model['name']} via {entry['displayName']}")[:4000],
                        "source": f"catalog:{self.discovery_id or self.source}"[:256],
                    })
        return proposals
