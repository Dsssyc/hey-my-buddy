"""Maintenance packet and partial write behavior on a private board."""
from copy import deepcopy
import json
from unittest.mock import patch

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from blackboard.routing.fixtures.maintenance_answers import EMPTY_STAGE, stage_and_commit
from support import FakeClock, seed_buddy
from hey_my_buddy.blackboard.routing.maintenance_data import (
    MAX_MICRO_TASKS, MAX_SUMMARY_CITATIONS, MAX_SUMMARY_KINDS, MAX_SUMMARY_TEXT_BYTES,
    answer_schema, build_packet, merge_domain, pending, rename_domain, stage_answer, pending_count,
)
from hey_my_buddy.protocol.structured_schema import schema_errors
from hey_my_buddy.errors import BoardError
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.protocol.task_types import TASK_TYPES
from hey_my_buddy.protocol.snapshot_query import kind_id
from hey_my_buddy.blackboard.evaluation.models import append_correspondence, current_model, ensure_model_service


def packet_key(kind):
    return tuple(kind[field] for field in ("harness", "modelId", "effort"))


class MaintenanceDataTests(WorkflowTestCase):
    def model_key(self, db, buddy):
        return (buddy["harness"], ensure_model_service(
            db, buddy["harness"], buddy.get("provider", "fixture"), buddy["model"], self.board_.store.now()),
                buddy["effort"])

    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.runs = [self.submit(self.board_, request_id=f"evidence-{n}", task=f"Task {n}",
                                 cwd=str(self.workdir(f"work-{n}")), kind="worktree")
                     for n in range(2)]
        for run in self.runs:
            claim = self.claim(self.board_, run_id=run["runId"])
            self.finish_turn(self.board_, claim)
        task_id = self.board_.submit_execution(
            requestId="maintenance-execution", task="Synthetic maintenance execution",
            adapter="command", argv=["true"], cwd=str(self.workdir("maintenance")), expectedSeconds=1800,
        )["task"]["runId"]
        self.maintenance_id = "maintenance-test"
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded', workspace_id=NULL, workspace_manifest_sha256=NULL WHERE run_id IN (?,?)",
                       [run["runId"] for run in self.runs])
            now = self.board_.store.now()
            db.execute("INSERT INTO maintenance_runs VALUES(?, 'manual','fixture',?, 'completed',NULL,NULL,?,?)",
                       (self.maintenance_id, task_id, now, now))

    def packet(self):
        with self.board_.store.db.read() as db:
            return build_packet(db)

    def summary(self, packet, citations=None):
        return [{"kindId": kind["kindId"], "text": "Observed work", "citations": citations or []}
                for kind in packet["kinds"]]

    def test_packet_has_oldest_unclassified_tasks_and_declared_schema(self):
        packet = self.packet()
        with self.board_.store.db.read() as db:
            self.assertTrue(pending(db))
        self.assertEqual({item["runId"] for item in packet["microTasks"]},
                         {run["runId"] for run in self.runs})
        self.assertEqual(len(packet["taskTypes"]), 7)
        self.assertEqual({item["task"] for item in packet["microTasks"]}, {"Task 0", "Task 1"})
        schema = answer_schema()
        answer = {"newNodes": [{"id": "new", "name": "Systems", "definition": "Systems work",
                                 "parentId": None}], "merges": [],
                  "classifications": [{"runId": run["runId"], "domainNodeId": "new", "taskType": "debug"}
                                      for run in self.runs], "summaries": self.summary(packet)}
        self.assertEqual(schema_errors(answer, schema), [])
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"]["classifications"], 2)
        self.assertIsNone(self.packet())
        with self.board_.store.db.read() as db:
            self.assertFalse(pending(db))

    def test_grouped_project_uses_creating_root_directory_without_goal_text(self):
        board = self.board_
        cwd = str(self.workdir("project-creator"))
        first = self.submit(board, request_id="project-creator", cwd=cwd, kind="worktree",
                            create_macro={"title": "Project", "goal": "Private goal sentinel"})
        second = self.submit(board, request_id="project-member", cwd=cwd, kind="worktree",
                             macroTaskId=first["macroTaskId"])
        for run in (first, second):
            self.finish_turn(board, self.claim(board, run_id=run["runId"]))
            self.accept_run(board, board.call("workflow_get", {"runId": run["runId"]}))
        with board.store.db.write() as db:
            db.execute("UPDATE tasks SET spec_json=json_set(spec_json,'$.cwd',?) WHERE task_id=?",
                       (str(self.workdir("different-member-source")), second["runId"]))
        packet = self.packet()
        projects = {item["runId"]: item["project"] for item in packet["microTasks"]}
        expected = {"id": f"repo:{cwd}", "path": cwd}
        self.assertEqual(projects[first["runId"]], expected)
        self.assertEqual(projects[second["runId"]], expected)
        self.assertNotIn("Private goal sentinel", json.dumps(packet))

    def test_packet_keeps_complete_acceptance_and_ordered_continuation_evidence(self):
        board = self.board_
        clock = FakeClock(board.store.now())
        self.enterContext(patch.object(board.store, "_clock", clock))
        self.enterContext(patch.object(board.store.workflow, "_clock", clock))
        run = self.submit(board, request_id="reworked-evidence", task="Repair a parser",
                          cwd=str(self.workdir("reworked-evidence")), kind="worktree")
        self.finish_turn(board, self.claim(board, run_id=run["runId"]))
        inputs = ["Check nested inputs.\n" + "完整输入😀" * 1200 + "\nFirst input ends here.",
                  "Add a regression case."]
        notes = ["Nested inputs still fail.", "完整说明😀" * 500 + "\nSecond note ends here."]
        for input_text, note in zip(inputs, notes):
            clock.advance(1)
            self.continue_run(board, run, input=input_text, note=note)
            self.finish_turn(board, self.claim(board, run_id=run["runId"]))
        acceptance = "Nested input and regression checks pass.\n" + "验收说明😀" * 500 + "\nAcceptance ends here."
        self.accept_run(board, board.call("workflow_get", {"runId": run["runId"]}), note=acceptance)

        packet = self.packet()
        item = next(item for item in packet["microTasks"] if item["runId"] == run["runId"])
        result = item["result"]
        self.assertEqual(result["acceptanceNote"], acceptance)
        self.assertIsNone(result["conclusion"])
        self.assertEqual(result["continuationRounds"], 2)
        continuations = result["continuations"]
        self.assertEqual([item["input"] for item in continuations], inputs)
        self.assertEqual([item["note"] for item in continuations], notes)
        self.assertLess(continuations[0]["createdAt"], continuations[1]["createdAt"])
        self.assertEqual([item["state"] for item in continuations], ["consumed", "consumed"])
        self.assertEqual([item["kind"] for item in continuations], ["manual", "manual"])
        self.assertEqual([item["turnIndex"] for item in continuations], [2, 3])
        self.assertTrue(all(item["consumedAt"] is not None for item in continuations))
        untouched = next(item for item in packet["microTasks"] if item["runId"] == self.runs[0]["runId"])
        self.assertIsNone(untouched["result"]["acceptanceNote"])
        self.assertEqual(untouched["result"]["continuations"], [])

    def test_static_summary_schema_defers_membership_and_uniqueness_to_the_batch(self):
        packet = self.packet()
        kind, = packet["kinds"]
        valid = {"kindId": kind["kindId"], "text": "Observed work", "citations": []}
        answer = {"newNodes": [], "merges": [], "classifications": [],
                  "summaries": [valid, valid, {**valid, "kindId": "outside-batch"}]}
        self.assertEqual(schema_errors(answer, answer_schema()), [])
        with self.board_.store.db.read() as db:
            staged, feedback = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE)
        self.assertEqual(staged["summaries"], {})
        self.assertIn(f"summaries[{kind['kindId']}]: duplicate kindId", feedback["errors"])
        self.assertIn("summaries[outside-batch]: unknown kindId", feedback["errors"])
        self.assertIn(f"summaries[{kind['kindId']}]: missing kindId", feedback["errors"])

    def test_encoded_kind_ids_bind_distinct_summaries_to_the_original_components(self):
        cases = (
            ("a:b", "c", "zcode:a%3Ab:c"),
            ("a", "b:c", "zcode:a:b%3Ac"),
            ("a", "custom:high", "zcode:a:custom%3Ahigh"),
            ("a", "custom%3Ahigh\"'\n高~", "zcode:a:custom%253Ahigh%22%27%0A%E9%AB%98%7E"),
        )
        with self.board_.store.db.write() as db:
            now = self.board_.store.now()
            for model_id in ("a:b", "a"):
                db.execute("INSERT INTO models(model_id,display_name) VALUES(?,?)", (model_id, model_id))
                append_correspondence(db, ("zcode", "fixture", f"model-{model_id}"), model_id, now, "same-name")
            for index, (model_id, effort, _) in enumerate(cases):
                alias = f"encoded-kind-{index}"
                seed_buddy(db, buddy_id=alias, harness="zcode", provider="fixture", model=f"model-{model_id}",
                           effort=effort, now=now)
                db.execute("UPDATE buddies SET alias=? WHERE buddy_id=?", (alias, alias))
        run_ids = {}
        for index, (_, _, kind_id) in enumerate(cases):
            alias = f"encoded-kind-{index}"
            run = self.submit(self.board_, request_id=alias, buddy=alias)
            run_id = run["runId"]
            self.finish_turn(self.board_, self.claim(self.board_, run_id=run_id))
            self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run_id}))
            run_ids[kind_id] = run_id

        packet = self.packet()
        ordinary = next(kind for kind in packet["kinds"] if kind["harness"] == BUDDY["harness"])
        self.assertEqual(ordinary["kindId"], ":".join(packet_key(ordinary)))
        self.assertEqual({packet_key(kind): kind["kindId"] for kind in packet["kinds"] if kind["harness"] == "zcode"},
                         {("zcode", model_id, effort): kind_id for model_id, effort, kind_id in cases})
        schema = answer_schema()

        answer = {
            "newNodes": [{"id": "parser", "name": "Parsing", "definition": "Parsing work", "parentId": None}],
            "merges": [],
            "classifications": [{"runId": run_id, "domainNodeId": "parser", "taskType": "debug"}
                                for run_id in packet["classificationRunIds"]],
            "summaries": [{"kindId": ordinary["kindId"], "text": "Ordinary kind", "citations": []},
                          *({"kindId": kind_id, "text": kind_id, "citations": [run_id]} for kind_id, run_id in run_ids.items())],
        }
        self.assertEqual(schema_errors(answer, schema), [])
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer, maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            self.assertEqual(result["written"]["summaries"], len(cases) + 1)
            rows = db.execute("SELECT s.model_id,s.effort,s.text,e.run_id FROM kind_summaries s"
                              " JOIN kind_summary_evidence e USING(harness,model_id,effort)"
                              " WHERE s.harness='zcode' AND e.cited=1")
            self.assertCountEqual([tuple(row) for row in rows],
                                  [(model_id, effort, kind_id, run_ids[kind_id]) for model_id, effort, kind_id in cases])

    def test_six_summary_kinds_are_selected_and_the_rest_drain_in_the_next_batch(self):
        kinds = [f"batch-kind-{index}" for index in range(9)]
        with self.board_.store.db.write() as db:
            for model in kinds:
                seed_buddy(db, buddy_id=model, harness="dsh", provider="fixture", model=model,
                           effort="off", now=self.board_.store.now())
                db.execute("UPDATE buddies SET alias=? WHERE buddy_id=?", (model, model))
        for model in kinds:
            run = self.submit(self.board_, request_id=model, buddy=model)
            self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
            self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run["runId"]}))
        first = self.packet()
        self.assertEqual(len(first["kinds"]), 6)
        original_ids = {kind["kindId"] for kind in first["kinds"]}
        answer = {"newNodes": [{"id": "work", "name": "Work", "definition": "Work evidence", "parentId": None}],
                  "merges": [],
                  "classifications": [{"runId": run_id, "domainNodeId": "work", "taskType": "change"}
                                      for run_id in first["classificationRunIds"]],
                  "summaries": self.summary(first)}
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=first, answer=answer, maintenance_run_id=self.maintenance_id)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["written"]["summaries"], 6)
        rest = self.packet()
        self.assertEqual(rest["classificationRunIds"], [])
        self.assertEqual(len(rest["kinds"]), 4)
        self.assertTrue(original_ids.isdisjoint(kind["kindId"] for kind in rest["kinds"]))

    def test_full_multibyte_batch_fits_dsh_structured_answer_limit(self):
        packet = self.packet()
        # This is a synthetic frozen batch over live task identities. Stage
        # every required entry to test the same membership and byte validation
        # used before any maintenance publication.
        run_ids = list(packet["classificationRunIds"])
        for index in range(len(run_ids), MAX_MICRO_TASKS):
            row = self.submit(self.board_, request_id=f"byte-budget-{index}",
                              cwd=str(self.workdir(f"budget-{index}")), kind="existing")
            run_ids.append(row["runId"])
        packet["classificationRunIds"] = run_ids
        packet["microTasks"] = [{"runId": run_id} for run_id in run_ids]
        citations = run_ids[:MAX_SUMMARY_CITATIONS]
        kind = packet["kinds"][0]
        packet["kinds"] = [
            {**kind, "modelId": f"00000000-0000-4000-8000-{index:012d}", "citationRunIds": citations,
             "unreadRunIds": run_ids}
            for index in range(MAX_SUMMARY_KINDS)
        ]
        for entry in packet["kinds"]:
            entry["kindId"] = kind_id(entry)
        text = "汉" * ((MAX_SUMMARY_TEXT_BYTES - 4) // 3) + "🙂"
        self.assertEqual(len(text.encode("utf-8")), MAX_SUMMARY_TEXT_BYTES)
        domain = "d" * 128
        answer = {
            "newNodes": [{"id": domain, "name": "工作", "definition": "工作证据", "parentId": None}],
            "merges": [],
            "classifications": [{"runId": run_id, "domainNodeId": domain, "taskType": "research"} for run_id in run_ids],
            "summaries": [{"kindId": entry["kindId"], "text": text, "citations": citations}
                          for entry in packet["kinds"]],
        }
        with self.board_.store.db.read() as db:
            staged, feedback = stage_answer(db, packet=packet, answer=canonical_json(answer), previous=EMPTY_STAGE)
            self.assertEqual(feedback["errors"], [])
            self.assertEqual(len(staged["classifications"]), MAX_MICRO_TASKS)
            self.assertEqual(len(staged["summaries"]), MAX_SUMMARY_KINDS)
            self.assertLess(len(canonical_json(answer).encode("utf-8")), 65_536)
            answer["summaries"][0]["text"] += "界"
            answer["summaries"][1]["citations"] = run_ids[:MAX_SUMMARY_CITATIONS + 1]
            staged, feedback = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE)
        self.assertIn(f"summaries[{packet['kinds'][0]['kindId']}]: text exceeds 4096 UTF-8 bytes", feedback["errors"])
        self.assertIn("$.summaries[1].citations: maxItems", feedback["errors"])
        self.assertEqual(len(staged["summaries"]), MAX_SUMMARY_KINDS - 2)
        answer["classifications"].append(answer["classifications"][0])
        with self.board_.store.db.read() as db:
            staged, feedback = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE)
        self.assertIn("$.classifications: maxItems", feedback["errors"])
        self.assertIn(f"classifications[{run_ids[0]}]: duplicate runId", feedback["errors"])
        self.assertIn(f"classifications[{run_ids[0]}]: missing runId", feedback["errors"])
        self.assertEqual(len(staged["classifications"]), MAX_MICRO_TASKS - 1)

    def test_schema_permitted_values_pass_the_write_boundary(self):
        kind = self.packet()["kinds"][0]
        prior = self.submit(self.board_, request_id="older-evidence", task="Earlier work",
                            cwd=str(self.workdir("older-evidence")), kind="existing")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=prior["runId"]))
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": prior["runId"]}))
        key = packet_key(kind)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO domain_nodes VALUES('older','Older','Previous expertise',NULL,NULL,?)", (self.maintenance_id,))
            db.execute("INSERT INTO micro_task_classifications VALUES(?,'older','review',?)", (prior["runId"], self.maintenance_id))
            db.execute("INSERT INTO kind_summaries VALUES(?,?,?,?,?)", (*key, "Earlier observations", self.maintenance_id))
            db.execute("INSERT INTO kind_summary_evidence VALUES(?,?,?,?,1)", (*key, prior["runId"]))
            packet = build_packet(db)
            schema = answer_schema()
            references = [[], [self.runs[0]["runId"]], [prior["runId"]],
                          [run["runId"] for run in self.runs] + [prior["runId"]]]
            for task_type in packet["taskTypes"]:
                for text in ("Observed expertise", " ", "x" * 4000):
                    for citations in references:
                        answer = {
                            "newNodes": [{"id": "fresh", "name": " Fresh ", "definition": "Current expertise", "parentId": None}],
                            "merges": [], "classifications": [
                                {"runId": run_id, "domainNodeId": "fresh", "taskType": task_type["id"]}
                                for run_id in packet["classificationRunIds"]],
                            "summaries": [{"kindId": kind['kindId'], 'text': text, 'citations': citations}],
                        }
                        with self.subTest(task_type=task_type["id"], text_length=len(text), citations=citations):
                            self.assertEqual(schema_errors(answer, schema), [])
                            db.execute("SAVEPOINT schema_case")
                            try:
                                checked = stage_and_commit(db, packet=packet, answer=answer, maintenance_run_id=self.maintenance_id)
                                self.assertEqual(checked["errors"], [])
                            finally:
                                db.execute("ROLLBACK TO schema_case")
                                db.execute("RELEASE schema_case")

    def test_existing_id_cannot_change_parent_and_names_are_canonical(self):
        packet = self.packet()
        answer = {"newNodes": [
            {"id": "parent-a", "name": " Parent A ", "definition": "A", "parentId": None},
            {"id": "parent-b", "name": "Parent B", "definition": "B", "parentId": None},
            {"id": "child", "name": " Child ", "definition": "C", "parentId": "parent-a"}],
            "merges": [], "classifications": [
                {"runId": run["runId"], "domainNodeId": "child", "taskType": "create"}
                for run in self.runs], "summaries": self.summary(packet)}
        self.assertEqual(schema_errors(answer, answer_schema()), [])
        with self.board_.store.db.write() as db:
            applied = stage_and_commit(db, packet=packet, answer=answer,
                                   maintenance_run_id=self.maintenance_id)
            self.assertEqual(applied["errors"], [])
            self.assertEqual(db.execute("SELECT name FROM domain_nodes WHERE node_id='child'").fetchone()[0],
                             "Child")
            changed = deepcopy(answer)
            changed["newNodes"][2]["parentId"] = "parent-b"
            changed["newNodes"].append(
                {"id": "duplicate", "name": " Parent A ", "definition": "Same name", "parentId": None})
            rejected = stage_and_commit(db, packet=packet, answer=changed,
                                    maintenance_run_id=self.maintenance_id)
            self.assertTrue(any("existing node cannot be changed" in error for error in rejected["errors"]))
            self.assertTrue(any("active domain name already exists" in error for error in rejected["errors"]))
            self.assertEqual(db.execute("SELECT parent_id FROM domain_nodes WHERE node_id='child'").fetchone()[0],
                             "parent-a")
            with self.assertRaises(BoardError):
                rename_domain(db, "child", " Parent B ", "Duplicate")
            rename_domain(db, "child", " Renamed ", "C")
            self.assertEqual(db.execute("SELECT name FROM domain_nodes WHERE node_id='child'").fetchone()[0],
                             "Renamed")

    def test_nul_bytes_in_new_domain_nodes_are_rejected_at_staging(self):
        packet = self.packet()
        for field, value in (("id", "bad\x00id"), ("name", "\x00Visible"), ("definition", "\x00Defined")):
            node = {"id": "clean-id", "name": "Clean", "definition": "Clean def", "parentId": None}
            node[field] = value
            answer = {"newNodes": [node], "merges": [], "classifications": [], "summaries": []}
            with self.subTest(field=field), self.board_.store.db.read() as db:
                staged, feedback = stage_answer(db, packet=packet, answer=answer, previous=EMPTY_STAGE)
            self.assertEqual(staged["tree"], [])
            self.assertTrue(any("NUL" in error for error in feedback["errors"]), feedback["errors"])

    def test_packet_reads_kind_counts_and_real_provider_failure_columns(self):
        with self.board_.store.db.write() as db:
            ensure_model_service(db, BUDDY["harness"], "other-provider", BUDDY["model"], self.board_.store.now())
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,"
                       "first_passes,rework_rounds,failures,defects) VALUES(?,?,?,2,1,1,0,1,1)",
                       self.model_key(db, BUDDY))
            db.executemany(
                "INSERT INTO buddy_infrastructure_failures(harness,provider,model,effort,count,last_at)"
                " VALUES(?,?,?,?,?,?)",
                [(BUDDY["harness"], provider, BUDDY["model"], BUDDY["effort"], count,
                  "2026-01-01T00:00:00Z") for provider, count in
                 ((BUDDY["provider"], 2), ("other-provider", 3))])
        packet = self.packet()
        self.assertEqual(len(packet["kinds"]), 1)
        self.assertEqual(packet["comparisonStatistics"], [])
        kind = packet["kinds"][0]
        with self.board_.store.db.read() as db:
            self.assertEqual(packet_key(kind),
                             (BUDDY["harness"], current_model(db, BUDDY["harness"], BUDDY["provider"], BUDDY["model"]), BUDDY["effort"]))
        self.assertEqual(kind["modelDisplayName"], BUDDY["model"])
        self.assertEqual((kind["statistics"]["overall"]["samples"],
                          kind["statistics"]["overall"]["failures"],
                          kind["statistics"]["overall"]["defects"]), (2, 1, 1))
        self.assertEqual({entry["provider"]: entry["count"]
                          for entry in kind["statistics"]["infrastructureFailures"]},
                         {BUDDY["provider"]: 2, "other-provider": 3})

    def test_comparison_statistics_include_sampled_kinds_outside_summary_batch(self):
        with self.board_.store.db.write() as db:
            current = self.model_key(db, BUDDY)
            peer = self.model_key(db, {"harness": "codex", "model": "retired-peer", "effort": "high"})
            unrelated = self.model_key(db, {"harness": "claude", "model": "unrelated-peer", "effort": "low"})
            db.executemany("INSERT INTO domain_nodes VALUES(?,?,?,?,?,?)", [
                ("systems", "Systems", "Systems work", None, None, self.maintenance_id),
                ("parser", "Parser", "Parser work", "systems", None, self.maintenance_id),
                ("old-parser", "Old parser", "Same expertise", None, "parser", self.maintenance_id),
                ("writing", "Writing", "Writing work", None, None, self.maintenance_id)])
            db.executemany(
                "INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,rework_rounds,failures)"
                " VALUES(?,?,?,?,?,?,?,?)",
                [(*current, 2, 1, 1, 0, 1), (*peer, 3, 2, 1, 1, 1), (*unrelated, 1, 1, 1, 0, 0)])
            db.executemany(
                "INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,samples,successes,"
                "first_passes,rework_rounds,failures) VALUES(?,?,?,?,?,?,?,?,?,?)", [
                    (*current, "parser", "debug", 2, 1, 1, 0, 1),
                    (*peer, "old-parser", "debug", 2, 1, 0, 1, 1),
                    (*peer, "parser", "review", 1, 1, 1, 0, 0),
                    (*unrelated, "writing", "write", 1, 1, 1, 0, 0)])
            db.execute("INSERT INTO kind_cell_statistics_buckets VALUES(?,?,?,?,?,'delivery_seconds',1,0,2)",
                       (*peer, "old-parser", "debug"))
            db.execute("INSERT INTO micro_task_classifications VALUES(?,'parser','debug',?)",
                       (self.runs[0]["runId"], self.maintenance_id))
        packet = self.packet()
        self.assertEqual(len(packet["kinds"]), 1)
        self.assertEqual(packet["kinds"][0]["modelDisplayName"], BUDDY["model"])
        comparisons = {packet_key(kind): kind["statistics"]
                       for kind in packet["comparisonStatistics"]}
        targets = {packet_key(kind) for kind in packet["kinds"]}
        self.assertEqual(targets, {current})
        self.assertTrue(targets.isdisjoint(comparisons))
        self.assertEqual(set(comparisons), {peer, unrelated})
        self.assertEqual(packet["kinds"][0]["statistics"]["byDomainTaskType"]["parser"]["debug"]["samples"], 2)
        stats = comparisons[peer]
        self.assertEqual(stats["overall"]["samples"], 3)
        self.assertEqual(stats["byDomain"]["systems"]["samples"], 3)
        self.assertEqual(stats["byDomainTaskType"]["parser"]["debug"]["samples"], 2)
        self.assertEqual(stats["byDomain"]["parser"]["samples"], 3)
        self.assertEqual(set(stats["byDomainTaskType"]["parser"]), {"debug"})
        self.assertEqual(set(stats["byDomainTaskType"]["systems"]), {"debug"})
        self.assertEqual(comparisons[unrelated]["overall"]["samples"], 1)
        self.assertEqual(comparisons[unrelated]["byDomain"], {})
        self.assertEqual(comparisons[unrelated]["byDomainTaskType"], {})
        median = stats["byDomainTaskType"]["parser"]["debug"]["medianDeliverySeconds"]
        self.assertGreaterEqual(median, 1)
        self.assertLess(median, 1.2)
        self.assertNotIn("old-parser", stats["byDomain"])

    def test_unclassified_tasks_do_not_select_peer_cells_without_target_samples(self):
        with self.board_.store.db.write() as db:
            peer = self.model_key(db, {"harness": "codex", "model": "peer", "effort": "high"})
            db.execute("INSERT INTO domain_nodes VALUES('parser','Parser','Parser work',NULL,NULL,?)",
                       (self.maintenance_id,))
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       "rework_rounds,failures) VALUES(?,?,?,1,1,1,0,0)", peer)
            db.execute("INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,samples,"
                       "successes,first_passes,rework_rounds,failures)"
                       " VALUES(?,?,?,'parser','debug',1,1,1,0,0)", peer)
        packet = self.packet()
        self.assertTrue(all(item["classification"] is None for item in packet["microTasks"]))
        self.assertEqual(len(packet["comparisonStatistics"]), 1)
        comparison = packet["comparisonStatistics"][0]
        self.assertEqual(comparison["statistics"]["overall"]["samples"], 1)
        self.assertEqual(comparison["statistics"]["byDomain"], {})
        self.assertEqual(comparison["statistics"]["byDomainTaskType"], {})

    def test_comparison_statistics_use_union_of_summary_kind_cells(self):
        second_buddy = {**BUDDY, "model": "second-target"}
        second = self.submit(self.board_, request_id="second-target", buddy=second_buddy,
                             cwd=str(self.workdir("second-target")), kind="worktree")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=second["runId"]))
        view = self.board_.call("workflow_get", {"runId": second["runId"]})
        self.accept_run(self.board_, view)
        with self.board_.store.db.write() as db:
            current = self.model_key(db, BUDDY)
            second_key = self.model_key(db, second_buddy)
            peer_key = self.model_key(db, {"harness": "codex", "model": "peer", "effort": "high"})
            db.executemany("INSERT INTO domain_nodes VALUES(?,?,?,NULL,NULL,?)", [
                (domain, domain, domain + " work", self.maintenance_id) for domain in ("parser", "writing")])
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       "rework_rounds,failures) VALUES(?,?,?,1,1,1,0,0)",
                       current)
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       "rework_rounds,failures) VALUES(?,?,?,3,3,3,0,0)", peer_key)
            db.executemany("INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,"
                           "samples,successes,first_passes,rework_rounds,failures) VALUES(?,?,?,?,?,1,1,1,0,0)", [
                (*current, "parser", "debug"),
                (*second_key, "writing", "write"),
                (*peer_key, "parser", "debug"),
                (*peer_key, "writing", "write"),
                (*peer_key, "writing", "review")])
        packet = self.packet()
        self.assertEqual(len(packet["kinds"]), 2)
        targets = {packet_key(kind) for kind in packet["kinds"]}
        comparisons = {packet_key(kind)
                       for kind in packet["comparisonStatistics"]}
        self.assertTrue(targets.isdisjoint(comparisons))
        self.assertEqual(comparisons, {peer_key})
        peer = next(item["statistics"] for item in packet["comparisonStatistics"] if item["modelId"] == peer_key[1])
        self.assertEqual(set(peer["byDomain"]), {"parser", "writing"})
        self.assertEqual(set(peer["byDomainTaskType"]["parser"]), {"debug"})
        self.assertEqual(set(peer["byDomainTaskType"]["writing"]), {"write"})

    def comparison_size_packets(self):
        """One relevant cell and a larger unrelated history, all on the private board."""
        peers = [{"harness": "codex", "model": f"fixture-peer-{index:02}", "effort": "high"}
                 for index in range(14)]
        domains = [f"unrelated-{index:02}" for index in range(20)]
        with self.board_.store.db.write() as db:
            current = self.model_key(db, BUDDY)
            db.executemany("INSERT INTO domain_nodes VALUES(?,?,?,NULL,NULL,?)", [
                (domain, domain, domain + " work", self.maintenance_id) for domain in ["parser", *domains]])
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       "rework_rounds,failures) VALUES(?,?,?,1,1,1,0,0)", current)
            db.execute("INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,samples,"
                       "successes,first_passes,rework_rounds,failures) VALUES(?,?,?,'parser','debug',1,1,1,0,0)",
                       current)
            for peer in peers:
                key = self.model_key(db, peer)
                cells = [("parser", "debug")] + [(domain, task_type) for domain in domains for task_type in TASK_TYPES]
                db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                           "rework_rounds,failures) VALUES(?,?,?,?,?,?,0,0)",
                           (*key, len(cells), len(cells), len(cells)))
                db.executemany("INSERT INTO kind_cell_statistics(harness,model_id,effort,domain_node_id,task_type,"
                               "samples,successes,first_passes,rework_rounds,failures) VALUES(?,?,?,?,?,1,1,1,0,0)",
                               [(*key, domain, task_type) for domain, task_type in cells])
            packet = build_packet(db)
        # The previous projection repeated every sampled target's full domain
        # facts (all its cells selected themselves), without infrastructure facts.
        repeated = [{**{field: kind[field] for field in ("harness", "modelId", "effort")},
                     "statistics": {field: kind["statistics"][field]
                                    for field in ("overall", "byDomain", "byDomainTaskType")}}
                    for kind in packet["kinds"] if kind["statistics"]["overall"]["samples"]]
        old_packet = {**packet, "comparisonStatistics": sorted(
            packet["comparisonStatistics"] + repeated,
            key=packet_key)}
        return old_packet, packet

    def test_comparison_packet_keeps_relevant_peer_cells_without_repeated_targets(self):
        old, new = self.comparison_size_packets()
        self.assertEqual(len(old["comparisonStatistics"]), 15)
        self.assertEqual(len(new["comparisonStatistics"]), 14)
        targets = {packet_key(kind) for kind in new["kinds"]}
        self.assertTrue(targets.isdisjoint(packet_key(kind)
                                          for kind in new["comparisonStatistics"]))
        for kind in new["comparisonStatistics"]:
            self.assertEqual(set(kind["statistics"]["byDomain"]), {"parser"})
            self.assertEqual(set(kind["statistics"]["byDomainTaskType"]), {"parser"})
            self.assertEqual(set(kind["statistics"]["byDomainTaskType"]["parser"]), {"debug"})
        self.assertLess(len(canonical_json(new).encode()), len(canonical_json(old).encode()))
        self.assertEqual({key: value for key, value in old.items() if key != "comparisonStatistics"},
                         {key: value for key, value in new.items() if key != "comparisonStatistics"})

    def test_repeated_classification_answer_accumulates_cell_once(self):
        accepted = self.submit(self.board_, request_id="accepted-cell", task="Accepted evidence",
                               cwd=str(self.workdir("accepted-cell")), kind="worktree")
        self.finish_turn(self.board_, self.claim(self.board_, run_id=accepted["runId"]))
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": accepted["runId"]}))
        packet = self.packet()
        answer = {"newNodes": [{"id": "cell", "name": "Cell", "definition": "Classified work",
                                "parentId": None}], "merges": [],
                  "classifications": [{"runId": run_id, "domainNodeId": "cell", "taskType": "review"}
                                      for run_id in packet["classificationRunIds"]],
                  "summaries": self.summary(packet, [accepted["runId"]])}
        with self.board_.store.db.write() as db:
            first = stage_and_commit(db, packet=packet, answer=answer,
                                 maintenance_run_id=self.maintenance_id)
            repeated = stage_and_commit(db, packet=packet, answer={**answer, "newNodes": [], "merges": []},
                                    maintenance_run_id=self.maintenance_id)
            cell = db.execute("SELECT samples,successes FROM kind_cell_statistics"
                              " WHERE domain_node_id='cell' AND task_type='review'").fetchone()
        self.assertEqual(first["errors"], [])
        self.assertEqual(repeated["errors"], [])
        self.assertEqual(repeated["written"]["classifications"], 0)
        self.assertEqual(tuple(cell), (3, 1))

    def test_partial_write_correction_cannot_replace_accepted_facts(self):
        packet = self.packet()
        first = self.runs[0]["runId"]
        second = self.runs[1]["runId"]
        kind_id = packet["kinds"][0]["kindId"]
        answer = {"newNodes": [{"id": "root", "name": "Engineering", "definition": "Build systems",
                                 "parentId": None}], "merges": [],
                  "classifications": [{"runId": first, "domainNodeId": "root", "taskType": "create"},
                                      {"runId": second, "domainNodeId": "missing", "taskType": "debug"}],
                  "summaries": [{"kindId": kind_id, 'text': 'Observed engineering work', 'citations': [first]}]}
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
        self.assertTrue(any(second in error for error in result["errors"]))
        self.assertEqual(result["written"]["summaries"], 1)
        answer["newNodes"] = []
        answer["classifications"] = [
            {"runId": first, "domainNodeId": "root", "taskType": "debug"},
            {"runId": second, "domainNodeId": "root", "taskType": "debug"}]
        answer["summaries"][0]["text"] = "Attempted replacement"
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            rows = list(db.execute("SELECT run_id,task_type FROM micro_task_classifications ORDER BY run_id"))
            summary = db.execute("SELECT text FROM kind_summaries").fetchone()[0]
        self.assertEqual(result["errors"], [])
        self.assertEqual(dict(rows), {first: "create", second: "debug"})
        self.assertEqual(summary, "Observed engineering work")

    def test_new_parent_chain_merge_flatten_and_rename_validation(self):
        packet = self.packet()
        answer = {"newNodes": [
            {"id": "leaf", "name": "Leaf", "definition": "Leaf expertise", "parentId": "source"},
            {"id": "source", "name": "Source", "definition": "Source expertise", "parentId": None},
            {"id": "target", "name": "Target", "definition": "Target expertise", "parentId": None},
            {"id": "alias", "name": "Alias", "definition": "Alias expertise", "parentId": None}],
            "merges": [{"sourceId": "alias", "targetId": "source"},
                       {"sourceId": "source", "targetId": "target"}],
            "classifications": [{"runId": run["runId"], "domainNodeId": "leaf", "taskType": "create"}
                                for run in self.runs], "summaries": self.summary(packet)}
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            rows = {r["node_id"]: dict(r) for r in db.execute("SELECT * FROM domain_nodes")}
            self.assertEqual(rows["leaf"]["parent_id"], "target")
            self.assertEqual(rows["alias"]["merged_into_id"], "target")
            repeated = stage_and_commit(db, packet=packet, answer={**answer, "newNodes": [], "merges": []},
                                    maintenance_run_id=self.maintenance_id)
            self.assertEqual(repeated["errors"], [])
            self.assertEqual(repeated["written"]["merges"], 0)
            rename_domain(db, "target", "Retained", "Renamed expertise")
            try:
                rename_domain(db, "leaf", "Retained", "Duplicate")
            except BoardError as error:
                self.assertEqual(error.code, "INVALID_ARGUMENT")
            else:
                self.fail("duplicate active name was accepted")
            try:
                merge_domain(db, "target", "leaf")
            except BoardError as error:
                self.assertEqual(error.code, "INVALID_ARGUMENT")
            else:
                self.fail("descendant merge was accepted")

    def test_invalid_citation_keeps_previous_summary(self):
        packet = self.packet()
        kind_id = packet["kinds"][0]["kindId"]
        answer = {"newNodes": [], "merges": [], "classifications": [],
                  "summaries": [{"kindId": kind_id, 'text': 'Unsupported', 'citations': ['outside-batch']}]}
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertIsNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertTrue(any("citation" in error for error in result["errors"]))

    def test_bad_shape_does_not_block_independent_facts_and_summary_is_required(self):
        packet = self.packet()
        first, second = (run["runId"] for run in self.runs)
        answer = {
            "newNodes": [
                {"id": "good", "name": "Good", "definition": "A valid node", "parentId": None},
                {"id": "bad", "name": "Bad", "definition": "A valid node", "parentId": 12},
                {"id": "duplicate", "name": "Good", "definition": "A duplicate name", "parentId": None},
                {"id": "cycle-a", "name": "A", "definition": "A node", "parentId": "cycle-b"},
                {"id": "cycle-b", "name": "B", "definition": "B node", "parentId": "cycle-a"},
            ],
            "merges": {},
            "classifications": [
                {"runId": first, "domainNodeId": "good", "taskType": "create"},
                {"runId": second, "domainNodeId": "good", "taskType": "invalid"},
            ],
            "summaries": [],
        }
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM domain_nodes").fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM micro_task_classifications").fetchone()[0], 1)
            self.assertTrue(pending(db))
        self.assertEqual(result["written"]["nodes"], 1)
        self.assertEqual(result["written"]["classifications"], 1)
        self.assertTrue(any("newNodes[1]" in error for error in result["errors"]))
        self.assertTrue(any("$.merges: type" in error for error in result["errors"]))
        self.assertTrue(any("active domain name already exists" in error for error in result["errors"]))
        self.assertTrue(any("parent cycle" in error for error in result["errors"]))
        self.assertTrue(any("summaries[" in error and "missing" in error for error in result["errors"]))

    def test_merge_target_must_still_be_active(self):
        packet = self.packet()
        answer = {
            "newNodes": [
                {"id": "source", "name": "Source", "definition": "Source", "parentId": None},
                {"id": "child", "name": "Child", "definition": "Child", "parentId": "source"},
                {"id": "target", "name": "Target", "definition": "Target", "parentId": None},
                {"id": "alias", "name": "Alias", "definition": "Alias", "parentId": None},
            ],
            "merges": [{"sourceId": "alias", "targetId": "target"},
                       {"sourceId": "source", "targetId": "alias"}],
            "classifications": [], "summaries": self.summary(packet),
        }
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            nodes = {row["node_id"]: row for row in db.execute("SELECT * FROM domain_nodes")}
        self.assertEqual(result["written"]["merges"], 1)
        self.assertTrue(any("merge target" in error for error in result["errors"]))
        self.assertIsNone(nodes["source"]["merged_into_id"])
        self.assertEqual(nodes["child"]["parent_id"], "source")

    def test_host_boundary_count_comes_from_requests(self):
        first = self.runs[0]["runId"]
        with self.board_.store.db.write() as db:
            now = self.board_.store.now()
            db.execute(
                "INSERT INTO workflow_requests(request_id,run_id,summary,payload_json,"
                "state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("host-boundary-test", first, "Review evidence", "{}", "superseded",
                 now, now),
            )
        item = next(item for item in self.packet()["microTasks"] if item["runId"] == first)
        self.assertEqual(item["result"]["hostBoundaries"], 1)
        self.assertEqual(item["result"]["hostBoundaryFacts"][0]["requestId"], "host-boundary-test")

    def test_deleted_packet_run_is_skipped_and_affected_summary_is_rejected(self):
        packet = deepcopy(self.packet())
        packet["microTasks"][0]["runId"] = "deleted-after-packet"
        packet["classificationRunIds"][0] = "deleted-after-packet"
        survivor = packet["classificationRunIds"][1]
        answer = {
            "newNodes": [{"id": "survivor", "name": "Survivor", "definition": "Valid work",
                          "parentId": None}],
            "merges": [],
            "classifications": [
                {"runId": "deleted-after-packet", "domainNodeId": "survivor", "taskType": "create"},
                {"runId": survivor, "domainNodeId": "survivor", "taskType": "create"},
            ],
            "summaries": self.summary(packet, ["deleted-after-packet", survivor]),
        }
        with self.board_.store.db.write() as db:
            result = stage_and_commit(db, packet=packet, answer=answer,
                                  maintenance_run_id=self.maintenance_id)
            rows = list(db.execute("SELECT run_id FROM micro_task_classifications"))
            self.assertIsNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertEqual([row[0] for row in rows], [survivor])
        self.assertEqual(result["written"]["classifications"], 1)
        self.assertTrue(any("citation" in error for error in result["errors"]))
        answer["newNodes"] = []
        answer["summaries"] = self.summary(packet, [survivor])
        with self.board_.store.db.write() as db:
            corrected = stage_and_commit(db, packet=packet, answer=answer,
                                     maintenance_run_id=self.maintenance_id)
            self.assertIsNotNone(db.execute("SELECT text FROM kind_summaries").fetchone())
        self.assertEqual(corrected["errors"], [])

    def test_console_domain_boundaries_reject_wrong_id_shapes(self):
        with self.board_.store.db.write() as db:
            for action in (lambda: rename_domain(db, {"id": "x"}, "Name", "Definition"),
                           lambda: merge_domain(db, {"id": "x"}, "target"),
                           lambda: merge_domain(db, "source", {"id": "x"})):
                with self.assertRaises(BoardError) as failure:
                    action()
                self.assertEqual(failure.exception.code, "INVALID_ARGUMENT")

    def test_raw_json_answer_and_invalid_json_boundary(self):
        packet = self.packet()
        answer = {
            "newNodes": [{"id": "work", "name": "Work", "definition": "Work tasks", "parentId": None}],
            "merges": [],
            "classifications": [{"runId": run["runId"], "domainNodeId": "work", "taskType": "create"}
                                for run in self.runs],
            "summaries": self.summary(packet),
        }
        with self.board_.store.db.write() as db:
            for raw in (None, "{invalid"):
                rejected = stage_and_commit(db, packet=packet, answer=raw,
                                        maintenance_run_id=self.maintenance_id)
                self.assertTrue(rejected["errors"])
            applied = stage_and_commit(db, packet=packet, answer=json.dumps(answer),
                                   maintenance_run_id=self.maintenance_id)
        self.assertEqual(applied["errors"], [])
        self.assertEqual(applied["written"]["classifications"], 2)

    def test_sixty_classified_tasks_are_read_in_two_batches_without_losing_progress(self):
        for number in range(2, 60):
            run = self.submit(self.board_, request_id=f"read-evidence-{number}", task=f"Evidence {number}",
                              cwd=str(self.workdir(f"read-evidence-{number}")), kind="existing")
            self.finish_turn(self.board_, self.claim(self.board_, run_id=run["runId"]))
            self.runs.append(run)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET state='concluded', workspace_id=NULL, workspace_manifest_sha256=NULL")
            db.execute("INSERT INTO domain_nodes VALUES('existing','Existing','Existing domain',NULL,NULL,?)",
                       (self.maintenance_id,))
            db.executemany("INSERT INTO micro_task_classifications VALUES(?,'existing','review',?)",
                           [(run["runId"], self.maintenance_id) for run in self.runs])
            self.assertEqual(pending_count(db), 60)
            first = build_packet(db)
            self.assertEqual(len(first["microTasks"]), 50)
            self.assertEqual(first["classificationRunIds"], [])
            self.assertTrue(all(item["classification"] == {"domainNodeId": "existing", "taskType": "review"}
                                for item in first["microTasks"]))
            first_ids = {item["runId"] for item in first["microTasks"]}
            result = stage_and_commit(db, packet=first, answer={
                "newNodes": [], "merges": [], "classifications": [],
                "summaries": self.summary(first, [first["microTasks"][0]["runId"]]),
            }, maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            self.assertEqual(pending_count(db), 10)
            second = build_packet(db)
            self.assertEqual(len(second["microTasks"]), 10)
            self.assertFalse(first_ids & {item["runId"] for item in second["microTasks"]})
            result = stage_and_commit(db, packet=second, answer={
                "newNodes": [], "merges": [], "classifications": [],
                "summaries": self.summary(second, [second["microTasks"][0]["runId"]]),
            }, maintenance_run_id=self.maintenance_id)
            self.assertEqual(result["errors"], [])
            self.assertEqual(pending_count(db), 0)
            self.assertIsNone(build_packet(db))
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence").fetchone()[0], 60)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM kind_summary_evidence WHERE cited=1").fetchone()[0], 1)

    def test_only_ability_rounds_require_kind_summary(self):
        first, second = (run["runId"] for run in self.runs)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO domain_nodes VALUES('existing','Existing','Domain',NULL,NULL,?)",
                       (self.maintenance_id,))
            db.executemany("INSERT INTO micro_task_classifications VALUES(?,'existing','review',?)",
                           [(first, self.maintenance_id), (second, self.maintenance_id)])
            db.execute("UPDATE attempts SET result_json=json_set(result_json,'$.terminationReason','service-stop')"
                       " WHERE attempt_id=(SELECT attempt_id FROM workflow_turns WHERE run_id=?)", (first,))
            self.assertEqual(pending_count(db), 1)
            packet = build_packet(db)
            self.assertEqual([task["runId"] for task in packet["microTasks"]], [second])
            self.assertEqual(packet["kinds"][0]["citationRunIds"], [second])
            for code in ("overloaded", "content-policy"):
                with self.subTest(code=code):
                    db.execute("UPDATE attempts SET result_json=json_set(result_json,'$.result.providerRejection.code',?)"
                               " WHERE attempt_id=(SELECT attempt_id FROM workflow_turns WHERE run_id=?)",
                               (code, second))
                    self.assertFalse(pending(db))
                    self.assertEqual(pending_count(db), 0)
                    self.assertIsNone(build_packet(db))

    def test_packet_order_and_finish_follow_conclusion_time(self):
        first, second = (run["runId"] for run in self.runs)
        with self.board_.store.db.write() as db:
            db.execute("UPDATE workflow_runs SET updated_at='2026-01-01T00:00:00Z'"
                       " WHERE run_id=?", (first,))
            db.execute("UPDATE workflow_runs SET updated_at='2025-01-01T00:00:00Z'"
                       " WHERE run_id=?", (second,))
            for number, run_id in enumerate((first, second)):
                ended = f"202{5 + number}-01-01T00:00:00Z"
                db.execute(
                    "INSERT INTO workflow_host_conclusions"
                    "(conclusion_id,run_id,execution_status,note,"
                    "actor_role,actor_id,created_at) VALUES(?,?,'failed','Done',"
                    "'host','test',?)",
                    (f"conclusion-{number}", run_id, ended))
            packet = build_packet(db)
        self.assertEqual([task["runId"] for task in packet["microTasks"]], [first, second])
        self.assertEqual([task["result"]["finishedAt"] for task in packet["microTasks"]],
                         ["2025-01-01T00:00:00Z", "2026-01-01T00:00:00Z"])
