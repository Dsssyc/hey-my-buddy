"""Private backup publication, inventory, and recovery tests."""
import hashlib
import gzip
import json
import os
from pathlib import Path
import sqlite3
import stat
import unittest
from unittest import mock

from hey_my_buddy.blackboard.store import backup
from hey_my_buddy.blackboard.store.db import SCHEMA_VERSION
from blackboard.store.upgrade_fixture import load_fixture
from hey_my_buddy.protocol.contracts import CONTRACT_VERSION
from hey_my_buddy.errors import BoardError
from support import BoardTestCase


STATE_FILES = ('console-settings.json',)


class BackupTests(BoardTestCase):
    def test_fixture_backup_retains_current_schema_and_exact_bytes(self):
        fixture = load_fixture(self.directory)
        before = (self.directory / 'board.sqlite3').read_bytes()
        current = Path(backup.create(self.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        self.assertEqual(backup.verify(current)['schema'], SCHEMA_VERSION)
        self.assertEqual(fixture['schemaVersion'], SCHEMA_VERSION)
        self.assertEqual(gzip.decompress((current / 'board.sqlite3.gz').read_bytes()), before)
        self.assertEqual((self.directory / 'board.sqlite3').read_bytes(), before)

    def test_roundtrip_contains_board_and_existing_state_files_only(self):
        board = self.board()
        board.store.db.close()
        for name in STATE_FILES:
            (board.directory / name).write_text(json.dumps({'name': name, 'generation': 1}), encoding="utf-8", newline="\n")
        for name in ('runtime-retention.json', 'active-runtime.json', 'launch-settings.json'):
            (board.directory / name).write_text(json.dumps({'name': name, 'generation': 1}), encoding="utf-8", newline="\n")
        for name in ('controls/x.json', 'attempts/run/attempt/result.json',
                     'workspaces/large', 'harnesses/zcode/native', 'console-sessions.json'):
            path = board.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('retained evidence', encoding="utf-8", newline="\n")
        first = backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        current = Path(first['path'])
        manifest = backup.verify(current)
        self.assertTrue(first['verified'])
        self.assertEqual(set(manifest['files']), {'board.sqlite3.gz', *(str(Path('state') / name) for name in STATE_FILES)})
        self.assertEqual((current / 'state/console-settings.json').stat().st_mode & 0o777, 0o600)
        (board.directory / 'console-settings.json').write_text('{"generation":2}', encoding="utf-8", newline="\n")
        second = backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual(first['path'], second['path'])
        self.assertEqual(json.loads((current / 'state/console-settings.json').read_text(encoding="utf-8")), {'generation': 2})
        self.assertEqual([path.name for path in current.parent.iterdir() if path.is_dir()], ['current'])

    def test_missing_state_files_are_optional(self):
        board = self.board()
        board.store.db.close()
        manifest = backup.verify(Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path']))
        self.assertEqual(set(manifest['files']), {'board.sqlite3.gz'})

    def test_verify_rejects_out_of_scope_manifest_entry_with_valid_hash(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        outside = current / 'state/attempts/run/result.json'
        outside.parent.mkdir(parents=True)
        outside.write_bytes(b'outside backup scope')
        manifest_path = current / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest['files']['state/attempts/run/result.json'] = {
            'bytes': outside.stat().st_size, 'sha256': hashlib.sha256(outside.read_bytes()).hexdigest()}
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        with self.assertRaises(BoardError) as caught:
            backup.verify(current)
        self.assertEqual(caught.exception.code, 'BACKUP_INVALID')

    def test_verify_refuses_database_schema_that_disagrees_with_manifest(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                     contract_version=CONTRACT_VERSION)['path'])
        path = current / 'manifest.json'
        manifest = json.loads(path.read_text(encoding="utf-8"))
        source = self.directory / 'mismatched.sqlite3'
        source.write_bytes(gzip.decompress((current / 'board.sqlite3.gz').read_bytes()))
        with sqlite3.connect(source) as connection:
            connection.execute("UPDATE meta SET value='25' WHERE key='schema_version'")
            manifest['databaseSnapshot'] = backup.database_snapshot(connection)
        connection.close()
        payload = current / 'board.sqlite3.gz'
        payload.write_bytes(gzip.compress(source.read_bytes()))
        manifest['files']['board.sqlite3.gz'] = {'bytes': payload.stat().st_size,
                                                'sha256': hashlib.sha256(payload.read_bytes()).hexdigest()}
        path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
        before = {file: file.read_bytes() for file in current.rglob('*') if file.is_file()}
        with self.assertRaises(BoardError) as caught:
            backup.verify(current)
        self.assertEqual(caught.exception.code, 'BACKUP_INVALID')
        self.assertEqual(caught.exception.message, 'Backup schema does not match its manifest')
        self.assertEqual({file: file.read_bytes() for file in current.rglob('*') if file.is_file()}, before)

    def test_verify_reports_incomplete_manifest_and_invalid_sqlite_as_backup_invalid(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                     contract_version=CONTRACT_VERSION)['path'])
        manifest_path = current / 'manifest.json'
        payload_path = current / 'board.sqlite3.gz'
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original_payload = payload_path.read_bytes()
        empty_sqlite = self.directory / 'empty.sqlite3'
        with sqlite3.connect(empty_sqlite):
            pass
        cases = ('missing_files', 'missing_hash', 'missing_meta', 'not_sqlite')
        for case in cases:
            with self.subTest(case=case):
                manifest = json.loads(json.dumps(original_manifest))
                payload = original_payload
                if case == 'missing_files':
                    del manifest['files']
                elif case == 'missing_hash':
                    del manifest['files']['board.sqlite3.gz']['sha256']
                else:
                    payload = gzip.compress(empty_sqlite.read_bytes() if case == 'missing_meta' else b'not a database')
                    manifest['files']['board.sqlite3.gz'] = {
                        'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                payload_path.write_bytes(payload)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
                with self.assertRaises(BoardError) as caught:
                    backup.verify(current)
                self.assertEqual(caught.exception.code, 'BACKUP_INVALID')
        payload_path.write_bytes(original_payload)
        manifest_path.write_text(json.dumps(original_manifest), encoding="utf-8", newline="\n")

    def test_verify_refuses_unsupported_schema(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        path = current / 'manifest.json'
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for version in (21, 23, 24, 25, 26, 28):
            with self.subTest(schema=version):
                manifest['schema'] = version
                path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
                with self.assertRaises(BoardError) as caught:
                    backup.verify(current)
                self.assertEqual(caught.exception.code, 'BACKUP_INVALID')
                self.assertEqual(caught.exception.message, 'Unsupported backup format or schema')

    def test_verify_reports_compressed_payload_corruption_with_matching_digest(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None,
                                     contract_version=CONTRACT_VERSION)['path'])
        manifest_path = current / 'manifest.json'
        payload_path = current / 'board.sqlite3.gz'
        original_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        original_payload = payload_path.read_bytes()
        cases = {
            'truncated': original_payload[:-8],
            'crc': original_payload[:-8] + bytes([original_payload[-8] ^ 1]) + original_payload[-7:],
            'magic': bytes([original_payload[0] ^ 1]) + original_payload[1:],
            'deflate': original_payload[:10] + bytes([(original_payload[10] & ~6) | 6]) + original_payload[11:],
        }
        for kind, payload in cases.items():
            with self.subTest(kind=kind):
                manifest = json.loads(json.dumps(original_manifest))
                manifest['files']['board.sqlite3.gz'] = {
                    'bytes': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
                payload_path.write_bytes(payload)
                manifest_path.write_text(json.dumps(manifest), encoding="utf-8", newline="\n")
                with self.assertRaises(BoardError) as caught:
                    backup.verify(current)
                self.assertEqual(caught.exception.code, 'BACKUP_INVALID')
                self.assertEqual(caught.exception.message, 'Backup compressed database is invalid')
                self.assertTrue(caught.exception.details['reason'])

    def test_offline_archive_refuses_an_uncheckpointed_wal_without_publication(self):
        board = self.board()
        with board.store.db.connect() as writer:
            writer.execute("INSERT INTO meta(key,value) VALUES('live-wal','retained')")
            before = (board.directory / 'board.sqlite3').read_bytes()
            wal = (board.directory / 'board.sqlite3-wal').read_bytes()
            with self.assertRaises(BoardError) as caught:
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
            self.assertEqual(caught.exception.code, 'BACKUP_INVALID')
            self.assertEqual((board.directory / 'board.sqlite3').read_bytes(), before)
            self.assertEqual((board.directory / 'board.sqlite3-wal').read_bytes(), wal)
            self.assertFalse((board.directory / 'backups').exists())

    def test_failed_validation_keeps_old_backup_and_retries_incoming(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        original = (current / 'manifest.json').read_bytes()
        with mock.patch.object(backup, 'verify', side_effect=BoardError('BACKUP_INVALID', 'injected')):
            with self.assertRaises(BoardError):
                backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual((current / 'manifest.json').read_bytes(), original)
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        backup.verify(current)
        self.assertFalse((current.parent / '.incoming').exists())

    def test_tamper_is_detected_and_linked_retained_evidence_is_untouched(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        with (current / 'board.sqlite3.gz').open('ab') as stream:
            stream.write(b'tamper')
        with self.assertRaises(BoardError):
            backup.verify(current)
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_text('retained evidence', encoding="utf-8", newline="\n")
        evidence = board.directory / 'attempts/run/attempt/result.json'
        evidence.parent.mkdir(parents=True)
        evidence.symlink_to(sentinel)
        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'retained evidence')
        self.assertTrue(evidence.is_symlink())

    def test_linked_preserved_state_file_is_refused_without_publication(self):
        board = self.board()
        board.store.db.close()
        sentinel = self.directory / 'outside-sentinel'
        sentinel.write_text('keep outside', encoding="utf-8", newline="\n")
        state_file = board.directory / 'console-settings.json'
        state_file.symlink_to(sentinel)
        with self.assertRaises(BoardError) as caught:
            backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
        self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
        self.assertEqual(caught.exception.details['path'], str(state_file))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), 'keep outside')
        self.assertFalse((board.directory / 'backups/current').exists())

    def test_retained_paths_do_not_join_backup_or_block_creation(self):
        board = self.board()
        board.store.db.close()
        native = board.directory / 'attempts/run/attempt/native/codex-home'
        native.mkdir(parents=True)
        secret = self.directory / 'private-auth.json'
        secret.write_text('private credential', encoding="utf-8", newline="\n")
        (native / 'auth.json').symlink_to(secret)
        checkout = board.directory / 'workspaces/large'
        checkout.parent.mkdir()
        checkout.write_text('unchanged checkout', encoding="utf-8", newline="\n")
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        self.assertEqual(set(backup.verify(current)['files']), {'board.sqlite3.gz'})
        self.assertTrue((native / 'auth.json').is_symlink())
        self.assertEqual(secret.read_text(encoding="utf-8"), 'private credential')
        self.assertEqual(checkout.read_text(encoding="utf-8"), 'unchanged checkout')

    @unittest.skipUnless(hasattr(os, 'mkfifo'), 'requires POSIX FIFOs')
    def test_special_rollback_source_refuses_publication_and_keeps_previous_generation(self):
        board = self.board()
        board.store.db.close()
        current = Path(backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)['path'])
        before = (current / 'manifest.json').read_bytes()
        source = board.directory / 'console-settings.json'
        for kind in ('directory', 'fifo'):
            with self.subTest(kind=kind):
                if kind == 'directory':
                    source.mkdir()
                else:
                    os.mkfifo(source)
                try:
                    with self.assertRaises(BoardError) as caught:
                        backup.create(board.directory, runtime_identity=None, plugin_commit=None, contract_version=CONTRACT_VERSION)
                    self.assertEqual(caught.exception.code, 'BACKUP_UNSAFE_PATH')
                    self.assertEqual((current / 'manifest.json').read_bytes(), before)
                    self.assertEqual(stat.S_ISDIR(source.lstat().st_mode), kind == 'directory')
                finally:
                    source.rmdir() if kind == 'directory' else source.unlink()

    def test_snapshot_keeps_historical_json_sort_and_exemption_format(self):
        with sqlite3.connect(self.directory / "fingerprint.sqlite3") as connection:
            connection.execute("CREATE TABLE workers(id INTEGER PRIMARY KEY AUTOINCREMENT,value TEXT)")
            connection.execute("CREATE TABLE events(seq INTEGER PRIMARY KEY AUTOINCREMENT,kind TEXT)")
            connection.execute('CREATE TABLE "quoted table"(value,other)')
            connection.executemany('INSERT INTO "quoted table" VALUES(?,?)', [
                ("中文", b"\x00\xff"), ("é", 2), ("😀", None), ("z\n", 1.25),
                ("a", "duplicate"), ("a", "duplicate"), (10, True), (2, False)])
            connection.execute("INSERT INTO workers(value) VALUES('ignored')")
            connection.executemany("INSERT INTO events(kind) VALUES(?)", [('before',), ('after',)])
            connection.execute('CREATE TABLE generated(value INTEGER,derived INTEGER GENERATED ALWAYS AS(value+1))')
            connection.execute('INSERT INTO generated(value) VALUES(10)')
            connection.setlimit(sqlite3.SQLITE_LIMIT_FUNCTION_ARG, 127)
            connection.execute('CREATE TABLE wide(' + ','.join('c' + str(index) for index in range(130)) + ')')
            connection.execute('INSERT INTO wide VALUES(' + ','.join('?' for _ in range(130)) + ')', tuple(range(130)))
            historical = {}
            names = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            for name in names:
                sql = 'SELECT * FROM "' + name.replace('"', '""') + '"'
                if name == 'events':
                    sql += ' WHERE seq<=1'
                elif name == 'sqlite_sequence':
                    sql += " WHERE name NOT IN ('events')"
                rows = sorted(json.dumps(list(row), ensure_ascii=False, separators=(',', ':'),
                                         default=lambda value:value.hex()) for row in connection.execute(sql))
                historical[name] = hashlib.sha256('\n'.join(rows).encode()).hexdigest()
            try:
                actual = backup.database_snapshot(connection, event_head=1)
            except sqlite3.DatabaseError as error:
                self.fail("Historical SELECT * fingerprint projection was refused: " + str(error))
            self.assertEqual(actual, {'tables': {name:connection.execute('SELECT COUNT(*) FROM "' + name + '"').fetchone()[0]
                                               for name in names}, 'fingerprints': historical, 'eventHead': 1})
            connection.execute("UPDATE workers SET value='changed'")
            self.assertNotEqual(backup.database_snapshot(connection, event_head=1), actual)
            self.assertNotEqual(backup.database_snapshot(connection)['fingerprints']['events'], historical['events'])
