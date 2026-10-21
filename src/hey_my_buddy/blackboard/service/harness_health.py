"""Service-owned, generation-fenced harness health; discovery never holds SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import ChainMap
import json
import os
from pathlib import Path
import threading

from ...paths import is_absolute_path
from ..store.db import canonical_json, utc_now
from ...errors import BoardError
from ...protocol.reasons import reason as make_reason

HARNESSES = ('dsh', 'zcode', 'codex', 'claude')
SCAN_SECONDS = 180
READY_SECONDS = 3600
MAX_PATH_HINTS = 64


def _not_checked():
    return make_reason('harness.not-checked', operation='probe')


def _set_buddies_unavailable(db, harness, cause):
    from ..catalog.catalog import CATALOG_FACT_REASONS
    protected = ','.join('?' for _ in CATALOG_FACT_REASONS)
    db.execute(f'UPDATE buddies SET available=0,unavailable_code=?,unavailable_params_json=?,unavailable_content=NULL '
               f'WHERE harness=? AND (unavailable_code IS NULL OR unavailable_code NOT IN ({protected}))',
               (cause['code'], canonical_json(cause['params']), harness, *CATALOG_FACT_REASONS))


def _later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def _snapshot(name, manual, environment=None):
    from ...buddy.harnesses.discovery import candidate_snapshot
    return candidate_snapshot(name, manual_path=manual, environment=environment)


def _discover(name, manual, environment=None):
    from ...buddy.harnesses.discovery import discover
    return discover(name, manual_path=manual, environment=environment)


def _name(name):
    if name not in HARNESSES:
        raise BoardError('UNSUPPORTED_HARNESS', 'Expected dsh, zcode, codex or claude')
    return name


def read_health(connection, harness):
    from ...protocol.capabilities import PERMISSION_LEVELS
    from ..catalog.accounts import view as account_view
    permissions = dict(PERMISSION_LEVELS[harness])
    account = account_view(connection, harness)
    row = connection.execute('SELECT * FROM harness_health WHERE harness=?', (harness,)).fetchone()
    if row is None:
        record = {'harness': harness, 'status': 'unknown', 'revision': 0,
                'manualPath': None, 'reason': _not_checked()}
        return {**record, 'providers': [], 'account': account, 'permissions': permissions}
    from ..routing.quota_routing import providers
    record = json.loads(row['record_json'])
    billing = record.get('billingByProvider') or {}
    if harness == "dsh":
        from ...protocol.billing import fact
        catalog = connection.execute("SELECT d.payload_json,c.updated_at FROM catalog_current c JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.harness='dsh' AND c.status='complete'").fetchone()
        if catalog:
            try:
                entries = json.loads(catalog["payload_json"])["providers"]
            except (ValueError, TypeError, KeyError):
                entries = []
            billing = {entry["provider"]: fact("metered" if entry.get("packageName") == "@deepseek-ai/dsh-llm-deepseek" else "unknown",
                                                "dsh/deepseek-api-key", catalog["updated_at"])
                       for entry in entries if entry.get("harness") == harness and isinstance(entry.get("provider"), str)}
    status = row['status']
    record = {**record, "billingByProvider": billing, "providers": providers(connection, harness), 'harness': harness,
            'status': status, 'account': account, 'revision': row['revision'], 'manualPath': row['manual_path'],
            'checkedAt': row['checked_at'], 'expiresAt': row['expires_at'], 'scanAfter': row['scan_after']}
    return {**record, 'permissions': permissions}


class HarnessHealth:
    def __init__(self, board, *, catalog_refresh=None):
        self.board = board
        self.catalog_refresh = catalog_refresh
        self._locks = {name: threading.RLock() for name in HARNESSES}
        self._pending = threading.Lock()
        self._closed = False
        self._thread = None
        self._path_hints = ()
        self._path_hint_lock = threading.Lock()

    def add_path_hint(self, path):
        """PATH is an ephemeral low-priority hint, never a stored environment."""
        if not isinstance(path, str) or len(path) > 32768:
            return
        from ...buddy.harnesses.discovery import has_candidate
        parts = dict.fromkeys(part for part in path.split(os.pathsep) if part and is_absolute_path(part))
        with self._path_hint_lock:
            hints = list(self._path_hints)
            for part in parts:
                if len(hints) == MAX_PATH_HINTS:
                    break
                if part not in hints and has_candidate(Path(part)):
                    hints.append(part)
            self._path_hints = tuple(hints)

    def environment(self):
        if not self._path_hints:
            return os.environ
        path = os.pathsep.join([*self._path_hints, os.environ.get('PATH', '')])
        return ChainMap({'PATH': path}, os.environ)

    def initialize(self):
        """An upgraded board starts unverified without rewriting user settings."""
        with self.board.db.write() as db:
            for name in HARNESSES:
                if db.execute('SELECT 1 FROM harness_health WHERE harness=?', (name,)).fetchone() is None:
                    db.execute('INSERT INTO harness_health(harness,record_json) VALUES(?,?)',
                               (name, canonical_json({'lastProbe': None, 'reason': _not_checked()})))
                    _set_buddies_unavailable(db, name, _not_checked())

    def get(self, name):
        with self.board.db.read() as db:
            return read_health(db, _name(name))

    def invalidate(self, name, revision, *, account_read_failure=None, cause=None):
        if cause is None:
            cause = (make_reason('harness.shutdown-unverified', operation='account-read')
                     if account_read_failure is not None else make_reason('harness.launch-failed', operation='probe'))
        with self.board.db.write() as db:
            row = read_health(db, _name(name))
            if account_read_failure is not None and (self._closed or (self.board.directory / 'upgrade.json').exists()):
                return row
            if row['revision'] != revision:
                if account_read_failure is None:
                    return row
                self.board._append_event(db, 'harness.account_read_failed', payload={
                    'harness': name, 'reason': cause, 'revision': revision,
                    'currentRevision': row['revision'], 'accountReadFailure': account_read_failure})
            else:
                record = json.loads(db.execute('SELECT record_json FROM harness_health WHERE harness=?', (name,)).fetchone()[0])
                record['reason'] = cause
                if account_read_failure is not None:
                    record['accountReadFailure'] = account_read_failure
                db.execute("UPDATE harness_health SET status='unhealthy',record_json=?,expires_at=NULL,scan_after=NULL,revision=revision+1 WHERE harness=?", (canonical_json(record), name))
                # ADR-027: a health failure must not erase catalog facts. Rows a
                # confirmed absence or an effort retirement already grounded stay
                # with their own reason; health only grounds the rest.
                _set_buddies_unavailable(db, name, cause)
                self.board._append_event(db, 'harness.invalidated', payload={'harness': name, 'reason': cause,
                    **({'accountReadFailure': account_read_failure} if account_read_failure is not None else {})})
            head = self.board._head_of(db)
        self.board._notify(head)
        return self.get(name)

    def all(self):
        with self.board.db.read() as db:
            return [read_health(db, name) for name in HARNESSES]

    def set_path(self, name, path):
        name = _name(name)
        if path is not None and (not isinstance(path, str) or len(path) > 4096 or not is_absolute_path(Path(path).expanduser())):
            raise BoardError('INVALID_ARGUMENT', 'path must be an absolute executable path, or null for automatic detection')
        with self.board.db.write() as db:
            db.execute("INSERT INTO harness_health(harness,manual_path,revision,record_json) VALUES(?,?,1,?) ON CONFLICT(harness) DO UPDATE SET manual_path=excluded.manual_path,revision=revision+1,status='unknown',record_json=excluded.record_json,checked_at=NULL,expires_at=NULL,scan_after=NULL", (name, str(Path(path).expanduser()) if path else None, canonical_json({'lastProbe': None, 'reason': _not_checked()})))
            _set_buddies_unavailable(db, name, _not_checked())
            self.board._append_event(db, 'harness.path_changed', payload={'harness': name, 'manualPath': path})
            head = self.board._head_of(db)
        self.board._notify(head)
        return self.refresh(name, force=True)

    def refresh(self, name, *, force=False, preflight=False, catalog_scan_claimed=False):
        name = _name(name)
        with self._locks[name]:
            old = self.get(name)
            if (self.board.directory / 'upgrade.json').exists():
                return old
            now = utc_now()
            if (not force and (old.get('lastProbe') or {}).get('status') == 'unknown'
                    and (old.get('scanAfter') or '') > now):
                return old
            if not force and not preflight:
                if (old.get('scanAfter') or '') > now:
                    return old
                if old['status'] != 'ready' and (old.get('expiresAt') or '') > now:
                    return old
            stale_catalog_due = self._stale_catalog(name, now) if not force and not preflight else False
            environment = self.environment()
            snapshot = _snapshot(name, old['manualPath'], environment)
            from ...buddy.harnesses.discovery import location_fingerprint
            signature = location_fingerprint(snapshot)
            unchanged = old.get('locationFingerprint') == signature
            if (not force and unchanged and (old.get('expiresAt') or '') > now
                    and (old.get('lastProbe') or {}).get('status') != 'unknown'):
                if name == "codex":
                    self._codex_account_read(old)
                if stale_catalog_due:
                    current = self.get(name)
                    if current['status'] == 'ready':
                        self._shared_stale_catalog_read(name, current)
                return self.get(name)
            with self.board.db.write() as db:
                if self._closed or (self.board.directory / 'upgrade.json').exists():
                    return read_health(db, name)
                current = read_health(db, name)
                if current['revision'] != old['revision']:
                    return current
                generation = old['revision'] + 1
                db.execute('INSERT INTO harness_health(harness,revision,record_json) VALUES(?,?,?) '
                           'ON CONFLICT(harness) DO UPDATE SET revision=excluded.revision',
                           (name, generation, canonical_json({'reason': _not_checked()})))
            try:
                record = _discover(name, old['manualPath'], environment)
            except Exception:
                record = {'harness': name, 'status': 'unhealthy',
                          'reason': make_reason('harness.probe-failed', operation='probe')}
            if record.get('status') == 'unknown':
                with self.board.db.write() as db:
                    if self._closed or (self.board.directory / 'upgrade.json').exists():
                        return read_health(db, name)
                    current = db.execute('SELECT revision,record_json FROM harness_health WHERE harness=?', (name,)).fetchone()
                    if current['revision'] != generation:
                        return read_health(db, name)
                    retained = json.loads(current['record_json'])
                    retained['lastProbe'] = {'status': 'unknown', 'reason': record.get('reason'),
                                             'observedAt': now, 'candidates': record.get('candidates', [])}
                    db.execute('UPDATE harness_health SET record_json=?,scan_after=? WHERE harness=?',
                               (canonical_json(retained), _later(SCAN_SECONDS), name))
                    self.board._append_event(db, 'harness.probe_unknown', payload={
                        'harness': name, 'revision': generation, 'reason': record.get('reason')})
                    head = self.board._head_of(db)
                self.board._notify(head)
                return self.get(name)
            retained = {key: old[key] for key in ("billingByProvider", "accountReadFailure") if key in old}
            if record.get("status") != "ready" or old.get("fingerprint") != record.get("fingerprint"):
                retained.pop("billingByProvider", None)
            record = {**retained, **record, 'locationFingerprint': signature,
                      'lastProbe': {'status': 'complete', 'observedAt': now,
                                    'reason': record.get('reason')}}
            status = record.get('status')
            native_ready = status == 'ready'
            if native_ready and (record.get('accountReadFailure', {}).get('reason') or {}).get('code') == 'harness.shutdown-unverified':
                status = 'unhealthy'
                record['reason'] = record['accountReadFailure']['reason']
            if status not in ('ready', 'missing', 'login-required', 'unhealthy'):
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery returned an invalid health status')
            if len(canonical_json(record).encode()) > 65536:
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery diagnostics exceeded their bound')
            stored_record = canonical_json({key: value for key, value in record.items()
                                            if key not in ('harness', 'status', 'available')})
            with self.board.db.write() as db:
                if (self.board.directory / 'upgrade.json').exists():
                    return read_health(db, name)
                if read_health(db, name)['revision'] != generation:
                    return read_health(db, name)
                db.execute('UPDATE harness_health SET status=?,record_json=?,checked_at=?,expires_at=? WHERE harness=? AND revision=?',
                           (status, stored_record, now, _later(READY_SECONDS if status == 'ready' else SCAN_SECONDS), name, generation))
                if status != 'ready':
                    # Same boundary as invalidate: health grounds the rows it
                    # marks, never the ones a catalog fact already grounded.
                    _set_buddies_unavailable(db, name, record['reason'])
                elif old['status'] != 'ready':
                    # ADR-027: health availability and adopted catalog facts are
                    # separate. Recovering health restores the model availability
                    # of the retained confirmed catalog (and of model services still
                    # awaiting disappearance confirmation); confirmed absences,
                    # retired efforts and native unavailable declarations are
                    # catalog facts and stay untouched.
                    from ..catalog.catalog import restore_retained_availability
                    restore_retained_availability(db, name)
                self.board._append_event(db, 'harness.checked', payload={'harness': name, 'status': status, 'revision': generation,
                                          'version': record.get('version'), 'reason': record.get('reason')})
                head = self.board._head_of(db)
            self.board._notify(head)
            current = self.get(name)
            if name == "codex" and native_ready:
                self._codex_account_read(current)
                current = self.get(name)
                status = current['status']
            # Necessary eager reads (manual force, a status or version change, a
            # moved binary) stay outside the shared window; only the stale-catalog
            # read shares rule 3's claim and single flight.
            if not self._closed and status == 'ready' and self.catalog_refresh:
                if force or old['status'] != 'ready' or old.get('version') != record.get('version') or not unchanged:
                    self.catalog_refresh(name, current, catalog_scan_claimed=catalog_scan_claimed)
                    if not catalog_scan_claimed:
                        self._note_catalog_scan(name)
                elif stale_catalog_due:
                    self._shared_stale_catalog_read(name, current)
            return self.get(name)

    def _stale_catalog(self, name, now):
        """True when this harness owes one bounded stale-catalog re-read now."""
        return (not self._closed and self.catalog_refresh is not None
                and self._catalog_scan_due(name, now) and self._catalog_expired(name, now))

    def _shared_stale_catalog_read(self, name, current):
        """Claim or skip the one bounded stale-catalog read, jointly with rule 3.

        The claim, its window and its in-process flight are the same mechanism
        an explicit validation rejection uses, so a health scan and an explicit
        request never start two native reads for one harness inside one window.
        An in-flight read is not waited for here — this refresh already holds the
        per-harness lock, and the explicit side's reader borrows that same lock;
        the facts arrive with the in-flight read, and the next scan window
        retries on its own cadence. Necessary eager reads (manual force, status
        or version changes) stay outside this boundary.
        """
        from ..catalog.catalog import coordinate_catalog_reread
        coordinate_catalog_reread(self.board.directory, name,
                                  reader=lambda: self.catalog_refresh(name, current, catalog_scan_claimed=True),
                                  wait=False)

    def _catalog_scan_due(self, name, now):
        from ..routing.quota_routing import _time
        with self.board.db.read() as db:
            row = db.execute('SELECT scan_after FROM harness_health WHERE harness=?', (name,)).fetchone()
        if row is None:
            return True
        moment, due = _time(now), _time(row['scan_after'])
        return due is None or moment is None or moment >= due

    def _note_catalog_scan(self, name):
        with self.board.db.write() as db:
            db.execute('UPDATE harness_health SET scan_after=? WHERE harness=?', (_later(SCAN_SECONDS), name))

    def _catalog_expired(self, name, now):
        """ADR-027 rule 4: a catalog past its shelf life needs a confirmed re-read.

        Only a confirmed (trusted) reading advances the confirmed-read time, so
        a streak of unknown readings never extends that deadline. A board with
        no confirmed reading yet — cold start, or an explicit upgrade —
        measures the shelf life from the last recorded observation instead, and
        a harness with no observation at all has nothing stale to re-read.
        """
        from ..catalog.catalog import CATALOG_SHELF_SECONDS, catalog_read_at
        from ..routing.quota_routing import _time
        with self.board.db.read() as db:
            read_at = catalog_read_at(db, name)
            if read_at is None:
                row = db.execute('SELECT updated_at FROM catalog_current WHERE harness=?', (name,)).fetchone()
                read_at = row['updated_at'] if row is not None else None
        if read_at is None:
            return False
        read, moment = _time(read_at), _time(now)
        return read is None or moment is None or (moment - read).total_seconds() >= CATALOG_SHELF_SECONDS

    def _codex_account_read(self, health):
        """One on-demand native read per harness every 180 seconds."""
        stop_unconfirmed = (health.get('accountReadFailure', {}).get('reason') or {}).get('code') == 'harness.shutdown-unverified'
        if (health.get('status') != 'ready' and not stop_unconfirmed) or not health.get('command'):
            return
        if self._closed or (self.board.directory / 'upgrade.json').exists():
            return
        from ..routing.quota_routing import _time
        with self.board.db.write() as db:
            row = db.execute("SELECT checked_at FROM harness_account_reads WHERE harness='codex'").fetchone()
            now = utc_now()
            checked, moment = _time(row[0]) if row else None, _time(now)
            if checked and 0 <= (moment - checked).total_seconds() < SCAN_SECONDS:
                return
            db.execute('INSERT INTO harness_account_reads(harness,checked_at) VALUES(?,?) '
                       'ON CONFLICT(harness) DO UPDATE SET checked_at=excluded.checked_at', ('codex', now))
        from ...buddy.harnesses.codex.protocol import CodexProtocolError
        billing = failure = None
        try:
            from ...buddy.harnesses.codex.account_probe import read
            billing = read(health['command'], self.environment(), entry=health.get('executable'))
        except (BoardError, CodexProtocolError, OSError, ValueError, RuntimeError) as error:
            diagnostic_code = error.code if isinstance(error, (BoardError, CodexProtocolError)) else None
            cause = (make_reason('harness.shutdown-unverified', operation='account-read')
                     if diagnostic_code == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED'
                     else make_reason('harness.account-read-failed', operation='account-read'))
            failure = {'reason': cause, 'errorType': type(error).__name__, 'message': str(error)[:512],
                       'diagnosticCode': diagnostic_code, 'observedAt': utc_now(),
                       'status': 'unknown' if diagnostic_code == 'deadline' else 'failed'}
            if diagnostic_code == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED':
                self.invalidate('codex', health['revision'], account_read_failure=failure)
                return
        account_observed = failure is None or billing is not None
        with self.board.db.write() as db:
            if self._closed or (self.board.directory / 'upgrade.json').exists():
                return
            row = db.execute("SELECT record_json,revision FROM harness_health WHERE harness='codex'").fetchone()
            if row is None:
                return
            if failure is not None:
                self.board._append_event(db, 'harness.account_read_unknown' if failure['status'] == 'unknown' else 'harness.account_read_failed', payload={
                    'harness': 'codex', 'reason': failure['reason'], 'revision': health['revision'],
                    'currentRevision': row['revision'], 'accountReadFailure': failure})
            if row['revision'] == health['revision']:
                record = json.loads(row['record_json'])
                previous_failure = record.pop('accountReadFailure', None)
                recovered_stop = previous_failure is not None and (previous_failure.get('reason') or {}).get('code') == 'harness.shutdown-unverified'
                if recovered_stop:
                    record['reason'] = None
                if failure is not None:
                    record['accountReadFailure'] = failure
                observed_at = utc_now()
                billing_read = billing is not None
                if billing is None:
                    from ...protocol.billing import fact
                    billing = fact('unknown', 'codex/account-read', observed_at)
                provider = billing.get('provider', 'unknown')
                billing = {key: value for key, value in billing.items() if key != 'provider'}
                if failure is None or failure['status'] != 'unknown' or billing_read:
                    record['billingByProvider'] = {provider: billing}
                db.execute("UPDATE harness_health SET record_json=? WHERE harness='codex'", (canonical_json(record),))
                if recovered_stop:
                    db.execute("UPDATE harness_health SET status='ready',expires_at=? WHERE harness='codex'", (_later(READY_SECONDS),))
                    from ..catalog.catalog import restore_retained_availability
                    restore_retained_availability(db, 'codex')
                if account_observed:
                    self.board._append_event(db, 'harness.account_observed', payload={'harness': 'codex',
                        'observedAt': observed_at, 'billing': record['billingByProvider']})
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
