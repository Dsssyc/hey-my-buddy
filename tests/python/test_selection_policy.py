"""Bounded routing policy facts and the typed model check: unit coverage.

The selector's historical bugs are regression tests here at the pure-policy
level: a positive DSH task preference can never be stated as a fallback or an
exclusion while a legal DSH candidate exists, and a constraint invented from a
DSH filename or task text has no path into the facts. Acceptance and refusal
are decided by program-derived facts only; the qualitative reason text is never
parsed.
"""
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
EMPTY_SUPPORT = {"cardProfileIds": [], "annotationProfileIds": []}


def derived_facts(profiles=(DSH_PROFILE, ZCODE_PROFILE), routing_preferences=(), prefer=(), hard=None):
    return selection_policy.policy_facts(
        profiles=list(profiles),
        routing_preferences=list(routing_preferences),
        prefer_profile_ids=list(prefer),
        hard_constraints=hard or {},
    )


def answer(profile_id, check, *, evidence=(), support=None):
    return {
        "profileId": profile_id,
        "reason": "grounded in the supplied table",
        "evidenceIds": list(evidence),
        "policyCheck": check,
        "support": support or EMPTY_SUPPORT,
    }


def validate(decision, profile_facts, routing_preferences=(), cards=(), annotations=()):
    return selection_policy.validate_decision(
        decision, profile_facts, routing_preferences, set(cards), set(annotations),
    )


