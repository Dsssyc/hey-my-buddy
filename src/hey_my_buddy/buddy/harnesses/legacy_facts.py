"""Conversions from the existing controllers' result payloads into contract values.

ADR-025 step 1-A. Every function here maps only fields a current controller
result actually carries into the frozen values of :mod:`run_contract`, with the
evidence basis each harness's code path really has today. Missing facts stay
unknown, a requested value never becomes a checked or observed one, a sent
input never becomes a started model, and the DSH Node runner's historical
stop observation is preserved verbatim under its own ``legacy-node-report``
basis until step four deletes it. No function adds a verdict, an allowance or
a new business rule: the roles and the blackboard keep every judgment.
"""
from __future__ import annotations

import json
from typing import Any, Mapping

from ...errors import BoardError
from .run_contract import (
    CatalogFacts,
    CatalogModelFacts,
    CatalogProviderFacts,
    CheckedConfiguration,
    CheckedValue,
    CompletionEvidence,
    DeniedInteraction,
    FrozenJson,
    MAX_VALUE_BYTES,
    ModelStartEvidence,
    NativeIdentity,
    ResultConfiguration,
    RunConfiguration,
    RunEnd,
    RunValue,
    StopEvidence,
    StopLayer,
    InterruptEvidence,
    _fail,
    _native_error_record,
    canonical_json,
)

#: The strongest model-start basis each harness's existing result field can
#: honestly claim. Codex and Claude Code set their flag when the input goes to
#: the native process; ZCode checks the native admission receipt; the DSH
#: no-tool plugin reports the flag itself from inside the Node run, which is a
#: legacy report, not a native start fact.
_MODEL_START_BASES = {
    "codex": "input-sent",
    "claude": "input-sent",
    "zcode": "input-admitted",
    "dsh": "legacy-report",
}

#: What the legacy ``resolved`` block of each harness means today. Codex and
#: Claude Code restate the requested configuration after a native catalog
#: membership check; ZCode re-reads the applied session snapshot; the DSH
#: no-tool path compares the native prepareCall probe.
_RESOLVED_CHECKS = {
    "codex": ("catalog-membership", "codex/app-server-model-list"),
    "claude": ("catalog-membership", "claude-code-initialize"),
    "zcode": ("native-readback", "zcode/session-snapshot"),
    "dsh": ("native-readback", "dsh/prepare-call"),
}

#: The JSON-schema check each fast path actually runs against the final value.
_FAST_VALIDATION_BASES = {
    "codex": "json-schema-subset",
    "claude": "json-schema-subset",
    "zcode": "json-schema-subset",
    "dsh": "legacy-node-json-schema-subset",
}

#: The delivery mechanism of each harness's governed final value.
_GOVERNED_MECHANISMS = {
    "codex": "native-schema",
    "claude": "native-schema",
    "zcode": "completion-tool",
    "dsh": "completion-tool",
}

_STOP_OK_BASE = "owned-process-group"
_STOP_UNCONFIRMED_BASE = "unconfirmed"
_STOP_NOT_SPAWNED_BASE = "spawn-not-started"
_STOP_LEGACY_NODE_BASE = "legacy-node-report"

#: The trusted observation source behind a confirmed inner stop. Codex, Claude
#: Code and ZCode results are always the Python controller's own group
#: observation. DSH has two real producers: the historical Node runner (the
#: legacy report) and the Python no-tool controller (a conservative owned
#: observation), so the caller must declare which payload a DSH result came
#: from; the harness name alone decides nothing.
_DECLARED_NATIVE_BASES = ("owned-process-group", "legacy-node-report")


def _harness(harness: str) -> str:
    if harness not in _MODEL_START_BASES:
        raise _fail(f"unknown harness {harness!r}", field="harness")
    return harness


def _mapping(value: Any) -> Mapping | None:
    return value if isinstance(value, Mapping) else None


def _bounded(value: Any, maximum: int = 128) -> str | None:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > maximum:
        return None
    return value


