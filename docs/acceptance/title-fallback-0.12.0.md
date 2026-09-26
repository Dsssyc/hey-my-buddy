# Task title fallback: 0.12.0

## Scope and status

This source candidate implements the next bounded slice of the Claude proposal's section XI.3: readable task-title fallback without changing schema 11. Release and named C-Two contract advance to 0.12.0 for the new task-read projection. The installed daily service remains 0.11.0 until a separately coordinated installation. Macro tasks, explicit submitted title fields, timeline, outcome ledger and routing changes remain outside this slice.

The display chooses the latest concluded outcome summary from the same governed run, then the original task's trimmed first line, then 未命名委派. The projection is capped at 2000 Unicode characters; the list and detail headings share the existing 100-character Unicode-safe excerpt. A later prepared/running turn does not erase the latest concluded summary, and a helper's summary cannot become its parent's title. The original task, its fingerprint, execution and raw-task search semantics remain unchanged.

## Verification

The Host inspected the fixed implementation and independently ran the checks below. The source candidate is verified; the daily 0.11.0 installation has not been replaced. Computer Use is excluded by the user's instruction, so this record claims component/DOM and interface verification, not real-browser visual acceptance.

The initial artifact's eight backend tests and frontend checks missed three cases that the Host reproduced before correcting them: json_extract serializes object/array summaries as text, the projection added a second SELECT per task-extension read, and splitting before trimming changed leading-blank-line tasks to the unnamed marker. The corrected query uses json_type='text', selects the six required workflow fields and the indexed correlated result in one statement, and preserves JSON-looking strings as legitimate text. The frontend trims the task before taking its first line. The review was recorded as rejected before correction, not silently relabelled as first-pass success.

The ten focused backend tests now cover all task-read entrypoints, null and malformed summaries, literal JSON-looking text, latest turn-index ordering, running continuations, helper isolation, the 2000-character bound, read-only behavior and the one-statement extension read. The actual App component test also checks that displayed summaries require no per-row detail/workflow fetch and that an older selected row follows a bounded workflow refresh. Duplicate helper-to-helper comparisons in the original component test were replaced with observable heading assertions.

The required uv run --frozen python -m buddy.checks completed with exit 0: 1,055 Python tests passed in 1026.886 seconds, followed by 207 Node tests with zero failures, cancellations or skips. The complete console Vitest run passed 220 tests across 20 files. With Node 24.19.0, npm run build passed tsc --noEmit and Vite production compilation; the new JavaScript asset is index-BrZGSgvF.js, and the CSS asset remains index-CZU-CAXh.css. Raw logs are under .dsh-skill-build/title-fallback-20260926-0P5v2F/. The six current-contract/schema tests also passed; schema remains 11.

## Collaboration

GLM-5.3-Flash/max implemented the bounded Python projection and React consumption in governed goal 7cfd4983-bc1d-45a1-9e0c-3b3dda8d4781, based on e3737b9 in an independent worktree. Its initial output was artifact ab1bf94b-c4d8-46ae-9ef4-72c4d0bb4c14, sealed at 7351ef6aa37bac774e8e34bfa20e679342cf5cba. After the rejected Host review, the same goal continued in its proven ZCode native session and applied the Host-authored fixed patch, SHA-256 da8dc31e9450934d1132e8c5f151416afd15925b4f909bf50b33d025ee7e0d8e. It rechecked the ten backend tests and 21 targeted frontend tests, then delivered corrected artifact 1168d86a-18a3-4043-94f5-840ecea4c75f, sealed at eea3e825dc2d823e79b277dd4d473dc57ec1b47c with confirmed shutdown. The Host independently compared all nine whole-goal source/test paths with that fixed tree; they match exactly.

The Host owns release/contract coordination, documentation, final bundle build and artifact acceptance. No Claude probe or model-card policy edit was part of this task. The user's separate uncommitted production-repairs-0.8.0.md remained unchanged.

## Integration and packaging

The verified implementation was committed as ef70240 on socu/buddy-core. Whole-goal integration int-a53ce6df-90d5-482c-8a45-a02e8afc2167 binds the corrected artifact to that target; the accepted review explicitly records the three Host corrections and the failed first pass. Cleanup plan cln-d4022994-d6cc-4a98-9fd3-e71c1d9f7aaf then removed the original physical checkout for this two-turn goal. Both logical workspace generations retain their input/output refs, patches, manifests and review/command receipts.

Committed source ef70240 was exported independently and staged as a distributable 0.12.0 plugin, excluding the user's uncommitted document. The private candidate check confirmed contract 0.12.0/schema 11, stable runtime 80f359daaed20e0a822926838cee66f6, correct served JavaScript/CSS bytes, live private capacity publication, command completion/cancellation without an execution deadline, and continued operation after the staged source moved. It made zero model calls and stopped its private service and supervisors. Logs and the staged package remain under .dsh-skill-build/title-fallback-20260926-0P5v2F/.

The daily board was not restarted or upgraded: a final ping still identified service 633c13f8-da09-4efb-af8c-936df5f4b4a0 on contract 0.11.0/schema 11. Deployment of the new read contract remains a separate coordinated step. Documentation checks passed for 53 files, 315 local links, 51 JSON examples and 38 shell examples, alongside skill validation and git diff --check.
