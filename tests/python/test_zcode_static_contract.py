"""L6-A3 free static contract: token-level template verification and counterexamples.

The L6-A2 review confirmed two bundles the old regex assembly wrongly accepted
— a membership test dropped before an unconditional register, and an
``allowedTools`` object parked in an unread third argument — so those
regressions come first here. The rest of the matrix pins the supported
registration flows, the tool-preserving transform, the resolver helper chain
and the call-site wiring against renamed identifiers, string and comment
decoys, and every rejected shape listed in the fixed L6-A3 design. All bundles
are synthetic minified text whose identifiers differ from the real
installation; the installed text itself is verified by the retained installed
test in ``test_zcode_read_only_protocol.py``.
"""
from __future__ import annotations

import unittest

from buddy.adapters.zcode_read_only import native_contract_problem

#: The strict session/create schema every synthetic bundle shares.
BUNDLE_SCHEMA = (
    'var Qm=m.enum(["build","plan","edit","yolo","auto"]),'
    'kR=m.object({sessionId:Dn.optional(),workspace:rd,parentSessionId:Dn.optional(),mode:Qm.optional(),'
    'model:Pu.optional(),titleGenerationEnabled:m.boolean().optional(),mcpServers:m.array(Ype).optional(),'
    'toolAllowlist:m.array(Dn).optional(),toolDenylist:m.array(Dn).optional(),'
    'offPeakToolEnabled:m.boolean().optional(),dynamicWorkflowEnabled:m.boolean().optional()}).strict();'
)
#: The three read-only registrations every synthetic bundle carries.
BUNDLE_TOOLS = (
    'var readTool={capability:"Read files",metadata:{name:"Read",readOnly:!0}};'
    'var globTool={metadata:{name:"Glob",readOnly:!0}};'
    'var grepTool={metadata:{name:"Grep",readOnly:!0}};'
)

# -- the installed mechanism, under fresh minified names -----------------------

#: registerBuiltInTools: the installed short-circuit OR chain with the
#: disallowed-Set builder, the agent-name predicate, a constant set and the
#: tool transform on the register argument.
REGISTER_OR_BODY = (
    'function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0,o=fD(t.disallowedTools);'
    'for(let s of builtins)'
    't.embeddedSearchEnabled===!0&&(s.metadata.name==="Glob"||s.metadata.name==="Grep")'
    '||n&&!n.has(s.metadata.name)'
    '||o?.has(s.metadata.name)'
    '||gA(s.metadata.name)&&t.includeAgent!==!0'
    '||s.metadata.name==="Skill"&&t.includeSkill===!1'
    '||(s.metadata.name==="CronCreate"||s.metadata.name==="CronList")&&t.includeAutomation!==!0'
    '||t.includeDynamicWorkflow===!1&&wS.has(s.metadata.name)'
    '||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})}'
)
#: The same flow in the supported in-loop positive if form.
REGISTER_IF_BODY = (
    'function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0,o=fD(t.disallowedTools);'
    'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))}'
)
#: resolveBuiltInToolEntryForBranch: name-keyed replacements whose conditions
#: never match a read-only tool, with the final unchanged fall-through.
TRANSFORM_BODY = (
    'function fO(e,t){return e.metadata.name==="Bash"?bR({bashTimeoutPolicy:t.bashTimeoutPolicy})'
    ':e.metadata.name===sK&&t.submitResultSchema!==void 0?sU(t.submitResultSchema)'
    ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})'
    ':e}'
)
#: resolveBuiltInToolAllowlist: alias-map, explore-filter and root-child chain.
RESOLVER_BODY = (
    'function fT(e){let t=nL(e.toolAllowlist);'
    'return e.toolset!=="explore"?aC(e,t):t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}'
    'function nL(e){return e?.map(v=>aM(v))}'
    'function aM(v){return v==="web_search"?"WebSearch":v}'
    'function aC(e,t){return t&&(e.taskType==="subagent_child"?t.includes(rC)?t:[...t,rC]:t)}'
)
#: The module constants the chain reads, plus the pure helper family.
HELPERS_BODY = (
    'var eS=new Set(dL),dL=["Bash","Glob","Grep","Read","WebFetch","WebSearch","TodoWrite"],'
    'rC="RespondToCoordinator",sK="submit_result",wS=new Set(["Agent","Task"]),nS=new Set(["Agent","Task"]);'
    'function fD(e){if(!e||e.length===0)return;let t=new Set;'
    'for(let n of e){let o=nZ(n);o&&t.add(o)}return t.size>0?t:void 0}'
    'function nZ(e){let t=e.trim(),n=t.indexOf("("),o=n>0?t.slice(0,n):t;return aM(o)}'
    'function gA(e){return e?nS.has(e):!1}'
)
MAPPING_BODY = 'r(Uw,"registerBuiltInTools");r(fT,"resolveBuiltInToolAllowlist");'
#: The refresh call site: direct options object at the options parameter
#: position, its unique allowedTools value the verified resolver on config.
CALLSITE_BODY = (
    'function refresh(e){Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,'
    'includeSkill:!!e.skillPort,embeddedSearchEnabled:!1,allowedTools:fT(e.config),'
    'disallowedTools:e.config.toolDisallowlist,silentDuplicateWarnings:!0})}'
)