def end_from_payload(payload: Mapping, *, signal: str | None = None) -> RunEnd:
    """Map a controller result's ``status``/``code`` onto the end fact.

    ``nativeExitCode`` is the inner native process's exit as the payload's own
    ``processState`` recorded it; it is never taken from any other source. The
    outer controller's exit code belongs to ``stopEvidence.controller``, not
    here, so the two layers stay distinguishable.
    """
    if not isinstance(payload, Mapping):
        raise _fail("the result payload must be an object")
    status = payload.get("status")
    if status == "ok":
        end_status = "ok"
    elif status == "cancelled":
        end_status = "cancelled"
    else:
        end_status = "error"
    process_state = _mapping(payload.get("processState"))
    native_exit_code = process_state.get("nativeExitCode") if process_state else None
    return RunEnd(status=end_status, reason_code=_bounded(payload.get("code")),
                  native_exit_code=native_exit_code, signal=_bounded(signal, maximum=32))


def model_start_from_payload(harness: str, payload: Mapping) -> tuple[bool | None, ModelStartEvidence]:
    """Map the legacy ``modelStarted`` field with its per-harness basis.

    Only the exact booleans ``True``/``False`` are facts; a missing field (the
    DSH governed result), a ``"unknown"`` placeholder or a value of any other
    type stays ``None`` with the ``unknown`` basis — never a proven
    not-started.
    """
    _harness(harness)
    value = payload.get("modelStarted")
    if value is True or value is False:
        identity = native_identity_from_payload(payload)
        return value, ModelStartEvidence(basis=_MODEL_START_BASES[harness], native_identity=identity)
    return None, ModelStartEvidence(basis="unknown", native_identity=native_identity_from_payload(payload))


#: Bare payload keys that name the same native identifiers under the
#: ``native*`` spellings some controllers use beside (or instead of) the
#: ``nativeIdentity`` object.
_NATIVE_ID_ALIASES = (("nativeSessionId", "session_id"), ("nativeThreadId", "thread_id"),
                      ("nativeTurnId", "turn_id"), ("nativeInputId", "input_id"),
                      ("nativeCallId", "call_id"))
_NATIVE_ID_KEYS_DIRECT = (("sessionId", "session_id"), ("threadId", "thread_id"), ("turnId", "turn_id"),
                          ("inputId", "input_id"), ("callId", "call_id"))


def native_identity_from_payload(payload: Mapping) -> NativeIdentity | None:
    """The payload's own native identifiers, or ``None`` when it names none.

    All three real spellings of the current payloads are read: the
    ``nativeIdentity`` object, the bare ``sessionId``-style keys and the bare
    ``nativeTurnId``-style keys the ZCode and Codex workers actually publish.
    """
    if not isinstance(payload, Mapping):
        return None
    sources = (payload.get("nativeIdentity"), payload)
    for source in sources:
        if not isinstance(source, Mapping):
            continue
        fields = {}
        for key, name in (*_NATIVE_ID_KEYS_DIRECT, *_NATIVE_ID_ALIASES):
            value = _bounded(source.get(key), maximum=512)
            if value:
                fields[name] = value
        if fields:
            return NativeIdentity(**fields)
    return None


def _configuration_block(block: Mapping | None) -> dict | None:
    """The provider/model/effort of one payload configuration block.

    The DSH payloads spell the effort ``reasoningEffort``; every other harness
    spells it ``effort``. Both name the same requested fact.
    """
    if not isinstance(block, Mapping):
        return None
    effort = block.get("effort")
    if not isinstance(effort, str) or not effort:
        effort = block.get("reasoningEffort")
    if not all(isinstance(block.get(key), str) and block[key] for key in ("provider", "model")) \
            or not isinstance(effort, str) or not effort:
        return None
    return {"provider": block["provider"], "model": block["model"], "effort": effort}


