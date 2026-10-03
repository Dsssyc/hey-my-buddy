"""The harness-internal run contract: frozen values, one codec, one protocol.

ADR-025 step 1-A draft (execution plan sections 3 and 5). One frozen
:class:`RunRequest` goes in, one factual :class:`RunResult` comes out, and every
harness implements the same :class:`HarnessRun` seam: ``run`` for one native run
and ``discover`` for model facts that never send a prompt. The values are facts:
unknown stays ``None``/``unknown``, an observed value is never filled in from a
requested one, and a spawned process is never reported as a started model. The
internal JSON uses ``formatVersion: 1`` with fixed key sets and the project's
bounded JSON rules; packages that already have a canonical projection are
validated against it once, at the decode boundary. The field set is a draft for
the later ADR-025 steps to adjust through the Host; the ordinary (256 KiB) and
strict (512 KiB) JSON allowed sets and read limits of the existing paths are
untouched by this module. No role verdict, routing mode, task brief, board
client, database handle or agent credential is part of this seam.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

from ...errors import BoardError
from ...protocol import activity as activity_protocol
from ...protocol import tool_evidence as tool_evidence_protocol
from ...protocol import usage as usage_protocol

#: The only internal format version. It never changes a public board contract.
FORMAT_VERSION = 1

HARNESS_NAMES = ("codex", "claude", "zcode", "dsh")
TOOL_SCOPES = ("none", "read", "write")
END_STATUSES = ("ok", "error", "cancelled")
MODEL_START_BASES = ("native-start", "input-admitted", "input-sent", "legacy-report", "unknown")
CHECK_BASES = ("catalog-membership", "native-readback", "unknown")
SCHEMA_STATUSES = ("valid", "invalid", "unknown")
VALUE_MECHANISMS = ("native-schema", "completion-tool", "final-message")
POLICY_ENFORCEMENTS = ("native", "unrestricted", "unknown")
GROUP_STATES = ("gone", "alive", "unknown")
CONTINUATION_MODES = ("native-session", "reconstructed-new-session")
#: The native-provided identifier fields, matching the tool-evidence identity set.
NATIVE_ID_FIELDS = ("session_id", "thread_id", "turn_id", "input_id", "call_id")
NATIVE_ID_KEYS = {"sessionId": "session_id", "threadId": "thread_id", "turnId": "turn_id",
                  "inputId": "input_id", "callId": "call_id"}

_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

#: Draft bounds of this internal format. They are new bounds for new frames; the
#: existing per-path read limits (ordinary 256 KiB evidence, strict 512 KiB
#: controller results) are separate and unchanged. The request bound holds a
#: full role-assembled prompt: the board's task text allows 1 MiB
#: (``protocol/schemas.MAX_TASK_BYTES``) and the role may add bounds of its own,
#: plus schemas and service descriptions; the result bound holds the largest
#: existing strict read (512 KiB) beside every bounded package.
MAX_RUN_REQUEST_BYTES = 16 * 1024 * 1024
MAX_RUN_RESULT_BYTES = 2 * 1024 * 1024
#: The bounded fact-package size: it holds the old strict 512 KiB read range.
#: The per-field and per-count bounds below (128 events, 512-char identifiers)
#: keep a maximal legal tool-evidence package within it, so no package the old
#: paths could carry is refused here for size alone.
MAX_PACKAGE_BYTES = 512 * 1024
#: The role-assembled input text: the board's task text bound is 1 MiB and the
#: roles add their own bounded material (hints, workspace facts, the answer
#: schema for fast prompts), so the bound is the task bound plus that margin.
#: The request frame bound above carries the worst-case JSON escaping of such
#: a text (six bytes per control character) instead of truncating or rewriting
#: a role prompt.
MAX_INPUT_TEXT_BYTES = 2 * 1024 * 1024
MAX_SCHEMA_BYTES = 65536
#: The final value's raw text and its parsed form hold the old 512 KiB strict
#: controller-read range; a value within it is never refused or dropped here.
MAX_VALUE_BYTES = 512 * 1024
MAX_ERROR_ITEMS = 16
MAX_ERROR_CHARS = 500
MAX_DENIED_INTERACTIONS = 64
MAX_UNKNOWN_EVENT_TYPES = 64
MAX_POLICY_LIMITATIONS = 8
MAX_EVIDENCE_REFS = 32
MAX_SESSION_SERVICES = 8
MAX_SERVICE_TOOL_NAMES = 16
MAX_NETWORK_DOMAINS = 64
MAX_CHECKS = 8
MAX_ROOT_IDENTITIES = 32
MAX_COUNT = 2**53 - 1
#: Existing return codes: negative values are POSIX signal terminations
#: (``-15`` for SIGTERM, ``-9`` for SIGKILL, as ``subprocess`` reports them),
#: nonnegative values include unsigned 32-bit Windows process exit codes.
MIN_EXIT_CODE = -(2**31)
MAX_EXIT_CODE = 2**32 - 1
MAX_PATH_BYTES = 4096
MAX_CATALOG_PROVIDERS = 8
MAX_CATALOG_MODELS = 200


def _fail(message: str, **details: Any) -> BoardError:
    return BoardError("INVALID_ARGUMENT", message, **details)


def canonical_json(value: object) -> str:
    """The project's deterministic bounded JSON text for one value."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _decode_strict(raw: str | bytes) -> object:
    """Strict bounded JSON: duplicate members and non-finite numbers are refused."""
    def no_duplicates(items: list[tuple[str, Any]]) -> dict:
        result: dict = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON member")
            result[key] = value
        return result

    def reject_constant(name: str) -> None:
        raise ValueError(f"non-finite JSON number {name}")

    return json.loads(raw, object_pairs_hook=no_duplicates, parse_constant=reject_constant)


def _text(value: Any, label: str, *, maximum: int) -> str:
    if not isinstance(value, str) or not value or "\0" in value or len(value.encode()) > maximum:
        raise _fail(f"{label} must be a nonempty string of at most {maximum} UTF-8 bytes", field=label)
    return value


def _optional_text(value: Any, label: str, *, maximum: int) -> str | None:
    return None if value is None else _text(value, label, maximum=maximum)


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise _fail(f"{label} must be an identifier of 1..128 characters", field=label)
    return value


