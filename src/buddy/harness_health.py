"""Service-owned, generation-fenced harness health; discovery never holds SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import threading

from .db import canonical_json, sha256_text, utc_now
from .errors import BoardError

HARNESSES = ('dsh', 'zcode', 'codex', 'claude')
SCAN_SECONDS = 180
READY_SECONDS = 3600


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _snapshot(name, manual):
    from .harness_discovery import candidate_snapshot
    return candidate_snapshot(name, manual_path=manual)


def _discover(name, manual):
    from .harness_discovery import discover
    return discover(name, manual_path=manual)


def _name(name):
    if name not in HARNESSES:
        raise BoardError('UNSUPPORTED_ADAPTER', 'Expected dsh, zcode, codex or claude')
    return name


def read_health(connection, adapter):
    row = connection.execute('SELECT * FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    if row is None:
        return {'adapter': adapter, 'status': 'unknown', 'available': False, 'revision': 0,
                'manualPath': None, 'reasonCode': 'HARNESS_NOT_CHECKED', 'remedy': 'Run buddy adapters with refresh:true'}
    return {**json.loads(row['record_json']), 'adapter': adapter, 'status': row['status'],
            'available': row['status'] == 'ready', 'revision': row['revision'], 'manualPath': row['manual_path'],
            'checkedAt': row['checked_at'], 'expiresAt': row['expires_at'], 'scanAfter': row['scan_after']}


class HarnessHealth:
    def __init__(self, board, *, catalog_refresh=None):
        self.board = board
        self.catalog_refresh = catalog_refresh
        self._locks = {name: threading.RLock() for name in HARNESSES}
        self._pending = threading.Lock()
        self._closed = False
        self._thread = None

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
            now = utc_now()
            if not force and not preflight and (old.get('scanAfter') or '') > now:
                return old
            snapshot = _snapshot(name, old['manualPath'])
            signature = sha256_text(canonical_json(snapshot))
            unchanged = old.get('locationFingerprint') == signature
            if not force and unchanged and (old.get('expiresAt') or '') > now:
                with self.board.db.write() as db:
                    db.execute('UPDATE harness_health SET scan_after=? WHERE adapter=? AND revision=?', (_later(SCAN_SECONDS), name, old['revision']))
                return self.get(name)
            with self.board.db.write() as db:
                current = read_health(db, name)
                if current['revision'] != old['revision']:
                    return current
                generation = old['revision'] + 1
                db.execute('INSERT INTO harness_health(adapter,revision) VALUES(?,?) ON CONFLICT(adapter) DO UPDATE SET revision=excluded.revision', (name, generation))
            try:
                record = _discover(name, old['manualPath'])
            except Exception:
                record = {'adapter': name, 'status': 'unhealthy', 'reasonCode': 'HARNESS_HANDSHAKE_FAILED',
                          'remedy': 'Repair the native CLI installation and run buddy adapters with refresh:true'}
            record = {**record, 'locationFingerprint': signature}
            status = record.get('status')
            if status not in ('ready', 'missing', 'login-required', 'unhealthy'):
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery returned an invalid health status')
            if len(canonical_json(record).encode()) > 65536:
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery diagnostics exceeded their bound')
            with self.board.db.write() as db:
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
            if status == 'ready' and self.catalog_refresh and (force or old['status'] != 'ready' or old.get('version') != record.get('version') or not unchanged):
                self.catalog_refresh(name, self.get(name))
            return self.get(name)

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
            self._thread.join(timeout=15)
