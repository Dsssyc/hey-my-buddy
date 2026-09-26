# Local console development

The React 19 console is built with Vite 8 and TypeScript. Use a supported Node 24 LTS version (see `.node-version` and `package.json`) for development; the installed Buddy runtime serves the checked-in build without npm or a Vite server.

```sh
cd apps/console
npm ci
npm run typecheck
npm test
npm run build
```

The build uses a relative base and writes `index.html` and hashed assets into `src/buddy/console_assets/`. Do not put authored documents in that output directory. Commit the rebuilt assets together with source changes; the stable runtime content hash includes them. The private HTTP surface, session and CSRF boundary, and evaluation semantics are documented in the [evaluation reference](../../docs/reference/evaluation.md).

Read [console entry](../../docs/reference/console.md) before testing browser sessions. `console '{"browser":false}'` returns a single-use entry without launching a browser; redeem it once and use the redirected session URL with its cookie. A copied session URL alone authorizes nothing. Real HTTP/CLI tests must stay on private state, and fake snapshots must include a valid `consoleSession` descriptor.

For browser acceptance, serve the built files through `buddy console` against an explicit private `BUDDY_STATE_DIR` and `BUDDY_RUNTIME_ROOT`. Use `BUDDY_DEV_SOURCE=1` when testing the source checkout. Vite's development server does not supply the authoritative API or console session, and it must not proxy an existing production private URL by default.