def _hex64(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise _fail(f"{label} must be a lowercase sha-256 hex digest", field=label)
    return value


def _flag(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise _fail(f"{label} must be a boolean", field=label)
    return value


def _optional_flag(value: Any, label: str) -> bool | None:
    if value is None:
        return None
    return _flag(value, label)


def _count(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_COUNT:
        raise _fail(f"{label} must be an integer between 0 and {MAX_COUNT}", field=label)
    return value


def _optional_count(value: Any, label: str) -> int | None:
    return None if value is None else _count(value, label)


def _exit_code(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_EXIT_CODE <= value <= MAX_EXIT_CODE:
        raise _fail(f"{label} must be a process exit code ({MIN_EXIT_CODE}..{MAX_EXIT_CODE}: "
                    f"negative values are POSIX signal terminations)", field=label)
    return value


def _path(value: Any, label: str) -> str:
    text = _text(value, label, maximum=MAX_PATH_BYTES)
    # Existing absolute paths of both platform syntaxes are legitimate values:
    # POSIX "/..." and Windows drive "C:\..." / "C:/..." and UNC "\\..." forms.
    posix_absolute = text.startswith("/")
    windows_absolute = (len(text) >= 3 and text[1] == ":" and text[2] in "\\/" and text[0].isalpha()) \
        or text.startswith("\\\\")
    if not posix_absolute and not windows_absolute:
        raise _fail(f"{label} must be an absolute path", field=label)
    return text


def _choice(value: Any, label: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise _fail(f"{label} must be one of {', '.join(choices)}", field=label)
    return value


def _exact_keys(value: Any, label: str, keys: set[str]) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        raise _fail(f"{label} must carry exactly {', '.join(sorted(keys))}", field=label)
    return value


@dataclass(frozen=True, init=False, repr=False)
class FrozenJson:
    """One bounded JSON value kept as canonical text.

    The stored text cannot be mutated through any handle, and every read parses
    a fresh copy, so a caller can never quietly change a frozen value's payload
    through a shared dict. Construction canonicalizes the value once.
    """

    text: str

    def __init__(self, value: Any):
        try:
            text = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise _fail("value is not bounded JSON", reason=str(error)[:200]) from None
        object.__setattr__(self, "text", text)

    @classmethod
    def from_value(cls, value: Any, label: str, *, maximum: int) -> "FrozenJson":
        """Canonicalize and bound one decoded JSON field."""
        try:
            text = canonical_json(value)
        except (TypeError, ValueError) as error:
            raise _fail(f"{label} is not bounded JSON", field=label, reason=str(error)[:200]) from None
        if len(text.encode()) > maximum:
            raise _fail(f"{label} exceeds its {maximum}-byte bound", field=label)
        try:
            _decode_strict(text)
        except ValueError as error:
            raise _fail(f"{label} is not strict JSON", field=label, reason=str(error)[:200]) from None
        instance = cls.__new__(cls)
        object.__setattr__(instance, "text", text)
        return instance

    @property
    def value(self) -> Any:
        return json.loads(self.text)

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"FrozenJson({self.text[:120]})"


def _optional_frozen_json(value: Any, label: str, *, maximum: int) -> FrozenJson | None:
    if value is None:
        return None
    if isinstance(value, FrozenJson):
        # A pre-built FrozenJson is immutable but its size must still respect
        # the field's own bound.
        if len(value.text.encode()) > maximum:
            raise _fail(f"{label} exceeds its {maximum}-byte bound", field=label)
        return value
    return FrozenJson.from_value(value, label, maximum=maximum)


@dataclass(frozen=True)
class RunIdentity:
    """The frozen execution identity shared by a request and its result."""

    task_id: str
    attempt_id: str
    generation: int
    invocation_id: str
    turn_id: str | None = None
    input_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_id", _identifier(self.task_id, "identity.taskId"))
        object.__setattr__(self, "attempt_id", _identifier(self.attempt_id, "identity.attemptId"))
        if isinstance(self.generation, bool) or not isinstance(self.generation, int) or self.generation < 0:
            raise _fail("identity.generation must be a nonnegative integer", field="identity.generation")
        object.__setattr__(self, "invocation_id", _identifier(self.invocation_id, "identity.invocationId"))
        object.__setattr__(self, "turn_id", _optional_text(self.turn_id, "identity.turnId", maximum=128))
        object.__setattr__(self, "input_sha256",
                           None if self.input_sha256 is None else _hex64(self.input_sha256, "identity.inputSha256"))

    def to_payload(self) -> dict:
        return {"taskId": self.task_id, "attemptId": self.attempt_id, "generation": self.generation,
                "invocationId": self.invocation_id, "turnId": self.turn_id, "inputSha256": self.input_sha256}

    @classmethod
    def from_payload(cls, value: Any) -> "RunIdentity":
        _exact_keys(value, "identity", {"taskId", "attemptId", "generation", "invocationId", "turnId", "inputSha256"})
        return cls(task_id=value["taskId"], attempt_id=value["attemptId"], generation=value["generation"],
                   invocation_id=value["invocationId"], turn_id=value["turnId"], input_sha256=value["inputSha256"])


@dataclass(frozen=True)
class RunConfiguration:
    """The frozen provider/model/effort selection; no default buddy, no fallback."""

    provider: str
    model: str
    effort: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "provider", _text(self.provider, "configuration.provider", maximum=128))
        object.__setattr__(self, "model", _text(self.model, "configuration.model", maximum=128))
        object.__setattr__(self, "effort", _text(self.effort, "configuration.effort", maximum=64))

    def to_payload(self) -> dict:
        return {"provider": self.provider, "model": self.model, "effort": self.effort}

    @classmethod
    def from_payload(cls, value: Any) -> "RunConfiguration":
        _exact_keys(value, "configuration", {"provider", "model", "effort"})
        return cls(provider=value["provider"], model=value["model"], effort=value["effort"])


@dataclass(frozen=True)
class FrozenAccountReference:
    """A non-secret reference to the frozen account.

    Only scalar references: adapter, source, revisions, an identity string and a
    native account location reference. No key, token or file content is carried,
    and the executor never opens the user's credential files.
    """

    adapter: str
    source: str | None = None
    revision: int | None = None
    credential_revision: int | None = None
    identity: str | None = None
    native_location: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter", _choice(self.adapter, "frozenAccount.adapter", HARNESS_NAMES))
        object.__setattr__(self, "source", _optional_text(self.source, "frozenAccount.source", maximum=64))
        for name in ("revision", "credential_revision"):
            value = getattr(self, name)
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise _fail(f"frozenAccount.{name} must be a nonnegative integer", field=f"frozenAccount.{name}")
        object.__setattr__(self, "identity", _optional_text(self.identity, "frozenAccount.identity", maximum=256))
        object.__setattr__(self, "native_location",
                           _optional_text(self.native_location, "frozenAccount.nativeLocation", maximum=1024))

    def to_payload(self) -> dict:
        return {"adapter": self.adapter, "source": self.source, "revision": self.revision,
                "credentialRevision": self.credential_revision, "identity": self.identity,
                "nativeLocation": self.native_location}

    @classmethod
    def from_payload(cls, value: Any) -> "FrozenAccountReference":
        _exact_keys(value, "frozenAccount", {"adapter", "source", "revision", "credentialRevision",
                                             "identity", "nativeLocation"})
        return cls(adapter=value["adapter"], source=value["source"], revision=value["revision"],
                   credential_revision=value["credentialRevision"], identity=value["identity"],
                   native_location=value["nativeLocation"])


@dataclass(frozen=True)
class PrivateStatePaths:
    """The attempt-private invocation and native roots of one run."""

    invocation_root: str
    native_root: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "invocation_root", _path(self.invocation_root, "privateState.invocationRoot"))
        object.__setattr__(self, "native_root", _path(self.native_root, "privateState.nativeRoot"))

    def to_payload(self) -> dict:
        return {"invocationRoot": self.invocation_root, "nativeRoot": self.native_root}

    @classmethod
    def from_payload(cls, value: Any) -> "PrivateStatePaths":
        _exact_keys(value, "privateState", {"invocationRoot", "nativeRoot"})
        return cls(invocation_root=value["invocationRoot"], native_root=value["nativeRoot"])


@dataclass(frozen=True)
class NetworkPolicy:
    """The requested network fact; ``False`` never forbids a model connection."""

    requested: bool
    allowed_domains: tuple[str, ...] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested", _flag(self.requested, "network.requested"))
        domains = self.allowed_domains
        if domains is not None:
            if not isinstance(domains, tuple):
                domains = tuple(domains)
            if len(domains) > MAX_NETWORK_DOMAINS:
                raise _fail(f"network.allowedDomains carries more than {MAX_NETWORK_DOMAINS} domains",
                            field="network.allowedDomains")
            domains = tuple(_text(domain, "network.allowedDomains", maximum=255) for domain in domains)
        object.__setattr__(self, "allowed_domains", domains)

    def to_payload(self) -> dict:
        return {"requested": self.requested,
                "allowedDomains": None if self.allowed_domains is None else list(self.allowed_domains)}

    @classmethod
    def from_payload(cls, value: Any) -> "NetworkPolicy":
        _exact_keys(value, "network", {"requested", "allowedDomains"})
        domains = value["allowedDomains"]
        return cls(requested=value["requested"],
                   allowed_domains=None if domains is None else tuple(domains))


@dataclass(frozen=True)
class RunBudget:
    """The run budget; ``timeoutSeconds: 0`` keeps the existing unlimited meaning."""

    timeout_seconds: int
    max_output_bytes: int
    tool_calls: int | None = None
    bytes_read: int | None = None

    def __post_init__(self) -> None:
        seconds = self.timeout_seconds
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 0 <= seconds <= 86400:
            raise _fail("budget.timeoutSeconds must be an integer between 0 and 86400",
                        field="budget.timeoutSeconds")
        object.__setattr__(self, "max_output_bytes", _count(self.max_output_bytes, "budget.maxOutputBytes"))
        object.__setattr__(self, "tool_calls", _optional_count(self.tool_calls, "budget.toolCalls"))
        object.__setattr__(self, "bytes_read", _optional_count(self.bytes_read, "budget.bytesRead"))

    def to_payload(self) -> dict:
        return {"timeoutSeconds": self.timeout_seconds, "toolCalls": self.tool_calls,
                "bytesRead": self.bytes_read, "maxOutputBytes": self.max_output_bytes}

    @classmethod
    def from_payload(cls, value: Any) -> "RunBudget":
        _exact_keys(value, "budget", {"timeoutSeconds", "toolCalls", "bytesRead", "maxOutputBytes"})
        return cls(timeout_seconds=value["timeoutSeconds"], max_output_bytes=value["maxOutputBytes"],
                   tool_calls=value["toolCalls"], bytes_read=value["bytesRead"])


@dataclass(frozen=True)
class RunContinuation:
    """A requested continuation; without evidence no native resume is enabled."""

    mode: str
    previous_session_id: str
    binding_ref: str | None = None
    previous_native_turn_id: str | None = None
    previous_attempt_id: str | None = None
    previous_input_sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _choice(self.mode, "continuation.mode", CONTINUATION_MODES))
        object.__setattr__(self, "previous_session_id",
                           _text(self.previous_session_id, "continuation.previousSessionId", maximum=512))
        object.__setattr__(self, "binding_ref",
                           _optional_text(self.binding_ref, "continuation.bindingRef", maximum=512))
        object.__setattr__(self, "previous_native_turn_id",
                           _optional_text(self.previous_native_turn_id, "continuation.previousNativeTurnId",
                                          maximum=512))
        object.__setattr__(self, "previous_attempt_id",
                           None if self.previous_attempt_id is None
                           else _identifier(self.previous_attempt_id, "continuation.previousAttemptId"))
        object.__setattr__(self, "previous_input_sha256",
                           None if self.previous_input_sha256 is None
                           else _hex64(self.previous_input_sha256, "continuation.previousInputSha256"))

    def to_payload(self) -> dict:
        return {"mode": self.mode, "previousSessionId": self.previous_session_id, "bindingRef": self.binding_ref,
                "previousNativeTurnId": self.previous_native_turn_id, "previousAttemptId": self.previous_attempt_id,
                "previousInputSha256": self.previous_input_sha256}

    @classmethod
    def from_payload(cls, value: Any) -> "RunContinuation":
        _exact_keys(value, "continuation", {"mode", "previousSessionId", "bindingRef", "previousNativeTurnId",
                                            "previousAttemptId", "previousInputSha256"})
        return cls(mode=value["mode"], previous_session_id=value["previousSessionId"],
                   binding_ref=value["bindingRef"], previous_native_turn_id=value["previousNativeTurnId"],
                   previous_attempt_id=value["previousAttemptId"], previous_input_sha256=value["previousInputSha256"])


@dataclass(frozen=True)
class SessionService:
    """One in-run service description; its instance stays with the role."""

    service_id: str
    kind: str
    tool_names: tuple[str, ...]
    input_schema: FrozenJson | None = None
    output_schema: FrozenJson | None = None
    delivery_mode: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _identifier(self.service_id, "sessionServices.serviceId"))
        object.__setattr__(self, "kind", _text(self.kind, "sessionServices.kind", maximum=32))
        names = self.tool_names
        if not isinstance(names, tuple):
            names = tuple(names)
        if len(names) > MAX_SERVICE_TOOL_NAMES:
            raise _fail(f"sessionServices.toolNames carries more than {MAX_SERVICE_TOOL_NAMES} names",
                        field="sessionServices.toolNames")
        object.__setattr__(self, "tool_names",
                           tuple(_text(name, "sessionServices.toolNames", maximum=512) for name in names))
        object.__setattr__(self, "input_schema",
                           _optional_frozen_json(self.input_schema, "sessionServices.inputSchema",
                                                 maximum=MAX_SCHEMA_BYTES))
        object.__setattr__(self, "output_schema",
                           _optional_frozen_json(self.output_schema, "sessionServices.outputSchema",
                                                 maximum=MAX_SCHEMA_BYTES))
        object.__setattr__(self, "delivery_mode",
                           _optional_text(self.delivery_mode, "sessionServices.deliveryMode", maximum=32))

    def to_payload(self) -> dict:
        return {"serviceId": self.service_id, "kind": self.kind, "toolNames": list(self.tool_names),
                "inputSchema": None if self.input_schema is None else self.input_schema.value,
                "outputSchema": None if self.output_schema is None else self.output_schema.value,
                "deliveryMode": self.delivery_mode}

    @classmethod
    def from_payload(cls, value: Any) -> "SessionService":
        _exact_keys(value, "sessionServices[]", {"serviceId", "kind", "toolNames", "inputSchema",
                                                 "outputSchema", "deliveryMode"})
        return cls(service_id=value["serviceId"], kind=value["kind"], tool_names=tuple(value["toolNames"]),
                   input_schema=_optional_frozen_json(value["inputSchema"], "sessionServices.inputSchema",
                                                      maximum=MAX_SCHEMA_BYTES),
                   output_schema=_optional_frozen_json(value["outputSchema"], "sessionServices.outputSchema",
                                                       maximum=MAX_SCHEMA_BYTES),
                   delivery_mode=value["deliveryMode"])


@dataclass(frozen=True)
class RunRequest:
    """One harness run request: values only, no role authority of any kind."""

    identity: RunIdentity
    harness: str
    configuration: RunConfiguration
    cwd: str
    private_state: PrivateStatePaths
    input_text: str
    tool_scope: str
    network: NetworkPolicy
    output_schema: FrozenJson
    budget: RunBudget
    frozen_account: FrozenAccountReference | None = None
    continuation: RunContinuation | None = None
    session_services: tuple[SessionService, ...] = ()
    capture_evidence: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunIdentity):
            raise _fail("identity must be a RunIdentity")
        object.__setattr__(self, "harness", _choice(self.harness, "harness", HARNESS_NAMES))
        if not isinstance(self.configuration, RunConfiguration):
            raise _fail("configuration must be a RunConfiguration")
        object.__setattr__(self, "cwd", _path(self.cwd, "cwd"))
        if not isinstance(self.private_state, PrivateStatePaths):
            raise _fail("private_state must be a PrivateStatePaths")
        text = self.input_text
        if not isinstance(text, str) or not text or "\0" in text or len(text.encode()) > MAX_INPUT_TEXT_BYTES:
            raise _fail(f"inputText must be a nonempty string of at most {MAX_INPUT_TEXT_BYTES} UTF-8 bytes",
                        field="inputText")
        object.__setattr__(self, "tool_scope", _choice(self.tool_scope, "toolScope", TOOL_SCOPES))
        if not isinstance(self.network, NetworkPolicy):
            raise _fail("network must be a NetworkPolicy")
        if not isinstance(self.output_schema, FrozenJson) or len(self.output_schema.text.encode()) > MAX_SCHEMA_BYTES:
            raise _fail(f"outputSchema must be bounded JSON of at most {MAX_SCHEMA_BYTES} bytes",
                        field="outputSchema")
        if not isinstance(self.budget, RunBudget):
            raise _fail("budget must be a RunBudget")
        if self.frozen_account is not None and not isinstance(self.frozen_account, FrozenAccountReference):
            raise _fail("frozen_account must be a FrozenAccountReference")
        if self.continuation is not None and not isinstance(self.continuation, RunContinuation):
            raise _fail("continuation must be a RunContinuation")
        services = self.session_services
        if not isinstance(services, tuple):
            services = tuple(services)
        if len(services) > MAX_SESSION_SERVICES:
            raise _fail(f"sessionServices carries more than {MAX_SESSION_SERVICES} services",
                        field="sessionServices")
        for service in services:
            if not isinstance(service, SessionService):
                raise _fail("sessionServices entries must be SessionService values")
        object.__setattr__(self, "session_services", services)
        object.__setattr__(self, "capture_evidence", _flag(self.capture_evidence, "captureEvidence"))

    def to_payload(self) -> dict:
        return {
            "formatVersion": FORMAT_VERSION,
            "identity": self.identity.to_payload(),
            "harness": self.harness,
            "configuration": self.configuration.to_payload(),
            "frozenAccount": None if self.frozen_account is None else self.frozen_account.to_payload(),
            "cwd": self.cwd,
            "privateState": self.private_state.to_payload(),
            "inputText": self.input_text,
            "toolScope": self.tool_scope,
            "network": self.network.to_payload(),
            "outputSchema": self.output_schema.value,
            "budget": self.budget.to_payload(),
            "continuation": None if self.continuation is None else self.continuation.to_payload(),
            "sessionServices": [service.to_payload() for service in self.session_services],
            "captureEvidence": self.capture_evidence,
        }

    @classmethod
    def from_payload(cls, value: Any) -> "RunRequest":
        _exact_keys(value, "run request", {"formatVersion", "identity", "harness", "configuration",
                                           "frozenAccount", "cwd", "privateState", "inputText", "toolScope",
                                           "network", "outputSchema", "budget", "continuation",
                                           "sessionServices", "captureEvidence"})
        _exact_version(value["formatVersion"], "run request")
        if value["outputSchema"] is None:
            raise _fail("outputSchema is required", field="outputSchema")
        services = value["sessionServices"]
        if not isinstance(services, list):
            raise _fail("sessionServices must be a list", field="sessionServices")
        return cls(
            identity=RunIdentity.from_payload(value["identity"]),
            harness=value["harness"],
            configuration=RunConfiguration.from_payload(value["configuration"]),
            cwd=value["cwd"],
            private_state=PrivateStatePaths.from_payload(value["privateState"]),
            input_text=value["inputText"],
            tool_scope=value["toolScope"],
            network=NetworkPolicy.from_payload(value["network"]),
            output_schema=FrozenJson.from_value(value["outputSchema"], "outputSchema", maximum=MAX_SCHEMA_BYTES),
            budget=RunBudget.from_payload(value["budget"]),
            frozen_account=None if value["frozenAccount"] is None
            else FrozenAccountReference.from_payload(value["frozenAccount"]),
            continuation=None if value["continuation"] is None
            else RunContinuation.from_payload(value["continuation"]),
            session_services=tuple(SessionService.from_payload(service) for service in services),
            capture_evidence=value["captureEvidence"],
        )


