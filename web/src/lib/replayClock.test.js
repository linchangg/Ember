import test from 'node:test'
import assert from 'node:assert/strict'
import { createClock } from './replayClock.js'
test('Linda local clock preserves its 90-minute opening, anchor math, bounded seek and disposal', () => {
  const original = Date.now
  let wall = 1000000
  Date.now = () => wall
  try {
    const m = {scenario_id:'fixture', replay_start_sim_ms:0, replay_end_sim_ms:12*3600000, t_ignition_sim_ms:600000}
    const c = createClock(m)
    assert.equal(c.kind, 'local'); assert.equal(c.now(), 600000+90*60000)
    c.play(); wall += 1000
    assert.equal(c.now(), 600000+90*60000+240000)
    c.pause(); const paused = c.now(); wall += 1000; assert.equal(c.now(), paused)
    c.setSpeed(60); c.play(); wall += 1000; assert.equal(c.now(), paused+60000)
    c.seek(-1000); assert.equal(c.now(),0); assert.equal(c.get().epoch,1)
    c.seek(m.replay_end_sim_ms+1000); assert.equal(c.now(),m.replay_end_sim_ms)
    assert.throws(() => c.setSpeed(NaN)); assert.throws(() => c.setSpeed(-1)); assert.throws(() => c.seek(NaN))
    c.pause(); c.dispose(); const state=c.get(); c.play(); c.seek(0); assert.strictEqual(c.get(),state)
  } finally { Date.now=original }
})
