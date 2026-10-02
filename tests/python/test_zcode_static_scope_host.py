"""Host review regressions for arrow whitespace and virtual scope descendants."""
import unittest

from buddy.adapters.zcode_read_only import native_contract_problem
from test_zcode_static_scope import rewired, WIRING


class HostScopeTests(unittest.TestCase):
    def test_parenthesized_arrow_parameters_survive_whitespace_and_comments(self):
        for gap in (' ', '/*x*/', '//x\n', '\n'):
            for body in ('{' + WIRING + '}', WIRING):
                caller = 'function refresh(e){const g=(fT)' + gap + '=>' + body + ';g(()=>["Bash"])}'
                with self.subTest(gap=gap, body=body):
                    self.assertIsNotNone(native_contract_problem(rewired(caller)))

    def test_virtual_loop_and_arrow_scopes_parent_their_nested_blocks(self):
        for body in (
            'if(e.ok){' + WIRING + '}',
            'for(let x of [1]){' + WIRING + '}',
            'if(e.ok){const g=()=>{' + WIRING + '};g()}',
        ):
            caller = 'function refresh(e){for(let fT of [()=>["Bash"]])' + body + '}'
            with self.subTest(body=body):
                self.assertIsNotNone(native_contract_problem(rewired(caller)))
        caller = 'function refresh(e){const g=fT=>(()=>{' + WIRING + '})();g(()=>["Bash"])}'
        self.assertIsNotNone(native_contract_problem(rewired(caller)))

    def test_completed_virtual_scope_does_not_shadow_a_later_call(self):
        for before in ('for(let fT of [])if(e.ok){e.fT=1}', 'const g=fT=>(()=>{e.fT=1})();'):
            caller = 'function refresh(e){' + before + ';if(e.ok){' + WIRING + '}}'
            with self.subTest(before=before):
                self.assertIsNone(native_contract_problem(rewired(caller)))


if __name__ == '__main__':
    unittest.main()