@dataclass(frozen=True)
class NativeIdentity:
    """One native-provided root identity; no field is ever fabricated."""

    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    input_id: str | None = None
    call_id: str | None = None

    def __post_init__(self) -> None:
        for name in NATIVE_ID_FIELDS:
            object.__setattr__(self, name,
                               _optional_text(getattr(self, name), f"nativeIdentity.{name}", maximum=512))
        if not any(getattr(self, name) for name in NATIVE_ID_FIELDS):
            raise _fail("nativeIdentity must carry at least one native identifier field")

    def to_payload(self) -> dict:
        return {key: getattr(self, name) for key, name in NATIVE_ID_KEYS.items() if getattr(self, name) is not None}

    @classmethod
    def from_payload(cls, value: Any) -> "NativeIdentity":
        if not isinstance(value, dict) or not value or set(value) - set(NATIVE_ID_KEYS):
            raise _fail("nativeIdentity must be an object of native identifier fields")
        return cls(**{name: value.get(key) for key, name in NATIVE_ID_KEYS.items()})


@dataclass(frozen=True)
class RunEnd:
    """The native/protocol end fact; never a role verdict."""

    status: str
    reason_code: str | None = None
    native_exit_code: int | None = None
    signal: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", _choice(self.status, "end.status", END_STATUSES))
        object.__setattr__(self, "reason_code", _optional_text(self.reason_code, "end.reasonCode", maximum=128))
        object.__setattr__(self, "native_exit_code", _exit_code(self.native_exit_code, "end.nativeExitCode"))
        object.__setattr__(self, "signal", _optional_text(self.signal, "end.signal", maximum=32))

    def to_payload(self) -> dict:
        return {"status": self.status, "reasonCode": self.reason_code,
                "nativeExitCode": self.native_exit_code, "signal": self.signal}

    @classmethod
    def from_payload(cls, value: Any) -> "RunEnd":
        _exact_keys(value, "end", {"status", "reasonCode", "nativeExitCode", "signal"})
        return cls(status=value["status"], reason_code=value["reasonCode"],
                   native_exit_code=value["nativeExitCode"], signal=value["signal"])


@dataclass(frozen=True)
class ModelStartEvidence:
    """The basis of the ``modelStarted`` boolean; a send is never a model proof."""

    basis: str
    native_identity: NativeIdentity | None = None
    event_sequence: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "basis", _choice(self.basis, "modelStartEvidence.basis", MODEL_START_BASES))
        if self.native_identity is not None and not isinstance(self.native_identity, NativeIdentity):
            raise _fail("modelStartEvidence.nativeIdentity must be a NativeIdentity")
        object.__setattr__(self, "event_sequence",
                           _optional_count(self.event_sequence, "modelStartEvidence.eventSequence"))

    def to_payload(self) -> dict:
        return {"basis": self.basis,
                "nativeIdentity": None if self.native_identity is None else self.native_identity.to_payload(),
                "eventSequence": self.event_sequence}

    @classmethod
    def from_payload(cls, value: Any) -> "ModelStartEvidence":
        _exact_keys(value, "modelStartEvidence", {"basis", "nativeIdentity", "eventSequence"})
        return cls(basis=value["basis"],
                   native_identity=None if value["nativeIdentity"] is None
                   else NativeIdentity.from_payload(value["nativeIdentity"]),
                   event_sequence=value["eventSequence"])


