"""L6-A3/A4 free static contract: token-level templates, lexical context, counterexamples.

The L6-A2 review confirmed two bundles the old regex assembly wrongly accepted
— a membership test dropped before an unconditional register, and an
``allowedTools`` object parked in an unread third argument — so those
regressions come first here. The L6-A3 full-template review then confirmed
seven more escapes — a hoisted allowlist declared after its loop, rebinding
the options parameter or the Set constructor, an always-true transform
condition, decoys in comments, strings, templates and regex literals, a formal
parameter impersonating a module constant, a truncated or rewritten constant,
and an escaped string spelling a read-only tool name as another value — saved
below with their scope and write variants. The rest of the matrix pins the
supported registration flows, the tool-preserving transform, the resolver
helper chain and the call-site wiring against renamed identifiers and every
rejected shape in the fixed L6-A3/A4 designs. All bundles are synthetic
minified text whose identifiers differ from the real installation; the
installed text itself is verified by the retained installed test in
``test_zcode_read_only_protocol.py``.
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


# -- the seven confirmed L6-A4 escapes, saved as fixed counterexamples ----------

#: The installed if-form registration, kept here as one editable reference for
#: the A4 mutations that follow.
A4_REGISTER = ('function Uw(e,t={}){let n=t.allowedTools?new Set(t.allowedTools):void 0,'
               'o=fD(t.disallowedTools);'
               'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t))}')


class A4OrderingTests(unittest.TestCase):
    """Hoisting order: declarations precede the one loop or nothing qualifies."""

    def test_a_var_allowlist_declared_after_the_loop_fails(self):
        bundle = mutated(GOOD_IF_BUNDLE, (A4_REGISTER,
                                          'function Uw(e,t={}){'
                                          'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t));'
                                          'var n=t.allowedTools?new Set(t.allowedTools):void 0,'
                                          'o=fD(t.disallowedTools)}'))
        self.assertIn("does not carry all of its declarations before",
                      native_contract_problem(bundle))

    def test_a_declaration_after_the_loop_fails(self):
        bundle = mutated(GOOD_IF_BUNDLE, (A4_REGISTER,
                                          'function Uw(e,t={}){'
                                          'let n=t.allowedTools?new Set(t.allowedTools):void 0,'
                                          'o=fD(t.disallowedTools);'
                                          'for(let s of builtins)if(!n||n.has(s.metadata.name))e.register(fO(s,t));'
                                          'let tail=1}'))
        self.assertIn("does not carry all of its declarations before",
                      native_contract_problem(bundle))

    def test_the_loop_initialization_still_precedes_the_gated_flow(self):
        self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE))


class A4BindingTests(unittest.TestCase):
    """Complete bindings: rebinding a proof name never proves the template."""

    def test_rebindings_of_the_proof_names_fail(self):
        cases = (
            ("options-shadowed-by-local",
             SIMPLE_IF_BUNDLE.replace("function Uw(e,t={}){let n=t.allowedTools?",
                                      "function Uw(e,t={}){var t={allowedTools:void 0};let n=t.allowedTools?"),
             "rebinds its parameters"),
            ("set-constructor-shadowed",
             SIMPLE_IF_BUNDLE.replace("function Uw(e,t={}){let n=t.allowedTools?",
                                      "function Uw(e,t={}){let Set=t.alternateSet,n=t.allowedTools?"),
             "rebinds its parameters"),
            ("registry-shadowed-by-local",
             SIMPLE_IF_BUNDLE.replace("function Uw(e,t={}){",
                                      "function Uw(e,t={}){var e=otherRegistry;"),
             "rebinds its parameters"),
            ("loop-variable-shadowed",
             GOOD_IF_BUNDLE.replace("o=fD(t.disallowedTools);",
                                    "o=fD(t.disallowedTools),s=1;"),
             "rebinds its parameters"),
            ("duplicate-parameters",
             SIMPLE_IF_BUNDLE.replace("function Uw(e,t={}){", "function Uw(e,e){"),
             "rebinds its parameters"),
            ("helper-shadowed-by-local",
             GOOD_BUNDLE.replace("function Uw(e,t={}){let n=",
                                 "function Uw(e,t={}){let gA=proxy,n="),
             "not a pure comparison"),
        )
        for label, bundle, reason in cases:
            with self.subTest(label=label):
                problem = native_contract_problem(bundle)
                self.assertIsNotNone(problem)
                self.assertIn(reason, problem)

    def test_unsafe_parameter_defaults_fail(self):
        for label, header in (
            ("default-carrying-allowlist", "function Uw(e,t={allowedTools:['Bash']}){"),
            ("void-default", "function Uw(e,t=void 0){"),
            ("helper-default", "function fO(e,t=sK){"),
        ):
            with self.subTest(label=label):
                bundle = GOOD_IF_BUNDLE.replace(
                    "function Uw(e,t={}){" if label != "helper-default" else "function fO(e,t){",
                    header)
                self.assertIn("unsupported parameter default", native_contract_problem(bundle))

    def test_writes_to_the_options_or_set_fail(self):
        for label, old, new in (
            ("options-written-before-loop",
             "function Uw(e,t={}){let n=", "function Uw(e,t={}){t=otherOptions;let n="),
            ("set-compound-update",
             "o=fD(t.disallowedTools);", "o=fD(t.disallowedTools);n+=other;"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (old, new))
                self.assertIsNotNone(native_contract_problem(bundle))


class A4ConditionTests(unittest.TestCase):
    """A transform condition must imply the tool name is not a read-only one."""

    def test_conditions_without_the_implication_fail(self):
        for label, old, new in (
            ("or-true", 'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return e.metadata.name==="Bash"||true?'),
            ("and-side-effect", 'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return e.metadata.name==="Bash"&&evil()?'),
            ("negated-name", 'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return !(e.metadata.name==="Read")?'),
            ("ternary-around-the-name", 'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return (e.metadata.name==="Bash"?1:2)?'),
            ("and-side-effect-last", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="EnterPlanMode"&&evil()?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (old, new))
                self.assertIn("tool transform does not preserve",
                              native_contract_problem(bundle))

    def test_name_or_name_and_pure_flags_still_qualify(self):
        for label, old, new in (
            ("or-of-names", 'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return e.metadata.name==="A"||e.metadata.name==="B"?'),
            ("and-of-name-and-flag", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="EnterPlanMode"&&t.embeddedSearchEnabled!==void 0?eP({})'),
            ("and-of-name-and-pure-global", ':e.metadata.name==="EnterPlanMode"?eP({embeddedSearchEnabled:t.embeddedSearchEnabled})',
             ':e.metadata.name==="EnterPlanMode"&&evilFlag?eP({})'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (old, new))
                self.assertIsNone(native_contract_problem(bundle))


class A4OuterContextTests(unittest.TestCase):
    """Strings, comments, templates and regex literals prove nothing."""

    def test_decoy_contexts_never_provide_the_wiring(self):
        real_call = ("function refresh(e){Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,"
                     "includeSkill:!!e.skillPort,embeddedSearchEnabled:!1,allowedTools:fT(e.config),"
                     "disallowedTools:e.config.toolDisallowlist,silentDuplicateWarnings:!0})}")
        for label, decoy in (
            ("comment", "/*Uw(e.registry,{allowedTools:fT(e.config)})*/"),
            ("string", "var d='Uw(e.registry,{allowedTools:fT(e.config)})';"),
            ("template", "var d=`Uw(e.registry,{allowedTools:fT(e.config)})`;"),
            ("template-with-interpolation",
             "var d=`x${p}Uw(e.registry,{allowedTools:fT(e.config)})`;"),
            ("regex", r"var re=/Uw\(e\.registry,\{allowedTools:fT\(e\.config\)\}\)/;"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE,
                                 (real_call, decoy + "function refresh(e){Uw(e.registry,{})}"))
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(bundle))

    def test_decoy_contexts_never_provide_definitions_or_constants(self):
        for label, old, new in (
            ("resolver-defined-in-a-string",
             'function fT(e){let t=nL(e.toolAllowlist);'
             'return e.toolset!=="explore"?aC(e,t):t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}',
             "var decoy='function fT(e){let t=nL(e.toolAllowlist);"
             'return e.toolset!==\"explore\"?aC(e,t):t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}\';'),
            ("resolver-defined-in-a-regex",
             'function fT(e){let t=nL(e.toolAllowlist);'
             'return e.toolset!=="explore"?aC(e,t):t?aC(e,t.filter(v=>eS.has(v))):aC(e,dL)}',
             r"var decoy=/function fT\(e\)\{return e\.toolAllowlist\}/;"),
            ("constant-declared-in-a-comment",
             'sK="submit_result"', '/*sK="submit_result"*/'),
            ("export-named-in-a-string",
             'r(Uw,"registerBuiltInTools");', 'var d=\'r(Uw,"registerBuiltInTools")\';'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIsNotNone(native_contract_problem(bundle))

    def test_the_real_export_string_argument_still_parses(self):
        self.assertIsNone(native_contract_problem(GOOD_IF_BUNDLE))


class A4LexicalBindingTests(unittest.TestCase):
    """Every proof reference resolves in its own bound environment."""

    def test_shadowed_proof_references_fail(self):
        for label, old, new in (
            ("alias-formal-shadows-the-key-constant",
             'function aM(v){return v==="web_search"?"WebSearch":v}',
             'var v="web_search";function aM(v){return v===v?"Bash":v}'),
            ("transform-formal-shadows-the-name-constant",
             'function fO(e,t){return e.metadata.name==="Bash"?bR({bashTimeoutPolicy:t.bashTimeoutPolicy})'
             ':e.metadata.name===sK&&t.submitResultSchema!==void 0?sU(t.submitResultSchema)',
             'function fO(e,sK){return e.metadata.name==="Bash"?bR({bashTimeoutPolicy:sK.bashTimeoutPolicy})'
             ':e.metadata.name===sK&&sK.submitResultSchema!==void 0?sU(sK.submitResultSchema)'),
            ("resolver-formal-shadows-the-explore-set",
             "function fT(e){let t=nL(e.toolAllowlist);",
             "function fT(e,eS){let t=nL(e.toolAllowlist);"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (old, new))
                self.assertIsNotNone(native_contract_problem(bundle))

    def test_shadowed_call_scopes_never_wire_the_functions(self):
        real_call = ("function refresh(e){Uw(e.registry,{bashTimeoutPolicy:e.config.bashTimeoutPolicy,"
                     "includeSkill:!!e.skillPort,embeddedSearchEnabled:!1,allowedTools:fT(e.config),"
                     "disallowedTools:e.config.toolDisallowlist,silentDuplicateWarnings:!0})}")
        for label, caller in (
            ("resolver-parameter", "function refresh(e,fT){Uw(e.registry,{allowedTools:fT(e.config)})}"),
            ("register-parameter", "function refresh(e,Uw){Uw(e.registry,{allowedTools:fT(e.config)})}"),
            ("late-var-after-the-call",
             "function refresh(e){Uw(e.registry,{allowedTools:fT(e.config)});var fT}"),
            ("arrow-parameter-around-the-call",
             "function refresh(e){var g=(fT)=>Uw(e.registry,{allowedTools:fT(e.config)});}"),
            ("default-parameter-shadow",
             "function refresh(e,Uw=otherRegister){Uw(e.registry,{allowedTools:fT(e.config)})}"),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (real_call, caller))
                self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                              native_contract_problem(bundle))

    def test_a_property_member_is_not_an_identifier_binding(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         ("function refresh(e){Uw(e.registry,{",
                          "function refresh(e){e.Uw();e.fT();Uw(e.registry,{"))
        self.assertIsNone(native_contract_problem(bundle))


class A4ConstantTests(unittest.TestCase):
    """Constants consume whole right-hand sides and admit no unrecognized write."""

    def test_incomplete_or_rewritten_constants_fail(self):
        for label, old, new in (
            ("concatenated-right-hand-side", 'sK="submit_result"', 'sK="Re"+"ad"'),
            ("unknown-later-write", 'sK="submit_result"', 'sK="submit_result";sK=getName()'),
            ("compound-write", 'sK="submit_result"', 'sK="submit_result";sK+="x"'),
            ("update-write", 'sK="submit_result"', 'sK="submit_result";sK++'),
            ("destructuring-binding", 'sK="submit_result"', 'var [sK]=arr'),
            ("collection-mutation",
             'wS=new Set(["Agent","Task"])', 'wS=new Set(["Agent","Task"]);wS.add("Read")'),
            ("index-write",
             'wS=new Set(["Agent","Task"])', 'wS=new Set(["Agent","Task"]);wS[0]="Read"'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_BUNDLE, (old, new))
                self.assertIsNotNone(native_contract_problem(bundle))

    def test_an_unrelated_shadowed_write_does_not_unsolve_the_constant(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         (MAPPING_BODY,
                          MAPPING_BODY + 'function unrelated(){var sK=other;sK=1;return sK}'))
        self.assertIsNone(native_contract_problem(bundle))


class A4EscapeTests(unittest.TestCase):
    """Proof regions carrying escaped string literals are rejected outright."""

    def test_escaped_literals_never_count_as_resolved_values(self):
        for label, old, new in (
            ("escaped-tool-name-in-the-transform",
             'function fO(e,t){return e.metadata.name==="Bash"?',
             'function fO(e,t){return e.metadata.name==="R\\x65ad"?'),
            ("escaped-alias-key",
             'function aM(v){return v==="web_search"?"WebSearch":v}',
             'function aM(v){return v==="web_sear\\x63h"?"WebSearch":v}'),
            ("escaped-constant-value", 'sK="submit_result"', 'sK="submit\\x5fresult"'),
        ):
            with self.subTest(label=label):
                bundle = mutated(GOOD_IF_BUNDLE, (old, new))
                self.assertIsNotNone(native_contract_problem(bundle))

    def test_an_escaped_options_key_fails_the_wiring(self):
        bundle = mutated(GOOD_IF_BUNDLE,
                         ("allowedTools:fT(e.config),", '"\\x61llowedTools":fT(e.config),'))
        self.assertIn("not called with allowedTools from resolveBuiltInToolAllowlist",
                      native_contract_problem(bundle))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
