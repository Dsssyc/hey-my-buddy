"""Private allocation ownership never crosses a link in the record namespace."""
import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest

from blackboard.tasks import test_workspace as fixtures
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import macro_task_deletion_workspace as deletion, workspace
from hey_my_buddy.errors import BoardError
from hey_my_buddy.paths import native_path


class PrivateAllocationPathTests(unittest.TestCase):
    setUp = fixtures.WorkspaceTests.setUp
    _seed_repository = fixtures.WorkspaceTests._seed_repository
    git = fixtures.WorkspaceTests.git
    intent = fixtures.WorkspaceTests.intent
    prepare = fixtures.WorkspaceTests.prepare

    def link_directory(self, link, target):
        if os.name == 'nt':
            result = subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J', str(link), str(target)],
                                    capture_output=True)
            self.assertEqual(result.returncode, 0, repr(result.stdout) + repr(result.stderr))
        else:
            link.symlink_to(target, target_is_directory=True)

    def unlink_directory(self, link):
        if os.name == 'nt':
            native_path(link).rmdir()
        else:
            link.unlink()

    def test_unavailable_repository_does_not_authorize_a_linked_record_parent(self):
        manifest = self.prepare()
        parent = self.state / 'workspaces'
        unrelated = self.root / 'unrelated-workspaces'
        parent.rename(unrelated)
        valuable = unrelated / manifest['workspaceId'] / 'valuable'
        valuable.write_bytes(b'outside the private record namespace')
        self.link_directory(parent, unrelated)
        retained_repository = self.root / 'moved-repository'
        self.repo.rename(retained_repository)
        try:
            with self.assertRaises(BoardError) as caught:
                deletion.delete_workspaces(SimpleNamespace(directory=self.state), [manifest], [])
            self.assertEqual(caught.exception.code, 'WORKSPACE_UNSAFE')
            self.assertEqual(valuable.read_bytes(), b'outside the private record namespace')
            self.assertIsNone(workspace.allocation_lock_directory(self.state, manifest['workspaceId']))
        finally:
            self.unlink_directory(parent)
            retained_repository.rename(self.repo)

    @unittest.skipUnless(os.name == 'nt', 'Windows dangling junction evidence')
    def test_dangling_allocation_junction_is_retained_as_unknown(self):
        manifest = self.prepare()
        directory = self.state / 'workspaces' / manifest['workspaceId']
        directory.rename(self.root / 'original-allocation')
        target = self.root / 'junction-target'
        target.mkdir()
        self.link_directory(directory, target)
        target.rmdir()
        try:
            skipped = deletion.delete_workspaces(SimpleNamespace(directory=self.state), [manifest], [])
            self.assertEqual([item['reason']['code'] for item in skipped],
                             ['macro-task-deletion.owner-unproven'])
            self.assertTrue(private_dirs.linked(directory))
            self.assertTrue(self.git('show-ref', '--verify', manifest['snapshot']['inputRef']))
        finally:
            self.unlink_directory(directory)