def configuration_from_payload(harness: str, payload: Mapping) -> ResultConfiguration:
    """Map requested, checked and observed configuration without any promotion.

    ``checked`` is built only from the payload's own ``resolved`` block, under
    the declared basis that harness really checked; a result without one (the
    DSH governed path reports the request only) keeps every checked value
    ``None``. ``observed`` keeps only what the native side actually reported.
    """
    _harness(harness)
    requested_value = _configuration_block(payload.get("requested"))
    requested = RunConfiguration.from_payload(requested_value) if requested_value else None
    observed_value = _mapping(payload.get("observed"))
    observed = FrozenJson(observed_value) if observed_value else None
    resolved_value = _configuration_block(payload.get("resolved"))
    checked = CheckedConfiguration()
    checks: tuple[str, ...] = ()
    if resolved_value:
        basis, source = _RESOLVED_CHECKS[harness]
        entries = {}
        for name in ("provider", "model", "effort"):
            value = _bounded(resolved_value.get(name))
            if value:
                entries[name] = CheckedValue(value=value, basis=basis, source=source)
        checked = CheckedConfiguration(**entries)
        checks = ("catalog-membership",) if basis == "catalog-membership" else ("native-readback",)
    return ResultConfiguration(requested=requested, checked=checked, observed=observed, checks=checks)


def stop_evidence_from_payload(payload: Mapping, *, harness: str,
                               controller: StopLayer | None = None,
                               outer_exit_code: int | None = None,
                               started: bool | None = None,
                               native_observation_basis: str | None = None,
                               interrupt_requested: bool | None = None,
                               interrupt_acknowledged: bool | None = None,
                               interrupt_basis: str | None = None) -> StopEvidence:
    """Map the result's process state onto the two-layer stop facts.

    ``started`` is a fact only the holding side can state (a spawn failure it
    observed); a confirmed stop report is never read as spawn evidence, so an
    unspecified ``started`` stays ``None``. The inner observation basis is
    declared per source: Codex, Claude Code and ZCode results are the Python
    controller's own group observation (``owned-process-group``); DSH has two
    real producers — the historical Node runner, whose report is preserved
    verbatim as ``legacy-node-report`` until step four, and the Python no-tool
    controller's conservative owned observation — so a confirmed DSH stop must
    declare its source through ``native_observation_basis`` instead of being
    decided by the harness name. The outer controller layer carries the exit
    the collector observed for the controller process itself.
    """
    _harness(harness)
    process_state = _mapping(payload.get("processState")) or {}
    confirmed = process_state.get("shutdownConfirmed") is True
    exit_code = process_state.get("nativeExitCode")
    if started is False:
        native = StopLayer(group_state="gone", started=False, leader_exited=None, exit_code=None,
                           observation_basis=_STOP_NOT_SPAWNED_BASE)
    elif confirmed:
        basis = native_observation_basis
        if basis is None:
            if harness == "dsh":
                raise _fail("a confirmed DSH stop must declare its native observation basis "
                            "(legacy-node-report for the Node runner, owned-process-group for the "
                            "Python no-tool controller)", field="native_observation_basis")
            basis = _STOP_OK_BASE
        elif basis not in _DECLARED_NATIVE_BASES:
            raise _fail(f"native_observation_basis must be one of {', '.join(_DECLARED_NATIVE_BASES)}",
                        field="native_observation_basis")
        native = StopLayer(group_state="gone", started=started,
                           leader_exited=True if exit_code is not None else None,
                           exit_code=exit_code, observation_basis=basis)
    else:
        native = StopLayer(group_state="unknown", started=started,
                           leader_exited=True if exit_code is not None else None,
                           exit_code=exit_code, observation_basis=_STOP_UNCONFIRMED_BASE)
    if interrupt_requested is None:
        interrupt_requested = payload.get("nativeInterruptRequested") \
            if isinstance(payload.get("nativeInterruptRequested"), bool) else None
    if interrupt_acknowledged is None:
        interrupt_acknowledged = payload.get("nativeInterruptAcknowledged") \
            if isinstance(payload.get("nativeInterruptAcknowledged"), bool) else None
    interrupt = InterruptEvidence(requested=interrupt_requested, acknowledged=interrupt_acknowledged,
                                  basis=interrupt_basis)
    if controller is None:
        controller = StopLayer(group_state="unknown", exit_code=outer_exit_code)
    return StopEvidence(native=native, controller=controller, interrupt=interrupt)


