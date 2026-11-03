"""Large workspace receipts remain complete in SQLite and readable over C-Two."""
from __future__ import annotations

import json

from admission import admit_execution
from support import BoardTestCase

from hey_my_buddy.protocol.transport import MAX_MESSAGE_BYTES


class ResultViewTests(BoardTestCase):
    def test_current_result_exposes_record_identity_without_input_manifest(self):
        from hey_my_buddy.blackboard.store.store import _public_result_payload

        seal = {"recordFile": {"path": "workspaces/ws-fixture/outputs/output.json", "sha256": "a" * 64},
                "snapshotSha256": "b" * 64}
        payload = {"status": "ok", "result": {"workspaceSeal": seal}}
        with self.assertRaises(TypeError):
            _public_result_payload(payload, self.directory)
        projected = _public_result_payload(payload, self.directory, "current-report-v2")
        self.assertEqual(projected["result"]["workspaceSeal"]["recordFile"],
                         {"path": str(self.directory / seal["recordFile"]["path"]), "sha256": "a" * 64})
        self.assertEqual(payload["result"]["workspaceSeal"], seal)
        self.assertNotIn("workspaceManifest", projected["result"])
        self.assertNotIn("workspaceManifestFile", projected["result"])

    def test_result_views_preserve_shared_and_separate_patch_paths(self):
        from hey_my_buddy.blackboard.store.store import _public_result_payload

        for key in ("workspaceSeal", "partialWorkspaceSeal"):
            for path in ("/private/output.patch", "/private/cumulative.patch"):
                with self.subTest(seal=key, path=path):
                    seal = {"diffPath": "/private/output.patch", "diffSha256": "digest",
                            "changedPaths": ["src/file.py"],
                            "cumulativePatch": {"path": path, "sha256": "digest", "changedPaths": ["src/file.py"]}}
                    payload = {"status": "ok", "result": {key: seal}}
                    view = _public_result_payload(payload, self.directory, "archived-report-v1")["result"][key]
                    self.assertEqual(view["diffPath"], seal["diffPath"])
                    self.assertEqual(view["diffSha256"], seal["diffSha256"])
                    self.assertEqual(view["cumulativePatch"]["path"], path)
                    self.assertEqual(view["cumulativePatch"]["sha256"], "digest")
                    self.assertNotIn("changedPathsCount", seal)

    def test_large_workspace_seal_is_bounded_in_all_public_result_replies(self):
        board = self.board()
        task = board.submit_execution(requestId="large-workspace-result", task="collect workspace output",
                                      cwd=str(self.workdir()), adapter="command", argv=["true"])["task"]
        attempt = admit_execution(board.client(), "n" * 32, task_id=task["runId"])["claim"]["attempt"]
        paths = [f"src/{index:04d}-" + "a" * 2990 for index in range(3000)]
        seal = {
            "changedPaths": paths, "includedUntracked": paths,
            "snapshot": {"changedPaths": paths, "excludedChangedPaths": paths},
            "cumulativePatch": {"path": "/private/cumulative.patch", "changedPaths": paths},
            "diffPath": "/private/output.patch", "snapshotSha256": "snapshot-digest",
        }
        report = {"status": "ok", "shutdownConfirmed": True, "result": {
            "workspaceSeal": seal,
            "turn": {"result": {"workspaceSeal": {"changedPaths": ["opaque harness content"]}}},
        }}
        self.assertGreater(len(json.dumps(report).encode()), MAX_MESSAGE_BYTES)

        # The fixture supplies stop proof after receipt reception.
        acknowledged = board.call("execution_result", {
            "attemptId": attempt["attemptId"], "generation": attempt["generation"],
            "nonce": "n" * 32, **report,
        })
        status = board.call("task_get", {"runId": task["runId"]})
        result = board.call("task_result", {"runId": task["runId"]})
        with board.store.db.read() as connection:
            stored = json.loads(connection.execute(
                "SELECT result_json FROM attempts WHERE attempt_id=?", (attempt["attemptId"],)
            ).fetchone()[0])
        self.assertEqual(stored["result"]["workspaceSeal"], seal)

        views = (
            ("acknowledgement", acknowledged, acknowledged["attempt"]["result"]["result"]),
            ("acknowledgement task", acknowledged, acknowledged["task"]["selectedAttempt"]["result"]["result"]),
            ("task_get", status, status["task"]["selectedAttempt"]["result"]["result"]),
            ("task_result", result, result["selectedAttempt"]["result"]["result"]),
            ("task_result body", result, result["result"]),
        )
        for name, reply, projected in views:
            with self.subTest(reply=name):
                self.assertLess(len(json.dumps(reply).encode()), MAX_MESSAGE_BYTES)
                workspace = projected["workspaceSeal"]
                self.assertEqual(workspace["changedPathsCount"], len(paths))
                self.assertTrue(workspace["changedPathsTruncated"])
                self.assertEqual(len(workspace["changedPaths"]), 32)
                self.assertEqual(workspace["changedPaths"][0], paths[0][:500])
                self.assertEqual(workspace["snapshot"]["excludedChangedPathsCount"], len(paths))
                self.assertEqual(workspace["cumulativePatch"]["changedPathsCount"], len(paths))
                self.assertEqual(workspace["diffPath"], seal["diffPath"])
                self.assertEqual(workspace["cumulativePatch"]["path"], seal["cumulativePatch"]["path"])
                self.assertEqual(projected["turn"], report["result"]["turn"])
        self.assertEqual(result["result"], result["selectedAttempt"]["result"]["result"])

    def test_partial_workspace_manifest_has_bounded_path_facts(self):
        from hey_my_buddy.blackboard.store.store import _public_result_payload

        paths = ["目录/" + "界" * 1500] * 40
        manifest = {"workspaceId": "ws-fixture", "writeScope": paths, "snapshot": {
            "includeSelectors": paths, "executionSelectors": paths,
            "includedUntracked": paths, "excludedUntracked": paths,
            "excludedEntries": {f"src/{index:04d}-" + "a" * 800: "digest" for index in range(40)},
        }}
        envelope = {"status": "failed", "result": {"partialWorkspaceSeal": {
            "changedPaths": paths, "snapshot": {"includedUntracked": paths},
            "diffPath": "/private/partial.patch",
        }, "workspaceManifest": manifest}}
        view = _public_result_payload(envelope, self.directory, "archived-report-v1")["result"]
        self.assertEqual(envelope["result"]["workspaceManifest"], manifest)
        self.assertEqual(view["partialWorkspaceSeal"]["changedPathsCount"], 40)
        self.assertEqual(view["workspaceManifest"]["writeScopeCount"], 40)
        self.assertEqual(view["workspaceManifest"]["snapshot"]["excludedEntriesCount"], 40)
        self.assertEqual(len(view["workspaceManifest"]["snapshot"]["excludedEntries"]), 32)
        self.assertLessEqual(len(view["partialWorkspaceSeal"]["changedPaths"][0].encode()), 500)
        self.assertLessEqual(len(view["workspaceManifest"]["snapshot"]["excludedEntries"][0].encode()), 500)

    def test_workspace_error_keeps_complete_evidence_and_bounds_public_paths(self):
        from hey_my_buddy.blackboard.store.store import _public_result_payload

        paths = ["src/" + "a" * 2990] * 3000
        evidence = {"blockingPaths": paths, "changedPaths": paths, "writeScope": paths,
                    "entries": [{"path": path, "changed": True} for path in paths]}
        error = {"code": "WORKSPACE_SCOPE_VIOLATION", "message": "outside scope",
                 "details": {"paths": paths, "changedPaths": paths, "evidence": evidence}}
        payload = {"status": "failed", "result": {"partialSealError": error}}
        view = _public_result_payload(payload, self.directory, "archived-report-v1")
        self.assertEqual(payload["result"]["partialSealError"]["details"]["evidence"], evidence)
        details = view["result"]["partialSealError"]["details"]
        self.assertEqual(details["pathsCount"], len(paths))
        self.assertEqual(details["evidence"]["entriesCount"], len(paths))
        self.assertEqual(details["evidence"]["writeScopeCount"], len(paths))
        self.assertEqual(len(details["evidence"]["entries"]), 32)
        self.assertLess(len(json.dumps(view).encode()), MAX_MESSAGE_BYTES)

