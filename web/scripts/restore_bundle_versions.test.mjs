import test from 'node:test'
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { restoreBundleVersions } from './restore_bundle_versions.mjs'
import { cpSync } from 'node:fs'
import { spawnSync } from 'node:child_process'

function sample(t) {
  const root = mkdtempSync(join(tmpdir(), 'emberline-package-'))
  t.after(() => rmSync(root, { recursive: true, force: true }))
  const branch = join(root, 'cache/eaton/main'), issue = join(branch, '1000')
  mkdirSync(issue, { recursive: true })
  const bytes = Buffer.from(JSON.stringify({ scenario_id: 'eaton', branch_id: 'main', knowledge_sim_ms: 1000 }))
  const hash = createHash('sha256').update(bytes).digest('hex')
  const reference = `cache/eaton/main/1000/bundles/${hash}.json`
  const index = { scenario_id: 'eaton', branch_id: 'main', cycles: [{ knowledge_sim_ms: 1000, bundle: reference }] }
  writeFileSync(join(issue, 'bundle.json'), bytes)
  writeFileSync(join(branch, 'index.json'), JSON.stringify(index))
  return { root, branch, issue, bytes, index, reference }
}

test('omitted version is restored byte-for-byte without editing index; repeat is safe', t => {
  const s = sample(t), before = readFileSync(join(s.branch, 'index.json'))
  assert.deepEqual(restoreBundleVersions(s.root, 'eaton'), { scenario: 'eaton', verified: 1, restored: 1 })
  assert.deepEqual(readFileSync(join(s.root, s.reference)), s.bytes)
  assert.deepEqual(readFileSync(join(s.branch, 'index.json')), before)
  assert.equal(restoreBundleVersions(s.root, 'eaton').restored, 0)
})
test('changed canonical bytes cannot be served under a stale hash', t => {
  const s = sample(t)
  writeFileSync(join(s.issue, 'bundle.json'), '{}')
  assert.throws(() => restoreBundleVersions(s.root, 'eaton'), /SHA256/)
  assert.equal(existsSync(join(s.root, s.reference)), false)
})
test('path traversal and wrong branch references are rejected', t => {
  const s = sample(t)
  for (const ref of ['../secret.json', 'cache/eaton/other/1000/bundle.json', 'cache/eaton/main/1000/../../secret']) {
    s.index.cycles[0].bundle = ref
    writeFileSync(join(s.branch, 'index.json'), JSON.stringify(s.index))
    assert.throws(() => restoreBundleVersions(s.root, 'eaton'), /path|reference/)
  }
})

test('actual Pages export includes immutable bundles and excludes private runtime state', t => {
  const s = sample(t), scripts = join(s.root, 'web/scripts'), dist = join(s.root, 'web/dist')
  mkdirSync(scripts, { recursive: true }); mkdirSync(dist)
  for (const name of ['pages_assets.mjs', 'restore_bundle_versions.mjs']) {
    cpSync(new URL(name, import.meta.url), join(scripts, name))
  }
  const statics = join(s.root, 'data/scenarios/eaton/web')
  mkdirSync(statics, { recursive: true }); writeFileSync(join(statics, 'manifest.json'), '{}')
  writeFileSync(join(dist, 'index.html'), '<!doctype html>')
  const privatePaths = ['dispatch.sqlite3', 'dispatch.sqlite3-journal', 'branch-evidence.json', 'main/checkpoints/state.npz']
  for (const name of privatePaths) {
    const file = join(s.root, 'cache/eaton', name)
    mkdirSync(join(file, '..'), { recursive: true }); writeFileSync(file, 'private-test-state')
  }
  const result = spawnSync(process.execPath, [join(scripts, 'pages_assets.mjs')], { encoding: 'utf8' })
  assert.equal(result.status, 0, result.stderr)
  assert.deepEqual(readFileSync(join(dist, s.reference)), s.bytes)
  for (const name of privatePaths) assert.equal(existsSync(join(dist, 'cache/eaton', name)), false)
})
