"""Regression checks for Git observations and disposable checkout boundaries."""
from __future__ import annotations
from contextlib import contextmanager

import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from temporary import TemporaryDirectory
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


class WorkspaceReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory(prefix="hey-my-buddy-workspace-review-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Workspace Review")
        self.git("config", "user.email", "review@example.invalid")
        (self.repo / "src").mkdir()
        (self.repo / "docs").mkdir()
        (self.repo / ".gitattributes").write_bytes(b"* text=auto eol=lf\n")
        (self.repo / "src/file.txt").write_bytes(b"base\n")
        (self.repo / "docs/readme.md").write_bytes(b"docs\n")
        self.git("add", ".")
        self.git("commit", "-qm", "base")

    def git(self, *args, cwd=None, data=None):
        command, environment = workspace._git_command(cwd or self.repo, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def intent(self, **updates):
        return {"kind": "worktree", "cwd": str(self.repo), "access": "write",
                "base": {"kind": "working-tree"}, "writeScope": ["src"],
                "integrator": {"role": "host", "id": "host-1"}, **updates}

    def prepare(self, request, **updates):
        return workspace.prepare(self.state, request, self.intent(**updates))

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(BoardError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code, caught.exception.payload())
        return caught.exception

    def test_ignore_stat_source_and_worker_edits_are_observed(self):
        self.git("config", "core.ignoreStat", "true")
        environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        def user_git(*args, cwd=self.repo):
            result = subprocess.run(["git", "-C", str(cwd), *args], env=environment,
                                    capture_output=True, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        user_git("read-tree", "HEAD")
        user_git("reset", "--hard", "HEAD")
        self.assertTrue(self.git("ls-files", "-v", "--", "src/file.txt").startswith(b"h "))
        source = self.repo / "src/file.txt"
        source.write_bytes(b"source edit hidden by ignoreStat\n")
        before = (self.repo / ".git/index").read_bytes()
        manifest = self.prepare("ignore-stat-source")
        self.assertEqual(self.git("show", manifest["inputCommit"] + ":src/file.txt"), source.read_bytes())
        self.assertEqual((self.repo / ".git/index").read_bytes(), before)
        checkout = Path(manifest["path"])
        self.assertTrue(self.git("ls-files", "-v", "--", "src/file.txt", cwd=checkout).startswith(b"H "))
        user_git("read-tree", "HEAD", cwd=checkout)
        user_git("reset", "--hard", "HEAD", cwd=checkout)
        self.assertTrue(self.git("ls-files", "-v", "--", "src/file.txt", cwd=checkout).startswith(b"h "))
        (checkout / "src/file.txt").write_bytes(b"worker edit hidden by ignoreStat\n")
        sealed = workspace.seal(self.state, manifest, "task", "ignore-stat-attempt")
        self.assertEqual(self.git("show", sealed["commit"] + ":src/file.txt"), b"worker edit hidden by ignoreStat\n")
        self.assertEqual((self.repo / ".git/index").read_bytes(), before)

    def test_assume_unchanged_worker_edit_is_sealed_and_cleanup_keeps_later_edit(self):
        manifest = self.prepare("assume-unchanged-worker")
        checkout = Path(manifest["path"])
        tracked = checkout / "src/file.txt"
        self.git("update-index", "--assume-unchanged", "src/file.txt", cwd=checkout)
        tracked.write_bytes(b"worker output hidden by assume-unchanged\n")

        before = workspace.cleanup_inspect(self.state, manifest)
        self.assertFalse(before["eligible"])
        self.assertIn("unsealed-changes", before["reasons"])
        sealed = workspace.seal(self.state, manifest, "task", "attempt")
        self.assertEqual(self.git("show", sealed["commit"] + ":src/file.txt"), tracked.read_bytes())
        self.assertEqual(sealed["changedPaths"], ["src/file.txt"])
        self.assertTrue(workspace.cleanup_inspect(self.state, manifest, sealed=sealed)["eligible"])

        tracked.write_bytes(b"later unsealed edit\n")
        after = workspace.cleanup_inspect(self.state, manifest, sealed=sealed)
        self.assertFalse(after["eligible"])
        self.assertIn("unsealed-changes", after["reasons"])
        self.assertEqual(tracked.read_bytes(), b"later unsealed edit\n")

    def test_new_checkout_rejects_symlinked_cwd_without_writing_at_target(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.repo / "cwd/newdir").mkdir(parents=True)
        for request, target in (("outside-link", outside), ("source-link", self.repo)):
            with self.subTest(request=request):
                oid = self.git("hash-object", "-w", "--stdin", data=os.fsencode(str(target))).strip().decode()
                self.git("update-index", "--add", "--cacheinfo", "120000," + oid + ",cwd")
                self.git("commit", "-qm", request)
                before_index = (self.repo / ".git/index").read_bytes()
                error = self.assert_error(
                    "WORKSPACE_UNSUPPORTED", self.prepare, request, cwd=str(self.repo / "cwd/newdir"),
                    base={"kind": "commit", "ref": "HEAD"})
                self.assertEqual(error.details["paths"], ["cwd"])
                self.assertFalse((target / "newdir").exists())
                self.assertEqual((self.repo / ".git/index").read_bytes(), before_index)
                self.assertFalse(list((self.state / "workspaces").rglob("manifest.json")))

    def test_checkout_path_cannot_follow_a_link_into_the_source_repository(self):
        request = "checkout-points-at-source"
        workspace_id = "ws-" + workspace._sha(workspace._json([str(self.state), request]))[:32]
        directory = self.state / "workspaces" / workspace_id
        directory.mkdir(parents=True)
        (directory / "checkout").symlink_to(self.repo, target_is_directory=True)
        before_index = (self.repo / ".git/index").read_bytes()
        before_head = self.git("rev-parse", "HEAD")

        with self.assertRaises(BoardError):
            self.prepare(request)

        self.assertEqual((self.repo / ".git/index").read_bytes(), before_index)
        self.assertEqual(self.git("rev-parse", "HEAD"), before_head)
        self.assertEqual((self.repo / "src/file.txt").read_bytes(), b"base\n")
        self.assertFalse((directory / "manifest.json").exists())

    def test_restore_updates_only_selected_real_index_entries(self):
        manifest = self.prepare("selective-index", kind="existing")
        base = workspace._entries(self.repo, manifest["inputCommit"])["src/file.txt"]
        untouched = self.repo / "docs/intent.txt"
        untouched.write_bytes(b"intent content\n")
        self.git("add", "-N", "docs/intent.txt")
        self.git("update-index", "--assume-unchanged", "docs/readme.md")
        original_other_entries = self.git("ls-files", "--debug", "-z", "--", "docs/intent.txt", "docs/readme.md")
        selected = self.repo / "src/file.txt"
        selected.write_bytes(b"staged edit\n")
        self.git("add", "src/file.txt")
        selected.write_bytes(b"unstaged edit\n")
        observation = workspace._observe(self.repo, [], state_dir=self.state)

        workspace._restore_paths(
            self.repo, observation,
            {"src/file.txt": {"mode": base[0], "oid": base[1]}},
            {"src/file.txt": {"mode": base[0], "oid": base[1]}},
            state_dir=self.state, selectors=[],
        )

        self.assertEqual(selected.read_bytes(), b"base\n")
        self.assertEqual(self.git("ls-files", "-s", "--", "src/file.txt"),
                         f"{base[0]} {base[1]} 0\tsrc/file.txt\n".encode())
        self.assertEqual(self.git("ls-files", "--debug", "-z", "--", "docs/intent.txt", "docs/readme.md"),
                         original_other_entries)
        self.assertEqual(untouched.read_bytes(), b"intent content\n")

    def test_excluded_untracked_uses_raw_bytes_and_does_not_write_a_blob(self):
        (self.repo / ".gitattributes").write_bytes(b"src/*.txt text\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "text attributes")
        payload = b"excluded raw\r\nunique workspace review payload\r\n"
        (self.repo / "src/excluded.txt").write_bytes(payload)
        (self.repo / "src/selected.txt").write_bytes(b"selected\r\n")
        oid = self.git("hash-object", "--stdin", data=payload).strip().decode()

        before_objects = set(self.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)").splitlines())
        observation = workspace._observe(self.repo, ["src/selected.txt"], state_dir=self.state)
        after_objects = set(self.git("cat-file", "--batch-all-objects", "--batch-check=%(objectname)").splitlines())
        self.assertEqual(after_objects - before_objects, {observation["included"]["src/selected.txt"][1].encode()})

        self.assertEqual(observation["untracked"]["src/excluded.txt"],
                         ["100644", hashlib.sha256(payload).hexdigest()])
        selected_oid = observation["included"]["src/selected.txt"][1]
        self.assertEqual(self.git("cat-file", "blob", selected_oid), b"selected\n")
        command, environment = workspace._git_command(self.repo, "cat-file", "-e", oid)
        absent = subprocess.run(command, capture_output=True, env=environment, timeout=30)
        self.assertNotEqual(absent.returncode, 0, "excluded content was written into the Git object store")

    def test_conflict_observation_hashes_excluded_untracked_raw_bytes(self):
        (self.repo / ".gitattributes").write_bytes(b"*.txt text\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "text attributes")
        excluded = self.repo / "outside.txt"
        excluded.write_bytes(b"original\r\n")
        manifest = self.prepare("excluded-conflict", kind="existing")
        self.assertEqual(manifest["snapshot"]["excludedEntries"]["outside.txt"][1],
                         hashlib.sha256(b"original\r\n").hexdigest())
        changed = b"different raw\r\n"
        excluded.write_bytes(changed)

        error = self.assert_error("WORKSPACE_SCOPE_VIOLATION", workspace.seal,
                                  self.state, manifest, "task", "attempt")
        evidence = {entry["path"]: entry for entry in error.details["evidence"]["entries"]}
        self.assertEqual(evidence["outside.txt"]["observed"]["sha256"], hashlib.sha256(changed).hexdigest())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO is unavailable")
    def test_fifo_is_rejected_without_opening_it(self):
        fifo = self.repo / "src/pipe"
        os.mkfifo(fifo)
        self.assert_error("WORKSPACE_UNSUPPORTED", workspace._raw_entry, self.repo, "src/pipe")

    def test_private_index_ignores_worker_tmpdir_and_symlink(self):
        manifest = self.prepare("private-tmp")
        checkout = Path(manifest["path"])
        outside = self.root / "outside-scratch"
        outside.mkdir()
        hostile = checkout / "src/tmp-link"
        created = []
        original = workspace.private_dirs.make_temporary_dir

        def record_scratch(*args, **kwargs):
            created.append(Path(kwargs["directory"]))
            return original(*args, **kwargs)

        with patch.dict(os.environ, {"TMPDIR": str(hostile)}), \
             patch.object(tempfile, "gettempdir", return_value=str(hostile)), \
             patch.object(workspace.private_dirs, "make_temporary_dir", side_effect=record_scratch):
            self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])
            hostile.symlink_to(outside, target_is_directory=True)
            (checkout / "src/file.txt").write_bytes(b"sealed edit\n")
            sealed = workspace.seal(self.state, manifest, "task", "attempt")
            self.assertTrue(workspace.cleanup_inspect(self.state, manifest, sealed=sealed)["eligible"])

        self.assertTrue(created)
        self.assertEqual(set(created), {self.state / "tmp"})
        self.assertEqual(list(outside.iterdir()), [])

    def test_observe_records_the_index_that_was_actually_copied(self):
        old_entry = workspace._entries(self.repo)["docs/readme.md"]
        replacement = b"new real staged value\n"
        new_oid = self.git("hash-object", "-w", "--stdin", data=replacement).strip().decode()
        original_index = workspace._index
        copies = []

        @contextmanager
        def replace_before_copy(*args, **kwargs):
            if kwargs.get("copy"):
                copies.append(True)
                self.git("update-index", "--index-info",
                         data=f"100644 {new_oid}\tdocs/readme.md\n".encode())
            with original_index(*args, **kwargs) as environment:
                yield environment

        with patch.object(workspace, "_index", side_effect=replace_before_copy):
            observation = workspace._observe(self.repo, [], state_dir=self.state)

        self.assertEqual(len(copies), 1)
        self.assertEqual(observation["index"]["docs/readme.md"], ["100644", new_oid])
        self.assertEqual(observation["tracked"]["docs/readme.md"], old_entry)
        self.assertEqual(workspace._entries(self.repo)["docs/readme.md"], ["100644", new_oid])
        self.assertEqual(self.git("show", ":docs/readme.md"), replacement)

    def test_read_only_sealing_reuses_one_git_observation(self):
        manifest = self.prepare("reader-observation", access="read", writeScope=[])
        with patch.object(workspace, "_observe", wraps=workspace._observe) as capture:
            sealed = workspace.seal(self.state, manifest, "reader", "attempt")
        self.assertEqual(capture.call_count, 1)
        self.assertEqual(capture.call_args.kwargs["state_dir"], self.state)
        self.assertEqual(sealed["changedPaths"], [])

    def test_large_git_operations_have_no_fixed_timeout(self):
        with patch.object(workspace.subprocess, "run") as run:
            run.return_value = subprocess.CompletedProcess([], 0, b"", b"")
            for command in (("add", "-u"), ("reset", "--hard", "HEAD"),
                            ("checkout-index", "--all"), ("restore", "--worktree", "src/file.txt")):
                with self.subTest(command=command):
                    workspace._git(self.repo, *command)
                    self.assertIsNone(run.call_args.kwargs.get("timeout"))
            workspace._git(self.repo, "status", "--porcelain=v1")
            self.assertEqual(run.call_args.kwargs["timeout"], workspace._GIT_TIMEOUT_SECONDS)
