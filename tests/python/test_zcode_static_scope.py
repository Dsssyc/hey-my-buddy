"""L6-A5 shared lexical and scope ranges: the six confirmed escapes, saved then fixed.

The L6-A4 fixed result passed its own checks, but an independent review
confirmed six reproducible false accepts, all crossing the shared lexical
region or the scope map: a wiring living only inside a regex literal that a
preceding comment let read as code, a destructured parameter or local whose
last binding the pattern reader dropped, a single unparenthesized arrow
parameter whose braced body lost its binding, a brace-less ``for`` body whose
loop variable bound the header alone, a ``for(var ...)`` whose hoisting to the
enclosing function never happened, and the reverse group match reading
brackets through masked spans. They are saved here first against the fixed
implementation, with their nested-destructuring, default-initializer,
braced/unbraced-arrow, comment and regex-character-class variants, the
positive controls that keep unrelated properties and ``let`` loops from
over-shadowing, and direct unit checks of the lexical region and the scope
map itself. Every counterexample keeps the fixed
``test_zcode_static_contract.GOOD_IF_BUNDLE`` as its base; unknown binding
syntax fails closed rather than widening the grammar, and nothing here pins a
name, hash or version.
"""
from __future__ import annotations

import unittest

import test_zcode_static_contract as static_contract
from buddy.adapters.zcode_read_only import native_contract_problem
from buddy.adapters.zcode_static_contract import _Bundle, _group_open_back, lexical_regions

GOOD_IF_BUNDLE = static_contract.GOOD_IF_BUNDLE
REAL_CALL = static_contract.CALLSITE_BODY
#: The wiring value every shadowed caller variant still spells out, so each
#: replacement genuinely offers the one qualifying call before a binding
#: catches it.
WIRING = "Uw(e.registry,{allowedTools:fT(e.config)})"


def rewired(caller: str) -> str:
    assert REAL_CALL in GOOD_IF_BUNDLE
    return GOOD_IF_BUNDLE.replace(REAL_CALL, caller)


class ConfirmedEscapeTests(unittest.TestCase):
    """The six confirmed L6-A4 escapes, saved as fixed counterexamples."""

    def test_the_confirmed_escapes_never_qualify(self):
        for label, caller in (
            ("regexp-only-wiring-after-a-comment",
             'function refresh(e){return /*comment*/ /Uw(e.registry,{allowedTools:fT(e.config)})/}'),
            ("regexp-only-wiring-after-a-line-comment",
             'function refresh(e){return//comment\n/Uw(e.registry,{allowedTools:fT(e.config)})/}'),
            ("array-parameter-last-binding",
             'function refresh(e,[fT]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("object-parameter-last-binding",
             'function refresh(e,{fT}){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("local-var-destructured-last-binding",
             'function refresh(e){var [fT]=e.list;Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("local-let-destructured-last-binding",
             'function refresh(e){let {fT}=e.list;Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("braced-arrow-single-parameter",
             'const g=fT=>{Uw(e.registry,{allowedTools:fT(e.config)})};g(()=>["Bash"])'),
            ("braceless-for-let-body",
             'for(let fT of [()=>["Bash"]])Uw(e.registry,{allowedTools:fT(e.config)})'),
            ("var-for-after-the-caller-hoists",
             'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});for(var fT of [])x()}'),
            ("classic-var-for-after-the-caller-hoists",
             'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});for(var fT=0;;)x()}'),
            ("var-destructured-for-header-hoists",
             'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});for(var [fT] of [])x()}'),
        ):
            with self.subTest(label=label):
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(rewired(caller)))


class DestructureVariantTests(unittest.TestCase):
    """Complete pattern reading: last items bind, keys and defaults stay out."""

    def test_nested_and_defaulted_patterns_bind_their_targets(self):
        for label, caller in (
            ("nested-object-pattern",
             'function refresh(e,{tools:{list:fT}}){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("nested-array-pattern",
             'function refresh(e,[first,[fT,last]]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("default-initializer-target-only",
             'function refresh(e,[fT=["Bash"]]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("object-shorthand-default",
             'function refresh(e,{fT=e.list}){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("rest-element-binding",
             'function refresh(e,[head,...fT]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("mid-pattern-binding",
             'function refresh(e,[fT,other]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("for-header-destructured-let",
             'for(let [fT] of [[]])Uw(e.registry,{allowedTools:fT(e.config)})'),
        ):
            with self.subTest(label=label):
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(rewired(caller)))

    def test_keys_and_default_initializers_never_bind(self):
        for label, caller in (
            ("object-key-is-not-a-binding",
             'function refresh(e,{fT:value}){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("nested-key-is-not-a-binding",
             'function refresh(e,{fT:{deep:value}}){Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("default-values-are-not-bindings",
             'function refresh(e,[other=fT()]){Uw(e.registry,{allowedTools:fT(e.config)})}'),
        ):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(rewired(caller)))

    def test_unknown_binding_syntax_fails_closed(self):
        caller = 'function refresh(e,{[fT]:value}){Uw(e.registry,{allowedTools:fT(e.config)})}'
        self.assertIsNotNone(native_contract_problem(rewired(caller)))


