"""B08/B09: selective reuse over real, fictional SQL relationships."""
from __future__ import annotations

import json
import hashlib
import copy
import shutil
import sqlite3
import threading
import unittest
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

from support import private_state_dir
from blackboard.tasks.test_console_objective_fixture import fixture_module, SOURCE
from hey_my_buddy.blackboard.tasks import objectives, objective_summary_cache as memo


class ObjectiveSummaryCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Generate the admitted relationships once, then restore a private DB
        # and fresh Python fixture for every case. Keep fixed artifact bindings
        # to the seed's immutable evidence instead of rewriting signed JSON.
        cls.seed_root = cls.enterClassContext(private_state_dir("objective-summary-seed-"))
        with fixture_module.SyntheticBoard(
            cls.seed_root / "state", cls.seed_root / "runtime", SOURCE,
            recipe=fixture_module.Recipe.smoke(), seed=613,
        ) as seed:
            cls.snapshot = sqlite3.connect(":memory:")
            cls.addClassCleanup(cls.snapshot.close)
            with seed.board.store.db.read() as connection:
                connection.backup(cls.snapshot)
            cls.fixture_values = copy.deepcopy({name: getattr(seed, name) for name in (
                "macros", "roots", "helpers", "completed_attempts", "plain", "controls", "operations",
            )})
            cls.clock_value = seed.clock.value
            cls.workspace_values = copy.deepcopy(vars(seed.workspace))

    def restore_seed(self, fixture):
        with closing(sqlite3.connect(fixture.board.store.db.path)) as connection:
            self.snapshot.backup(connection)
        shutil.copytree(self.seed_root / "state", fixture.state, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns("board.sqlite3", "board.sqlite3-wal", "board.sqlite3-shm"))
        for name, value in copy.deepcopy(self.fixture_values).items():
            setattr(fixture, name, value)
        fixture.clock.value = self.clock_value
        vars(fixture.workspace).update(copy.deepcopy(self.workspace_values))
        fixture.workspace.root = fixture.state

    def setUp(self):
        self.root = self.enterContext(private_state_dir("objective-summary-cache-"))
        with patch.object(fixture_module.SyntheticBoard, "generate", autospec=True, side_effect=self.restore_seed):
            self.fixture = self.enterContext(fixture_module.SyntheticBoard(
                self.root / "state", self.root / "runtime", SOURCE,
                recipe=fixture_module.Recipe.smoke(), seed=613,
            ))
        self.store = self.fixture.board.store

    def read(self, **params):
        return objectives.objective_list(self.store, params)

    def rows(self, **params):
        return {row["objectiveId"]: row for row in self.read(**params)["objectives"]}

    def sql(self, statement, params=()):
        with self.store.db.write() as connection:
            connection.execute(statement, params)

    def root_for(self, group):
        with self.store.db.read() as connection:
            return connection.execute("SELECT run_id FROM workflow_runs WHERE objective_id=? ORDER BY created_at,rowid LIMIT 1", (group,)).fetchone()[0]

    def assert_recomputed(self, counter, groups):
        self.assertCountEqual([call.args[1] for call in counter.call_args_list], groups)
        counter.reset_mock()

    def test_unrelated_raw_sql_commits_reuse_every_summary(self):
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            expected = self.read()
            self.assert_recomputed(counter, self.fixture.macros)
            for statement, params in (
                ("INSERT INTO meta(key,value) VALUES('unrelated-counter','1')", ()),
                ("UPDATE tasks SET revision=revision+1 WHERE task_id=?", (self.fixture.plain[0],)),
                ("UPDATE attempts SET result_json=json_set(result_json,'$.privateSyntheticInjection.padding',?) WHERE attempt_id=?", ("changed fictional private padding", self.fixture.completed_attempts[0])),
            ):
                self.sql(statement, params)
                self.assertEqual(self.read(), expected)
                self.assert_recomputed(counter, [])

    def test_root_helper_and_router_stop_changes_are_selective(self):
        group = self.fixture.macros[0]
        parent = self.root_for(group)
        helper = self.fixture.helpers[0]
        with self.store.db.read() as connection:
            route = connection.execute("SELECT d.task_id FROM workflow_routes r JOIN decision_requests d USING(decision_id) WHERE r.run_id=?", (parent,)).fetchone()[0]
        # Make the owner a delivered terminal result, so its descendant stops
        # decide review_ready. No event or revision increment accompanies writes.
        self.sql("UPDATE workflow_runs SET state='delivered' WHERE run_id=?", (parent,))
        self.sql("UPDATE tasks SET state='completed' WHERE task_id=?", (parent,))
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            initial = self.rows()[group]
            self.assert_recomputed(counter, self.fixture.macros)
            for task in (parent, helper, route):
                with self.subTest(task=task):
                    self.sql("UPDATE attempts SET shutdown_confirmed=0 WHERE task_id=?", (task,))
                    changed = self.rows()[group]
                    self.assert_recomputed(counter, [group])
                    self.assertLess(changed["counts"]["review"], initial["counts"]["review"])
                    self.sql("UPDATE attempts SET shutdown_confirmed=1 WHERE task_id=?", (task,))
                    self.assertEqual(self.rows()[group], initial)
                    self.assert_recomputed(counter, [group])
            self.sql("UPDATE workflow_runs SET host_id='fictional-new-host',owner_generation=2 WHERE run_id=?", (parent,))
            self.assertIn("fictional-new-host", self.rows()[group]["currentHostIds"])
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE workflow_runs SET state='accepted' WHERE run_id=?", (parent,))
            self.sql("UPDATE tasks SET accepted_at='2026-01-01T00:00:00Z' WHERE task_id=?", (parent,))
            self.assertGreater(self.rows()[group]["counts"]["accepted"], initial["counts"]["accepted"])
            self.assert_recomputed(counter, [group])

    def test_raw_task_state_attempt_selection_execution_and_receipt_presence(self):
        group=self.fixture.macros[1]
        root=self.root_for(group)
        with self.store.db.read() as connection:
            task=connection.execute("SELECT selected_attempt_id FROM tasks WHERE task_id=?",(root,)).fetchone()
            selected=task[0]
            result=connection.execute("SELECT result_json FROM attempts WHERE attempt_id=?",(selected,)).fetchone()[0]
        self.sql("UPDATE workflow_runs SET state='delivered' WHERE run_id=?",(root,))
        self.sql("UPDATE tasks SET state='completed',accepted_at=NULL,active_attempt_id=NULL WHERE task_id=?",(root,))
        initial=self.rows()[group]
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.sql("UPDATE tasks SET state='running' WHERE task_id=?",(root,))
            changed=self.rows()[group]
            self.assertGreater(changed["counts"]["active"],initial["counts"]["active"])
            self.assert_recomputed(counter,[group])
            self.sql("UPDATE tasks SET state='completed' WHERE task_id=?",(root,))
            self.rows(); counter.reset_mock()
            for statement,params,restore,restore_params in (
                ("UPDATE tasks SET active_attempt_id='fictional-dangling-active' WHERE task_id=?",(root,),"UPDATE tasks SET active_attempt_id=NULL WHERE task_id=?",(root,)),
                ("UPDATE tasks SET selected_attempt_id='fictional-dangling-selected' WHERE task_id=?",(root,),"UPDATE tasks SET selected_attempt_id=? WHERE task_id=?",(selected,root)),
                ("UPDATE attempts SET execution_state='uncertain' WHERE attempt_id=?",(selected,),"UPDATE attempts SET execution_state='finished' WHERE attempt_id=?",(selected,)),
                ("UPDATE attempts SET result_json=NULL WHERE attempt_id=?",(selected,),"UPDATE attempts SET result_json=? WHERE attempt_id=?",(result,selected)),
            ):
                with self.subTest(statement=statement):
                    self.sql(statement,params)
                    self.assertLess(self.rows()[group]["counts"]["review"],initial["counts"]["review"])
                    self.assert_recomputed(counter,[group])
                    self.sql(restore,restore_params)
                    self.assertEqual(self.rows()[group],initial)
                    self.assert_recomputed(counter,[group])

    def test_request_and_turn_identity_facts_conservatively_invalidate_one_group(self):
        group=self.fixture.macros[0]
        root=self.root_for(group)
        self.read()
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.sql("UPDATE workflow_requests SET state='declined' WHERE run_id=?",(root,))
            self.read()
            self.assert_recomputed(counter,[group])
            self.sql("UPDATE workflow_turns SET disposition='attention' WHERE run_id=?",(root,))
            self.read()
            self.assert_recomputed(counter,[group])

    def test_attempt_execution_fact_alone_recomputes_only_its_macro(self):
        """C2: the attempt marker must work even with frozen review_ready."""
        group = self.fixture.macros[1]
        root = self.root_for(group)
        with self.store.db.read() as connection:
            selected = connection.execute(
                "SELECT selected_attempt_id FROM tasks WHERE task_id=?", (root,),
            ).fetchone()[0]
        # Both finished/unconfirmed and uncertain/unconfirmed are unresolved
        # stops. Prime before caching so review_ready cannot be the invalidator.
        self.sql("UPDATE attempts SET shutdown_confirmed=0 WHERE attempt_id=?", (selected,))
        original_dependencies = memo.dependencies
        captures = []

        def capture(connection, members):
            markers, reusable = original_dependencies(connection, members)
            membership = {identifier: [tuple(row[column] for column in (*memo.MEMBER_COLUMNS, "matching"))
                                       for row in rows] for identifier, rows in members.items()}
            captures.append((membership, copy.deepcopy(markers), reusable.copy()))
            return markers, reusable

        def facts():
            with self.store.db.read() as connection:
                tables = [row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                other = {table: [tuple(row) for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')]
                         for table in tables if table != "attempts"}
                attempts = [dict(row) for row in connection.execute("SELECT * FROM attempts ORDER BY rowid")]
                return other, attempts

        with patch.object(memo, "dependencies", side_effect=capture), \
                patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            initial = self.read()
            self.assert_recomputed(counter, self.fixture.macros)
            self.assertEqual(self.read(), initial)
            self.assert_recomputed(counter, [])
            before_other, before_attempts = facts()
            self.sql("UPDATE attempts SET execution_state='uncertain' WHERE attempt_id=?", (selected,))
            after_other, after_attempts = facts()
            self.assertEqual(after_other, before_other)  # Includes task/run revisions and all JSON/events.
            expected_attempts = copy.deepcopy(before_attempts)
            target = next(row for row in expected_attempts if row["attempt_id"] == selected)
            self.assertEqual(target["execution_state"], "finished")
            self.assertEqual(target["shutdown_confirmed"], 0)
            target["execution_state"] = "uncertain"
            self.assertEqual(after_attempts, expected_attempts)

            changed = self.read()
            self.assertEqual(changed, initial)
            before_members, before_markers, before_reusable = captures[1]
            after_members, after_markers, after_reusable = captures[2]
            self.assertEqual(after_members, before_members)  # Includes review_ready and matching.
            self.assertEqual(after_reusable, before_reusable)
            without_attempts = lambda markers: {identifier: [row for row in rows if row[0] != "attempt"]
                                                for identifier, rows in markers.items()}
            self.assertEqual(without_attempts(after_markers), without_attempts(before_markers))
            self.assert_recomputed(counter, [group])  # C2 behavior assertion; unrelated macros reuse.
            expected_markers = copy.deepcopy(before_markers)
            for index, row in enumerate(expected_markers[group]):
                if row[0] == "attempt" and row[2] == selected:
                    self.assertEqual(row[4], "finished")
                    expected_markers[group][index] = (*row[:4], "uncertain", *row[5:])
                    break
            else:
                self.fail("Selected attempt must have its own dependency marker")
            self.assertEqual(after_markers, expected_markers)
            self.assertEqual(self.read(), initial)
            self.assert_recomputed(counter, [])

    def test_project_alias_and_raw_source_event_correction_affect_query_membership(self):
        group=self.fixture.macros[1]
        root=self.root_for(group)
        directory=self.root / "fictional-identity-anchor"
        directory.mkdir()
        path=str(directory)
        inode=directory.stat().st_ino
        encode=lambda values:json.dumps(values,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()
        old=hashlib.sha256(encode([path,7,inode])).hexdigest()
        new=hashlib.sha256(encode([path,inode])).hexdigest()
        entry={"version":1,"source":"fixed-input-and-inode","newId":new,"anchors":[
            {"directory":path,"inode":inode,"manifestSha256":"a"*64,"workspaceId":"fictional-identity-workspace","role":"repositoryId","legacyDevice":7}]}
        encoded=json.dumps(entry)
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?",(old,group))
        self.sql("UPDATE workflow_artifacts SET manifest_json=json_set(manifest_json,'$.repositoryId',?) WHERE run_id=? AND kind='input'",(old,root))
        self.assertEqual(self.read(projectId=new)["total"],0)
        self.read()
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.sql("INSERT INTO meta(key,value) VALUES(?,?)",("workspace-identity:"+old,encoded))
            self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?)",(json.dumps({"version":1,"entries":{old:encoded}}),))
            self.assertEqual(self.rows()[group]["project"]["id"],new)
            self.assert_recomputed(counter,[group])
            self.assertEqual(self.rows(projectId=new)[group]["matchingRuns"],1)
            self.assert_recomputed(counter,[group])
            self.sql("UPDATE events SET payload_json=json_set(payload_json,'$.governed.sourceHostId','fictional-corrected-source') WHERE task_id=? AND kind='task.submitted'",(root,))
            self.assertEqual(self.rows(query="fictional-corrected-source")[group]["matchingRuns"],1)
            self.assert_recomputed(counter,[group])

    def test_objective_metadata_description_and_identity_reason_raw_corrections(self):
        group = self.fixture.macros[0]
        first = self.root_for(group)
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.read()
            counter.reset_mock()
            self.sql("UPDATE objectives SET title='Fictional revised title',project_path='/fictional/revised',source_host_id='revised-source',created_at='2020-01-01T00:00:00Z' WHERE objective_id=?", (group,))
            row = self.rows()[group]
            self.assertEqual(row["title"], "Fictional revised title")
            self.assertEqual(row["project"]["path"], "/fictional/revised")
            self.assertEqual(row["sourceHostId"], "revised-source")
            self.assertEqual(row["createdAt"], "2020-01-01T00:00:00Z")
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE events SET payload_json=json_set(payload_json,'$.description','Fictional corrected description') WHERE kind='workflow.objective_created' AND task_id=?", (first,))
            self.assertEqual(self.rows()[group]["description"], "Fictional corrected description")
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE objectives SET project_id='fictional-unproved-id' WHERE objective_id=?", (group,))
            self.rows()
            counter.reset_mock()
            self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?)", (json.dumps({"kept": {"fictional-unproved-id": "fictional-proof-missing"}}),))
            self.assertEqual(self.rows()[group]["project"]["identityReason"], "fictional-proof-missing")
            self.assert_recomputed(counter, [group])

    def test_first_description_run_is_reselected_after_raw_creation_order_change(self):
        group = self.fixture.macros[0]
        first = self.root_for(group)
        with self.store.db.read() as connection:
            second = connection.execute("SELECT run_id FROM workflow_runs WHERE objective_id=? AND run_id<>? ORDER BY created_at,rowid LIMIT 1", (group,first)).fetchone()[0]
        self.read()
        self.sql("UPDATE workflow_runs SET created_at='2000-01-01T00:00:00Z' WHERE run_id=?", (second,))
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.assertIsNone(self.rows()[group]["description"])
            self.assert_recomputed(counter, [group])

    def test_raw_description_json_type_correction_is_not_a_marker_collision(self):
        group=self.fixture.macros[0]
        root=self.root_for(group)
        self.sql("UPDATE events SET payload_json=json_set(payload_json,'$.description',json(?)) WHERE kind='workflow.objective_created' AND task_id=?",('{"fictional":1}',root))
        self.assertEqual(self.rows()[group]["description"],{"fictional":1})
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.sql("UPDATE events SET payload_json=json_set(payload_json,'$.description',?) WHERE kind='workflow.objective_created' AND task_id=?",('{"fictional":1}',root))
            self.assertEqual(self.rows()[group]["description"],'{"fictional":1}')
            self.assert_recomputed(counter,[group])

    def test_duplicate_description_keys_use_existing_python_summary_semantics(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        for key in ('"description"', '"descr\\u0069ption"'):
            with self.subTest(key=key):
                payload = '{"objectiveId":' + json.dumps(group) + ',"description":"fixed",' + key + ':"old"}'
                self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (payload, root))
                self.assertEqual(self.rows()[group]["description"], "old")
                self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (payload.replace('"old"', '"new"'), root))
                self.assertEqual(self.rows()[group]["description"], "new")
                with self.store.db.read() as connection:
                    self.assertEqual(objectives._summary(connection, group, [], [])["description"], "new")

    def test_duplicate_objective_id_corrections_cannot_reuse_old_description(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        payload = '{"objectiveId":"obj-first", "objectiveId":' + json.dumps(group) + ',"description":"old"}'
        self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (payload, root))
        self.assertEqual(self.rows()[group]["description"], "old")
        # The first SQLite-selected key and total document length stay identical.
        other = group[:-1] + ('0' if group[-1] != '0' else '1')
        self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (payload.replace(json.dumps(group), json.dumps(other)), root))
        self.assertIsNone(self.rows()[group]["description"])

    def test_duplicate_kept_top_level_keys_use_existing_python_identity_reason(self):
        group = self.fixture.macros[0]
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        for key in ('"kept"', '"ke\\u0070t"'):
            with self.subTest(key=key):
                payload = '{"kept":{' + json.dumps(project) + ':"fixed"},' + key + ':{' + json.dumps(project) + ':"old"}}'
                self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (payload,))
                self.assertEqual(self.rows()[group]["project"]["identityReason"], "old")
                self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (payload.replace('"old"', '"new"'),))
                self.assertEqual(self.rows()[group]["project"]["identityReason"], "new")
                with self.store.db.read() as connection:
                    self.assertEqual(objectives._summary(connection, group, [], [])["project"]["identityReason"], "new")

    def test_duplicate_project_keys_in_kept_only_disable_affected_project_reuse(self):
        group = self.fixture.macros[0]
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        for key in (json.dumps(project), '"\\u0066ictional-json-project"'):
            with self.subTest(key=key):
                payload = '{"kept":{' + json.dumps(project) + ':"fixed",' + key + ':"old"}}'
                self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (payload,))
                self.assertEqual(self.rows()[group]["project"]["identityReason"], "old")
                self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (payload.replace('"old"', '"new"'),))
                with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
                    self.assertEqual(self.rows()[group]["project"]["identityReason"], "new")
                    self.assert_recomputed(counter, [group])

    def test_unconsumed_duplicate_json_keys_and_unrelated_report_changes_keep_reuse(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        event = '{"objectiveId":' + json.dumps(group) + ',"noise":"one","noise":"two","description":"fixed"}'
        self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (event, root))
        report = '{"noise":"one","noise":"two","kept":{"fictional-other":"one","fictional-other":"two",' + json.dumps(project) + ':"fixed"}}'
        self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?)", (report,))
        expected = self.read()
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (report.replace('"two"', '"new"'),))
            self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (event.replace('"two"', '"new"'), root))
            self.sql("INSERT INTO meta(key,value) VALUES('fictional-unrelated-commit','1')")
            self.assertEqual(self.read(), expected)
            self.assert_recomputed(counter, [])

    def test_description_type_precision_nul_and_size_boundaries_preserve_summary(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        cases = (
            ("x" * memo.TEXT_BOUND, "x" * (memo.TEXT_BOUND - 1) + "y"),
            ("x" * (memo.TEXT_BOUND + 1), "x" * memo.TEXT_BOUND + "y"),
            ("fixed\0old", "fixed\0new"),
            ("字" * memo.TEXT_BOUND, "字" * (memo.TEXT_BOUND - 1) + "新"),
            (92233720368547758080, 92233720368547758081),
            (None, False),
            (False, {"fictional": "new"}),
        )
        for before, after in cases:
            with self.subTest(before_type=type(before).__name__, size=len(str(before))):
                for value in (before, after):
                    payload = json.dumps({"objectiveId": group, "description": value})
                    self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (payload, root))
                    self.assertEqual(self.rows()[group]["description"], value)
                    with self.store.db.read() as connection:
                        self.assertEqual(objectives._summary(connection, group, [], [])["description"], value)

    def test_identity_reason_type_precision_nul_and_size_boundaries_preserve_summary(self):
        group = self.fixture.macros[0]
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration','{}')")
        cases = (
            ("x" * memo.TEXT_BOUND, "x" * (memo.TEXT_BOUND - 1) + "y"),
            ("x" * (memo.TEXT_BOUND + 1), "x" * memo.TEXT_BOUND + "y"),
            ("fixed\0old", "fixed\0new"),
            (92233720368547758080, 92233720368547758081),
            ({"fictional": 1}, '{"fictional":1}'),
            (None, True),
        )
        for before, after in cases:
            with self.subTest(before_type=type(before).__name__, size=len(str(before))):
                for value in (before, after):
                    self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (json.dumps({"kept": {project: value}}),))
                    cached = self.rows()[group]
                    with self.store.db.read() as connection:
                        direct = objectives._summary(connection, group, [], [])
                    self.assertEqual(cached["project"], direct["project"])
                    self.assertEqual(cached["project"].get("identityReason"), value if value else None)

    def test_kept_container_type_changes_do_not_freeze_previous_identity_reason(self):
        group = self.fixture.macros[0]
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration','{}')")
        for kept in ({project: "old"}, None, [], "old", {project: "new"}):
            with self.subTest(kept=kept):
                self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (json.dumps({"kept": kept}),))
                with self.store.db.read() as connection:
                    direct = objectives._summary(connection, group, [], [])
                self.assertEqual(self.rows()[group]["project"], direct["project"])

    def test_standalone_title_task_and_summary_nul_suffix_corrections(self):
        root = self.root_for(self.fixture.macros[1])
        group = "run:" + root
        self.sql("UPDATE workflow_runs SET objective_id=NULL WHERE run_id=?", (root,))
        corrections = (
            ("UPDATE workflow_runs SET title=? WHERE run_id=?", "title"),
            ("UPDATE workflow_runs SET title=NULL,goal_json=json_set(goal_json,'$.task',?) WHERE run_id=?", "title"),
            ("UPDATE workflow_turns SET outcome_json=json_set(outcome_json,'$.summary',?) WHERE run_id=?", "summary"),
        )
        for statement, field in corrections:
            with self.subTest(field=field, statement=statement):
                self.sql(statement, ("fixed\0old", root))
                self.assertEqual(self.rows()[group][field], "fixed\0old")
                self.sql(statement, ("fixed\0new", root))
                self.assertEqual(self.rows()[group][field], "fixed\0new")

    def test_huge_unrelated_report_and_event_fields_are_not_copied_into_marker(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        project = "fictional-json-project"
        self.sql("UPDATE objectives SET project_id=? WHERE objective_id=?", (project, group))
        padding = "fictional-unconsumed-padding-" * 80000
        report = json.dumps({"padding": padding, "kept": {project: "fixed"}})
        event = json.dumps({"padding": padding, "objectiveId": group, "description": "fixed"})
        self.sql("INSERT INTO meta(key,value) VALUES('workspace-identity-migration',?)", (report,))
        self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (event, root))
        expected = self.read()
        encoded = json.dumps(list(memo.for_store(self.store).entries.values()))
        self.assertNotIn("fictional-unconsumed-padding", encoded)
        self.assertLess(len(encoded), 100000)
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.sql("UPDATE meta SET value=? WHERE key='workspace-identity-migration'", (report.replace("padding-", "padding!"),))
            self.sql("UPDATE events SET payload_json=? WHERE kind='workflow.objective_created' AND task_id=?", (event.replace("padding-", "padding!"), root))
            self.assertEqual(self.read(), expected)
            self.assert_recomputed(counter, [])

    def test_standalone_title_task_and_latest_concluded_turn_summary(self):
        root = self.root_for(self.fixture.macros[1])
        self.sql("UPDATE workflow_runs SET objective_id=NULL,title='Fictional standalone title' WHERE run_id=?", (root,))
        group = "run:" + root
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            initial = self.rows()[group]
            counter.reset_mock()
            self.sql("UPDATE workflow_runs SET title=NULL,goal_json=json_set(goal_json,'$.task',?) WHERE run_id=?", ("Fictional task fallback\nmore", root))
            self.assertEqual(self.rows()[group]["title"], "Fictional task fallback")
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE workflow_turns SET outcome_json=json_set(outcome_json,'$.summary',?) WHERE run_id=?", ("  Fictional  corrected summary\nsecond line", root))
            self.assertEqual(self.rows()[group]["summary"], "Fictional corrected summary")
            self.assert_recomputed(counter, [group])
            self.sql("INSERT INTO workflow_turns(turn_id,run_id,turn_index,resume_mode,input_json,state,outcome_json,created_at,updated_at) VALUES('fictional-turn-2',?,2,'reconstructed-new-session','{}','prepared',?, '2026-01-01','2026-01-01')", (root,json.dumps({"summary":"Fictional newest summary"})))
            self.assertEqual(self.rows()[group]["summary"], "Fictional corrected summary")
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE workflow_turns SET state='concluded' WHERE turn_id='fictional-turn-2'")
            self.assertEqual(self.rows()[group]["summary"], "Fictional newest summary")
            self.assert_recomputed(counter, [group])
            self.sql("UPDATE workflow_turns SET outcome_json=json_set(outcome_json,'$.summary','Fictional latest corrected') WHERE turn_id='fictional-turn-2'")
            self.assertEqual(self.rows()[group]["summary"], "Fictional latest corrected")
            self.assert_recomputed(counter, [group])
            self.assertNotEqual(initial["summary"], self.rows()[group]["summary"])

    def test_raw_relationship_moves_only_recompute_old_and_new_groups(self):
        old, new = self.fixture.macros[:2]
        helper = self.fixture.helpers[0]
        self.read()
        before = self.rows()
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.sql("UPDATE workflow_children SET parent_run_id=? WHERE child_task_id=?", (self.root_for(new), helper))
            after = self.rows()
            self.assertEqual(after[old]["counts"]["helpers"], before[old]["counts"]["helpers"] - 1)
            self.assertEqual(after[new]["counts"]["helpers"], before[new]["counts"]["helpers"] + 1)
            self.assert_recomputed(counter, [old,new])
            self.sql("UPDATE workflow_routes SET run_id=?", (self.root_for(new),))
            self.read()
            self.assert_recomputed(counter, [old,new])

    def test_filters_query_matches_page_order_addition_and_removal(self):
        group = self.fixture.macros[0]
        root = self.root_for(group)
        first = self.read(limit=1)
        self.assertEqual(first["total"], 3)
        second = self.read(limit=1,before=first["nextCursor"])
        self.assertNotEqual(first["objectives"][0]["objectiveId"], second["objectives"][0]["objectiveId"])
        self.assertEqual(self.read(query="needle-fictional")["total"],0)
        self.sql("UPDATE workflow_runs SET goal_json=json_set(goal_json,'$.task','needle-fictional') WHERE run_id=?", (root,))
        self.assertEqual(self.read(query="needle-fictional")["objectives"][0]["matchingRuns"],1)
        self.sql("UPDATE workflow_runs SET goal_json=json_set(goal_json,'$.task','other-fictional') WHERE run_id=?", (root,))
        self.assertEqual(self.read(query="needle-fictional")["total"],0)
        self.sql("UPDATE objectives SET activity_seq=999999 WHERE objective_id=?", (group,))
        self.assertEqual(self.read(limit=1)["objectives"][0]["objectiveId"],group)
        self.assertTrue(self.read(limit=1,before=first["nextCursor"])["changed"])
        self.sql("UPDATE workflow_runs SET objective_id=NULL WHERE objective_id=?", (group,))
        after = self.read()
        self.assertNotIn(group,{row["objectiveId"] for row in after["objectives"]})
        self.assertGreater(after["total"],3)
        self.assertEqual(self.read(hostId="not-a-fictional-host")["total"],0)
        self.assertTrue(self.read(filter="host")["total"])
        self.assertTrue(self.read(filter="review")["total"])

    def test_matching_count_changes_in_cached_group_and_metadata_queries(self):
        group = self.fixture.macros[1]
        root = self.root_for(group)
        self.sql("UPDATE workflow_runs SET goal_json=json_set(goal_json,'$.task','fictional-count-needle') WHERE objective_id=?", (group,))
        initial = self.rows(query="fictional-count-needle")[group]["matchingRuns"]
        with patch.object(objectives, "_summary", wraps=objectives._summary) as counter:
            self.sql("UPDATE workflow_runs SET goal_json=json_set(goal_json,'$.task','fictional-other') WHERE run_id=?", (root,))
            self.assertEqual(self.rows(query="fictional-count-needle")[group]["matchingRuns"], initial-1)
            self.assert_recomputed(counter,[group])
            self.sql("UPDATE workflow_runs SET execution_configuration_json=json_set(execution_configuration_json,'$.model','fictional-model') WHERE run_id=?", (root,))
            self.assertEqual(self.rows(query="fictional-model")[group]["matchingRuns"],1)
            self.assert_recomputed(counter,[group])
            self.sql("UPDATE workflow_runs SET host_id='fictional-takeover' WHERE run_id=?", (root,))
            self.assertEqual(self.rows(hostId="fictional-takeover")[group]["matchingRuns"],1)
            self.assert_recomputed(counter,[group])

    def test_concurrent_reads_share_one_computation_and_writer_cannot_poison_snapshot(self):
        original = objectives._summary
        started, committed = threading.Event(), threading.Event()
        group = self.fixture.macros[0]
        results = []

        def compute(connection, identifier, *args):
            if identifier == group and not started.is_set():
                started.set()
                self.assertTrue(committed.wait(10))
            return original(connection,identifier,*args)

        def write():
            self.assertTrue(started.wait(10))
            self.sql("UPDATE objectives SET title='Fictional concurrent title' WHERE objective_id=?", (group,))
            committed.set()

        with patch.object(objectives,"_summary",side_effect=compute) as counter:
            with ThreadPoolExecutor(max_workers=3) as executor:
                writer=executor.submit(write)
                reader=executor.submit(self.rows)
                results.append(reader.result(timeout=15))
                writer.result(timeout=15)
            self.assertEqual(results[0][group]["title"],"Fictional macro 000")
            self.assertEqual(self.rows()[group]["title"],"Fictional concurrent title")
            self.assert_recomputed(counter,[*self.fixture.macros,group])
            self.store._objective_summary_cache=memo.SummaryCache()
            with ThreadPoolExecutor(max_workers=4) as executor:
                simultaneous=list(executor.map(lambda _:self.read(),range(8)))
            self.assertTrue(all(value==simultaneous[0] for value in simultaneous))
            self.assert_recomputed(counter,self.fixture.macros)

    def test_board_filter_cache_isolation_and_mutation_of_returned_values(self):
        initial=self.rows()
        initial[self.fixture.macros[0]]["counts"]["roots"]=-500
        self.assertGreater(self.rows()[self.fixture.macros[0]]["counts"]["roots"],0)
        hit = self.rows()[self.fixture.macros[0]]
        expected = copy.deepcopy(hit)
        hit["counts"]["roots"] = -500
        hit["currentHostIds"].append("fictional-poisoned-host")
        self.assertEqual(self.rows()[self.fixture.macros[0]], expected)
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.read(query="macro-000")
            self.assert_recomputed(counter,[self.fixture.macros[0]])
            self.read()
            self.assert_recomputed(counter,[])
            with fixture_module.SyntheticBoard(self.root/"second-state",self.root/"second-runtime",SOURCE,recipe=fixture_module.Recipe.smoke(),seed=613) as other:
                self.assertEqual(other.macros,self.fixture.macros)
                objectives.objective_list(other.board.store,{})
                self.assert_recomputed(counter,other.macros)
                self.assertIsNot(memo.for_store(other.board.store),memo.for_store(self.store))

    def test_entries_bytes_and_time_boundaries(self):
        now=[0]
        self.store._objective_summary_cache=memo.SummaryCache(max_entries=2,max_bytes=100000,clock=lambda:now[0])
        cache=memo.for_store(self.store)
        self.read()
        self.assertLessEqual(len(cache.entries),2)
        self.assertLessEqual(cache.bytes,cache.max_bytes)
        # One group's query remains cached until the conservative expiry.
        self.read(query="macro-000")
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.read(query="macro-000")
            self.assert_recomputed(counter,[])
            now[0]=memo.MAX_AGE_SECONDS
            self.read(query="macro-000")
            self.assert_recomputed(counter,[self.fixture.macros[0]])
        cache=memo.SummaryCache(max_bytes=10)
        self.store._objective_summary_cache=cache
        self.read()
        self.assertEqual(len(cache.entries),0)
        self.assertEqual(cache.bytes,0)

    def test_restored_cases_isolate_database_clock_cache_and_python_fixture(self):
        """E: restoring a case must discard every mutable fixture layer."""
        expected_fixture_values = copy.deepcopy(self.fixture_values)
        expected_clock_value = self.clock_value
        expected_workspace_values = copy.deepcopy(self.workspace_values)
        expected_database = list(self.snapshot.iterdump())
        initial = self.read()
        self.sql("INSERT INTO meta(key,value) VALUES('fictional-case-only','changed')")
        self.fixture.clock.advance(600)
        self.fixture.macros.append("fictional-case-only")
        self.fixture.controls[next(iter(self.fixture.controls))]["hostId"] = "fictional-case-only"
        self.fixture.workspace.dirty = True
        self.fixture.workspace.manifests.clear()
        self.fixture.initial_facts["counts"]["tasks"] = -500
        self.fixture.board.console.read_cache.calls["fictional-case-only"] = {"projections": 1}
        memo.for_store(self.store).clock = lambda: -500
        self.assertEqual(self.fixture_values, expected_fixture_values,
                         "E1: mutating a case must not contaminate the class seed")
        self.assertEqual(self.clock_value, expected_clock_value)
        self.assertEqual(self.workspace_values, expected_workspace_values)
        self.assertEqual(list(self.snapshot.iterdump()), expected_database)

        with patch.object(fixture_module.SyntheticBoard, "generate", autospec=True, side_effect=self.restore_seed):
            with fixture_module.SyntheticBoard(
                self.root / "restored-state", self.root / "restored-runtime", SOURCE,
                recipe=fixture_module.Recipe.smoke(), seed=613,
            ) as restored:
                self.assertNotEqual(restored.board.store.db.path, self.store.db.path)
                with restored.board.store.db.read() as connection:
                    self.assertEqual(list(connection.iterdump()), expected_database)
                for name, value in expected_fixture_values.items():
                    self.assertEqual(getattr(restored, name), value)
                self.assertEqual(restored.clock.value, expected_clock_value)
                self.assertIsNot(restored.clock, self.fixture.clock)
                expected_workspace = copy.deepcopy(expected_workspace_values)
                expected_workspace["root"] = restored.state
                self.assertEqual(vars(restored.workspace), expected_workspace)
                self.assertGreater(restored.initial_facts["counts"]["tasks"], 0)
                self.assertEqual(memo.for_store(restored.board.store).entries, {})
                self.assertEqual(restored.board.console.read_cache.calls, {})
                self.assertEqual(restored.board.console.read_cache._entries, {})
                self.assertEqual(restored.launch_guard.call_count, 0)
                self.assertEqual(objectives.objective_list(restored.board.store, {}), initial)

    def test_marker_never_copies_large_result_or_outcome_documents(self):
        root=self.root_for(self.fixture.macros[1])
        self.sql("UPDATE workflow_runs SET objective_id=NULL WHERE run_id=?",(root,))
        self.sql("UPDATE workflow_turns SET outcome_json=json_set(outcome_json,'$.privatePadding',?) WHERE run_id=?",("fictional-padding-"*120000,root))
        self.read()
        cache=memo.for_store(self.store)
        encoded=json.dumps(list(cache.entries.values()))
        self.assertNotIn("privateSyntheticInjection",encoded)
        self.assertNotIn("fictional-padding-",encoded)
        self.assertLess(len(encoded),100000)
        # Arbitrarily large raw-SQL display fields are bounded in the marker;
        # instead of risking a collision outside its prefix, decline reuse.
        self.sql("UPDATE workflow_turns SET outcome_json=json_set(outcome_json,'$.summary',?) WHERE run_id=?",(" "*5000+"fictional-visible",root))
        self.assertEqual(self.rows()["run:"+root]["summary"],"fictional-visible")
        with patch.object(objectives,"_summary",wraps=objectives._summary) as counter:
            self.assertEqual(self.rows()["run:"+root]["summary"],"fictional-visible")
            self.assert_recomputed(counter,["run:"+root])


class SummaryCacheReturnTests(unittest.TestCase):
    def test_cache_hits_own_nested_list_and_dict_values(self):
        """C1: mutate the actual hit return, beyond the cold-write copy."""
        cache = memo.SummaryCache()
        expected = {"counts": {"roots": 3}, "hosts": [{"id": "fictional-host"}]}
        # A compute spy proves both later calls really take the hit path.
        compute = Mock(return_value=copy.deepcopy(expected))
        self.assertEqual(cache.get("group", ("fixed",), compute), expected)
        hit = cache.get("group", ("fixed",), compute)
        hit["counts"]["roots"] = -500
        hit["hosts"][0]["id"] = "fictional-poisoned-host"
        hit["hosts"].append({"id": "fictional-added-host"})
        self.assertEqual(cache.get("group", ("fixed",), compute), expected,
                         "C1: mutating a cache-hit return must not poison the next hit")
        compute.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