@dataclass(frozen=True)
class CheckedValue:
    """One checked configuration value with the basis that backs it."""

    value: str
    basis: str
    source: str | None = None
    native_identity: NativeIdentity | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _text(self.value, "checked.value", maximum=128))
        object.__setattr__(self, "basis", _choice(self.basis, "checked.basis", CHECK_BASES))
        object.__setattr__(self, "source", _optional_text(self.source, "checked.source", maximum=120))
        if self.native_identity is not None and not isinstance(self.native_identity, NativeIdentity):
            raise _fail("checked.nativeIdentity must be a NativeIdentity")

    def to_payload(self) -> dict:
        return {"value": self.value, "basis": self.basis, "source": self.source,
                "nativeIdentity": None if self.native_identity is None else self.native_identity.to_payload()}

    @classmethod
    def from_payload(cls, value: Any) -> "CheckedValue":
        _exact_keys(value, "checked[]", {"value", "basis", "source", "nativeIdentity"})
        return cls(value=value["value"], basis=value["basis"], source=value["source"],
                   native_identity=None if value["nativeIdentity"] is None
                   else NativeIdentity.from_payload(value["nativeIdentity"]))


@dataclass(frozen=True)
class CheckedConfiguration:
    """Per-value checked configuration; a requested value never stands in for one."""

    provider: CheckedValue | None = None
    model: CheckedValue | None = None
    effort: CheckedValue | None = None

    def __post_init__(self) -> None:
        for name in ("provider", "model", "effort"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, CheckedValue):
                raise _fail(f"checked.{name} must be a CheckedValue")

    def to_payload(self) -> dict:
        return {"provider": None if self.provider is None else self.provider.to_payload(),
                "model": None if self.model is None else self.model.to_payload(),
                "effort": None if self.effort is None else self.effort.to_payload()}

    @classmethod
    def from_payload(cls, value: Any) -> "CheckedConfiguration":
        _exact_keys(value, "checked", {"provider", "model", "effort"})
        return cls(provider=None if value["provider"] is None else CheckedValue.from_payload(value["provider"]),
                   model=None if value["model"] is None else CheckedValue.from_payload(value["model"]),
                   effort=None if value["effort"] is None else CheckedValue.from_payload(value["effort"]))


@dataclass(frozen=True)
class ResultConfiguration:
    """Requested, checked and observed configuration, kept strictly apart."""

    requested: RunConfiguration | None = None
    checked: CheckedConfiguration = field(default_factory=CheckedConfiguration)
    observed: FrozenJson | None = None
    checks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.requested is not None and not isinstance(self.requested, RunConfiguration):
            raise _fail("configuration.requested must be a RunConfiguration")
        if not isinstance(self.checked, CheckedConfiguration):
            raise _fail("configuration.checked must be a CheckedConfiguration")
        object.__setattr__(self, "observed", _optional_frozen_json(self.observed, "configuration.observed",
                                                                   maximum=MAX_SCHEMA_BYTES))
        checks = self.checks
        if not isinstance(checks, tuple):
            checks = tuple(checks)
        if len(checks) > MAX_CHECKS:
            raise _fail(f"configuration.checks carries more than {MAX_CHECKS} entries", field="configuration.checks")
        object.__setattr__(self, "checks",
                           tuple(_text(check, "configuration.checks", maximum=32) for check in checks))

    def to_payload(self) -> dict:
        return {"requested": None if self.requested is None else self.requested.to_payload(),
                "checked": self.checked.to_payload(),
                "observed": None if self.observed is None else self.observed.value,
                "checks": list(self.checks)}

    @classmethod
    def from_payload(cls, value: Any) -> "ResultConfiguration":
        _exact_keys(value, "configuration", {"requested", "checked", "observed", "checks"})
        return cls(requested=None if value["requested"] is None
                   else RunConfiguration.from_payload(value["requested"]),
                   checked=CheckedConfiguration.from_payload(value["checked"]),
                   observed=_optional_frozen_json(value["observed"], "configuration.observed",
                                                  maximum=MAX_SCHEMA_BYTES),
                   checks=tuple(value["checks"]))


@dataclass(frozen=True)
class RunValue:
    """The final value of one run and the basis of its schema check."""

    schema_status: str
    mechanism: str
    raw: str | None = None
    parsed: FrozenJson | None = None
    validation_basis: str | None = None
    errors: tuple[str, ...] = ()
    correction_count: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "schema_status", _choice(self.schema_status, "value.schemaStatus", SCHEMA_STATUSES))
        object.__setattr__(self, "mechanism", _choice(self.mechanism, "value.mechanism", VALUE_MECHANISMS))
        if self.raw is not None:
            if not isinstance(self.raw, str) or len(self.raw.encode()) > MAX_VALUE_BYTES:
                raise _fail(f"value.raw must be a string of at most {MAX_VALUE_BYTES} UTF-8 bytes",
                            field="value.raw")
        object.__setattr__(self, "parsed", _optional_frozen_json(self.parsed, "value.parsed",
                                                                 maximum=MAX_VALUE_BYTES))
        object.__setattr__(self, "validation_basis",
                           _optional_text(self.validation_basis, "value.validationBasis", maximum=128))
        errors = self.errors
        if not isinstance(errors, tuple):
            errors = tuple(errors)
        if len(errors) > MAX_ERROR_ITEMS:
            raise _fail(f"value.errors carries more than {MAX_ERROR_ITEMS} entries", field="value.errors")
        object.__setattr__(self, "errors",
                           tuple(_text(error, "value.errors", maximum=MAX_ERROR_CHARS) for error in errors))
        corrections = self.correction_count
        if isinstance(corrections, bool) or not isinstance(corrections, int) or corrections < 0:
            raise _fail("value.correctionCount must be a nonnegative integer", field="value.correctionCount")
        object.__setattr__(self, "correction_count", corrections)

    def to_payload(self) -> dict:
        return {"schemaStatus": self.schema_status, "mechanism": self.mechanism, "raw": self.raw,
                "parsed": None if self.parsed is None else self.parsed.value,
                "validationBasis": self.validation_basis, "errors": list(self.errors),
                "correctionCount": self.correction_count}

    @classmethod
    def from_payload(cls, value: Any) -> "RunValue":
        _exact_keys(value, "value", {"schemaStatus", "mechanism", "raw", "parsed", "validationBasis",
                                     "errors", "correctionCount"})
        return cls(schema_status=value["schemaStatus"], mechanism=value["mechanism"], raw=value["raw"],
                   parsed=_optional_frozen_json(value["parsed"], "value.parsed", maximum=MAX_SCHEMA_BYTES),
                   validation_basis=value["validationBasis"], errors=tuple(value["errors"]),
                   correction_count=value["correctionCount"])


@dataclass(frozen=True)
class CompletionEvidence:
    """How the final value was actually delivered, with its native evidence."""

    mechanism: str
    stream_end: bool | None = None
    native_identity: NativeIdentity | None = None
    call_id: str | None = None
    event_order: int | None = None
    receipt_ref: str | None = None
    receipt_verified: bool | None = None
    native_outcome: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "mechanism", _choice(self.mechanism, "completionEvidence.mechanism",
                                                      VALUE_MECHANISMS))
        for name in ("stream_end", "receipt_verified"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, bool):
                raise _fail(f"completionEvidence.{name} must be a boolean or null", field=f"completionEvidence.{name}")
        if self.native_identity is not None and not isinstance(self.native_identity, NativeIdentity):
            raise _fail("completionEvidence.nativeIdentity must be a NativeIdentity")
        object.__setattr__(self, "call_id", _optional_text(self.call_id, "completionEvidence.callId", maximum=512))
        object.__setattr__(self, "event_order", _optional_count(self.event_order, "completionEvidence.eventOrder"))
        object.__setattr__(self, "receipt_ref",
                           _optional_text(self.receipt_ref, "completionEvidence.receiptRef", maximum=1024))
        object.__setattr__(self, "native_outcome",
                           _optional_text(self.native_outcome, "completionEvidence.nativeOutcome", maximum=64))

    def to_payload(self) -> dict:
        return {"mechanism": self.mechanism, "streamEnd": self.stream_end,
                "nativeIdentity": None if self.native_identity is None else self.native_identity.to_payload(),
                "callId": self.call_id, "eventOrder": self.event_order, "receiptRef": self.receipt_ref,
                "receiptVerified": self.receipt_verified, "nativeOutcome": self.native_outcome}

    @classmethod
    def from_payload(cls, value: Any) -> "CompletionEvidence":
        _exact_keys(value, "completionEvidence", {"mechanism", "streamEnd", "nativeIdentity", "callId",
                                                  "eventOrder", "receiptRef", "receiptVerified", "nativeOutcome"})
        return cls(mechanism=value["mechanism"], stream_end=value["streamEnd"],
                   native_identity=None if value["nativeIdentity"] is None
                   else NativeIdentity.from_payload(value["nativeIdentity"]),
                   call_id=value["callId"], event_order=value["eventOrder"], receipt_ref=value["receiptRef"],
                   receipt_verified=value["receiptVerified"], native_outcome=value["nativeOutcome"])


@dataclass(frozen=True)
class DeniedInteraction:
    """One native interaction the harness refused; no tool arguments are kept.

    ``request_id`` stays ``None`` when the harness's own record carried no
    native request id; one is never invented for it.
    """

    method: str
    action: str
    request_id: str | None = None
    native_identity: NativeIdentity | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "method", _text(self.method, "deniedInteractions.method", maximum=128))
        object.__setattr__(self, "action", _text(self.action, "deniedInteractions.action", maximum=32))
        object.__setattr__(self, "request_id",
                           _optional_text(self.request_id, "deniedInteractions.requestId", maximum=128))
        if self.native_identity is not None and not isinstance(self.native_identity, NativeIdentity):
            raise _fail("deniedInteractions.nativeIdentity must be a NativeIdentity")
        object.__setattr__(self, "reason", _optional_text(self.reason, "deniedInteractions.reason", maximum=400))

    def to_payload(self) -> dict:
        return {"requestId": self.request_id, "method": self.method, "action": self.action,
                "nativeIdentity": None if self.native_identity is None else self.native_identity.to_payload(),
                "reason": self.reason}

    @classmethod
    def from_payload(cls, value: Any) -> "DeniedInteraction":
        _exact_keys(value, "deniedInteractions[]", {"requestId", "method", "action", "nativeIdentity", "reason"})
        return cls(method=value["method"], action=value["action"], request_id=value["requestId"],
                   native_identity=None if value["nativeIdentity"] is None
                   else NativeIdentity.from_payload(value["nativeIdentity"]),
                   reason=value["reason"])