class ForScopeVariantTests(unittest.TestCase):
    """``var`` hoists to the function, ``let`` binds the loop, bodies count."""

    def test_let_loops_never_shadow_outside_their_body(self):
        for label, caller in (
            ("let-after-the-caller",
             'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});for(let fT of [])x()}'),
            ("braced-let-after-the-caller",
             'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});for(let fT of []){x()}}'),
            ("unrelated-let-loop-variable",
             'function refresh(e){for(let other of [])x();Uw(e.registry,{allowedTools:fT(e.config)})}'),
        ):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(rewired(caller)))

    def test_unreadable_loop_bodies_fail_closed_over_their_span(self):
        for label, caller in (
            ("compound-assignment-body",
             'for(let fT of [])x+=1,Uw(e.registry,{allowedTools:fT(e.config)});'),
            ("regex-literal-body",
             'for(let fT of [])y=/x/,Uw(e.registry,{allowedTools:fT(e.config)});'),
        ):
            with self.subTest(label=label):
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(rewired(caller)))

    def test_supported_braceless_bodies_still_bind_only_their_statement(self):
        # The body statement ends at its semicolon, so a call after it is a
        # separate statement that keeps resolving the global resolver, while
        # the call written as the body statement stays inside the binding.
        after = ('for(let fT of [])x();'
                 'function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)})}')
        self.assertIsNone(native_contract_problem(rewired(after)))
        inside = 'for(let fT of [])x(),Uw(e.registry,{allowedTools:fT(e.config)});'
        self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                      native_contract_problem(rewired(inside)))


class ArrowVariantTests(unittest.TestCase):
    """Arrow parameters bind the whole body, braced or a single expression."""

    def test_every_arrow_parameter_shape_shadows_its_body(self):
        for label, caller in (
            ("braced-single-parameter",
             'const g=fT=>{Uw(e.registry,{allowedTools:fT(e.config)})};g(1)'),
            ("unbraced-single-parameter",
             'const g=fT=>Uw(e.registry,{allowedTools:fT(e.config)});g(1)'),
            ("braced-parenthesized-parameters",
             'const g=(first,fT)=>{Uw(e.registry,{allowedTools:fT(e.config)})};g(1,2)'),
            ("unbraced-parenthesized-parameter",
             'const g=(fT)=>Uw(e.registry,{allowedTools:fT(e.config)});g(1)'),
            ("defaulted-arrow-parameter",
             'const g=(fT=["Bash"])=>{Uw(e.registry,{allowedTools:fT(e.config)})};g()'),
            ("arrow-inside-a-call-argument",
             'later(fT=>{Uw(e.registry,{allowedTools:fT(e.config)})})'),
            ("braced-arrow-in-a-for-header",
             'for(const step of [1])(fT)=>{Uw(e.registry,{allowedTools:fT(e.config)})}(step);'),
        ):
            with self.subTest(label=label):
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(rewired(caller)))

    def test_unrelated_arrow_parameters_do_not_shadow(self):
        caller = ('const g=unrelated=>{Uw(e.registry,{allowedTools:fT(e.config)})};'
                  'g(()=>["Bash"])')
        self.assertIsNone(native_contract_problem(rewired(caller)))


class DecoyContextPositiveTests(unittest.TestCase):
    """Comments and regex character classes mask decoys and nothing else."""

    def test_comment_and_char_class_decoys_leave_the_real_wiring(self):
        for label, caller in (
            ("comment-decoys-around",
             'function refresh(e){/*[{allowedTools:fT(e.config)}]*/Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("line-comment-decoy",
             'function refresh(e){// Uw(e.registry,{allowedTools:fT(e.config)})\n'
             'Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("regex-char-class-decoy",
             'function refresh(e){var re=/[{]allowedTools:fT(e.config)[}]/;re.test(y);'
             'Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("escaped-char-class-decoy",
             r'function refresh(e){var re=/[\]]fT(e.config)/;re.test(y);'
             'Uw(e.registry,{allowedTools:fT(e.config)})}'),
        ):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(rewired(caller)))

    def test_property_members_never_shadow_the_functions(self):
        for label, caller in (
            ("property-calls-before-the-wiring",
             'function refresh(e){e.Uw();e.fT();Uw(e.registry,{allowedTools:fT(e.config)})}'),
            ("deep-property-chain",
             'function refresh(e){e.registry.fT();e.config.Uw;Uw(e.registry,{allowedTools:fT(e.config)})}'),
        ):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(rewired(caller)))