GOOD_BUNDLE = (BUNDLE_SCHEMA + REGISTER_OR_BODY + TRANSFORM_BODY + RESOLVER_BODY
               + HELPERS_BODY + MAPPING_BODY + CALLSITE_BODY + BUNDLE_TOOLS)
GOOD_IF_BUNDLE = (BUNDLE_SCHEMA + REGISTER_IF_BODY + TRANSFORM_BODY + RESOLVER_BODY
                  + HELPERS_BODY + MAPPING_BODY + CALLSITE_BODY + BUNDLE_TOOLS)
#: The same chain with every registration-local variable renamed with ``$``:
#: minified names are never pinned, only the template shapes are.
REGISTER_RENAMED_BODY = (
    'function Uw($e,$t={}){let $n=$t.allowedTools?new Set($t.allowedTools):void 0,$o=fD($t.disallowedTools);'
    'for(let $s of builtins)'
    '$t.embeddedSearchEnabled===!0&&($s.metadata.name==="Glob"||$s.metadata.name==="Grep")'
    '||$n&&!$n.has($s.metadata.name)'
    '||$o?.has($s.metadata.name)'
    '||gA($s.metadata.name)&&$t.includeAgent!==!0'
    '||$s.metadata.name==="Skill"&&$t.includeSkill===!1'
    '||$t.includeDynamicWorkflow===!1&&wS.has($s.metadata.name)'
    '||$e.register(fO($s,$t),{silentDuplicateWarning:$t.silentDuplicateWarnings})}'
)
GOOD_RENAMED_BUNDLE = (BUNDLE_SCHEMA + REGISTER_RENAMED_BODY + TRANSFORM_BODY + RESOLVER_BODY
                       + HELPERS_BODY + MAPPING_BODY + CALLSITE_BODY + BUNDLE_TOOLS)
#: The minimal supported shapes: the positive if flow without a transform and
#: the direct same-value local resolver, plus the plain OR chain under fresh
#: names with the direct resolver.
SIMPLE_IF_BUNDLE = (BUNDLE_SCHEMA
                    + 'function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0;'
                      'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(s)}'
                    + 'function fT(e){let t=e.toolAllowlist;return t}'
                    + MAPPING_BODY
                    + 'function refresh(e){Uw(e.registry,{embeddedSearchEnabled:!1,'
                      'allowedTools:fT(e.config),disallowedTools:e.config.toolDisallowlist})}'
                    + BUNDLE_TOOLS)
