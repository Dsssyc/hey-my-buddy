"""Reference ownership, immutable evidence and the 33 → 34 install boundary."""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

from blackboard.store.upgrade_fixture import FIXTURE, load_fixture, load_delivered_stock, load_workspace_growth_stock, load_legacy_stock
from hey_my_buddy.blackboard.store import schema_30_31, schema_31_32, schema_32_33, schema_33_34, upgrade
from hey_my_buddy.blackboard.store.db import Database


class WorkspaceReferenceUpgradeTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory(prefix='workspace-ref-upgrade-')
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)

    def restored(self, name='state', *, schema33=True, delivered=False, legacy=None):
        fixture = load_fixture(self.root / name)
        if delivered:
            load_delivered_stock(fixture)
        if legacy is not None:
            load_legacy_stock(fixture, version=legacy)
        db = sqlite3.connect(fixture['state'] / 'board.sqlite3')
        db.row_factory = sqlite3.Row
        self.addCleanup(db.close)
        if schema33:
            db.execute('PRAGMA foreign_keys=OFF')
            with db:
                schema_30_31.upgrade(db)
                schema_31_32.upgrade(db)
                schema_32_33.upgrade(db)
        return fixture, db

    @staticmethod
    def schema(db):
        return [tuple(row) for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]

    @staticmethod
    def files(fixture):
        return {relative: (fixture['state'] / relative).read_bytes() for relative in fixture['resourceFiles']}

    def assert_new_structure(self, db):
        fresh = Database(self.root / 'fresh')
        fresh.initialize()
        self.addCleanup(fresh.close)
        with fresh.read() as connection:
            self.assertEqual(self.schema(db), self.schema(connection))
        self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(), [])
        self.assertEqual([row[0] for row in db.execute('PRAGMA integrity_check')], ['ok'])

    def test_step_owns_its_definitions_and_does_not_import_current_code(self):
        tree = ast.parse(Path(schema_33_34.__file__).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                self.assertEqual(node.level, 0)
                self.assertFalse((node.module or '').startswith('hey_my_buddy'))
            if isinstance(node, ast.Import):
                self.assertFalse(any(alias.name.startswith('hey_my_buddy') for alias in node.names))

    def test_floor_chain_and_direct_33_match_a_new_34_board(self):
        floor, floor_db = self.restored('floor', schema33=False)
        direct, direct_db = self.restored('direct')
        self.assertEqual(upgrade.upgrade_state(floor['state']), {'sourceSchema': 30, 'targetSchema': 34})
        self.assertEqual(upgrade.upgrade_state(direct['state']), {'sourceSchema': 33, 'targetSchema': 34})
        self.assertEqual(self.schema(floor_db), self.schema(direct_db))
        self.assert_new_structure(direct_db)

    def test_staged_writer_stock_extends_the_floor_chain_without_rewriting_it(self):
        fixture, db = self.restored('staged', schema33=False)
        base_seals = [tuple(row) for row in db.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY rowid')]
        load_workspace_growth_stock(fixture, stage='floor30')
        self.assertEqual(db.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0], '30')
        with db:
            schema_30_31.upgrade(db)
            schema_31_32.upgrade(db)
            schema_32_33.upgrade(db)
        load_workspace_growth_stock(fixture, stage='schema33')
        manifests = [json.loads(row[0]) for row in db.execute('SELECT workspace_manifest_json FROM workflow_runs')]
        self.assertEqual({value['version'] for value in manifests}, {3, 4})
        self.assertGreater(db.execute('SELECT COUNT(*) FROM workflow_continuations WHERE workspace_manifest_json IS NULL').fetchone()[0], 0)
        before_seals = [tuple(row) for row in db.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY rowid')]
        before_inputs = [tuple(row) for row in db.execute('SELECT turn_id,input_json FROM workflow_turns ORDER BY rowid')]
        before_results = [tuple(row) for row in db.execute('SELECT attempt_id,result_json FROM attempts ORDER BY rowid')]
        files = self.files(fixture)
        upgrade.upgrade_state(fixture['state'])
        self.assert_new_structure(db)
        self.assertEqual([tuple(row) for row in db.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY rowid')], before_seals)
        self.assertEqual([tuple(row) for row in db.execute('SELECT turn_id,input_json FROM workflow_turns ORDER BY rowid')], before_inputs)
        self.assertEqual([tuple(row) for row in db.execute('SELECT attempt_id,result_json FROM attempts ORDER BY rowid')], before_results)
        self.assertEqual(before_seals[:len(base_seals)], base_seals)
        self.assertEqual(self.files(fixture), files)
        self.assertEqual({row[0] for row in db.execute('SELECT kind FROM workflow_artifacts')},
                         {'input','turn-input','output','resolved-output','partial-output','abandoned-site','shared-refs'})
        self.assertEqual({row[0] for row in db.execute('SELECT output_origin FROM workflow_artifacts WHERE output_origin IS NOT NULL')},
                         {'worker','host'})
        stash = [json.loads(row[0]) for row in db.execute("SELECT manifest_json FROM workflow_artifacts WHERE kind='shared-refs'")]
        observations = [value[key] for value in stash for key in ('startStash','sealStash')]
        self.assertEqual({value['state'] for value in observations}, {'observed','unknown'})
        self.assertTrue(any(value['state'] == 'observed' and value['totalCount'] > 0 for value in observations))
        self.assertTrue(any(value['state'] == 'observed' and value['totalCount'] == 0 for value in observations))
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        for label, run_id in metadata['growth33RunIds'].items():
            run = db.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
            self.assertEqual(run['standalone_project_id'] is None, label == 'macro')
        pending = db.execute("SELECT * FROM workflow_continuations WHERE workspace_facts_role='pending'").fetchall()
        self.assertTrue(pending)
        for row in pending:
            self.assertIsNone(row['workspace_id'])
            self.assertIsNone(row['workspace_manifest_sha256'])
        for statement in (
                "UPDATE workflow_continuations SET workspace_id='extra' WHERE workspace_facts_role='pending'",
                "UPDATE workflow_continuations SET workspace_manifest_sha256=NULL WHERE workspace_facts_role!='pending'",
                "UPDATE workflow_continuations SET checkout_root=NULL WHERE workspace_facts_role='owner'",
                "UPDATE workflow_runs SET standalone_project_id=NULL WHERE macro_task_id IS NULL"):
            db.execute('SAVEPOINT invalid_continuation')
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            finally:
                db.execute('ROLLBACK TO invalid_continuation')
                db.execute('RELEASE invalid_continuation')

    def test_supplement_provenance_and_stage_are_explicit(self):
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        source = Path(__file__).resolve().parents[4]
        for stage, schema, commit in (('growth30', 30, metadata['sourceCommit']),
                                      ('growth33', 33, 'e3459d8a3b03c06d2af192382a788e051ced9a64')):
            stem = 'schema-' + str(schema) + '-workspace-growth'
            self.assertEqual(metadata[stage + 'WriterCommit'], commit)
            self.assertEqual(metadata[stage + 'BaseSha256'], hashlib.sha256((FIXTURE / 'board.sql').read_bytes()).hexdigest())
            self.assertEqual(metadata[stage + 'GeneratorSha256'], '70e3ed545a219aa2fb08b3d2738f73ed0840d16e12fffc6e91a4767a931a77a6')
            for suffix, key in (('stock.sql','SqlSha256'), ('resources.json','ResourcesSha256')):
                self.assertEqual(metadata[stage + key], hashlib.sha256((FIXTURE / (stem + '-' + suffix)).read_bytes()).hexdigest())
        fixture, _db = self.restored('wrong-stage', schema33=False)
        with self.assertRaisesRegex(ValueError, 'requires schema 33'):
            load_workspace_growth_stock(fixture, stage='schema33')

    def test_legacy_derived_stock_preserves_bound_evidence_from_floor_and_33(self):
        from hey_my_buddy.blackboard.tasks import workspace
        from hey_my_buddy.protocol.execution import read_execution_receipt
        for version in (1, 2):
            for direct in (False, True):
                with self.subTest(version=version, direct33=direct):
                    fixture, db = self.restored(f'legacy-{version}-{direct}-路径', schema33=direct, legacy=version)
                    run_id = fixture['stockIds']['helperParent']
                    run = db.execute('SELECT * FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
                    self.assertEqual(run['state'], 'concluded')
                    current = json.loads(run['workspace_manifest_json'])
                    continuation = db.execute('SELECT * FROM workflow_continuations WHERE run_id=?', (run_id,)).fetchone()
                    self.assertEqual(json.loads(continuation['workspace_manifest_json']), current)
                    self.assertEqual(current['version'], version)
                    source_manifests = [current]
                    old_inputs = list(db.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind IN ('input','turn-input')", (run_id,)))
                    self.assertEqual({row['kind'] for row in old_inputs}, {'input','turn-input'})
                    for row in old_inputs:
                        value = json.loads(row['manifest_json'])
                        self.assertEqual(value['version'], version)
                        self.assertEqual(row['manifest_sha256'], value['manifestSha256'])
                        source_manifests.append(value)
                    turns = list(db.execute('SELECT * FROM workflow_turns WHERE run_id=? ORDER BY turn_index', (run_id,)))
                    self.assertEqual(len(turns), 2)
                    for index, turn in enumerate(turns):
                        value = json.loads(turn['input_json'])
                        self.assertEqual(value['version'], version)
                        self.assertEqual(value['executionWorkspace']['version'], version)
                        self.assertEqual(value['context']['task'], 'fixture parent requesting bounded help')
                        self.assertIs(value['context']['taskTruncated'], False)
                        self.assertEqual(value['context']['artifactTotal'], (1, 4)[index])
                        self.assertLess(len(json.dumps(value['context'], ensure_ascii=False).encode()), 65536)
                        source_manifests.append(value['executionWorkspace'])
                        result = json.loads(db.execute('SELECT result_json FROM attempts WHERE attempt_id=?', (turn['attempt_id'],)).fetchone()[0])['result']
                        if 'turn' in result:
                            self.assertEqual(result['turn']['inputSha256'], hashlib.sha256(turn['input_json'].encode()).hexdigest())
                    for value in source_manifests:
                        self.assertIsInstance(value['integrator'], str if version == 1 else dict)
                        expected = hashlib.sha256(json.dumps({key: item for key, item in value.items() if key != 'manifestSha256'},
                                                            sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()
                        self.assertEqual(value['manifestSha256'], expected)
                    by_sha = {value['manifestSha256']: value for value in source_manifests}
                    outputs_to_verify = []
                    for row in db.execute("SELECT * FROM workflow_artifacts WHERE run_id=? AND kind IN ('output','partial-output')", (run_id,)):
                        artifact = json.loads(row['manifest_json'])
                        expected = hashlib.sha256(json.dumps(artifact['snapshot'], ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                        self.assertEqual(artifact['snapshotSha256'], expected)
                        outputs_to_verify.append(artifact)
                    for entry in fixture['operations']:
                        if entry['operation'] == 'execution_result':
                            reference = entry['params']['receipt']
                            self.assertIsNotNone(read_execution_receipt(Path(reference['path']).parent, sha256=reference['sha256']))
                    evidence = {}
                    for table, columns in (('workflow_artifacts','artifact_id,manifest_json'),
                                           ('workflow_turns','turn_id,input_json'), ('attempts','attempt_id,result_json')):
                        query = f'SELECT rowid,{columns} FROM {table} ORDER BY rowid'
                        evidence[query] = [tuple(row) for row in db.execute(query)]
                    files = self.files(fixture)
                    upgrade.upgrade_state(fixture['state'])
                    self.assert_new_structure(db)
                    for query, rows in evidence.items():
                        self.assertEqual([tuple(row) for row in db.execute(query)], rows)
                    self.assertEqual(self.files(fixture), files)
                    # The frozen 32 → 33 step creates the project marker that
                    # today's Git verifier requires; the legacy seals are intact.
                    for artifact in outputs_to_verify:
                        manifest = by_sha[artifact['manifestSha256']]
                        workspace._artifact_binding(manifest, manifest, artifact)
                    after = db.execute('SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?', (run_id,)).fetchone()
                    self.assertEqual(tuple(after), (None, None))
                    for row in db.execute('SELECT * FROM workflow_turns WHERE run_id=?', (run_id,)):
                        value = json.loads(row['input_json'])['executionWorkspace']
                        self.assertEqual((row['workspace_id'],row['workspace_manifest_sha256']), (value['workspaceId'],value['manifestSha256']))
                    owner = db.execute("SELECT workspace_kind,checkout_root,repository_path FROM workflow_continuations WHERE run_id=? AND workspace_facts_role='owner'", (run_id,)).fetchone()
                    self.assertEqual(tuple(owner), (current['kind'],current['checkoutRoot'],current['snapshot']['repositoryPath']))

    def test_legacy_stock_provenance_identifies_sources_and_version_transitions(self):
        metadata = json.loads((FIXTURE / 'metadata.json').read_text())
        legacy = metadata['legacyStock']
        self.assertEqual(legacy['baseSha256'], hashlib.sha256((FIXTURE / 'board.sql').read_bytes()).hexdigest())
        self.assertEqual(legacy['sourceResourcesSha256'], metadata['resourcesSha256'])
        source = Path(__file__).resolve().parents[4]
        self.assertEqual(legacy['generatorSha256'], hashlib.sha256((source / 'packaging/generate-upgrade-fixture.py').read_bytes()).hexdigest())
        self.assertEqual(legacy['history'], {
            'manifest1To2': 'e87ac40ef5475e7fb0768a2b5b47ccd5095292f8',
            'manifest2To3': '8810d564b9b652c02d75b8fa75a50ab1349def51',
            'turnInput1To2': 'd560e1f2b11556beeb555b993ea2f726220794dc',
            'turnInput2To3': '8dc9834a1ab24355694e79b8592f03b039461814'})
        for version in (1,2):
            stem = f'schema-30-legacy-v{version}'
            profile = legacy['profiles'][str(version)]
            for suffix,key in (('stock.sql','sqlSha256'),('resources.json','resourcesSha256')):
                self.assertEqual(profile[key],hashlib.sha256((FIXTURE / (stem + '-' + suffix)).read_bytes()).hexdigest())
            resources = json.loads((FIXTURE / (stem + '-resources.json')).read_text())
            self.assertEqual(resources['sourceRunId'], metadata['stockIds']['helperParent'])
            sources = {(row['table'],row['sourceRowid']) for row in resources['sourceRows']}
            self.assertTrue({('workflow_runs',9),('workflow_continuations',1),('workflow_turns',7),('workflow_turns',9),
                             ('workflow_artifacts',27),('workflow_artifacts',29)} <= sources)
            self.assertEqual(len(resources['sourceManifestPairs']), 2)
            self.assertEqual(profile['updatedRows'],len(resources['sourceRows']))
            self.assertEqual(profile['derivedHashes'],len(resources['hashBindings']))

    def test_legacy_profile_requires_one_fresh_floor_copy(self):
        fixture, db = self.restored('legacy-guard', schema33=False, legacy=1)
        before = list(db.iterdump())
        with self.assertRaisesRegex(ValueError, 'fresh floor copy'):
            load_legacy_stock(fixture, version=2)
        self.assertEqual(list(db.iterdump()), before)
        other, _db = self.restored('legacy-wrong-stage')
        with self.assertRaisesRegex(ValueError, 'requires schema 30'):
            load_legacy_stock(other, version=1)

    def test_every_sealed_byte_identity_and_rowid_survives(self):
        fixture, db = self.restored(delivered=True)
        snapshots = {}
        for table, columns in (
                ('workflow_turns', 'turn_id,input_json'),
                ('attempts', 'attempt_id,result_json'),
                ('workflow_artifacts', 'artifact_id,manifest_json,created_at,run_id,turn_id,attempt_id'),
                ('workflow_continuations', 'continuation_id,input_text,helper_outcomes_json'),
                ('attempt_received_receipts', '*'),
                ('workflow_workspace_conflicts', '*')):
            query = f'SELECT rowid,{columns} FROM {table} ORDER BY rowid'
            snapshots[query] = [tuple(row) for row in db.execute(query)]
        files = self.files(fixture)
        old = [dict(row) for row in db.execute('SELECT rowid,* FROM workflow_artifacts ORDER BY rowid')]
        turn_pairs = {row['turn_id']: (json.loads(row['input_json'])['executionWorkspace']['workspaceId'],
                                      json.loads(row['input_json'])['executionWorkspace']['manifestSha256'])
                      for row in db.execute('SELECT * FROM workflow_turns')}
        upgrade.upgrade_state(fixture['state'])
        for query, rows in snapshots.items():
            self.assertEqual([tuple(row) for row in db.execute(query)], rows)
        self.assertEqual(self.files(fixture), files)
        for before, after in zip(old, db.execute('SELECT rowid,* FROM workflow_artifacts ORDER BY rowid')):
            self.assertEqual(after['content_form'], 'sealed-inline-v1')
            self.assertIsNone(after['record_path'])
            self.assertIsNone(after['record_sha256'])
            column = ('input_manifest_sha256' if before['kind'] in ('input','turn-input') else
                      'shared_refs_sha256' if before['kind'] == 'shared-refs' else 'output_snapshot_sha256')
            self.assertEqual(after[column], before['manifest_sha256'])
            if before['kind'] in ('output', 'partial-output'):
                origin = 'host' if json.loads(before['manifest_json']).get('kind') == 'host-output' else 'worker'
                self.assertEqual(after['output_origin'], origin)
        for row in db.execute('SELECT * FROM workflow_turns'):
            self.assertEqual((row['workspace_id'], row['workspace_manifest_sha256']), turn_pairs[row['turn_id']])
        self.assertEqual({row[0] for row in db.execute('SELECT result_form FROM attempts WHERE result_json IS NOT NULL')},
                         {'archived-report-v1'})
        self.assertEqual(db.execute('SELECT COUNT(*) FROM attempts WHERE result_json IS NULL AND result_form IS NOT NULL').fetchone()[0], 0)
        for table in ('workflow_runs','workflow_children','workflow_continuations'):
            self.assertNotIn('workspace_manifest_json', {row[1] for row in db.execute(f'PRAGMA table_info({table})')})
        self.assertNotIn('manifest_sha256', {row[1] for row in db.execute('PRAGMA table_info(workflow_artifacts)')})
        self.assertFalse({'workspace_id','workspace_manifest_sha256'} &
                         {row[1] for row in db.execute('PRAGMA table_info(workflow_children)')})

    def test_unique_fact_owner_matches_every_frozen_pair(self):
        fixture, db = self.restored()
        expected = {}
        for table, column in (('workflow_artifacts','manifest_json'), ('workflow_continuations','workspace_manifest_json')):
            predicate = "kind='input'" if table == 'workflow_artifacts' else 'workspace_manifest_json IS NOT NULL'
            for (raw,) in db.execute(f'SELECT {column} FROM {table} WHERE {predicate}'):
                value = json.loads(raw)
                expected[value['workspaceId'], value['manifestSha256']] = (
                    value['kind'], value['checkoutRoot'], value['snapshot']['repositoryPath'])
        upgrade.upgrade_state(fixture['state'])
        owners = list(db.execute("SELECT workspace_id,input_manifest_sha256,workspace_kind,checkout_root,repository_path"
                                 " FROM workflow_artifacts WHERE workspace_facts_role='owner' UNION ALL"
                                 " SELECT workspace_id,workspace_manifest_sha256,workspace_kind,checkout_root,repository_path"
                                 " FROM workflow_continuations WHERE workspace_facts_role='owner'"))
        self.assertEqual(len(owners), len(expected))
        self.assertEqual({tuple(row[:2]): tuple(row[2:]) for row in owners}, expected)
        for row in db.execute('SELECT * FROM workflow_runs'):
            if row['macro_task_id'] is None:
                self.assertIsNotNone(row['standalone_project_id'])
            else:
                self.assertIsNone(row['standalone_project_id'])
            self.assertEqual(row['workspace_id'] is None, row['state'] in ('accepted','concluded'))
            self.assertEqual(row['workspace_manifest_sha256'] is None, row['state'] in ('accepted','concluded'))

    def test_missing_or_changed_current_file_refuses_and_names_its_run(self):
        for mutation, category in (('missing', 'manifest-missing'), ('content', 'manifest-digest-mismatch'),
                                   ('binding', 'manifest-binding-mismatch'), ('unsafe', 'manifest-unsafe'),
                                   ('shape', 'manifest-invalid'), ('missing-digest', 'manifest-binding-mismatch')):
            with self.subTest(mutation=mutation):
                fixture, db = self.restored(mutation)
                row = db.execute("SELECT * FROM workflow_runs WHERE state NOT IN ('accepted','concluded') ORDER BY rowid LIMIT 1").fetchone()
                value = json.loads(row['workspace_manifest_json'])
                path = fixture['state'] / 'workspaces' / value['workspaceId'] / 'manifest.json'
                if mutation == 'missing':
                    path.unlink()
                elif mutation == 'shape':
                    path.write_text('[]')
                elif mutation == 'missing-digest':
                    value.pop('manifestSha256')
                    path.write_text(json.dumps(value))
                elif mutation == 'unsafe':
                    if os.name == 'nt':
                        continue
                    path.chmod(0o644)
                else:
                    value['access' if mutation == 'content' else 'workspaceId'] = (('write' if value['access'] == 'read' else 'read')
                        if mutation == 'content' else 'ws-' + 'f' * 32)
                    path.write_text(json.dumps(value))
                before = list(db.iterdump())
                with self.assertRaisesRegex(ValueError, category) as caught:
                    upgrade.upgrade_state(fixture['state'])
                self.assertIn(row['run_id'], str(caught.exception))
                original = json.loads(row['workspace_manifest_json'])
                self.assertIn(original['workspaceId'], str(caught.exception))
                self.assertIn(original['manifestSha256'], str(caught.exception))
                self.assertEqual(list(db.iterdump()), before)

    def test_terminal_inline_history_including_no_turn_needs_no_manifest_file(self):
        fixture, db = self.restored()
        rows = db.execute("SELECT * FROM workflow_runs WHERE state IN ('accepted','concluded')").fetchall()
        selected = [row for row in rows if json.loads(row['workspace_manifest_json'])['kind'] == 'existing']
        self.assertTrue(selected)
        no_turn = False
        sealed = [tuple(row) for row in db.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY rowid')]
        for row in selected:
            value = json.loads(row['workspace_manifest_json'])
            no_turn |= db.execute('SELECT COUNT(*) FROM workflow_turns WHERE run_id=?', (row['run_id'],)).fetchone()[0] == 0
            (fixture['state'] / 'workspaces' / value['workspaceId'] / 'manifest.json').unlink()
        self.assertTrue(no_turn)
        upgrade.upgrade_state(fixture['state'])
        for row in selected:
            current = db.execute('SELECT workspace_id,workspace_manifest_sha256 FROM workflow_runs WHERE run_id=?',
                                 (row['run_id'],)).fetchone()
            self.assertEqual(tuple(current), (None, None))
        self.assertEqual([tuple(row) for row in db.execute('SELECT artifact_id,manifest_json FROM workflow_artifacts ORDER BY rowid')], sealed)

    def test_terminal_checkout_or_cleanup_still_requires_its_allocation_file(self):
        for reason in ('checkout-present','planned','blocked','applying'):
            with self.subTest(reason=reason):
                fixture, db = self.restored('terminal-' + reason)
                if reason == 'checkout-present':
                    row = next(row for row in db.execute("SELECT * FROM workflow_runs WHERE state='accepted'")
                               if json.loads(row['workspace_manifest_json'])['kind'] == 'worktree'
                               and Path(json.loads(row['workspace_manifest_json'])['checkoutRoot']).exists())
                else:
                    plan = db.execute("SELECT * FROM workspace_cleanup_plans WHERE state='applied' LIMIT 1").fetchone()
                    self.assertIsNotNone(plan)
                    row = db.execute('SELECT * FROM workflow_runs WHERE run_id=?', (plan['run_id'],)).fetchone()
                    # Explicit failure experiment: turn a completed private
                    # journal back into the named outstanding action.
                    db.execute('UPDATE workspace_cleanup_plans SET state=?,applied_at=NULL,result_json=NULL WHERE plan_id=?',
                               (reason, plan['plan_id']))
                    db.commit()
                value = json.loads(row['workspace_manifest_json'])
                (fixture['state'] / 'workspaces' / value['workspaceId'] / 'manifest.json').unlink()
                before = list(db.iterdump())
                with self.assertRaisesRegex(ValueError, 'manifest-missing') as caught:
                    upgrade.upgrade_state(fixture['state'])
                self.assertIn(row['run_id'], str(caught.exception))
                self.assertEqual(list(db.iterdump()), before)

    def test_valid_applying_missing_allocation_uses_only_its_frozen_proof(self):
        for valid in (True, False):
            with self.subTest(valid=valid):
                fixture, db = self.restored('missing-allocation-' + str(valid))
                plan = db.execute("SELECT * FROM workspace_cleanup_plans WHERE state='applied' LIMIT 1").fetchone()
                self.assertIsNotNone(plan)
                evidence = json.loads(plan['evidence_json'])
                if not valid:
                    evidence['workspace']['allocation']['manifestSha256'] = 'f' * 64
                db.execute("UPDATE workspace_cleanup_plans SET state='applying',applied_at=NULL,result_json=NULL,evidence_json=? WHERE plan_id=?",
                           (json.dumps(evidence), plan['plan_id']))
                db.commit()
                directory = fixture['state'] / 'workspaces' / plan['workspace_id']
                self.assertFalse((directory / 'checkout').exists())
                shutil.rmtree(directory)
                if valid:
                    upgrade.upgrade_state(fixture['state'])
                    self.assertEqual(db.execute('SELECT state FROM workspace_cleanup_plans WHERE plan_id=?',
                                                (plan['plan_id'],)).fetchone()[0], 'applying')
                    self.assertFalse(directory.exists())
                else:
                    before = list(db.iterdump())
                    with self.assertRaisesRegex(ValueError, 'cleanup-proof-mismatch'):
                        upgrade.upgrade_state(fixture['state'])
                    self.assertEqual(list(db.iterdump()), before)

    def test_reclaimed_checkout_keeps_its_manifest_and_upgrades(self):
        fixture, db = self.restored()
        row = db.execute("SELECT r.* FROM workflow_runs r JOIN workspace_cleanup_plans p ON p.run_id=r.run_id WHERE p.state='applied' LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        value = json.loads(row['workspace_manifest_json'])
        self.assertFalse(Path(value['checkoutRoot']).exists())
        path = fixture['state'] / 'workspaces' / value['workspaceId'] / 'manifest.json'
        raw = path.read_bytes()
        upgrade.upgrade_state(fixture['state'])
        self.assertEqual(path.read_bytes(), raw)

    def test_inconsistent_owner_or_macro_project_refuses_the_whole_upgrade(self):
        for mutation, category in (('facts','workspace-facts-conflict'), ('project','macro-project-conflict'),
                                   ('owner','initial-input-missing')):
            with self.subTest(mutation=mutation):
                fixture, db = self.restored('inconsistent-' + mutation)
                row = db.execute("SELECT r.* FROM workflow_runs r WHERE r.macro_task_id IS NOT NULL ORDER BY rowid LIMIT 1").fetchone()
                if mutation == 'project':
                    db.execute("UPDATE macro_tasks SET project_id='another-project' WHERE macro_task_id=?", (row['macro_task_id'],))
                elif mutation == 'owner':
                    db.execute("DELETE FROM workflow_artifacts WHERE run_id=? AND kind='input'", (row['run_id'],))
                else:
                    value = json.loads(row['workspace_manifest_json'])
                    value['snapshot']['repositoryPath'] += '-changed'
                    db.execute('UPDATE workflow_runs SET workspace_manifest_json=? WHERE run_id=?',
                               (json.dumps(value), row['run_id']))
                db.commit()
                before = list(db.iterdump())
                with self.assertRaisesRegex(ValueError, category) as caught:
                    upgrade.upgrade_state(fixture['state'])
                self.assertIn(row['run_id'], str(caught.exception))
                self.assertEqual(list(db.iterdump()), before)

    def test_shared_input_pair_gets_one_owner_and_one_reference(self):
        fixture, db = self.restored()
        row = dict(db.execute("SELECT * FROM workflow_artifacts WHERE kind='input' LIMIT 1").fetchone())
        row['artifact_id'] = 'copied-frozen-input'
        # The old unique key intentionally permits NULL-attempt input holders.
        self.assertIsNone(row['attempt_id'])
        db.execute('INSERT INTO workflow_artifacts(' + ','.join(row) + ') VALUES(' + ','.join('?' for _ in row) + ')', list(row.values()))
        db.commit()
        upgrade.upgrade_state(fixture['state'])
        pair = json.loads(row['manifest_json'])
        holders = db.execute("SELECT * FROM workflow_artifacts WHERE kind='input' AND workspace_id=? AND input_manifest_sha256=? ORDER BY rowid",
                             (pair['workspaceId'], pair['manifestSha256'])).fetchall()
        self.assertEqual([item['workspace_facts_role'] for item in holders], ['owner', 'reference'])
        self.assertEqual(holders[1]['manifest_json'], row['manifest_json'])
        self.assertEqual(tuple(holders[1][key] for key in ('workspace_kind','checkout_root','repository_path')), (None,None,None))

    def test_current_content_forms_accept_only_their_own_identity(self):
        fixture, db = self.restored()
        upgrade.upgrade_state(fixture['state'])
        db.execute("UPDATE workflow_artifacts SET content_form='input-reference',manifest_json=NULL WHERE kind='input'")
        db.execute("UPDATE workflow_artifacts SET content_form='record-reference',manifest_json=NULL,record_path='output.json',record_sha256=? WHERE kind IN ('output','partial-output','resolved-output','abandoned-site')", ('a' * 64,))
        self.assertGreater(db.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE content_form='input-reference'").fetchone()[0], 0)
        self.assertGreater(db.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE content_form='record-reference'").fetchone()[0], 0)
        self.assertEqual(db.execute("SELECT COUNT(*) FROM workflow_artifacts WHERE kind='shared-refs' AND content_form!='sealed-inline-v1'").fetchone()[0], 0)
        for statement in (
                "UPDATE workflow_artifacts SET shared_refs_sha256='extra' WHERE kind='input'",
                "UPDATE workflow_artifacts SET input_manifest_sha256='extra' WHERE kind='output'",
                "UPDATE workflow_artifacts SET manifest_json='{}' WHERE content_form='record-reference'",
                "UPDATE workflow_artifacts SET output_origin='host' WHERE kind='resolved-output'"):
            db.execute('SAVEPOINT invalid_content')
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(statement)
            finally:
                db.execute('ROLLBACK TO invalid_content')
                db.execute('RELEASE invalid_content')

    def test_checked_pairs_are_deduplicated_at_the_boundary(self):
        fixture, _db = self.restored()
        with mock.patch.object(schema_33_34, '_verify_file', wraps=schema_33_34._verify_file) as verify:
            upgrade.upgrade_state(fixture['state'])
        pairs = [call.args[1] for call in verify.call_args_list]
        self.assertTrue(pairs)
        self.assertEqual(len(pairs), len(set(pairs)))

    def test_intermediate_and_final_failures_roll_back_board_and_leave_files_unchanged(self):
        for index, failure in enumerate(('before-rebuild','after-rebuild','before-commit')):
            with self.subTest(failure=failure):
                fixture, db = self.restored('rollback-' + str(index))
                before = list(db.iterdump())
                raw_before = (fixture['state'] / 'board.sqlite3').read_bytes()
                files = self.files(fixture)
                rebuild = schema_33_34._rebuild
                real = upgrade.STEPS[33]
                def fail_rebuild(*args):
                    if failure == 'after-rebuild':
                        rebuild(*args)
                    raise RuntimeError(failure)
                def fail_commit(connection):
                    real(connection)
                    raise RuntimeError(failure)
                patch = (mock.patch.dict(upgrade.STEPS, {33: fail_commit}) if failure == 'before-commit' else
                         mock.patch.object(schema_33_34, '_rebuild', side_effect=fail_rebuild))
                with patch, self.assertRaisesRegex(RuntimeError, failure):
                    upgrade.upgrade_state(fixture['state'])
                self.assertEqual(list(db.iterdump()), before)
                self.assertEqual((fixture['state'] / 'board.sqlite3').read_bytes(), raw_before)
                self.assertEqual(self.files(fixture), files)

    def test_null_combinations_are_enforced_by_sql(self):
        fixture, db = self.restored()
        upgrade.upgrade_state(fixture['state'])
        statements = [
            "UPDATE workflow_runs SET workspace_id=NULL WHERE state='failed'",
            "UPDATE workflow_runs SET workspace_manifest_sha256=NULL WHERE state='cancelled'",
            "UPDATE workflow_runs SET workspace_id='ws-invalid' WHERE state='accepted'",
            "UPDATE workflow_runs SET standalone_project_id='duplicate' WHERE macro_task_id IS NOT NULL",
            "UPDATE workflow_turns SET workspace_id=NULL",
            "UPDATE workflow_artifacts SET input_manifest_sha256=NULL WHERE kind='input'",
            "UPDATE workflow_artifacts SET output_snapshot_sha256=NULL WHERE kind='output'",
            "UPDATE workflow_artifacts SET shared_refs_sha256=NULL WHERE kind='shared-refs'",
            "UPDATE workflow_artifacts SET output_origin=NULL WHERE kind='output'",
            "UPDATE workflow_artifacts SET record_path='extra',record_sha256='extra' WHERE kind='shared-refs'",
            "UPDATE workflow_artifacts SET workspace_facts_role=NULL WHERE kind='input'",
            "UPDATE workflow_artifacts SET checkout_root=NULL WHERE workspace_facts_role='owner'",
            "UPDATE workflow_artifacts SET content_form='input-reference',manifest_json=NULL WHERE kind='turn-input'",
            "UPDATE attempts SET result_form=NULL WHERE result_json IS NOT NULL",
            "UPDATE attempts SET result_json=NULL WHERE result_form IS NOT NULL",
        ]
        for statement in statements:
            with self.subTest(sql=statement):
                db.execute('SAVEPOINT invalid_shape')
                try:
                    with self.assertRaises(sqlite3.IntegrityError):
                        db.execute(statement)
                finally:
                    db.execute('ROLLBACK TO invalid_shape')
                    db.execute('RELEASE invalid_shape')


if __name__ == '__main__':
    unittest.main()
