"""Retired Host caches cannot hold a verified installation behind its marker."""
from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from support import BoardTestCase
from hey_my_buddy.errors import BoardError
from hey_my_buddy.install import upgrade


class HostRetirementTests(BoardTestCase):
    def test_recovery_finishes_despite_a_retired_directory_cleanup_failure(self):
        state = self.directory
        runtimes = state / "runtimes"
        previous,target = runtimes / "old",runtimes / "new"
        previous.mkdir(parents=True)
        target.mkdir()
        journal = {"phase":"verified","contractChanged":True,
                   "previous":str(previous),"target":str(target),
                   "before":{"schema":34},"expected":{"schema":35}}
        for failure in (PermissionError("Retired cache is not writable"),
                        BoardError("PRIVATE_PATH_UNSAFE","Retired directory is linked")):
            with self.subTest(failure=type(failure).__name__):
                upgrade.write_journal(state / "upgrade.json",journal)
                with mock.patch.object(upgrade.runtime,"is_ready",return_value=True), \
                     mock.patch.object(upgrade,"probe",side_effect=[{"ready":True},None]), \
                     mock.patch.object(upgrade,"verify_started",return_value={"verified":True}), \
                     mock.patch.object(upgrade,"_verify_skill_generation"), \
                     mock.patch.object(upgrade,"write_active_runtime"), \
                     mock.patch.object(upgrade,"retain_runtimes",return_value={}), \
                     mock.patch.object(upgrade,"_retire_host_files",side_effect=failure):
                    result = upgrade.recover(state,runtimes)
                self.assertTrue(result["upgraded"])
                self.assertTrue(result["recoveredJournal"])
                self.assertIn("hostCleanupError",result)
                self.assertFalse((state / "upgrade.json").exists())
                written = json.loads((state / "upgrade-last.json").read_text())
                self.assertEqual(written["phase"],"verified")
                self.assertEqual(written["hostCleanupError"],result["hostCleanupError"])

    def test_subsequent_contract_changes_do_not_repeat_schema_35_retirement(self):
        with mock.patch.object(upgrade,"_retire_host_files",side_effect=AssertionError("already retired")):
            self.assertEqual(upgrade._finish_host_retirement(self.directory,{"before":{"schema":35}}),{})
            self.assertEqual(upgrade._finish_host_retirement(self.directory,{"before":{"schema":36}}),{})

    def test_retirement_refuses_a_link_without_touching_its_target(self):
        target = self.directory / "unrelated"
        target.mkdir()
        evidence = target / "keep.txt"
        evidence.write_text("keep",encoding="utf-8")
        (self.directory / "controls").symlink_to(target,target_is_directory=True)
        result = upgrade._finish_host_retirement(self.directory,{"before":{"schema":34}})
        self.assertEqual(result["hostCleanupError"]["code"],"PRIVATE_PATH_UNSAFE")
        self.assertEqual(evidence.read_text(),"keep")
