"""The ADR-025 step 5-A live backend: one C-Two endpoint behind the live seam.

One :class:`CTwoLiveEndpoint` runs inside a run's controller process. It
registers exactly one random-person-name C-Two resource whose contract exposes
the three named live operations — ``request``, ``observe`` and
``capabilities`` — and nothing else: there is no arbitrary method-name
dispatch. One :class:`CTwoLiveChannel` is the :class:`LiveChannel` client the
holding side connects to that endpoint with. The contract class is a trusted
constructor parameter on both sides; the production ``HarnessRunLive`` /
``WorkerRuntimeLive`` contracts are declared by the Host under ``protocol/``
and tests use their own CRM declared beside the peer fixture.

The transport facts follow the accepted F-C1 evidence: routing is by the
address read back through ``cc.server_address()`` after registration, the
person name is display-only and is never an authorization or identity
judgment, ``set_server``/``set_client`` are applied through
:mod:`hey_my_buddy.protocol.rpc_config` before the first register or connect,
and an endpoint that cannot be reached is an unavailable fact — never a
stopped process. Every cross-process frame is one strict pydantic model of the
existing live format plus the two private envelope fields this backend really
reads (``instanceId`` and ``token``) and, on the request frame, the transport
window and original local monotonic cutoff the server enforces at admission.
The narrow token never travels in a
reply, a snapshot, a diagnostic or a log line.

Inside the endpoint the RPC threads only validate and enqueue: requests enter
one bounded queue that the controller's owner loop consumes through
:class:`CTwoLiveEndpoint.consume_request`, and the RPC thread then waits —
inside the caller's transport window, on one standard-library event — for the
owner's :meth:`CTwoLiveEndpoint.settle_request` answer, so no RPC thread ever
touches the native connection and no fact is ever invented from the buffer
insertion: a ``queued`` reply exists only after the owner's own durable
journal commit, an owner refusal keeps the original code and the request
retryable, and a window expiry leaves the unconsumed entry dropped rather
than delivered late. Snapshots serve the bounded latest published facts only
— activity keeps the monotonic projection of
:mod:`hey_my_buddy.protocol.activity`, inquiry states, the observation and
the journal availability fact keep their newest published values — and a read
never starts a native turn or extends any deadline. Every snapshot the
endpoint encodes — pages and point queries alike — is measured against the
64 KiB frame before it leaves, with the overflow reported as an explicit
unavailability instead of a trimmed or undecodable fact. Durable journals and
results stay with the production code that owns them.

On the client side C-Two's native connection and call deadlines enforce one
caller transport window. Expiry ends the client's wait, without cancelling a
dispatched owner operation or implying that the endpoint stopped. The private
request frame carries that same local monotonic deadline so connecting and
queueing cannot give an unconsumed request a second full window.
"""
from __future__ import annotations

import hashlib
import os
import queue
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Annotated, Any, Literal, Mapping, Optional

import c_two as cc
from c_two.error import CallDeadlineExceeded

from ...errors import BoardError
from ...json_codec import canonical_json, decode_bounded_frame, decode_strict_json
from ...protocol import activity as activity_protocol
from ...protocol import rpc_config
from ...protocol.inquiry import DEFAULT_TRANSPORT_TIMEOUT_MS
from ...protocol.internal_models import (
    FrozenJson,
    Hex64,
    Identifier,
    InternalModel,
    JsonTuple,
    NonNegativeInt,
    OptionalText,
    Text,
    check_text,
    fail,
)
from .live import (
    LIVE_FIELDS,
    MAX_CLOSE_REASON_BYTES,
    MAX_INQUIRIES_PER_RUN,
    MAX_LIVE_FRAME_BYTES,
    MAX_OBSERVE_LIMIT,
    MAX_REQUEST_ID,
    MAX_TRANSPORT_TIMEOUT_MS,
    MIN_TRANSPORT_TIMEOUT_MS,
    InquiryState,
    LiveCapabilities,
    LiveJournal,
    LiveObservation,
    LiveReply,
    LiveRequest,
    LiveSnapshot,
)
from .run_contract import RunIdentity
from pydantic import Field

#: The bounded admission buffer between the RPC threads and the owner loop. It
#: never exceeds the per-run inquiry budget, so a full queue is the defensive
#: fact for a stalled owner loop rather than a second inquiry limit.
MAX_PENDING_LIVE_REQUESTS = MAX_INQUIRIES_PER_RUN


class _PendingRequest:
    """One admitted request between its enqueue and the owner's settlement.

    The slot is the whole hand-off: the RPC thread validates and enqueues it,
    then waits on ``done`` inside the caller's transport window; only
    :meth:`CTwoLiveEndpoint.settle_request` — the owner's local answer after
    its own journal commit — sets ``reply``. Nothing about the native side is
    ever inferred from the buffer insertion itself.
    """

    __slots__ = ("question_id", "digest", "deadline", "done", "reply", "expired")

    def __init__(self, question_id: str, digest: str, deadline: float):
        self.question_id = question_id
        self.digest = digest
        self.deadline = deadline
        self.done = threading.Event()
        self.reply: LiveReply | None = None
        self.expired = False


_HEX64 = re.compile(r"^[0-9a-f]{64}$")

