"""Program-owned routing facts and generic Router answer boundaries."""
from __future__ import annotations

import copy
import unittest

from buddy import selection_policy

DSH_ID = "dsh:deepseek-official:deepseek-flash:off"
DSH_HIGH_ID = "dsh:deepseek-official:deepseek-flash:high"
ZCODE_ID = "zcode:zai-api:GLM-5.3-Flash:max"
DSH_PROFILE = {
    "profileId": DSH_ID, "adapter": "dsh", "provider": "deepseek-official",
    "model": "deepseek-flash", "effort": "off", "enabled": True, "available": True,
}
DSH_HIGH_PROFILE = {**DSH_PROFILE, "profileId": DSH_HIGH_ID, "effort": "high"}
ZCODE_PROFILE = {
    "profileId": ZCODE_ID, "adapter": "zcode", "provider": "zai-api",
    "model": "GLM-5.3-Flash", "effort": "max", "enabled": True, "available": True,
}
DISABLED_DSH_PROFILE = {**DSH_HIGH_PROFILE, "profileId": "dsh:deepseek-official:deepseek-v4-pro:high", "enabled": False}
UNAVAILABLE_ZCODE_PROFILE = {**ZCODE_PROFILE, "profileId": "zcode:zai-api:GLM-5.3-Flash:low", "effort": "low", "available": False}
POSITIVE_DSH_RULE = [{"match": {"adapter": "dsh"}, "reason": "Use the installed DSH harness for this task"}]
ABSENT_PROVIDER_RULE = [{"match": {"provider": "not-installed"}, "reason": "Try this provider when available"}]
def derived_facts(profiles=(DSH_PROFILE, ZCODE_PROFILE), routing_preferences=(), prefer=(), hard=None):
    return selection_policy.policy_facts(
        profiles=list(profiles), routing_preferences=list(routing_preferences),
        prefer_profile_ids=list(prefer), hard_constraints=hard or {},
    )