def value_from_fast_payload(harness: str, payload: Mapping) -> RunValue | None:
    """Map a fast/no-tool result's final-message value and its schema check.

    The existing payloads carry exactly ``rawAnswer``, ``answerValid`` and
    ``correctionCount``; the schema status and validation basis stay the ones
    the old path declared, and no second, unified schema check runs here. When
    the old path judged the raw value valid, ``parsed`` is that same raw text
    decoded once (the old callers' decode step); a payload whose valid raw
    cannot decode keeps ``parsed`` unknown while the raw and the status survive.
    """
    _harness(harness)
    raw = payload.get("rawAnswer")
    answer_valid = payload.get("answerValid")
    corrections = payload.get("correctionCount")
    if raw is None and answer_valid is None and corrections is None:
        return None
    schema_status = "unknown"
    if answer_valid is True:
        schema_status = "valid"
    elif answer_valid is False:
        schema_status = "invalid"
    if raw is not None and (not isinstance(raw, str) or len(raw.encode()) > MAX_VALUE_BYTES):
        raise _fail(f"the payload rawAnswer exceeds the {MAX_VALUE_BYTES}-byte value bound "
                    f"of the strict controller read it came from", field="value.raw")
    parsed = None
    if schema_status == "valid" and isinstance(raw, str) and raw:
        # The decode mirrors the old callers' own step (plain ``json.loads``,
        # which the old ordinary validator allowed, non-finite numbers
        # included). ``parsed`` is the bounded-JSON representation of that
        # value when one exists; a value outside bounded JSON keeps
        # ``parsed`` unknown while the raw text and the old validator's own
        # schema conclusion survive untouched — the roles project them by the
        # original rules. No unified strict check is introduced here.
        try:
            candidate = json.loads(raw)
        except ValueError:
            candidate = None
        if candidate is not None:
            try:
                parsed = FrozenJson(candidate)
            except BoardError:
                parsed = None
    return RunValue(schema_status=schema_status, mechanism="final-message", raw=raw, parsed=parsed,
                    validation_basis=_FAST_VALIDATION_BASES[harness],
                    errors=(),
                    correction_count=corrections if isinstance(corrections, int) and corrections >= 0 else 0)


def governed_value(harness: str, outcome: Mapping, *, schema_status: str,
                   validation_basis: str, errors: tuple[str, ...] = (),
                   correction_count: int = 0) -> RunValue:
    """The governed final value delivered by each harness's own mechanism.

    ``outcome`` is the structured value; ``schema_status`` and
    ``validation_basis`` are the actual check facts the caller's path produced
    and must be passed explicitly — no default marks an arbitrary dict as
    valid or verified here.
    """
    _harness(harness)
    return RunValue(schema_status=schema_status, mechanism=_GOVERNED_MECHANISMS[harness],
                    parsed=FrozenJson(outcome), validation_basis=validation_basis,
                    errors=errors, correction_count=correction_count)


def completion_evidence(harness: str, *, stream_end: bool | None = None,
                        native_identity: NativeIdentity | None = None, call_id: str | None = None,
                        event_order: int | None = None, receipt_ref: str | None = None,
                        receipt_verified: bool | None = None,
                        native_outcome: str | None = None) -> CompletionEvidence:
    """The delivery evidence for a governed value, under the harness's mechanism."""
    _harness(harness)
    return CompletionEvidence(mechanism=_GOVERNED_MECHANISMS[harness], stream_end=stream_end,
                              native_identity=native_identity, call_id=call_id, event_order=event_order,
                              receipt_ref=receipt_ref, receipt_verified=receipt_verified,
                              native_outcome=native_outcome)


