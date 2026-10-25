import assert from 'node:assert/strict'
import test from 'node:test'
import { createTimelineSelection } from './latestSelection.ts'

function deferred() {
  let resolve: () => void = () => {}
  const promise = new Promise<void>((done) => {
    resolve = done
  })
  return { promise, resolve }
}

test('a selection made while loading is kept and a superseded one cannot replace it', async () => {
  const published: number[] = []
  const gate = deferred()
  let calls = 0
  const selection = createTimelineSelection(async (request, stillCurrent) => {
    calls += 1
    if (calls === 1) await gate.promise
    if (!stillCurrent()) return
    published.push(request.issue)
  })
  selection.request({ issue: 1736302800000, leadMin: 120 })
  selection.request({ issue: 1736308500000, leadMin: 120 })
  selection.request({ issue: 1736311800000, leadMin: 30 })
  gate.resolve()
  await new Promise((resolve) => setTimeout(resolve, 30))
  assert.equal(calls, 2)
  assert.deepEqual(published, [1736311800000])
})

test('finished selections stay in order when each settles before the next request', async () => {
  const published: number[] = []
  const selection = createTimelineSelection(async (request, stillCurrent) => {
    await Promise.resolve()
    if (stillCurrent()) published.push(request.issue)
  })
  selection.request({ issue: 1, leadMin: 120 })
  await new Promise((resolve) => setTimeout(resolve, 20))
  selection.request({ issue: 2, leadMin: 120 })
  await new Promise((resolve) => setTimeout(resolve, 20))
  assert.deepEqual(published, [1, 2])
})

test('a failed in-flight selection does not drop a newer request', async () => {
  const published: number[] = []
  const gate = deferred()
  let calls = 0
  const selection = createTimelineSelection(async (request, stillCurrent) => {
    calls += 1
    if (calls === 1) {
      await gate.promise
      throw new Error('Asset HTTP 404')
    }
    if (stillCurrent()) published.push(request.issue)
  })
  selection.request({ issue: 10, leadMin: 120 })
  selection.request({ issue: 20, leadMin: 120 })
  gate.resolve()
  await new Promise((resolve) => setTimeout(resolve, 30))
  assert.deepEqual(published, [20])
})