#: Random display names for one run's endpoint, drawn once per controller run.
#: A duplicate name on another process is harmless without a relay anchor
#: (F-C1 E2) and the name is never part of any authorization decision.
_PERSON_NAMES = (
    "Ada", "Alan", "Amber", "Ana", "Aria", "Ari", "Arthur", "Asha", "Aurelia", "Axel",
    "Bao", "Bea", "Bo", "Bruno", "Cara", "Cass", "Cecilia", "Cedric", "Chen", "Cleo",
    "Cora", "Dahlia", "Dara", "Dax", "Delia", "Dev", "Diana", "Dora", "Dorian", "Dun",
    "Edith", "Elena", "Elio", "Elsa", "Emrys", "Enzo", "Esme", "Ezra", "Faye", "Fen",
    "Fiona", "Fleur", "Fox", "Gaia", "Gideon", "Gina", "Greta", "Hana", "Harvey", "Hazel",
    "Heidi", "Hera", "Hild", "Ida", "Idris", "Ilya", "Ines", "Iris", "Isolde", "Iva",
    "Ivo", "Jade", "Jalen", "Jasper", "Jia", "Joon", "Juno", "Kai", "Kara", "Keir",
    "Kenzie", "Kestrel", "Kira", "Klaus", "Laleh", "Lena", "Leo", "Lila", "Lin", "Lior",
    "Livia", "Loke", "Lucia", "Lum", "Lyra", "Mabel", "Mae", "Magnus", "Mali", "Manon",
    "Mara", "Marius", "Marlow", "Matis", "Mei", "Milo", "Mira", "Mireille", "Nadia", "Nara",
    "Nell", "Nia", "Noor", "Nox", "Odile", "Ola", "Omar", "Ona", "Orion", "Oskar",
    "Otto", "Paloma", "Petra", "Phoebe", "Piet", "Pim", "Quinn", "Rada", "Rafa", "Remy",
    "Rhea", "Rin", "Romy", "Rook", "Rune", "Saga", "Sage", "Sami", "Sasha", "Selene",
    "Sena", "Sian", "Silas", "Sina", "Sol", "Solveig", "Sonny", "Sora", "Sten", "Suri",
    "Sven", "Talia", "Tamar", "Taro", "Tessa", "Teo", "Thalia", "Theo", "Tilde", "Tove",
    "Ugo", "Una", "Uri", "Valda", "Vera", "Vero", "Vesper", "Vida", "Viggo", "Vinnie",
    "Vita", "Vivi", "Wade", "Wren", "Xu", "Yan", "Yara", "Yuki", "Yusuf", "Zadie",
    "Zane", "Zara", "Zia", "Zola", "Zora", "Zuri",
)

#: The per-component binding of the full run identity, in declared order; the
#: first mismatch names exactly its own component in the refusal reason.
_IDENTITY_COMPONENTS = (
    ("taskId", "task_id"),
    ("attemptId", "attempt_id"),
    ("generation", "generation"),
    ("invocationId", "invocation_id"),
    ("turnId", "turn_id"),
    ("inputSha256", "input_sha256"),
)

#: The inquiry-state values that ride an observed reply, exactly the bridge
#: state set the existing backend maps; anything else stays a refusal.
_REPLY_STATE_STATUSES = {"queued": "queued", "claimed": "queued", "delivered": "delivered",
                         "answered": "answered", "discarded": "discarded"}

LiveField = Literal["activity", "inquiries", "observation"]

#: The transport window as one strict bounded field type, on the one frame
#: whose server side genuinely consumes it.
TransportWindowMs = Annotated[int, Field(ge=MIN_TRANSPORT_TIMEOUT_MS, le=MAX_TRANSPORT_TIMEOUT_MS)]


def random_person_name() -> str:
    """One random person name for a run's endpoint; display use only."""
    return secrets.choice(_PERSON_NAMES)


