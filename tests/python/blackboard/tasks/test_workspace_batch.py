"""Batched workspace object numbering and storage, tested against real Git repositories.

The batch must keep the exact per-file ``git hash-object --no-filters --stdin``
contract: identities cover the already-read bytes with no path, filter or CRLF
conversion involved, written objects live in the same repository and stay
readable, and no ref, branch, tag or stash is created. The structural
regression pins the Git process count so removing the batching or changing the
byte handling fails here.
"""
import hashlib
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hey_my_buddy.blackboard.tasks import workspace


#: Content shapes whose numbering must not depend on how they reach Git.
PAYLOADS = (
    b"",
    b"\n",
    b"plain text\n",
    b"crlf line\r\nsecond line\r\n",
    b"binary\x00\xff\xfe\x00\r\n\x00tail",
    "unicode ✓ accented é\n".encode(),
    b"no trailing newline",
    b"repeat block\n" * 4096,
    b"large\n" + bytes(range(256)) * 8192,
)


class BatchedObjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="buddy-workspace-batch-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.state = self.root / "state"
        self.git("init", "-q")
        self.git("config", "user.name", "Workspace Batch Test")
        self.git("config", "user.email", "batch@example.invalid")

    def git(self, *args, cwd=None, data=None, check=True):
        env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
        result = subprocess.run(["git", "-C", str(cwd or self.repo), *args], input=data,
                                capture_output=True, env=env, timeout=120)
        if check:
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        return result.stdout

    def reference_oids(self, repo, payloads):
        """The old per-file contract: one hash-object run per payload."""
        return [self.git("hash-object", "--no-filters", "--stdin", cwd=repo, data=payload).decode().strip()
                for payload in payloads]

    def supported_formats(self):
        formats = ["sha1"]
        probe = self.root / "sha256-probe"
        probe.mkdir()
        result = subprocess.run(["git", "-C", str(probe), "init", "-q", "--object-format=sha256"],
                                capture_output=True)
        if result.returncode == 0 and self.git("rev-parse", "--show-object-format",
                                               cwd=probe).decode().strip() == "sha256":
            formats.append("sha256")
        return formats

    def tree_entries(self, repo, treeish):
        entries = {}
        for record in self.git("ls-tree", "-r", "-z", treeish, cwd=repo).split(b"\0"):
            if record:
                metadata, _, name = record.partition(b"\t")
                mode, _kind, oid = metadata.decode().split()
                entries[os.fsdecode(name)] = (mode, oid)
        return entries

    def mixed_checkout(self, name, object_format=None, autocrlf=False):
        repo = self.root / name
        repo.mkdir()
        args = ["init", "-q"]
        if object_format:
            args.append("--object-format=" + object_format)
        self.git(*args, cwd=repo)
        self.git("config", "user.name", "Batch Checkout Test", cwd=repo)
        self.git("config", "user.email", "checkout@example.invalid", cwd=repo)
        if autocrlf:
            self.git("config", "core.autocrlf", "true", cwd=repo)
        (repo / "src").mkdir()
        if autocrlf:
            (repo / ".gitattributes").write_text("* text=auto\n")
            self.git("add", ".gitattributes", cwd=repo)
        tracked = {"src/kept.txt": b"kept\n", "src/crlf.txt": b"crlf\r\nlines\r\n",
                   "src/binary.bin": b"\x00\xff\x10\r\n\x00", "src/empty.dat": b""}
        for path, payload in tracked.items():
            (repo / path).write_bytes(payload)
        self.git("add", "-A", cwd=repo)
        self.git("commit", "-qm", "mixed base", cwd=repo)
        (repo / "src/selected.bin").write_bytes(b"selected\x00\xfe\r\n")
        return repo, tracked

    def worktree_intent(self, repo, selected=()):
        return {"kind": "worktree", "cwd": str(repo), "access": "write", "base": {"kind": "working-tree"},
                "writeScope": ["src"], "integrator": "host", "includeUntracked": list(selected)}

    def object_files(self, repo):
        return sorted(str(path.relative_to(repo)) for path in (repo / ".git" / "objects").rglob("*")
                      if path.is_file())

    # -- object numbering ------------------------------------------------------
    def test_batch_identities_match_hash_object_for_every_content_kind(self):
        sha1_expected = self.reference_oids(self.repo, PAYLOADS)
        self.assertEqual(workspace._batch_blobs(self.repo, list(PAYLOADS), write=False), sha1_expected)
        self.assertEqual(workspace._blob(self.repo, PAYLOADS[3], write=False), sha1_expected[3])
        self.assertEqual(workspace._batch_blobs(self.repo, [], write=False), [])
        if "sha256" in self.supported_formats():
            repo = self.root / "repo-sha256"
            repo.mkdir()
            self.git("init", "-q", "--object-format=sha256", cwd=repo)
            expected = self.reference_oids(repo, PAYLOADS)
            self.assertEqual(workspace._batch_blobs(repo, list(PAYLOADS), write=False), expected)
            # The repository's object format decides the number; no length is hardcoded.
            self.assertEqual(len(expected[0]), 64)
            self.assertNotEqual(expected[0], sha1_expected[0])

    def test_batch_write_stores_objects_in_the_same_repository_without_refs(self):
        refs_before = self.git("for-each-ref", "--format=%(refname)")
        objects_before = self.object_files(self.repo)
        oids = workspace._batch_blobs(self.repo, list(PAYLOADS), write=True)
        self.assertEqual(oids, self.reference_oids(self.repo, PAYLOADS))
        for oid, payload in zip(oids, PAYLOADS):
            self.assertEqual(self.git("cat-file", "-t", oid), b"blob\n")
            self.assertEqual(self.git("cat-file", "blob", oid), payload)
        self.assertEqual(self.git("for-each-ref", "--format=%(refname)"), refs_before)
        self.assertEqual(self.git("stash", "list"), b"")
        self.assertNotEqual(self.object_files(self.repo), objects_before, "write=True must store objects")
        # A second write finds every object already present and stores nothing again.
        objects_after_first = self.object_files(self.repo)
        self.assertEqual(workspace._batch_blobs(self.repo, list(PAYLOADS), write=True), oids)
        self.assertEqual(self.object_files(self.repo), objects_after_first)

    def test_object_format_and_unknown_format_fail_closed(self):
        with self.assertRaises(workspace.BoardError) as caught:
            workspace._blob_oid(b"data", "stretched256")
        self.assertEqual(caught.exception.code, "WORKSPACE_UNSUPPORTED")
        self.assertEqual(workspace._blob_oid(b"data", "sha256"),
                         hashlib.sha256(b"blob 4\x00data").hexdigest())

    # -- filters and autocrlf --------------------------------------------------
    def test_filtered_path_hashing_never_decides_the_numbering(self):
        (self.repo / ".gitattributes").write_text("* text=auto\n")
        self.git("add", ".gitattributes")
        self.git("commit", "-qm", "attributes")
        self.git("config", "core.autocrlf", "true")
        payload = b"crlf\r\nlines\r\n"
        unfiltered = self.reference_oids(self.repo, [payload])[0]
        # The path-driven filtered reference is exactly what the batch must not use.
        filtered = self.git("hash-object", "--stdin", "--path=crlf.txt", data=payload).decode().strip()
        self.assertNotEqual(unfiltered, filtered, "the fixture must make the filtered path differ")
        self.assertEqual(workspace._batch_blobs(self.repo, [payload], write=True), [unfiltered])
        self.assertEqual(workspace._blob(self.repo, payload, write=True), unfiltered)
        self.assertEqual(self.git("cat-file", "blob", unfiltered), payload)

    def test_prepare_verify_and_seal_bytes_match_per_file_hash_object(self):
        repo, tracked = self.mixed_checkout("filtered-checkout", autocrlf=True)
        manifest = workspace.prepare(self.state, "parity", self.worktree_intent(repo, ["src/selected.bin"]))
        entries = self.tree_entries(repo, manifest["inputTree"])
        for path in (*tracked, "src/selected.bin"):
            payload = (repo / path).read_bytes()
            reference = self.reference_oids(repo, [payload])[0]
            self.assertEqual(entries[path], ("100644", reference), path)
        self.assertTrue(workspace.verify(manifest)["unchanged"])
        target = Path(manifest["path"])
        changed = {"src/kept.txt": b"changed output\r\n", "src/new.txt": b"new\r\noutput\x00"}
        for path, payload in changed.items():
            (target / path).write_bytes(payload)
        output = workspace.seal(self.state, manifest, "task", "attempt")
        entries = self.tree_entries(repo, output["tree"])
        for path, payload in changed.items():
            reference = self.reference_oids(repo, [payload])[0]
            self.assertEqual(entries[path], ("100644", reference), path)
            self.assertEqual(self.git("show", output["commit"] + ":" + path, cwd=repo), payload)
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, output)["changedPaths"],
                         ["src/kept.txt", "src/new.txt"])

    def test_sha256_repository_roundtrip_uses_full_length_numbers(self):
        if "sha256" not in self.supported_formats():
            self.skipTest("this Git does not support the sha256 object format")
        repo, tracked = self.mixed_checkout("sha256-checkout", object_format="sha256")
        manifest = workspace.prepare(self.state, "sha256", self.worktree_intent(repo, ["src/selected.bin"]))
        entries = self.tree_entries(repo, manifest["inputTree"])
        for path in (*tracked, "src/selected.bin"):
            payload = (repo / path).read_bytes()
            reference = self.reference_oids(repo, [payload])[0]
            self.assertEqual(len(reference), 64, path)
            self.assertEqual(entries[path], ("100644", reference), path)
        target = Path(manifest["path"])
        (target / "src/kept.txt").write_bytes(b"sha256 output\x00\r\n")
        output = workspace.seal(self.state, manifest, "task", "attempt")
        reference = self.reference_oids(repo, [b"sha256 output\x00\r\n"])[0]
        self.assertEqual(self.tree_entries(repo, output["tree"])["src/kept.txt"], ("100644", reference))
        self.assertEqual(workspace.verify_sealed_output(manifest, manifest, output)["changedPaths"],
                         ["src/kept.txt"])

    # -- observation of mixed modes --------------------------------------------
    def test_observe_numbers_mixed_content_and_modes_like_hash_object(self):
        payloads = {"text.txt": b"text\n", "crlf.txt": b"crlf\r\nline\r\n",
                    "binary.bin": b"\x00\x01\xff\xfe\x00", "empty.dat": b"", "large.bin": PAYLOADS[-1]}
        for path, payload in payloads.items():
            (self.repo / path).write_bytes(payload)
        (self.repo / "script.sh").write_bytes(b"#!/bin/sh\necho hi\n")
        (self.repo / "script.sh").chmod(0o755)
        (self.repo / "link").symlink_to("text.txt")
        self.git("add", "-A")
        self.git("commit", "-qm", "mixed modes")
        # Git numbers a symlink by its target string, not by the target's content.
        contents = {path: (self.repo / path).read_bytes() for path in payloads}
        contents["script.sh"] = (self.repo / "script.sh").read_bytes()
        contents["link"] = os.fsencode(os.readlink(self.repo / "link"))
        expected = {path: self.reference_oids(self.repo, [payload])[0] for path, payload in contents.items()}
        observed = workspace._observe(self.repo, [])
        for path, oid in expected.items():
            self.assertEqual(observed["tracked"][path][1], oid, path)
        self.assertEqual(observed["tracked"]["script.sh"][0], "100755")
        self.assertEqual(observed["tracked"]["link"][0], "120000")
        written = workspace._observe(self.repo, [], write=True)
        self.assertEqual(written["tracked"], observed["tracked"])
        for path, oid in expected.items():
            self.assertEqual(self.git("cat-file", "blob", oid), contents[path])

    # -- structural process-count regression -----------------------------------
    def git_process_counter(self, tag):
        """A PATH shim that logs every Git execution and forwards to the real Git."""
        real = shutil.which("git")
        self.assertTrue(real, "a real git binary is required")
        bin_dir = self.root / f"git-bin-{tag}"
        bin_dir.mkdir()
        log = self.root / f"git-count-{tag}.log"
        script = "#!/bin/sh\nprintf 'x\\n' >> %s\nexec %s \"$@\"\n" % (shlex.quote(str(log)), shlex.quote(real))
        wrapper = bin_dir / "git"
        wrapper.write_text(script)
        wrapper.chmod(0o755)
        return bin_dir, log

    def count_git_processes(self, tag, operation, *args, **kwargs):
        bin_dir, log = self.git_process_counter(tag)
        shadowed = {"PATH": str(bin_dir) + os.pathsep + os.environ.get("PATH", ""), "W1_GIT_LOG": str(log)}
        with patch.dict(os.environ, shadowed):
            result = operation(*args, **kwargs)
        return len(log.read_text().splitlines()), result

    def build_scaling_checkout(self, name, tracked, modified, untracked):
        repo = self.root / name
        repo.mkdir()
        self.git("init", "-q", cwd=repo)
        self.git("config", "user.name", "Scaling Test", cwd=repo)
        self.git("config", "user.email", "scaling@example.invalid", cwd=repo)
        (repo / "src").mkdir()
        for number in range(tracked):
            (repo / "src" / f"file-{number}.txt").write_bytes(f"tracked {number}\n".encode())
        self.git("add", "-A", cwd=repo)
        self.git("commit", "-qm", "base", cwd=repo)
        for number in range(modified):
            (repo / "src" / f"file-{number}.txt").write_bytes(f"modified {number}\n".encode())
        selected = []
        for number in range(untracked):
            path = f"src/new-{number}.txt"
            (repo / path).write_bytes(f"untracked {number}\n".encode())
            selected.append(path)
        return repo, selected

    def test_git_process_count_does_not_grow_from_small_checkout_to_800_files(self):
        small_repo, small_selected = self.build_scaling_checkout("small-checkout", 12, 2, 2)
        large_repo, large_selected = self.build_scaling_checkout("large-checkout", 800, 20, 5)
        small_count, small_manifest = self.count_git_processes(
            "p-small", workspace.prepare, self.state, "small-request", self.worktree_intent(small_repo, small_selected))
        large_count, large_manifest = self.count_git_processes(
            "p-large", workspace.prepare, self.state, "large-request", self.worktree_intent(large_repo, large_selected))
        verify_small, _ = self.count_git_processes("v-small", workspace.verify, small_manifest)
        verify_large, _ = self.count_git_processes("v-large", workspace.verify, large_manifest)
        for manifest, tag in ((small_manifest, "small"), (large_manifest, "large")):
            target = Path(manifest["path"])
            (target / "src/file-0.txt").write_bytes(b"sealed output\n")
            (target / f"src/output-{tag}.txt").write_bytes(b"new output\n")
        seal_small, small_output = self.count_git_processes(
            "s-small", workspace.seal, self.state, small_manifest, "task", "attempt")
        seal_large, large_output = self.count_git_processes(
            "s-large", workspace.seal, self.state, large_manifest, "task", "attempt")
        # The shim must actually observe the operation's processes.
        probe_bin, probe_log = self.git_process_counter("probe")
        with patch.dict(os.environ, {"PATH": str(probe_bin) + os.pathsep + os.environ["PATH"],
                                     "W1_GIT_LOG": str(probe_log)}):
            subprocess.run(["git", "--version"], capture_output=True, check=True)
        self.assertEqual(probe_log.read_text().count("\n"), 1)
        self.assertGreaterEqual(small_count, 10)
        counts = {"prepare": (small_count, large_count), "verify": (verify_small, verify_large),
                  "seal": (seal_small, seal_large)}
        for operation, (small, large) in counts.items():
            self.assertLessEqual(large - small, 8,
                                 f"{operation}: git processes grew with the checkout file count "
                                 f"(small={small}, large={large})")
        self.assertEqual(small_output["changedPaths"], ["src/file-0.txt", "src/output-small.txt"])
        self.assertEqual(large_output["changedPaths"], ["src/file-0.txt", "src/output-large.txt"])


if __name__ == "__main__":
    unittest.main()
