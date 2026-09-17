import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const project = fileURLToPath(new URL('../../', import.meta.url));

/** Parse through the uv-managed Python environment; no JavaScript dependencies. */
export function loadYaml(text, { mode = 'json' } = {}) {
  const python = process.env.BUDDY_PYTHON;
  const command = python || 'uv';
  const args = python ? ['-m', 'buddy.yaml_bridge'] : ['run', '--frozen', '--project', project, 'python', '-m', 'buddy.yaml_bridge'];
  const result = spawnSync(command, args, {
    input: JSON.stringify({ text, mode }), encoding: 'utf8', timeout: 30000,
    maxBuffer: Math.max(1024 * 1024, Buffer.byteLength(text) * 8), windowsHide: true,
  });
  if (result.error || result.status !== 0) throw new Error('YAML parsing failed; check the uv environment and input syntax');
  try { return JSON.parse(result.stdout); } catch { throw new Error('YAML bridge returned invalid JSON'); }
}
