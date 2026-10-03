"""Public model facts snapshots from models.dev (ADR-021 decision 12).

Facts such as price, billing unit, context length, release date and machine-readable
capabilities belong to the model, never to a buddy, and are program-owned: the Router
never writes them. This module fetches one public machine-readable snapshot, resolves
the explicit provider/model identity mapping, and stores a dated snapshot per model
family in the ``model_facts`` table.

The fetch is triggered only by a user action — enabling a model through the console,
and later the model profile plane building a profile — and never on a schedule: there
is no background or periodic network access. Requests carry no credentials of any
kind. A failed fetch keeps the previous snapshot untouched; a completed fetch with no
entry (or no resolvable identity) records ``unknown`` so a reader can display it.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.request
from pathlib import Path

from ..store.db import canonical_json, sha256_text, utc_now
from ...errors import BoardError

#: The one public machine-readable source. Facts record this origin with their fetch
#: date; no other source, and no local note or evaluation, ever counts as a fact.
SOURCE_URL = "https://models.dev/api.json"
#: Offline fixture hook, the same pattern as ``BUDDY_MODEL_CATALOG_FILE``: when set,
#: the source is read from this file instead of the network. Test harnesses pin it so
#: the suite never touches the network.
SOURCE_FILE_ENV = "BUDDY_MODEL_FACTS_FILE"
FETCH_TIMEOUT_SECONDS = 10
MAX_SOURCE_BYTES = 32 * 1024 * 1024
MAX_FAMILIES = 200
MAX_PROVIDERS = 512
#: The live catalog holds 8,339 models (2026-10-01); the bound stays well above it.
#: A payload beyond the bounds is an explicit failure — the previous snapshot is
#: retained — never a silent truncation that would misrecord valid entries unknown.
MAX_MODELS = 16384
MAX_MODALITIES = 16
MAX_PRICE_TIERS = 8

#: models.dev prices a subscription entry under a plan provider with zeros; that
#: means plan inclusion, never a free API.
PLAN_PROVIDER_MARKERS = ("-plan", "-tokenhub")

#: The scalar models.dev ``cost`` fields, mapped to their snapshot names. The live
#: field is ``cost``; ``context_over_200k`` mirrors the over-200k context tier.
COST_FIELDS = (
    ("input", "input"),
    ("output", "output"),
    ("cache_read", "cacheRead"),
    ("cache_write", "cacheWrite"),
    ("input_audio", "inputAudio"),
    ("output_audio", "outputAudio"),
    ("reasoning", "reasoning"),
)

#: Explicit provider identity mapping from the provider labels the installed harness
#: catalogs use to the provider ids models.dev publishes, verified against the live
#: catalog recorded 2026-10-01 (225 providers, 8,339 models). A label not listed
#: here still resolves by exact, then normalized, equality against the snapshot's
#: own provider ids; substrings are never matched. Aliases always target the API
#: provider: subscription entries (``*-coding-plan``, ``*-token-plan``,
#: ``*-tokenhub``) carry zero prices that mean plan inclusion, never a free API.
PROVIDER_ALIASES = {
    "deepseek-official": "deepseek",
    "zai-api": "zai",
    "zhipu": "zhipuai",
}

UNKNOWN_IDENTITY = "identity-unresolved"
UNKNOWN_ENTRY = "entry-missing"
UNKNOWN_INVALID = "entry-invalid"
UNKNOWN_NEVER = "never-fetched"


def _identity(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise BoardError("INVALID_ARGUMENT", f"model facts family {field} must be a nonempty string of at most 256 characters")
    return value.strip()


def normalize_family(value: str) -> str:
    """The comparable form of one provider or model label: case and punctuation aside."""
    return "".join(character for character in value.strip().lower() if character.isalnum())


def build_request() -> urllib.request.Request:
    """The credential-free request for the public source.

    No authorization, cookie or token header is ever attached: the source is public
    and the request must never carry user credentials, keys or board identity.
    """
    return urllib.request.Request(
        SOURCE_URL,
        headers={"Accept": "application/json", "User-Agent": "hey-my-buddy-model-facts"},
    )


def fetch_source() -> tuple[bytes, str]:
    """One bounded read of the public source; returns the raw payload and its origin.

    The file hook wins when set, so tests and air-gapped setups stay deterministic.
    Any failure raises: the caller then retains the previous snapshot.
    """
    override = os.environ.get(SOURCE_FILE_ENV)
    if override:
        path = Path(override).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as error:
            raise BoardError("MODEL_FACTS_SOURCE_UNAVAILABLE", "The model facts source file could not be read") from error
        if len(raw) > MAX_SOURCE_BYTES:
            raise BoardError("MODEL_FACTS_SOURCE_INVALID", "The model facts source file exceeds the size bound")
        return raw, f"file:{path}"
    request = build_request()
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_SOURCE_BYTES + 1)
    except OSError as error:
        raise BoardError("MODEL_FACTS_SOURCE_UNAVAILABLE", "The public model facts source could not be fetched") from error
    if len(raw) > MAX_SOURCE_BYTES:
        raise BoardError("MODEL_FACTS_SOURCE_INVALID", "The public model facts source exceeds the size bound")
    return raw, SOURCE_URL


def parse_source(raw: bytes) -> dict[str, dict[str, dict]]:
    """Index one snapshot payload as ``{provider id: {model id: entry}}``.

    Only structural garbage is refused; one malformed provider or model entry is
    skipped so the rest of a large public catalog still yields facts. A payload
    beyond the provider or model bounds raises: the caller then retains the previous
    snapshot instead of silently truncating valid entries into unknown.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise BoardError("MODEL_FACTS_SOURCE_INVALID", "The model facts source is not valid JSON") from error
    if not isinstance(payload, dict):
        raise BoardError("MODEL_FACTS_SOURCE_INVALID", "The model facts source must be a JSON object")
    index: dict[str, dict[str, dict]] = {}
    model_count = 0
    for provider_id, provider in payload.items():
        if not isinstance(provider_id, str) or not isinstance(provider, dict):
            continue
        if len(index) >= MAX_PROVIDERS:
            raise BoardError("MODEL_FACTS_SOURCE_INVALID",
                             f"The model facts source exceeds {MAX_PROVIDERS} providers; the previous snapshot is retained")
        models: dict[str, dict] = {}
        raw_models = provider.get("models")
        if isinstance(raw_models, dict):
            for model_id, entry in raw_models.items():
                if not isinstance(model_id, str) or not isinstance(entry, dict):
                    continue
                if model_count >= MAX_MODELS:
                    raise BoardError("MODEL_FACTS_SOURCE_INVALID",
                                     f"The model facts source exceeds {MAX_MODELS} models; the previous snapshot is retained")
                models[model_id] = entry
                model_count += 1
        index[provider_id] = models
    return index


