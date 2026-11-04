"""Host attention states and history filters follow completed workflow changes."""

from blackboard.tasks.test_workflow import WorkflowTestCase


class HostAttentionTests(WorkflowTestCase):
    def setUp(self):
        super().setUp()
        self.board_instance = self.board()
        self.register(self.board_instance)

    def returned(self, name, disposition="completed"):
        run = self.submit(self.board_instance, request_id=name, cwd=str(self.workdir(name)))
        claim = self.claim(self.board_instance, claim_request_id=name, run_id=run["runId"])
        self.finish_turn(self.board_instance, claim, disposition=disposition)
        return self.board_instance.call("workflow_get", {"runId": run["runId"]})

    def assert_history(self, *, host, review):
        for filter_name, expected in (("host", host), ("review", review)):
            with self.subTest(filter=filter_name):
                page = self.board_instance.call("task_list", {"filter": filter_name})
                self.assertEqual(page["total"], expected)

    def test_attention_ids_actions_and_history_follow_mutations(self):
        attention = self.returned("attention", "attention")
        delivered = self.returned("delivered")
        concluded = self.returned("concluded")
        self.assertEqual(attention["state"], "awaiting-host")
        self.assertTrue(attention["activeRequest"]["requestId"])
        self.assertEqual(attention["actions"], ["continue", "cancel", "get"])
        self.assertEqual(delivered["state"], "delivered")
        self.assertTrue(delivered["finalArtifactId"])
        self.assertEqual(delivered["actions"], ["accept", "continue", "conclude", "get"])
        self.assert_history(host=1, review=2)

        self.continue_run(self.board_instance, attention)
        self.assert_history(host=0, review=2)
        self.accept_run(self.board_instance, delivered)
        self.assert_history(host=0, review=1)
        self.board_instance.call("workflow_conclude", {"runId": concluded["runId"],
                                                      "artifactId": concluded["finalArtifactId"],
                                                      "note": "retained fixture conclusion"})
        self.assert_history(host=0, review=0)

    def test_history_filter_counts_ignore_pagination_and_query(self):
        for index in range(22):
            self.returned(f"delivered-{index}")
        page = self.board_instance.call("task_list", {"filter": "review", "limit": 1, "offset": 20})
        self.assertEqual(len(page["runs"]), 1)
        self.assertEqual(page["total"], 22)
        empty = self.board_instance.call("task_list", {"filter": "review", "query": "no matching task"})
        self.assertEqual(empty["total"], 0)
        self.assert_history(host=0, review=22)