@dataclass(frozen=True)
class UnknownEvents:
    """Native events of legal shape that no rule classified; never silently dropped."""

    counts: tuple[tuple[str, int], ...]
    total: int
    truncated: bool = False

    def __post_init__(self) -> None:
        entries = self.counts
        if not isinstance(entries, tuple):
            entries = tuple(entries)
        if len(entries) > MAX_UNKNOWN_EVENT_TYPES:
            raise _fail(f"unknownEvents carries more than {MAX_UNKNOWN_EVENT_TYPES} types",
                        field="unknownEvents.countsByType")
        cleaned: list[tuple[str, int]] = []
        seen: set[str] = set()
        for name, count in entries:
            if name in seen:
                raise _fail("unknownEvents.countsByType repeats an event type",
                            field="unknownEvents.countsByType")
            seen.add(name)
            cleaned.append((_text(name, "unknownEvents.countsByType", maximum=64),
                            _count(count, "unknownEvents.countsByType")))
        object.__setattr__(self, "counts", tuple(cleaned))
        object.__setattr__(self, "total", _count(self.total, "unknownEvents.total"))
        object.__setattr__(self, "truncated", _flag(self.truncated, "unknownEvents.truncated"))
        if sum(count for _name, count in cleaned) != self.total:
            raise _fail("unknownEvents.total must equal the sum of countsByType", field="unknownEvents.total")

    def to_payload(self) -> dict:
        return {"countsByType": {name: count for name, count in self.counts},
                "total": self.total, "truncated": self.truncated}

    @classmethod
    def from_payload(cls, value: Any) -> "UnknownEvents":
        _exact_keys(value, "unknownEvents", {"countsByType", "total", "truncated"})
        entries = value["countsByType"]
        if not isinstance(entries, dict):
            raise _fail("unknownEvents.countsByType must be an object", field="unknownEvents.countsByType")
        return cls(counts=tuple(entries.items()), total=value["total"], truncated=value["truncated"])


@dataclass(frozen=True)
class PolicyFact:
    """One effective-policy fact; only a native readback may claim enforcement."""

    enforcement: str
    requested: FrozenJson | None = None
    reported: FrozenJson | None = None
    basis: str | None = None
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "enforcement", _choice(self.enforcement, "effectivePolicy.enforcement",
                                                        POLICY_ENFORCEMENTS))
        object.__setattr__(self, "requested",
                           _optional_frozen_json(self.requested, "effectivePolicy.requested", maximum=MAX_SCHEMA_BYTES))
        object.__setattr__(self, "reported",
                           _optional_frozen_json(self.reported, "effectivePolicy.reported", maximum=MAX_SCHEMA_BYTES))
        object.__setattr__(self, "basis", _optional_text(self.basis, "effectivePolicy.basis", maximum=128))
        limits = self.limitations
        if not isinstance(limits, tuple):
            limits = tuple(limits)
        if len(limits) > MAX_POLICY_LIMITATIONS:
            raise _fail(f"effectivePolicy.limitations carries more than {MAX_POLICY_LIMITATIONS} entries",
                        field="effectivePolicy.limitations")
        object.__setattr__(self, "limitations",
                           tuple(_text(limit, "effectivePolicy.limitations", maximum=256) for limit in limits))

    def to_payload(self) -> dict:
        return {"requested": None if self.requested is None else self.requested.value,
                "enforcement": self.enforcement,
                "reported": None if self.reported is None else self.reported.value,
                "basis": self.basis, "limitations": list(self.limitations)}

    @classmethod
    def from_payload(cls, value: Any) -> "PolicyFact":
        _exact_keys(value, "effectivePolicy[]", {"requested", "enforcement", "reported", "basis", "limitations"})
        return cls(requested=_optional_frozen_json(value["requested"], "effectivePolicy.requested",
                                                   maximum=MAX_SCHEMA_BYTES),
                   enforcement=value["enforcement"],
                   reported=_optional_frozen_json(value["reported"], "effectivePolicy.reported",
                                                  maximum=MAX_SCHEMA_BYTES),
                   basis=value["basis"], limitations=tuple(value["limitations"]))


@dataclass(frozen=True)
class EffectivePolicy:
    """Effective policy per tools, filesystem and network; honesty over neatness."""

    tools: PolicyFact | None = None
    filesystem: PolicyFact | None = None
    network: PolicyFact | None = None

    def __post_init__(self) -> None:
        for name in ("tools", "filesystem", "network"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, PolicyFact):
                raise _fail(f"effectivePolicy.{name} must be a PolicyFact")

    def to_payload(self) -> dict:
        return {"tools": None if self.tools is None else self.tools.to_payload(),
                "filesystem": None if self.filesystem is None else self.filesystem.to_payload(),
                "network": None if self.network is None else self.network.to_payload()}

    @classmethod
    def from_payload(cls, value: Any) -> "EffectivePolicy":
        _exact_keys(value, "effectivePolicy", {"tools", "filesystem", "network"})
        return cls(tools=None if value["tools"] is None else PolicyFact.from_payload(value["tools"]),
                   filesystem=None if value["filesystem"] is None else PolicyFact.from_payload(value["filesystem"]),
                   network=None if value["network"] is None else PolicyFact.from_payload(value["network"]))


@dataclass(frozen=True)
class ContinuationFacts:
    """The run's native continuation facts; the role decides whether to use them."""

    resumable: bool | None = None
    native_session_ref: str | None = None
    binding_ref: str | None = None
    checkpoint_ref: str | None = None
    basis: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "resumable", _optional_flag(self.resumable, "continuation.resumable"))
        for name in ("native_session_ref", "binding_ref", "checkpoint_ref"):
            object.__setattr__(self, name,
                               _optional_text(getattr(self, name), f"continuation.{name}", maximum=1024))
        object.__setattr__(self, "basis", _optional_text(self.basis, "continuation.basis", maximum=128))

    def to_payload(self) -> dict:
        return {"resumable": self.resumable, "nativeSessionRef": self.native_session_ref,
                "bindingRef": self.binding_ref, "checkpointRef": self.checkpoint_ref, "basis": self.basis}

    @classmethod
    def from_payload(cls, value: Any) -> "ContinuationFacts":
        _exact_keys(value, "continuation facts", {"resumable", "nativeSessionRef", "bindingRef",
                                                  "checkpointRef", "basis"})
        return cls(resumable=value["resumable"], native_session_ref=value["nativeSessionRef"],
                   binding_ref=value["bindingRef"], checkpoint_ref=value["checkpointRef"], basis=value["basis"])


@dataclass(frozen=True)
class StopLayer:
    """One layer's stop facts; unknown is never read as stopped.

    ``group_state`` is ``gone`` only when this layer's own owned observation
    found the group gone, or when the holding side confirmed the spawn never
    happened. An exited leader, a closed stream or an acknowledged interrupt is
    never, by itself, a gone group.
    """

    group_state: str = "unknown"
    started: bool | None = None
    leader_exited: bool | None = None
    exit_code: int | None = None
    observation_basis: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "group_state", _choice(self.group_state, "stopEvidence.groupState", GROUP_STATES))
        for name in ("started", "leader_exited"):
            object.__setattr__(self, name, _optional_flag(getattr(self, name), f"stopEvidence.{name}"))
        object.__setattr__(self, "exit_code", _exit_code(self.exit_code, "stopEvidence.exitCode"))
        object.__setattr__(self, "observation_basis",
                           _optional_text(self.observation_basis, "stopEvidence.observationBasis", maximum=48))

    def to_payload(self) -> dict:
        return {"groupState": self.group_state, "started": self.started, "leaderExited": self.leader_exited,
                "exitCode": self.exit_code, "observationBasis": self.observation_basis}

    @classmethod
    def from_payload(cls, value: Any) -> "StopLayer":
        _exact_keys(value, "stopEvidence layer", {"groupState", "started", "leaderExited", "exitCode",
                                                  "observationBasis"})
        return cls(group_state=value["groupState"], started=value["started"], leader_exited=value["leaderExited"],
                   exit_code=value["exitCode"], observation_basis=value["observationBasis"])


@dataclass(frozen=True)
class InterruptEvidence:
    """A native interrupt: the request and its acknowledgement stay separate.

    A missing record stays ``None``/unknown; only an explicit ``True``/``False``
    from the payload or the collector is a fact.
    """

    requested: bool | None = None
    acknowledged: bool | None = None
    basis: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "requested", _optional_flag(self.requested, "stopEvidence.interrupt.requested"))
        object.__setattr__(self, "acknowledged",
                           _optional_flag(self.acknowledged, "stopEvidence.interrupt.acknowledged"))
        object.__setattr__(self, "basis", _optional_text(self.basis, "stopEvidence.interrupt.basis", maximum=48))

    def to_payload(self) -> dict:
        return {"requested": self.requested, "acknowledged": self.acknowledged, "basis": self.basis}

    @classmethod
    def from_payload(cls, value: Any) -> "InterruptEvidence":
        _exact_keys(value, "stopEvidence.interrupt", {"requested", "acknowledged", "basis"})
        return cls(requested=value["requested"], acknowledged=value["acknowledged"], basis=value["basis"])


@dataclass(frozen=True)
class StopEvidence:
    """Two conservative layers plus the interrupt record."""

    native: StopLayer = field(default_factory=StopLayer)
    controller: StopLayer = field(default_factory=StopLayer)
    interrupt: InterruptEvidence = field(default_factory=InterruptEvidence)

    def __post_init__(self) -> None:
        for name in ("native", "controller"):
            if not isinstance(getattr(self, name), StopLayer):
                raise _fail(f"stopEvidence.{name} must be a StopLayer")
        if not isinstance(self.interrupt, InterruptEvidence):
            raise _fail("stopEvidence.interrupt must be an InterruptEvidence")

    def to_payload(self) -> dict:
        return {"native": self.native.to_payload(), "controller": self.controller.to_payload(),
                "interrupt": self.interrupt.to_payload()}

    @classmethod
    def from_payload(cls, value: Any) -> "StopEvidence":
        _exact_keys(value, "stopEvidence", {"native", "controller", "interrupt"})
        return cls(native=StopLayer.from_payload(value["native"]),
                   controller=StopLayer.from_payload(value["controller"]),
                   interrupt=InterruptEvidence.from_payload(value["interrupt"]))


