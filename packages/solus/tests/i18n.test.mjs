import test from 'node:test'
import assert from 'node:assert/strict'
import { formatAmount, normalizeAmounts, translate } from '../src/i18n.js'

test('money separators preserve exact decimal digits, signs and large values', () => {
  assert.equal(formatAmount('98751.32', 'tr'), '98.751,32')
  assert.equal(formatAmount('-26691.51', 'tr'), '-26.691,51')
  assert.equal(formatAmount('98751.32', 'en'), '98,751.32')
  assert.equal(formatAmount('900719925474099312345.00000001', 'tr'), '900.719.925.474.099.312.345,00000001')
  assert.equal(formatAmount('0.00', 'tr'), '0,00')
  assert.equal(formatAmount('1234', 'tr'), '1.234')
  assert.equal(formatAmount(null, 'tr'), '—')
})

test('Turkish amounts become canonical payload strings without changing source evidence', () => {
  const payload = { lines: [{ amount: '1.234,56' }], description: 'Invoice 1.234,56', original: 'source,1.234,56', evidence: [{ quote: '1.234,56' }], fee: '0.00' }
  const normalized = normalizeAmounts(payload, 'tr')
  assert.equal(normalized.lines[0].amount, '1234.56')
  assert.equal(normalized.description, payload.description)
  assert.equal(normalized.original, payload.original)
  assert.deepEqual(normalized.evidence, payload.evidence)
  assert.equal(normalized.fee, '0.00')
  assert.equal(payload.lines[0].amount, '1.234,56')
  assert.throws(() => normalizeAmounts({ amount: '1,234,56' }, 'tr'))
  assert.equal(translate('Reports', 'tr'), 'Raporlar')
  assert.equal(translate('liability', 'tr'), 'Borç / yükümlülük')
})
