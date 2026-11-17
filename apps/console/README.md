# Local console development

The React 19 console is built with Vite 8 and TypeScript. Use a supported Node 24 LTS version (see `.node-version` and `package.json`) for development; the installed Buddy runtime serves the checked-in build without npm or a Vite server.

```sh
cd apps/console
npm ci
npm run typecheck
npm test
npm run build
```

The build uses a relative base and writes `index.html` and hashed assets into `src/hey_my_buddy/console/assets/`. Do not put authored documents in that output directory. Commit the rebuilt assets together with source changes; the stable runtime content hash includes them. The private HTTP surface, session and CSRF boundary, and evaluation semantics are documented in the [evaluation reference](../../docs/reference/evaluation.md).

Read [console entry](../../docs/reference/console.md) before testing browser sessions. `console '{"browser":false}'` returns a single-use entry without launching a browser; redeem it once and use the redirected session URL with its cookie. A copied session URL alone authorizes nothing. Real HTTP/CLI tests must stay on private state, and fake snapshots must include a valid `consoleSession` descriptor.

For browser acceptance, serve the built files through `hey-my-buddy console` against an explicit private `HMB_STATE_DIR` and `HMB_RUNTIME_ROOT`. Use `HMB_DEV_SOURCE=1` when testing the source checkout. Vite's development server does not supply the authoritative API or console session, and it must not proxy an existing production private URL by default.

Generate the four README timeline images from the current source with Node 24 and the existing Playwright Chromium installation:

```sh
npm --prefix apps/console run screenshot:readme
```

Run this from the repository root after `npm --prefix apps/console ci`. If Chromium is missing, install it once with `PLAYWRIGHT_SKIP_BROWSER_GC=1 npm --prefix apps/console exec -- playwright install --only-shell chromium`. The command typechecks and builds into `tmp/readme-shot/assets`, starts only the synthetic preview's `readme` scenario, checks the real console in one browser with one Playwright worker, and writes `docs/assets/console-timeline-{en,zh}-{light,dark}.png`. It neither opens a board nor calls a model. Results stay under `tmp/readme-shot/playwright`; the ordinary browser suite does not include this capture. `HMB_PREVIEW_PYTHON` can select the preview's Python interpreter.

The authored story lives in `tests/probes/readme_console_scene.py`, built with the preview's existing record constructors. Preview it manually with `python3 tests/probes/macro_task_console_preview.py --assets "$PWD/tmp/readme-shot/assets" --scenario readme --language en` (or `zh`). The capture fixes a 1280 × 820 CSS-pixel viewport, 2× pixel ratio, UTC, `2026-09-27T10:00:00.000Z`, `en-US`/`zh-CN`, light/dark preference and reduced motion through browser settings. It waits for fonts and finite animations, without injecting page elements, changing styles or editing the captured PNGs. Identical bytes are expected on repeated runs on the same machine and browser/font installation; cross-platform font rendering can differ.
