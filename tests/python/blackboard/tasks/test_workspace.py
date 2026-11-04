"""Workspace boundaries tested against disposable real Git repositories."""
import os
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import unittest
import tempfile
import uuid
from unittest.mock import patch

from hey_my_buddy.errors import BoardError
from hey_my_buddy.blackboard.tasks import workspace, macro_task_deletion_workspace
from hey_my_buddy.blackboard.store.store import BoardStore
from hey_my_buddy.blackboard.tasks.workspace_bindings import MANIFEST_OWNERS_CTE
from hey_my_buddy.paths import native_path


def deletion_sql_inputs(case, manifests, artifacts=()):
    """Publish prepared test workspaces as current SQL owner facts for physical deletion."""
    if not hasattr(case, '_deletion_store'):
        case._deletion_store = BoardStore(case.state)
        case._deletion_store.initialize()
        case.addCleanup(case._deletion_store.db.close)
    store = case._deletion_store
    manifests = list(manifests)
    artifacts = list(artifacts)
    now = store.now()
    with store.db.write() as connection:
        for manifest in manifests:
            identifier = manifest['workspaceId']
            request_id = case._workspace_request_ids[identifier]
            attached = next((row for row in artifacts if row.get('workspaceId') == identifier), None)
            run_id = attached['taskId'] if attached is not None else 'fixture-' + identifier
            project_id = workspace.manifest_project_id(manifest)
            connection.execute(
                "INSERT OR IGNORE INTO tasks(task_id,request_id,spec_json,input_fingerprint,fingerprint_version,"
                "adapter,cwd,state,revision,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, request_id, '{"expectedSeconds":1}', 'fixture', 1,
                 'codex', manifest['checkoutRoot'], 'cancelled', 1, now, now))
            connection.execute(
                "INSERT OR IGNORE INTO workflow_runs(run_id,standalone_project_id,control_verifier,"
                "execution_workspace_json,workspace_id,workspace_manifest_sha256,state,created_at,updated_at)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (run_id, project_id, 'fixture', '{}', identifier, manifest['manifestSha256'],
                 'cancelled', now, now))
            connection.execute(
                "INSERT OR IGNORE INTO workflow_artifacts(artifact_id,run_id,kind,content_form,workspace_id,"
                "input_manifest_sha256,workspace_facts_role,workspace_kind,checkout_root,repository_path,"
                "input_commit,staged_commit,created_at) VALUES(?,?,'input','input-reference',?,?,?,?,?,?,?,?,?)",
                ('fixture-input-' + identifier + '-' + manifest['manifestSha256'], run_id, identifier,
                 manifest['manifestSha256'], 'owner', manifest['kind'], manifest['checkoutRoot'],
                 manifest['snapshot']['repositoryPath'], manifest['inputCommit'],
                 manifest['snapshot']['stagedCommit'], now))
        for artifact in artifacts:
            kind = {'host-output': 'partial-output', 'resolution': 'resolved-output',
                    'abandoned-site': 'abandoned-site', None: 'output'}[artifact.get('kind')]
            origin = ('host' if artifact.get('kind') == 'host-output' else 'worker') if kind in ('output','partial-output') else None
            artifact_id = 'fixture-output-' + uuid.uuid5(uuid.NAMESPACE_URL,
                artifact['ref'] + artifact['snapshotSha256']).hex
            connection.execute(
                "INSERT OR IGNORE INTO workflow_artifacts(artifact_id,run_id,attempt_id,kind,content_form,"
                "manifest_json,output_snapshot_sha256,output_origin,output_workspace_id,output_manifest_sha256,"
                "output_commit,output_action,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (artifact_id, artifact['taskId'], artifact['attemptId'], kind, 'sealed-inline-v1',
                 json.dumps(artifact), artifact['snapshotSha256'], origin, artifact['workspaceId'],
                 artifact['manifestSha256'], artifact['commit'],
                 artifact.get('action') if kind == 'resolved-output' else None, now))
    pairs = {(row['workspaceId'], row['manifestSha256']) for row in manifests}
    with store.db.read() as connection:
        owners = [dict(row) for row in connection.execute(
            f'WITH manifest_owners AS ({MANIFEST_OWNERS_CTE})'
            ' SELECT o.*,r.standalone_project_id AS project_id FROM manifest_owners o'
            ' JOIN workflow_runs r ON r.run_id=o.owner_run_id')
            if (row['workspace_id'], row['manifest_sha256']) in pairs]
        records = [dict(row) for row in connection.execute(
            "SELECT * FROM workflow_artifacts WHERE kind IN ('output','resolved-output','partial-output','abandoned-site')")
            if (row['output_workspace_id'], row['output_manifest_sha256']) in pairs]
    return store, owners, records


