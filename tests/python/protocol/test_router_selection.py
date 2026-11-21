import json
import unittest

from hey_my_buddy.errors import BoardError
from hey_my_buddy.protocol import router_selection
from hey_my_buddy.protocol.structured_schema import schema_errors


def candidate(buddy_id, model_id, *, overall, rolling):
    return {"buddyId": buddy_id, "harness": "test", "modelId": model_id, "effort": "high",
            "statistics": {"overall": {"samples": overall}, "byDomain": {
                "root": {"samples": rolling}, "leaf": {"samples": 0}}}}


class SelectionContractTests(unittest.TestCase):
    def setUp(self):
        self.answer = {"buddyId": "chosen", "domainNodeId": "leaf", "taskType": "debug", "note": "compare the kinds",
                       "evidence": [], "preferredBuddyId": "preferred"}
        self.document = {"routingMode": "review", "exploration": {"quota": True},
                         "domainNodes": [{"nodeId": "root", "parentId": None},
                                         {"nodeId": "leaf", "parentId": "root"}],
                         "buddies": [candidate("chosen", "m1", overall=10, rolling=2),
                                     candidate("preferred", "m2", overall=9, rolling=3),
                                     candidate("same-kind", "m2", overall=9, rolling=3)]}

    def failure(self, answer, code, *, with_errors=False):
        with self.assertRaises(BoardError) as caught:
            router_selection.validate_answer(answer, self.document)
        self.assertEqual(caught.exception.code, code)
        details = caught.exception.details.copy()
        if code in router_selection.FORMAT_ERROR_CODES and not with_errors:
            errors = details.pop("errors")
            self.assertTrue(errors[0]["reason"])
        return details

    def test_static_schema_does_not_receive_dynamic_membership_or_reselection(self):
        schema = router_selection.answer_schema("review", exploration=True)
        for field in ("buddyId", "preferredBuddyId", "domainNodeId"):
            self.assertNotIn("enum", schema["properties"][field])
        self.assertNotIn("reselected", schema["properties"])
        self.assertEqual(schema_errors(self.answer, schema), [])
        self.assertEqual(router_selection.validate_answer(json.dumps(self.answer), self.document), self.answer)
        self.assertEqual(self.failure({**self.answer, "reselected": True}, "answer-shape"),
                         {"field": '$["reselected"]'})

    def test_candidates_and_domain_bounds_precede_shape(self):
        for field, bound in (("buddyId", "not-a-candidate"), ("preferredBuddyId", "not-a-candidate"),
                             ("domainNodeId", "not-an-active-domain")):
            with self.subTest(field=field):
                self.assertEqual(self.failure({**self.answer, field: "unknown", "extra": 42}, "router-out-of-bounds"),
                                 {"field": f"$.{field}", "bound": bound})
                self.assertEqual(self.failure({**self.answer, field: []}, "answer-shape"), {"field": f"$.{field}"})
                self.assertEqual(self.failure({**self.answer, field: ""}, "answer-shape"), {"field": f"$.{field}"})

    def test_unexpected_preferred_id_still_checks_frozen_membership_first(self):
        self.document.pop("exploration")
        self.assertEqual(self.failure({**self.answer, "preferredBuddyId": "unknown"}, "router-out-of-bounds"),
                         {"field": "$.preferredBuddyId", "bound": "not-a-candidate"})
        self.assertEqual(self.failure(self.answer, "answer-shape"), {"field": '$["preferredBuddyId"]'})

    def test_fast_file_bound_precedes_an_incomplete_evidence_entry(self):
        self.document["routingMode"] = "fast"
        answer = {**self.answer, "note": None, "evidence": [{"kind": "file"}]}
        self.assertEqual(self.failure(answer, "router-evidence-out-of-bounds"), {"field": "$.evidence[0].kind"})

    def test_frozen_first_level_samples_and_kind_identity_precede_shape(self):
        valid = router_selection.validate_answer(self.answer, self.document)
        self.assertTrue(router_selection.reselected(valid))
        for answer in (
                {**self.answer, "domainNodeId": None, "extra": True},
                {**self.answer, "buddyId": "same-kind", "evidence": "incorrect"}):
            with self.subTest(answer=answer):
                self.assertEqual(self.failure(answer, "router-exploration-out-of-bounds"),
                                 {"bound": "not-less-sampled"})

    def test_quota_abstention_requires_a_preferred_candidate(self):
        abstention = {**self.answer, "buddyId": None, "domainNodeId": None}
        self.assertFalse(router_selection.reselected(router_selection.validate_answer(abstention, self.document)))
        problem = self.failure({**abstention, "preferredBuddyId": "unknown"}, "router-out-of-bounds")
        self.assertEqual(problem["field"], "$.preferredBuddyId")
        del abstention["preferredBuddyId"]
        self.assertEqual(self.failure(abstention, "answer-shape"), {"field": "$.preferredBuddyId"})

    def test_file_evidence_requires_a_pure_checkout_relative_path(self):
        good = {**self.answer, "evidence": [{"kind": "file", "ref": "src/文件.py"}]}
        self.assertEqual(router_selection.validate_answer(good, self.document), good)
        for path in ("/src/a.py", "src/../a.py", "src/./a.py", "src\\a.py", "src/a.py:12", ".", "a\na", "a\x85a"):
            with self.subTest(path=path):
                answer = {**good, "evidence": [{"kind": "file", "ref": path}]}
                self.assertEqual(self.failure(answer, "answer-shape"), {"field": "$.evidence[0].ref"})
        unknown = {**good, "evidence": [{"kind": "website", "ref": "x"}]}
        self.assertEqual(self.failure(unknown, "answer-shape"), {"field": "$.evidence[0].kind"})
        self.assertEqual(self.failure({**good, "taskType": "coding"}, "answer-shape"), {"field": "$.taskType"})

    def test_format_feedback_identifies_multiple_schema_and_path_errors(self):
        answer = {**self.answer, "extra": True, "spare": None, "note": "",
                  "evidence": [{"kind": "file", "ref": "src/foo.py:31"}, {"kind": "statistics"}]}
        errors = self.failure(answer, "answer-shape", with_errors=True)["errors"]
        self.assertEqual([(item["field"], item["reason"]) for item in errors], [
            ('$["extra"]', "additionalProperties"), ('$["spare"]', "additionalProperties"),
            ("$.note", "minLength"), ("$.evidence[1].ref", "required"), ("$.evidence[0].ref", "relative-path"),
        ])
        large = {**self.answer, **{f"extra{index}": True for index in range(100)}}
        errors = self.failure(large, "answer-shape", with_errors=True)["errors"]
        self.assertEqual(len(errors), router_selection.MAX_FORMAT_ERRORS)

    def test_no_answer_is_distinct_from_received_empty_invalid_or_non_object_text(self):
        self.assertEqual(self.failure(None, "router-no-answer"), {})
        self.assertEqual(self.failure("  ", "answer-empty"), {"field": "$"})
        self.assertEqual(self.failure('{\n  "buddyId":}', "answer-invalid-json"), {"line": 2, "column": 13})
        self.assertEqual(self.failure("null", "answer-shape"), {"field": "$"})
        self.assertEqual(self.failure('{"buddyId":"chosen","buddyId":"preferred"}', "answer-invalid-json"),
                         {"field": "$"})
        self.assertEqual(self.failure(json.dumps({**self.answer, "note": "\ud800"}), "answer-shape"),
                         {"field": "$.note"})


if __name__ == "__main__":
    unittest.main()