@dataclass(frozen=True)
class EvidenceRef:
    """One private evidence file's binding, size, digest and retention fact."""

    kind: str
    location: str
    retained: bool = True
    size_bytes: int | None = None
    sha256: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", _text(self.kind, "evidenceRefs.kind", maximum=32))
        object.__setattr__(self, "location", _text(self.location, "evidenceRefs.location", maximum=1024))
        object.__setattr__(self, "retained", _flag(self.retained, "evidenceRefs.retained"))
        object.__setattr__(self, "size_bytes", _optional_count(self.size_bytes, "evidenceRefs.sizeBytes"))
        object.__setattr__(self, "sha256",
                           None if self.sha256 is None else _hex64(self.sha256, "evidenceRefs.sha256"))

    def to_payload(self) -> dict:
        return {"kind": self.kind, "location": self.location, "retained": self.retained,
                "sizeBytes": self.size_bytes, "sha256": self.sha256}

    @classmethod
    def from_payload(cls, value: Any) -> "EvidenceRef":
        _exact_keys(value, "evidenceRefs[]", {"kind", "location", "retained", "sizeBytes", "sha256"})
        return cls(kind=value["kind"], location=value["location"], retained=value["retained"],
                   size_bytes=value["sizeBytes"], sha256=value["sha256"])


def _normalized_package(kind: str, value: Any) -> FrozenJson | None:
    """Validate one already-normalized fact package against its own projection.

    ``None`` stays unknown; a package that its own normalizer cannot accept is
    refused instead of being quietly dropped, so no fact is lost in the codec.
    """
    if value is None:
        return None
    if kind == "activity":
        try:
            normalized = activity_protocol.normalize_activity(value)
        except BoardError as error:
            raise _fail("the activity package is not the canonical projection",
                        reason=str(error.message)[:200]) from None
    elif kind == "usage":
        normalized = usage_protocol.normalize_token_usage(value)
    elif kind == "quota":
        normalized = usage_protocol.normalize_quota(value)
    elif kind == "nativeFailure":
        normalized = usage_protocol.normalize_quota_failure(value)
    elif kind == "lastAssistantMessage":
        normalized = usage_protocol.normalize_last_assistant_message(value)
    else:  # pragma: no cover - the kind set is closed above
        raise _fail(f"unknown package kind {kind}")
    if normalized is None:
        raise _fail(f"the {kind} package is not a usable canonical projection", field=kind)
    return FrozenJson.from_value(normalized, kind, maximum=MAX_PACKAGE_BYTES)


def _validated_tool_evidence(value: Any) -> FrozenJson | None:
    """Structural validation of one existing version-1 tool-evidence package.

    The check is shape only: exact fields, the current version, bounded
    identifiers and ACP categories. No allowance, completeness or publication
    judgment lives here; that stays with the blackboard's single judge.
    """
    if value is None:
        return None
    package = value
    fields = {"version", "binding", "nativeIdentity", "streamComplete", "events",
              "toolCalls", "unsettledToolCalls", "truncated"}
    _exact_keys(package, "toolEvidence", fields)
    if package["version"] != tool_evidence_protocol.TOOL_EVIDENCE_VERSION:
        raise _fail("the tool evidence package version is not supported")
    binding = package["binding"]
    _exact_keys(binding, "toolEvidence.binding", set(tool_evidence_protocol.BINDING_FIELDS))
    if binding["adapter"] not in HARNESS_NAMES:
        raise _fail("the tool evidence binding adapter is not a harness name")
    _text(binding["taskId"], "toolEvidence.binding.taskId", maximum=128)
    _text(binding["attemptId"], "toolEvidence.binding.attemptId", maximum=128)
    if isinstance(binding["generation"], bool) or not isinstance(binding["generation"], int) \
            or binding["generation"] < 0:
        raise _fail("the tool evidence binding generation must be a nonnegative integer")
    for flag in ("streamComplete", "truncated"):
        _flag(package[flag], f"toolEvidence.{flag}")
    _count(package["toolCalls"], "toolEvidence.toolCalls")
    _count(package["unsettledToolCalls"], "toolEvidence.unsettledToolCalls")
    identities = package["nativeIdentity"]
    if not isinstance(identities, list):
        raise _fail("toolEvidence.nativeIdentity must be a list of root identities")
    # An empty list is a real fact: the existing collector publishes failure and
    # unknown packages before any native root was observed. Whether such a
    # package can pass is the role's and the board's judgment, never the
    # structure's.
    for identity in identities:
        if not isinstance(identity, dict) or not identity or set(identity) - set(NATIVE_ID_KEYS):
            raise _fail("a tool evidence root identity is malformed")
        for key, item in identity.items():
            _text(item, f"toolEvidence.nativeIdentity.{key}", maximum=512)
    events = package["events"]
    if not isinstance(events, list) or len(events) > tool_evidence_protocol.MAX_TOOL_EVENTS:
        raise _fail(f"toolEvidence.events must be a list of at most "
                    f"{tool_evidence_protocol.MAX_TOOL_EVENTS} events")
    for event in events:
        _exact_keys(event, "toolEvidence.events[]", set(tool_evidence_protocol.EVENT_FIELDS))
        if not isinstance(event["nativeIdentity"], dict) or not event["nativeIdentity"] \
                or set(event["nativeIdentity"]) - set(NATIVE_ID_KEYS):
            raise _fail("a tool evidence event identity is malformed")
        for key, item in event["nativeIdentity"].items():
            _text(item, f"toolEvidence event {key}", maximum=512)
        _text(event["callId"], "toolEvidence event callId", maximum=512)
        _text(event["toolName"], "toolEvidence event toolName", maximum=512)
        if event["category"] not in tool_evidence_protocol.ACP_CATEGORIES:
            raise _fail("a tool evidence event category is not an ACP category")
        if event["phase"] not in tool_evidence_protocol.TOOL_EVENT_PHASES:
            raise _fail("a tool evidence event phase must be start or end")
    return FrozenJson.from_value(package, "toolEvidence", maximum=MAX_PACKAGE_BYTES)


def _native_error_record(value: Any) -> FrozenJson | None:
    """One harness's own raw native failure record (``{code, kind}`` shape).

    This is a second, separate existing fact beside the whitelist quota
    attribution: ZCode and DSH results carry a raw ``nativeFailure`` record for
    failures that are not quota failures, and it must round-trip instead of
    being folded into the end reason. ``code`` is required and bounded; the
    other entries are bounded scalars.
    """
    if value is None:
        return None
    if isinstance(value, FrozenJson):
        value = value.value
    if not isinstance(value, dict) or "code" not in value:
        raise _fail("nativeError must be an object with a code", field="nativeError")
    for key, item in value.items():
        if item is None or isinstance(item, (bool, int)):
            continue
        _text(item, f"nativeError.{key}", maximum=400 if key == "code" else 64)
    return FrozenJson.from_value(value, "nativeError", maximum=MAX_PACKAGE_BYTES)


