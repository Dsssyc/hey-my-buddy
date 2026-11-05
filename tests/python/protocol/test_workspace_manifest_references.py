"""Frozen references must read the exact private file they name."""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import workspace_manifest as references
from hey_my_buddy.protocol.workspace_manifest import (
    artifact_manifest, manifest_ref, project_id, record_reference, resolve_manifest, resolve_record, turn_manifest,
)
from hey_my_buddy.protocol.turn_input import folder_changes_notice


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


class WorkspaceManifestReferences(unittest.TestCase):
    def test_none_turn_uses_explicit_context_without_manifest_or_git(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            workspace = {"kind": "none", "path": str(state / "ordinary-folder"),
                         "projectId": "folder-project", "projectKind": "folder",
                         "access": "write", "integrator": {"role": "host"}}
            self.assertEqual(turn_manifest(state, {"version": 5, "executionWorkspace": workspace}), workspace)
            self.assertEqual(project_id(workspace), "folder-project")
            self.assertEqual(folder_changes_notice(workspace), "文件夹的改动未封存")
            self.assertIsNone(folder_changes_notice({**workspace, "access": "read"}))
            self.assertEqual(folder_changes_notice({**workspace, "access": "full", "accessNote": "needed"}),
                             "文件夹的改动未封存")
            for invalid in ({**workspace, "workspaceId": "ws-fake"},
                            {**workspace, "path": "relative"},
                            {**workspace, "access": "full"},
                            {**workspace, "access": "read", "accessNote": "unused"}):
                with self.subTest(workspace=invalid), self.assertRaises(BoardError) as caught:
                    turn_manifest(state, {"version": 5, "executionWorkspace": invalid})
                self.assertEqual(caught.exception.code, "TURN_INVALID")

    def test_current_reference_rejects_changed_file_and_archived_turn_keeps_its_body(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            workspace_id = "ws-" + "a" * 32
            directory = state / "workspaces" / workspace_id
            directory.mkdir(parents=True)
            body = {"version": 4, "workspaceId": workspace_id, "projectId": "project", "path": "/example"}
            manifest = {**body, "manifestSha256": hashlib.sha256(_bytes(body)).hexdigest()}
            (directory / "manifest.json").write_bytes(_bytes(manifest))
            reference = manifest_ref(manifest)
            self.assertEqual(resolve_manifest(state, reference), manifest)
            self.assertEqual(turn_manifest(state, {"version": 4, "executionWorkspace": reference}), manifest)
            with self.assertRaises(BoardError) as caught:
                turn_manifest(state, {"version": 4.0, "executionWorkspace": reference})
            self.assertEqual(caught.exception.code, "TURN_INVALID")
            archived = {"version": 3, "executionWorkspace": {"version": 3, "repositoryId": "historical"}}
            self.assertEqual(turn_manifest(state, archived), archived["executionWorkspace"])
            (directory / "manifest.json").write_bytes(_bytes({**manifest, "path": "/changed"}))
            with self.assertRaises(BoardError) as caught:
                resolve_manifest(state, reference)
            self.assertEqual(caught.exception.code, "WORKSPACE_MANIFEST_CHANGED")

    def test_record_reference_binds_existing_bytes_and_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            state = Path(temporary)
            record = {"version": 1, "workspaceId": "ws-" + "b" * 32,
                      "taskId": "task", "attemptId": "attempt", "snapshotSha256": "c" * 64}
            output_id = hashlib.sha256(_bytes(["task", "attempt"])).hexdigest()
            directory = state / "workspaces" / record["workspaceId"] / "outputs" / output_id
            directory.mkdir(parents=True)
            file = directory / "output.json"
            file.write_bytes(_bytes(record))
            reference = record_reference(state, {**record, "cumulativePatch": {"path": "derived"}})
            self.assertFalse(Path(reference["recordFile"]["path"]).is_absolute())
            with patch.object(references, "read_bytes", wraps=references.read_bytes) as reader:
                self.assertEqual(resolve_record(state, reference), record)
            self.assertEqual(reader.call_count, 1, "Reading one reference must not reopen its record")
            for malformed in ({}, {"manifestSha256": "not-a-hash"},
                              {"recordFile": {}, "snapshotSha256": record["snapshotSha256"]},
                              {**reference, "recordFile": {**reference["recordFile"], "path": str(file)}}):
                with self.subTest(reference=malformed), self.assertRaises(BoardError) as caught:
                    resolve_record(state, malformed)
                self.assertEqual(caught.exception.code, "ARTIFACT_MISMATCH")
            row = {"content_form": "record-reference", "record_path": reference["recordFile"]["path"],
                   "record_sha256": reference["recordFile"]["sha256"],
                   "output_snapshot_sha256": record["snapshotSha256"]}
            self.assertEqual(artifact_manifest(state, row), record)
            file.write_bytes(_bytes({**record, "taskId": "other"}))
            with self.assertRaises(BoardError) as caught:
                resolve_record(state, reference)
            self.assertEqual(caught.exception.code, "ARTIFACT_MISMATCH")
