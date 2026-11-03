/** Stable semver allocation, including idempotent reruns of a tagged commit. */
import { execFileSync } from 'node:child_process'
import { readFileSync, writeFileSync, appendFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'

const parse = tag => /^v(\d+)\.(\d+)\.(\d+)$/.exec(tag)?.slice(1).map(Number)
const compare = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]
export function releaseVersion(tags, currentTags = [], bump = 'patch') {
  if (!['patch', 'minor', 'major'].includes(bump)) throw Error('Unknown version bump')
  const ordered = values => values.map(parse).filter(Boolean).sort(compare)
  const existing = ordered(currentTags).at(-1)
  if (existing) return existing.join('.')
  const latest = ordered(tags).at(-1)
  if (!latest) return '2.2.0'
  const next = [...latest]
  const part = { major: 0, minor: 1, patch: 2 }[bump]
  next[part]++
  for (let i = part + 1; i < 3; i++) next[i] = 0
  return (compare(next, [2, 2, 0]) < 0 ? [2, 2, 0] : next).join('.')
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const git = (...args) => execFileSync('git', args, { encoding: 'utf8' }).trim().split('\n')
  const version = releaseVersion(git('tag', '--list'), git('tag', '--points-at', 'HEAD'), process.env.RELEASE_BUMP || 'patch')
  const path = 'packages/heartbreaker/package.json'
  const pkg = JSON.parse(readFileSync(path, 'utf8'))
  pkg.version = version
  writeFileSync(path, JSON.stringify(pkg, null, 2) + '\n')
  const metadata = { version, tag: `v${version}`, commit: git('rev-parse', 'HEAD')[0] }
  writeFileSync('heartbreaker-release.json', JSON.stringify(metadata, null, 2) + '\n')
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `version=${version}\ntag=v${version}\n`)
  console.log(JSON.stringify(metadata))
}
