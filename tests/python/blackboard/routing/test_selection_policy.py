"""Program-owned routing facts and generic Router answer boundaries.

Task-local routing preferences and their program validation are cancelled
(ADR-021 decision 5); the facts here cover what remains — hard constraints and
the user's published prefer entries — plus the retired-parameter boundary."""

from __future__ import annotations

import copy
import unittest

from hey_my_buddy.blackboard.routing import selection_policy

DSH_ID = "dsh:deepseek-official:deepseek-flash:off"
DSH_HIGH_ID = "dsh:deepseek-official:deepseek-flash:high"
ZCODE_ID = "zcode:zai-api:GLM-5.3-Flash:max"
DSH_BUDDY = {
    "buddyId": DSH_ID, "harness": "dsh", "provider": "deepseek-official",
    "model": "deepseek-flash", "effort": "off", "enabled": True, "available": True,
}
DSH_HIGH_BUDDY = {**DSH_BUDDY, "buddyId": DSH_HIGH_ID, "effort": "high"}
ZCODE_BUDDY = {
    "buddyId": ZCODE_ID, "harness": "zcode", "provider": "zai-api",
    "model": "GLM-5.3-Flash", "effort": "max", "enabled": True, "available": True,
}
DISABLED_DSH_BUDDY = {**DSH_HIGH_BUDDY, "buddyId": "dsh:deepseek-official:deepseek-v4-pro:high", "enabled": False}
UNAVAILABLE_ZCODE_BUDDY = {**ZCODE_BUDDY, "buddyId": "zcode:zai-api:GLM-5.3-Flash:low", "effort": "low", "available": False}
def derived_facts(buddies=(DSH_BUDDY, ZCODE_BUDDY), prefer=(), hard=None):
    return selection_policy.policy_facts(
        buddies=list(buddies), prefer_buddy_ids=list(prefer), hard_constraints=hard or {},
    )