class WorkspaceTests(unittest.TestCase):
    def test_relative_gitfile_targets_are_resolved_before_native_io(self):
        checkout = self.root / "relative-gitfile"
        checkout.mkdir()
        pointer = checkout / ".git"
        real_native = workspace.native_path
        def canonical_native(path):
            self.assertTrue(Path(path).is_absolute())
            self.assertNotIn("..", Path(path).parts)
            return real_native(path)
        failure = BoardError("WORKSPACE_GIT_ERROR", "Git failed", returncode=128, stderr="opaque failure")
        for target, present in (("../repo/.git", True), ("../missing-repository", False)):
            with self.subTest(target=target):
                pointer.write_text("gitdir: " + target + "\n", encoding="utf-8")
                command, env = workspace._git_command(checkout, "rev-parse", "--absolute-git-dir")
                observed = subprocess.run(command, env=env, capture_output=True)
                self.assertEqual(observed.returncode, 0 if present else 128)
                expected = str(self.repo / ".git") if present else None
                with patch.object(workspace, "native_path", side_effect=canonical_native):
                    self.assertEqual(workspace.repository_at(checkout), expected)
                    with patch.object(workspace, "_line", side_effect=failure):
                        if present:
                            with self.assertRaises(BoardError) as caught:
                                workspace.repository_at(checkout)
                            self.assertIs(caught.exception, failure)
                        else:
                            self.assertIsNone(workspace.repository_at(checkout))

    @unittest.skipIf(os.name == "nt", "POSIX symlink traversal; relative target test runs on every OS")
    def test_gitfile_resolution_follows_links_before_parent_components(self):
        checkout = self.root / "linked-gitfile"
        checkout.mkdir()
        outside = self.root / "outside"
        (outside / "nested").mkdir(parents=True)
        (checkout / "link").symlink_to(outside / "nested", target_is_directory=True)
        self.git("init", "--bare", "-q", str(outside / "metadata"))
        self.git("init", "--bare", "-q", str(checkout / "missing"))
        (checkout / "metadata").mkdir()
        failure = BoardError("WORKSPACE_GIT_ERROR", "Git failed", returncode=128, stderr="opaque failure")
        for target, present in (("metadata", True), ("missing", False)):
            with self.subTest(target=target):
                (checkout / ".git").write_text(f"gitdir: link/../{target}\n", encoding="utf-8")
                expected = str(outside / target) if present else None
                self.assertEqual(workspace.repository_at(checkout), expected)
                with patch.object(workspace, "_line", side_effect=failure):
                    if present:
                        with self.assertRaises(BoardError) as caught:
                            workspace.repository_at(checkout)
                        self.assertIs(caught.exception, failure)
                    else:
                        self.assertIsNone(workspace.repository_at(checkout))

    def test_parent_repository_proof_skips_empty_git_directories(self):
        child = self.repo / "parent-search" / "child"
        child.mkdir(parents=True)
        failure = BoardError("WORKSPACE_GIT_ERROR", "Git failed", returncode=128, stderr="opaque failure")
        for empty_git in (False, True):
            with self.subTest(empty_git=empty_git):
                if empty_git:
                    (child / ".git").mkdir()
                self.assertEqual(workspace.repository_at(child, allow_parent=True), str(self.repo / ".git"))
                with patch.object(workspace, "_line", side_effect=failure):
                    self.assertIsNone(workspace.repository_at(child, allow_parent=False))
                    with self.assertRaises(BoardError) as caught:
                        workspace.repository_at(child, allow_parent=True)
                    self.assertIs(caught.exception, failure)

    def test_repository_absence_uses_gitfile_target_not_git_diagnostic(self):
        checkout = self.root / "gitfile-checkout"
        checkout.mkdir()
        pointer = checkout / ".git"
        for target in ("../missing-repository", str(self.root / "missing-repository")):
            pointer.write_text("gitdir: " + target + "\n", encoding="utf-8")
            for diagnostic in ("opaque fixture failure", "fatal: gitfile does not point to a valid repository"):
                with self.subTest(target=target, diagnostic=diagnostic):
                    failure = BoardError("WORKSPACE_GIT_ERROR", "Git failed", returncode=128, stderr=diagnostic)
                    with patch.object(workspace, "_line", side_effect=failure):
                        self.assertIsNone(workspace.repository_at(checkout))
        # A readable, live target cannot turn another Git failure into absence.
        pointer.write_text("gitdir: " + str(self.repo / ".git") + "\n", encoding="utf-8")
        with patch.object(workspace, "_line", side_effect=failure):
            with self.assertRaises(BoardError) as caught:
                workspace.repository_at(checkout)
        self.assertIs(caught.exception, failure)
        for malformed in ("gitdir: \n", "not a gitfile\n"):
            pointer.write_text(malformed, encoding="utf-8")
            with self.subTest(malformed=malformed), patch.object(workspace, "_line", side_effect=failure):
                with self.assertRaises(BoardError) as caught:
                    workspace.repository_at(checkout)
            self.assertIs(caught.exception, failure)
        plain = self.root / "plain-with-head-file"
        plain.mkdir()
        (plain / "HEAD").write_text("an ordinary user file\n", encoding="utf-8")
        with patch.object(workspace, "_line", side_effect=failure):
            self.assertIsNone(workspace.repository_at(plain))
            with self.assertRaises(BoardError) as caught:
                workspace.repository_at(self.repo / ".git")
        self.assertIs(caught.exception, failure)

    def test_repository_absence_never_hides_an_unreadable_gitfile_target(self):
        checkout = self.root / "unreadable-gitfile-checkout"
        checkout.mkdir()
        target = self.root / "unreadable-repository"
        target.mkdir()
        (checkout / ".git").write_text("gitdir: " + str(target) + "\n", encoding="utf-8")
        real_listdir = os.listdir
        def listing(path):
            if Path(path) == native_path(target):
                raise PermissionError("fixture cannot read Git directory")
            return real_listdir(path)
        failure = BoardError("WORKSPACE_GIT_ERROR", "Git failed", returncode=128, stderr="opaque fixture failure")
        with patch.object(workspace, "_line", side_effect=failure), patch.object(os, "listdir", side_effect=listing):
            with self.assertRaises(BoardError) as caught:
                workspace.repository_at(checkout)
        self.assertEqual(caught.exception.code, "WORKSPACE_IO_ERROR")
        self.assertIsInstance(caught.exception.__cause__, PermissionError)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-workspace-repository-")
        cls.addClassCleanup(temporary.cleanup)
        fixture = cls(methodName="runTest")
        fixture.repo = Path(temporary.name) / "repo"
        fixture.repo.mkdir()
        fixture._seed_repository()
        cls._repository_template = fixture.repo

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hey-my-buddy-workspace-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        template = getattr(self, "_repository_template", None)
        if template is None:
            # Other TestCases borrow setUp without inheriting our class fixture
            # or _seed_repository method; preserve their original Git setup.
            WorkspaceTests._seed_repository(self)
        else:
            # copytree creates distinct ordinary files and Git administration;
            # refresh the copied index against this repository's new inodes.
            shutil.copytree(template, self.repo, dirs_exist_ok=True)
            self.git("update-index", "--refresh")
            self.base = self.git("rev-parse", "HEAD").decode().strip()

    def _seed_repository(self):
        self.git("init", "-q")
        self.git("config", "user.name", "Workspace Test")
        self.git("config", "user.email", "workspace@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "docs").mkdir()
        (self.repo / "src/file.txt").write_text("base\n", encoding="utf-8", newline="\n")
        (self.repo / "docs/readme.md").write_text("base docs\n", encoding="utf-8", newline="\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").decode().strip()

    def git(self, *args, cwd=None, data=None):
        command, git_env = workspace._git_command(cwd or self.repo, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=git_env, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def assert_error(self, code, operation, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            operation(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.payload())
        return caught.exception

    def intent(self, **updates):
        return {"kind": "worktree", "cwd": str(self.repo), "access": "write", "base": {"kind": "working-tree"},
                "writeScope": ["src"], "integrator": {"role": "host", "id": "host-1"}, **updates}

    def prepare(self, request="request", **updates):
        manifest = workspace.prepare(self.state, request, self.intent(**updates))
        self.__dict__.setdefault('_workspace_request_ids', {})[manifest['workspaceId']] = request
        return manifest

    @staticmethod
    def legacy_snapshot(manifest):
        snapshot = {key: value for key, value in manifest["snapshot"].items() if key != "executionSelection"}
        snapshot.update(executionSelectors=workspace.execution_selection(manifest).get("selectors", [])
                        if workspace.execution_selection(manifest) else [],
                        includeSelectors=[], includedUntracked=workspace.input_untracked_paths(manifest),
                        excludedUntracked=sorted(snapshot["excludedEntries"]))
        return snapshot

    def legacy_full_manifest(self, manifest):
        legacy = dict(manifest, version=2, accessReason="Original Host explanation", access="full", snapshot=self.legacy_snapshot(manifest))
        legacy["repositoryId"] = legacy.pop("projectId")
        legacy["checkoutId"] = "0" * 64
        legacy["manifestSha256"] = workspace._sha(workspace._json({
            key: value for key, value in legacy.items() if key != "manifestSha256"}))
        directory = self.state / "workspaces" / legacy["workspaceId"]
        (directory / "manifest.json").write_bytes(workspace._json(legacy))
        request_path = directory / "request.json"
        request = json.loads(request_path.read_bytes())
        request["version"] = 2
        request["intent"]["access"] = "full"
        request["intent"].pop("accessNote", None)
        request["intent"]["accessReason"] = legacy["accessReason"]
        request_path.write_bytes(workspace._json(request))
        return legacy

    def test_integrator_is_a_structured_identity_for_new_workspaces(self):
        for invalid in ("host:host-1", {}, {"role": "host"}, {"role": "worker", "id": "worker-1"},
                        {"role": "lead", "id": " "}, {"role": "host", "id": "host-1", "extra": True}):
            with self.subTest(integrator=invalid):
                self.assert_error("INVALID_WORKSPACE", workspace._intent, self.intent(integrator=invalid))
        manifest = self.prepare(integrator={"role": "lead", "id": "lead-1"})
        self.assertEqual(manifest["version"], 5)
        self.assertEqual(manifest["integrator"], {"role": "lead", "id": "lead-1"})
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])

    def test_historical_hashed_manifest_remains_readable(self):
        manifest = self.prepare()
        historical = dict(manifest, version=1, integrator="host:host-1", snapshot=self.legacy_snapshot(manifest))
        historical["repositoryId"] = historical.pop("projectId")
        historical["checkoutId"] = "0" * 64
        historical["manifestSha256"] = hashlib.sha256(workspace._json({
            key: value for key, value in historical.items() if key != "manifestSha256"
        })).hexdigest()
        workspace._validate_manifest(historical)
        self.assert_error("WORKSPACE_MANIFEST_CHANGED", workspace._validate_manifest,
                          dict(historical, integrator="host:someone-else"))
        invalid_current = dict(historical, version=2)
        invalid_current["manifestSha256"] = hashlib.sha256(workspace._json({
            key: value for key, value in invalid_current.items() if key != "manifestSha256"
        })).hexdigest()
        self.assert_error("INVALID_WORKSPACE", workspace._validate_manifest, invalid_current)

    def test_manifest_versions_one_through_four_keep_their_frozen_selector_form(self):
        current = self.prepare(kind="existing")
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                legacy = dict(current, version=version, snapshot=self.legacy_snapshot(current))
                if version < 4:
                    legacy["repositoryId"] = legacy.pop("projectId")
                    legacy["checkoutId"] = "0" * 64
                if version == 1:
                    legacy["integrator"] = "host:host-1"
                legacy["manifestSha256"] = workspace._sha(workspace._json({
                    key: value for key, value in legacy.items() if key != "manifestSha256"}))
                frozen = workspace._json(legacy)
                workspace._validate_manifest(legacy)
                self.assertTrue(workspace.verify(legacy, state_dir=self.state)["unchanged"])
                self.assertEqual(workspace.execution_selection(legacy), {"kind": "selectors", "selectors": []})
                self.assertEqual(workspace._json(legacy), frozen)

    def test_new_worktree_materializes_inputs_without_an_execution_selection(self):
        (self.repo / "source-input.txt").write_text("frozen source\n", encoding="utf-8")
        manifest = self.prepare(includeUntracked=["source-input.txt"])
        self.assertEqual(manifest["version"], 5)
        self.assertNotIn("executionSelection", manifest["snapshot"])
        self.assertNotIn("executionSelectors", manifest["snapshot"])
        checkout = Path(manifest["checkoutRoot"])
        self.assertEqual((checkout / "source-input.txt").read_text(), "frozen source\n")
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])

    def test_full_manifest_version_keeps_legacy_reason_only_in_historical_artifacts(self):
        current = self.prepare(access="full", accessNote="Host approved this access")
        self.assertEqual(current["version"], 5)
        self.assertEqual(current["access"], "full")
        self.assertNotIn("accessNote", current)
        self.assertNotIn("accessReason", current)
        self.assertTrue(workspace.verify(current, state_dir=self.state)["unchanged"])
        legacy = self.legacy_full_manifest(current)
        self.assertTrue(workspace.verify(legacy, state_dir=self.state)["unchanged"])
        first = dict(legacy, version=1, integrator="host:host-1")
        first["manifestSha256"] = workspace._sha(workspace._json({
            key: value for key, value in first.items() if key != "manifestSha256"}))
        self.assertTrue(workspace.verify(first, state_dir=self.state)["unchanged"])
        self.assert_error("INVALID_WORKSPACE", workspace._validate_manifest,
                          dict(legacy, accessNote="renamed"))
        self.assert_error("INVALID_WORKSPACE", workspace._validate_manifest,
                          dict(current, accessReason="not in v5"))

    def test_old_full_manifest_allocation_can_be_deleted(self):
        current = self.prepare(access="full", accessNote="Host approved this access")
        legacy = self.legacy_full_manifest(current)
        checkout = Path(legacy["checkoutRoot"])
        self.assertTrue(checkout.is_dir())
        store, owners, artifacts = deletion_sql_inputs(self, [legacy])
        self.assertEqual(macro_task_deletion_workspace.delete_workspaces(store, owners, artifacts), [])
        self.assertFalse(checkout.exists())

    def test_v2_request_replays_without_changing_its_disk_records(self):
        current = self.prepare()
        directory = self.state / "workspaces" / current["workspaceId"]
        old = dict(current, version=2, snapshot=self.legacy_snapshot(current))
        old["repositoryId"] = old.pop("projectId")
        old["checkoutId"] = "0" * 64
        old["manifestSha256"] = workspace._sha(workspace._json({
            key: value for key, value in old.items() if key != "manifestSha256"}))
        (directory / "manifest.json").write_bytes(workspace._json(old))
        request_path = directory / "request.json"
        request = json.loads(request_path.read_bytes())
        request["version"] = 2
        request_path.write_bytes(workspace._json(request))
        before = {name: (directory / name).read_bytes() for name in ("request.json", "manifest.json")}
        self.assertEqual(self.prepare(), old)
        self.assertEqual({name: (directory / name).read_bytes() for name in before}, before)

    def test_incomplete_v4_request_cannot_create_a_v5_manifest(self):
        current = self.prepare()
        directory = self.state / "workspaces" / current["workspaceId"]
        (directory / "manifest.json").unlink()
        request_path = directory / "request.json"
        request = json.loads(request_path.read_bytes())
        request["version"] = 4
        request_path.write_bytes(workspace._json(request))
        input_path = directory / "input.json"
        pinned = json.loads(input_path.read_bytes())
        input_path.write_bytes(workspace._json(pinned))
        before = request_path.read_bytes()
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        self.assertEqual(request_path.read_bytes(), before)
        self.assertFalse((directory / "manifest.json").exists())

    def test_borrowed_setup_without_a_template_preserves_router_input_capture(self):
        from blackboard.routing.test_router import RouterInputTests

        for template in ("absent", None):
            with self.subTest(template=template):
                borrower = RouterInputTests("test_existing_input_is_materialized_without_live_or_ignored_files")
                self.assertFalse(hasattr(borrower, "_repository_template"))
                self.assertFalse(hasattr(borrower, "_seed_repository"))
                if template is None:
                    borrower._repository_template = None
                result = unittest.TestResult()
                borrower.run(result)
                self.assertEqual(result.testsRun, 1)
                self.assertTrue(result.wasSuccessful(), result.errors + result.failures)

    def test_identity_unifies_siblings_and_distinguishes_linked_checkout(self):
        manifest = self.prepare()
        left = workspace.inspect(str(self.repo / "src"))
        right = workspace.inspect(str(self.repo / "docs"))
        linked = self.root / "linked"
        self.git("worktree", "add", "--detach", str(linked), self.base)
        other = workspace.inspect(str(linked / "src"))
        self.assertEqual(left["checkoutRoot"], right["checkoutRoot"])
        self.assertNotEqual(left["checkoutRoot"], other["checkoutRoot"])
        self.assertEqual(left["projectId"], other["projectId"])
        self.assertEqual(left["projectId"], manifest["projectId"])
        self.assertEqual(left["checkoutRoot"], str(self.repo))
        self.assertEqual(left["path"], str(self.repo / "src"))

    def test_non_git_and_unborn_checkout_are_explicit_errors(self):
        non_git = self.root / "non-git"
        non_git.mkdir()
        # TMPDIR may live inside a checkout. An invalid local Git pointer stops
        # discovery from borrowing that ancestor as this fixture's repository.
        (non_git / ".git").write_text("gitdir: missing-private-repository\n", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_UNSUPPORTED", workspace.inspect, str(non_git))
        unborn = self.root / "unborn"
        unborn.mkdir()
        self.git("init", "-q", cwd=unborn)
        self.assert_error("WORKSPACE_REF_INVALID", workspace.inspect, str(unborn))

    def test_dirty_input_preserves_staging_binary_modes_symlinks_and_source(self):
        # Exercise native link checkout even where Git defaults to placeholders.
        self.git("config", "core.symlinks", "true")
        source = self.repo / "src/file.txt"
        source.write_text("staged\n", encoding="utf-8", newline="\n")
        self.git("add", "src/file.txt")
        source.write_bytes(b"unstaged\x00binary\xff\n")
        source.chmod(0o755)
        selected = self.repo / "src/selected.bin"
        selected.write_bytes(b"selected\x00\xfe")
        (self.repo / "src/excluded.txt").write_text("must not be copied", encoding="utf-8", newline="\n")
        outside = self.root / "outside-secret"
        outside.write_text("do not follow", encoding="utf-8", newline="\n")
        (self.repo / "src/link").symlink_to(outside)
        original_index = (self.repo / ".git/index").read_bytes()
        original_status = self.git("status", "--porcelain=v1", "-z")
        manifest = self.prepare(includeUntracked=["src/selected.bin", "src/link"])
        target = Path(manifest["checkoutRoot"])
        self.assertEqual((target / "src/file.txt").read_bytes(), source.read_bytes())
        input_mode = self.git("ls-tree", manifest["inputCommit"], "--", "src/file.txt").split()[0]
        self.assertEqual(self.git("ls-files", "-s", "--", "src/file.txt", cwd=target).split()[0], input_mode)
        self.assertEqual((target / "src/selected.bin").read_bytes(), selected.read_bytes())
        self.assertEqual(workspace._observe(target, [], state_dir=self.state)["tracked"]["src/link"],
                         workspace._entries(self.repo, manifest["inputCommit"])["src/link"])
        self.assertFalse((target / "src/excluded.txt").exists())
        self.assertEqual(workspace.input_untracked_paths(manifest), ["src/link", "src/selected.bin"])
        self.assertEqual(self.git("show", manifest["snapshot"]["stagedCommit"] + ":src/file.txt"), b"staged\n")
        self.assertEqual(original_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(original_status, self.git("status", "--porcelain=v1", "-z"))
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").decode().strip())
        self.assertEqual(outside.read_text(encoding="utf-8"), "do not follow")
        self.assertEqual(sorted(manifest["snapshot"]["excludedEntries"]), ["src/excluded.txt"])
        self.assertNotIn("stagedSha256", manifest["snapshot"])
        self.assertNotIn("unstagedSha256", manifest["snapshot"])
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])

    def test_prepare_replay_freezes_source_and_changed_intent_conflicts(self):
        intent = self.intent()
        first = workspace.prepare(self.state, "same", intent)
        (self.repo / "src/file.txt").write_text("new input\n", encoding="utf-8", newline="\n")
        self.git("commit", "-qam", "source advanced")
        self.assertEqual(workspace.prepare(self.state, "same", intent), first)
        second = workspace.prepare(self.state, "new-request", intent)
        self.assertNotEqual(first["inputCommit"], second["inputCommit"])
        self.assertEqual(native_path(Path(first["path"]) / "src/file.txt").read_text(encoding="utf-8"), "base\n")
        self.assert_error("WORKSPACE_CONFLICT", workspace.prepare, self.state, "same", self.intent(writeScope=["docs"]))

    def test_existing_commit_requires_exact_checkout(self):
        manifest = self.prepare(kind="existing", base={"kind": "commit", "ref": self.base})
        self.assertEqual(manifest["inputCommit"], self.base)
        (self.repo / "src/file.txt").write_text("dirty\n", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "dirty", kind="existing", base={"kind": "commit", "ref": self.base})
        self.git("add", ".")
        self.git("commit", "-qm", "new head")
        self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "different", kind="existing", base={"kind": "commit", "ref": self.base})

    def test_verify_detects_drift_and_read_cannot_opt_out(self):
        manifest = self.prepare(access="read", writeScope=[])
        native_path(Path(manifest["path"]) / "src/file.txt").write_text("changed\n", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, state_dir=self.state)
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, require_unchanged=False, state_dir=self.state)

    def test_unknown_worktree_target_is_preserved(self):
        _, directory = workspace._workspace_directory(self.state, "collision")
        target = directory / "checkout"
        target.mkdir()
        (target / "precious.txt").write_text("preserve\n", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare, "collision")
        self.assertEqual((target / "precious.txt").read_text(encoding="utf-8"), "preserve\n")

    def test_completed_checkout_recovers_after_interrupted_manifest_write(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        with patch.object(workspace, "_write_once", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        (self.repo / "src/file.txt").write_text("source advanced after interruption", encoding="utf-8", newline="\n")
        recovered = self.prepare()
        self.assertEqual(native_path(Path(recovered["path"]) / "src/file.txt").read_text(encoding="utf-8"), "base\n")
        self.assertTrue(workspace.verify(recovered, state_dir=self.state)["unchanged"])

    def test_changed_interrupted_checkout_is_not_overwritten(self):
        original = workspace._write_once
        def interrupt(path, data):
            if path.name == "manifest.json":
                raise OSError("simulated crash")
            return original(path, data)
        original_git = workspace._git
        def failed_removal(root, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                raise BoardError("WORKSPACE_GIT_ERROR", "simulated cleanup failure", argv=["git", *args])
            return original_git(root, *args, **kwargs)
        with patch.object(workspace, "_write_once", side_effect=interrupt), patch.object(workspace, "_git", side_effect=failed_removal):
            error = self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        self.assertFalse(error.details["preparationCleanup"]["removed"])
        _, directory = workspace._workspace_directory(self.state, "request")
        changed = directory / "checkout/src/file.txt"
        changed.write_text("valuable unfinished work", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        self.assertEqual(changed.read_text(encoding="utf-8"), "valuable unfinished work")

    def test_seal_pins_actual_output_binary_diff_and_replays_after_removal(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_bytes(b"output\x00\xff\n")
        (target / "src/new.txt").write_text("new output\n", encoding="utf-8", newline="\n")
        (target / "src/link").symlink_to("../../external-missing")
        (target / "src/new.txt").chmod(0o755)
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(output["changedPaths"], ["src/file.txt", "src/link", "src/new.txt"])
        self.assertEqual(self.git("show", output["commit"] + ":src/file.txt"), b"output\x00\xff\n")
        self.assertEqual(self.git("show", output["commit"] + ":src/link"), b"../../external-missing")
        self.assertIn(b"100644" if os.name == "nt" else b"100755",
                      self.git("ls-tree", output["commit"], "src/new.txt"))
        self.assertEqual(output["diffSha256"], hashlib.sha256(native_path(Path(output["diffPath"])).read_bytes()).hexdigest())
        self.assertEqual(self.git("rev-parse", output["commit"] + "^").decode().strip(), manifest["inputCommit"])
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), output)
        integration = self.root / "integration"
        self.git("worktree", "add", "--detach", str(integration), manifest["inputCommit"])
        self.git("-c", "core.symlinks=true", "apply", "--binary", output["diffPath"], cwd=integration)
        self.assertEqual((integration / "src/file.txt").read_bytes(), b"output\x00\xff\n")
        self.assertEqual(workspace._observe(integration, ["src/link"], state_dir=self.state)["included"]["src/link"],
                         workspace._entries(self.repo, output["commit"])["src/link"])
        self.assertEqual(bool((integration / "src/new.txt").stat().st_mode & 0o111), os.name != "nt")
        self.git("worktree", "unlock", str(target))
        self.git("worktree", "remove", "--force", str(target))
        self.git("gc", "--prune=now")
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt"), output)
        self.assertEqual(self.git("rev-parse", output["ref"]).decode().strip(), output["commit"])

    def test_outside_scope_edits_and_new_files_are_reported(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "docs/readme.md").write_text("outside\n", encoding="utf-8", newline="\n")
        (target / "new.txt").write_text("outside new\n", encoding="utf-8", newline="\n")
        error = self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal, self.state, manifest, "task", "attempt")
        self.assertEqual(error.details["paths"], ["docs/readme.md", "new.txt"])
        self.assertEqual((target / "docs/readme.md").read_text(encoding="utf-8"), "outside\n")

    def test_existing_excluded_inputs_are_not_accidentally_sealed(self):
        (self.repo / "outside.txt").write_text("unselected\n", encoding="utf-8", newline="\n")
        manifest = self.prepare(kind="existing")
        (self.repo / "src/file.txt").write_text("changed\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(output["changedPaths"], ["src/file.txt"])
        self.assertNotIn(b"outside.txt", self.git("ls-tree", "-r", output["commit"]))
        (self.repo / "outside.txt").write_text("changed excluded input\n", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal, self.state, manifest, "task", "new-attempt")

    @unittest.skipUnless(os.name == "nt", "Git for Windows long file handling")
    def test_short_worktree_long_file_seals_and_returns_the_same_patch_as_the_source(self):
        relative = Path("src")
        # Use the same Git path in both cases: the source file stays below MAX_PATH,
        # while the managed checkout adds its workspace allocation to the prefix.
        while len(str(self.repo / relative / "payload.bin")) + 33 <= 240:
            relative /= "d" * 32
        remaining = 240 - len(str(self.repo / relative / "payload.bin")) - 1
        if remaining > 0:
            relative /= "d" * remaining
        relative /= "payload.bin"
        source = self.repo / relative
        self.assertLessEqual(len(str(source)), 240)
        native_path(source.parent).mkdir(parents=True, exist_ok=True)
        native_path(source).write_bytes(b"before\x00\xff\r\n")
        self.git("add", "--", relative.as_posix())
        self.git("commit", "-qm", "long file baseline")
        config = (self.repo / ".git/config").read_bytes()

        manifest = self.prepare(request="long-file")
        checkout = Path(manifest["checkoutRoot"])
        target = checkout / relative
        self.assertLess(len(str(checkout)), 260)
        self.assertGreater(len(str(target)), 260)
        self.assertEqual(native_path(target).read_bytes(), b"before\x00\xff\r\n")
        self.assertTrue(workspace.verify(manifest, require_unchanged=True, state_dir=self.state)["unchanged"])
        self.assertEqual(workspace._git(checkout, "status", "--porcelain=v1", "--untracked-files=all"), b"")
        native_path(target).write_bytes(b"after\x00\xfe\r\n")
        sealed = workspace.seal(self.state, manifest, "long-file", "attempt")
        returned = workspace.cumulative_patch(manifest, sealed, manifest["inputCommit"])
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, sealed)["changedPaths"],
                         [relative.as_posix()])

        ordinary = self.prepare(request="ordinary-file", kind="existing")
        native_path(source).write_bytes(b"after\x00\xfe\r\n")
        control = workspace.seal(self.state, ordinary, "ordinary-file", "attempt")
        expected = workspace.cumulative_patch(ordinary, control, ordinary["inputCommit"])
        self.assertEqual(native_path(returned["path"]).read_bytes(), native_path(expected["path"]).read_bytes())
        self.assertEqual(returned["sha256"], expected["sha256"])
        self.assertEqual(returned["changedPaths"], [relative.as_posix()])
        self.assertEqual((self.repo / ".git/config").read_bytes(), config)

    def test_read_only_seal_rejects_index_only_and_untracked_drift(self):
        manifest = self.prepare(access="read", writeScope=[])
        target = Path(manifest["path"])
        output = workspace.seal(self.state, manifest, "task", "initial")
        self.assertEqual(output["commit"], manifest["inputCommit"])
        self.assertEqual(output["changedPaths"], [])
        self.git("rm", "--cached", "src/file.txt", cwd=target)
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, manifest, "task", "changed-index")
        self.git("add", "src/file.txt", cwd=target)
        (target / "extra.txt").write_text("extra", encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, manifest, "task", "changed-file")

    def test_seal_recovers_original_output_after_interrupted_publication(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_text("first output\n", encoding="utf-8", newline="\n")
        original = workspace._pin
        def interrupt(root, ref, commit):
            if "/outputs/" in ref:
                raise OSError("simulated crash")
            return original(root, ref, commit)
        with patch.object(workspace, "_pin", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", workspace.seal, self.state, manifest, "task", "attempt")
        (target / "src/file.txt").write_text("later edits\n", encoding="utf-8", newline="\n")
        self.git("gc", "--prune=now")
        recovered = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("show", recovered["commit"] + ":src/file.txt"), b"first output\n")
        self.assertEqual((target / "src/file.txt").read_text(encoding="utf-8"), "later edits\n")

    def test_output_ref_collision_and_diff_corruption_are_honest_conflicts(self):
        manifest = self.prepare()
        target = Path(manifest["path"])
        (target / "src/file.txt").write_text("output\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.git("update-ref", output["ref"], self.base)
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("rev-parse", output["ref"]).decode().strip(), self.base)
        self.git("update-ref", output["ref"], output["commit"])
        native_path(Path(output["diffPath"])).write_bytes(b"corrupted")
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")

    def test_missing_input_and_output_refs_allow_seal_and_verification_but_block_cleanup(self):
        manifest = self.prepare()
        target = Path(manifest["checkoutRoot"])
        self.git("update-ref", "-d", manifest["snapshot"]["inputRef"])
        self.git("update-ref", "-d", manifest["snapshot"]["stagedRef"])
        (target / "src/file.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        self.git("update-ref", "-d", output["ref"])
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, output)["changedPaths"], ["src/file.txt"])
        integrated = self.root / "integrated"
        self.git("worktree", "add", "--detach", str(integrated), output["commit"])
        result = workspace.integration_verify(output, original_input=manifest, final_input=manifest,
                                              path=str(integrated), ref="HEAD")
        self.assertTrue(result["verified"])
        cleanup = workspace.cleanup_inspect(self.state, manifest, output)
        self.assertFalse(cleanup["eligible"])
        self.assertIn("sealed-output-invalid", cleanup["reasons"])
        self.assertTrue(target.is_dir())

    def test_matching_seal_cleanup_proof_only_updates_the_private_index(self):
        manifest = self.prepare()
        native_path(Path(manifest["checkoutRoot"]) / "src/file.txt").write_text("sealed output\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        git = workspace._git

        def read_only_git(root, *args, **kwargs):
            self.assertNotIn(args[0], {"write-tree", "commit-tree", "update-ref"})
            if args[0] in {"add", "update-index", "read-tree"}:
                temporary = Path(kwargs["env"]["GIT_INDEX_FILE"])
                self.assertTrue(temporary.is_relative_to(self.state / "tmp"))
            return git(root, *args, **kwargs)

        with patch.object(workspace, "_git", side_effect=read_only_git):
            cleanup = workspace.cleanup_inspect(self.state, manifest, output)
        self.assertTrue(cleanup["eligible"], cleanup["reasons"])

    def test_existing_checkout_continuation_uses_exact_sealed_baseline(self):
        (self.repo / "src/file.txt").write_text("initial staged\n", encoding="utf-8", newline="\n")
        self.git("add", "src/file.txt")
        original_index = (self.repo / ".git/index").read_bytes()
        initial = self.prepare("turn-one", kind="existing")
        (self.repo / "src/file.txt").write_text("first output\n", encoding="utf-8", newline="\n")
        (self.repo / "src/new.txt").write_text("new output\n", encoding="utf-8", newline="\n")
        first = workspace.seal(self.state, initial, "task", "attempt-one")
        self.assertEqual(first["includedUntracked"], ["src/new.txt"])
        self.assertEqual(self.prepare("turn-one", kind="existing"), initial)
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, initial, state_dir=self.state)
        continuation = self.prepare("turn-two", kind="existing", base={"kind": "working-tree", "ref": first["commit"]},
                                    includeUntracked=first["includedUntracked"])
        self.assertEqual(continuation["inputCommit"], first["commit"])
        self.assertEqual(continuation["inputTree"], first["tree"])
        self.assertTrue(workspace.verify(continuation, state_dir=self.state)["unchanged"])
        (self.repo / "src/new.txt").write_text("second output\n", encoding="utf-8", newline="\n")
        second = workspace.seal(self.state, continuation, "task", "attempt-two")
        self.assertEqual(second["inputCommit"], first["commit"])
        self.assertEqual(second["changedPaths"], ["src/new.txt"])
        self.assertNotEqual(first["snapshotSha256"], second["snapshotSha256"])
        self.assertEqual(original_index, (self.repo / ".git/index").read_bytes())
        self.assertEqual(self.base, self.git("rev-parse", "HEAD").decode().strip())
        for index in range(40):
            (self.repo / "src" / f"host-{index:02}.txt").write_text("Host commit\n", encoding="utf-8", newline="\n")
        long_path = "src/" + "/".join(["a" * 100] * 5) + "/long.txt"
        native_path(self.repo / long_path).parent.mkdir(parents=True)
        native_path(self.repo / long_path).write_text("Long Host path\n", encoding="utf-8", newline="\n")
        self.git("add", long_path, *(f"src/host-{index:02}.txt" for index in range(40)))
        self.git("commit", "-qm", "Host changes after the seal")
        error = self.assert_error("WORKSPACE_BASE_MISMATCH", self.prepare, "stale-handoff", kind="existing",
                                  base={"kind": "working-tree", "ref": first["commit"]}, includeUntracked=first["includedUntracked"])
        self.assertEqual(error.details["baseCommit"], first["commit"])
        self.assertEqual(error.details["paths"], [long_path[:500], *[f"src/host-{index:02}.txt" for index in range(31)]])
        self.assertEqual(error.details["pathsCount"], 42)
        self.assertTrue(error.details["pathsTruncated"])
        self.assertTrue(error.details["pathsSamplesShortened"])

    def test_sibling_cwd_maps_to_equivalent_subdirectory_in_isolated_tree(self):
        manifest = self.prepare(cwd=str(self.repo / "src"))
        self.assertEqual(Path(manifest["path"]), Path(manifest["checkoutRoot"]) / "src")
        self.assertTrue(native_path(Path(manifest["path"]) / "file.txt").exists())
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])

    def test_symbolic_input_reference_cannot_modify_a_branch(self):
        workspace_id, _ = workspace._workspace_directory(self.state, "request")
        reference = f"refs/hey-my-buddy/workspaces/{workspace_id}/input"
        self.git("symbolic-ref", reference, "refs/heads/unborn-target")
        self.assert_error("WORKSPACE_CONFLICT", self.prepare)
        result = subprocess.run(["git", "-C", str(self.repo), "show-ref", "--verify", "refs/heads/unborn-target"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)

    @unittest.skipIf(os.name == "nt", "control characters are not Windows file names")
    def test_explicit_ignored_untracked_input_and_unusual_filenames(self):
        (self.repo / ".gitignore").write_text("*.secret\n", encoding="utf-8", newline="\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore secrets")
        name = "src/odd\nname\t.secret"
        (self.repo / name).write_bytes(b"\x00selected")
        (self.repo / "src/unselected.secret").write_text("excluded", encoding="utf-8", newline="\n")
        manifest = self.prepare(includeUntracked=[name])
        self.assertEqual(native_path(Path(manifest["path"]) / name).read_bytes(), b"\x00selected")
        self.assertFalse(native_path(Path(manifest["path"]) / "src/unselected.secret").exists())
        (self.repo / name).write_bytes(b"later source input")
        self.assertEqual(native_path(Path(manifest["path"]) / name).read_bytes(), b"\x00selected")
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])
        native_path(Path(manifest["path"]) / name).write_bytes(b"changed selected input")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, state_dir=self.state)

    def test_ignored_caches_do_not_interfere_with_managed_input_or_output(self):
        (self.repo / ".gitignore").write_text("*.ignored\n", encoding="utf-8", newline="\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore generated files")
        reader = self.prepare("reader", access="read", writeScope=[])
        native_path(Path(reader["path"]) / "new.ignored").write_text("generated cache", encoding="utf-8", newline="\n")
        self.assertTrue(workspace.verify(reader, state_dir=self.state)["unchanged"])
        self.assertEqual(workspace.seal(self.state, reader, "reader", "attempt")["changedPaths"], [])
        writer = self.prepare("writer")
        native_path(Path(writer["path"]) / "docs/out.ignored").write_text("generated cache outside write scope", encoding="utf-8", newline="\n")
        native_path(Path(writer["path"]) / "src/out.ignored").write_text("generated cache inside write scope", encoding="utf-8", newline="\n")
        native_path(Path(writer["path"]) / "src/file.txt").write_text("managed output\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, writer, "writer", "attempt")
        self.assertEqual(output["changedPaths"], ["src/file.txt"])
        self.assertNotIn(b"out.ignored", self.git("ls-tree", "-r", output["commit"]))
        existing_file = self.repo / "docs/source.ignored"
        existing_file.write_text("preexisting ignored cache", encoding="utf-8", newline="\n")
        existing = self.prepare("existing", kind="existing", access="read", writeScope=[])
        self.assertNotIn("docs/source.ignored", sorted(existing["snapshot"]["excludedEntries"]))
        existing_file.write_text("updated ignored cache", encoding="utf-8", newline="\n")
        self.assertTrue(workspace.verify(existing, state_dir=self.state)["unchanged"])
        self.assertEqual(workspace.seal(self.state, existing, "existing", "attempt")["changedPaths"], [])

    def test_ignored_dependencies_and_unreadable_environments_are_not_read(self):
        (self.repo / ".gitignore").write_text("node_modules/\n.venv/\ntmp/\n", encoding="utf-8", newline="\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "exclude environments")
        dependency = self.repo / "node_modules/package"
        dependency.mkdir(parents=True)
        for number in range(1000):
            (dependency / f"module-{number}.js").write_text("ignored dependency\n", encoding="utf-8", newline="\n")
        environment = self.repo / ".venv"
        environment.mkdir()
        (environment / "unreadable").write_text("private environment input", encoding="utf-8", newline="\n")
        environment.chmod(0)
        self.addCleanup(environment.chmod, 0o700)
        (self.repo / "tmp").mkdir()
        (self.repo / "tmp/output.log").write_text("ignored runtime log", encoding="utf-8", newline="\n")
        (self.repo / ".git/info/exclude").write_text("local-environment/\n", encoding="utf-8", newline="\n")
        (self.repo / "local-environment").mkdir()
        (self.repo / "local-environment/secret").write_text("ignored by repository exclude", encoding="utf-8", newline="\n")
        manifest = self.prepare(kind="existing", access="read", writeScope=[], base={"kind": "commit", "ref": "HEAD"})
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])
        self.assertEqual(workspace.seal(self.state, manifest, "task", "attempt")["changedPaths"], [])
        self.assertEqual(sorted(manifest["snapshot"]["excludedEntries"]), [])
        self.assertEqual(manifest["exclusionPolicy"], {"version": 1, "kind": "git-standard",
                         "managedPaths": "tracked-and-nonignored-untracked", "selectedIgnored": "includeUntracked"})

    def test_explicit_ignored_directory_is_bounded_and_checked(self):
        (self.repo / ".gitignore").write_text("fixtures/private/\nother-private/\n", encoding="utf-8", newline="\n")
        self.git("add", ".gitignore")
        self.git("commit", "-qm", "ignore private fixtures")
        fixtures = self.repo / "fixtures/private"
        fixtures.mkdir(parents=True)
        (fixtures / "config.json").write_text('{"fixture": true}', encoding="utf-8", newline="\n")
        (fixtures / "binary.dat").write_bytes(b"\x00selected binary\xff")
        ignored = self.repo / "other-private"
        ignored.mkdir()
        (ignored / "secret").write_text("must not be read", encoding="utf-8", newline="\n")
        ignored.chmod(0)
        self.addCleanup(ignored.chmod, 0o700)
        manifest = self.prepare(kind="existing", access="read", writeScope=[], includeUntracked=["fixtures/private"])
        self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])
        self.assertEqual(workspace.input_untracked_paths(manifest), ["fixtures/private/binary.dat", "fixtures/private/config.json"])
        (fixtures / "config.json").write_text('{"fixture": "changed"}', encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CHANGED", workspace.verify, manifest, state_dir=self.state)
        self.assert_error("WORKSPACE_CHANGED", workspace.seal, self.state, manifest, "task", "attempt")

    def test_frozen_input_survives_gc_before_public_input_ref_publication(self):
        (self.repo / "src/file.txt").write_text("frozen uncommitted input\n", encoding="utf-8", newline="\n")
        original = workspace._pin
        def interrupt(root, ref, commit):
            if ref.endswith("/input") and "/retained/" not in ref:
                raise OSError("simulated crash")
            return original(root, ref, commit)
        with patch.object(workspace, "_pin", side_effect=interrupt):
            self.assert_error("WORKSPACE_IO_ERROR", self.prepare)
        (self.repo / "src/file.txt").write_text("later source edits\n", encoding="utf-8", newline="\n")
        self.git("gc", "--prune=now")
        manifest = self.prepare()
        self.assertEqual(native_path(Path(manifest["path"]) / "src/file.txt").read_text(encoding="utf-8"), "frozen uncommitted input\n")

    def test_replayed_output_rejects_inconsistent_top_level_fields(self):
        manifest = self.prepare()
        native_path(Path(manifest["path"]) / "src/file.txt").write_text("output\n", encoding="utf-8", newline="\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        path = Path(output["diffPath"]).parent / "output.json"
        changed = {**output, "changedPaths": []}
        native_path(path).write_text(json.dumps(changed), encoding="utf-8", newline="\n")
        self.assert_error("WORKSPACE_CONFLICT", workspace.seal, self.state, manifest, "task", "attempt")

    def test_malformed_manifest_and_input_fail_with_structured_errors(self):
        manifest = self.prepare()
        changed = {**manifest, "access": "read"}
        self.assert_error("WORKSPACE_MANIFEST_CHANGED", workspace.verify, changed, state_dir=self.state)
        changed = {**manifest, "snapshot": {}}
        changed["manifestSha256"] = hashlib.sha256(json.dumps({key: value for key, value in changed.items() if key != "manifestSha256"},
                                                             sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
        self.assert_error("INVALID_WORKSPACE", workspace.verify, changed, state_dir=self.state)
        self.assert_error("INVALID_WORKSPACE", self.prepare, "escape", writeScope=["../outside"])
        self.assert_error("INVALID_WORKSPACE", self.prepare, "git-metadata", includeUntracked=[".git/config"])


if __name__ == "__main__":
    unittest.main()
