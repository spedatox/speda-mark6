import { build } from 'esbuild'
import { mkdtemp } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import path from 'node:path'
import { spawnSync } from 'node:child_process'
import { fileURLToPath } from 'node:url'

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..')
const scratch = await mkdtemp(path.join(tmpdir(), 'speda-conversation-'))
for (const file of ['operation_wait_client.test.ts', 'turn_progress.test.tsx']) {
  const output = path.join(scratch, `${file}.cjs`)
  await build({entryPoints:[path.join(root, 'tests/compatibility', file)], outfile:output,
    platform:'node', format:'cjs', bundle:true, jsx:'automatic'})
  const result = spawnSync(process.execPath, ['--test', output], {stdio:'inherit'})
  if (result.status !== 0) process.exit(result.status ?? 1)
}
