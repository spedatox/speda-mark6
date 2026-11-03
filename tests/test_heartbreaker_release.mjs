import { test } from 'node:test'
import assert from 'node:assert/strict'
import { releaseVersion } from '../scripts/heartbreaker_release.mjs'

test('Renovated release floor and exact semantic tags', () => {
  assert.equal(releaseVersion(['v2.1.21', 'v30.0.0-beta', 'archive']), '3.0.0')
  assert.equal(releaseVersion(['v3.9.9', 'v3.10.0'], []), '3.10.1')
  assert.equal(releaseVersion([], []), '3.0.0')
})
test('reruns reuse the version already attached to this commit', () => {
  assert.equal(releaseVersion(['v3.0.1', 'v3.0.2'], ['v3.0.1']), '3.0.1')
  assert.equal(releaseVersion(['v2.1.21'], ['v2.1.21']), '3.0.0')
})
test('major and minor inputs are bounded choices', () => {
  assert.equal(releaseVersion(['v3.0.4'], [], 'minor'), '3.1.0')
  assert.equal(releaseVersion(['v3.0.4'], [], 'major'), '4.0.0')
  assert.throws(() => releaseVersion([], [], 'arbitrary'))
})
