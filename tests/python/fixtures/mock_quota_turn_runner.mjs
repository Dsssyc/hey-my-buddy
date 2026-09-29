/** Offline quota failure after real edits; later turns use the existing mock protocol. */
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
function flag(name) {
  const index = process.argv.indexOf(name);
  if (index >= 0) return process.argv[index + 1];
  return process.argv.find(value => value.startsWith(name + '='))?.slice(name.length + 1);
}
const input = JSON.parse(readFileSync(flag('--turn-input-file'), 'utf8'));
const cwd = flag('--cwd');
if (input.context.turnIndex === 1) {
  writeFileSync(join(cwd, 'tracked.txt'), 'partial work preserved after quota failure\n');
  const usagePath = flag('--usage-file');
  if (usagePath) writeFileSync(usagePath, JSON.stringify({version: 1, taskId: input.taskId,
    attemptId: input.attemptId, generation: input.generation, updatedAt: new Date().toISOString(),
    nativeUsage: {source: 'dsh/session-assistant-usage', tokenUsage: {inputBasis: 'excludes-cached',
      inputTokens: 100, cachedInputTokens: 20, outputTokens: 5, records: 1, completeness: 'partial'},
      failure: {code: 'QUOTA'}, lastAssistantMessage: {text: 'The first change is saved; verification remains.'}}
  }), {mode: 0o600});
  process.stdout.write(JSON.stringify({status: 'error', code: 'quota-rejected', modelStarted: true,
    error: 'offline native quota fixture', processState: {shutdownConfirmed: true},
    quotaFailure: {rateLimitType: 'five-hour', resetsAt: null},
    lastAssistantMessage: {text: 'The first change is saved; verification remains.', source: 'fixture-native-assistant', validatedOutcome: false}
  }) + '\n');
  process.exitCode = 1;
} else {
  writeFileSync(join(cwd, 'continued-context.json'), JSON.stringify(input.context));
  await import('./mock_turn_runner.mjs');
}
