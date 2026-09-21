"""Installed-harness model catalog discovery and the validated catalog view.

Discovery is explicit and read-only: it runs the Node helper beside this package,
which reads the *installed* DSH provider artifact (its exported configuration schema
and declared provider route), and returns bounded metadata. It never runs a model,
never reads user settings and never emits a credential value.

When the helper or the harness is unavailable the discovery call fails honestly and
leaves the published table untouched. Discovered profiles are proposals: publishing
them goes through the same durable writer gate as any other edit.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .errors import BoardError

DISCOVERY_TIMEOUT_SECONDS = 60
MAX_MODELS = 200
MAX_PROVIDERS = 16
MAX_TEXT = 2000
CATALOG_FILE_ENV = "BUDDY_MODEL_CATALOG_FILE"


def helper_path() -> Path:
    """The discovery helper shipped beside the runtime scripts."""
    return Path(__file__).resolve().parents[2] / "scripts" / "model-catalog.mjs"


def node_binary() -> str | None:
    return os.environ.get("BUDDY_NODE") or shutil.which("node")


def helper_available() -> bool:
    """True when the discovery route exists in this build; not a discovery result."""
    override = os.environ.get(CATALOG_FILE_ENV)
    if override:
        return Path(override).expanduser().is_file()
    return helper_path().is_file() and node_binary() is not None


def _bounded(value: Any, limit: int = MAX_TEXT) -> str:
    return value if isinstance(value, str) else ""


def canonical_payload(value: Any) -> dict:
    """Validate and normalize one discovery document into the persisted shape.

    Everything outside the known metadata fields is dropped, so an unexpected
    credential-looking field in a hand-written override can never be persisted or
    served back through the snapshot.
    """
    if not isinstance(value, dict):
        raise BoardError("CATALOG_INVALID", "The discovered catalog must be a JSON object")
    source = _bounded(value.get("source"), 256)
    if not source.strip():
        raise BoardError("CATALOG_INVALID", "The discovered catalog has no source attribution")
    raw_providers = value.get("providers")
    if not isinstance(raw_providers, list) or not raw_providers:
        raise BoardError("CATALOG_INVALID", "The discovered catalog carries no provider")
    if len(raw_providers) > MAX_PROVIDERS:
        raise BoardError("CATALOG_INVALID", f"The discovered catalog carries more than {MAX_PROVIDERS} providers")
    providers: list[dict] = []
    for index, entry in enumerate(raw_providers):
        if not isinstance(entry, dict):
            raise BoardError("CATALOG_INVALID", f"providers[{index}] must be an object")
        provider = _bounded(entry.get("provider"), 128).strip()
        if not provider:
            raise BoardError("CATALOG_INVALID", f"providers[{index}] has no provider route name")
        adapter = _bounded(entry.get("adapter"), 32).strip() or "dsh"
        efforts = [item for item in (entry.get("efforts") or []) if isinstance(item, str) and item.strip()]
        raw_models = entry.get("models")
        if not isinstance(raw_models, list) or not raw_models:
            raise BoardError("CATALOG_INVALID", f"providers[{index}] advertises no model")
        models: list[dict] = []
        seen: set[str] = set()
        for model_index, model in enumerate(raw_models[:MAX_MODELS]):
            if not isinstance(model, dict):
                raise BoardError("CATALOG_INVALID", f"providers[{index}].models[{model_index}] must be an object")
            model_id = _bounded(model.get("id"), 200).strip()
            if not model_id or model_id in seen:
                raise BoardError("CATALOG_INVALID", f"providers[{index}].models[{model_index}] has a missing or repeated id")
            seen.add(model_id)
            context_window = model.get("contextWindow")
            if context_window is not None and (
                isinstance(context_window, bool) or not isinstance(context_window, int) or context_window <= 0
            ):
                context_window = None
            modalities = [item for item in (model.get("inputModalities") or []) if isinstance(item, str)]
            models.append(
                {
                    "id": model_id,
                    "name": _bounded(model.get("name"), 200) or model_id,
                    "description": _bounded(model.get("description"), MAX_TEXT),
                    "contextWindow": context_window,
                    "inputModalities": modalities or ["text"],
                }
            )
        providers.append(
            {
                "provider": provider,
                "displayName": _bounded(entry.get("displayName"), 200) or provider,
                "packageName": _bounded(entry.get("packageName"), 200),
                "packageVersion": _bounded(entry.get("packageVersion"), 64),
                "adapter": adapter,
                "efforts": efforts,
                "models": models,
            }
        )
    return {
        "source": source,
        "harnessVersion": _bounded(value.get("harnessVersion"), 64) or None,
        "providerVersion": _bounded(value.get("providerVersion"), 512) or None,
        "discoveredAt": _bounded(value.get("discoveredAt"), 64) or None,
        "providers": providers,
        "warnings": [item for item in (value.get("warnings") or []) if isinstance(item, str)][:16],
    }


def discover() -> dict:
    """Read the installed harness catalog. No model call; no state is written here."""
    override = os.environ.get(CATALOG_FILE_ENV)
    if override:
        path = Path(override).expanduser()
        try:
            value = json.loads(path.read_text())
        except OSError as error:
            raise BoardError(
                "CATALOG_UNAVAILABLE", f"The catalog override {path} could not be read: {error.strerror or error}"
            ) from error
        except ValueError as error:
            raise BoardError("CATALOG_UNAVAILABLE", f"The catalog override {path} is not valid JSON") from error
        payload = canonical_payload(value)
        payload["source"] = f"file:{path}"
        return payload

    script = helper_path()
    node = node_binary()
    if not script.is_file() or node is None:
        raise BoardError(
            "CATALOG_UNAVAILABLE",
            "Installed-harness discovery is not available in this build: the discovery helper or Node.js is missing. "
            "The published table was not changed.",
        )
    try:
        completed = subprocess.run(
            [node, str(script)],
            capture_output=True,
            text=True,
            timeout=DISCOVERY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise BoardError(
            "CATALOG_UNAVAILABLE",
            f"Installed-harness discovery did not complete: {type(error).__name__}. The published table was not changed.",
        ) from error
    stdout = completed.stdout.strip()
    if completed.returncode != 0 or not stdout:
        detail = ""
        try:
            parsed = json.loads(stdout)
            detail = parsed.get("error", {}).get("message", "")
        except ValueError:
            detail = ""
        if not detail:
            detail = (completed.stderr or "").strip().splitlines()[-1] if completed.stderr.strip() else "no diagnostic"
        raise BoardError(
            "CATALOG_UNAVAILABLE",
            f"Installed-harness discovery failed: {detail[:400]}. The published table was not changed.",
        )
    try:
        value = json.loads(stdout)
    except ValueError as error:
        raise BoardError(
            "CATALOG_UNAVAILABLE", "The discovery helper returned a non-JSON document; the published table was not changed"
        ) from error
    return canonical_payload(value)


class CatalogView:
    """A validated view over one recorded discovery."""

    def __init__(self, payload: dict, *, discovery_id: str | None = None, recorded_at: str | None = None):
        self.payload = payload
        self.discovery_id = discovery_id
        self.recorded_at = recorded_at

    @classmethod
    def from_payload(cls, payload: Any, row: Any | None = None) -> "CatalogView":
        canonical = canonical_payload(payload)
        return cls(
            canonical,
            discovery_id=(row["discovery_id"] if row is not None else None),
            recorded_at=(row["discovered_at"] if row is not None else canonical.get("discoveredAt")),
        )

    @staticmethod
    def canonical_payload(value: Any) -> dict:
        return canonical_payload(value)

    @property
    def source(self) -> str:
        return self.payload["source"]

    def _provider(self, provider: str) -> dict | None:
        for entry in self.payload["providers"]:
            if entry["provider"] == provider:
                return entry
        return None

    def efforts_for(self, provider: str) -> list[str]:
        entry = self._provider(provider)
        return list(entry["efforts"]) if entry else []

    def lookup(self, provider: str, model: str) -> dict | None:
        entry = self._provider(provider)
        if entry is None:
            return None
        for candidate in entry["models"]:
            if candidate["id"] == model:
                return candidate
        return None

    def metadata(self) -> dict:
        return {
            "discoveryId": self.discovery_id,
            "source": self.payload["source"],
            "harnessVersion": self.payload.get("harnessVersion"),
            "providerVersion": self.payload.get("providerVersion"),
            "discoveredAt": self.recorded_at or self.payload.get("discoveredAt"),
            "providers": [
                {
                    "provider": entry["provider"],
                    "displayName": entry["displayName"],
                    "adapter": entry["adapter"],
                    "packageName": entry["packageName"],
                    "packageVersion": entry["packageVersion"],
                    "efforts": list(entry["efforts"]),
                    "models": [dict(model) for model in entry["models"]],
                }
                for entry in self.payload["providers"]
            ],
            "warnings": list(self.payload.get("warnings", [])),
        }

    def proposed_profiles(self) -> list[dict]:
        """Turn the discovery into proposed profile data. Proposals are not published."""
        attribution = f"catalog:{self.discovery_id or self.payload['source']}"
        proposals: list[dict] = []
        for entry in self.payload["providers"]:
            efforts = entry["efforts"] or [""]
            for model in entry["models"]:
                for effort in efforts:
                    profile_id = ":".join(
                        part for part in ("dsh", entry["provider"], model["id"], effort) if part != ""
                    )
                    capabilities = [f"execution:{entry['adapter']}"]
                    if effort:
                        capabilities.append(f"effort:{effort}")
                    capabilities.extend(f"input:{item}" for item in model["inputModalities"])
                    description = model["description"] or f"{model['name']} via {entry['displayName']}"
                    proposals.append(
                        {
                            "profileId": profile_id[:128],
                            "label": (f"{model['name']} · {effort}" if effort else model["name"])[:200],
                            "adapter": "dsh",
                            "provider": entry["provider"],
                            "model": model["id"],
                            "effort": effort or "off",
                            "available": True,
                            "enabled": False,
                            "capabilities": capabilities[:32],
                            "contextWindow": model["contextWindow"],
                            "description": description[:4000],
                            "source": attribution[:256],
                        }
                    )
        return proposals


__all__ = ["CatalogView", "canonical_payload", "discover", "helper_available", "helper_path", "node_binary"]
