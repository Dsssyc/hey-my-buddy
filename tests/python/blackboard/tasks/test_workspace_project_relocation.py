"""Project markers and relocated repositories exercised with private real Git data."""
from __future__ import annotations

from pathlib import Path
import os
import secrets
import shutil
import subprocess
import unittest
from unittest.mock import patch

from support import BoardTestCase, unlink_read_only
from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


PROJECT_REF = "refs/hey-my-buddy/project"


class ProjectRepositoryCase(BoardTestCase):
    def copy_repository(self, target):
        if os.name == "nt":
            shutil.copytree(self.repo, target)
        else:
            subprocess.run(["cp", "-R", str(self.repo), str(target)], check=True, capture_output=True)

    def setUp(self):
        super().setUp()
        self.repo = self.directory / "repo"
        self.repo.mkdir()
        self.git(self.repo, "init", "-q")
        self.git(self.repo, "config", "user.name", "Project Marker Test")
        self.git(self.repo, "config", "user.email", "marker@example.invalid")
        self.git(self.repo, "config", "core.autocrlf", "false")
        (self.repo / "tracked.txt").write_text("base\n", encoding="utf-8", newline="\n")
        self.git(self.repo, "add", "tracked.txt")
        self.git(self.repo, "commit", "-qm", "base")

    def git(self, path, *args, data=None, check=True):
        command, environment = workspace._git_command(path, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=30)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout.strip()

    def common(self, repo=None):
        return str((repo or self.repo) / ".git")

    def intent(self, *, kind="worktree", cwd=None):
        return {"kind": kind, "cwd": str(cwd or self.repo), "access": "write",
                "base": {"kind": "working-tree"}, "writeScope": ["."],
                "integrator": {"role": "host", "id": "host-1"}}

    def prepare(self, name="request", *, kind="worktree", cwd=None):
        return workspace.prepare(self.directory, name, self.intent(kind=kind, cwd=cwd))

    def assert_code(self, code, call, *args, **kwargs):
        with self.assertRaises(BoardError) as raised:
            call(*args, **kwargs)
        self.assertEqual(raised.exception.code, code, raised.exception.payload())

    def fixed_files(self, manifest):
        allocation = self.directory / "workspaces" / manifest["workspaceId"]
        return {str(path.relative_to(allocation)): path.read_bytes()
                for path in allocation.rglob("*") if path.is_file() and path.suffix == ".json"}


