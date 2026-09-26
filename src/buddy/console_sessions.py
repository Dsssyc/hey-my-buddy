"""Bounded, process-local browser sessions; Console serializes their transitions.

Public IDs identify cookie paths, never authorize requests. Only a redeemed launch
ticket creates a cookie, and only the latest redemption may write. Nothing here is
persisted, so a stopped/replaced console cannot resurrect browser authority.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hmac
import secrets
import time
from typing import Callable

from .errors import BoardError

ENTRY_SECONDS = 60
IDLE_SECONDS = 300
MAX_ENTRIES = 16
MAX_SESSIONS = 64

READ_OPERATIONS = frozenset({
    "evaluation_history", "selection_get", "selection_list", "model_profiles", "workflow_get",
})


@dataclass
class BrowserSession:
    id: str
    cookie: str = field(repr=False)
    csrf: str = field(repr=False)
    last_seen: float
    can_write: bool = True

    def view(self) -> dict:
        return {"id": self.id, "canWrite": self.can_write,
                "reason": None if self.can_write else "superseded"}


class ConsoleSessions:
    """Call under the owning Console lock, including the final mutation dispatch."""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic):
        self.clock = clock
        self.console_id = secrets.token_hex(12)
        self.last_seen = clock()
        self.entries: dict[str, float] = {}
        self.sessions: dict[str, BrowserSession] = {}

    def issue(self) -> str:
        now = self.clock()
        self.entries = {token: end for token, end in self.entries.items() if now < end}
        if len(self.entries) >= MAX_ENTRIES:
            raise BoardError("CONSOLE_LIMIT", "Too many unused entry links; wait for their expiry")
        ticket = secrets.token_urlsafe(32)
        self.entries[ticket] = now + ENTRY_SECONDS
        self.last_seen = now
        return ticket

    def redeem(self, ticket: str) -> tuple[BrowserSession, BrowserSession | None]:
        now = self.clock()
        deadline = self.entries.get(ticket)
        if deadline is None or now >= deadline:
            self.entries.pop(ticket, None)
            raise BoardError("CONSOLE_ENTRY_EXPIRED", "This entry link is used or expired; run buddy console again")
        if len(self.sessions) >= MAX_SESSIONS:
            raise BoardError("CONSOLE_LIMIT", "Too many active console sessions; close unused pages and wait for expiry")
        del self.entries[ticket]
        previous = next((session for session in self.sessions.values() if session.can_write), None)
        if previous is not None:
            previous.can_write = False
        current = BrowserSession(secrets.token_hex(12), secrets.token_urlsafe(32), secrets.token_urlsafe(32), now)
        self.sessions[current.id] = current
        self.last_seen = now
        return current, previous

    def authenticate(self, session_id: str, cookie: str) -> BrowserSession:
        session = self.sessions.get(session_id)
        now = self.clock()
        if session is None or now - session.last_seen >= IDLE_SECONDS or not hmac.compare_digest(session.cookie.encode(), cookie.encode()):
            raise BoardError("CONSOLE_SESSION_EXPIRED", "This browser session is unavailable; run buddy console again")
        session.last_seen = self.last_seen = now
        return session

    def expire(self) -> list[BrowserSession]:
        now = self.clock()
        self.entries = {token: end for token, end in self.entries.items() if now < end}
        expired = [session for session in self.sessions.values() if now - session.last_seen >= IDLE_SECONDS]
        for session in expired:
            del self.sessions[session.id]
        return expired

    def idle(self) -> bool:
        return self.clock() - self.last_seen >= IDLE_SECONDS

    def path(self, session: BrowserSession) -> str:
        return f"/console/{self.console_id}/{session.id}/"
