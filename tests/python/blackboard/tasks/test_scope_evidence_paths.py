"""Scope evidence crosses the file boundary without changing logical paths."""
import json
from pathlib import Path
import unittest

from temporary import TemporaryDirectory
from hey_my_buddy.paths import native_path
from hey_my_buddy.blackboard.tasks import workspace


class ScopeEvidencePathTests(unittest.TestCase):
    def test_deep_scope_evidence_can_be_created_and_read_back(self):
        with TemporaryDirectory(prefix='scope-evidence-') as temporary:
            allocation = Path(temporary).resolve() / ('a' * 70) / ('b' * 70)
            output = allocation / 'outputs' / workspace._sha(workspace._json(['task-1', 'attempt-1']))
            native_path(output).mkdir(parents=True)
            self.assertGreater(len(str(output / 'scope-conflicts')), 260)
            manifest = {'workspaceId': 'ws-fixture', 'manifestSha256': 'd' * 64}
            evidence = {**manifest, 'observedFingerprint': 'a' * 64, 'path': str(output)}
            workspace._record_scope_evidence(output, evidence, 'task-1', 'attempt-1')
            record_path = output / 'scope-conflicts' / ('a' * 64 + '.json')
            self.assertEqual(json.loads(native_path(record_path).read_bytes())['path'], str(output))
            self.assertEqual(workspace._scope_records(allocation, manifest, 'task-1', 'attempt-1'),
                             [{**evidence, 'taskId': 'task-1', 'attemptId': 'attempt-1'}])