SIMPLE_OR_BUNDLE = (BUNDLE_SCHEMA
                    + 'function $r($e,$t={}){let $n=$t.allowedTools?new Set($t.allowedTools):void 0;'
                      'for(let $s of $l)'
                      '$t.includeSkill===!1&&$s.metadata.name==="Skill"'
                      '||$n&&!$n.has($s.metadata.name)'
                      '||$t.includeNodeRepl!==!0&&$s.metadata.name==="js"'
                      '||$e.register($s)}'
                    + 'function $f(e){let t=e.toolAllowlist;return t}'
                    + 'r($r,"registerBuiltInTools");r($f,"resolveBuiltInToolAllowlist");'
                    + 'function refresh(e){$r(e.registry,{allowedTools:$f(e.config)})}'
                    + BUNDLE_TOOLS)
#: The resolver returning the config member directly.
DIRECT_RESOLVER_BUNDLE = (SIMPLE_IF_BUNDLE
                          .replace("function fT(e){let t=e.toolAllowlist;return t}",
                                   "function fT(e){return e.toolAllowlist}"))


def mutated(base: str, *edits: tuple[str, str]) -> str:
    for old, new in edits:
        assert old in base, old
        base = base.replace(old, new)
    return base


class SupportedMechanismTests(unittest.TestCase):
    """Every supported template qualifies under any minified naming."""

    def test_the_supported_template_set_qualifies(self):
        for label, bundle in (("installed-or-form", GOOD_BUNDLE),
                              ("installed-if-form", GOOD_IF_BUNDLE),
                              ("renamed-identifiers", GOOD_RENAMED_BUNDLE),
                              ("simple-if-form", SIMPLE_IF_BUNDLE),
                              ("simple-or-form", SIMPLE_OR_BUNDLE),
                              ("direct-resolver", DIRECT_RESOLVER_BUNDLE)):
            with self.subTest(label=label):
                self.assertIsNone(native_contract_problem(bundle))


class P1RegressionTests(unittest.TestCase):
    """The two confirmed L6-A2 escapes, saved as failing-then-fixed regressions."""

    def test_a_discarded_membership_test_before_an_unconditional_register(self):
        bundle = mutated(SIMPLE_IF_BUNDLE,
                         ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(s)",
                          "for(let s of builtins){n&&!n.has(s.metadata.name);!1||e.register(s)}"))
        self.assertIn("unrecognized control flow", native_contract_problem(bundle))

    def test_a_gated_register_of_an_unrelated_argument(self):
        bundle = mutated(GOOD_IF_BUNDLE, ("e.register(fO(s,t))}", "e.register(other)}"))
        self.assertIn("does not register the loop variable", native_contract_problem(bundle))

    def test_an_allowed_tools_object_in_an_unread_third_argument(self):
        bundle = mutated(SIMPLE_IF_BUNDLE,
                         ("Uw(e.registry,{embeddedSearchEnabled:!1,",
                          "Uw(e.registry,{},{embeddedSearchEnabled:!1,"))
        self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                      native_contract_problem(bundle))