class ProjectMarkerTests(ProjectRepositoryCase):
    def test_inspect_is_read_only_until_prepare_and_siblings_share_the_project(self):
        first = workspace.inspect(str(self.repo))
        self.assertIsNone(first["projectId"])
        self.assertIsNone(workspace.read_project_id(self.common()))
        self.assertFalse(self.git(self.repo, "show-ref", "--verify", PROJECT_REF, check=False))

        manifest = self.prepare()
        project_id = manifest["projectId"]
        self.assertRegex(project_id, r"^[0-9a-f]{64}$")
        self.assertEqual(workspace.read_project_id(self.common()), project_id)
        self.assertEqual(self.git(self.repo, "cat-file", "-t", PROJECT_REF), b"blob")
        self.assertEqual(self.git(self.repo, "show", PROJECT_REF).decode().strip(), project_id)
        self.assertEqual(manifest["version"], 5)
        self.assertNotIn("repositoryId", manifest)
        self.assertNotIn("checkoutId", manifest)

        sibling = self.repo / "subdir"
        sibling.mkdir()
        observed = workspace.inspect(str(sibling))
        self.assertEqual(observed["projectId"], project_id)
        self.assertEqual(observed["checkoutRoot"], str(self.repo))
        self.assertEqual(observed["repositoryPath"], self.common())
        self.assertEqual(observed["path"], str(sibling))
        linked = self.directory / "sibling-worktree"
        self.git(self.repo, "worktree", "add", "--detach", str(linked), "HEAD")
        self.assertEqual(workspace.inspect(str(linked))["projectId"], project_id)

    def test_clone_is_a_new_project_but_directory_copy_keeps_marker(self):
        original = self.prepare()
        copied = self.directory / "copied"
        self.copy_repository(copied)
        self.assertEqual(workspace.inspect(str(copied))["projectId"], original["projectId"])
        clone = self.directory / "clone"
        self.git(self.directory, "clone", "-q", str(self.repo), str(clone))
        self.assertIsNone(workspace.inspect(str(clone))["projectId"])
        new = workspace.ensure_project_id(str(clone / ".git"))
        self.assertRegex(new, r"^[0-9a-f]{64}$")
        self.assertNotEqual(new, original["projectId"])

    def test_first_creator_loses_cas_and_reads_winner_without_stderr_parsing(self):
        winner = secrets.token_hex(32)
        real_run = subprocess.run
        observed_cas = []

        def competing_run(command, *args, **kwargs):
            if "update-ref" in command and PROJECT_REF in command and not observed_cas:
                observed_cas.append(command)
                _, env = workspace._git_command(self.repo, "rev-parse", "HEAD")
                blob = real_run(["git", "-C", str(self.repo), "hash-object", "-w", "--stdin"],
                                input=winner.encode(), env=env, capture_output=True, check=True).stdout.strip()
                real_run(["git", "-C", str(self.repo), "update-ref", PROJECT_REF,
                          blob.decode(), "0" * 40], env=env, capture_output=True, check=True)
                # The real loser is a nonzero update-ref. Its diagnostic is not
                # the protocol for deciding whether the winning ref is valid.
                loser = real_run(command, *args, **kwargs)
                self.assertNotEqual(loser.returncode, 0)
                loser.stderr = b"localized CAS conflict"
                return loser
            return real_run(command, *args, **kwargs)

        with patch.object(workspace.subprocess, "run", side_effect=competing_run):
            self.assertEqual(workspace.ensure_project_id(self.common()), winner)
        self.assertEqual(len(observed_cas), 1)
        self.assertIn("0" * 40, observed_cas[0])
        self.assertEqual(workspace.read_project_id(self.common()), winner)

    def test_bad_marker_and_git_read_errors_are_not_treated_as_absence(self):
        self.git(self.repo, "update-ref", PROJECT_REF, "HEAD")
        with self.assertRaises(BoardError):
            workspace.read_project_id(self.common())
        with self.assertRaises(BoardError):
            workspace.ensure_project_id(self.common())
        self.git(self.repo, "update-ref", "-d", PROJECT_REF)
        bad_blob = self.git(self.repo, "hash-object", "-w", "--stdin", data=b"not-a-project-id")
        self.git(self.repo, "update-ref", PROJECT_REF, bad_blob.decode())
        with self.assertRaises(BoardError):
            workspace.read_project_id(self.common())

    def test_repository_at_distinguishes_repo_absence_and_other_git_failure(self):
        self.assertEqual(workspace.repository_at(str(self.repo)), self.common())
        self.assertIsNone(workspace.repository_at(str(self.directory / "missing")))
        self.assertIsNone(workspace.repository_at(str(self.repo / "tracked.txt")))
        plain = self.directory / "plain"
        plain.mkdir()
        self.assertIsNone(workspace.repository_at(str(plain)))
        real_run = subprocess.run

        def refused(command, *args, **kwargs):
            if "rev-parse" in command:
                return subprocess.CompletedProcess(command, 128, b"",
                    b"fatal: cannot chdir to 'not a git repository': Permission denied")
            return real_run(command, *args, **kwargs)

        with patch.object(workspace.subprocess, "run", side_effect=refused):
            self.assert_code("WORKSPACE_GIT_ERROR", workspace.repository_at, str(self.repo))

    def test_repository_at_git_directory_without_files_is_absent(self):
        git_dir = self.repo / ".git"
        for path in git_dir.rglob("*"):
            if path.is_file():
                unlink_read_only(path)
        directories = {path.relative_to(git_dir) for path in git_dir.rglob("*")}
        self.assertIsNone(workspace.repository_at(str(self.repo)))
        self.assertIsNone(workspace.repository_at(str(git_dir)))
        self.assertEqual({path.relative_to(git_dir) for path in git_dir.rglob("*")}, directories)
        self.assertTrue(all(path.is_dir() for path in git_dir.rglob("*")))

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "chmod 000 requires POSIX and a non-root user")
    def test_repository_at_unreadable_location_is_an_error(self):
        mode = self.repo.stat().st_mode
        self.repo.chmod(0)
        try:
            with self.assertRaises(BoardError) as error:
                workspace.repository_at(str(self.repo))
            self.assertEqual(error.exception.code, "WORKSPACE_GIT_ERROR")
            self.assertIn(str(self.repo), str(error.exception.payload()))
        finally:
            self.repo.chmod(mode)

    @unittest.skipUnless(os.name == "posix" and os.geteuid() != 0,
                         "chmod 000 requires POSIX and a non-root user")
    def test_repository_at_unreadable_git_directory_is_an_error(self):
        git_dir = self.repo / ".git"
        mode = git_dir.stat().st_mode
        git_dir.chmod(0)
        try:
            with self.assertRaises(BoardError) as error:
                workspace.repository_at(str(self.repo))
            self.assertEqual(error.exception.code, "WORKSPACE_IO_ERROR")
            self.assertIn(str(git_dir), str(error.exception))
            self.assertIsInstance(error.exception.__cause__, PermissionError)
        finally:
            git_dir.chmod(mode)

    def test_historical_manifest_versions_use_repository_id_without_rewriting(self):
        current = self.prepare()
        snapshot = {key: value for key, value in current["snapshot"].items() if key != "executionSelection"}
        snapshot.update(executionSelectors=workspace.execution_selection(current).get("selectors", [])
                        if workspace.execution_selection(current) else [],
                        includeSelectors=[], includedUntracked=workspace.input_untracked_paths(current),
                        excludedUntracked=sorted(snapshot["excludedEntries"]))
        for version in (1, 2, 3):
            with self.subTest(version=version):
                historical = dict(current, version=version, checkoutId="historical-checkout",
                                  repositoryId=current["projectId"], snapshot=snapshot)
                historical.pop("projectId")
                if version == 1:
                    historical["integrator"] = "host:host-1"
                historical["manifestSha256"] = workspace._sha(workspace._json({
                    key: value for key, value in historical.items() if key != "manifestSha256"}))
                sealed_bytes = workspace._json(historical)
                self.assertTrue(workspace.verify(historical, state_dir=self.directory)["valid"])
                self.assertEqual(workspace._json(historical), sealed_bytes)


