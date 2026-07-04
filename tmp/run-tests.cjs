// Invoke vitest programmatically and capture a basic summary.
const path = require('path');
const { spawnSync } = require('child_process');

const webRoot = '/home/AI02/Documents/quantaeye/multi_agents_platform/web';
const cli = path.join(webRoot, 'node_modules/vitest/dist/cli.js');

const result = spawnSync(
  process.execPath,
  [cli, 'run', '--reporter=basic'],
  { cwd: webRoot, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] }
);

process.stdout.write(result.stdout || '');
process.stderr.write(result.stderr || '');
process.exit(result.status ?? 0);
