"""Input-limit declarations and the pure measurement of one final prompt.

Everything here is synthetic: no harness, model or native turn is started.
The counting facts follow the versioned public-source reading recorded in the
ADR-031 workspace growth plan: Codex counts Unicode scalar values of the turn's
native text input, a non-BMP character is one scalar, a combining mark is one
of its own, and a prompt exactly at the declared maximum is admitted.
"""
from __future__ import annotations

import json
import unittest

from hey_my_buddy.protocol.capabilities import (
    INPUT_LIMITS, UNKNOWN_INPUT_LIMIT_NOTE, declared_input_limit, select_input_limit, version_matches,
)
from hey_my_buddy.protocol.harness_names import HARNESS_NAMES
from hey_my_buddy.protocol.input_limit import (
    MEASUREMENT_UNITS, PROMPT_PARTS, UTF8_BYTES, UNICODE_SCALARS, PromptParts, check_prompt_input,
    measure_prompt, measure_text,
)

CODEX = INPUT_LIMITS["codex"]


def parts(**texts: str) -> PromptParts:
    """Six source partitions from the named few; every other part is empty."""
    fields = dict.fromkeys(("role", "task", "host_input", "context", "delivery", "format"), "")
    unknown = set(texts) - set(fields)
    assert not unknown, unknown
    fields.update(texts)
    return PromptParts(**fields)


def prompt_of(total_scalars: int, filler: str = "a") -> PromptParts:
    """One prompt whose six parts sum to exactly the given scalar count."""
    assert total_scalars >= 6
    return PromptParts(role=filler, task=filler, host_input=filler, context=filler,
                       delivery=filler, format=filler * (total_scalars - 5))


class DeclarationTests(unittest.TestCase):
    def test_codex_declares_the_verified_turn_start_text_maximum(self):
        self.assertEqual(CODEX.state, "known")
        self.assertEqual(CODEX.value, 1_048_576)
        self.assertEqual(CODEX.unit, "unicode-scalars")
        self.assertEqual(CODEX.scope, "turn-start-text")
        self.assertEqual(CODEX.form, "cli-app-server")
        self.assertEqual(CODEX.versions, ("0.164.0",))
        self.assertEqual(CODEX.source, "codex-0.164.0-public-source")
        # The declared unit is one the pure measurement can count.
        self.assertIn(CODEX.unit, MEASUREMENT_UNITS)
        self.assertEqual(CODEX.to_payload(), {
            "state": "known", "form": "cli-app-server", "scope": "turn-start-text",
            "source": "codex-0.164.0-public-source", "value": 1_048_576,
            "unit": "unicode-scalars", "versions": ["0.164.0"]})

    def test_the_other_three_channels_declare_unknown_without_a_value(self):
        expected = {"claude": ("cli-stream-json", "prompt-text", "claude-code-cli-reference"),
                    "dsh": ("cli-acp", "session-prompt-text", "dsh-cli-help"),
                    "zcode": ("cli-app-server", "session-send-text", "zcode-cli-docs")}
        for name, (form, scope, source) in expected.items():
            with self.subTest(harness=name):
                declared = INPUT_LIMITS[name]
                self.assertEqual(declared.state, "unknown")
                self.assertIsNone(declared.value)
                self.assertIsNone(declared.unit)
                self.assertEqual(declared.to_payload(), {
                    "state": "unknown", "form": form, "scope": scope, "source": source,
                    "note": UNKNOWN_INPUT_LIMIT_NOTE})
                # The unknown note says no value was obtained and never reads
                # as unlimited.
                self.assertIn("no numeric input limit was obtained", declared.note)
                self.assertIn("does not mean there is no limit", declared.note)
                self.assertNotIn("value", declared.to_payload())

    def test_only_the_four_harnesses_carry_a_declaration(self):
        self.assertEqual(set(INPUT_LIMITS), set(HARNESS_NAMES))
        for name in HARNESS_NAMES:
            self.assertIs(declared_input_limit(name), INPUT_LIMITS[name])
        for name in ("command", "decision", "unknown-harness"):
            self.assertIsNone(declared_input_limit(name))


