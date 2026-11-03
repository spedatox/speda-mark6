import { build } from 'esbuild'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawnSync } from 'node:child_process'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const scratch = await mkdtemp(path.join(tmpdir(), 'speda-worker-client-'))
for (const client of ['heartbreaker', 'striker']) {
  const output = path.join(scratch, `${client}.test.cjs`)
  await build({ entryPoints: [path.join(root, 'tests/compatibility/worker_client.test.tsx')],
    outfile: output, platform: 'node', format: 'cjs', bundle: true, jsx: 'automatic',
    external: ['react', 'react/*', 'react-dom', 'react-dom/*', 'react-test-renderer', 'scheduler'],
    alias: { '@client': path.join(root, `packages/${client}/src/renderer/src`) },
    define: { 'process.env.NODE_ENV': '"development"' }, logLevel: 'warning' })
  process.stdout.write(`\n${client}: actual React hook, reducer and stream transport\n`)
  const result = spawnSync(process.execPath, ['--test', '--test-concurrency=1', output], { stdio: 'inherit',
    env: { ...process.env, NODE_PATH: [path.join(root,'node_modules'),path.join(root,'packages/heartbreaker/node_modules')].join(path.delimiter) } })
  if (result.status !== 0) process.exit(result.status ?? 1)
}