class RegistrationFlowTests(unittest.TestCase):
    """Decoys around the two complete flows fail closed with specific reasons."""

    def test_statement_decoys_around_the_positive_gate_fail(self):
        for label, body, reason in (
            ("semicolon-decoy",
             "for(let s of builtins){if(!n||n.has(s.metadata.name));e.register(s)}",
             "unrecognized control flow"),
            ("comma-expression",
             "for(let s of builtins)(n&&!n.has(s.metadata.name),e.register(s))",
             "unrecognized control flow"),
            ("assignment-expression",
             "for(let s of builtins){if(!n||n.has(s.metadata.name))e.register(s);x=1}",
             "unrecognized control flow"),
            ("else-branch",
             "for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t));else e.register(s)",
             "outside the one allowlist-gated flow"),
            ("loopvar-mismatch",
             "for(let s of builtins)if(!n||n.has($s.metadata.name))e.register(fO(s,t))",
             "unrecognized control flow"),
            ("gate-on-another-set",
             "for(let s of builtins)if(!q||q.has(s.metadata.name))e.register(fO(s,t))",
             "unrecognized control flow"),
            ("inverted-gate",
             "for(let s of builtins)if(n&&!n.has(s.metadata.name))e.register(fO(s,t))",
             "unrecognized control flow"),
            ("unconditional-register",
             "for(let s of builtins)e.register(fO(s,t))",
             "unrecognized control flow"),
            ("second-register-inside-loop",
             "for(let s of builtins){if(!n||n.has(s.metadata.name))e.register(fO(s,t));e.register(s)}",
             "outside the one allowlist-gated flow"),
            ("foreign-registry",
             "for(let s of builtins)if(!n||n.has(s.metadata.name))k.register(fO(s,t))",
             "own registry parameters"),
            ("register-outside-the-loop",
             "for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t));e.register(s)",
             "outside the one allowlist-gated flow"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE,
                                 ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))",
                                  body))
                self.assertIn(reason, native_contract_problem(bundle))

    def test_chain_decoys_fail_closed(self):
        good_chain = ("for(let s of builtins)"
                      "t.embeddedSearchEnabled===!0&&(s.metadata.name===\"Glob\"||s.metadata.name===\"Grep\")"
                      "||n&&!n.has(s.metadata.name)")
        for label, old, new, reason in (
            ("extra-tail", "||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})}",
                           "||e.register(fO(s,t),{silentDuplicateWarning:t.silentDuplicateWarnings})||x}",
                           "unrecognized control flow"),
            ("inverted-membership", "n&&!n.has(s.metadata.name)", "n&&n.has(s.metadata.name)",
             "unrecognized control flow"),
            ("membership-on-another-loopvar", "n&&!n.has(s.metadata.name)",
             "n&&!n.has(q.metadata.name)", "unrecognized control flow"),
            ("set-rewritten-after-declaration",
             "t.silentDuplicateWarnings})}", "t.silentDuplicateWarnings});n=void 0}",
             "rewrites its allowlist Set"),
            ("set-not-from-options", "new Set(t.allowedTools)", "new Set(globalAllow)",
             "no Set membership filter"),
            ("set-dropped", "t.allowedTools?new Set(t.allowedTools):void 0", "void 0",
             "no Set membership filter"),
            ("forEach-loop", "for(let s of builtins)", "builtins.forEach(function(s)",
             "for-of loop"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIn(reason, native_contract_problem(bundle))

    def test_rejection_operands_accept_only_verified_shapes(self):
        for label, old, new, reason in (
            ("side-effecting-call", "gA(s.metadata.name)&&t.includeAgent!==!0",
             "evil(s)&&t.includeAgent!==!0", "not a pure comparison"),
            ("side-effect-in-index", "gA(s.metadata.name)&&t.includeAgent!==!0",
             "gA(t.byName[evil()])&&t.includeAgent!==!0", "not a pure comparison"),
            ("unresolved-constant-set", "t.includeDynamicWorkflow===!1&&wS.has(s.metadata.name)",
             "t.includeDynamicWorkflow===!1&&qQ.has(s.metadata.name)", "not a pure comparison"),
            ("predicate-not-the-template", "function gA(e){return e?nS.has(e):!1}",
             "function gA(e){return !0}", "not a pure comparison"),
            ("predicate-acting", "function gA(e){return e?nS.has(e):!1}",
             "function gA(e){globalFlag=e;return !1}", "not a pure comparison"),
            ("disallow-builder-acting", "function fD(e){if(!e||e.length===0)return;let t=new Set;",
             "function fD(e){spy(e);let t=new Set;", "preamble declaration"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIn(reason, native_contract_problem(bundle))


class TransformTests(unittest.TestCase):
    """The register argument transform must provably keep Read/Glob/Grep."""

    def test_transforms_that_could_replace_a_read_only_tool_fail(self):
        for label, old, new in (
            ("branch-on-read", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="Read"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})'),
            ("branch-on-glob", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="Glob"?eP({})'),
            ("final-else-not-the-tool", ":e}function fT", ":e.name}function fT"),
            ("condition-without-name", ':e.metadata.name===sK&&t.submitResultSchema!==void 0',
             ':t.submitResultSchema!==void 0'),
            ("unresolved-name-constant", 'rC="RespondToCoordinator",sK="submit_result"',
             'rC="RespondToCoordinator"'),
            ("call-in-condition", ':e.metadata.name===sK&&t.submitResultSchema!==void 0',
             ':e.metadata.name===sK&&hZ(t)'),
            ("bare-name-operand", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="EnterPlanMode"||e.metadata.name?eP({})'),
            ("computed-name-lookup", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="EnterPlanMode"||t.nameMap[e.metadata.name]==="Bash"?eP({})'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIn("tool transform does not preserve",
                              native_contract_problem(bundle))

    def test_the_identity_transform_still_qualifies(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))",
                          "for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(s)"))
        self.assertIsNone(native_contract_problem(bundle))


class ResolverChainTests(unittest.TestCase):
    """The resolver's helpers must preserve the read-only names end to end."""

    def test_resolver_shapes_that_discard_or_rewrite_the_allowlist_fail(self):
        for label, old, new, reason in (
            ("read-then-return-void", "function fT(e){let t=nL(e.toolAllowlist);",
             "function fT(e){let t=nL(e.toolAllowlist);return void 0}",
             "does not read the config toolAllowlist"),
            ("read-on-another-member", "let t=nL(e.toolAllowlist);",
             "let t=nL(e.toolDenylist);", "does not read the config toolAllowlist"),
            ("map-result-discarded", "function nL(e){return e?.map(v=>aM(v))}",
             "function nL(e){e?.map(v=>aM(v))}", "alias-map"),
            ("malicious-alias", 'function aM(v){return v==="web_search"?"WebSearch":v}',
             'function aM(v){return v==="Read"?"Bash":v}', "alias map sends a read-only tool name"),
            ("alias-value-unresolved", 'function aM(v){return v==="web_search"?"WebSearch":v}',
             "function aM(v){return v===zZ?zY:v}", "alias-map"),
            ("explore-set-missing-grep", 'dL=["Bash","Glob","Grep","Read","WebFetch","WebSearch","TodoWrite"]',
             'dL=["Bash","Glob","Read","WebFetch","WebSearch","TodoWrite"]',
             "explore filter does not keep"),
            ("explore-set-unresolved", "t.filter(v=>eS.has(v))", "t.filter(v=>qQ.has(v))",
             "explore filter does not keep"),
            ("root-branch-filters", "function aC(e,t){return t&&(e.taskType===\"subagent_child\"?t.includes(rC)?t:[...t,rC]:t)}",
             "function aC(e,t){return t&&(e.taskType===\"subagent_child\"?t.includes(rC)?t:[...t,rC]:t.filter(v=>v!==\"Grep\"))}",
             "root-child"),
            ("unconditional-append", "function aC(e,t){return t&&(e.taskType===\"subagent_child\"?t.includes(rC)?t:[...t,rC]:t)}",
             "function aC(e,t){return [...t,rC]}", "root-child"),
            ("child-key-unresolved", "t.includes(rC)?t:[...t,rC]:t)}",
             "t.includes(qQ)?t:[...t,qQ]:t)}", "root-child"),
            ("unrecognized-return-path", ":t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}",
             ":t?aC(e,t.filter(v=>eS.has(v))):aC(e,[...dL,\"Bash\"])}",
             "does not read the config toolAllowlist"),
            ("root-branch-rewrites-list", "?aC(e,t):t?", "?aC(e,[...t,\"TodoWrite\"]):t?",
             "does not read the config toolAllowlist"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIn(reason, native_contract_problem(bundle))


class CallSiteWiringTests(unittest.TestCase):
    """Only the options-position direct object with the resolver call wires."""

    def test_malformed_call_sites_never_prove_the_wiring(self):
        for label, old, new in (
            ("variable-options", "Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,",
             "Uw(e.registry,e.options"),
            ("spread-options", "Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,",
             "Uw(e.registry,{...e.config.defaults,bashTimeoutPolicy:e.config.bashTimeoutPolicy,"),
            ("duplicate-allowed-tools", "allowedTools:fT(e.config),",
             "allowedTools:fT(e.config),allowedTools:void 0,"),
            ("nested-value", "allowedTools:fT(e.config),", "allowedTools:{value:fT(e.config)},"),
            ("void-value", "allowedTools:fT(e.config),", "allowedTools:void 0,"),
            ("non-config-argument", "allowedTools:fT(e.config),", "allowedTools:fT(e),"),
            ("foreign-resolver", "allowedTools:fT(e.config),", "allowedTools:oT(e.config),"),
            ("resolver-not-called", "allowedTools:fT(e.config),", "allowedTools:e.config.toolAllowlist,"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(bundle))


class DecoyTests(unittest.TestCase):
    """Strings and comments cannot stand in for code the token stream sees."""

    def test_a_comment_decoy_does_not_excuse_an_unconditional_register(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))",
                          "for(let s of builtins)"
                          "/*if(!n||n.has(s.metadata.name))e.register(fO(s,t))*/"
                          "e.register(fO(s,t))"))
        self.assertIn("unrecognized control flow", native_contract_problem(bundle))

    def test_a_string_decoy_does_not_excuse_a_bad_chain(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         ("function Uw(e,t={}){",
                          'var decoy="if(!n||n.has(s.metadata.name))e.register(fO(s,t))";'
                          "function Uw(e,t={}){"),
                         ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))",
                          "for(let s of builtins)e.register(fO(s,t))"))
        self.assertIn("unrecognized control flow", native_contract_problem(bundle))

    def test_an_ambiguous_definition_or_mapping_fails_closed(self):
        decoy = ('var d="function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0;'
                 'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))}";')
        bundle = GOOD_IF_BUNDLE.replace("function Uw(e,t={}){", decoy + "function Uw(e,t={})", 1)
        bundle = mutated(bundle, ("for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))",
                                  "for(let s of builtins)e.register(fO(s,t))"))
        self.assertIsNotNone(native_contract_problem(bundle))
        renamed = mutated(GOOD_IF_BUNDLE,
                          ('r(Uw,"registerBuiltInTools");r(fT,"resolveBuiltInToolAllowlist");',
                           'r(Uw,"registerBuiltInTools");r(fT,"resolveBuiltInToolAllowlist");'
                           'r(other,"registerBuiltInTools");'))
        self.assertIn("names registerBuiltInTools ambiguously", native_contract_problem(renamed))

    def test_a_quoted_membership_test_inside_the_loop_fails(self):
        bundle = mutated(SIMPLE_IF_BUNDLE,
                         ("if(!n||n.has(s.metadata.name))e.register(s)",
                          'if(!n||n.has(s.metadata.name)||"n&&!n.has(s.metadata.name)")e.register(s)'))
        self.assertIn("unrecognized control flow", native_contract_problem(bundle))


class BoundTests(unittest.TestCase):
    """The entry guard: no text, blanks and oversize bundles prove nothing."""

    def test_the_text_must_be_a_bounded_nonempty_string(self):
        for label, value, reason in (
            ("none", None, "no public CLI bundle text was provided"),
            ("bytes", b"function Uw(){}", "no public CLI bundle text was provided"),
            ("blank", "  \n", "no public CLI bundle text was provided"),
            ("oversize", "x" * (32 * 1024 * 1024 + 1), "exceeds the 32 MiB"),
        ):
            with self.subTest(label=label):
                self.assertIn(reason, native_contract_problem(value))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