class VersionSelectionTests(unittest.TestCase):
    def test_the_public_version_line_of_the_verified_release_selects_the_known_value(self):
        for observed in ("codex-cli 0.164.0", "0.164.0", "codex-cli 0.164.0 (default)",
                         "rust-v0.164.0", "v0.164.0"):
            with self.subTest(observed=observed):
                self.assertTrue(version_matches(observed, CODEX.versions))
                self.assertIs(select_input_limit("codex", observed), CODEX)

    def test_any_other_observed_version_never_applies_the_known_value(self):
        for observed in ("codex-cli 0.165.0", "codex-cli 0.164.1", "codex-cli 0.164",
                         "0.164.0-rc.1", "0.164.0+build.5", "codex-cli 0.164.0.1",
                         "codex-cli0.164.0", "codex version unknown", ""):
            with self.subTest(observed=observed):
                self.assertFalse(version_matches(observed, CODEX.versions))
                effective = select_input_limit("codex", observed)
                self.assertEqual(effective.state, "unknown")
                self.assertIsNone(effective.value)
                # The unknown keeps the channel it is about and says which
                # versions the value is verified on.
                self.assertEqual(effective.form, CODEX.form)
                self.assertEqual(effective.scope, CODEX.scope)
                self.assertEqual(effective.source, CODEX.source)
                self.assertIn("0.164.0", effective.note)
                self.assertIn("does not match the verified versions", effective.note)

    def test_an_unobserved_version_selects_unknown_for_the_known_harness(self):
        effective = select_input_limit("codex", None)
        self.assertEqual(effective.state, "unknown")
        self.assertIsNone(effective.value)
        self.assertIn("no harness version was observed", effective.note)
        self.assertIn("0.164.0", effective.note)

    def test_the_unknown_harnesses_stay_unknown_for_every_version(self):
        for name in ("claude", "dsh", "zcode"):
            for observed in ("2.1.284", "0.1.5-rc.1", "0.17.1", None):
                with self.subTest(harness=name, observed=observed):
                    effective = select_input_limit(name, observed)
                    self.assertIs(effective, INPUT_LIMITS[name])
                    self.assertEqual(effective.note, UNKNOWN_INPUT_LIMIT_NOTE)


class MeasurementTests(unittest.TestCase):
    def test_chinese_non_bmp_and_combining_characters_count_their_scalars(self):
        # Chinese: one scalar per character, three UTF-8 bytes each.
        self.assertEqual(measure_text("中文").unicode_scalars, 2)
        self.assertEqual(measure_text("中文").utf8_bytes, 6)
        # A non-BMP character is one scalar and four bytes, never a UTF-16 pair.
        self.assertEqual(measure_text("😀").unicode_scalars, 1)
        self.assertEqual(measure_text("😀").utf8_bytes, 4)
        self.assertEqual(measure_text("😀" * 1000).unicode_scalars, 1000)
        # A combining mark is one scalar of its own, not folded into a cluster.
        self.assertEqual(measure_text("e\u0301").unicode_scalars, 2)
        self.assertEqual(measure_text("e\u0301").utf8_bytes, 3)
        self.assertEqual(measure_text("é").unicode_scalars, 1)

    def test_json_escapes_are_counted_after_decoding_not_as_written(self):
        decoded = json.loads('"\\u4e2d\\ud83d\\ude00"')
        self.assertEqual(decoded, "中😀")
        self.assertEqual(measure_text(decoded).unicode_scalars, 2)
        self.assertEqual(measure_text(decoded).utf8_bytes, 7)
        # The escape sequence as literal text is six scalars of its own; the
        # measurement counts the string it is given, never reinterpreting it.
        self.assertEqual(measure_text("\\u4e2d").unicode_scalars, 6)
        # A NUL rides the wire escaped and still counts one scalar.
        self.assertEqual(measure_text("line one\x00line two").unicode_scalars, 17)

    def test_every_parts_sum_equals_the_total_in_every_unit(self):
        value = parts(role="角色说明\n", task="task 文本 😀", host_input=json.dumps(
            {"中文": ["值", "😀"]}, ensure_ascii=False), context="context " * 3,
            delivery="delivery", format="format")
        measured = measure_prompt(value)
        self.assertEqual([name for name, _sizes in measured.parts], list(PROMPT_PARTS))
        for unit in (UNICODE_SCALARS, UTF8_BYTES):
            with self.subTest(unit=unit):
                self.assertEqual(sum(measured.part_sizes(unit).values()),
                                 measured.total.size(unit))
        # The strongest form of the sum property: the parts are a complete
        # partition, so their concatenation holds exactly the total.
        whole = value.role + value.task + value.host_input + value.context + value.delivery + value.format
        self.assertEqual(measured.total.unicode_scalars, len(whole))
        self.assertEqual(measured.total.utf8_bytes, len(whole.encode("utf-8")))
        self.assertEqual(measured.part_sizes(UNICODE_SCALARS), {
            "role": len(value.role), "task": len(value.task), "hostInput": len(value.host_input),
            "context": len(value.context), "delivery": len(value.delivery),
            "format": len(value.format)})

    def test_empty_parts_count_zero_and_an_empty_prompt_totals_zero(self):
        measured = measure_prompt(PromptParts(role="", task="", host_input="",
                                              context="", delivery="", format=""))
        self.assertEqual(measured.total, measure_text(""))
        self.assertEqual(measured.total.unicode_scalars, 0)
        self.assertEqual(measured.total.utf8_bytes, 0)
        self.assertEqual(measured.part_sizes(UNICODE_SCALARS),
                         dict.fromkeys(PROMPT_PARTS, 0))

    def test_an_unsupported_unit_fails_loudly(self):
        measured = measure_prompt(parts(task="text"))
        for broken in ("", "utf-16-units", "tokens", "unicode-scalars "):
            with self.subTest(unit=broken), self.assertRaises(ValueError):
                measured.total.size(broken)
        with self.assertRaises(ValueError):
            measured.to_payload("bytes")


