"""Service-owned, generation-fenced harness health; discovery never holds SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import ChainMap
import json
import os
from pathlib import Path
import threading

from .db import canonical_json, sha256_text, utc_now
from .errors import BoardError

HARNESSES = ('dsh', 'zcode', 'codex', 'claude')
SCAN_SECONDS = 180
READY_SECONDS = 3600


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _snapshot(name, manual, environment=None):
    from .harness_discovery import candidate_snapshot
    return candidate_snapshot(name, manual_path=manual, environment=environment)


def _discover(name, manual, environment=None):
    from .harness_discovery import discover
    return discover(name, manual_path=manual, environment=environment)


def _name(name):
    if name not in HARNESSES:
        raise BoardError('UNSUPPORTED_ADAPTER', 'Expected dsh, zcode, codex or claude')
    return name


def read_health(connection, adapter):
    from .harness_review import verification_view
    row = connection.execute('SELECT * FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    if row is None:
        record = {'adapter': adapter, 'status': 'unknown', 'available': False, 'revision': 0,
                'manualPath': None, 'reasonCode': 'HARNESS_NOT_CHECKED', 'remedy': 'Run buddy adapters with refresh:true'}
        return {**record, 'reviewVerification': verification_view(connection, adapter, record)}
    from .native_observations import quota_view
    record = json.loads(row['record_json'])
    billing = record.get("billingByProvider") if isinstance(record.get("billingByProvider"), dict) else {}
    if adapter == "dsh":
        from .billing import fact
        catalog = connection.execute("SELECT d.payload_json,c.updated_at FROM catalog_current c JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.adapter='dsh' AND c.status='complete'").fetchone()
        if catalog:
            try:
                entries = json.loads(catalog["payload_json"])["providers"]
            except (ValueError, TypeError, KeyError):
                entries = []
            billing = {entry["provider"]: fact("metered" if entry.get("packageName") == "@deepseek-ai/dsh-llm-deepseek" else "unknown",
                                                "dsh/deepseek-api-key", catalog["updated_at"])
                       for entry in entries if entry.get("adapter") == adapter and isinstance(entry.get("provider"), str)}
    record = {**record, "billingByProvider": billing, "quota": quota_view(json.loads(row["quota_json"]) if row["quota_json"] else None), 'adapter': adapter, 'status': row['status'],
            'available': row['status'] == 'ready', 'revision': row['revision'], 'manualPath': row['manual_path'],
            'checkedAt': row['checked_at'], 'expiresAt': row['expires_at'], 'scanAfter': row['scan_after']}
    return {**record, 'reviewVerification': verification_view(connection, adapter, record)}


class HarnessHealth:
    def __init__(self, board, *, catalog_refresh=None):
        self.board = board
        self.catalog_refresh = catalog_refresh
        self._locks = {name: threading.RLock() for name in HARNESSES}
        self._pending = threading.Lock()
        self._closed = False
        self._thread = None
        self._path_hints = ()

    def add_path_hint(self, path):
        """PATH is an ephemeral low-priority hint, never a stored environment."""
        if not isinstance(path, str) or len(path) > 32768:
            return
        parts = [part for part in path.split(os.pathsep) if part and Path(part).is_absolute()]
        self._path_hints = tuple(dict.fromkeys([*parts, *self._path_hints]))[:64]

    def environment(self):
        if not self._path_hints:
            return os.environ
        path = os.pathsep.join([*self._path_hints, os.environ.get('PATH', '')])
        return ChainMap({'PATH': path}, os.environ)

    def initialize(self):
        """An upgraded board starts unverified without rewriting user settings."""
        with self.board.db.write() as db:
            for name in HARNESSES:
                if db.execute('SELECT 1 FROM harness_health WHERE adapter=?', (name,)).fetchone() is None:
                    db.execute('INSERT INTO harness_health(adapter) VALUES(?)', (name,))
                    db.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='HARNESS_NOT_CHECKED' WHERE adapter=?", (name,))

    def get(self, name):
        with self.board.db.read() as db:
            return read_health(db, _name(name))

    def invalidate(self, name, revision, reason='HARNESS_START_FAILED'):
        with self.board.db.write() as db:
            row = read_health(db, _name(name))
            if row['revision'] != revision:
                return row
            db.execute("UPDATE harness_health SET status='unhealthy',record_json=?,expires_at=NULL,scan_after=NULL,revision=revision+1 WHERE adapter=?", (canonical_json({**row, "reasonCode": reason, "available": False}), name))
            db.execute('UPDATE evaluation_profiles SET available=0,unavailable_reason=? WHERE adapter=?', (reason, name))
            self.board._append_event(db, 'harness.invalidated', payload={'adapter': name, 'reasonCode': reason})
            head = self.board._head_of(db)
        self.board._notify(head)
        return self.get(name)

    def all(self):
        with self.board.db.read() as db:
            return [read_health(db, name) for name in HARNESSES]

    def set_path(self, name, path, *, expected_revision=None):
        name = _name(name)
        if path is not None and (not isinstance(path, str) or len(path) > 4096 or not Path(path).expanduser().is_absolute()):
            raise BoardError('INVALID_ARGUMENT', 'path must be an absolute executable path, or null for automatic detection')
        with self.board.db.write() as db:
            old = read_health(db, name)
            if expected_revision is not None and expected_revision != old['revision']:
                raise BoardError('REVISION_CONFLICT', 'Harness settings changed; reread before saving')
            db.execute("INSERT INTO harness_health(adapter,manual_path,revision) VALUES(?,?,1) ON CONFLICT(adapter) DO UPDATE SET manual_path=excluded.manual_path,revision=revision+1,status='unknown',record_json='{}',checked_at=NULL,expires_at=NULL,scan_after=NULL", (name, str(Path(path).expanduser()) if path else None))
            db.execute("UPDATE evaluation_profiles SET available=0,unavailable_reason='HARNESS_NOT_CHECKED' WHERE adapter=?", (name,))
            self.board._append_event(db, 'harness.path_changed', payload={'adapter': name, 'manualPath': path})
            head = self.board._head_of(db)
        self.board._notify(head)
        return self.refresh(name, force=True)

    def refresh(self, name, *, force=False, preflight=False):
        name = _name(name)
        with self._locks[name]:
            old = self.get(name)
            if (self.board.directory / 'upgrade.json').exists():
                return old
            now = utc_now()
            if not force and not preflight and (old.get('scanAfter') or '') > now:
                return old
            environment = self.environment()
            snapshot = _snapshot(name, old['manualPath'], environment)
            signature = sha256_text(canonical_json(snapshot))
            unchanged = old.get('locationFingerprint') == signature
            if not force and unchanged and (old.get('expiresAt') or '') > now:
                with self.board.db.write() as db:
                    db.execute('UPDATE harness_health SET scan_after=? WHERE adapter=? AND revision=?', (_later(SCAN_SECONDS), name, old['revision']))
                if name == "codex":
                    self._codex_account_read(old)
                return self.get(name)
            with self.board.db.write() as db:
                if self._closed or (self.board.directory / 'upgrade.json').exists():
                    return read_health(db, name)
                current = read_health(db, name)
                if current['revision'] != old['revision']:
                    return current
                generation = old['revision'] + 1
                db.execute('INSERT INTO harness_health(adapter,revision) VALUES(?,?) ON CONFLICT(adapter) DO UPDATE SET revision=excluded.revision', (name, generation))
            try:
                record = _discover(name, old['manualPath'], environment)
            except Exception:
                record = {'adapter': name, 'status': 'unhealthy', 'reasonCode': 'HARNESS_HANDSHAKE_FAILED',
                          'remedy': 'Repair the native CLI installation and run buddy adapters with refresh:true'}
            retained = {key: old[key] for key in ("reviewVerification", "billingByProvider", "quotaCheckedAt") if key in old}
            if record.get("status") != "ready" or old.get("fingerprint") != record.get("fingerprint"):
                retained.pop("billingByProvider", None)
            record = {**retained, **record, 'locationFingerprint': signature}
            status = record.get('status')
            if status not in ('ready', 'missing', 'login-required', 'unhealthy'):
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery returned an invalid health status')
            if len(canonical_json(record).encode()) > 65536:
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery diagnostics exceeded their bound')
            with self.board.db.write() as db:
                if (self.board.directory / 'upgrade.json').exists():
                    return read_health(db, name)
                if read_health(db, name)['revision'] != generation:
                    return read_health(db, name)
                db.execute('UPDATE harness_health SET status=?,record_json=?,checked_at=?,expires_at=?,scan_after=? WHERE adapter=? AND revision=?',
                           (status, canonical_json(record), now, _later(READY_SECONDS if status == 'ready' else SCAN_SECONDS), _later(SCAN_SECONDS), name, generation))
                if status != 'ready':
                    db.execute('UPDATE evaluation_profiles SET available=0,unavailable_reason=? WHERE adapter=?', (record.get('reasonCode') or 'HARNESS_UNHEALTHY', name))
                self.board._append_event(db, 'harness.checked', payload={'adapter': name, 'status': status, 'revision': generation,
                                          'version': record.get('version'), 'reasonCode': record.get('reasonCode')})
                head = self.board._head_of(db)
            self.board._notify(head)
            if name == "codex" and status == "ready":
                self._codex_account_read(self.get(name))
            if not self._closed and status == 'ready' and self.catalog_refresh and (force or old['status'] != 'ready' or old.get('version') != record.get('version') or not unchanged):
                self.catalog_refresh(name, self.get(name))
            return self.get(name)

    def _codex_account_read(self, health):
        """One on-demand account read per 180 seconds, even across forced refreshes."""
        if health.get("status") != "ready" or not health.get("command"):
            return
        from .native_observations import _time
        checked = _time(health.get("quotaCheckedAt"))
        with self.board.db.read() as db:
            saved = db.execute("SELECT value FROM meta WHERE key='codex_account_read_at'").fetchone()
        if saved:
            checked = _time(saved[0])
        now = _time(utc_now())
        if checked and now and 0 <= (now - checked).total_seconds() < SCAN_SECONDS:
            return
        if self._closed or (self.board.directory / 'upgrade.json').exists():
            return
        with self.board.db.write() as db:
            db.execute("INSERT INTO meta(key,value) VALUES('codex_account_read_at',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (utc_now(),))
        billing = quota = None
        try:
            from .codex_account_probe import read
            billing, quota = read(health["command"], self.environment())
        except Exception:
            pass
        with self.board.db.write() as db:
            row = db.execute("SELECT record_json,quota_json,revision FROM harness_health WHERE adapter='codex'").fetchone()
            if row is None or row["revision"] != health["revision"]:
                return
            record = json.loads(row["record_json"])
            record["quotaCheckedAt"] = utc_now()
            if billing is not None:
                record["billingByProvider"] = {"openai": billing}
            else:
                from .billing import fact
                record["billingByProvider"] = {"openai": fact("unknown", "codex/account-read", record["quotaCheckedAt"])}
            db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (canonical_json(record),))
            if quota is not None:
                from .quota_routing import record as record_quota
                record_quota(db, 'codex', {**quota, 'provider': 'openai'})
                previous = json.loads(row["quota_json"]) if row["quota_json"] else None
                if previous is None or (_time(quota["observedAt"]) and _time(quota["observedAt"]) > (_time(previous.get("observedAt")) or _time("1970-01-01T00:00:00Z"))):
                    db.execute("UPDATE harness_health SET quota_json=? WHERE adapter='codex'", (canonical_json({**quota, "provider": "openai"}),))
            self.board._append_event(db, 'harness.account_observed', payload={'adapter': 'codex',
                'observedAt': record['quotaCheckedAt'], 'billing': record['billingByProvider'], 'quotaRecorded': quota is not None})
            head = self.board._head_of(db)
        self.board._notify(head)

    def kick(self):
        """One rate-limited scan borrowed from a Host call; no timer or polling loop."""
        if self._closed or not self._pending.acquire(blocking=False):
            return
        def run():
            try:
                for name in HARNESSES:
                    if self._closed:
                        break
                    try:
                        self.refresh(name)
                    except (BoardError, OSError):
                        pass
            finally:
                self._pending.release()
        self._thread = threading.Thread(target=run, name='harness-discovery', daemon=True)
        self._thread.start()

    def close(self):
        self._closed = True
        if self._thread:
            self._thread.join(timeout=55)