class PolicyFactsTests(unittest.TestCase):
    def test_positive_dsh_preference_matches_legal_dsh(self):
        facts = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        self.assertEqual(facts["taskPreference"], {"ruleIndex": 0, "matchingProfileIds": [DSH_ID]})
        self.assertEqual(facts["hardConstraints"], {})
        check = selection_policy.expected_policy_check(facts, POSITIVE_DSH_RULE, DSH_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": 0, "outcome": "matched"})
        self.assertEqual(check["userPreference"], "none")

    def test_absent_preferred_candidate_is_fallback_and_no_rules_is_none(self):
        facts = derived_facts(routing_preferences=ABSENT_PROVIDER_RULE)
        self.assertEqual(facts["taskPreference"], {"ruleIndex": None, "matchingProfileIds": []})
        self.assertEqual(selection_policy.expected_policy_check(facts, ABSENT_PROVIDER_RULE, ZCODE_ID)["taskPreference"],
                         {"ruleIndex": None, "outcome": "fallback"})
        self.assertEqual(selection_policy.expected_policy_check(derived_facts(), [], ZCODE_ID)["taskPreference"],
                         {"ruleIndex": None, "outcome": "none"})

    def test_task_and_user_preferences_record_legal_alternatives_without_support(self):
        facts = derived_facts(routing_preferences=POSITIVE_DSH_RULE, prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(facts, POSITIVE_DSH_RULE, ZCODE_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": 0, "outcome": "alternative"})
        self.assertEqual(check["userPreference"], "alternative")
        from buddy import router
        answer = {"profileId": ZCODE_ID, "reason": "file evidence supports another route", "evidence": []}
        self.assertEqual(router.validate_answer(answer, [DSH_ID, ZCODE_ID]), answer)

    def test_hard_bounds_and_unavailable_profiles_do_not_create_preference_matches(self):
        facts = derived_facts(profiles=(DSH_PROFILE, DISABLED_DSH_PROFILE, UNAVAILABLE_ZCODE_PROFILE),
                              routing_preferences=POSITIVE_DSH_RULE, hard={"effort": "high"})
        self.assertEqual(facts["taskPreference"], {"ruleIndex": 0, "matchingProfileIds": [DSH_ID]})
        self.assertEqual(facts["hardConstraints"], {"effort": "high"})

    def test_first_matching_rule_has_a_program_integer_index(self):
        rules = [*ABSENT_PROVIDER_RULE, *POSITIVE_DSH_RULE]
        facts = derived_facts(routing_preferences=rules)
        check = selection_policy.expected_policy_check(facts, rules, DSH_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": 1, "outcome": "matched"})
        self.assertIs(type(check["taskPreference"]["ruleIndex"]), int)

    def test_effort_variants_remain_user_controlled_alternatives(self):
        facts = derived_facts(profiles=(DSH_PROFILE, DSH_HIGH_PROFILE), prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(facts, [], DSH_HIGH_ID)
        self.assertEqual(check["userPreference"], "alternative")
        self.assertEqual(check["hardConstraints"], {})

    def test_facts_are_deterministic_and_never_mutate_user_inputs(self):
        profiles = [dict(DSH_PROFILE), dict(ZCODE_PROFILE)]
        rules = [{"match": {"adapter": "dsh"}, "reason": "positive"}]
        prefer = [DSH_ID]
        frozen = copy.deepcopy((profiles, rules, prefer))
        first = derived_facts(profiles=profiles, routing_preferences=rules, prefer=prefer, hard={"effort": "off"})
        self.assertEqual(first, derived_facts(profiles=profiles, routing_preferences=rules, prefer=prefer, hard={"effort": "off"}))
        selection_policy.expected_policy_check(first, rules, DSH_ID)
        self.assertEqual((profiles, rules, prefer), frozen)


class RouterAnswerTests(unittest.TestCase):
    def answer(self, **changes):
        return {"profileId": DSH_ID, "reason": "Read the frozen checkout", "evidence": [], **changes}

    def validate(self, value):
        from buddy import router
        return router.validate_answer(value, [DSH_ID, ZCODE_ID])

    def assert_code(self, code, answer):
        from buddy.errors import BoardError
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

    def test_unknown_card_annotation_and_preference_references_are_allowed(self):
        evidence = [{"kind": kind, "ref": "not-supplied"} for kind in ("card", "annotation", "preference")]
        self.assertEqual(self.validate(self.answer(evidence=evidence))["evidence"], evidence)

    def test_abstention_uses_same_shape_and_can_cite_files(self):
        answer = self.answer(profileId=None, evidence=[{"kind": "file", "ref": "README.md"}])
        self.assertEqual(self.validate(answer), answer)

    def test_outside_frozen_candidates_is_rejected(self):
        for value in ("ghost", "", 3, True, [], {}):
            with self.subTest(value=value):
                self.assert_code("router-out-of-bounds", self.answer(profileId=value))

    def test_extra_legacy_fields_and_missing_required_fields_are_rejected(self):
        for key in ("policyCheck", "support", "evidenceIds", "diagnostics"):
            with self.subTest(key=key):
                self.assert_code("answer-shape", self.answer(**{key: None}))
        for key in ("profileId", "reason", "evidence"):
            answer = self.answer()
            del answer[key]
            self.assert_code("answer-shape", answer)
        for value in (None, [], "{not json", "[]"):
            self.assert_code("answer-invalid-json" if value == "{not json" else "answer-shape", value)

    def test_reason_and_reference_shape_are_bounded(self):
        from buddy import router
        for value in (None, "", "  ", 1, "x" * (router.MAX_REASON + 1)):
            with self.subTest(reason=value):
                self.assert_code("answer-shape", self.answer(reason=value))
        invalid = [None, {}, "id", [{"kind": "card"}], [{"kind": "card", "ref": "a", "extra": 1}],
                   [{"kind": "unknown", "ref": "a"}], [{"kind": "card", "ref": ""}],
                   [{"kind": "card", "ref": "a\nsecret"}], [{"kind": "card", "ref": "a\x7f"}],
                   [{"kind": "card", "ref": "x" * 1025}],
                   [{"kind": "card", "ref": "a"}] * (router.MAX_REFERENCES + 1)]
        for value in invalid:
            with self.subTest(evidence=value):
                self.assert_code("answer-shape", self.answer(evidence=value))
        self.validate(self.answer(reason="x" * router.MAX_REASON,
                                  evidence=[{"kind": "card", "ref": "x" * 1024}] * router.MAX_REFERENCES))

    def test_file_paths_cannot_escape_checkout(self):
        for ref in ("/etc/passwd", "../file", "src/../../file", "src/../file", "C:/file", "src\\file", "."):
            with self.subTest(ref=ref):
                self.assert_code("answer-shape", self.answer(evidence=[{"kind": "file", "ref": ref}]))
        self.validate(self.answer(evidence=[{"kind": "file", "ref": "src/module.py"}]))

    def test_schema_freezes_candidates_and_has_no_model_policy_fields(self):
        from buddy import router
        from buddy.adapters.read_only import correction_code, schema_errors, valid_answer
        schema = router.answer_schema([DSH_ID, ZCODE_ID])
        self.assertEqual(schema["properties"]["profileId"]["enum"], [DSH_ID, ZCODE_ID, None])
        self.assertEqual(set(schema["properties"]), {"profileId", "reason", "evidence"})
        self.assertTrue(valid_answer(self.answer(), schema))
        self.assertTrue(valid_answer(self.answer(profileId=None), schema))
        self.assertFalse(valid_answer(self.answer(profileId="ghost"), schema))
        self.assertFalse(valid_answer(self.answer(policyCheck={}), schema))
        self.assertFalse(valid_answer(self.answer(reason=""), schema))
        self.assertFalse(valid_answer(self.answer(evidence=[{"kind": "card"}]), schema))
        self.assertFalse(valid_answer(self.answer(evidence=[{"kind": "web", "ref": "x"}]), schema))
        self.assertFalse(valid_answer(self.answer(profileId=True), schema))
        self.assertFalse(valid_answer("not json", schema))
        # Out-of-bounds choices are never format-corrected; shape errors are.
        self.assertIsNone(correction_code(self.answer(profileId="ghost"), schema))
        self.assertEqual(correction_code(self.answer(reason=""), schema), "answer-shape")
        self.assertEqual(correction_code("{", schema), "answer-invalid-json")
        self.assertIsNone(correction_code(self.answer(), schema))
        with self.assertRaises(ValueError):
            schema_errors({}, {"type": "object", "pattern": "x"})


if __name__ == "__main__":
    unittest.main()
