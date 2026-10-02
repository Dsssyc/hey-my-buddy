"""Free qualification counterexamples for proof dependencies and real members."""
import unittest

from buddy.adapters.zcode_read_only import native_contract_problem
from test_zcode_static_contract import GOOD_IF_BUNDLE, GOOD_BUNDLE


class ProofDependencyTests(unittest.TestCase):
    def test_every_verified_helper_and_set_constructor_keeps_its_binding(self):
        for tail in (
            'fO=(e,t)=>bR({});', 'aM=v=>"Bash";', 'nL=e=>["Bash"];',
            'aC=(e,t)=>["Bash"];', 'fD=e=>({has:()=>false});',
            'fO/*c*/=(e,t)=>bR({});', 'fO//c\n=(e,t)=>bR({});',
            '([fO,x="\\n"]=[(e,t)=>bR({})]);',
            'var url="x//u";fO=(e,t)=>bR({});\n[0];',
            'function FakeSet(names){this.has=name=>true}var Set=FakeSet;',
            'function Set(names){this.has=name=>true}',
            'class Set{has(name){return true}}',
        ):
            with self.subTest(tail=tail):
                self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE + tail))

    def test_assignment_rhs_uses_its_own_scope_and_computed_calls_are_mutations(self):
        for tail in (
            'var otherKey="submit_result";function change(otherKey){sK=otherKey}change("Read");',
            'function change(){let otherKey="Read";sK=otherKey}change();',
            '([sK]=["Read"]);', '({x:sK}={x:"Read"});',
            '([sK="\\x00"]=["Read"]);', 's\\u004b="Read";',
        ):
            with self.subTest(tail=tail):
                self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE + tail))
        for tail in ('wS["add"]("Read");', 'wS[methodName]("Read");',
                     'wS["add"]?.("Read");', 'wS/*c*/.add/*v*/("Read");',
                     'wS.add?.("Read");', 'wS?.add("Read");', 'wS?.["add"]("Read");'):
            with self.subTest(tail=tail):
                self.assertIsNotNone(native_contract_problem(GOOD_BUNDLE + tail))

    def test_unrelated_local_writes_and_real_read_metadata_still_qualify(self):
        self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE))
        self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE + 'function other(aM){aM=()=>"Bash"}'))
        self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE.replace('readOnly:!0', 'readOnly:true')))
        for tail in ('var x=0;x+++Qm;', 'var x=0;x---Qm;', 'var x=0;x++ + Qm;'):
            with self.subTest(tail=tail):
                self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE + tail))


class RealMemberTests(unittest.TestCase):
    def test_expression_window_end_never_substitutes_for_real_eof(self):
        enum = 'm.enum(["build","plan","edit","yolo","auto"])'
        for old, new in (
            (enum, enum + ' ' * 4096 + '+0'),
            (enum, enum + '/*' + 'x' * 4096 + '*/+0'),
            ('}).strict();', '}).strict()' + ' ' * 2048 + '+0;'),
            ('metadata:{name:"Read",readOnly:!0}', 'metadata:{name:"Read",readOnly:!0}' + ' ' * 1024 + '+0'),
            ('sK="submit_result"', 'sK="Re"' + ' ' * 4096 + '+"ad"'),
        ):
            with self.subTest(old=old):
                self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE.replace(old, new)))

    def test_prefix_updates_preserve_their_write_kind(self):
        for update in ('++Qm', '++/*c*/Qm', '--//c\nQm'):
            text = GOOD_IF_BUNDLE.replace(']),kR=m.object(', ']);' + update + ';var kR=m.object(', 1)
            with self.subTest(update=update):
                self.assertIsNotNone(native_contract_problem(text))
        self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE + 'function change(){return++Qm}change();'))

    def test_mode_enum_binding_and_every_write_are_proved(self):
        from test_zcode_static_contract import BUNDLE_SCHEMA
        local = BUNDLE_SCHEMA[BUNDLE_SCHEMA.index('kR=m.object('):].removeprefix('kR=').removesuffix(';')
        text = GOOD_IF_BUNDLE.replace(local, 'function buildSessionSchema(Qm){return ' + local + '}')
        text += ';buildSessionSchema(m.enum(["build"]));'
        self.assertIsNotNone(native_contract_problem(text))
        text = GOOD_IF_BUNDLE.replace('kR=m.object(', '[Qm]=[m.enum(["build"])];var kR=m.object(')
        self.assertIsNotNone(native_contract_problem(text))
        self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE + 'Qm/*c*/=m.enum(["build"]);'))

    def test_comments_strings_and_duplicate_members_cannot_provide_schema_facts(self):
        for old, new in (
            ('toolAllowlist:m.array(Dn).optional(),', '/*toolAllowlist:m.array(Dn).optional(),*/'),
            ('toolAllowlist:m.array(Dn).optional(),', 'decoy:"toolAllowlist:m.array(Dn).optional()",'),
            ('toolAllowlist:m.array(Dn).optional(),', 'toolAllowlist:m.string().optional(),'),
            ('toolAllowlist:m.array(Dn).optional(),', 'toolAllowlist:m.array(Dn).optional(),toolAllowlist:m.string().optional(),'),
            ('"plan",', '/*"plan",*/'), ('"plan",', '"/*plan*/",'),
            ('m.enum(["build","plan","edit","yolo","auto"])', 'm.enum(["build","plan","edit","yolo","auto"])+0'),
            ('}).strict();', '}).strict()+0;'),
            ('name:"Read",readOnly:!0', 'name:"Read",/*readOnly:!0*/readOnly:!1'),
            ('name:"Read",readOnly:!0', 'name:"Read",readOnly:!0,readOnly:!1'),
            ('name:"Read",readOnly:!0', 'name:"Read",decoy:"readOnly:!0",readOnly:!1'),
        ):
            with self.subTest(new=new):
                self.assertNotEqual(GOOD_IF_BUNDLE.replace(old, new), GOOD_IF_BUNDLE)
                self.assertIsNotNone(native_contract_problem(GOOD_IF_BUNDLE.replace(old, new)))

    def test_comments_between_real_tokens_do_not_erase_members(self):
        text = GOOD_IF_BUNDLE.replace('toolAllowlist:', 'toolAllowlist/*member*/:')
        text = text.replace('readOnly:!0', 'readOnly:/*value*/!0')
        self.assertIsNone(native_contract_problem(text))


if __name__ == "__main__":
    unittest.main()