class PolicyFactsTests(unittest.TestCase):
    def test_input_shaped_echo_never_controls_the_program_derived_check(self):
        for rules, prefer in (([], []), (POSITIVE_DSH_RULE, []), ([], [DSH_ID])):
            with self.subTest(rules=rules, prefer=prefer):
                facts = derived_facts(profiles=(DSH_PROFILE,), routing_preferences=rules, prefer=prefer)
                echoed = {"hardConstraints": facts["hardConstraints"],
                          "taskPreference": {**facts["taskPreference"], "outcome": "matched"},
                          "userPreference": "none"}
                expected = selection_policy.expected_policy_check(facts, rules, DSH_ID)
                check, support, failure = validate(answer(DSH_ID, echoed), facts, rules)
                self.assertIsNone(failure)
                self.assertEqual(check, expected)
                self.assertEqual(support, EMPTY_SUPPORT)
                minimal = answer(DSH_ID, echoed)
                del minimal["policyCheck"]
                self.assertEqual(validate(minimal, facts, rules), (expected, EMPTY_SUPPORT, None))

    def test_positive_dsh_preference_matches_legal_dsh_and_cannot_be_inverted(self):
        derived = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        self.assertEqual(derived["taskPreference"], {"ruleIndex": 0, "matchingProfileIds": [DSH_ID]})
        self.assertEqual(derived["hardConstraints"], {})
        expected = selection_policy.expected_policy_check(derived, POSITIVE_DSH_RULE, DSH_ID)
        self.assertEqual(expected["taskPreference"], {"ruleIndex": 0, "outcome": "matched"})
        self.assertEqual(expected["userPreference"], "none")
        # An echo can no longer invert program-owned facts.
        for outcome in ("fallback", "none", "avoided", "excluded", "avoid"):
            with self.subTest(outcome=outcome):
                inverted = {**expected, "taskPreference": {"ruleIndex": 0, "outcome": outcome}}
                check, _, failure = validate(answer(DSH_ID, inverted), derived, POSITIVE_DSH_RULE)
                self.assertIsNone(failure)
                self.assertEqual(check, expected)

    def test_phantom_adapter_constraint_from_a_dsh_filename_is_refused(self):
        derived = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        # Working on DSH source files adds no adapter constraint of its own.
        self.assertEqual(derived["hardConstraints"], {})
        expected = selection_policy.expected_policy_check(derived, POSITIVE_DSH_RULE, DSH_ID)
        invented = {**expected, "hardConstraints": {"adapter": "dsh"}}
        checked, _, failure = validate(answer(DSH_ID, invented), derived, POSITIVE_DSH_RULE)
        self.assertIsNone(failure)
        self.assertEqual(checked["hardConstraints"], {})
        dropped = {**expected, "hardConstraints": {}}
        derived_with_hard = derived_facts(routing_preferences=POSITIVE_DSH_RULE, hard={"effort": "high"})
        checked, _, failure = validate(answer(DSH_ID, dropped), derived_with_hard, POSITIVE_DSH_RULE)
        self.assertIsNone(failure)
        self.assertEqual(checked["hardConstraints"], {"effort": "high"})

    def test_matched_preference_is_accepted_with_the_exact_expected_check(self):
        derived = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        check = selection_policy.expected_policy_check(derived, POSITIVE_DSH_RULE, DSH_ID)
        policy_check, support, failure = validate(answer(DSH_ID, check), derived, POSITIVE_DSH_RULE)
        self.assertIsNone(failure)
        self.assertEqual(policy_check, check)
        self.assertEqual(support, EMPTY_SUPPORT)

    def test_absent_preferred_candidate_is_an_honest_fallback(self):
        derived = derived_facts(routing_preferences=ABSENT_PROVIDER_RULE)
        self.assertEqual(derived["taskPreference"], {"ruleIndex": None, "matchingProfileIds": []})
        check = selection_policy.expected_policy_check(derived, ABSENT_PROVIDER_RULE, ZCODE_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": None, "outcome": "fallback"})
        _, _, failure = validate(answer(ZCODE_ID, check), derived, ABSENT_PROVIDER_RULE)
        self.assertIsNone(failure)
        # The same shape without any supplied rules is "none", never "fallback".
        plain = derived_facts()
        check = selection_policy.expected_policy_check(plain, [], ZCODE_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": None, "outcome": "none"})
        _, _, failure = validate(answer(ZCODE_ID, check), plain, [])
        self.assertIsNone(failure)

    def test_supported_alternative_is_accepted_and_unsupported_alternative_is_refused(self):
        derived = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        check = selection_policy.expected_policy_check(derived, POSITIVE_DSH_RULE, ZCODE_ID)
        self.assertEqual(check["taskPreference"], {"ruleIndex": 0, "outcome": "alternative"})
        _, _, failure = validate(answer(ZCODE_ID, check), derived, POSITIVE_DSH_RULE)
        self.assertEqual(failure[0], selection_policy.POLICY_ALTERNATIVE_UNSUPPORTED)
        _, _, failure = validate(
            answer(ZCODE_ID, check, evidence=["ev-1"]),
            derived, POSITIVE_DSH_RULE, cards=(), annotations=(),
        )
        self.assertIsNone(failure)
        _, _, failure = validate(
            answer(ZCODE_ID, check, support={"cardProfileIds": [], "annotationProfileIds": [DSH_ID]}),
            derived, POSITIVE_DSH_RULE, annotations=(DSH_ID,),
        )
        self.assertIsNone(failure)
        # An unsupported alternative on the user-preference axis is refused too.
        user_derived = derived_facts(prefer=[DSH_ID])
        user_check = selection_policy.expected_policy_check(user_derived, [], ZCODE_ID)
        self.assertEqual(user_check["userPreference"], "alternative")
        _, _, failure = validate(answer(ZCODE_ID, user_check), user_derived, [])
        self.assertEqual(failure[0], selection_policy.POLICY_ALTERNATIVE_UNSUPPORTED)

    def test_null_abstention_is_accepted_only_fully_empty(self):
        abstention = {
            "profileId": None, "reason": "no candidate is clearly better supported",
            "evidenceIds": [], "policyCheck": None, "support": EMPTY_SUPPORT,
        }
        policy_check, support, failure = validate(abstention, derived_facts())
        self.assertIsNone(failure)
        self.assertIsNone(policy_check)
        self.assertEqual(support, EMPTY_SUPPORT)
        carrying = {**abstention, "policyCheck": {"hardConstraints": {}, "taskPreference": {"ruleIndex": None, "outcome": "none"}, "userPreference": "none"}}
        checked, _, failure = validate(carrying, derived_facts())
        self.assertIsNone(failure)
        self.assertIsNone(checked)
        _, _, failure = validate({**abstention, "support": {"cardProfileIds": [DSH_ID], "annotationProfileIds": []}}, derived_facts(), cards=(DSH_ID,))
        self.assertEqual(failure[0], selection_policy.POLICY_CHECK_SHAPE)
        # Python enforces the empty evidenceIds contract independently of Node.
        _, _, failure = validate({**abstention, "evidenceIds": ["ev-1"]}, derived_facts())
        self.assertEqual(failure[0], selection_policy.POLICY_CHECK_SHAPE)
        _, _, failure = validate({**abstention, "evidenceIds": "ev-1"}, derived_facts())
        self.assertEqual(failure[0], selection_policy.POLICY_CHECK_SHAPE)

    def test_unknown_or_unrelated_support_references_are_refused(self):
        derived = derived_facts()
        check = selection_policy.expected_policy_check(derived, [], DSH_ID)
        _, _, failure = validate(
            answer(DSH_ID, check, support={"cardProfileIds": ["ghost"], "annotationProfileIds": []}),
            derived, [], cards=(DSH_ID,),
        )
        self.assertEqual(failure[0], selection_policy.POLICY_SUPPORT_UNKNOWN)
        # A real supplied annotation for an unrelated candidate is still out of scope.
        _, _, failure = validate(
            answer(DSH_ID, check, support={"cardProfileIds": [], "annotationProfileIds": [ZCODE_ID]}),
            derived, [], annotations=(ZCODE_ID,),
        )
        self.assertEqual(failure[0], selection_policy.POLICY_SUPPORT_UNKNOWN)
        _, _, failure = validate(
            answer(DSH_ID, check, support={"cardProfileIds": [DSH_ID, DSH_ID], "annotationProfileIds": []}),
            derived, [], cards=(DSH_ID,),
        )
        self.assertEqual(failure[0], selection_policy.POLICY_SUPPORT_UNKNOWN)
        oversized = {"cardProfileIds": [f"p-{index}" for index in range(selection_policy.MAX_SUPPORT_IDS + 1)], "annotationProfileIds": []}
        _, _, failure = validate(answer(DSH_ID, check, support=oversized), derived, [], cards=tuple(oversized["cardProfileIds"]))
        self.assertEqual(failure[0], selection_policy.POLICY_SUPPORT_UNKNOWN)

    def test_pins_excludes_and_hard_filters_still_bind_preference_matching(self):
        derived = derived_facts(
            profiles=(DSH_PROFILE, DISABLED_DSH_PROFILE, UNAVAILABLE_ZCODE_PROFILE),
            routing_preferences=POSITIVE_DSH_RULE,
        )
        self.assertEqual(
            derived["taskPreference"],
            {"ruleIndex": 0, "matchingProfileIds": [DSH_ID]},
            "a disabled or unavailable DSH profile can never carry the preference match",
        )
        derived = derived_facts(hard={"effort": "high"})
        self.assertEqual(derived["hardConstraints"], {"effort": "high"})

    def test_all_effort_variants_remain_user_controlled(self):
        profiles = (DSH_PROFILE, DSH_HIGH_PROFILE)
        derived = derived_facts(profiles=profiles, prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(derived, [], DSH_HIGH_ID)
        self.assertEqual(check["userPreference"], "alternative")
        self.assertEqual(check["hardConstraints"], {}, "no effort or model constraint is invented")
        _, _, failure = validate(
            answer(DSH_HIGH_ID, check, support={"cardProfileIds": [], "annotationProfileIds": [DSH_ID]}),
            derived, [], annotations=(DSH_ID,),
        )
        self.assertIsNone(failure, "a higher-effort variant stays a legal supported alternative, not a pin violation")

    def test_user_preferences_and_annotations_are_untouched_and_facts_are_deterministic(self):
        profiles = [dict(DSH_PROFILE), dict(ZCODE_PROFILE)]
        preferences = [{"profileId": DSH_ID, "mode": "prefer", "reason": "user preferred"}]
        rules = [{"match": {"adapter": "dsh"}, "reason": "positive"}]
        frozen = (copy.deepcopy(profiles), copy.deepcopy(preferences), copy.deepcopy(rules))
        first = derived_facts(profiles=profiles, routing_preferences=rules, prefer=[DSH_ID], hard={"effort": "off"})
        second = derived_facts(profiles=profiles, routing_preferences=rules, prefer=[DSH_ID], hard={"effort": "off"})
        self.assertEqual(first, second)
        self.assertEqual((profiles, preferences, rules), frozen, "derivation never mutates its inputs")
        check = selection_policy.expected_policy_check(first, rules, DSH_ID)
        decision = answer(DSH_ID, check)
        frozen_decision = copy.deepcopy(decision)
        validate(decision, first, rules)
        self.assertEqual(decision, frozen_decision)

    def test_model_index_and_shape_never_override_derived_index(self):
        derived = derived_facts(routing_preferences=POSITIVE_DSH_RULE)
        check = selection_policy.expected_policy_check(derived, POSITIVE_DSH_RULE, DSH_ID)
        wrong_index = {**check, "taskPreference": {"ruleIndex": 1, "outcome": "matched"}}
        checked, _, failure = validate(answer(DSH_ID, wrong_index), derived, POSITIVE_DSH_RULE)
        self.assertIsNone(failure)
        self.assertEqual(checked, check)
        null_index = {**check, "taskPreference": {"ruleIndex": None, "outcome": "none"}}
        checked, _, failure = validate(answer(DSH_ID, null_index), derived, POSITIVE_DSH_RULE)
        self.assertIsNone(failure)
        self.assertEqual(checked, check)
        # Python bool is an int subclass and json parses 0.0 as float, so both
        # would compare equal to the derived index 0 and reach list indexing:
        # only an explicit null or non-bool integer in the derived range is legal.
        for bad_index in (False, True, 0.0, 1.0, "0", 2):
            with self.subTest(bad_index=bad_index):
                typed = {**check, "taskPreference": {"ruleIndex": bad_index, "outcome": "matched"}}
                checked, _, failure = validate(answer(DSH_ID, typed), derived, POSITIVE_DSH_RULE)
                self.assertIsNone(failure)
                self.assertIs(type(checked["taskPreference"]["ruleIndex"]), int)
                self.assertEqual(checked, check)
        for broken in (None, {}, {"hardConstraints": {}, "taskPreference": {"ruleIndex": 0, "outcome": "matched"}}):
            with self.subTest(broken=broken):
                checked, _, failure = validate(answer(DSH_ID, broken), derived, POSITIVE_DSH_RULE)
                self.assertIsNone(failure)
                self.assertEqual(checked, check)
        _, _, failure = validate(answer(DSH_ID, check, support={"cardProfileIds": []}), derived)
        self.assertEqual(failure[0], selection_policy.POLICY_CHECK_SHAPE)

    def test_unknown_user_outcome_enum_is_replaced_by_program_outcome(self):
        derived = derived_facts(prefer=[DSH_ID])
        check = selection_policy.expected_policy_check(derived, [], DSH_ID)
        checked, _, failure = validate({**answer(DSH_ID, check), "policyCheck": {**check, "userPreference": "deprioritized"}}, derived)
        self.assertIsNone(failure)
        self.assertEqual(checked, check)


class DiagnosticTests(unittest.TestCase):
    def diagnostic(self, text='{"profileId":"p1","reason":"<redacted>"}'):
        return {"calls": 2, "failures": [{"code": "answer-shape", "answer": {
            "text": text, "sha256": "a"*64, "bytes": 500, "redacted": True, "truncated": False,
        }}]}

    def test_bounded_structure_and_supplied_ids_survive_without_changing_decision(self):
        diagnostics = self.diagnostic()
        output = {"status": "ok", "decision": {"profileId": "p1"}, "diagnostics": diagnostics}
        self.assertEqual(selection_policy.sanitize_diagnostics(output, {"profiles": [{"profileId": "p1"}]}), output)
        self.assertEqual(output["diagnostics"], diagnostics)

    def test_redacted_flag_cannot_authorize_secret_text_or_unbounded_shape(self):
        base = self.diagnostic()
        variants = [self.diagnostic('{"reason":"SECRET-123"}'), self.diagnostic('{"profileId":"ghost"}'),
                    self.diagnostic('{"reason":123456789}'), self.diagnostic('界'*1000),
                    {**base, "calls": True}, {**base, "calls": 3}, {**base, "failures": base["failures"]*3}]
        for diagnostics in variants:
            with self.subTest(diagnostics=diagnostics):
                output = selection_policy.sanitize_diagnostics({"status": "ok", "diagnostics": diagnostics}, {})
                self.assertNotIn("diagnostics", output)
                self.assertTrue(output["diagnosticsOmitted"])
                self.assertEqual(output["status"], "ok")

    def test_unparseable_text_never_leaves_the_sanitizer(self):
        output = selection_policy.sanitize_diagnostics({"diagnostics": self.diagnostic('Bearer secret-token')}, {})
        self.assertEqual(output["diagnostics"]["failures"][0]["answer"]["text"], "<unparsed-answer>")


if __name__ == "__main__":
    unittest.main()
