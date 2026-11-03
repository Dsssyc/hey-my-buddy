"""Private allocation deletion accepts linked ancestors and rejects linked leaves."""
import os
from pathlib import Path
import subprocess
import unittest

from blackboard.tasks import test_workspace as fixtures
from hey_my_buddy import private_dirs
from hey_my_buddy.blackboard.tasks import macro_task_deletion_workspace as deletion, workspace
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

    def test_unavailable_repository_is_deleted_through_a_linked_record_parent(self):
        manifest = self.prepare()
        parent = self.state / 'workspaces'
        relocated = self.root / 'relocated-workspaces'
        parent.rename(relocated)
        self.link_directory(parent, relocated)
        directory = parent / manifest['workspaceId']
        (directory / 'private-bytes').write_bytes(b'owned allocation content')
        retained_repository = self.root / 'moved-repository'
        self.repo.rename(retained_repository)
        try:
            refs_before = self.git('show-ref', cwd=retained_repository)
            self.assertEqual(workspace.allocation_lock_directory(self.state, manifest['workspaceId']), directory)
            self.assertEqual(workspace.resolve_allocation(self.state, manifest)['workspaceId'], manifest['workspaceId'])

            store, owners, records = fixtures.deletion_sql_inputs(self, [manifest])
            skipped = deletion.delete_workspaces(store, owners, records)

            self.assertEqual(skipped, [])
            self.assertEqual(list(relocated.iterdir()), [])
            self.assertTrue(private_dirs.linked(parent))
            self.assertEqual(self.git('show-ref', cwd=retained_repository), refs_before)
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
            store, owners, records = fixtures.deletion_sql_inputs(self, [manifest])
            skipped = deletion.delete_workspaces(store, owners, records)
            self.assertEqual([item['reason']['code'] for item in skipped],
                             ['macro-task-deletion.owner-unproven'])
            self.assertTrue(private_dirs.linked(directory))
            self.assertTrue(self.git('show-ref', '--verify', manifest['snapshot']['inputRef']))
        finally:
            self.unlink_directory(directory)
