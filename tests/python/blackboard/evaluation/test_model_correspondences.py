"""Model-service correspondences, console corrections and frozen execution evidence."""
from __future__ import annotations

from blackboard.tasks.test_workflow import BUDDY, WorkflowTestCase
from support import enable_fixture_buddy

from hey_my_buddy.blackboard.evaluation.models import current_model, model_view
from hey_my_buddy.blackboard.routing.evidence import domain_statistics
from hey_my_buddy.blackboard.routing.maintenance_data import build_packet
from hey_my_buddy.blackboard.routing.statistics import accumulate_cell, accumulate_defect
from hey_my_buddy.console.reads import console_snapshot_projection
from hey_my_buddy.errors import BoardError


class ModelCorrespondenceTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_ = self.board()
        self.session = "model-correspondence-console"
        self.board_.service.register_console_authority(self.session)

    def publish(self, command, **changes):
        return self.board_.call("user_policy_publish", {
            "commandId": command, "consoleAuthority": {"sessionId": self.session}, **changes,
        })

    def model_id(self, buddy):
        with self.board_.store.db.read() as db:
            return current_model(db, buddy["harness"], buddy["provider"], buddy["model"])

    def shown_correspondence(self, service):
        """The console's current view of a service's correspondence, as sent back on undo."""
        return next(row for row in console_snapshot_projection(self.board_.service)[0]["modelCorrespondences"]
                    if all(row[key] == service[key] for key in service))

    def test_exact_name_unifies_services_and_different_name_stays_separate(self):
        first = BUDDY
        same_name = {**first, "provider": "second-provider"}
        different_name = {**first, "provider": "third-provider", "model": "different-name"}
        for buddy in (first, same_name, different_name):
            enable_fixture_buddy(self.board_.store, buddy)
        shared = self.model_id(first)
        self.assertEqual(self.model_id(same_name), shared)
        self.assertNotEqual(self.model_id(different_name), shared)
        with self.board_.store.db.read() as db:
            view = model_view(db, shared)
        self.assertEqual(view["displayName"], first["model"])
        self.assertEqual(view["names"], [
            {key: buddy[key] for key in ("harness", "provider", "model")}
            for buddy in sorted((first, same_name), key=lambda value: value["provider"])
        ])

    def test_new_same_name_service_follows_latest_correspondence_and_names_follow_evidence(self):
        first = BUDDY
        mirror = {**first, 'provider': 'mirror-provider'}
        for buddy in (first, mirror):
            enable_fixture_buddy(self.board_.store, buddy)
        old_id = self.model_id(first)
        service = {key: first[key] for key in ('harness', 'provider', 'model')}
        self.publish('same-name-generation', modelCorrespondenceChanges=[{
            **service, 'action': 'generation', 'displayName': first['model'],
        }])
        new_id = self.model_id(first)
        newcomer = {**first, 'provider': 'new-provider'}
        enable_fixture_buddy(self.board_.store, newcomer)
        self.assertEqual(self.model_id(newcomer), new_id)
        self.assertEqual(self.model_id(mirror), old_id)
        with self.board_.store.db.read() as db:
            old_model = model_view(db, old_id)
            new_model = model_view(db, new_id)
        self.assertEqual(old_model['displayName'], new_model['displayName'])
        # Names follow live evidence: the old generation keeps the services still
        # corresponding to it, the new one those that moved with the change.
        self.assertEqual(old_model['names'], [
            {key: mirror[key] for key in ('harness', 'provider', 'model')}])
        self.assertEqual(new_model['names'], [
            {key: buddy[key] for key in ('harness', 'provider', 'model')}
            for buddy in sorted((first, newcomer), key=lambda value: value['provider'])])

    def test_console_generation_undo_remap_and_model_notes(self):
        first = BUDDY
        other = {**first, "provider": "another-provider", "model": "another-name"}
        for buddy in (first, other):
            enable_fixture_buddy(self.board_.store, buddy)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        other_id = self.model_id(other)
        self.publish("generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Second generation",
        }])
        generated = self.model_id(first)
        self.assertNotIn(generated, (original, other_id))
        self.publish("annotate", modelNoteChanges=[{"modelId": generated, "text": "New generation observed"}])
        snapshot = console_snapshot_projection(self.board_.service)[0]
        self.assertEqual(next(model for model in snapshot["models"] if model["modelId"] == generated)["displayName"],
                         "Second generation")
        self.assertEqual([(note["modelId"], note["text"]) for note in snapshot["modelNotes"]],
                         [(generated, "New generation observed")])
        self.assertTrue(snapshot["modelNotes"][0]["updatedAt"])
        shown = next(correspondence for correspondence in snapshot["modelCorrespondences"]
                     if correspondence["model"] == first["model"])
        self.assertEqual(shown["modelId"], generated)
        self.publish("undo", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": shown["correspondenceId"]}])
        self.assertEqual(self.model_id(first), original)
        self.publish("remap", modelCorrespondenceChanges=[{**service, "action": "map", "modelId": other_id}])
        self.assertEqual(self.model_id(first), other_id)
        with self.board_.store.db.read() as db:
            history = list(db.execute(
                "SELECT model_id,source FROM model_correspondences WHERE harness=? AND provider=? AND model=? ORDER BY rowid",
                tuple(service.values()),
            ))
            # Undo removed the generation change; the note keeps its model alive
            # with no remaining name of its own.
            self.assertEqual(model_view(db, generated)["names"], [])
        self.assertEqual([(row["model_id"], row["source"]) for row in history], [
            (original, "same-name"), (other_id, "user"),
        ])

    def test_undo_walks_back_one_change_at_a_time_until_the_first_row(self):
        first = BUDDY
        other = {**first, "provider": "another-provider", "model": "another-name"}
        for buddy in (first, other):
            enable_fixture_buddy(self.board_.store, buddy)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        other_id = self.model_id(other)
        self.publish("map-other", modelCorrespondenceChanges=[{**service, "action": "map", "modelId": other_id}])
        self.publish("map-back", modelCorrespondenceChanges=[{**service, "action": "map", "modelId": original}])
        self.assertEqual(self.model_id(first), original)
        self.publish("undo-latest", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
        self.assertEqual(self.model_id(first), other_id)
        self.publish("undo-earlier", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
        self.assertEqual(self.model_id(first), original)
        with self.assertRaises(BoardError) as rejected:
            self.publish("undo-first-row", modelCorrespondenceChanges=[{
                **service, "action": "undo",
                "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
        self.assertEqual(rejected.exception.code, "CONFLICT")
        with self.board_.store.db.read() as db:
            history = list(db.execute(
                "SELECT model_id,source FROM model_correspondences WHERE harness=? AND provider=? AND model=? ORDER BY rowid",
                tuple(service.values())))
        self.assertEqual([(row["model_id"], row["source"]) for row in history], [(original, "same-name")])

    def test_stale_or_repeated_correspondence_id_is_rejected_without_deleting(self):
        first = BUDDY
        other = {**first, "provider": "another-provider", "model": "another-name"}
        for buddy in (first, other):
            enable_fixture_buddy(self.board_.store, buddy)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        other_id = self.model_id(other)
        same_name_id = self.shown_correspondence(service)["correspondenceId"]
        self.publish("map-other", modelCorrespondenceChanges=[{**service, "action": "map", "modelId": other_id}])
        # A second console tab still shows the superseded same-name change.
        with self.assertRaises(BoardError) as stale:
            self.publish("undo-stale", modelCorrespondenceChanges=[{
                **service, "action": "undo", "correspondenceId": same_name_id}])
        self.assertEqual((stale.exception.code, str(stale.exception)),
                         ("CONFLICT", "This correspondence is not the model service's latest change"))
        self.assertEqual(self.model_id(first), other_id)
        latest_id = self.shown_correspondence(service)["correspondenceId"]
        self.publish("undo-latest", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": latest_id}])
        # The same row asked for again is stale now; nothing further is deleted.
        with self.assertRaises(BoardError) as repeated:
            self.publish("undo-again", modelCorrespondenceChanges=[{
                **service, "action": "undo", "correspondenceId": latest_id}])
        self.assertEqual(repeated.exception.code, "CONFLICT")
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute(
                "SELECT COUNT(*) FROM model_correspondences WHERE harness=? AND provider=? AND model=?",
                tuple(service.values())).fetchone()[0], 1)
            # The only remaining row is not undoable either, with its own id.
            only_id = db.execute(
                "SELECT correspondence_id FROM model_correspondences WHERE harness=? AND provider=? AND model=?",
                tuple(service.values())).fetchone()[0]
        with self.assertRaises(BoardError) as sole:
            self.publish("undo-sole", modelCorrespondenceChanges=[{
                **service, "action": "undo", "correspondenceId": only_id}])
        self.assertEqual(sole.exception.code, "CONFLICT")

    def test_undo_of_a_generation_removes_the_model_nothing_references(self):
        first = BUDDY
        enable_fixture_buddy(self.board_.store, first)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        self.publish("generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Replacement generation",
        }])
        generated = self.model_id(first)
        self.assertNotEqual(generated, original)
        self.publish("undo", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())
        snapshot = console_snapshot_projection(self.board_.service)[0]
        self.assertNotIn(generated, {model["modelId"] for model in snapshot["models"]})

    def test_one_save_deletes_lost_models_after_all_changes_whatever_the_order(self):
        first = BUDDY
        other = {**first, "provider": "another-provider", "model": "another-name"}
        enable_fixture_buddy(self.board_.store, other)
        for order in (False, True):
            with self.subTest(map_first=order):
                fresh = {**first, "provider": f"order-{order}-provider"}
                enable_fixture_buddy(self.board_.store, fresh)
                fresh_service = {key: fresh[key] for key in ("harness", "provider", "model")}
                original = self.model_id(fresh)
                self.publish(f"generation-{order}", modelCorrespondenceChanges=[{
                    **fresh_service, "action": "generation", "displayName": f"Generation {order}",
                }])
                generated = self.model_id(fresh)
                undo = {**fresh_service, "action": "undo",
                        "correspondenceId": self.shown_correspondence(fresh_service)["correspondenceId"]}
                remap = {**{key: other[key] for key in ("harness", "provider", "model")},
                         "action": "map", "modelId": generated}
                changes = [remap, undo] if order else [undo, remap]
                self.publish(f"mixed-{order}", modelCorrespondenceChanges=changes)
                self.assertEqual(self.model_id(fresh), original)
                self.assertEqual(self.model_id(other), generated)
                with self.board_.store.db.read() as db:
                    # The map in the same save keeps the model, in either order.
                    self.assertIsNotNone(db.execute(
                        "SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())

    def test_one_save_applies_undo_and_note_changes_in_one_deletion_check(self):
        first = BUDDY
        enable_fixture_buddy(self.board_.store, first)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        self.publish("generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Noted generation",
        }])
        generated = self.model_id(first)
        # A note written in the same save as the undo keeps the model: the
        # deletion check runs only after every change of the save.
        self.publish("undo-with-note", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}],
            modelNoteChanges=[{"modelId": generated, "text": "Kept by the note"}])
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())
        # Remarking the generation and clearing the note in one save removes it.
        self.publish("remap", modelCorrespondenceChanges=[{**service, "action": "map", "modelId": generated}])
        self.publish("undo-with-cleared-note", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}],
            modelNoteChanges=[{"modelId": generated, "text": ""}])
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())

    def test_clearing_the_last_note_of_an_unreferenced_model_deletes_it(self):
        first = BUDDY
        enable_fixture_buddy(self.board_.store, first)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        self.publish("generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Noted generation",
        }])
        generated = self.model_id(first)
        self.publish("annotate", modelNoteChanges=[{"modelId": generated, "text": "Only reference left"}])
        self.publish("undo", modelCorrespondenceChanges=[{
            **service, "action": "undo", "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertIsNotNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())
        # A later save that only clears the note takes the last reference with it.
        self.publish("clear-note", modelNoteChanges=[{"modelId": generated, "text": ""}])
        with self.board_.store.db.read() as db:
            self.assertIsNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (generated,)).fetchone())
        snapshot = console_snapshot_projection(self.board_.service)[0]
        self.assertNotIn(generated, {model["modelId"] for model in snapshot["models"]})

    def test_undo_keeps_a_model_an_execution_or_statistics_still_references(self):
        first = BUDDY
        enable_fixture_buddy(self.board_.store, first)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        original = self.model_id(first)
        self.publish("generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Executed generation",
        }])
        executed = self.model_id(first)
        run = self.submit(self.board_, request_id="executed-generation", cwd=str(self.workdir("run")))
        claim = self.claim(self.board_, claim_request_id="claim-executed", run_id=run["runId"])
        self.finish_turn(self.board_, claim)
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": run["runId"]}))
        with self.board_.store.db.read() as db:
            self.assertEqual(db.execute("SELECT model_id FROM attempts WHERE task_id=?",
                                        (run["runId"],)).fetchone()[0], executed)
            self.assertEqual(db.execute("SELECT samples FROM kind_statistics WHERE model_id=?",
                                        (executed,)).fetchone()[0], 1)
            # The executed service stays among the model's names after the undo.
            self.assertIn(service, model_view(db, executed)["names"])

        self.publish("generation-again", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Counted generation",
        }])
        counted = self.model_id(first)
        with self.board_.store.db.write() as db:
            db.execute("INSERT INTO kind_statistics(harness,model_id,effort,samples,successes,first_passes,"
                       "rework_rounds,failures) VALUES(?,?,?,1,1,0,0,0)",
                       (first["harness"], counted, first["effort"]))

        for command, model in (("undo-executed", executed), ("undo-counted", counted)):
            with self.subTest(model=model):
                self.publish(command, modelCorrespondenceChanges=[{
                    **service, "action": "undo",
                    "correspondenceId": self.shown_correspondence(service)["correspondenceId"]}])
                with self.board_.store.db.read() as db:
                    self.assertIsNotNone(db.execute("SELECT 1 FROM models WHERE model_id=?", (model,)).fetchone())
        self.assertEqual(self.model_id(first), original)
        with self.board_.store.db.read() as db:
            self.assertEqual(model_view(db, executed)["names"], [service])

    def test_invalid_correspondence_patch_rolls_back_every_entry(self):
        first = BUDDY
        second = {**first, "provider": "second-provider", "model": "second-model"}
        for buddy in (first, second):
            enable_fixture_buddy(self.board_.store, buddy)
        service = {key: first[key] for key in ("harness", "provider", "model")}
        second_service = {key: second[key] for key in service}
        original = self.model_id(first)
        missing_id = "00000000-0000-0000-0000-000000000000"
        patches = [
            ([{**service, "provider": "missing-provider", "action": "map", "modelId": original}], "NOT_FOUND"),
            ([{**service, "action": "map", "modelId": missing_id}], "NOT_FOUND"),
            ([{**service, "action": "undo"}], "INVALID_ARGUMENT"),
            ([{**service, "action": "unexpected"}], "INVALID_ARGUMENT"),
            ([{**service, "action": "generation", "displayName": "New"},
              {**second_service, "action": "map", "modelId": missing_id}], "NOT_FOUND"),
            ([{**service, "action": "map", "modelId": original},
              {**service, "action": "undo"}], "INVALID_ARGUMENT"),
            ([{**service, "action": "map", "modelId": original},
              {**second_service, "action": "undo"}], "INVALID_ARGUMENT"),
        ]
        with self.board_.store.db.read() as db:
            before = tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                           for table in ("models", "model_correspondences"))
        for index, (patch, code) in enumerate(patches):
            with self.subTest(patch=patch), self.assertRaises(BoardError) as caught:
                self.publish(f"bad-correspondence-{index}", modelCorrespondenceChanges=patch)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(self.model_id(first), original)
        for invalid in ({"not": "a list"}, None, "map"):
            with self.subTest(invalid=invalid), self.assertRaises(BoardError) as caught:
                self.publish(f"bad-list-{type(invalid).__name__}", modelCorrespondenceChanges=invalid)
            self.assertEqual(caught.exception.code, "INVALID_ARGUMENT")
        with self.board_.store.db.read() as db:
            self.assertEqual(tuple(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                                   for table in ("models", "model_correspondences")), before)

    def test_admission_freezes_old_and_new_samples_and_late_cell(self):
        buddy = BUDDY
        service = {key: buddy[key] for key in ("harness", "provider", "model")}
        old = self.submit(self.board_, request_id="old-generation", cwd=str(self.workdir("old")))
        old_claim = self.claim(self.board_, claim_request_id="claim-old", run_id=old["runId"])
        old_id = self.model_id(buddy)
        self.publish("split-generation", modelCorrespondenceChanges=[{
            **service, "action": "generation", "displayName": "Replacement generation",
        }])
        new_id = self.model_id(buddy)
        self.assertNotEqual(old_id, new_id)
        self.finish_turn(self.board_, old_claim)
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": old["runId"]}))
        new = self.submit(self.board_, request_id="new-generation", cwd=str(self.workdir("old")))
        new_claim = self.claim(self.board_, claim_request_id="claim-new", run_id=new["runId"])
        self.finish_turn(self.board_, new_claim)
        self.accept_run(self.board_, self.board_.call("workflow_get", {"runId": new["runId"]}))

        with self.board_.store.db.write() as db:
            attempts = list(db.execute(
                "SELECT task_id,model_id FROM attempts WHERE task_id IN (?,?) ORDER BY task_id",
                (old["runId"], new["runId"]),
            ))
            self.assertEqual({row["task_id"]: row["model_id"] for row in attempts},
                             {old["runId"]: old_id, new["runId"]: new_id})
            overall = {row["model_id"]: row["samples"] for row in db.execute(
                "SELECT model_id,samples FROM kind_statistics WHERE harness=? AND effort=?",
                (buddy["harness"], buddy["effort"]),
            )}
            self.assertEqual(overall, {old_id: 1, new_id: 1})
            now = self.board_.store.now()
            buddy_id = ":".join(buddy[field] for field in ("harness", "provider", "model", "effort"))
            db.execute(
                "INSERT INTO maintenance_runs(maintenance_run_id,trigger_type,buddy_id,task_id,state,started_at,finished_at)"
                " VALUES('correspondence-classification','manual',?,?,'completed',?,?)",
                (buddy_id, old["runId"], now, now),
            )
            db.execute(
                "INSERT INTO domain_nodes(node_id,name,definition,parent_id,merged_into_id,maintenance_run_id)"
                " VALUES('correspondence-domain','Correspondence domain','Fixture domain',NULL,NULL,'correspondence-classification')"
            )
            db.execute(
                "INSERT INTO micro_task_classifications(run_id,domain_node_id,task_type,maintenance_run_id)"
                " VALUES(?,'correspondence-domain','debug','correspondence-classification')", (old["runId"],),
            )
            accumulate_cell(db, old["runId"], "correspondence-domain", "debug")
            # A later accepted correction still belongs to the delivery's frozen model.
            db.execute("INSERT INTO workflow_fixes(run_id,fixed_run_id,created_at) VALUES(?,?,?)",
                       (new["runId"], old["runId"], now))
            self.assertEqual(accumulate_defect(db, old["runId"]), [(buddy["harness"], old_id, buddy["effort"])])
            cells = list(db.execute("SELECT model_id,samples FROM kind_cell_statistics"))
            self.assertEqual([(row["model_id"], row["samples"]) for row in cells], [(old_id, 1)])
            self.assertEqual({row["model_id"]: row["defects"] for row in db.execute(
                "SELECT model_id,defects FROM kind_statistics")}, {old_id: 1, new_id: 0})
            self.assertEqual(db.execute("SELECT defects FROM kind_cell_statistics").fetchone()[0], 1)
            packet = build_packet(db)
            self.assertEqual({turn["kind"]["modelId"] for task in packet["microTasks"]
                              for turn in task["result"]["turns"] if turn["kind"]}, {old_id, new_id})
            current = domain_statistics(db, [{"buddy_id": "current", **buddy}])["current"]
            self.assertEqual(current["overall"]["samples"], 1)
            self.assertEqual(current["byDomain"], {})