class RepositoryRelocationTests(ProjectRepositoryCase):
    def test_rename_broken_worktree_is_repaired_from_cwd(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        moved = self.directory / "renamed"
        self.repo.rename(moved)
        self.assertIsNone(workspace.repository_at(str(checkout)))
        self.assertEqual(workspace.locate_repository(manifest, cwd=str(moved)), str(moved / ".git"))
        self.assertEqual(workspace.repository_at(str(checkout)), str(moved / ".git"))
        self.assertTrue(workspace.verify(manifest, state_dir=self.directory)["valid"])

    def test_copy_can_recover_when_original_is_gone(self):
        manifest = self.prepare()
        copied = self.directory / "copy"
        self.copy_repository(copied)
        self.repo.rename(self.directory / "unoffered-original")
        self.assertEqual(workspace.locate_repository(manifest, candidates=(str(copied),)),
                         str(copied / ".git"))
        self.assertEqual(workspace.repository_at(manifest["checkoutRoot"]), str(copied / ".git"))

    def _user_worktrees(self):
        roots = [self.directory / "user-one", self.directory / "user-two"]
        for root in roots:
            self.git(self.repo, "worktree", "add", "--detach", str(root), "HEAD")
        return {root / ".git": (root / ".git").read_bytes() for root in roots}

    def test_move_reconnect_changes_only_our_private_gitfile(self):
        users = self._user_worktrees()
        manifest = self.prepare()
        moved = self.directory / "moved"
        self.repo.rename(moved)
        self.assertEqual(workspace.locate_repository(manifest, cwd=str(moved)), str(moved / ".git"))
        self.assertTrue(workspace.verify(manifest, state_dir=self.directory)["valid"])
        self.assertEqual({path: path.read_bytes() for path in users}, users)

    def test_copy_reconnect_preserves_users_still_linked_to_original(self):
        users = self._user_worktrees()
        manifest = self.prepare()
        copied = self.directory / "copy"
        self.copy_repository(copied)
        checkout = Path(manifest["checkoutRoot"])
        # Only our private link is lost. The user's two links still lead to the
        # original and must stay there when the candidate is the copied repo.
        # Git marks .git as hidden on Windows. Update the existing gitfile
        # without CREATE_ALWAYS, which Windows rejects for hidden files.
        with (checkout / ".git").open("r+b") as gitfile:
            gitfile.write(b"gitdir: missing-repository\n")
            gitfile.truncate()
        self.assertEqual(workspace.locate_repository(manifest, cwd=str(copied)), str(copied / ".git"))
        self.assertTrue(workspace.verify(manifest, state_dir=self.directory)["valid"])
        self.assertEqual({path: path.read_bytes() for path in users}, users)
        for pointer in users:
            self.assertEqual(workspace.repository_at(str(pointer.parent)), self.common())

    def test_reconnect_requires_registration_backlink_and_allocation_lock(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        pointer = (checkout / ".git").read_bytes()
        git_dir = Path(workspace.inspect(str(checkout))["gitDir"])
        moved = self.directory / "moved"
        self.repo.rename(moved)
        registration = moved / ".git" / "worktrees" / git_dir.name
        backlink = (registration / "gitdir").read_bytes()
        lock = (registration / "locked").read_bytes()
        for filename, changed in (("gitdir", str(self.directory / "someone-else" / ".git").encode() + b"\n"),
                                  ("locked", b"another-owner\n")):
            with self.subTest(filename=filename):
                (registration / filename).write_bytes(changed)
                self.assertIsNone(workspace.locate_repository(manifest, cwd=str(moved)))
                self.assertEqual((checkout / ".git").read_bytes(), pointer)
                (registration / "gitdir").write_bytes(backlink)
                (registration / "locked").write_bytes(lock)

    def test_missing_checkout_gitfile_does_not_find_ancestor_repository(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        self.git(self.directory, "init", "-q")
        (checkout / ".git").unlink()
        self.assertIsNone(workspace.repository_at(str(checkout)))
        self.assertEqual(workspace.repository_at(str(checkout), allow_parent=True), str(self.directory / ".git"))
        self.assertEqual(workspace.locate_repository(manifest), self.common())
        self.assertTrue(workspace.verify(manifest, state_dir=self.directory)["valid"])

    def test_live_link_never_evaluates_known_location_query(self):
        manifest = self.prepare()
        def candidates():
            self.fail("A live link must not scan sealed manifests")
        self.assertEqual(workspace.locate_repository(manifest, candidates=candidates), self.common())

    def test_live_link_wins_over_copied_candidate(self):
        manifest = self.prepare()
        copied = self.directory / "copy"
        self.copy_repository(copied)
        self.assertEqual(workspace.locate_repository(manifest, cwd=str(copied)), self.common())
        self.assertEqual(workspace.repository_at(manifest["checkoutRoot"]), self.common())

    def test_broken_link_rejects_clone_and_copy_without_retained_input(self):
        manifest = self.prepare()
        checkout = Path(manifest["checkoutRoot"])
        before_files = self.fixed_files(manifest)
        git_pointer = (checkout / ".git").read_bytes()
        copied = self.directory / "copy"
        self.copy_repository(copied)
        clone = self.directory / "clone"
        self.git(self.directory, "clone", "-q", str(self.repo), str(clone))
        retained = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}/retained/input"
        self.git(copied, "update-ref", "-d", retained)
        self.repo.rename(self.directory / "moved-unoffered")

        self.assertIsNone(workspace.locate_repository(
            manifest, cwd=str(clone), candidates=(str(copied),)))
        self.assertIsNone(workspace.repository_at(str(checkout)))
        self.assertEqual((checkout / ".git").read_bytes(), git_pointer)
        self.assertEqual(self.fixed_files(manifest), before_files)

    def test_missing_repository_keeps_allocation_and_reports_missing_reason(self):
        manifest = self.prepare()
        before_files = self.fixed_files(manifest)
        self.repo.rename(self.directory / "moved-unoffered")
        self.assert_code("WORKSPACE_REPOSITORY_NOT_FOUND", workspace.verify,
                         manifest, state_dir=self.directory)
        self.assertEqual(self.fixed_files(manifest), before_files)

    def test_linked_existing_checkout_is_never_repaired(self):
        linked = self.directory / "existing-linked"
        self.git(self.repo, "worktree", "add", "--detach", str(linked), "HEAD")
        manifest = self.prepare("existing", kind="existing", cwd=linked)
        pointer = (linked / ".git").read_bytes()
        moved = self.directory / "moved"
        self.repo.rename(moved)
        self.assertEqual(workspace.locate_repository(manifest, cwd=str(moved)), str(moved / ".git"))
        self.assertEqual((linked / ".git").read_bytes(), pointer)
        self.assert_code("WORKSPACE_CHECKOUT_NOT_FOUND", workspace.verify, manifest, state_dir=self.directory)
        self.assertIsNone(workspace.repository_at(str(linked)))

    def test_missing_existing_root_is_named_at_inspect_boundary(self):
        linked = self.directory / "existing-linked"
        self.git(self.repo, "worktree", "add", "--detach", str(linked), "HEAD")
        manifest = self.prepare("existing", kind="existing", cwd=linked)
        linked.rename(self.directory / "existing-moved")
        self.assert_code("WORKSPACE_CHECKOUT_NOT_FOUND", workspace.inspect, str(linked), checkout=manifest)

    def test_all_allocation_refs_can_be_removed_while_link_is_live(self):
        manifest = self.prepare()
        prefix = f"refs/hey-my-buddy/workspaces/{manifest['workspaceId']}"
        refs = self.git(self.repo, "for-each-ref", "--format=%(refname)", prefix).decode().splitlines()
        self.assertGreaterEqual(len(refs), 4)
        for ref in refs:
            self.git(self.repo, "update-ref", "-d", ref)
        self.assertTrue(workspace.verify(manifest, state_dir=self.directory)["valid"])
        sealed = workspace.seal(self.directory, manifest, "task-1", "attempt-1")
        self.assertEqual(sealed["inputCommit"], manifest["inputCommit"])

    def test_live_link_still_requires_original_project_marker(self):
        manifest = self.prepare()
        replacement = secrets.token_hex(32)
        blob = self.git(self.repo, "hash-object", "-w", "--stdin", data=replacement.encode())
        self.git(self.repo, "update-ref", PROJECT_REF, blob.decode())
        self.assertIsNone(workspace.locate_repository(manifest))
        self.assert_code("WORKSPACE_CHANGED", workspace.verify, manifest, state_dir=self.directory)
