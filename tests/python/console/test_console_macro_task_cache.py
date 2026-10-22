"""B08/B09 at the real Console HTTP boundary; no timing thresholds."""
from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.parse import urlencode

from support import private_state_dir
from blackboard.tasks.test_console_macro_task_fixture import fixture_module, SOURCE
from hey_my_buddy.blackboard.tasks import macro_tasks


class ConsoleMacroTaskCacheTests(unittest.TestCase):
    def setUp(self):
        self.root = self.enterContext(private_state_dir("console-macro-task-cache-"))
        self.fixture = self.enterContext(fixture_module.SyntheticBoard(
            self.root / "state", self.root / "runtime", SOURCE,
            recipe=fixture_module.Recipe.smoke(), seed=613,
        ))

    def test_unrelated_commits_recompute_no_macro_and_related_commits_only_one(self):
        with fixture_module.HandlerProbe(self.fixture.board) as probe:
            first, page = probe.request("identity")
            self.assertEqual(first["summaryCalls"], 3)
            group = page["macroTasks"][0]["macroTaskId"]
            other = next(identifier for identifier in self.fixture.macros if identifier != group)
            for scenario in ("no-write", "unrelated-meta", "unrelated-plain"):
                with self.subTest(scenario=scenario):
                    self.fixture.mutate(scenario,1,relevant_macro=group,other_macro=other)
                    row, body = probe.request("identity",first["etag"])
                    self.assertEqual(row["summaryCalls"],0)
                    self.assertEqual(row["status"],304)
                    self.assertIsNone(body)
                    self.assertEqual(row["wireBodyBytes"],0)
            self.fixture.mutate("single-macro",2,relevant_macro=group,other_macro=other)
            related, changed = probe.request("identity",first["etag"])
            self.assertEqual(related["status"],200)
            self.assertEqual(related["summaryGroups"],[group])
            self.assertGreater(changed["cursor"],page["cursor"])
            compressed, decoded = probe.request("gzip",related["etag"])
            self.assertEqual(compressed["status"],200)
            self.assertEqual(compressed["summaryCalls"],0)
            self.assertEqual(decoded,changed)
            self.assertNotEqual(compressed["etag"],related["etag"])
            replay, _ = probe.request("gzip",compressed["etag"])
            self.assertEqual(replay["status"],304)
            self.fixture.mutate("all-macros",3,relevant_macro=group,other_macro=other)
            all_changed, _ = probe.request("identity",related["etag"])
            self.assertCountEqual(all_changed["summaryGroups"],self.fixture.macros)

    def test_raw_metadata_and_description_changes_refresh_body_page_and_cursor(self):
        store = self.fixture.board.store
        group = self.fixture.macros[0]
        with fixture_module.HandlerProbe(self.fixture.board) as probe:
            first, page = probe.request("identity",query="limit=1")
            cursor = page["nextCursor"]
            second_query = urlencode({"limit":1,"before":cursor})
            _, second = probe.request("identity",query=second_query)
            self.assertNotEqual(page["macroTasks"][0]["macroTaskId"],second["macroTasks"][0]["macroTaskId"])
            with store.db.write() as connection:
                connection.execute("UPDATE macro_tasks SET activity_seq=999999,title='Fictional HTTP title' WHERE macro_task_id=?",(group,))
                root = connection.execute("SELECT run_id FROM workflow_runs WHERE macro_task_id=? ORDER BY created_at,rowid LIMIT 1",(group,)).fetchone()[0]
                connection.execute("UPDATE events SET payload_json=json_set(payload_json,'$.description','Fictional HTTP correction') WHERE kind='workflow.macro_task_created' AND task_id=?",(root,))
            row, updated = probe.request("identity",first["etag"],query="limit=1")
            self.assertEqual(row["status"],200)
            self.assertEqual(row["summaryGroups"],[group])
            summary = updated["macroTasks"][0]
            self.assertEqual(summary["macroTaskId"],group)
            self.assertEqual(summary["title"],"Fictional HTTP title")
            self.assertEqual(summary["description"],"Fictional HTTP correction")
            self.assertNotEqual(updated["nextCursor"],cursor)
            _, after_page = probe.request("identity",query=second_query)
            self.assertTrue(after_page["changed"])
            with store.db.write() as connection:
                connection.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','fictional-http-needle') WHERE task_id=?",(root,))
            _, matched = probe.request("identity",query="limit=50&query=fictional-http-needle")
            self.assertEqual(matched["totals"]["all"],1)
            self.assertEqual(matched["macroTasks"][0]["matchingRuns"],1)
            with store.db.write() as connection:
                connection.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','fictional-http-removed') WHERE task_id=?",(root,))
            _, removed = probe.request("identity",query="limit=50&query=fictional-http-needle")
            self.assertEqual(removed["totals"]["all"],0)

    def test_other_filtered_macro_commit_does_not_recompute_visible_summary(self):
        group, other = self.fixture.macros[:2]
        query="limit=50&query=macro-000"
        with fixture_module.HandlerProbe(self.fixture.board) as probe:
            first, page=probe.request("identity",query=query)
            self.assertEqual(first["summaryGroups"],[group])
            self.fixture.mutate("other-macro",1,relevant_macro=group,other_macro=other)
            row, changed=probe.request("identity",first["etag"],query=query)
            # Existing cursor reports the new event head, even when the visible
            # group's summary stayed identical. Standard ETag reflects the body.
            self.assertEqual(row["status"],200)
            self.assertEqual(row["summaryCalls"],0)
            self.assertEqual(changed["macroTasks"],page["macroTasks"])

    def test_write_during_summary_compute_serves_uncached_then_regenerates(self):
        store=self.fixture.board.store
        group=self.fixture.macros[0]
        original=macro_tasks._summary
        wrote=[]

        def compute(connection,identifier,*args):
            if identifier==group and not wrote:
                with store.db.write() as writer:
                    writer.execute("UPDATE macro_tasks SET title='Fictional during compute' WHERE macro_task_id=?",(group,))
                wrote.append(True)
            return original(connection,identifier,*args)

        with patch.object(macro_tasks,"_summary",side_effect=compute):
            with fixture_module.HandlerProbe(self.fixture.board) as probe:
                row,page=probe.request("identity")
                self.assertEqual(wrote,[True])
                self.assertEqual(row["summaryGroups"].count(group),1)
                self.assertEqual({item["macroTaskId"]:item for item in page["macroTasks"]}[group]["title"],"Fictional macro 000")
                replay,updated=probe.request("identity",row["etag"])
                self.assertEqual(replay["status"],200)
                self.assertEqual(replay["summaryGroups"],[group])
                self.assertEqual({item["macroTaskId"]:item for item in updated["macroTasks"]}[group]["title"],"Fictional during compute")
                cached,_=probe.request("identity",replay["etag"])
                self.assertEqual(cached["status"],304)
                self.assertEqual(cached["summaryCalls"],0)


