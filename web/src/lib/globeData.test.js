import test from 'node:test'
import assert from 'node:assert/strict'
import { newHeli } from './globeData.js'

test('an unavailable or empty event feed cannot crash globe initialization', () => {
  assert.equal(newHeli([]), null)
  assert.equal(newHeli([{ v: [1, 0, 0] }]), null)
})

test('coincident and antipodal events produce no invalid flight', () => {
  for (const vectors of [[[1, 0, 0], [1, 0, 0]], [[1, 0, 0], [-1.000000000000001, 0, 0]]]) {
    let i = 0
    assert.equal(newHeli(vectors.map((v) => ({ v })), () => (i++ % 2 ? 0.75 : 0)), null)
  }
})

test('valid separated events preserve the animated flight parameters', () => {
  const events = [{ v: [1, 0, 0] }, { v: [Math.cos(.3), Math.sin(.3), 0] }]
  const values = [0, .75, .5, .25]
  const flight = newHeli(events, () => values.shift())
  assert.ok(Math.abs(flight.ang - .3) < 1e-12)
  assert.equal(flight.speed, .03)
  assert.equal(flight.hue, .25)
})
