"""Workspace snapshots use Git's path-aware index semantics in real repositories."""
import hashlib
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from temporary import TemporaryDirectory
from unittest.mock import patch

from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import workspace


LARGE_BINARY = b"large\x00\xff\r\n" + bytes(range(256)) * 8192


class GitIndexSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="hey-my-buddy-workspace-git-index-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.state = self.root / "state"

    def git(self, repo, *args, data=None):
        command, environment = workspace._git_command(repo, *args)
        result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=120)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def supported_formats(self):
        formats = ["sha1"]
        probe = self.root / "sha256-probe"
        probe.mkdir()
        result = subprocess.run(["git", "-C", str(probe), "init", "-q", "--object-format=sha256"],
                                capture_output=True)
        if result.returncode == 0 and self.git(probe, "rev-parse", "--show-object-format").strip() == b"sha256":
            formats.append("sha256")
        return formats

    def repository(self, name, *, object_format="sha1"):
        repo = self.root / name
        repo.mkdir()
        init = ["init", "-q"]
        if object_format == "sha256":
            init.append("--object-format=sha256")
        self.git(repo, *init)
        self.git(repo, "config", "user.name", "Git Index Test")
        self.git(repo, "config", "user.email", "index@example.invalid")
        (repo / "src").mkdir()
        (repo / "src/text.txt").write_bytes(b"base\n")
        (repo / "src/binary.bin").write_bytes(b"\x00base\xff")
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "base")
        return repo

    def intent(self, repo, selected=()):
        return {"kind": "worktree", "cwd": str(repo), "access": "write", "base": {"kind": "working-tree"},
                "writeScope": ["src"], "integrator": {"role": "host", "id": "host-1"},
                "includeUntracked": list(selected)}

    def tree_entries(self, repo, treeish):
        entries = {}
        for record in self.git(repo, "ls-tree", "-r", "-z", treeish).split(b"\0"):
            if record:
                metadata, _, name = record.partition(b"\t")
                mode, _kind, oid = metadata.decode().split()
                entries[os.fsdecode(name)] = (mode, oid)
        return entries

    def source_state(self, repo):
        refs = self.git(repo, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()
        return (self.git(repo, "rev-parse", "HEAD"), (repo / ".git/index").read_bytes(),
                tuple(line for line in refs if not line.startswith(b"refs/hey-my-buddy/")),
                self.git(repo, "stash", "list"))

    def test_normalized_input_and_output_match_git_for_sha1_and_sha256(self):
        for object_format in self.supported_formats():
            with self.subTest(object_format=object_format):
                repo = self.repository(object_format + "-content", object_format=object_format)
                (repo / ".gitattributes").write_text("src/*.txt text\n", encoding="utf-8", newline="\n")
                self.git(repo, "add", ".gitattributes")
                self.git(repo, "commit", "-qm", "attributes")
                (repo / "src/text.txt").write_bytes(b"staged\r\n")
                self.git(repo, "add", "src/text.txt")
                (repo / "src/text.txt").write_bytes(b"unstaged\r\nsecond\r\n")
                (repo / "src/binary.bin").write_bytes(LARGE_BINARY)
                (repo / "src/selected.txt").write_bytes(b"selected\r\n")
                (repo / "src/excluded.txt").write_bytes(b"excluded\r\n")
                before = self.source_state(repo)
                manifest = workspace.prepare(self.state, object_format, self.intent(repo, ["src/selected.txt"]))
                self.assertEqual(self.source_state(repo), before)
                staged = manifest["snapshot"]["stagedCommit"]
                self.assertEqual(self.git(repo, "show", staged + ":src/text.txt"), b"staged\n")
                self.assertEqual(self.git(repo, "show", manifest["inputCommit"] + ":src/text.txt"),
                                 b"unstaged\nsecond\n")
                self.assertEqual(self.git(repo, "show", manifest["inputCommit"] + ":src/selected.txt"),
                                 b"selected\n")
                self.assertEqual(self.git(repo, "show", manifest["inputCommit"] + ":src/binary.bin"),
                                 LARGE_BINARY)
                self.assertNotIn("src/excluded.txt", self.tree_entries(repo, manifest["inputTree"]))
                self.assertEqual(manifest["snapshot"]["excludedEntries"]["src/excluded.txt"],
                                 ["100644", hashlib.sha256(b"excluded\r\n").hexdigest()])
                self.assertEqual(len(self.tree_entries(repo, manifest["inputTree"])["src/binary.bin"][1]),
                                 64 if object_format == "sha256" else 40)
                self.assertTrue(workspace.verify(manifest, state_dir=self.state)["unchanged"])
                checkout = Path(manifest["checkoutRoot"])
                (checkout / "src/text.txt").write_bytes(b"output\r\n")
                (checkout / "src/binary.bin").write_bytes(LARGE_BINARY + b"\x00output")
                (checkout / "src/new.txt").write_bytes(b"new\r\n")
                output = workspace.seal(self.state, manifest, "task", object_format)
                self.assertEqual(self.git(repo, "show", output["commit"] + ":src/text.txt"), b"output\n")
                self.assertEqual(self.git(repo, "show", output["commit"] + ":src/new.txt"), b"new\n")
                self.assertEqual(self.git(repo, "show", output["commit"] + ":src/binary.bin"),
                                 LARGE_BINARY + b"\x00output")
                self.assertEqual(workspace.verify_sealed_output(manifest, manifest, output)["changedPaths"],
                                 ["src/binary.bin", "src/new.txt", "src/text.txt"])
                self.assertEqual(self.source_state(repo), before)

    def test_private_index_stays_under_state_tmp_and_is_removed(self):
        repo = self.repository("private-index")
        worker_tmp = self.root / "worker-tmp"
        worker_tmp.mkdir()
        created = []
        allocate = private_dirs.make_temporary_dir

        def capture(*, prefix, directory):
            path = allocate(prefix=prefix, directory=directory)
            created.append(path)
            return path

        with patch.dict(os.environ, {"TMPDIR": str(worker_tmp), "TEMP": str(worker_tmp), "TMP": str(worker_tmp)}), \
                patch.object(private_dirs, "make_temporary_dir", side_effect=capture):
            manifest = workspace.prepare(self.state, "private-index", self.intent(repo))
            (Path(manifest["checkoutRoot"]) / "src/text.txt").write_bytes(b"changed\n")
            workspace.seal(self.state, manifest, "task", "private-index")
        self.assertTrue(any(path.name.startswith("hey-my-buddy-index-") for path in created))
        self.assertTrue(all(path.parent == self.state / "tmp" for path in created))
        self.assertEqual(list((self.state / "tmp").iterdir()), [])
        self.assertEqual(list(worker_tmp.iterdir()), [])

    def git_process_counter(self, tag):
        real = shutil.which("git")
        self.assertTrue(real, "a real Git binary is required")
        log = self.root / f"git-count-{tag}.log"
        wrapper = self.root / f"git-counter-{tag}.py"
        wrapper.write_text(
            "import subprocess, sys\n"
            f"with open({str(log)!r}, 'a', encoding='utf-8', newline='\\n') as stream:\n"
            "    stream.write('x\\n')\n"
            f"raise SystemExit(subprocess.call([{real!r}, *sys.argv[1:]]))\n",
            encoding="utf-8", newline="\n")
        return wrapper, log

    def count_git_processes(self, tag, operation, *args, **kwargs):
        wrapper, log = self.git_process_counter(tag)
        git_command = workspace._git_command

        def counted_command(*arguments, **options):
            command, environment = git_command(*arguments, **options)
            return [sys.executable, str(wrapper), *command[1:]], environment

        with patch.object(workspace, "_git_command", side_effect=counted_command):
            result = operation(*args, **kwargs)
        return len(log.read_text(encoding="utf-8").splitlines()), result

    def scaling_checkout(self, name, count):
        repo = self.repository(name)
        # repository() contributes two tracked files, so this makes exactly
        # count tracked files in each checkout.
        for number in range(count - 2):
            (repo / "src" / f"file-{number}.txt").write_bytes(f"base {number}\n".encode())
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "scaling base")
        for number in range(min(count - 2, 20)):
            (repo / "src" / f"file-{number}.txt").write_bytes(f"changed {number}\n".encode())
        selected = []
        for number in range(5):
            path = f"src/selected-{number}.txt"
            (repo / path).write_bytes(f"selected {number}\n".encode())
            selected.append(path)
        (repo / "src/excluded.txt").write_bytes(b"excluded\n")
        return repo, selected

    def test_git_process_count_does_not_grow_from_8_to_800_files(self):
        small_repo, small_selected = self.scaling_checkout("small", 8)
        large_repo, large_selected = self.scaling_checkout("large", 800)
        small, small_manifest = self.count_git_processes(
            "prepare-small", workspace.prepare, self.state, "small", self.intent(small_repo, small_selected))
        large, large_manifest = self.count_git_processes(
            "prepare-large", workspace.prepare, self.state, "large", self.intent(large_repo, large_selected))
        self.assertGreater(small, 0)
        self.assertLessEqual(large - small, 8, (small, large))
        small_verify, _ = self.count_git_processes("verify-small", workspace.verify, small_manifest, state_dir=self.state)
        large_verify, _ = self.count_git_processes("verify-large", workspace.verify, large_manifest, state_dir=self.state)
        self.assertLessEqual(large_verify - small_verify, 8, (small_verify, large_verify))
        for manifest in (small_manifest, large_manifest):
            checkout = Path(manifest["checkoutRoot"])
            (checkout / "src/file-0.txt").write_bytes(b"sealed output\n")
            (checkout / "src/output.txt").write_bytes(b"new output\n")
        small_seal, small_output = self.count_git_processes(
            "seal-small", workspace.seal, self.state, small_manifest, "task", "small")
        large_seal, large_output = self.count_git_processes(
            "seal-large", workspace.seal, self.state, large_manifest, "task", "large")
        self.assertLessEqual(large_seal - small_seal, 8, (small_seal, large_seal))
        self.assertEqual(small_output["changedPaths"], ["src/file-0.txt", "src/output.txt"])
        self.assertEqual(large_output["changedPaths"], ["src/file-0.txt", "src/output.txt"])


if __name__ == "__main__":
    unittest.main()
