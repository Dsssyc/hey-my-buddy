"""Bounded, harness-neutral model discovery and legal configuration validation.

Adapters own native discovery. This module validates public metadata, preserves
adapter/provider/model identity, and proposes disabled profiles. Discovery never
calls a model or publishes an evaluation card.
"""
from __future__ import annotations

import hashlib
from contextlib import nullcontext
import json
import os
import re
from pathlib import Path
from typing import Any

from ...errors import BoardError

MAX_MODELS = 200
MAX_PROVIDERS = 32
MAX_TEXT = 2000
MAX_CATALOG_BYTES = 2 * 1024 * 1024
CATALOG_FILE_ENV = "BUDDY_MODEL_CATALOG_FILE"
CONFIGURATION_FIELDS = ("adapter", "provider", "model", "effort")


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
        discoveries.append({"adapter": name, "status": result["status"], "reason": _text(result.get("reason")) or None})
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
        payload = canonical_payload(json.loads(raw))
    except OSError as error:
        raise BoardError("CATALOG_UNAVAILABLE", "The catalog override could not be read") from error
    except ValueError as error:
        raise BoardError("CATALOG_INVALID", "The catalog override is not valid JSON") from error
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
                    discoveries.append({"adapter": name, "status": "unknown", "reason": reason or "harness unavailable"})
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
            discoveries.append({"adapter": name, "status": "unknown", "reason": error.code})
            continue
        providers.extend(payload["providers"])
        discoveries.append({"adapter": name, "status": "complete"})
        warnings.extend(payload["warnings"])
        sources.append(payload["source"])
        if payload.get("harnessVersion"):
            versions.append(f"{name}:{payload['harnessVersion']}")
    return canonical_payload({
        "source": "; ".join(sources) or "native-discovery", "harnessVersion": "; ".join(versions),
        "providers": providers, "warnings": warnings, "discoveries": discoveries,
    })


def validate_configuration(configuration: dict, *, directory: Path) -> dict:
    """Validate an explicit native tuple, without selection, inference or state writes."""
    if not isinstance(configuration, dict) or set(configuration) != set(CONFIGURATION_FIELDS):
        raise BoardError("INVALID_ARGUMENT", "configuration requires exactly adapter, provider, model and effort")
    selected = {key: _identity(configuration[key], key) for key in CONFIGURATION_FIELDS}
    from ...buddy.harnesses.registry import adapter
    instance = adapter(selected["adapter"])
    if not instance.model_discovery:
        raise BoardError("UNSUPPORTED_ADAPTER", "This adapter does not declare a model execution catalog")
    payload = _override()
    if payload is None:
        from ..store.db import Database
        from ..service.harness_health import read_health
        from . import catalog_store
        with Database(directory).read() as db:
            health = read_health(db, selected['adapter'])
            if not health['available']:
                raise BoardError('ADAPTER_UNAVAILABLE', health.get('remedy') or 'Run buddy adapters with refresh:true', harness=health)
            recorded = catalog_store.current(db)
        if recorded is None:
            raise BoardError('CATALOG_UNAVAILABLE', 'No native model catalog is recorded; run buddy adapters with refresh:true')
        payload = recorded.payload
    view = CatalogView.from_payload(payload)
    model = view.lookup(selected["adapter"], selected["provider"], selected["model"])
    if model is None or not model["available"]:
        raise BoardError("CONFIGURATION_UNAVAILABLE", "The requested native model route is not available", configuration=selected)
    if selected["effort"] not in model["efforts"]:
        raise BoardError("INVALID_ARGUMENT", "The requested effort is not supported by this model", legalEfforts=model["efforts"], configuration=selected)
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
