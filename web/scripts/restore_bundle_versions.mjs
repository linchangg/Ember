/** Restore omitted immutable copies only when canonical bytes match the index hash. */
import { createHash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, readdirSync, writeFileSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export function restoreBundleVersions(assetRoot, scenario) {
  if (!/^[A-Za-z0-9_-]+$/.test(scenario)) throw Error('Invalid scenario')
  const root = resolve(assetRoot), cache = join(root, 'cache', scenario), pending = []
  let verified = 0
  for (const branch of readdirSync(cache, { withFileTypes: true })) {
    if (!branch.isDirectory()) continue
    const indexPath = join(cache, branch.name, 'index.json')
    if (!existsSync(indexPath)) continue
    const index = JSON.parse(readFileSync(indexPath, 'utf8'))
    if (index.scenario_id !== scenario || index.branch_id !== branch.name) throw Error('Index identity mismatch')
    const seen = new Set()
    for (const entry of index.cycles) {
      const issue = entry.knowledge_sim_ms
      if (!Number.isSafeInteger(issue) || seen.has(issue)) throw Error('Invalid or duplicate issue')
      seen.add(issue)
      const prefix = `cache/${scenario}/${branch.name}/${issue}/`
      if (typeof entry.bundle !== 'string' || !entry.bundle.startsWith(prefix)) throw Error('Bundle path outside issue')
      const suffix = entry.bundle.slice(prefix.length)
      const match = /^bundles\/([a-f0-9]{64})\.json$/.exec(suffix)
      if (!match && suffix !== 'bundle.json') throw Error('Invalid bundle reference')
      const target = join(root, entry.bundle)
      const source = existsSync(target) ? target : join(root, prefix, 'bundle.json')
      const bytes = readFileSync(source)
      if (match && createHash('sha256').update(bytes).digest('hex') !== match[1]) throw Error('Bundle SHA256 mismatch')
      const bundle = JSON.parse(bytes)
      if (bundle.scenario_id !== scenario || bundle.branch_id !== branch.name || bundle.knowledge_sim_ms !== issue) throw Error('Bundle identity mismatch')
      verified++
      if (!existsSync(target)) pending.push({ target, bytes })
    }
  }
  // Validate the entire package before writing any missing copies. Never change the index.
  for (const { target, bytes } of pending) {
    mkdirSync(dirname(target), { recursive: true })
    writeFileSync(target, bytes, { flag: 'wx' })
  }
  return { scenario, verified, restored: pending.length }
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.length !== 4) throw Error('Usage: node restore_bundle_versions.mjs ASSET_ROOT SCENARIO')
  console.log(JSON.stringify(restoreBundleVersions(process.argv[2], process.argv[3])))
}
