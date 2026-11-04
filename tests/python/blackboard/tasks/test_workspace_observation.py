"""Real Git coverage for literal workspace observation and bounded path argv."""
from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import subprocess
import struct
import tempfile
import unittest
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path


class WorkspaceObservationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="hey-my-buddy-workspace-observation-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Observation Test")
        self.git("config", "user.email", "observation@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write(".gitignore", b"private/\nprivate-sibling/\n*.ignored\n")
        self.write(".gitattributes", b"* text=auto eol=lf\n")
        self.write("src/tracked.txt", b"source\n")
        self.write("docs/tracked.txt", b"docs\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "observation fixture")
        self.head = self.git("rev-parse", "HEAD").strip().decode()
        self.index = workspace._entries(self.repo)

    def write(self, path, data):
        target = native_path(self.repo / path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def git(self, *args, data=None):
        command, environment = workspace._git_command(self.repo, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def observe(self, selected=(), **kwargs):
        source = self.repo / ".git/index"
        before = source.read_bytes()
        watermark = source.stat().st_mtime_ns
        head = self.git("rev-parse", "HEAD")
        try:
            with patch.object(workspace, "_index", wraps=workspace._index) as indexes:
                return workspace._observe(self.repo, selected, state_dir=self.state, **kwargs)
        finally:
            self.assertEqual(indexes.call_count, 1)
            self.assertTrue(indexes.call_args.kwargs["copy"])
            self.assertEqual(source.read_bytes(), before)
            self.assertEqual(source.stat().st_mtime_ns, watermark)
            self.assertEqual(self.git("rev-parse", "HEAD"), head)

    def assert_fingerprint(self, observation, raw, included, *, tracked=None, index=None):
        expected = {"head": self.head, "index": self.index if index is None else index,
                    "tracked": self.index if tracked is None else tracked,
                    "untracked": raw, "included": included}
        payload = json.dumps(expected, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
        expected["fingerprint"] = hashlib.sha256(payload).hexdigest()
        self.assertEqual({key: value for key, value in observation.items() if key != "untrackedEntries"}, expected)

    def blob(self, data, mode="100644"):
        return [mode, self.git("hash-object", "--stdin", data=data).strip().decode()]

    def stored_blobs(self, *paths):
        """The entries Git itself stages for untracked paths, through a scratch index.

        Git stores a link's blob text from its own reading of the link, and Git
        for Windows normalizes an absolute target's backslashes to forward
        slashes, so the expected ids come from Git's own staging rather than
        from the target spelling the test created."""
        index = self.root / "git-stored-index"
        add_command, environment = workspace._git_command(self.repo, "add", "--force", "--", *paths)
        environment["GIT_INDEX_FILE"] = str(index)
        added = subprocess.run(add_command, env=environment, capture_output=True, timeout=120)
        self.assertEqual(added.returncode, 0, added.stderr.decode(errors="replace"))
        list_command, environment = workspace._git_command(self.repo, "ls-files", "-s", "-z", "--", *paths)
        environment["GIT_INDEX_FILE"] = str(index)
        listed = subprocess.run(list_command, env=environment, capture_output=True, timeout=120)
        self.assertEqual(listed.returncode, 0, listed.stderr.decode(errors="replace"))
        entries = {}
        for record in listed.stdout.split(b"\0"):
            if record:
                meta, path = record.split(b"\t", 1)
                mode, blob_id, _stage = meta.decode().split(" ")
                entries[path.decode()] = [mode, blob_id]
        return entries

    def raw(self, data, mode="100644"):
        return [mode, hashlib.sha256(data).hexdigest()]

    def test_ignored_file_directory_and_siblings_have_the_original_fingerprint(self):
        selected = ("private/file", "private/directory")
        payloads = {"private/file": b"selected\n", "private/directory/a": b"a\n",
                    "private/directory/b.ignored": b"\0binary\xff", "src/excluded.txt": b"excluded\r\n"}
        for path, payload in payloads.items():
            self.write(path, payload)
        self.write("private/file-sibling", b"ignored sibling\n")
        self.write("private/directory-sibling/secret", b"ignored directory sibling\n")
        self.write("private-sibling/secret", b"ignored root sibling\n")

        observation = self.observe(selected, require_selected=True)

        included = {path: self.blob(payload) for path, payload in payloads.items() if path.startswith("private/")}
        raw = {path: self.raw(payload) for path, payload in payloads.items()}
        self.assert_fingerprint(observation, raw, included)
        self.assertEqual(observation["untrackedEntries"], included)
        self.assertEqual(set(observation["untracked"]) - set(observation["included"]), {"src/excluded.txt"})

    def test_capture_adds_only_nonignored_exact_files_in_scope(self):
        self.write("private/exact", b"explicit\n")
        self.write("private/exact-sibling", b"ignored sibling\n")
        self.write("src/new.txt", b"captured\r\n")
        self.write("src/new.ignored", b"ignored in scope\n")
        self.write("docs/new.txt", b"excluded\r\n")

        observation = self.observe(["private/exact"], capture=["src"], require_selected=True)

        included = {"private/exact": self.blob(b"explicit\n")}
        raw = {"private/exact": self.raw(b"explicit\n"), "src/new.txt": self.raw(b"captured\r\n"),
               "docs/new.txt": self.raw(b"excluded\r\n")}
        self.assert_fingerprint(observation, raw, included)
        self.assertEqual(observation["untrackedEntries"], {**included, "src/new.txt": self.blob(b"captured\n")})

    def test_staged_unstaged_and_deleted_tracked_entries_share_the_index(self):
        self.write("src/tracked.txt", b"staged\r\n")
        self.write("src/staged-new.txt", b"staged new\n")
        self.git("add", "src/tracked.txt", "src/staged-new.txt")
        original = workspace._entries(self.repo)
        self.write("src/tracked.txt", b"unstaged\r\n")
        (self.repo / "docs/tracked.txt").unlink()
        (self.repo / "src/staged-new.txt").unlink()
        self.write("private/selected", b"selected\n")

        observation = self.observe(["private/selected"], require_selected=True)

        tracked = {path: entry for path, entry in original.items()
                   if path not in {"docs/tracked.txt", "src/staged-new.txt"}}
        tracked["src/tracked.txt"] = self.blob(b"unstaged\n")
        included = {"private/selected": self.blob(b"selected\n")}
        self.assert_fingerprint(observation, {"private/selected": self.raw(b"selected\n")}, included,
                                tracked=tracked, index=original)
        self.assertEqual(original["src/tracked.txt"], self.blob(b"staged\n"))

    def test_empty_tracked_and_missing_selectors_do_not_select_untracked_input(self):
        for path in ("empty", "private/empty"):
            (self.repo / path).mkdir(parents=True)
        selected = ["empty", "private/empty", "src", "src/tracked.txt", "missing", "src/tracked.txt/child"]
        baseline = self.observe()
        self.assertEqual(self.observe(selected), baseline)
        for path in selected:
            with self.subTest(path=path), self.assertRaises(BoardError) as caught:
                self.observe([path], require_selected=True)
            self.assertEqual(caught.exception.code, "INVALID_WORKSPACE")
            self.assertEqual(caught.exception.details["path"], path)

    def test_selected_links_include_dangling_links_without_following_targets(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret").write_bytes(b"outside bytes\n")
        targets = {"private/file-link": str(outside / "secret"), "private/dangling": "missing-target",
                   "private/directory-link": str(outside)}
        for path, target in targets.items():
            link = self.repo / path
            link.parent.mkdir(parents=True, exist_ok=True)
            try:
                link.symlink_to(target, target_is_directory=path.endswith("directory-link"))
            except OSError as error:
                if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                    self.skipTest("Windows symlink privilege is unavailable")
                raise

        observation = self.observe(list(targets), require_selected=True)

        raw = {path: self.raw(os.fsencode(target), "120000") for path, target in targets.items()}
        included = self.stored_blobs(*targets)
        self.assertEqual(set(included), set(targets))
        self.assert_fingerprint(observation, raw, included)
        self.assertEqual((outside / "secret").read_bytes(), b"outside bytes\n")

    def test_spaces_apostrophes_and_leading_dash_are_literal(self):
        selected = ["private/space name", "private/apostrophe'name", "-leading.ignored"]
        for path in selected:
            self.write(path, b"selected\n")
        self.write("private/space name-sibling", b"ignored sibling\n")

        observation = self.observe(selected, require_selected=True)

        self.assert_fingerprint(observation, {path: self.raw(b"selected\n") for path in selected},
                                {path: self.blob(b"selected\n") for path in selected})

    def test_selection_below_a_directory_link_matches_literal_git_discovery(self):
        outside = self.root / "link-target"
        outside.mkdir()
        (outside / "file").write_bytes(b"outside bytes\n")
        link = self.repo / "private/link"
        link.parent.mkdir()
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            if os.name == "nt" and getattr(error, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege is unavailable")
            raise
        selector = "private/link/file"
        discovered = self.git("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", selector)
        self.assertEqual(discovered, b"")
        baseline = self.observe()

        try:
            observation = self.observe([selector])
        except BoardError as error:
            self.fail(f"Literal discovery omitted the selector, but observation failed: {error.payload()!r}")

        self.assertEqual(observation, baseline)
        with self.assertRaises(BoardError) as caught:
            self.observe([selector], require_selected=True)
        self.assertEqual(caught.exception.code, "INVALID_WORKSPACE")
        self.assertEqual(caught.exception.details["path"], selector)
        self.assertEqual((outside / "file").read_bytes(), b"outside bytes\n")

    @unittest.skipIf(os.name == "nt", "Windows filenames cannot contain these characters")
    def test_newlines_quotes_and_wildcards_are_literal(self):
        selected = ["private/line\nname", 'private/double"quote', "private/wild*?[name]",
                    "private/a'\nadd 'private/b"]
        for path in selected:
            self.write(path, b"selected\n")
        self.write("private/wildXan", b"ignored wildcard sibling\n")
        self.write("private/b", b"ignored sibling with a similar name\n")

        observation = self.observe(selected, require_selected=True)

        self.assert_fingerprint(observation, {path: self.raw(b"selected\n") for path in selected},
                                {path: self.blob(b"selected\n") for path in selected})

    def test_case_aliases_match_literal_git_discovery(self):
        self.write("private/Name", b"ignored bytes\n")
        self.write("src/New.txt", b"nonignored bytes\n")
        if not (self.repo / "PRIVATE/name").exists():
            self.skipTest("filesystem distinguishes case")
        for selector in ("PRIVATE", "private/name", "SRC/new.txt"):
            with self.subTest(selector=selector):
                nonignored = self.git("ls-files", "--others", "--exclude-standard", "-z")
                ignored = self.git("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", selector)
                expected_paths = {os.fsdecode(path) for path in (nonignored + ignored).split(b"\0") if path}
                observation = self.observe([selector])
                self.assertEqual(set(observation["untracked"]), expected_paths)
                raw = {path: self.raw((self.repo / path).read_bytes()) for path in expected_paths}
                included = {path: self.blob((self.repo / path).read_bytes()) for path in expected_paths
                            if workspace._in_scope(path, [selector])}
                self.assert_fingerprint(observation, raw, included)

    def test_selected_paths_exceed_native_argv_limit_but_use_bounded_commands(self):
        limit = 32767 if os.name == "nt" else os.sysconf("SC_ARG_MAX")
        width = len("private/00000-" + "p" * 190) + 1
        paths = [f"private/{number:05d}-" + "p" * 190 for number in range(limit // width + 32)]
        for path in paths:
            self.write(path, b"selected\n")
        # The last chunk's directory selector overlaps earlier chunks, and a
        # repeated exact selector must not duplicate the merged observation.
        selected = [*paths, "private", paths[0]]
        path_bytes = sum(len(os.fsencode(path)) + 1 for path in selected)
        self.assertGreater(path_bytes, limit)
        command, environment = workspace._git_command(
            self.repo, "ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--", *selected)
        with self.assertRaises(OSError) as caught:
            subprocess.run(command, capture_output=True, env=environment, timeout=120)
        if os.name == "nt":
            self.assertEqual(caught.exception.winerror, 206)
        else:
            self.assertEqual(caught.exception.errno, errno.E2BIG)
        original_git = workspace._git
        private_indexes = set()
        streamed_sizes = []
        selector_chunks = []

        def record_command(root, *args, **kwargs):
            command, environment = workspace._git_command(root, *args, git_dir=kwargs.get("git_dir"))
            environment.update(kwargs.get("env") or {})
            if os.name == "nt":
                units = len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 + 1
                self.assertLessEqual(units, limit - 260)
            else:
                strings = sum(len(os.fsencode(arg)) + 1 for arg in command)
                strings += sum(len(os.fsencode(key + "=" + value)) + 1 for key, value in environment.items())
                pointers = (len(command) + len(environment) + 2) * struct.calcsize("P")
                self.assertLessEqual(strings + pointers, limit - 2048)
            if args[0] in {"ls-files", "add", "update-index"}:
                private_indexes.add(kwargs["env"]["GIT_INDEX_FILE"])
            if args[:3] == ("ls-files", "--others", "--ignored"):
                selector_chunks.append(list(args[args.index("--") + 1:]))
            if args[:2] == ("add", "--force"):
                streamed_sizes.append(len(kwargs["data"]))
            return original_git(root, *args, **kwargs)

        with patch.object(workspace, "_git", side_effect=record_command), \
             patch.object(workspace, "_raw_entry", wraps=workspace._raw_entry) as raw_reads:
            observation = self.observe(selected)

        self.assertEqual(len(private_indexes), 1)
        self.assertGreater(len(selector_chunks), 1)
        self.assertEqual([path for chunk in selector_chunks for path in chunk], selected)
        self.assertGreater(max(streamed_sizes), limit)
        self.assertEqual(set(observation["included"]), set(paths))
        self.assertEqual(raw_reads.call_count, len(paths))
        raw_entry = self.raw(b"selected\n")
        git_entry = self.blob(b"selected\n")
        self.assert_fingerprint(observation, {path: raw_entry for path in paths},
                                {path: git_entry for path in paths})

    def test_windows_chunks_count_quoting_and_utf16_units(self):
        args = ("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--")
        selected = [f"private/{number}/" + '\U0001f642 space "quoted" \\' * 30
                    for number in range(100)]
        with patch.object(workspace, "_WINDOWS", True):
            chunks = list(workspace._git_path_chunks(self.repo, args, selected))
        self.assertGreater(len(chunks), 1)
        self.assertEqual([path for chunk in chunks for path in chunk], selected)
        for chunk in chunks:
            command, _environment = workspace._git_command(self.repo, *args, *chunk)
            units = len(subprocess.list2cmdline(command).encode("utf-16-le")) // 2 + 1
            self.assertLessEqual(units, 32767 - 260)

    @unittest.skipIf(os.name == "nt", "POSIX argv and environment share ARG_MAX")
    def test_posix_environment_reduces_the_available_path_budget(self):
        args = ("ls-files", "--others", "--ignored", "--exclude-standard", "-z", "--")
        selected = ["private/" + "p" * 190] * (os.sysconf("SC_ARG_MAX") // 198 + 32)
        normal = list(workspace._git_path_chunks(self.repo, args, selected))
        larger_environment = list(workspace._git_path_chunks(
            self.repo, args, selected, env={"W1_ARGV_BUDGET": "e" * 32768}))
        self.assertLess(len(larger_environment[0]), len(normal[0]))
        self.assertEqual([path for chunk in larger_environment for path in chunk], selected)


if __name__ == "__main__":
    unittest.main()
