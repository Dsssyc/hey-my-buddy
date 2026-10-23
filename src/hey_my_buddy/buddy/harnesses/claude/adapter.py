"""Claude Code capability description; execution belongs to the registered run.

P1 scope (ADR-012 section six): a ``--json-schema`` structured result, native
permission requests converted to controller attention, and a freshly allocated
``--session-id`` per attempt with no native resume. Availability reports the
native initialize metadata; unknown observations and failures are cached
briefly so repeated
capability reads do not respawn the CLI. The micro task Worker turn, the
read-only structured call and model discovery all execute through the
registered :mod:`~hey_my_buddy.buddy.harnesses.claude.native_run` under the
shared role controller; this description carries no execution entries of its
own (ADR-025 step 3-B2).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from ....errors import BoardError
from ....protocol.capabilities import adapter_capabilities
from .config import ClaudeUnavailable, cli_command
from .native_run import validate_turn_provenance

#: Native initialize metadata is cached briefly so repeated capability reads do
#: not respawn the CLI; ``discover_models`` always refreshes explicitly.
_METADATA_TTL_SECONDS = 60.0
_metadata_cache: dict | None = None
_metadata_lock = threading.RLock()


class ClaudeAdapter:
    """Native availability and facts, with no inherited execution entries."""

    name = "claude"
    native_resume = False
    interrupted_resume = False
    native_resume_permission_change = False
    model_discovery = True

    @property
    def capabilities(self):
        return adapter_capabilities(self.name)

    def available(self) -> tuple[bool, str | None]:
        from ..runtime_selection import selected
        record = selected("claude")
        if record is not None:
            return record.get('status') == 'ready', (record.get('reason') or {}).get('code')
        usable, reason = self.discovery_available()
        if not usable:
            return False, reason
        try:
            from ..runtime_selection import environment_for
            metadata = _cached_native_metadata(state_dir=Path(environment_for()["HMB_STATE_DIR"]))
        except BoardError as error:
            return False, error.code
        unknown = next((item for item in metadata['catalog']['discoveries']
                        if item['status'] == 'unknown'), None)
        if unknown is not None:
            cause = unknown.get('reason')
            return False, cause['code'] if cause is not None else 'catalog-unavailable'
        return True, None

    def discovery_available(self) -> tuple[bool, str | None]:
        # Explicit discovery must be able to recheck login even when a previous
        # execution-readiness query cached an unauthenticated result.
        try:
            cli_command()
        except ClaudeUnavailable as error:
            return False, str(error)
        return True, None

    validate_turn_provenance = staticmethod(validate_turn_provenance)

    def discover_models(self, *, state_dir: Path) -> dict:
        """One explicit initialize-only discovery; never a user message.

        The refresh goes through the one shared discovery outer layer
        (``roles.run_execution.discover_models``) — the same carrier the
        availability probe uses — so the explicit call and the probe share
        one cache: an explicit discovery bypasses a briefly cached
        metadata failure, and its result warms the same entry. This
        harness's availability fact depends on that same bounded metadata,
        which is why the description keeps the cache here.
        """
        _reset_metadata_cache()
        return _cached_native_metadata(state_dir=state_dir)["catalog"]


def _reset_metadata_cache() -> None:
    global _metadata_cache
    with _metadata_lock:
        _metadata_cache = None


def _cache_key() -> tuple:
    try:
        command = cli_command()
    except ClaudeUnavailable as error:
        return ("missing", str(error))
    return (tuple(command),)


def _probe_native_metadata(*, state_dir: Path) -> dict:
    """One initialize-only native metadata read through the registered discovery carrier.

    The probe goes through the one shared discovery outer layer
    (``roles.run_execution.discover_models``), which owns its private
    directory, its launch and the two-layer stop confirmation around this
    module's own operation; this description adds only the TTL cache around
    it. Bounded native metadata failures arrive on that layer's ``HARNESS_UNAVAILABLE`` refusal and are what
    availability reports.
    """
    from ...roles.run_execution import discover_models
    # The metadata receipt keeps the catalog under its own key, the shape
    # every existing consumer of an explicit refresh reads.
    return {"catalog": discover_models("claude", state_dir=state_dir)}


def _cached_native_metadata(*, state_dir: Path) -> dict:
    # Concurrent capability readers share one initialize-only probe. In
    # particular an unauthenticated console refresh must not fan out CLIs.
    with _metadata_lock:
        return _load_native_metadata(state_dir=state_dir)


def _load_native_metadata(*, state_dir: Path) -> dict:
    """Cached initialize metadata for repeated capability reads.

    Unknown observations and explicit failures are cached too, so console and
    capability checks do not respawn the native CLI on every call while the
    result stays unknown or unavailable. ``discover_models``
    always resets the cache for an explicit fresh discovery.
    """
    global _metadata_cache
    key = _cache_key()
    now = time.monotonic()
    if _metadata_cache and _metadata_cache.get("key") == key and now - _metadata_cache.get("at", 0.0) < _METADATA_TTL_SECONDS:
        if "error" in _metadata_cache:
            raise _metadata_cache["error"]
        return _metadata_cache["metadata"]
    try:
        metadata = _probe_native_metadata(state_dir=state_dir)
    except BoardError as error:
        _metadata_cache = {"key": key, "at": now, "error": error}
        raise
    _metadata_cache = {"key": key, "at": now, "metadata": metadata}
    return metadata