class LexicalRegionTests(unittest.TestCase):
    """Direct checks of the shared code context around every slash."""

    def test_a_regex_after_a_comment_is_masked(self):
        for label, text, probe in (
            ("block-comment", "return/*c*/ /fT(x)/;", "fT(x)"),
            ("line-comment", "return//c\n/fT(x)/;", "fT(x)"),
            ("comment-between-keyword-and-regex", "return /*(*/ /fT(x)/;", "fT(x)"),
        ):
            with self.subTest(label=label):
                regions = lexical_regions(text)
                self.assertFalse(regions.is_code(text.index(probe)))

    def test_a_slash_after_a_finished_literal_or_operand_divides(self):
        for label, text, probe in (
            ("after-a-string", 'y="a" / fT;', "fT"),
            ("after-an-identifier", "y=a / fT;", "fT"),
            ("after-a-closing-bracket", "y=a[0] / fT;", "fT"),
            ("after-a-non-control-call", 'g("(") / fT;', "fT"),
        ):
            with self.subTest(label=label):
                regions = lexical_regions(text)
                self.assertTrue(regions.is_code(text.index(probe)))

    def test_a_regex_after_a_control_header_is_masked(self):
        text = 'if(")/" )/fT/.test(x);'
        regions = lexical_regions(text)
        self.assertFalse(regions.is_code(text.index("fT")))

    def test_the_reverse_group_match_skips_masked_spans(self):
        text = 'if(")")/*(*/ /fT/.test(x);'
        regions = lexical_regions(text)
        close = text.index(")/*")
        self.assertEqual(_group_open_back(text, close, regions.spans), text.index("("))

    def test_regex_character_classes_keep_the_literal_open(self):
        text = r"var re=/[fT(x)]/;later(fT);"
        regions = lexical_regions(text)
        self.assertFalse(regions.is_code(text.index("fT(x)]")))
        self.assertTrue(regions.is_code(text.rindex("fT")))

    def test_template_interpolations_stay_one_masked_span(self):
        text = "var d=`x${/fT(/}y`;"
        regions = lexical_regions(text)
        self.assertFalse(regions.is_code(text.index("/fT(/") + 1))


class ScopeMapTests(unittest.TestCase):
    """Direct checks of the bound environments the scope map reports."""

    @staticmethod
    def bindings_at(text: str, probe: str):
        return _Bundle(text).chain_bindings(text.index(probe))

    def test_complete_patterns_bind_their_last_items(self):
        for label, text, binding in (
            ("array-parameter", "function a(e,[fT]){fT()}", "fT()"),
            ("object-parameter", "function a(e,{fT}){fT()}", "fT()"),
            ("nested-object", "function a(e,{x:{y:fT}}){fT()}", "fT()"),
            ("defaulted-target", "function a(e,[fT=1]){fT()}", "fT()"),
            ("local-var-destructure", "function a(e){var [fT]=e;l();fT()}", "fT()"),
            ("for-header-destructure", "function a(){for(let [fT] of [])fT()}", "fT()"),
        ):
            with self.subTest(label=label):
                self.assertIn("fT", self.bindings_at(text, binding))

    def test_keys_and_properties_never_bind(self):
        for label, text in (
            ("object-key", "function a(e,{fT:value}){fT()}"),
            ("property-member", "function a(e){e.fT();fT()}"),
            ("deep-property", "function a(e){e.x.fT;fT()}"),
        ):
            with self.subTest(label=label):
                self.assertNotIn("fT", self.bindings_at(text, "fT"))

    def test_var_hoists_to_the_function_let_does_not(self):
        hoisted = "function a(e){fT();for(var fT of [])x()}"
        self.assertIn("fT", self.bindings_at(hoisted, "fT()"))
        braced = "function a(e){fT();for(var fT of []){x()}}"
        self.assertIn("fT", self.bindings_at(braced, "fT()"))
        unhoisted = "function a(e){fT();for(let fT of [])x()}"
        self.assertNotIn("fT", self.bindings_at(unhoisted, "fT()"))

    def test_arrow_parameter_shapes_bind_the_whole_body(self):
        for label, text in (
            ("braced-single", "const g=fT=>{fT()};"),
            ("unbraced-single", "const g=fT=>fT();"),
            ("braced-parenthesized", "const g=(a,fT)=>{fT()};"),
            ("arrow-in-for-header", "for(const s of [1])(fT)=>{fT()}(s);"),
        ):
            with self.subTest(label=label):
                self.assertIn("fT", self.bindings_at(text, "fT("))

    def test_unreadable_binding_contexts_report_no_bindings(self):
        text = "function a(){for(let fT of [])x+=1,fT();}"
        self.assertIsNone(self.bindings_at(text, "fT();"))

    def test_a_computed_key_over_shadows_rather_than_resolves(self):
        # The computed key's identifier is not a binding, but the bounded
        # grammar cannot prove that, so it shadows: fail closed.
        bindings = self.bindings_at("function a(e,{[fT]:x}){fT()}", "fT()}")
        self.assertIn("fT", bindings)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