def _secret(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _HEX64.fullmatch(value):
        raise fail(f"{label} must be a lowercase hex digest of 64 characters", field=label)
    return value


def _timeout_ms(value: Any, label: str) -> int:
    """The preserved transport window bound, checked exactly as the live seam does."""
    if isinstance(value, bool) or not isinstance(value, int) \
            or not MIN_TRANSPORT_TIMEOUT_MS <= value <= MAX_TRANSPORT_TIMEOUT_MS:
        raise fail(f"{label} must be an integer between {MIN_TRANSPORT_TIMEOUT_MS} and "
                   f"{MAX_TRANSPORT_TIMEOUT_MS}", field=label)
    return value


class LiveWireRequest(LiveRequest):
    """One live request frame on the C-Two wire: the request plus the envelope.

    ``timeoutMs`` keeps the validated transport window. ``deadlineMonotonic``
    carries its original cutoff across this same-machine IPC boundary: unlike
    starting another window on arrival, it includes connection and dispatch
    time. It is private to this backend, never a public board-schema field.
    """

    instance_id: Hex64
    token: Hex64
    timeout_ms: TransportWindowMs
    deadline_monotonic: Optional[float] = Field(None, ge=0, allow_inf_nan=False)


class LiveWireObserve(InternalModel):
    """One observe query frame: the envelope plus the selection of sources."""

    instance_id: Hex64
    token: Hex64
    identity: RunIdentity
    after_seq: Optional[NonNegativeInt] = None
    limit: Optional[int] = Field(None, ge=1, le=MAX_OBSERVE_LIMIT)
    fields: Optional[JsonTuple(LiveField, max_items=3)] = None
    inquiry_id: Optional[Identifier] = None


class LiveWireQuery(InternalModel):
    """One capabilities query frame: the envelope alone."""

    instance_id: Hex64
    token: Hex64
    identity: RunIdentity


class LiveEndpointDescriptor(InternalModel):
    """Bounded private readiness facts; the credential is native opaque JSON.

    The holder consumes ``endpointCredential`` through C-Two's codec. Address,
    instance and the held process PID remain independent binding checks.
    Windows has no reap credential and uses the native default pipe domain.
    """

    address: Text(256)
    name: Text(128)
    instance_id: Hex64
    host_pid: NonNegativeInt
    endpoint_credential: OptionalText(8192) = None


class ConfirmedProcessGone(InternalModel):
    """The holder's own evidence that the endpoint's process group vanished.

    ``exitCode`` is the leader's reaped exit code (negative for a POSIX
    signal) and ``groupGone`` is the holder's own group observation; the
    cleanup refuses unless the leader was actually reaped and the group was
    actually observed gone, because an unobservable group is never stopped.
    """

    pid: NonNegativeInt
    exit_code: Optional[int] = Field(None, ge=-(2**31), le=2**32 - 1)
    group_gone: bool


CleanupOutcomeValue = Literal["reaped", "already-absent", "busy", "stale-target",
                              "unverified", "io-error", "not-applicable"]


class CleanupOutcome(InternalModel):
    """The public native reap fact, or a holder binding refusal."""

    outcome: CleanupOutcomeValue
    reason: OptionalText(256) = None


def _carried_facts(entry: InquiryState) -> dict:
    """One entry's facts without its paging position, through the public dump."""
    dump = entry.model_dump()
    dump.pop("seq")
    return dump


def _request_digest(request: LiveRequest) -> str:
    """The existing request digest over the full kind/payload pair."""
    return hashlib.sha256(canonical_json(
        {"kind": request.kind, "payload": request.payload.to_payload()}).encode()).hexdigest()


def _question_reply(entry: InquiryState, *, duplicate: bool) -> LiveReply:
    """One duplicate's reply from the question's own current published state.

    The reply never re-enqueues and never upgrades the state: whatever the
    entry last published is the fact, the entry's own committed withdrawal
    reason and delivery record ride along in the correlation exactly as the
    existing ask value reads them, and an unrecognized state stays a refusal
    instead of being projected onto the reply set.
    """
    correlation: dict[str, Any] = {"questionId": entry.question_id, "duplicate": duplicate}
    if entry.reason is not None:
        correlation["reason"] = entry.reason
    if entry.delivery is not None:
        correlation["delivery"] = entry.delivery.value
    status = _REPLY_STATE_STATUSES.get(entry.status)
    if status is None:
        return LiveReply(status="unavailable", reason_code="question-state-unknown")
    return LiveReply(status=status, observed=True, state=entry.status,
                     native_correlation=correlation)


def _selected_fields(fields: Any) -> frozenset:
    if fields is None:
        return frozenset(LIVE_FIELDS)
    if isinstance(fields, str) or not isinstance(fields, (list, tuple, set, frozenset)):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    selected = frozenset(fields)
    if not selected <= frozenset(LIVE_FIELDS):
        raise fail(f"fields must be a selection of {', '.join(LIVE_FIELDS)}", field="fields")
    return selected or frozenset(LIVE_FIELDS)


def _observe_arguments(after_seq: Any, limit: Any, fields: Any, inquiry_id: Any) -> dict:
    """The observe arguments validated exactly as the live seam validates them."""
    if inquiry_id is not None:
        if not isinstance(inquiry_id, str) or not inquiry_id \
                or len(inquiry_id.encode()) > MAX_REQUEST_ID:
            raise fail("inquiryId must be a nonempty string of at most "
                       f"{MAX_REQUEST_ID} bytes", field="inquiryId")
        return {"inquiry_id": inquiry_id}
    if after_seq is not None and (isinstance(after_seq, bool) or not isinstance(after_seq, int)
                                  or after_seq < 0):
        raise fail("afterSeq must be null or a nonnegative integer", field="afterSeq")
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_OBSERVE_LIMIT:
        raise fail(f"limit must be an integer between 1 and {MAX_OBSERVE_LIMIT}", field="limit")
    selected = _selected_fields(fields)
    return {"after_seq": after_seq, "limit": limit,
            "fields": tuple(selected) if selected != frozenset(LIVE_FIELDS) else None}


def write_ready_material(path: str | Path, descriptor: LiveEndpointDescriptor) -> None:
    """Publish one endpoint descriptor as a fresh 0600 file with no links.

    The file is created exclusively: a pre-existing entry at the path — a
    link included — is refused instead of being followed or overwritten.
    """
    if not isinstance(descriptor, LiveEndpointDescriptor):
        raise fail("descriptor must be a LiveEndpointDescriptor value")
    text = canonical_json(descriptor.to_payload())
    if len(text.encode()) > 16384:
        raise fail("live readiness material exceeds its frame bound")
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        os.write(fd, text.encode())
    finally:
        os.close(fd)


def cleanup_owned_endpoint(descriptor: LiveEndpointDescriptor,
                           evidence: ConfirmedProcessGone, *,
                           context: cc.LocalEndpointContext | None = None) -> CleanupOutcome:
    """Reap once through C-Two only after the held leader and group are gone.

    The supplied context is the holder's configured private domain, never a
    domain derived from the untrusted readiness credential. No file paths or
    native identity fields are constructed here; C-Two owns the codec and reap.
    """
    if not isinstance(descriptor, LiveEndpointDescriptor):
        raise fail("descriptor must be a LiveEndpointDescriptor value")
    if not isinstance(evidence, ConfirmedProcessGone):
        raise fail("evidence must be a ConfirmedProcessGone value")
    if evidence.exit_code is None or evidence.group_gone is not True:
        return CleanupOutcome(outcome="unverified", reason="vanishing-not-confirmed")
    if evidence.pid != descriptor.host_pid:
        return CleanupOutcome(outcome="unverified", reason="process-identity-mismatch")
    if descriptor.endpoint_credential is None:
        return CleanupOutcome(outcome="not-applicable" if os.name == "nt" else "unverified",
                              reason="endpoint-credential-unavailable")
    try:
        credential = cc.EndpointCredential.from_json(descriptor.endpoint_credential)
        trusted = context if context is not None else cc.local_endpoint_context()
    except (ValueError, TypeError):
        return CleanupOutcome(outcome="unverified", reason="endpoint-credential-invalid")
    if credential.address != descriptor.address:
        return CleanupOutcome(outcome="unverified", reason="endpoint-address-mismatch")
    if (credential.context.platform, credential.context.namespace_id, credential.context.root) != \
            (trusted.platform, trusted.namespace_id, trusted.root):
        return CleanupOutcome(outcome="unverified", reason="endpoint-domain-mismatch")
    result = cc.reap_endpoint(descriptor.address, credential, context=trusted)
    return CleanupOutcome(outcome=result["status"], reason=result.get("reason"))


class CTwoLiveEndpoint:
    """The server half of the step 5-A backend, owned by one run's controller.

    The three CRM methods are the whole wire surface. ``request`` validates the
    frame, binds the instance, the token and every component of the run
    identity, then admits the question into one bounded queue for the owner
    loop and waits for that owner's :meth:`settle_request` answer within the
    caller's transport window — an RPC thread never writes the native
    connection, no inquiry state exists before the owner's commit, and a
    window expiry drops the unconsumed entry instead of delivering it late.
    Both admission indexes — the settled questions and the settled request
    ids, aliases included — are bounded by the per-run budget of 32 with no
    eviction, so a new request id past the budget is an explicit
    ``request-limit`` refusal while every known id stays idempotent.
    ``observe`` serves the bounded latest published facts read-only, journal
    availability fact included as the owner published it, and ``capabilities``
    serves the delivery fact the controller declared. ``stop`` is the clean
    endpoint lifecycle — unregister this resource, then shut the process's
    C-Two server down — which is what makes this run's socket file disappear.
    """

    def __init__(self, identity: RunIdentity, capabilities: LiveCapabilities, contract: type, *,
                 name: str | None = None, instance_id: str | None = None,
                 token: str | None = None, state_dir: str | Path | None = None):
        if not isinstance(identity, RunIdentity):
            raise fail("identity must be a RunIdentity")
        if not isinstance(capabilities, LiveCapabilities):
            raise fail("capabilities must be a LiveCapabilities value")
        if not isinstance(contract, type):
            raise fail("contract must be a CRM class")
        for operation in ("capabilities", "request", "observe"):
            if not callable(getattr(contract, operation, None)):
                raise fail(f"the live contract must declare a {operation} operation")
        self._state_dir = rpc_config.resolve_state_dir(state_dir)
        self._identity = identity
        self._capabilities = capabilities
        self._contract = contract
        self._name = check_text(name if name is not None else random_person_name(),
                                "name", maximum=128)
        self._instance_id = _secret(instance_id if instance_id is not None else secrets.token_hex(32),
                                    "instanceId")
        self._token = _secret(token if token is not None else secrets.token_hex(32), "token")
        self._lock = threading.RLock()
        self._queue: queue.Queue[tuple[LiveRequest, _PendingRequest]] = \
            queue.Queue(maxsize=MAX_PENDING_LIVE_REQUESTS)
        #: The committed admission indexes of the existing contract, bounded by
        #: the per-run budget of 32: one payload digest per settled question
        #: and one question per settled request id. Nothing is ever evicted —
        #: an old request id keeps its replay-or-conflict binding forever — so
        #: a NEW request id past the budget is refused as ``request-limit``
        #: while every already-known id stays idempotent. The public CLI's 32
        #: inquiries are unchanged; this is the same budget applied to the
        #: internal transport's distinct request ids, which the production
        #: caller derives from its inquiry ids anyway.
        self._admitted: dict[str, str] = {}
        self._requests: dict[str, str] = {}
        #: The in-flight hand-offs, keyed by request id. Each slot exists from
        #: its enqueue until the owner settles it (or it is dropped expired),
        #: so the pending count is bounded by the admission queue beside it.
        self._pending: dict[str, _PendingRequest] = {}
        self._entries: tuple[InquiryState, ...] = ()
        self._next_seq = 1
        self._activity = None
        self._observation: LiveObservation | None = None
        #: The observation read facts exactly as the owner last published
        #: them — ``(observed, reason, error)`` — or ``None`` before any read:
        #: the endpoint then reports the existing no-source fact instead of
        #: inferring success from its own buffer.
        self._observation_read: tuple[bool, str | None, str | None] | None = None
        self._journal: LiveJournal | None = None
        self._closed_reason: str | None = None
        self._descriptor: LiveEndpointDescriptor | None = None
        self._stopped = False

    @property
    def identity(self) -> RunIdentity:
        return self._identity

    @property
    def instance_id(self) -> str:
        """The endpoint's instance id; a display-safe fact, never the token."""
        return self._instance_id

    def start(self) -> LiveEndpointDescriptor:
        """Apply both C-Two profiles, register the resource, read the address back.

        ``configure_server`` and ``configure_client`` both run before the first
        register — a late call is silently ignored by C-Two (F-C1 E4), so the
        ordering here is a hard precondition, not a preference. The returned
        descriptor carries the address read back through
        ``cc.server_address()``; it never carries the token.
        """
        if self._descriptor is not None:
            raise fail("the live endpoint was already started")
        rpc_config.configure_server(self._state_dir)
        rpc_config.configure_client(self._state_dir)
        try:
            cc.register(self._contract, self, name=self._name,
                        concurrency=cc.ConcurrencyConfig(mode=cc.ConcurrencyMode.PARALLEL))
        except (ValueError, RuntimeError) as error:
            raise BoardError("PRIVATE_PATH_UNSAFE", str(error)) from error
        address = cc.server_address()
        if not isinstance(address, str) or not address:
            raise fail("the registered live endpoint reported no server address")
        inspected = cc.inspect_endpoint(address)
        credential = inspected["credential"]
        if inspected["status"] != "present" and inspected["status"] != "not-applicable":
            raise BoardError("LIVE_UNAVAILABLE", "Registered endpoint credential is unavailable",
                             reason=inspected["status"])
        self._descriptor = LiveEndpointDescriptor(
            address=check_text(address, "address", maximum=256),
            name=self._name, instance_id=self._instance_id, host_pid=os.getpid(),
            endpoint_credential=credential.to_json() if credential is not None else None)
        return self._descriptor

    def consume_request(self, timeout_s: float) -> LiveRequest | None:
        """One owner-loop consumption from the bounded admission queue.

        ``0`` is the original non-blocking drain — the owner's native poll
        empties what is already queued without waiting — and a positive value
        is one total wait window. A dead entry is dropped instead of handed
        over: one whose window expired unconsumed, one already settled, one
        closed with its waiters woken, or one whose slot no longer belongs to
        the current pending mapping. A filled refusal reply never lifts that
        expiry gate.
        """
        deadline = time.monotonic() + max(0.0, float(timeout_s))
        while True:
            remaining = deadline - time.monotonic()
            try:
                if remaining <= 0:
                    request, slot = self._queue.get_nowait()
                else:
                    request, slot = self._queue.get(timeout=remaining)
            except queue.Empty:
                return None
            with self._lock:
                if time.monotonic() >= slot.deadline:
                    slot.expired = True
                if slot.expired or slot.reply is not None \
                        or self._pending.get(request.request_id) is not slot:
                    self._forget_slot(slot)
                    continue
            return request

    def settle_request(self, request_id: str, reply: LiveReply) -> None:
        """The owner's settlement of one consumed request — a local method.

        This is the one answer mechanism, not a new RPC operation: the owner
        loop calls it with the fact its own side actually achieved. A settled
        observation (``queued`` at the earliest, after the native bridge's
        durable journal commit) binds every request id that joined the
        delivery — each within the budget, so every alias keeps the
        replay-or-conflict gate — publishes the question's first inquiry
        state only when the owner has not already published a richer one from
        the journal callback, and never overwrites that published state; a
        settled refusal clears the slot untouched, so the unsuccessful
        request stays retryable exactly as the existing bridges allow.
        """
        if not isinstance(reply, LiveReply):
            raise fail("reply must be a LiveReply value")
        with self._lock:
            slot = self._pending.get(request_id)
            if slot is None or slot.reply is not None:
                return
            aliases = [pending_id for pending_id, other in self._pending.items()
                       if other is slot]
            if reply.observed:
                status = {None: None, "claimed": "queued"}.get(reply.state, reply.state) \
                    or reply.status
                for pending_id in aliases:
                    self._requests[pending_id] = slot.question_id
                self._admitted[slot.question_id] = slot.digest
                if self._question_entry(slot.question_id) is None:
                    self._merge_states((InquiryState(question_id=slot.question_id,
                                                     status=status),))
                if reply.native_correlation is None:
                    # The settled fact identifies its question the way the
                    # existing ask value does; the owner's own correlation,
                    # when it carries one, stays untouched.
                    reply = LiveReply(**reply.model_dump(exclude={"native_correlation"}),
                                      native_correlation={"questionId": slot.question_id,
                                                          "duplicate": False})
            slot.reply = reply
            slot.done.set()
            for pending_id in aliases:
                self._pending.pop(pending_id, None)

    def publish_activity(self, payload: Mapping[str, Any]) -> bool:
        """Keep one newer canonical activity receipt; the bounded latest only.

        The merge rule is the activity protocol's own: normalize, then keep the
        candidate only when it advances the projection — an identical repeat is
        idempotent and an older receipt never replaces a newer one.
        """
        normalized = activity_protocol.normalize_activity(payload)
        with self._lock:
            previous = self._activity.value if self._activity is not None else None
            if not activity_protocol.is_newer(normalized, previous):
                return False
            self._activity = FrozenJson.from_value(
                normalized, "activity", maximum=MAX_LIVE_FRAME_BYTES // 2)
            return True

    def publish_inquiry_state(self, state: InquiryState) -> None:
        """Publish one question's newest facts; a change takes a fresh seq."""
        if not isinstance(state, InquiryState):
            raise fail("state must be an InquiryState value")
        with self._lock:
            self._merge_states((state,))

    def publish_observation(self, observation: LiveObservation | Mapping[str, Any]) -> None:
        """Keep one bounded observation value; a success read establishes it.

        A published value is the owner's own successful native read, so it
        carries the ``observed=True`` fact with it; the value is the newest
        published only and nothing is inferred beside it.
        """
        if isinstance(observation, Mapping):
            observation = LiveObservation.from_payload(dict(observation))
        if not isinstance(observation, LiveObservation):
            raise fail("observation must be a LiveObservation value")
        with self._lock:
            self._observation = observation
            self._observation_read = (True, None, None)

    def publish_snapshot(self, snapshot: LiveSnapshot | Mapping[str, Any]) -> None:
        """Adopt one owner-published snapshot's read facts — a local method.

        The owner's actual observation read outcome rides in the snapshot's
        own ``observed``/``reason``/``error`` fields exactly as the existing
        channel reports them: a failed read is preserved verbatim, keeps the
        last activity untouched and serves no look-alike observation value,
        while a carried observation value or journal fact is adopted as its
        own publication would be. The inquiries and the paging position are
        the owner's own sequence of settled publications and never ride here.
        """
        if isinstance(snapshot, Mapping):
            snapshot = LiveSnapshot.from_payload(dict(snapshot))
        if not isinstance(snapshot, LiveSnapshot):
            raise fail("snapshot must be a LiveSnapshot value")
        with self._lock:
            if snapshot.observed is not None:
                self._observation_read = (snapshot.observed, snapshot.reason, snapshot.error)
                if not snapshot.observed:
                    self._observation = None
            if snapshot.observation is not None:
                self._observation = snapshot.observation
                if snapshot.observed is None:
                    self._observation_read = (True, None, None)
            if snapshot.journal is not None:
                self._journal = snapshot.journal

    def publish_journal(self, journal: LiveJournal | Mapping[str, Any]) -> None:
        """Keep the durable journal's own availability fact; the owner's input only.

        The journal projection is exactly what the owner's existing locked
        read produced — availability, reason, its own deduplicated count and
        the per-question rejections its identity binding refused. Nothing is
        inferred here, no zero is invented for an unread journal and the fact
        is never mixed into the inquiry states: an endpoint whose owner has
        published nothing simply carries no journal fact.
        """
        if isinstance(journal, Mapping):
            journal = LiveJournal.from_payload(dict(journal))
        if not isinstance(journal, LiveJournal):
            raise fail("journal must be a LiveJournal value")
        with self._lock:
            self._journal = journal

    def close(self, *, reason: str) -> None:
        """Refuse further requests, wake every waiter; published facts stay readable."""
        check_text(reason, "reason", maximum=MAX_CLOSE_REASON_BYTES)
        with self._lock:
            if self._closed_reason is None:
                self._closed_reason = reason
            for request_id, slot in list(self._pending.items()):
                if slot.reply is None:
                    # The owner is gone: the waiters learn the channel closed
                    # instead of hanging, and their unconsumed entries die as
                    # expired rather than reaching the native side.
                    slot.expired = True
                    slot.reply = LiveReply(status="unavailable", reason_code="channel-closed")
                    slot.done.set()
                self._pending.pop(request_id, None)

    def stop(self) -> None:
        """The clean lifecycle: close, unregister this resource, shut the server down.

        The controller process hosts exactly this one C-Two resource, so
        shutting the process's server down unloads this run's socket file —
        the disappearance is C-Two's own, observed in F-C1 E1.
        """
        if self._stopped:
            return
        self.close(reason="endpoint-stopped")
        cc.unregister(self._name)
        cc.shutdown()
        self._stopped = True

    # -- the three named CRM operations --------------------------------------

    def capabilities(self, request_json: str) -> str:
        """Serve the declared delivery fact; refusals raise, there is no carrier."""
        frame, refusal = self._authenticate_frame(request_json, LiveWireQuery)
        if refusal is not None:
            raise fail("the live capabilities call was refused", reason=refusal)
        return canonical_json(self._capabilities.to_payload())

    def request(self, request_json: str) -> str:
        """Validate, bind and admit one live request, then await its settlement.

        The RPC thread's own work ends at the bounded queue: the request waits
        for the owner's :meth:`settle_request` answer inside the caller's
        transport window and reports exactly what the owner settled — a
        journal-committed ``queued`` fact, the owner's own refusal with its
        original code, or an explicit window expiry. No inquiry state exists
        before the owner's commit, and an entry the owner has not consumed
        within its window is dropped instead of triggering a late native
        delivery.
        """
        started = time.monotonic()
        frame, refusal = self._authenticate_frame(request_json, LiveWireRequest)
        if refusal is not None or frame is None:
            return self._refusal_reply(refusal or "frame-invalid")
        deadline = started + frame.timeout_ms / 1000.0
        if frame.deadline_monotonic is not None:
            deadline = min(deadline, frame.deadline_monotonic)
        question_id = frame.payload.question_id
        digest = _request_digest(frame)
        with self._lock:
            if self._closed_reason is not None:
                return self._refusal_reply("channel-closed")
            pending = self._pending.get(frame.request_id)
            if pending is not None:
                # The same request id is already in flight: a changed payload
                # conflicts now, an identical one joins the same delivery.
                if pending.digest != digest:
                    return self._refusal_reply("request-payload-conflict")
                slot = pending
            else:
                bound_question = self._requests.get(frame.request_id)
                if bound_question is not None:
                    # A committed request id is bound over its whole payload: an
                    # identical replay answers from the bound question's own
                    # state and a changed payload — a different question id
                    # included — is a conflict before anything is enqueued.
                    if self._admitted.get(bound_question) != digest:
                        return self._refusal_reply("request-payload-conflict")
                    entry = self._question_entry(bound_question)
                    if entry is None:  # pragma: no cover - a settled commit publishes first
                        return self._refusal_reply("question-state-unknown")
                    return canonical_json(_question_reply(entry, duplicate=True).to_payload())
                slot = None
                for other in self._pending.values():
                    # The same question already in flight under another request
                    # id: join that delivery rather than enqueue a second one —
                    # the join is itself a budgeted alias, so the settled
                    # binding covers it and its conflict gate holds.
                    if other.question_id == question_id:
                        if other.digest != digest:
                            return self._refusal_reply("question-payload-conflict")
                        if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                            return self._refusal_reply("request-limit")
                        slot = other
                        self._pending[frame.request_id] = slot
                        break
                if slot is None:
                    admitted_digest = self._admitted.get(question_id)
                    if admitted_digest is not None:
                        # The question is already committed: the same payload
                        # under a fresh request id is a budgeted alias — a
                        # duplicate without a second delivery — and a different
                        # payload under it is the question's own conflict.
                        if admitted_digest != digest:
                            return self._refusal_reply("question-payload-conflict")
                        if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                            return self._refusal_reply("request-limit")
                        self._requests[frame.request_id] = question_id
                        entry = self._question_entry(question_id)
                        if entry is None:  # pragma: no cover - a settled commit publishes first
                            return self._refusal_reply("question-state-unknown")
                        return canonical_json(_question_reply(entry, duplicate=True).to_payload())
                    if self._capabilities.inquiry_delivery == "unsupported":
                        return canonical_json(LiveReply(
                            status="unsupported", reason_code="inquiry-unsupported").to_payload())
                    if len(self._admitted) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                        return self._refusal_reply("inquiry-limit")
                    if len(self._requests) + len(self._pending) >= MAX_INQUIRIES_PER_RUN:
                        return self._refusal_reply("request-limit")
                    if time.monotonic() >= deadline:
                        return self._refusal_reply("request-window-expired")
                    slot = _PendingRequest(question_id, digest, deadline)
                    try:
                        # The queue carries the plain request without the
                        # envelope beside its slot: the owner loop never holds
                        # the token this frame authenticated with.
                        self._queue.put_nowait(
                            (LiveRequest(identity=frame.identity, request_id=frame.request_id,
                                         kind=frame.kind, payload=frame.payload), slot))
                    except queue.Full:
                        return self._refusal_reply("queue-full")
                    self._pending[frame.request_id] = slot
        if not slot.done.wait(timeout=max(0.0, deadline - time.monotonic())):
            with self._lock:
                if slot.reply is None:
                    # The window passed without the owner's answer: the caller
                    # learns the expiry, and the unconsumed queue entry dies
                    # with the slot instead of reaching the native side late.
                    slot.expired = True
            return self._refusal_reply("request-window-expired")
        reply = slot.reply
        if reply is None:  # pragma: no cover - every wake sets a reply
            return self._refusal_reply("channel-closed")
        return canonical_json(reply.to_payload())

    def observe(self, request_json: str) -> str:
        """Serve one bounded read-only page of the latest published facts."""
        frame, refusal = self._authenticate_frame(request_json, LiveWireObserve)
        if refusal is not None or frame is None:
            return canonical_json(LiveSnapshot(unavailable=refusal or "frame-invalid",
                                               observed=False,
                                               reason=refusal or "frame-invalid").to_payload())
        with self._lock:
            unavailable: list[str] = []
            if self._closed_reason is not None:
                unavailable.append("channel-closed")
            if frame.inquiry_id is not None:
                entry = self._question_entry(frame.inquiry_id)
                if entry is None:
                    return canonical_json(LiveSnapshot(
                        unavailable="+".join(unavailable) or None, observed=True).to_payload())
                snapshot = LiveSnapshot(inquiries=(entry,),
                                        unavailable="+".join(unavailable) or None, observed=True)
                if _frame_fits(snapshot):
                    return canonical_json(snapshot.to_payload())
                return _oversized_snapshot_reply()
            if frame.limit is None:
                return canonical_json(LiveSnapshot(
                    unavailable="frame-invalid", observed=False,
                    reason="frame-invalid").to_payload())
            selected = frozenset(frame.fields) if frame.fields else frozenset(LIVE_FIELDS)
            activity = self._activity if "activity" in selected else None
            journal = self._journal if "inquiries" in selected else None
            if "observation" in selected:
                observed, reason, error, observation = self._observation_facts()
            else:
                # The selection did not read the observation, so its read
                # facts — a published failure included — are not carried.
                observed, reason, error, observation = None, None, None, None
            # The paging bound covers the first candidate and the whole final
            # frame: every probe carries the snapshot's own worst-case small
            # fields — the longer ``false`` literal, the journal fact, the
            # observation read facts and the availability text with the
            # overflow reason appended — so a page that passes the probe
            # passes the final encoding beside it.
            probe_unavailable = "+".join([*unavailable, "frame-too-large"]) or None
            page: list[InquiryState] = []
            matching: list[InquiryState] = []
            overflow = False
            if "inquiries" in selected:
                matching = [entry for entry in self._entries
                            if frame.after_seq is None or entry.seq > frame.after_seq]
                for entry in matching:
                    if len(page) >= frame.limit:
                        break
                    if not _frame_fits(LiveSnapshot(
                            activity=activity, inquiries=tuple(page + [entry]),
                            observation=observation, journal=journal,
                            unavailable=probe_unavailable, truncated=False, observed=observed,
                            reason=reason, error=error)):
                        overflow = True
                        break
                    page.append(entry)
            truncated = len(page) < len(matching)
            final_unavailable = probe_unavailable if overflow and not page \
                else ("+".join(unavailable) or None)
            snapshot = LiveSnapshot(activity=activity, inquiries=tuple(page),
                                    observation=observation, journal=journal,
                                    unavailable=final_unavailable, truncated=truncated,
                                    observed=observed, reason=reason, error=error)
            if _frame_fits(snapshot):
                return canonical_json(snapshot.to_payload())
            return _oversized_snapshot_reply()

    # -- internals ------------------------------------------------------------

    def _authenticate_frame(self, request_json: Any, model: type):
        """Decode one frame and bind the instance, the token and the identity.

        Returns the validated frame with ``None``, or ``None`` with the refusal
        reason code the caller reports — the narrow token itself never appears
        in any refusal.
        """
        return authenticate_live_frame(request_json, model, identity=self._identity,
                                       instance_id=self._instance_id, token=self._token)

    def _question_entry(self, question_id: str) -> InquiryState | None:
        for entry in self._entries:
            if entry.question_id == question_id:
                return entry
        return None

    def _forget_slot(self, slot: "_PendingRequest") -> None:
        """Drop every pending mapping that still points at one dead slot."""
        for pending_id in [pending_id for pending_id, other in self._pending.items()
                           if other is slot]:
            self._pending.pop(pending_id, None)

    def _observation_facts(self) -> tuple[bool, str | None, str | None, LiveObservation | None]:
        """The observation read facts, exactly the existing channel's three.

        A never-published read is the existing no-source fact — observed
        ``False`` with ``observation-unavailable`` — never an inferred
        success; an owner-published failure carries its own transport
        classification and specific code verbatim and serves no look-alike
        observation value; an owner-published value is a successful read.
        """
        if self._observation_read is not None:
            observed, reason, error = self._observation_read
            return observed, reason, error, self._observation if observed else None
        if self._observation is not None:
            return True, None, None, self._observation
        return False, "observation-unavailable", None, None

    def _merge_states(self, states: tuple[InquiryState, ...]) -> tuple[InquiryState, ...]:
        """Fold states into the entries; a change takes a fresh seq.

        The mechanism is the live seam's own: a question keeps its sequence
        while every carried fact is unchanged and takes a fresh, higher one
        whenever any of them changes, so an observer that has read up to a
        sequence sees exactly the changes after it. Unlike the journal merge,
        one call may carry a single question's new fact beside untouched
        others, so the untouched entries always survive it.
        """
        merged = list(self._entries)
        position = {entry.question_id: index for index, entry in enumerate(merged)}
        for state in states:
            index = position.get(state.question_id)
            if index is not None:
                if _carried_facts(merged[index]) == _carried_facts(state):
                    continue
                merged[index] = state.model_copy(update={"seq": self._next_seq})
            else:
                merged.append(state.model_copy(update={"seq": self._next_seq}))
                position[state.question_id] = len(merged) - 1
            self._next_seq += 1
        self._entries = tuple(sorted(merged, key=lambda entry: entry.seq))
        return self._entries

    def _refusal_reply(self, reason_code: str) -> str:
        return canonical_json(
            LiveReply(status="unavailable", reason_code=reason_code).to_payload())


def authenticate_live_frame(request_json: Any, model: type, *, identity: RunIdentity,
                            instance_id: str, token: str):
    """The same bounded frame and complete binding validation at both live hops."""
    value, refusal = decode_live_wire_frame(request_json)
    if refusal is not None:
        return None, refusal
    try:
        frame = model.from_payload(value)
    except BoardError:
        return None, "frame-invalid"
    if frame.instance_id != instance_id:
        return None, "instance-mismatch"
    if not secrets.compare_digest(frame.token, token):
        return None, "token-mismatch"
    for wire_name, field_name in _IDENTITY_COMPONENTS:
        if getattr(frame.identity, field_name) != getattr(identity, field_name):
            return None, f"identity-mismatch:{wire_name}"
    return frame, None


def decode_live_wire_frame(raw: Any) -> tuple[Any, str | None]:
    """The strict bounded decode every inbound frame passes first."""
    if not isinstance(raw, str):
        return None, "frame-invalid"
    if len(raw.encode()) > MAX_LIVE_FRAME_BYTES:
        return None, "frame-too-large"
    try:
        return decode_strict_json(raw), None
    except (ValueError, RecursionError):
        return None, "frame-invalid"


def _frame_fits(snapshot: LiveSnapshot) -> bool:
    """The paging bound: one whole candidate frame within the 64 KiB limit."""
    return len(canonical_json(snapshot.to_payload()).encode()) <= MAX_LIVE_FRAME_BYTES


def _oversized_snapshot_reply() -> str:
    """The explicit fact when even an empty page cannot fit one frame.

    The facts are not trimmed to squeeze through: the read reports the frame
    bound it could not meet, the caller narrows the field selection or pages
    the inquiries alone, and every published fact stays reachable that way.
    """
    return canonical_json(LiveSnapshot(unavailable="frame-too-large", observed=False,
                                       reason="frame-too-large").to_payload())


class CTwoLiveChannel:
    """The client half of the step 5-A backend: one :class:`LiveChannel` by address.

    Each call opens one fresh C-Two connection to the endpoint's address — the
    measured production shape — with the display name only selecting the
    resource at that address. The whole connect and call runs under the
    caller's transport window through C-Two's native deadlines, so a peer
    that never answers returns within the window instead
    of hanging the holder. A dead, unreachable or stalling endpoint is always
    an unavailable fact; nothing here ever reports a process as stopped.
    Closing the channel is local: it changes no result ownership, stops
    nothing and never waits for an in-flight call.
    """

    def __init__(self, identity: RunIdentity, contract: type, *, name: str, address: str,
                 instance_id: str, token: str, state_dir: str | Path | None = None):
        if not isinstance(identity, RunIdentity):
            raise fail("identity must be a RunIdentity")
        if not isinstance(contract, type):
            raise fail("contract must be a CRM class")
        for operation in ("capabilities", "request", "observe"):
            if not callable(getattr(contract, operation, None)):
                raise fail(f"the live contract must declare a {operation} operation")
        self._state_dir = rpc_config.resolve_state_dir(state_dir)
        self._identity = identity
        self._contract = contract
        self._name = check_text(name, "name", maximum=128)
        self._address = check_text(address, "address", maximum=256)
        self._instance_id = _secret(instance_id, "instanceId")
        self._token = _secret(token, "token")
        self._closed_reason: str | None = None

    @property
    def identity(self) -> RunIdentity:
        """The complete execution identity this channel is bound to."""
        return self._identity

    def capabilities(self) -> LiveCapabilities:
        """The endpoint's declared delivery fact, read live over C-Two."""
        if self._closed_reason is not None:
            raise BoardError("LIVE_UNAVAILABLE", "the live channel is closed")
        frame = LiveWireQuery(instance_id=self._instance_id, token=self._token,
                              identity=self._identity)
        try:
            raw = self._call("capabilities", frame,
                             DEFAULT_TRANSPORT_TIMEOUT_MS)
        except CallDeadlineExceeded:
            raise BoardError("LIVE_UNAVAILABLE",
                             "the live capabilities call did not finish within its transport window",
                             reason="transport-window-expired") from None
        except Exception as error:
            raise BoardError("LIVE_UNAVAILABLE",
                             "the live endpoint did not answer the capabilities call") from error
        try:
            return LiveCapabilities.from_payload(_decode_reply(raw, "live capabilities reply"))
        except BoardError as error:
            raise BoardError("LIVE_UNAVAILABLE",
                             "the live capabilities reply was not a valid frame") from error

    def request(self, request: LiveRequest, *, timeout_ms: int) -> LiveReply:
        """Deliver one live request within its transport window."""
        _timeout_ms(timeout_ms, "timeoutMs")
        if self._closed_reason is not None:
            return LiveReply(status="unavailable", reason_code="channel-closed")
        if request.identity != self._identity:
            return LiveReply(status="unavailable", reason_code="identity-mismatch")
        frame = LiveWireRequest(identity=request.identity, request_id=request.request_id,
                                kind=request.kind, payload=request.payload,
                                instance_id=self._instance_id, token=self._token,
                                timeout_ms=timeout_ms)
        try:
            raw = self._call("request", frame, timeout_ms)
        except CallDeadlineExceeded:
            return LiveReply(status="unavailable", reason_code="transport-window-expired")
        except Exception:
            return LiveReply(status="unavailable", reason_code="transport-unreachable")
        try:
            return LiveReply.from_payload(_decode_reply(raw, "live reply"))
        except BoardError:
            return LiveReply(status="unavailable", reason_code="reply-invalid")

    def observe(self, *, after_seq: int | None = None, limit: int | None = None, timeout_ms: int,
                fields: Any = None, inquiry_id: str | None = None) -> LiveSnapshot:
        """One bounded page of the run's live facts, over the selected sources."""
        _timeout_ms(timeout_ms, "timeoutMs")
        if self._closed_reason is not None:
            return LiveSnapshot(unavailable="channel-closed")
        arguments = _observe_arguments(after_seq, limit, fields, inquiry_id)
        frame = LiveWireObserve(instance_id=self._instance_id, token=self._token,
                                identity=self._identity, **arguments)
        try:
            raw = self._call("observe", frame, timeout_ms)
        except CallDeadlineExceeded:
            return LiveSnapshot(observed=False, reason="transport-window-expired")
        except Exception:
            return LiveSnapshot(observed=False, reason="transport-unreachable")
        try:
            return LiveSnapshot.from_payload(_decode_reply(raw, "live snapshot"))
        except BoardError:
            return LiveSnapshot(observed=False, reason="reply-invalid")

    def close(self, *, reason: str) -> None:
        """Close this client side; the endpoint and its facts are untouched."""
        check_text(reason, "reason", maximum=MAX_CLOSE_REASON_BYTES)
        if self._closed_reason is None:
            self._closed_reason = reason

    def _call(self, operation: str, frame: InternalModel, timeout_ms: int) -> str:
        """One native bounded connection and call under the same deadline.

        Only C-Two owns transport waiting. The owner's business queue still
        enforces this original same-machine cutoff, even when connection time
        consumed most of the window. A zero budget reaches the SDK so its
        pre-dispatch expiry keeps the same failure facts as any other deadline.
        """
        deadline = time.monotonic() + timeout_ms / 1000.0
        if isinstance(frame, LiveWireRequest):
            frame = frame.model_copy(update={"deadline_monotonic": deadline})
        text = canonical_json(frame.to_payload())
        rpc_config.configure_client(self._state_dir)
        with cc.connect(self._contract, name=self._name, address=self._address,
                        timeout=max(0.0, deadline - time.monotonic())) as peer:
            bounded = cc.with_call_options(peer, timeout=max(0.0, deadline - time.monotonic()))
            if operation == "capabilities":
                return bounded.capabilities(text)
            if operation == "request":
                return bounded.request(text)
            return bounded.observe(text)


def _decode_reply(raw: Any, label: str) -> Any:
    if not isinstance(raw, str):
        raise fail(f"the {label} is missing")
    return decode_bounded_frame(raw, label=label, maximum=MAX_LIVE_FRAME_BYTES)


__all__ = [
    "CTwoLiveChannel", "CTwoLiveEndpoint", "CleanupOutcome",
    "ConfirmedProcessGone", "LiveEndpointDescriptor", "LiveWireObserve",
    "LiveWireQuery", "LiveWireRequest", "MAX_PENDING_LIVE_REQUESTS",
    "authenticate_live_frame", "decode_live_wire_frame", "cleanup_owned_endpoint", "random_person_name", "write_ready_material",
]
