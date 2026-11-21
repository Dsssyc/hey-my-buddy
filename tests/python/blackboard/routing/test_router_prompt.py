"""Routing starts with bounded input and reads full evidence from its frozen round."""
from blackboard.routing.test_decision import DecisionTestCase
from admission import admit_board

from hey_my_buddy.blackboard.routing import router_sequence
from hey_my_buddy.buddy.roles.router import render_selection_prompt
from hey_my_buddy.buddy.roles.prompts import router_prompt
from hey_my_buddy.json_codec import canonical_json
from hey_my_buddy.private_files import read_snapshot
from hey_my_buddy.protocol.snapshot_query import SnapshotQuery


def candidate():
    def statistics(samples, first_passes, failures, median):
        return {"samples": samples, "successes": samples - failures, "firstPasses": first_passes,
                "failures": failures, "reworkRounds": 0, "defects": min(1, samples - failures),
                "firstPassRate": first_passes / samples if samples else None,
                "failureRate": failures / samples if samples else None,
                "medianDeliverySeconds": median}
    return {"buddyId": "legal", "load": {"active": 2, "limit": 2, "queued": 1,
            "wait": {"seconds": 300, "atLeast": True, "overdue": 1}},
            "statistics": {"overall": statistics(20, 16, 4, 120),
                           "byDomain": {"domain": statistics(10, 10, 0, 60)},
                           "byDomainTaskType": {"domain": {"review": statistics(4, 0, 4, None)}}}}

class RouterPromptTests(DecisionTestCase):
    def test_claim_and_prompt_stay_bounded_and_query_recovers_the_full_task(self):
        board = self.board()
        self.seed(board)
        task = "维修解析器" * 8000
        selected = self.request(board, task=task)
        claim = admit_board(board, {"nonce": "n" * 32, "taskId": selected["runId"]})["claim"]
        document = claim["decisionInput"]
        source = document["snapshot"]
        self.assertEqual((source["namespace"], source["owner"], source["round"]), ("routing", selected["decisionId"], 1))
        self.assertEqual(document["protocolVersion"], 20)
        self.assertTrue(document["taskTruncated"])
        self.assertEqual(document["taskCharacters"], len(task))
        self.assertEqual(document["taskBytes"], len(task.encode()))
        self.assertLessEqual(len(document["task"].encode()), 8192)
        self.assertTrue(task.startswith(document["task"]))
        self.assertLess(len(canonical_json(document).encode()), 12 * 1024)
        prompt = router_prompt(render_selection_prompt(document))
        self.assertLess(len(prompt.encode()), 12 * 1024)
        for field in ("buddies", "domainNodes", "routingBasis", "resultTime", "firstPassRateInterval"):
            self.assertNotIn(f'"{field}"', prompt)
        self.assertNotIn("enum", document["outputSchema"]["properties"]["buddyId"])
        base = read_snapshot(board.directory, "routing", source["owner"], source["reference"], round=1)
        self.assertEqual(base["protocolVersion"], 20)
        self.assertNotIn("outputSchema", base)
        self.assertGreaterEqual(len(base["buddies"]), 2)
        query = SnapshotQuery(base)
        offset = 0
        pages = []
        while offset is not None:
            page, _ = query.call("query_task", {"offset": offset})
            pages.append(page["text"])
            offset = page["nextOffset"]
        self.assertEqual("".join(pages), task)
        with board.store.db.read() as connection:
            dispatch = router_sequence.dispatch(connection, selected["runId"])
        from hey_my_buddy.json_codec import sha256_text
        self.assertEqual(document["inputSha256"], sha256_text(canonical_json(dispatch["document"])))

    def test_historical_protocol_19_dispatch_remains_readable_without_a_snapshot_file(self):
        import json

        board = self.board()
        self.seed(board)
        selected = self.request(board)
        with board.store.db.write() as connection:
            record = connection.execute("SELECT snapshot_json FROM router_requests WHERE decision_id=?",
                                        (selected["decisionId"],)).fetchone()
            sealed = json.loads(record[0])
            base = sealed["baseInput"]
            del base["protocolVersion"]
            base["outputSchema"] = {"properties": {"buddyId": {"enum": [
                candidate["buddyId"] for candidate in base["buddies"]] + [None]}}}
            connection.execute("UPDATE router_requests SET snapshot_json=? WHERE decision_id=?",
                               (canonical_json(sealed), selected["decisionId"]))
        audit = self.decision(board, selected["decisionId"])
        self.assertEqual(audit["input"]["outputSchema"], base["outputSchema"])
        self.assertNotIn("protocolVersion", audit["input"])