class CheckTests(unittest.TestCase):
    def test_a_prompt_exactly_at_the_declared_maximum_is_admitted(self):
        value = prompt_of(CODEX.value)
        check = check_prompt_input(CODEX, value)
        self.assertFalse(check.blocked)
        payload = check.to_payload()
        self.assertEqual(payload["measurement"], {
            "unit": "unicode-scalars", "total": 1_048_576,
            "parts": {**dict.fromkeys(PROMPT_PARTS[:-1], 1), "format": 1_048_571}})

    def test_a_prompt_one_scalar_over_the_maximum_is_blocked_with_the_host_facts(self):
        check = check_prompt_input(CODEX, prompt_of(CODEX.value + 1))
        self.assertTrue(check.blocked)
        payload = check.to_payload()
        self.assertEqual(payload["blocked"], True)
        self.assertEqual(payload["limit"], CODEX.to_payload())
        self.assertEqual(payload["measurement"]["unit"], "unicode-scalars")
        self.assertEqual(payload["measurement"]["total"], 1_048_577)
        self.assertEqual(sum(payload["measurement"]["parts"].values()),
                         payload["measurement"]["total"])

    def test_the_declared_unit_is_counted_not_its_byte_size(self):
        # Four-byte characters at exactly the maximum: the UTF-8 size is four
        # times the limit, and the prompt is still admitted.
        at_limit = prompt_of(CODEX.value, filler="😀")
        self.assertEqual(measure_prompt(at_limit).total.utf8_bytes, CODEX.value * 4)
        self.assertFalse(check_prompt_input(CODEX, at_limit).blocked)
        one_over = prompt_of(CODEX.value + 1, filler="中")
        self.assertTrue(check_prompt_input(CODEX, one_over).blocked)

    def test_an_unknown_limit_never_blocks_and_carries_both_diagnostic_sizes(self):
        for limit in (INPUT_LIMITS["claude"], INPUT_LIMITS["dsh"], INPUT_LIMITS["zcode"],
                      select_input_limit("codex", None),
                      select_input_limit("codex", "codex-cli 0.165.0")):
            with self.subTest(limit=limit.note):
                value = prompt_of(CODEX.value * 2, filler="中")
                check = check_prompt_input(limit, value)
                self.assertFalse(check.blocked)
                payload = check.to_payload()
                self.assertEqual(payload["measurement"]["unit"], "unicode-scalars")
                self.assertEqual(payload["measurement"]["total"], CODEX.value * 2)
                self.assertEqual(payload["measurement"]["utf8Bytes"], CODEX.value * 6)
                self.assertNotIn("value", payload["limit"])

    def test_the_selected_declaration_judges_the_prompt_end_to_end(self):
        value = prompt_of(CODEX.value + 1)
        self.assertTrue(check_prompt_input(select_input_limit("codex", "codex-cli 0.164.0"), value).blocked)
        self.assertFalse(check_prompt_input(select_input_limit("codex", "codex-cli 0.165.0"), value).blocked)
        self.assertFalse(check_prompt_input(select_input_limit("claude", "2.1.284"), value).blocked)


if __name__ == "__main__":
    unittest.main()
