"""Native Windows checkout failures reported by workspace preparation."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from temporary import TemporaryDirectory

from hey_my_buddy.blackboard.tasks import workspace
from hey_my_buddy.errors import BoardError


def _intent(repository: Path, commit: str) -> dict:
    return {"kind": "worktree", "cwd": str(repository), "access": "write",
            "base": {"kind": "commit", "ref": commit}, "includeUntracked": [],
            "writeScope": ["src"], "integrator": {"role": "host", "id": "host-1"}}


def _git(repository: Path, *args: str, data: bytes | None = None) -> bytes:
    command, environment = workspace._git_command(repository, *args)
    result = subprocess.run(command, input=data, capture_output=True, env=environment, timeout=120)
    if result.returncode:
        raise AssertionError(f"Git {args[0]} failed: {result.stderr.decode(errors='replace')}")
    return result.stdout


def _cause(error: BoardError) -> dict:
    cause = error.__cause__
    return {"code": getattr(cause, "code", None),
            "operation": getattr(cause, "details", {}).get("operation")}


def _remove_own_symlink_privilege() -> None:
    """Permanently remove one privilege from this short-lived child token only."""
    class Luid(ctypes.Structure):
        _fields_ = [("LowPart", wintypes.DWORD), ("HighPart", wintypes.LONG)]

    class LuidAndAttributes(ctypes.Structure):
        _fields_ = [("Luid", Luid), ("Attributes", wintypes.DWORD)]

    class TokenPrivileges(ctypes.Structure):
        _fields_ = [("PrivilegeCount", wintypes.DWORD),
                    ("Privileges", LuidAndAttributes * 1)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    security.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                          ctypes.POINTER(wintypes.HANDLE)]
    security.OpenProcessToken.restype = wintypes.BOOL
    security.LookupPrivilegeValueW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR,
                                                ctypes.POINTER(Luid)]
    security.LookupPrivilegeValueW.restype = wintypes.BOOL
    security.AdjustTokenPrivileges.argtypes = [wintypes.HANDLE, wintypes.BOOL,
                                               ctypes.POINTER(TokenPrivileges), wintypes.DWORD,
                                               ctypes.c_void_p, ctypes.c_void_p]
    security.AdjustTokenPrivileges.restype = wintypes.BOOL

    token = wintypes.HANDLE()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 0x0028, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        luid = Luid()
        if not security.LookupPrivilegeValueW(None, "SeCreateSymbolicLinkPrivilege", ctypes.byref(luid)):
            raise ctypes.WinError(ctypes.get_last_error())
        privileges = TokenPrivileges()
        privileges.PrivilegeCount = 1
        privileges.Privileges[0].Luid = luid
        privileges.Privileges[0].Attributes = 0x00000004  # SE_PRIVILEGE_REMOVED
        ctypes.set_last_error(0)
        if not security.AdjustTokenPrivileges(token, False, ctypes.byref(privileges), 0, None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        # ERROR_NOT_ALL_ASSIGNED means this child never held the privilege.
        status = ctypes.get_last_error()
        if status not in (0, 1300):
            raise ctypes.WinError(status)
    finally:
        kernel.CloseHandle(token)


def _restricted_child(state: Path, repository: Path, commit: str) -> None:
    _remove_own_symlink_privilege()
    probe = state.parent / "symlink-permission-probe"
    try:
        probe.symlink_to("target.txt")
    except OSError as error:
        if getattr(error, "winerror", None) != 1314:
            raise
    else:
        probe.unlink()
        print(json.dumps({"kind": "unprivileged-symlink-allowed"}))
        return

    try:
        workspace.prepare(state, "symlink-denied", _intent(repository, commit))
    except BoardError as error:
        print(json.dumps({"kind": "error", "error": error.payload(), "cause": _cause(error)}))
    else:
        print(json.dumps({"kind": "unexpected-success"}))


@unittest.skipUnless(os.name == "nt", "native Windows checkout behavior")
class NativeWindowsWorkspaceCheckoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="hey-my-buddy-win-checkout-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repository = self.root / "repo"
        self.repository.mkdir()
        self.state = self.root / "state"
        _git(self.repository, "init", "-q")
        _git(self.repository, "config", "user.name", "Windows Checkout Test")
        _git(self.repository, "config", "user.email", "checkout@example.invalid")
        _git(self.repository, "config", "core.protectNTFS", "false")

    def commit_index(self, mode: str, path: str, content: bytes) -> str:
        oid = _git(self.repository, "hash-object", "-w", "--stdin", data=content).strip()
        _git(self.repository, "update-index", "--index-info",
             data=mode.encode() + b" " + oid + b"\t" + os.fsencode(path) + b"\n")
        tree = _git(self.repository, "write-tree").strip().decode("ascii")
        commit = _git(self.repository, "commit-tree", tree,
                      data=b"Windows checkout fixture\n").strip().decode("ascii")
        _git(self.repository, "update-ref", "HEAD", commit)
        return commit

    def assert_unsupported(self, error: BoardError, path: str) -> None:
        self.assertEqual(error.code, "WORKSPACE_UNSUPPORTED", error.payload())
        self.assertIn(path, error.details["paths"])
        self.assertTrue(error.details["reason"])
        self.assertEqual(_cause(error), {"code": "WORKSPACE_GIT_ERROR", "operation": "reset"})

    def test_reserved_name_reset_failure_is_reported_by_prepare(self):
        commit = self.commit_index("100644", "CON.txt", b"cannot write this name\n")
        _git(self.repository, "config", "core.protectNTFS", "true")
        with self.assertRaises(BoardError) as caught:
            workspace.prepare(self.state, "reserved-name", _intent(self.repository, commit))
        self.assert_unsupported(caught.exception, "CON.txt")

    def test_denied_symlink_reset_failure_is_reported_by_prepare(self):
        _git(self.repository, "config", "core.symlinks", "true")
        target = self.repository / "src" / "target.txt"
        target.parent.mkdir()
        target.write_bytes(b"target\n")
        _git(self.repository, "add", "src/target.txt")
        commit = self.commit_index("120000", "src/link", b"target.txt")
        child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--restricted-child",
                                str(self.state), str(self.repository), commit],
                               capture_output=True, text=True, timeout=180)
        self.assertEqual(child.returncode, 0, child.stderr)
        result = json.loads(child.stdout)
        if result["kind"] == "unprivileged-symlink-allowed":
            self.skipTest("Windows permits symbolic links without SeCreateSymbolicLinkPrivilege")
        self.assertEqual(result["kind"], "error", result)
        error = result["error"]
        self.assertEqual(error["code"], "WORKSPACE_UNSUPPORTED", result)
        self.assertIn("src/link", error["details"]["paths"])
        self.assertTrue(error["details"]["reason"])
        self.assertEqual(result["cause"], {"code": "WORKSPACE_GIT_ERROR", "operation": "reset"})


if __name__ == "__main__":
    if sys.argv[1:2] == ["--restricted-child"]:
        _restricted_child(Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4])
    else:
        unittest.main()