@dataclass(frozen=True)
class RunResult:
    """One native run's facts; ``ok`` means the native interaction completed.

    The outer collector adds the outer stop facts and the roles project this
    value into the current AdapterOutcome and board results, so no field here is
    a Worker success, a publishable Router answer or a Host acceptance.
    """

    identity: RunIdentity
    harness: str
    end: RunEnd
    harness_version: str | None = None
    model_started: bool | None = None
    model_start_evidence: ModelStartEvidence = field(
        default_factory=lambda: ModelStartEvidence(basis="unknown"))
    configuration: ResultConfiguration = field(default_factory=ResultConfiguration)
    native_identity: NativeIdentity | None = None
    root_identities: tuple[NativeIdentity, ...] = ()
    value: RunValue | None = None
    completion_evidence: CompletionEvidence | None = None
    tool_evidence: FrozenJson | None = None
    denied_interactions: tuple[DeniedInteraction, ...] = ()
    unknown_events: UnknownEvents | None = None
    effective_policy: EffectivePolicy = field(default_factory=EffectivePolicy)
    activity: FrozenJson | None = None
    usage: FrozenJson | None = None
    quota: FrozenJson | None = None
    native_failure: FrozenJson | None = None
    native_error: FrozenJson | None = None
    last_assistant_message: FrozenJson | None = None
    continuation: ContinuationFacts | None = None
    stop_evidence: StopEvidence = field(default_factory=StopEvidence)
    evidence_refs: tuple[EvidenceRef, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.identity, RunIdentity):
            raise _fail("identity must be a RunIdentity")
        object.__setattr__(self, "harness", _choice(self.harness, "harness", HARNESS_NAMES))
        if not isinstance(self.end, RunEnd):
            raise _fail("end must be a RunEnd")
        object.__setattr__(self, "harness_version",
                           _optional_text(self.harness_version, "harnessVersion", maximum=64))
        object.__setattr__(self, "model_started", _optional_flag(self.model_started, "modelStarted"))
        if not isinstance(self.model_start_evidence, ModelStartEvidence):
            raise _fail("model_start_evidence must be a ModelStartEvidence")
        if not isinstance(self.configuration, ResultConfiguration):
            raise _fail("configuration must be a ResultConfiguration")
        if self.native_identity is not None and not isinstance(self.native_identity, NativeIdentity):
            raise _fail("native_identity must be a NativeIdentity")
        roots = self.root_identities
        if not isinstance(roots, tuple):
            roots = tuple(roots)
        if len(roots) > MAX_ROOT_IDENTITIES:
            raise _fail(f"rootIdentities carries more than {MAX_ROOT_IDENTITIES} roots", field="rootIdentities")
        for root in roots:
            if not isinstance(root, NativeIdentity):
                raise _fail("rootIdentities entries must be NativeIdentity values")
        object.__setattr__(self, "root_identities", roots)
        if self.value is not None and not isinstance(self.value, RunValue):
            raise _fail("value must be a RunValue")
        if self.completion_evidence is not None and not isinstance(self.completion_evidence, CompletionEvidence):
            raise _fail("completion_evidence must be a CompletionEvidence")
        if self.tool_evidence is not None and not isinstance(self.tool_evidence, FrozenJson):
            raise _fail("tool_evidence must be a FrozenJson tool-evidence package")
        denied = self.denied_interactions
        if not isinstance(denied, tuple):
            denied = tuple(denied)
        if len(denied) > MAX_DENIED_INTERACTIONS:
            raise _fail(f"deniedInteractions carries more than {MAX_DENIED_INTERACTIONS} entries",
                        field="deniedInteractions")
        for entry in denied:
            if not isinstance(entry, DeniedInteraction):
                raise _fail("deniedInteractions entries must be DeniedInteraction values")
        object.__setattr__(self, "denied_interactions", denied)
        if self.unknown_events is not None and not isinstance(self.unknown_events, UnknownEvents):
            raise _fail("unknown_events must be an UnknownEvents value")
        if not isinstance(self.effective_policy, EffectivePolicy):
            raise _fail("effective_policy must be an EffectivePolicy")
        for name in ("activity", "usage", "quota", "native_failure", "last_assistant_message"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, FrozenJson):
                raise _fail(f"{name} must be a FrozenJson package")
        # The raw native failure record is validated in every form, so a bare
        # dict without a code is refused at construction, not just at decode.
        object.__setattr__(self, "native_error",
                           _native_error_record(self.native_error.value
                                                if isinstance(self.native_error, FrozenJson)
                                                else self.native_error))
        if self.continuation is not None and not isinstance(self.continuation, ContinuationFacts):
            raise _fail("continuation must be a ContinuationFacts value")
        if not isinstance(self.stop_evidence, StopEvidence):
            raise _fail("stop_evidence must be a StopEvidence")
        refs = self.evidence_refs
        if not isinstance(refs, tuple):
            refs = tuple(refs)
        if len(refs) > MAX_EVIDENCE_REFS:
            raise _fail(f"evidenceRefs carries more than {MAX_EVIDENCE_REFS} entries", field="evidenceRefs")
        for ref in refs:
            if not isinstance(ref, EvidenceRef):
                raise _fail("evidenceRefs entries must be EvidenceRef values")
        object.__setattr__(self, "evidence_refs", refs)

    def to_payload(self) -> dict:
        return {
            "formatVersion": FORMAT_VERSION,
            "identity": self.identity.to_payload(),
            "harness": self.harness,
            "harnessVersion": "unknown" if self.harness_version is None else self.harness_version,
            "end": self.end.to_payload(),
            "modelStarted": self.model_started,
            "modelStartEvidence": self.model_start_evidence.to_payload(),
            "configuration": self.configuration.to_payload(),
            "nativeIdentity": None if self.native_identity is None else self.native_identity.to_payload(),
            "rootIdentities": [root.to_payload() for root in self.root_identities],
            "value": None if self.value is None else self.value.to_payload(),
            "completionEvidence": None if self.completion_evidence is None
            else self.completion_evidence.to_payload(),
            "toolEvidence": None if self.tool_evidence is None else self.tool_evidence.value,
            "deniedInteractions": [entry.to_payload() for entry in self.denied_interactions],
            "unknownEvents": None if self.unknown_events is None else self.unknown_events.to_payload(),
            "effectivePolicy": self.effective_policy.to_payload(),
            "activity": None if self.activity is None else self.activity.value,
            "usage": None if self.usage is None else self.usage.value,
            "quota": None if self.quota is None else self.quota.value,
            "nativeFailure": None if self.native_failure is None else self.native_failure.value,
            "nativeError": None if self.native_error is None else self.native_error.value,
            "lastAssistantMessage": None if self.last_assistant_message is None
            else self.last_assistant_message.value,
            "continuation": None if self.continuation is None else self.continuation.to_payload(),
            "stopEvidence": self.stop_evidence.to_payload(),
            "evidenceRefs": [ref.to_payload() for ref in self.evidence_refs],
        }

    @classmethod
    def from_payload(cls, value: Any) -> "RunResult":
        keys = {"formatVersion", "identity", "harness", "harnessVersion", "end", "modelStarted",
                "modelStartEvidence", "configuration", "nativeIdentity", "rootIdentities", "value",
                "completionEvidence", "toolEvidence", "deniedInteractions", "unknownEvents",
                "effectivePolicy", "activity", "usage", "quota", "nativeFailure", "nativeError",
                "lastAssistantMessage",
                "continuation", "stopEvidence", "evidenceRefs"}
        _exact_keys(value, "run result", keys)
        _exact_version(value["formatVersion"], "run result")
        harness_version = value["harnessVersion"]
        roots = value["rootIdentities"]
        if not isinstance(roots, list):
            raise _fail("rootIdentities must be a list", field="rootIdentities")
        denied = value["deniedInteractions"]
        if not isinstance(denied, list):
            raise _fail("deniedInteractions must be a list", field="deniedInteractions")
        refs = value["evidenceRefs"]
        if not isinstance(refs, list):
            raise _fail("evidenceRefs must be a list", field="evidenceRefs")
        return cls(
            identity=RunIdentity.from_payload(value["identity"]),
            harness=value["harness"],
            end=RunEnd.from_payload(value["end"]),
            harness_version=None if harness_version in (None, "unknown") else harness_version,
            model_started=value["modelStarted"],
            model_start_evidence=ModelStartEvidence.from_payload(value["modelStartEvidence"]),
            configuration=ResultConfiguration.from_payload(value["configuration"]),
            native_identity=None if value["nativeIdentity"] is None
            else NativeIdentity.from_payload(value["nativeIdentity"]),
            root_identities=tuple(NativeIdentity.from_payload(root) for root in roots),
            value=None if value["value"] is None else RunValue.from_payload(value["value"]),
            completion_evidence=None if value["completionEvidence"] is None
            else CompletionEvidence.from_payload(value["completionEvidence"]),
            tool_evidence=_validated_tool_evidence(value["toolEvidence"]),
            denied_interactions=tuple(DeniedInteraction.from_payload(entry) for entry in denied),
            unknown_events=None if value["unknownEvents"] is None
            else UnknownEvents.from_payload(value["unknownEvents"]),
            effective_policy=EffectivePolicy.from_payload(value["effectivePolicy"]),
            activity=_normalized_package("activity", value["activity"]),
            usage=_normalized_package("usage", value["usage"]),
            quota=_normalized_package("quota", value["quota"]),
            native_failure=_normalized_package("nativeFailure", value["nativeFailure"]),
            native_error=_native_error_record(value["nativeError"]),
            last_assistant_message=_normalized_package("lastAssistantMessage", value["lastAssistantMessage"]),
            continuation=None if value["continuation"] is None
            else ContinuationFacts.from_payload(value["continuation"]),
            stop_evidence=StopEvidence.from_payload(value["stopEvidence"]),
            evidence_refs=tuple(EvidenceRef.from_payload(ref) for ref in refs),
        )


def _exact_version(value: Any, label: str) -> None:
    """``formatVersion`` is the exact integer 1: ``True`` and ``1.0`` compare
    equal to ``1`` in Python and are refused all the same."""
    if type(value) is not int or value != FORMAT_VERSION:
        raise _fail(f"the {label} format version is not supported", formatVersion=value)


def _decode_frame(value: str | bytes | Mapping, label: str, maximum: int, build) -> Any:
    """One bounded, strict decode path shared by text, bytes and Mapping forms.

    The Mapping form is canonicalized and bounded exactly like the text form,
    so no spelling of the same frame slips past the size limit; undecodable
    bytes and pathological structures come back as structured errors.
    """
    if isinstance(value, Mapping):
        try:
            text = canonical_json(dict(value))
        except (TypeError, ValueError, RecursionError) as error:
            raise _fail(f"the {label} frame is not bounded JSON", reason=str(error)[:200]) from None
        if len(text.encode()) > maximum:
            raise _fail(f"the {label} frame exceeds its {maximum}-byte bound", limit=maximum)
        try:
            payload = _decode_strict(text)
        except ValueError as error:
            raise _fail(f"the {label} frame is not strict JSON", reason=str(error)[:200]) from None
        return build(payload)
    if isinstance(value, bytes):
        try:
            value = value.decode()
        except UnicodeDecodeError:
            raise _fail(f"the {label} frame is not valid UTF-8", field=label) from None
    if not isinstance(value, str) or len(value.encode()) > maximum:
        raise _fail(f"the {label} frame is missing or exceeds its {maximum}-byte bound", limit=maximum)
    try:
        payload = _decode_strict(value)
    except ValueError as error:
        raise _fail(f"the {label} frame is not strict JSON", reason=str(error)[:200]) from None
    except RecursionError:
        raise _fail(f"the {label} frame nests too deeply", field=label) from None
    return build(payload)


def encode_run_request(request: RunRequest) -> str:
    """Canonical bounded JSON text of one run request."""
    text = canonical_json(request.to_payload())
    if len(text.encode()) > MAX_RUN_REQUEST_BYTES:
        raise _fail("the run request frame exceeds its byte bound", limit=MAX_RUN_REQUEST_BYTES)
    return text


def decode_run_request(value: str | bytes | Mapping) -> RunRequest:
    """Validate and unfreeze one run request from its internal JSON form."""
    return _decode_frame(value, "run request", MAX_RUN_REQUEST_BYTES, RunRequest.from_payload)


def encode_run_result(result: RunResult) -> str:
    """Canonical bounded JSON text of one run result."""
    text = canonical_json(result.to_payload())
    if len(text.encode()) > MAX_RUN_RESULT_BYTES:
        raise _fail("the run result frame exceeds its byte bound", limit=MAX_RUN_RESULT_BYTES)
    return text


