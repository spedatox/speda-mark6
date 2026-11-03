import { test } from 'node:test'
import assert from 'node:assert/strict'
import { releaseVersion } from '../scripts/heartbreaker_release.mjs'

test('Renovated release floor and exact semantic tags', () => {
  assert.equal(releaseVersion(['v2.1.21', 'v30.0.0-beta', 'archive']), '2.2.0')
  assert.equal(releaseVersion(['v2.9.9', 'v2.10.0'], []), '2.10.1')
  assert.equal(releaseVersion([], []), '2.2.0')
})
test('reruns reuse the version already attached to this commit', () => {
  assert.equal(releaseVersion(['v2.2.1', 'v2.2.2'], ['v2.2.1']), '2.2.1')
})
test('major and minor inputs are bounded choices', () => {
  assert.equal(releaseVersion(['v2.2.4'], [], 'minor'), '2.3.0')
  assert.equal(releaseVersion(['v2.2.4'], [], 'major'), '3.0.0')
  assert.throws(() => releaseVersion([], [], 'arbitrary'))
})