def _number(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _flag(entry: dict, key: str) -> bool | None:
    value = entry.get(key)
    return value if isinstance(value, bool) else None


def _modality_list(value) -> list[str] | None:
    if not isinstance(value, list):
        return None
    names = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    return names[:MAX_MODALITIES] or None


def _price_fields(cost: dict) -> dict:
    """The scalar cost fields present in one models.dev ``cost`` (or tier) object."""
    price: dict = {}
    for key, name in COST_FIELDS:
        number = _number(cost.get(key))
        if number is not None:
            price[name] = number
    return price


def _price_tiers(tiers) -> list[dict]:
    """The bounded context-tier price list; a tier's size travels with its prices."""
    if not isinstance(tiers, list):
        return []
    extracted: list[dict] = []
    for tier in tiers:
        if not isinstance(tier, dict) or len(extracted) >= MAX_PRICE_TIERS:
            break
        prices = _price_fields(tier)
        if not prices:
            continue
        bounds = tier.get("tier") if isinstance(tier.get("tier"), dict) else {}
        size = bounds.get("size")
        if bounds.get("type") == "context" and isinstance(size, int) and not isinstance(size, bool) and size > 0:
            prices["tierContextSize"] = size
        extracted.append(prices)
    return extracted


def extract_facts(entry: dict, *, plan_provider: bool = False) -> dict:
    """The bounded program facts of one models.dev model entry.

    models.dev publishes prices as USD per million tokens under ``cost``; the unit
    and the billing method are recorded next to the numbers so a reader never has to
    assume them. An entry under a subscription plan provider carries zero prices that
    mean plan inclusion — recorded as ``plan-included`` so a zero is never read as a
    free API. Context-tier prices (``tiers``, ``context_over_200k``) are preserved
    beside the base price. Absent fields are simply absent from the snapshot: a
    reader reports unknown rather than zero.
    """
    facts: dict = {}
    cost = entry.get("cost") if isinstance(entry.get("cost"), dict) else None
    price = _price_fields(cost) if cost else {}
    tiers = _price_tiers(cost.get("tiers")) if cost else []
    if tiers:
        price["tiers"] = tiers
    over = cost.get("context_over_200k") if cost else None
    if isinstance(over, dict):
        over_price = _price_fields(over)
        if over_price:
            price["contextOver200k"] = over_price
    if price:
        facts["price"] = {"currency": "USD", "unit": "usd-per-million-tokens", **price}
        facts["billing"] = {"method": "plan-included" if plan_provider else "per-token",
                            "unit": "million-tokens", "currency": "USD"}
    limit = entry.get("limit") if isinstance(entry.get("limit"), dict) else None
    context = limit.get("context") if limit else None
    if isinstance(context, int) and not isinstance(context, bool) and context > 0:
        facts["contextLength"] = context
    output = limit.get("output") if limit else None
    if isinstance(output, int) and not isinstance(output, bool) and output > 0:
        facts["outputLimit"] = output
    release = entry.get("release_date")
    if isinstance(release, str) and release.strip() and len(release.strip()) <= 64:
        facts["releaseDate"] = release.strip()
    capabilities: dict = {}
    for key, name in (("tool_call", "toolCall"), ("reasoning", "reasoning"), ("attachment", "attachment"),
                      ("temperature", "temperature"), ("open_weights", "openWeights")):
        flag = _flag(entry, key)
        if flag is not None:
            capabilities[name] = flag
    modalities = entry.get("modalities") if isinstance(entry.get("modalities"), dict) else None
    if modalities is not None:
        inputs = _modality_list(modalities.get("input"))
        outputs = _modality_list(modalities.get("output"))
        if inputs is not None:
            capabilities["inputModalities"] = inputs
        if outputs is not None:
            capabilities["outputModalities"] = outputs
    if capabilities:
        facts["capabilities"] = capabilities
    return facts


def resolve_identity(index: dict[str, dict[str, dict]], provider: str, model: str) -> tuple[str | None, str | None]:
    """Map one ``(provider, model)`` family label onto the snapshot's own identity.

    The mapping is explicit and auditable: a listed provider alias wins, then exact
    and normalized equality against the published provider ids; the model resolves by
    exact, then normalized, equality inside that provider, with a ``models/`` prefix
    tolerated. Nothing is inferred from substrings. The returned pair says how far
    the resolution reached — an unresolved provider is ``(None, None)``, a provider
    without the requested model keeps the provider and a ``None`` model — so the
    caller can report the exact unknown reason instead of a guess.
    """
    provider_id = PROVIDER_ALIASES.get(provider)
    if provider_id is None:
        if provider in index:
            provider_id = provider
        else:
            wanted = normalize_family(provider)
            matches = [name for name in index if normalize_family(name) == wanted]
            provider_id = matches[0] if len(matches) == 1 else None
    if provider_id is None or provider_id not in index:
        return None, None
    models = index[provider_id]
    if model in models:
        return provider_id, model
    wanted = normalize_family(model.removeprefix("models/"))
    matches = [name for name in models if normalize_family(name.removeprefix("models/")) == wanted]
    return provider_id, matches[0] if len(matches) == 1 else None


def _families(families) -> list[tuple[str, str, str]]:
    validated: list[tuple[str, str, str]] = []
    for family in families:
        if not isinstance(family, (tuple, list)) or len(family) != 3:
            raise BoardError("INVALID_ARGUMENT", "A model facts family is an (adapter, provider, model) triple")
        adapter, provider, model = (_identity(item, "member") for item in family)
        triple = (adapter, provider, model)
        if triple not in validated:
            validated.append(triple)
    if len(validated) > MAX_FAMILIES:
        raise BoardError("INVALID_ARGUMENT", f"A model facts refresh accepts at most {MAX_FAMILIES} families")
    return validated


def refresh_families(board, families, *, now: str | None = None, source=None) -> dict:
    """Refresh the durable facts snapshot of the given model families.

    This is the one refresh function for both trigger planes: the console's user
    enablement path calls it here, and the model profile plane (ADR-021 L11) calls
    the same function when it builds a profile. One source read covers every family
    in the call. When the fetch or parse fails, nothing is written, so every family
    keeps its previous snapshot; when the fetch succeeds, a family without a
    resolvable models.dev entry records ``unknown`` with the reason and date.

    Returns a bounded outcome; a failure is reported, never raised, so a caller that
    is already inside a committed business operation cannot be broken by the network.
    """
    validated = _families(families)
    retained = [list(family) for family in validated]
    if not validated:
        return {"ok": True, "fetchedAt": None, "source": None, "refreshed": [], "unknown": [], "retained": []}
    try:
        raw, origin = (source() if source is not None else fetch_source())
        index = parse_source(raw)
    except BoardError as error:
        return {"ok": False, "reason": error.code, "message": error.message, "retained": retained}
    fetched_at = now or utc_now()
    payload_sha256 = hashlib.sha256(raw).hexdigest()
    rows, refreshed, unknown = [], [], []
    for family in validated:
        adapter, provider, model = family
        provider_id, model_id = resolve_identity(index, provider, model)
        if provider_id is None:
            status, facts, identity_view, reason = "unknown", {}, {}, UNKNOWN_IDENTITY
        elif model_id is None:
            status, facts, identity_view, reason = "unknown", {}, {"devProvider": provider_id}, UNKNOWN_ENTRY
        else:
            entry = index[provider_id][model_id]
            identity_view = {"devProvider": provider_id, "devModel": model_id}
            plan_provider = any(marker in provider_id for marker in PLAN_PROVIDER_MARKERS)
            facts = extract_facts(entry, plan_provider=plan_provider)
            if facts:
                status, reason = "current", None
            else:
                status, reason = "unknown", UNKNOWN_INVALID
        if status == "current":
            refreshed.append(list(family))
        else:
            unknown.append({"family": list(family), "reason": reason})
        rows.append((adapter, provider, model, status, canonical_json(facts), canonical_json(identity_view),
                     origin, payload_sha256, fetched_at, reason))
    with board.db.write() as connection:
        connection.executemany(
            "INSERT INTO model_facts(adapter,provider,model,status,facts_json,identity_json,source,payload_sha256,fetched_at,reason)"
            " VALUES(?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(adapter,provider,model) DO UPDATE SET status=excluded.status,facts_json=excluded.facts_json,"
            " identity_json=excluded.identity_json,source=excluded.source,payload_sha256=excluded.payload_sha256,"
            " fetched_at=excluded.fetched_at,reason=excluded.reason",
            rows,
        )
    return {"ok": True, "fetchedAt": fetched_at, "source": origin, "payloadSha256": payload_sha256,
            "refreshed": refreshed, "unknown": unknown, "retained": []}


def family_facts(board, families) -> dict[tuple[str, str, str], dict]:
    """The stored snapshot view for these families; a family with no row is unknown.

    This is the read side every consumer shares: a ``current`` view carries the facts
    with their source and fetch date, and every other case — never fetched, fetch
    failed, or the source had no entry — reads as ``unknown`` with its reason, so a
    display never has to invent a fact.
    """
    validated = _families(families)
    views: dict[tuple[str, str, str], dict] = {}
    with board.db.read() as connection:
        for family in validated:
            adapter, provider, model = family
            row = connection.execute(
                "SELECT * FROM model_facts WHERE adapter=? AND provider=? AND model=?", family
            ).fetchone()
            if row is None:
                views[family] = {"adapter": adapter, "provider": provider, "model": model, "status": "unknown",
                                 "facts": None, "identity": None, "source": None, "fetchedAt": None,
                                 "reason": UNKNOWN_NEVER}
                continue
            views[family] = {
                "adapter": adapter,
                "provider": provider,
                "model": model,
                "status": row["status"],
                "facts": json.loads(row["facts_json"]) or None,
                "identity": json.loads(row["identity_json"]) or None,
                "source": row["source"],
                "fetchedAt": row["fetched_at"],
                "reason": row["reason"],
            }
    return views


def snapshot_marker(board) -> str:
    """A stable content marker for the whole stored snapshot set, for tests and audits."""
    with board.db.read() as connection:
        rows = connection.execute(
            "SELECT adapter,provider,model,status,facts_json,identity_json,source,payload_sha256,fetched_at,reason"
            " FROM model_facts ORDER BY adapter,provider,model"
        ).fetchall()
    return sha256_text(canonical_json([list(row) for row in rows])) if rows else "empty"
