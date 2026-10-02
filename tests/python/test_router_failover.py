"""ADR-021 switch policy; no model, service, state or runtime calls."""
import unittest

from buddy.errors import BoardError
from buddy.router_failover import classify_outcome


class RouterFailoverTests(unittest.TestCase):
    def classify(self, **changes):
        facts = dict(stage="publication", code=None, answer_valid=False, abstained=False,
                     cancelled=False, circumstances_changed=False, shutdown_confirmed=True)
        return classify_outcome(**(facts | changes))

    def test_stopped_unavailable_preflight_and_unusable_runtime_answers_allow_next_router(self):
        cases = {
            "preflight": ("router-unavailable", "router-quota-exhausted", "router-review-unsupported",
                          "router-no-tool-unsupported"),
            "runtime": ("timeout", "provider-error", "native-error", "empty-answer",
                        "answer-invalid-json", "answer-shape", "readonly-budget-exhausted"),
            "publication": ("router-tools-forbidden", "router-tool-evidence-unverified",
                            "router-budget-exhausted", "router-evidence-out-of-bounds", "router-out-of-bounds"),
        }
        for stage, codes in cases.items():
            for code in codes:
                with self.subTest(stage=stage, code=code):
                    self.assertEqual(self.classify(stage=stage, code=code), "no-answer")
                    self.assertEqual(self.classify(stage=stage, code=code, shutdown_confirmed=False),
                                     "stop-unconfirmed")

    def test_valid_selection_and_valid_abstention_finish_without_switching(self):
        self.assertEqual(self.classify(answer_valid=True), "answered")
        self.assertEqual(self.classify(answer_valid=True, abstained=True), "abstained")
        # A null profile in a rejected answer never restores the Router's health.
        self.assertEqual(self.classify(abstained=True, code="router-tools-forbidden"), "no-answer")
        self.assertEqual(self.classify(abstained=True, code="answer-shape"), "no-answer")

    def test_valid_looking_answers_cannot_hide_unknown_stop(self):
        for abstained in (False, True):
            with self.subTest(abstained=abstained):
                self.assertEqual(self.classify(answer_valid=True, abstained=abstained,
                                               shutdown_confirmed=False), "stop-unconfirmed")

    def test_context_distinguishes_outside_frozen_candidates_from_later_loss_of_legality(self):
        # The same historical code covers two different facts; the publication
        # controller must provide context rather than letting the code decide.
        self.assertEqual(self.classify(code="router-out-of-bounds"), "no-answer")
        self.assertEqual(self.classify(code="router-out-of-bounds", circumstances_changed=True), "changed")
        for code in ("input-changed", "router-configuration-changed", "router-table-changed",
                     "account-selection-changed", "reader-expired", "owner-fenced"):
            with self.subTest(code=code):
                self.assertEqual(self.classify(code=code, circumstances_changed=True), "changed")

    def test_cancellation_and_changed_context_cannot_be_overridden_by_success_or_stop_flags(self):
        for valid in (False, True):
            for stopped in (False, True):
                with self.subTest(valid=valid, stopped=stopped):
                    self.assertEqual(self.classify(answer_valid=valid, shutdown_confirmed=stopped,
                                                   cancelled=True, circumstances_changed=True), "cancelled")
                    self.assertEqual(self.classify(answer_valid=valid, shutdown_confirmed=stopped,
                                                   circumstances_changed=True), "changed")

    def test_boolean_facts_are_strict_and_stage_and_code_are_typed(self):
        for name in ("answer_valid", "abstained", "cancelled", "circumstances_changed", "shutdown_confirmed"):
            for value in (None, 0, 1, "true", [], {}):
                with self.subTest(name=name, value=value), self.assertRaises(BoardError) as error:
                    self.classify(**{name: value})
                self.assertEqual(error.exception.code, "INVALID_ARGUMENT")
        for stage in (None, False, 1, [], {}, "claim", ""):
            with self.subTest(stage=stage), self.assertRaises(BoardError):
                self.classify(stage=stage)
        for code in (False, 1, [], {}):
            with self.subTest(code=code), self.assertRaises(BoardError):
                self.classify(code=code)


if __name__ == "__main__":
    unittest.main()
