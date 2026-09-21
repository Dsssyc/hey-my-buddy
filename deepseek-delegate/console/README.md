# Local console development

The React 19 console is built with Vite 8 and TypeScript. Use a supported Node 24 LTS version (see `.node-version` and `package.json`) for development; the installed Buddy runtime serves the checked-in build without npm or a Vite server.

```sh
cd deepseek-delegate/console
npm ci
npm run typecheck
npm test
npm run build
```

The build uses a relative base and replaces `../python/buddy/console_assets/` with `index.html` and hashed assets. Do not put authored documents in that output directory. Commit the rebuilt assets together with source changes; the stable runtime content hash includes them. Runtime behavior and the private HTTP boundary are documented in [the evaluation reference](../references/evaluation.md).

For browser acceptance, serve the built files through `buddy console` against an explicit private `BUDDY_STATE_DIR` and `BUDDY_RUNTIME_ROOT`. Use `BUDDY_DEV_SOURCE=1` when testing the source checkout. Vite's development server does not supply the authoritative API or console session, and it must not proxy an existing production private URL by default.
