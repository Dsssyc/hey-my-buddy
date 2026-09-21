# Console assets

This directory is the production location for the built React/Vite console bundle, served by the private loopback console (`python/buddy/console.py`) below the private URL prefix.

The frontend workstream owns the source project (`deepseek-delegate/console/`) and writes its build output here as `index.html` plus hashed `assets/…` files. Nothing in this directory is a mock UI: when `index.html` is absent, the console answers with an honest setup page and the JSON API keeps working, so the backend can be verified without a frontend build.

Serving rules:

- only an extension allowlist is served (`.html`, `.js`, `.mjs`, `.css`, `.json`, `.map`, `.svg`, images, fonts, `.wasm`, `.txt`);
- paths are resolved under this directory, symlinks escaping it and any `..` segment are refused, and directories are never listed;
- `index.html` is also served for extension-less navigation paths outside `/assets` and `/api`, so client-side routing works with Vite's relative base;
- running the distributed backend never requires npm; packaging stages this directory with the Python package.
