"""Service-owned, generation-fenced harness health; discovery never holds SQLite."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from collections import ChainMap
import json
import os
from pathlib import Path
import threading

from ..store.db import canonical_json, utc_now
from ...errors import BoardError

HARNESSES = ('dsh', 'zcode', 'codex', 'claude')
SCAN_SECONDS = 180
READY_SECONDS = 3600
MAX_PATH_HINTS = 64


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
        raise BoardError('UNSUPPORTED_ADAPTER', 'Expected dsh, zcode, codex or claude')
    return name


def read_health(connection, adapter):
    from ...buddy.harnesses.registry import adapter as executor
    from ..catalog.accounts import view as account_view
    eligibility = executor(adapter).local_read_only_check()
    account = account_view(connection, adapter)
    row = connection.execute('SELECT * FROM harness_health WHERE adapter=?', (adapter,)).fetchone()
    if row is None:
        record = {'adapter': adapter, 'status': 'unknown', 'available': False, 'revision': 0,
                'manualPath': None, 'reasonCode': 'HARNESS_NOT_CHECKED', 'remedy': 'Run buddy adapters with refresh:true'}
        return {**record, 'account': account, 'systemSandbox': eligibility['systemSandbox'],
                'readOnlyStructured': eligibility}
    from ..evaluation.native_observations import quota_view, latest_quota
    from ..routing.quota_routing import routing_facts
    record = json.loads(row['record_json'])
    record.pop('reviewVerification', None)
    billing = record.get('billingByProvider') or {}
    if adapter == "dsh":
        from ...protocol.billing import fact
        catalog = connection.execute("SELECT d.payload_json,c.updated_at FROM catalog_current c JOIN evaluation_catalog d ON d.discovery_id=c.discovery_id WHERE c.adapter='dsh' AND c.status='complete'").fetchone()
        if catalog:
            try:
                entries = json.loads(catalog["payload_json"])["providers"]
            except (ValueError, TypeError, KeyError):
                entries = []
            billing = {entry["provider"]: fact("metered" if entry.get("packageName") == "@deepseek-ai/dsh-llm-deepseek" else "unknown",
                                                "dsh/deepseek-api-key", catalog["updated_at"])
                       for entry in entries if entry.get("adapter") == adapter and isinstance(entry.get("provider"), str)}
    status = row['status']
    record = {**record, "billingByProvider": billing, "quota": quota_view(latest_quota(connection, adapter)),
            "quotaRouting": routing_facts(connection, adapter), 'adapter': adapter,
            'available': status == 'ready', 'status': status, 'account': account, 'revision': row['revision'], 'manualPath': row['manual_path'],
            'checkedAt': row['checked_at'], 'expiresAt': row['expires_at'], 'scanAfter': row['scan_after']}
    return {**record, 'systemSandbox': eligibility['systemSandbox'], 'readOnlyStructured': eligibility}


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
        parts = dict.fromkeys(part for part in path.split(os.pathsep) if part and Path(part).is_absolute())
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
                if db.execute('SELECT 1 FROM harness_health WHERE adapter=?', (name,)).fetchone() is None:
                    db.execute('INSERT INTO harness_health(adapter) VALUES(?)', (name,))
                    from ..catalog.catalog import CATALOG_FACT_REASONS
                    db.execute("UPDATE buddies SET available=0,unavailable_reason='HARNESS_NOT_CHECKED'"
                               ' WHERE adapter=? AND (unavailable_reason IS NULL OR unavailable_reason NOT IN (?,?))',
                               (name, *CATALOG_FACT_REASONS))

    def get(self, name):
        with self.board.db.read() as db:
            return read_health(db, _name(name))

    def invalidate(self, name, revision, reason='HARNESS_START_FAILED', *, account_read_failure=None):
        with self.board.db.write() as db:
            row = read_health(db, _name(name))
            if account_read_failure is not None and (self._closed or (self.board.directory / 'upgrade.json').exists()):
                return row
            if row['revision'] != revision:
                if account_read_failure is None:
                    return row
                self.board._append_event(db, 'harness.account_read_failed', payload={
                    'adapter': name, 'reasonCode': reason, 'revision': revision,
                    'currentRevision': row['revision'], 'accountReadFailure': account_read_failure})
            else:
                record = json.loads(db.execute('SELECT record_json FROM harness_health WHERE adapter=?', (name,)).fetchone()[0])
                record.update(reasonCode=reason, available=False)
                if account_read_failure is not None:
                    record['accountReadFailure'] = account_read_failure
                db.execute("UPDATE harness_health SET status='unhealthy',record_json=?,expires_at=NULL,scan_after=NULL,revision=revision+1 WHERE adapter=?", (canonical_json(record), name))
                # ADR-027: a health failure must not erase catalog facts. Rows a
                # confirmed absence or an effort retirement already grounded stay
                # with their own reason; health only grounds the rest.
                from ..catalog.catalog import CATALOG_FACT_REASONS
                db.execute('UPDATE buddies SET available=0,unavailable_reason=?'
                           ' WHERE adapter=? AND (unavailable_reason IS NULL OR unavailable_reason NOT IN (?,?))',
                           (reason, name, *CATALOG_FACT_REASONS))
                self.board._append_event(db, 'harness.invalidated', payload={'adapter': name, 'reasonCode': reason,
                    **({'accountReadFailure': account_read_failure} if account_read_failure is not None else {})})
            head = self.board._head_of(db)
        self.board._notify(head)
        return self.get(name)

    def all(self):
        with self.board.db.read() as db:
            return [read_health(db, name) for name in HARNESSES]

    def set_path(self, name, path):
        name = _name(name)
        if path is not None and (not isinstance(path, str) or len(path) > 4096 or not Path(path).expanduser().is_absolute()):
            raise BoardError('INVALID_ARGUMENT', 'path must be an absolute executable path, or null for automatic detection')
        with self.board.db.write() as db:
            db.execute("INSERT INTO harness_health(adapter,manual_path,revision) VALUES(?,?,1) ON CONFLICT(adapter) DO UPDATE SET manual_path=excluded.manual_path,revision=revision+1,status='unknown',record_json='{}',checked_at=NULL,expires_at=NULL,scan_after=NULL", (name, str(Path(path).expanduser()) if path else None))
            from ..catalog.catalog import CATALOG_FACT_REASONS
            db.execute("UPDATE buddies SET available=0,unavailable_reason='HARNESS_NOT_CHECKED'"
                       ' WHERE adapter=? AND (unavailable_reason IS NULL OR unavailable_reason NOT IN (?,?))',
                       (name, *CATALOG_FACT_REASONS))
            self.board._append_event(db, 'harness.path_changed', payload={'adapter': name, 'manualPath': path})
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
            if not force and unchanged and (old.get('expiresAt') or '') > now:
                if name == "codex":
                    self._codex_account_read(old)
                if stale_catalog_due:
                    current = self.get(name)
                    if current['available']:
                        self._shared_stale_catalog_read(name, current)
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
            retained = {key: old[key] for key in ("billingByProvider", "accountReadFailure") if key in old}
            if record.get("status") != "ready" or old.get("fingerprint") != record.get("fingerprint"):
                retained.pop("billingByProvider", None)
            record = {**retained, **record, 'locationFingerprint': signature,
                      'systemSandbox': old['systemSandbox']}
            status = record.get('status')
            native_ready = status == 'ready'
            if native_ready and record.get('accountReadFailure', {}).get('reasonCode') == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED':
                status = 'unhealthy'
                record.update(status=status, available=False, reasonCode=record['accountReadFailure']['reasonCode'])
            if status not in ('ready', 'missing', 'login-required', 'unhealthy'):
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery returned an invalid health status')
            if len(canonical_json(record).encode()) > 65536:
                raise BoardError('HARNESS_INVALID_RESULT', 'Discovery diagnostics exceeded their bound')
            with self.board.db.write() as db:
                if (self.board.directory / 'upgrade.json').exists():
                    return read_health(db, name)
                if read_health(db, name)['revision'] != generation:
                    return read_health(db, name)
                db.execute('UPDATE harness_health SET status=?,record_json=?,checked_at=?,expires_at=? WHERE adapter=? AND revision=?',
                           (status, canonical_json(record), now, _later(READY_SECONDS if status == 'ready' else SCAN_SECONDS), name, generation))
                if status != 'ready':
                    # Same boundary as invalidate: health grounds the rows it
                    # marks, never the ones a catalog fact already grounded.
                    from ..catalog.catalog import CATALOG_FACT_REASONS
                    db.execute('UPDATE buddies SET available=0,unavailable_reason=?'
                               ' WHERE adapter=? AND (unavailable_reason IS NULL OR unavailable_reason NOT IN (?,?))',
                               (record.get('reasonCode') or 'HARNESS_UNHEALTHY', name, *CATALOG_FACT_REASONS))
                elif old['status'] != 'ready':
                    # ADR-027: health availability and adopted catalog facts are
                    # separate. Recovering health restores the model availability
                    # of the retained confirmed catalog (and of model services still
                    # awaiting disappearance confirmation); confirmed absences,
                    # retired efforts and native unavailable declarations are
                    # catalog facts and stay untouched.
                    from ..catalog.catalog import restore_retained_availability
                    restore_retained_availability(db, name)
                self.board._append_event(db, 'harness.checked', payload={'adapter': name, 'status': status, 'revision': generation,
                                          'version': record.get('version'), 'reasonCode': record.get('reasonCode')})
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
        from ..evaluation.native_observations import _time
        with self.board.db.read() as db:
            row = db.execute('SELECT scan_after FROM harness_health WHERE adapter=?', (name,)).fetchone()
        if row is None:
            return True
        moment, due = _time(now), _time(row['scan_after'])
        return due is None or moment is None or moment >= due

    def _note_catalog_scan(self, name):
        with self.board.db.write() as db:
            db.execute('UPDATE harness_health SET scan_after=? WHERE adapter=?', (_later(SCAN_SECONDS), name))

    def _catalog_expired(self, name, now):
        """ADR-027 rule 4: a catalog past its shelf life needs a confirmed re-read.

        Only a confirmed (trusted) reading advances the confirmed-read time, so
        a streak of unknown readings never extends that deadline. A board with
        no confirmed reading yet — cold start, or an explicit upgrade —
        measures the shelf life from the last recorded observation instead, and
        a harness with no observation at all has nothing stale to re-read.
        """
        from ..catalog.catalog import CATALOG_SHELF_SECONDS, catalog_read_at
        from ..evaluation.native_observations import _time
        with self.board.db.read() as db:
            read_at = catalog_read_at(db, name)
            if read_at is None:
                row = db.execute('SELECT updated_at FROM catalog_current WHERE adapter=?', (name,)).fetchone()
                read_at = row['updated_at'] if row is not None else None
        if read_at is None:
            return False
        read, moment = _time(read_at), _time(now)
        return read is None or moment is None or (moment - read).total_seconds() >= CATALOG_SHELF_SECONDS

    def _codex_account_read(self, health):
        """One on-demand native read per harness every 180 seconds."""
        stop_unconfirmed = health.get('accountReadFailure', {}).get('reasonCode') == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED'
        if (health.get('status') != 'ready' and not stop_unconfirmed) or not health.get('command'):
            return
        if self._closed or (self.board.directory / 'upgrade.json').exists():
            return
        from ..evaluation.native_observations import _time
        with self.board.db.write() as db:
            row = db.execute("SELECT checked_at FROM harness_account_reads WHERE adapter='codex'").fetchone()
            now = utc_now()
            checked, moment = _time(row[0]) if row else None, _time(now)
            if checked and 0 <= (moment - checked).total_seconds() < SCAN_SECONDS:
                return
            db.execute('INSERT INTO harness_account_reads(adapter,checked_at) VALUES(?,?) '
                       'ON CONFLICT(adapter) DO UPDATE SET checked_at=excluded.checked_at', ('codex', now))
        from ...buddy.harnesses.codex.protocol import CodexProtocolError
        billing = quota = failure = None
        try:
            from ...buddy.harnesses.codex.account_probe import read
            billing, quota, error = read(health['command'], self.environment())
        except (BoardError, CodexProtocolError, OSError, ValueError, RuntimeError) as caught:
            error = caught
        if error is not None:
            reason = error.code if isinstance(error, (BoardError, CodexProtocolError)) else 'HARNESS_ACCOUNT_READ_FAILED'
            failure = {'reasonCode': reason, 'errorType': type(error).__name__, 'message': str(error)[:512],
                       'observedAt': utc_now()}
            if reason == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED':
                self.invalidate('codex', health['revision'], reason, account_read_failure=failure)
                return
        account_observed = failure is None or billing is not None
        with self.board.db.write() as db:
            if self._closed or (self.board.directory / 'upgrade.json').exists():
                return
            row = db.execute("SELECT record_json,revision FROM harness_health WHERE adapter='codex'").fetchone()
            if row is None:
                return
            if failure is not None:
                self.board._append_event(db, 'harness.account_read_failed', payload={
                    'adapter': 'codex', 'reasonCode': failure['reasonCode'], 'revision': health['revision'],
                    'currentRevision': row['revision'], 'accountReadFailure': failure})
            if row['revision'] == health['revision']:
                if quota is not None:
                    from ..evaluation.native_observations import record_quota
                    record_quota(db, 'codex', quota)
                record = json.loads(row['record_json'])
                previous_failure = record.pop('accountReadFailure', None)
                recovered_stop = previous_failure is not None and previous_failure['reasonCode'] == 'HARNESS_ACCOUNT_STOP_UNCONFIRMED'
                if recovered_stop:
                    record.pop('reasonCode', None)
                    record.update(status='ready', available=True)
                if failure is not None:
                    record['accountReadFailure'] = failure
                observed_at = utc_now()
                if billing is None:
                    from ...protocol.billing import fact
                    billing = fact('unknown', 'codex/account-read', observed_at)
                provider = billing.get('provider', 'unknown')
                billing = {key: value for key, value in billing.items() if key != 'provider'}
                record['billingByProvider'] = {provider: billing}
                db.execute("UPDATE harness_health SET record_json=? WHERE adapter='codex'", (canonical_json(record),))
                if recovered_stop:
                    db.execute("UPDATE harness_health SET status='ready',expires_at=? WHERE adapter='codex'", (_later(READY_SECONDS),))
                    from ..catalog.catalog import restore_retained_availability
                    restore_retained_availability(db, 'codex')
                if account_observed:
                    self.board._append_event(db, 'harness.account_observed', payload={'adapter': 'codex',
                        'observedAt': observed_at, 'billing': record['billingByProvider'], 'quotaRecorded': quota is not None})
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
