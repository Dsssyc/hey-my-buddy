# Claude Code Worker P1: 0.11.0

## Scope and status

The user authorized only section VI / section XI step 1 of the [Claude proposal](../decisions/012-claude-code-distribution-and-evidence-routing.md) on 2026-09-26. Source implementation and verification are in progress. The proposal was copied from the Claude worktree and its index entry integrated as 6fdef7d; the other proposal sections and all section XII policy choices remain unapproved for implementation. The separate accepted performance ADR also carries number 012, so links use full filenames to distinguish them.

The candidate uses release/contract 0.11.0 and retains schema 11. The installed daily service remains 0.10.0; the user expressly deferred installation. No user preference, enabled model, capacity setting, routing policy, native global setting or automatic maintenance schedule is changed for this work.

## Native metadata checks

On 2026-09-26 local time, tests/probes/claude_catalog.py launched installed Claude Code 2.1.282 in a private directory, sent exactly one initialize control request, sent zero user/model messages, and observed an exit-zero child with confirmed process-group shutdown. The second run added permission-prompt-tool stdio and a preallocated session UUID to confirm those flags at initialization. Neither run establishes a paid model-turn behavior. Sanitized reports are retained under .dsh-skill-build/claude-p1-20260926/catalog-native-1 and catalog-native-2.

Native metadata reported apiProvider firstParty and tokenSource none. The same invocation environment's auth status reported loggedIn false and authMethod none. Thus execution must report unavailable until the native CLI is authenticated; this observation does not make a claim about authentication in another Claude application or Host session.

The directory advertised default and opus[1m] resolving to claude-opus-5-5[1m], Fable resolving to claude-fable-5-1, Sonnet resolving to claude-sonnet-5, and Haiku resolving to claude-haiku-4-5-20251001. Haiku had no supportedEffortLevels. Discovery uses canonical resolvedModel IDs, skips default, deduplicates canonical models, and maps missing effort levels to default without an effort CLI flag. These are directory facts, not served-model attestation or demonstrated coding capability.

## Identifier boundary

A regression using the real native [1m] suffix first failed because generated profile IDs did not satisfy the existing card/preference/evidence identifier contract. The catalog now keeps the canonical native model unchanged and derives a stable legal profile ID with a hash when encoding is needed, avoiding collisions with a literal normalized name. This small catalog change is required to make the requested native identity usable through current publication operations; it does not change the schema or routing policy. The catalog/current-core affected run passed 14 tests after the correction.

## Remaining user inputs and real probes

The section XII settings-source choice is pending: whether Worker execution uses only Buddy private settings or also project settings/CLAUDE.md. The isolated policy can be exercised explicitly by fixtures while the adapter refuses execution without an explicit policy choice. This is not a global default decision made for the user.

Every paid native probe requires the user's prior approval for that run, as expressly requested. No paid Claude probe has run. The native CLI also requires login before such probes can establish structured output, permission denial/attention, interruption, sandbox enforcement or effort readback. Native resume and inquiry are outside P1 and are not declared.