class PolicyFactsTests(unittest.TestCase):
    def test_facts_carry_no_task_preference_slot(self):
        # The retired scenarios matched ordered task-preference rules and recorded
        # matched/alternative/fallback outcomes with a rule index; the slot itself
        # is gone, and supplying the retired parameter is a hard signature error.
        facts = derived_facts()
        self.assertEqual(set(facts), {"hardConstraints", "userPreferredBuddyIds"})
        with self.assertRaises(TypeError):
            selection_policy.policy_facts(
                buddies=[], prefer_buddy_ids=[],
                routing_preferences=[{"match": {"harness": "dsh"}, "reason": "retired"}],
                hard_constraints={})

    def test_user_preference_records_a_legal_alternative_without_support(self):
        facts = derived_facts(prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(facts, ZCODE_ID)
        self.assertEqual(check["userPreference"], "alternative")
        self.assertEqual(check["hardConstraints"], {})
        from hey_my_buddy.blackboard.routing import router
        answer = {"buddyId": ZCODE_ID, "note": "file evidence supports another route", "evidence": []}
        self.assertEqual(router.validate_answer(answer, [DSH_ID, ZCODE_ID]), answer)

    def test_no_user_preference_is_none_and_a_preferred_choice_is_matched(self):
        self.assertEqual(selection_policy.expected_policy_check(derived_facts(), ZCODE_ID)["userPreference"], "none")
        facts = derived_facts(prefer=[DSH_ID])
        self.assertEqual(facts["userPreferredBuddyIds"], [DSH_ID])
        self.assertEqual(selection_policy.expected_policy_check(facts, DSH_ID)["userPreference"], "matched")

    def test_hard_bounds_and_unavailable_buddies_do_not_create_preference_matches(self):
        facts = derived_facts(buddies=(DSH_BUDDY, DISABLED_DSH_BUDDY, UNAVAILABLE_ZCODE_BUDDY),
                              prefer=[DISABLED_DSH_BUDDY["buddyId"], UNAVAILABLE_ZCODE_BUDDY["buddyId"]],
                              hard={"effort": "high"})
        self.assertEqual(facts["userPreferredBuddyIds"], [])
        self.assertEqual(facts["hardConstraints"], {"effort": "high"})

    def test_effort_variants_remain_user_controlled_alternatives(self):
        facts = derived_facts(buddies=(DSH_BUDDY, DSH_HIGH_BUDDY), prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(facts, DSH_HIGH_ID)
        self.assertEqual(check["userPreference"], "alternative")
        self.assertEqual(check["hardConstraints"], {})

    def test_facts_are_deterministic_and_never_mutate_user_inputs(self):
        buddies = [dict(DSH_BUDDY), dict(ZCODE_BUDDY)]
        prefer = [DSH_ID]
        frozen = copy.deepcopy((buddies, prefer))
        first = derived_facts(buddies=buddies, prefer=prefer, hard={"effort": "off"})
        self.assertEqual(first, derived_facts(buddies=buddies, prefer=prefer, hard={"effort": "off"}))
        selection_policy.expected_policy_check(first, DSH_ID)
        self.assertEqual((buddies, prefer), frozen)


class RouterAnswerTests(unittest.TestCase):
    def answer(self, **changes):
        return {"buddyId": DSH_ID, "note": "Read the frozen checkout", "evidence": [], **changes}

    def validate(self, value):
        from hey_my_buddy.blackboard.routing import router
        return router.validate_answer(value, [DSH_ID, ZCODE_ID])

    def assert_code(self, code, answer):
        from hey_my_buddy.errors import BoardError
        with self.assertRaises(BoardError) as caught:
            self.validate(answer)
        self.assertEqual(caught.exception.code, code)

    def test_dict_and_json_string_answers_match_and_never_mutate_input(self):
        import json
        answer = self.answer(evidence=[{"kind": "file", "ref": "src/example.py"}])
        frozen = copy.deepcopy(answer)
        self.assertEqual(self.validate(answer), frozen)
        self.assertEqual(self.validate(json.dumps(answer)), frozen)
        self.assertEqual(answer, frozen)

    def test_annotation_and_preference_references_are_advisory(self):
        evidence = [{"kind": kind, "ref": "not-supplied"} for kind in ("annotation", "preference")]
        self.assertEqual(self.validate(self.answer(evidence=evidence))["evidence"], evidence)

    def test_abstention_uses_same_shape_and_can_cite_files(self):
        answer = self.answer(buddyId=None, evidence=[{"kind": "file", "ref": "README.md"}])
        self.assertEqual(self.validate(answer), answer)

    def test_outside_frozen_candidates_is_rejected(self):
        for value in ("ghost", "", 3, True, [], {}):
            with self.subTest(value=value):
                self.assert_code("router-out-of-bounds", self.answer(buddyId=value))

    def test_extra_legacy_fields_and_missing_required_fields_are_rejected(self):
        self.assert_code("answer-shape", {"buddyId": DSH_ID, "reason": "legacy", "evidence": []})
        for key in ("policyCheck", "support", "evidenceIds", "diagnostics"):
            with self.subTest(key=key):
                self.assert_code("answer-shape", self.answer(**{key: None}))
        for key in ("buddyId", "note", "evidence"):
            answer = self.answer()
            del answer[key]
            self.assert_code("answer-shape", answer)
        for value in (None, [], "{not json", "[]"):
            self.assert_code("answer-invalid-json" if value == "{not json" else "answer-shape", value)

    def test_note_and_reference_shape_are_bounded(self):
        from hey_my_buddy.blackboard.routing import router
        for value in (None, "", "  ", 1, "x" * (router.MAX_NOTE + 1)):
            with self.subTest(note=value):
                self.assert_code("answer-shape", self.answer(note=value))
        invalid = [None, {}, "id", [{"kind": "annotation"}], [{"kind": "annotation", "ref": "a", "extra": 1}],
                   [{"kind": "unknown", "ref": "a"}], [{"kind": "card", "ref": "a"}],
                   [{"kind": "annotation", "ref": ""}], [{"kind": "annotation", "ref": "a\nsecret"}],
                   [{"kind": "annotation", "ref": "a\x7f"}], [{"kind": "annotation", "ref": "x" * 1025}],
                   [{"kind": "annotation", "ref": "a"}] * (router.MAX_REFERENCES + 1)]
        for value in invalid:
            with self.subTest(evidence=value):
                self.assert_code("answer-shape", self.answer(evidence=value))
        self.validate(self.answer(note="x" * router.MAX_NOTE,
                                  evidence=[{"kind": "annotation", "ref": "x" * 1024}] * router.MAX_REFERENCES))

    def test_file_paths_cannot_escape_checkout(self):
        for ref in ("/etc/passwd", "../file", "src/../../file", "src/../file", "C:/file", "src\\file", "."):
            with self.subTest(ref=ref):
                self.assert_code("answer-shape", self.answer(evidence=[{"kind": "file", "ref": ref}]))
        self.validate(self.answer(evidence=[{"kind": "file", "ref": "src/module.py"}]))

    def test_schema_freezes_candidates_and_has_no_model_policy_fields(self):
        from hey_my_buddy.blackboard.routing import router
        from hey_my_buddy.buddy.roles.structured_call import correction_code, schema_errors, valid_answer
        schema = router.answer_schema([DSH_ID, ZCODE_ID])
        self.assertEqual(schema["properties"]["buddyId"]["enum"], [DSH_ID, ZCODE_ID, None])
        self.assertEqual(set(schema["properties"]), {"buddyId", "note", "evidence"})
        self.assertTrue(valid_answer(self.answer(), schema))
        self.assertTrue(valid_answer(self.answer(buddyId=None), schema))
        self.assertFalse(valid_answer(self.answer(buddyId="ghost"), schema))
        self.assertFalse(valid_answer(self.answer(policyCheck={}), schema))
        self.assertFalse(valid_answer(self.answer(note=""), schema))
        self.assertFalse(valid_answer(self.answer(evidence=[{"kind": "card", "ref": "retired"}]), schema))
        self.assertFalse(valid_answer(self.answer(evidence=[{"kind": "web", "ref": "x"}]), schema))
        self.assertFalse(valid_answer(self.answer(buddyId=True), schema))
        self.assertFalse(valid_answer("not json", schema))
        # Out-of-bounds choices are never format-corrected; shape errors are.
        self.assertIsNone(correction_code(self.answer(buddyId="ghost"), schema))
        self.assertEqual(correction_code(self.answer(note=""), schema), "answer-shape")
        self.assertEqual(correction_code("{", schema), "answer-invalid-json")
        self.assertIsNone(correction_code(self.answer(), schema))
        with self.assertRaises(ValueError):
            schema_errors({}, {"type": "object", "pattern": "x"})


if __name__ == "__main__":
    unittest.main()
