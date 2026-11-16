import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { TurnProgress } from '../../packages/heartbreaker/src/renderer/src/components/TurnProgress'

test('a settled pending command still renders readable progress beside its prose', () => {
  const html = renderToStaticMarkup(<TurnProgress status="Checking the saved server receipt." streaming={false} hasContent />)
  assert.match(html, /role="status"/)
  assert.match(html, /Checking the saved server receipt/)
})

test('streaming findings retain progress while an empty model stream avoids duplicate indicators', () => {
  assert.match(renderToStaticMarkup(<TurnProgress status="Firewall verified." streaming hasContent />), /Firewall verified/)
  assert.equal(renderToStaticMarkup(<TurnProgress status="Thinking" streaming hasContent={false} />), '')
})
