import { test } from 'node:test'
import assert from 'node:assert/strict'
import { leadRows } from './replayData.js'

test('lead rows compare the order when there was one, else the warning, earliest first and missed last', () => {
  const z = (o) => ({ official_warn_sim_ms: null, warn_sim_ms: null, warn_lead_min: null, official_order_sim_ms: null, order_sim_ms: null, order_lead_min: null, ...o })
  const lead = {
    branches: {
      b: {
        zones: {
          A: z({ official_order_sim_ms: 10, order_sim_ms: 5, order_lead_min: 5, official_warn_sim_ms: 10, warn_lead_min: 9 }),
          B: z({ official_warn_sim_ms: 10, warn_sim_ms: 1, warn_lead_min: 9 }),
          C: z({ official_order_sim_ms: 10 }),
          D: z({ official_order_sim_ms: 10, order_sim_ms: 12, order_lead_min: -2 }),
          E: z({}),
        },
      },
    },
  }
  const rows = leadRows(lead, 'b', new Map([['A', 'Alpha']]))
  assert.deepEqual(rows.map((r) => [r.id, r.kind, r.lead]), [['B', 'warn', 9], ['A', 'order', 5], ['D', 'order', -2], ['C', 'order', null]])
  assert.equal(rows[1].name, 'Alpha')
  assert.deepEqual(leadRows(null, 'b', new Map()), [])
})