def native_error_from_payload(payload: Mapping) -> FrozenJson | None:
    """The payload's own raw native failure record (ZCode/DSH ``nativeFailure``).

    This is the second existing failure fact beside the whitelist quota
    attribution: a non-quota ``{code, kind}`` record keeps its own round-trip
    position (``RunResult.nativeError``) instead of being folded into the end
    reason or lost. The quota attribution maps separately through the payload's
    ``quotaFailure`` key into ``RunResult.nativeFailure``.
    """
    if not isinstance(payload, Mapping) or "nativeFailure" not in payload:
        return None
    return _native_error_record(payload.get("nativeFailure"))


def denied_interactions_from_payload(payload: Mapping) -> tuple[DeniedInteraction, ...]:
    """Map the recorded native denials; tool arguments are never carried over.

    The Codex controller always records ``{method, threadId, turnId}`` for every
    interactive request it refuses; its capture-evidence trace adds a request id
    but also raw parameter values, of which only the identity fields are read.
    """
    entries = payload.get("deniedRequests")
    if not isinstance(entries, list):
        entries = payload.get("nativeDeniedRequests")
    if not isinstance(entries, list):
        return ()
    results = []
    for entry in entries[:64]:
        if not isinstance(entry, Mapping):
            continue
        method = _bounded(entry.get("method"), maximum=128)
        if not method:
            continue
        request_id = entry.get("requestId")
        request_id = str(request_id) if isinstance(request_id, (int, str)) and request_id != "" else None
        request_id = _bounded(request_id, maximum=128)
        identity = native_identity_from_payload(entry)
        if identity is None:
            # The evidence trace keeps the identity under its own ``params``
            # object; only the identity keys are read, never command or cwd.
            identity = native_identity_from_payload(entry.get("params"))
        results.append(DeniedInteraction(method=method, action="denied", request_id=request_id,
                                         native_identity=identity,
                                         reason=_bounded(entry.get("reason"), maximum=400)))
    return tuple(results)


def catalog_facts_from_payload(payload: Mapping) -> CatalogFacts:
    """Map one existing native catalog projection onto the frozen value.

    The projection keeps every field: entries the closed names do not cover
    (``contextWindow`` and each harness's optional fields) ride along verbatim,
    a supported complete empty catalog (no providers, or providers with no
    models) is preserved as the empty fact it is, and the value projects back
    to the original payload byte-for-byte under canonical JSON.
    """
    if not isinstance(payload, Mapping):
        raise _fail("the catalog payload must be an object")
    adapter = payload.get("adapter")
    source = _bounded(payload.get("source"), maximum=120)
    harness_version = _bounded(payload.get("harnessVersion"), maximum=64)
    discovered_at = _bounded(payload.get("discoveredAt"), maximum=64)
    providers_value = payload.get("providers")
    if not source or not harness_version or not discovered_at \
            or adapter not in ("codex", "claude", "zcode", "dsh") or not isinstance(providers_value, list):
        raise _fail("the catalog payload is missing its source, adapter, version, time or providers")
    providers = tuple(CatalogProviderFacts.from_payload(provider) for provider in providers_value)
    warnings_value = payload.get("warnings")
    warnings = tuple(_bounded(warning, maximum=500) for warning in warnings_value
                     if _bounded(warning, maximum=500)) if isinstance(warnings_value, list) else ()
    known = {"source", "adapter", "harnessVersion", "discoveredAt", "providers", "warnings"}
    rest = {key: payload[key] for key in payload if key not in known}
    return CatalogFacts(adapter=adapter, source=source, harness_version=harness_version,
                        discovered_at=discovered_at, providers=providers, warnings=warnings,
                        warnings_key_present="warnings" in payload,
                        extra=FrozenJson(rest) if rest else None)


def catalog_facts_equal_payload(facts: CatalogFacts, payload: Mapping) -> bool:
    """Whether the frozen value projects back to the exact original payload."""
    return canonical_json(facts.to_payload()) == canonical_json(payload)


__all__ = [
    "catalog_facts_equal_payload", "catalog_facts_from_payload", "completion_evidence",
    "configuration_from_payload", "denied_interactions_from_payload", "end_from_payload",
    "governed_value", "model_start_from_payload", "native_error_from_payload",
    "native_identity_from_payload",
    "stop_evidence_from_payload", "value_from_fast_payload",
]
