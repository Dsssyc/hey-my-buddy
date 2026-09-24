#!/usr/bin/env node
/**
 * Discover the installed DSH harness provider/model catalog without a model call.
 *
 * Design notes:
 * - This reads the *installed* harness package: the adapter's exported configuration
 *   schema resolves the real model list and the legal reasoning-effort options. There
 *   is no hardcoded model table here and no network request.
 * - Only metadata is printed. Credential values, environment variables, API keys and
 *   user settings are never read or emitted.
 * - The provider route name is extracted from the installed adapter artifact; when it
 *   cannot be determined the helper reports an honest error instead of guessing.
 * - A private developer/test override (`BUDDY_MODEL_CATALOG_FILE`) is handled on the
 *   Python side and never reaches this script.
 */
import { createRequire } from 'node:module';
import { existsSync, readFileSync, realpathSync } from 'node:fs';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { pathToFileURL } from 'node:url';

/** Adapter packages whose installed configuration schema owns a provider catalog. */
const ADAPTER_PACKAGES = ['@deepseek-ai/dsh-llm-deepseek'];
const MAX_MODELS = 200;
const MAX_TEXT = 2000;

function fail(message, code = 'CATALOG_UNAVAILABLE') {
  process.stdout.write(JSON.stringify({ error: { code, message: String(message).slice(0, MAX_TEXT) } }));
  process.exit(2);
}

function bounded(value) {
  return typeof value === 'string' ? value.slice(0, MAX_TEXT) : value;
}

function candidateRoots() {
  const roots = [];
  if (process.env.DSH_PACKAGE_ROOT) roots.push(process.env.DSH_PACKAGE_ROOT);
  const home = process.env.DSH_HOME || join(homedir(), '.dsh');
  const bin = process.env.DSH_BIN || findOnPath('dsh');
  if (bin) {
    try {
      const app = dirname(dirname(realpathSync(bin))); // <app>/bin/dsh -> <app>
      roots.push(join(app, 'lib', 'node_modules'), app);
    } catch {}
  }
  roots.push(join(home, 'profiles', 'headless'));
  roots.push(join(homedir(), '.local', 'share', 'dsh', 'lib', 'node_modules'));
  roots.push(join(homedir(), '.local', 'share', 'dsh'));
  return [...new Set(roots)];
}

function findOnPath(name) {
  for (const directory of (process.env.PATH || '').split(':')) {
    if (!directory) continue;
    const candidate = join(directory, name);
    if (existsSync(candidate)) return candidate;
  }
  return null;
}

function resolveDshPackage() {
  for (const root of candidateRoots()) {
    const require = createRequire(join(root, 'package.json'));
    for (const specifier of ['@deepseek-ai/dsh/package.json', '@deepseek-ai/dsh']) {
      try {
        return require.resolve(specifier);
      } catch {}
    }
  }
  return null;
}

function packageDirectory(resolvedFile) {
  let directory = dirname(resolvedFile);
  for (let depth = 0; depth < 6; depth += 1) {
    const candidate = join(directory, 'package.json');
    if (existsSync(candidate)) return candidate;
    const parent = dirname(directory);
    if (parent === directory) break;
    directory = parent;
  }
  return null;
}

function unionValues(schema) {
  if (!schema || schema.type !== 'union' || !Array.isArray(schema.list)) return [];
  const values = [];
  for (const entry of schema.list) {
    if (entry && entry.type === 'const' && typeof entry.value === 'string' && !values.includes(entry.value)) {
      values.push(entry.value);
    }
  }
  return values;
}

function providerIdFromSource(text) {
  for (const pattern of [/const\s+PROVIDER\s*=\s*"([^"]+)"/, /registerAdapter\(\s*\[\s*"([^"]+)"/]) {
    const match = pattern.exec(text);
    if (match) return match[1];
  }
  return null;
}

function modelEntry(value) {
  if (!value || typeof value.id !== 'string' || !value.id) return null;
  const modalities = Array.isArray(value.inputModalities)
    ? value.inputModalities.filter((item) => typeof item === 'string').slice(0, 8)
    : ['text'];
  return {
    id: value.id,
    name: typeof value.name === 'string' && value.name ? value.name : value.id,
    description: bounded(typeof value.description === 'string' ? value.description : ''),
    contextWindow: Number.isInteger(value.contextWindow) && value.contextWindow > 0 ? value.contextWindow : null,
    inputModalities: modalities.length ? modalities : ['text'],
  };
}

const dshPackageJson = resolveDshPackage();
if (!dshPackageJson) {
  fail('The installed DSH harness package was not found (looked under DSH_PACKAGE_ROOT, the dsh launcher, DSH_HOME and ~/.local/share/dsh)');
}
const dshRequire = createRequire(dshPackageJson);
let dshVersion = null;
try {
  dshVersion = JSON.parse(readFileSync(dshPackageJson, 'utf8')).version || null;
} catch {}

const providers = [];
const warnings = [];
for (const specifier of ADAPTER_PACKAGES) {
  let resolved;
  try {
    resolved = dshRequire.resolve(specifier);
  } catch (error) {
    warnings.push(`${specifier}: not installed in the resolved harness`);
    continue;
  }
  let module;
  try {
    module = await import(pathToFileURL(resolved).href);
  } catch (error) {
    warnings.push(`${specifier}: import failed (${error && error.message ? error.message : error})`);
    continue;
  }
  if (typeof module.Config !== 'function') {
    warnings.push(`${specifier}: installed artifact exposes no configuration schema`);
    continue;
  }
  let resolvedConfig;
  try {
    resolvedConfig = module.Config({});
  } catch (error) {
    warnings.push(`${specifier}: configuration schema could not be resolved (${error && error.message ? error.message : error})`);
    continue;
  }
  const models = Array.isArray(resolvedConfig.models)
    ? resolvedConfig.models.map(modelEntry).filter(Boolean).slice(0, MAX_MODELS)
    : [];
  if (!models.length) {
    warnings.push(`${specifier}: the installed artifact advertises no models`);
    continue;
  }
  const efforts = unionValues(module.Config.dict && module.Config.dict.reasoningEffort);
  let providerId = null;
  try {
    providerId = providerIdFromSource(readFileSync(resolved, 'utf8'));
  } catch {}
  if (!providerId) {
    warnings.push(`${specifier}: the installed artifact does not declare its provider route`);
    continue;
  }
  let packageName = specifier;
  let packageVersion = null;
  const manifest = packageDirectory(resolved);
  if (manifest) {
    try {
      const value = JSON.parse(readFileSync(manifest, 'utf8'));
      packageName = value.name || packageName;
      packageVersion = value.version || null;
    } catch {}
  }
  providers.push({
    provider: providerId,
    displayName: providerId,
    packageName,
    packageVersion,
    adapter: 'dsh',
    efforts,
    models,
  });
}

if (!providers.length) {
  fail(`No installed DSH provider catalog could be read. ${warnings.join('; ')}`.slice(0, MAX_TEXT));
}

process.stdout.write(
  JSON.stringify({
    source: `dsh:${dirname(dshPackageJson)}`,
    harnessVersion: dshVersion,
    providerVersion: providers
      .map((entry) => `${entry.packageName}@${entry.packageVersion || 'unknown'}`)
      .join(', '),
    discoveredAt: new Date().toISOString(),
    providers,
    warnings,
  })
);
