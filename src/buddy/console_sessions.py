"""Bounded, explicitly revocable browser sessions; only bearer hashes persist."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time
from typing import Callable

from .errors import BoardError

ENTRY_SECONDS = 10 * 60
# Browsers bound persistent cookies; the server never expires a login by age.
COOKIE_SECONDS = 400 * 24 * 60 * 60
MAX_ENTRIES = 16
MAX_SESSIONS = 64
READ_OPERATIONS = frozenset({
    "accounts", "account_status",
    "evaluation_history", "selection_get", "selection_list", "model_profiles", "workflow_get",
})

@dataclass
class BrowserSession:
    id: str
    cookie: str = field(repr=False)
    csrf: str = field(repr=False)
    last_seen: float
    token_hash: str = field(default="", repr=False)

    def view(self) -> dict:
        return {"id": self.id, "canWrite": True, "reason": None}

class ConsoleSessions:
    """Call under the owning Console lock, including mutation dispatch."""

    def __init__(self, *, clock: Callable[[], float] = time.time, path: Path | None = None):
        self.clock = clock
        self.file = path
        self.console_id = secrets.token_hex(12)
        self.entries: dict[str, float] = {}
        self.sessions: dict[str, BrowserSession] = {}
        if path is not None and path.exists():
            if path.is_symlink() or path.stat().st_mode & 0o077:
                raise BoardError("CONSOLE_SESSION_STORE", "Session file must be a private regular file")
            try:
                data = json.loads(path.read_text())
                if data.get("version") != 1 or len(data["sessions"]) > MAX_SESSIONS:
                    raise ValueError("invalid session store")
                for row in data["sessions"]:
                    token_hash = row["tokenHash"]
                    if len(token_hash) != 64 or any(c not in '0123456789abcdef' for c in token_hash):
                        raise ValueError("invalid hash")
                    self.sessions[row["id"]] = BrowserSession(row["id"], "", secrets.token_urlsafe(32), row["lastSeen"], token_hash)
            except (ValueError, TypeError, KeyError) as error:
                raise BoardError("CONSOLE_SESSION_STORE", "Invalid persistent console session store") from error

    def _save(self) -> None:
        if self.file is None:
            return
        rows = [{"id": s.id, "tokenHash": s.token_hash, "lastSeen": s.last_seen} for s in self.sessions.values()]
        temporary = self.file.with_name(self.file.name + ".tmp-" + secrets.token_hex(8))
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"version": 1, "sessions": rows}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.file)
        finally:
            temporary.unlink(missing_ok=True)

    def issue(self) -> str:
        self.expire()
        if len(self.entries) >= MAX_ENTRIES:
            raise BoardError("CONSOLE_LIMIT", "Too many unused entry links; wait for their expiry")
        ticket = secrets.token_urlsafe(32)
        self.entries[ticket] = self.clock() + ENTRY_SECONDS
        return ticket

    def redeem(self, ticket: str, cookie: str = "") -> BrowserSession:
        self.expire()
        deadline = self.entries.get(ticket)
        if deadline is None or self.clock() >= deadline:
            raise BoardError("CONSOLE_ENTRY_EXPIRED", "This entry link is used or expired; run buddy console again")
        if cookie:
            try:
                current = self.authenticate(None, cookie)
            except BoardError:
                pass
            else:
                del self.entries[ticket]
                return current
        if len(self.sessions) >= MAX_SESSIONS:
            raise BoardError("CONSOLE_LIMIT", "Too many console sessions; revoke a login in Settings")
        del self.entries[ticket]
        return self.create()

    def create(self) -> BrowserSession:
        if len(self.sessions) >= MAX_SESSIONS:
            raise BoardError("CONSOLE_LIMIT", "Too many console sessions; revoke a login in Settings")
        token = secrets.token_urlsafe(32)
        current = BrowserSession(secrets.token_hex(12), token, secrets.token_urlsafe(32), self.clock(), hashlib.sha256(token.encode()).hexdigest())
        self.sessions[current.id] = current
        try:
            self._save()
        except Exception:
            self.sessions.pop(current.id, None)
            raise
        return current

    def authenticate(self, session_id: str | None, cookie: str) -> BrowserSession:
        digest = hashlib.sha256(cookie.encode()).hexdigest()
        session = next((s for s in self.sessions.values() if (session_id is None or s.id == session_id) and hmac.compare_digest(s.token_hash, digest)), None)
        if not cookie or session is None:
            raise BoardError("CONSOLE_SESSION_EXPIRED", "This browser session is unavailable; run buddy console again")
        session.last_seen = self.clock()
        session.cookie = cookie
        self._save()
        return session

    def expire(self) -> list[BrowserSession]:
        now = self.clock()
        self.entries = {token: end for token, end in self.entries.items() if now < end}
        return []

    def revoke(self, session_id: str | None = None) -> list[BrowserSession]:
        previous = self.sessions
        revoked = [s for s in previous.values() if session_id is None or s.id == session_id]
        self.sessions = {key: s for key, s in previous.items() if s not in revoked}
        try:
            self._save()
        except Exception:
            self.sessions = previous
            raise
        return revoked

    def path(self, session: BrowserSession) -> str:
        return "/"