def decode_run_result(value: str | bytes | Mapping) -> RunResult:
    """Validate and unfreeze one run result from its internal JSON form."""
    return _decode_frame(value, "run result", MAX_RUN_RESULT_BYTES, RunResult.from_payload)


@dataclass(frozen=True)
class CatalogModelFacts:
    """One discovered native model and its declared efforts.

    Every field the closed names above do not cover (``contextWindow`` and each
    harness's other optional entries) is kept verbatim in ``extra``, so the
    projection back is the original entry, not a smallest common shape.
    """

    id: str
    name: str
    efforts: tuple[str, ...]
    input_modalities: tuple[str, ...] = ("text",)
    description: str = ""
    available: bool = True
    extra: FrozenJson | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _text(self.id, "catalog model id", maximum=128))
        object.__setattr__(self, "name", _text(self.name, "catalog model name", maximum=256))
        efforts = self.efforts
        if not isinstance(efforts, tuple):
            efforts = tuple(efforts)
        if len(efforts) > 16:
            raise _fail("a catalog model carries at most 16 reasoning efforts", field="catalog model efforts")
        object.__setattr__(self, "efforts",
                           tuple(_text(effort, "catalog model effort", maximum=32) for effort in efforts))
        modalities = self.input_modalities
        if not isinstance(modalities, tuple):
            modalities = tuple(modalities)
        if len(modalities) > 8:
            raise _fail("a catalog model carries at most 8 input modalities",
                        field="catalog model inputModalities")
        object.__setattr__(self, "input_modalities",
                           tuple(_text(modality, "catalog model inputModalities", maximum=32)
                                 for modality in modalities))
        description = self.description
        if not isinstance(description, str) or "\0" in description or len(description.encode()) > 2000:
            raise _fail("catalog model description must be a string of at most 2000 UTF-8 bytes",
                        field="catalog model description")
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "available", _flag(self.available, "catalog model available"))
        object.__setattr__(self, "extra", _optional_frozen_json(self.extra, "catalog model extra",
                                                                maximum=MAX_SCHEMA_BYTES))

    def to_payload(self) -> dict:
        payload = {"id": self.id, "name": self.name, "description": self.description,
                   "efforts": list(self.efforts), "inputModalities": list(self.input_modalities),
                   "available": self.available}
        if self.extra is not None:
            payload.update(self.extra.value)
        return payload

    @classmethod
    def from_payload(cls, value: Any) -> "CatalogModelFacts":
        known = {"id", "name", "description", "efforts", "inputModalities", "available"}
        if not isinstance(value, dict) or not known <= set(value):
            raise _fail("a catalog model must carry id, name, description, efforts, "
                        "inputModalities and available")
        rest = {key: value[key] for key in value if key not in known}
        return cls(id=value["id"], name=value["name"], description=value["description"],
                   efforts=tuple(value["efforts"]), input_modalities=tuple(value["inputModalities"]),
                   available=value["available"], extra=FrozenJson(rest) if rest else None)


@dataclass(frozen=True)
class CatalogProviderFacts:
    """One discovered provider and its models; an all-disabled provider may
    carry none."""

    adapter: str
    provider: str
    display_name: str
    models: tuple[CatalogModelFacts, ...] = ()
    package_name: str | None = None
    package_version: str | None = None
    extra: FrozenJson | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter", _choice(self.adapter, "catalog provider adapter", HARNESS_NAMES))
        object.__setattr__(self, "provider", _text(self.provider, "catalog provider", maximum=128))
        object.__setattr__(self, "display_name",
                           _text(self.display_name, "catalog provider displayName", maximum=256))
        models = self.models
        if not isinstance(models, tuple):
            models = tuple(models)
        if len(models) > MAX_CATALOG_MODELS:
            raise _fail(f"a catalog provider carries at most {MAX_CATALOG_MODELS} models",
                        field="catalog provider models")
        for model in models:
            if not isinstance(model, CatalogModelFacts):
                raise _fail("catalog provider models must be CatalogModelFacts values")
        object.__setattr__(self, "models", models)
        object.__setattr__(self, "package_name",
                           _optional_text(self.package_name, "catalog packageName", maximum=128))
        object.__setattr__(self, "package_version",
                           _optional_text(self.package_version, "catalog packageVersion", maximum=64))
        object.__setattr__(self, "extra", _optional_frozen_json(self.extra, "catalog provider extra",
                                                                maximum=MAX_SCHEMA_BYTES))

    def to_payload(self) -> dict:
        payload = {"adapter": self.adapter, "provider": self.provider, "displayName": self.display_name,
                   "packageName": self.package_name, "packageVersion": self.package_version,
                   "models": [model.to_payload() for model in self.models]}
        if self.extra is not None:
            payload.update(self.extra.value)
        return payload

    @classmethod
    def from_payload(cls, value: Any) -> "CatalogProviderFacts":
        known = {"adapter", "provider", "displayName", "packageName", "packageVersion", "models"}
        if not isinstance(value, dict) or not known <= set(value):
            raise _fail("a catalog provider must carry adapter, provider, displayName, packageName, "
                        "packageVersion and models")
        models = value["models"]
        if not isinstance(models, list):
            raise _fail("catalog provider models must be a list")
        rest = {key: value[key] for key in value if key not in known}
        return cls(adapter=value["adapter"], provider=value["provider"], display_name=value["displayName"],
                   package_name=value["packageName"], package_version=value["packageVersion"],
                   models=tuple(CatalogModelFacts.from_payload(model) for model in models),
                   extra=FrozenJson(rest) if rest else None)


@dataclass(frozen=True)
class CatalogFacts:
    """What ``discover`` returns: native model facts, no prompt ever sent.

    The projection mirrors the existing catalog payload of the current
    controllers; :meth:`to_payload` reproduces that exact shape, including an
    empty provider list (a supported complete empty catalog), the omission of
    an empty ``warnings`` list and every field the closed names do not cover.
    """

    adapter: str
    source: str
    harness_version: str
    discovered_at: str
    providers: tuple[CatalogProviderFacts, ...] = ()
    warnings: tuple[str, ...] = ()
    #: Whether the source projection carried the ``warnings`` key at all: an
    #: explicitly empty list is preserved as the empty list it was, while the
    #: projections that omit the key keep omitting it.
    warnings_key_present: bool = False
    extra: FrozenJson | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "adapter", _choice(self.adapter, "catalog adapter", HARNESS_NAMES))
        object.__setattr__(self, "source", _text(self.source, "catalog source", maximum=120))
        object.__setattr__(self, "harness_version", _text(self.harness_version, "catalog harnessVersion", maximum=64))
        object.__setattr__(self, "discovered_at", _text(self.discovered_at, "catalog discoveredAt", maximum=64))
        providers = self.providers
        if not isinstance(providers, tuple):
            providers = tuple(providers)
        if len(providers) > MAX_CATALOG_PROVIDERS:
            raise _fail(f"a catalog carries at most {MAX_CATALOG_PROVIDERS} providers",
                        field="catalog providers")
        for provider in providers:
            if not isinstance(provider, CatalogProviderFacts):
                raise _fail("catalog providers must be CatalogProviderFacts values")
        object.__setattr__(self, "providers", providers)
        warnings = self.warnings
        if not isinstance(warnings, tuple):
            warnings = tuple(warnings)
        if len(warnings) > 16:
            raise _fail("a catalog carries at most 16 warnings", field="catalog warnings")
        object.__setattr__(self, "warnings",
                           tuple(_text(warning, "catalog warnings", maximum=500) for warning in warnings))
        object.__setattr__(self, "extra", _optional_frozen_json(self.extra, "catalog extra",
                                                                maximum=MAX_SCHEMA_BYTES))

    def to_payload(self) -> dict:
        payload = {"source": self.source, "adapter": self.adapter, "harnessVersion": self.harness_version,
                   "discoveredAt": self.discovered_at,
                   "providers": [provider.to_payload() for provider in self.providers]}
        if self.warnings or self.warnings_key_present:
            payload["warnings"] = list(self.warnings)
        if self.extra is not None:
            payload.update(self.extra.value)
        return payload


@runtime_checkable
class HarnessRun(Protocol):
    """The one seam every harness implements (ADR-025 decision 1).

    ``run`` takes one frozen request and returns one factual result. ``observer``
    is the role-owned in-process callback that receives already-normalized,
    retained run facts and returns whether to continue; its observation set is
    placed by step 1-C. ``services`` are the role-held narrow session service
    instances (completion, inquiry, checkpoint); their method surface belongs to
    the roles. ``cancelled`` is the outer cancel check. ``discover`` never sends
    a prompt and takes no input text.
    """

    def run(self, request: RunRequest, *, observer: Callable[[Mapping[str, Any]], bool],
            services: Any, cancelled: Callable[[], bool]) -> RunResult: ...

    def discover(self) -> CatalogFacts: ...


__all__ = [
    "CHECK_BASES", "CONTINUATION_MODES", "END_STATUSES", "FORMAT_VERSION", "FrozenAccountReference",
    "FrozenJson", "GROUP_STATES", "HARNESS_NAMES", "HarnessRun", "MAX_RUN_REQUEST_BYTES",
    "MAX_RUN_RESULT_BYTES", "MODEL_START_BASES", "MAX_COUNT", "CatalogFacts", "CatalogModelFacts",
    "CatalogProviderFacts", "CheckedConfiguration", "CheckedValue", "CompletionEvidence",
    "ContinuationFacts", "DeniedInteraction", "EffectivePolicy", "EvidenceRef", "InterruptEvidence",
    "NativeIdentity", "NetworkPolicy", "POLICY_ENFORCEMENTS", "PrivateStatePaths", "RunBudget",
    "RunConfiguration", "RunContinuation", "RunEnd", "RunIdentity", "RunRequest", "RunResult",
    "RunValue", "SCHEMA_STATUSES", "SessionService", "StopEvidence", "StopLayer", "TOOL_SCOPES",
    "UnknownEvents", "VALUE_MECHANISMS", "canonical_json", "decode_run_request", "decode_run_result",
    "encode_run_request", "encode_run_result",
]
