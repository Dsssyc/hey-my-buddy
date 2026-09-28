# Model effort display correction

Verified on 2026-09-24 against the 0.6 contract. The user reported `DeepSeek-V41-Flash · off · off` in the decision-profile selector. Catalog labels already carried the native effort suffix, and the UI appended it again. The same duplication affected other efforts and profile selectors.

DSH Flash/max completed governed run `999a0a84-a1e9-40f3-aa81-c7e6ddccb7f1` in an isolated worktree based on `e0fb9435a8893661401c3eca80accd869d5f2ddd`. Sealed output `137e9bf7044bffc45cd5c2391731280df2d3f569` changed eight files under `apps/console/src`; artifact `99804e0f-cf0c-482b-99f8-77eb09933fcd` has diff SHA-256 `f1972f158d1d2bb68f7c790b92f4fe22a0531fd97429707a011c120d7e7a6bce`. The Host checked that the integrated source matched the sealed output exactly and acknowledged the artifact after verification. The task reported confirmed self and descendant shutdown.

The shared display helper removes only the label's exact matching effort suffix, preserves custom names and model fallbacks, and displays `off` as `非思考`. Other native effort names remain unchanged. Decision/helper/routing selectors, model cards and related summaries use the same formatting. The underlying profile ID, label, effort, routing policy and submitted configuration are unchanged.

The Host ran the new decision-selector regression against the original components first. It failed specifically on `off · off` and `max · max`. After applying the fix, all 35 frontend tests passed, including ten new cases for catalog-shaped profiles, custom/unknown values, rendered selectors and raw payload preservation. TypeScript checking and the Vite production build passed with Node 24.15.0; the resulting asset is `index-DV2jcvu0.js`. The Python and DSH execution code was unchanged, so their broad suites were not repeated. Raw red/green/build logs are retained locally under `tmp/profile-label-*.log`.

The built console was checked in the real browser on stable runtime `3c4a8b092a16b329313add56771b6904`. The decision selector displayed `DeepSeek-V41-Flash · 非思考`, `DeepSeek-V41-Flash · max` and `GLM-5.3 · high`. Model cards showed the model name and one effort badge. A screenshot confirmed the corrected selector, and the browser reported no console errors or warnings. The published profiles, preferences and configuration remained identical at table revision 1 before and after the update.

The source fix was integrated only into `socu/buddy-core`. The original checkout and the excluded CodeBuddy branch were not changed.