class MacroTaskReadCacheIntegrationTests(unittest.TestCase):
    """SQLite + existing ReadCache without a listening socket; not HTTP evidence."""

    def setUp(self):
        self.root=self.enterContext(private_state_dir("macro-task-response-cache-"))
        self.fixture=self.enterContext(fixture_module.SyntheticBoard(
            self.root/"state",self.root/"runtime",SOURCE,recipe=fixture_module.Recipe.smoke(),seed=613))
        self.store=self.fixture.board.store
        self.cache=self.fixture.board.console.read_cache

    def read(self, *, etag=None, coding="identity", params=None):
        params=params or {}
        import json
        return self.cache.serve(kind="macroTasks",key=json.dumps(params,sort_keys=True),session="fictional-session",
            extra_marker=(),generate=lambda:(macro_tasks.macro_task_list(self.store,params),None),
            if_none_match=etag,coding=coding)

    def test_response_cache_unrelated_commit_and_coding_validators(self):
        with patch.object(macro_tasks,"_summary",wraps=macro_tasks._summary) as counter:
            first=self.read()
            self.assertEqual(counter.call_count,3)
            counter.reset_mock()
            with self.store.db.write() as writer:
                writer.execute("INSERT INTO meta(key,value) VALUES('fictional-unrelated','1')")
            replay=self.read(etag=first.entry.identity_etag)
            self.assertTrue(replay.not_modified)
            self.assertEqual(counter.call_count,0)
            compressed=self.read(etag=first.entry.identity_etag,coding="gzip")
            self.assertFalse(compressed.not_modified)
            self.assertNotEqual(compressed.entry.gzip_etag,first.entry.identity_etag)
            self.assertTrue(self.read(etag=compressed.entry.gzip_etag,coding="gzip").not_modified)

    def test_generation_write_serves_uncached_then_regenerates(self):
        group=self.fixture.macros[0]
        original=macro_tasks._summary
        writes=[]

        def compute(connection,identifier,*args):
            if identifier==group and not writes:
                with self.store.db.write() as writer:
                    writer.execute("UPDATE macro_tasks SET title='Fictional cache race' WHERE macro_task_id=?",(group,))
                writes.append(True)
            return original(connection,identifier,*args)

        with patch.object(macro_tasks,"_summary",side_effect=compute) as counter:
            result=self.read()
            self.assertEqual(writes,[True])
            rows={row["macroTaskId"]:row for row in json.loads(result.entry.body)["macroTasks"]}
            self.assertEqual(rows[group]["title"],"Fictional macro 000")
            self.assertEqual([call.args[1] for call in counter.call_args_list].count(group),1)
            self.assertEqual(counter.call_count,3)
            regenerated=self.read(etag=result.entry.identity_etag)
            self.assertFalse(regenerated.not_modified)
            refreshed={row["macroTaskId"]:row for row in json.loads(regenerated.entry.body)["macroTasks"]}
            self.assertEqual(refreshed[group]["title"],"Fictional cache race")
            self.assertEqual([call.args[1] for call in counter.call_args_list].count(group),2)
            counter.reset_mock()
            self.assertTrue(self.read(etag=regenerated.entry.identity_etag).not_modified)
            self.assertEqual(counter.call_count,0)

    def test_response_cache_raw_sql_query_and_group_reassignment(self):
        root=self.fixture.roots[0]
        group=self.fixture.macros[0]
        with self.store.db.write() as writer:
            writer.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.task','fictional-cache-needle') WHERE task_id=?",(root,))
        first=self.read(params={"query":"fictional-cache-needle","limit":1})
        with self.store.db.write() as writer:
            writer.execute("UPDATE workflow_runs SET macro_task_id=NULL WHERE run_id=?",(root,))
        changed=self.read(etag=first.entry.identity_etag,params={"query":"fictional-cache-needle","limit":1})
        self.assertFalse(changed.not_modified)
        self.assertEqual(json.loads(first.entry.body)["macroTasks"][0]["macroTaskId"],group)
        self.assertEqual(json.loads(changed.entry.body)["macroTasks"][0]["macroTaskId"],"run:"+root)


if __name__ == "__main__":
    unittest.main()
