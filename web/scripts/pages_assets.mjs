// After `vite build`: puts the scenario files where the app looks for them (static/<id>/, cache/<id>/main/) inside dist/,
// and copies index.html to 404.html so deep links such as /dashboard work on GitHub Pages.
// Scenarios: the synthetic fixture always; the real Eaton run too if its files are in the repo.
import { createHash } from 'node:crypto'
import { cpSync, existsSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../..', import.meta.url))
const dist = fileURLToPath(new URL('../dist', import.meta.url))
// cache: the scenario's cache dir (every branch plus lead_times.json); the fixture only has main
const SOURCES = {
  fixture: { static: 'fixtures/static/fixture', cache: 'fixtures/cache/fixture/main', branch: 'main' },
  eaton: { static: 'data/scenarios/eaton/web', cache: 'cache/eaton' },
}
// vite-plugin-cesium writes its files under dist/<base>/cesium; the page asks for <base>cesium/, which is dist/cesium on Pages
const nested = join(dist, (process.env.VITE_BASE ?? '/').replace(/^\/|\/$/g, ''))
if ((process.env.VITE_BASE ?? '/') !== '/' && nested !== dist && existsSync(nested)) {
  cpSync(nested, dist, { recursive: true })
  rmSync(nested, { recursive: true })
}
const size = (dir) => readdirSync(dir, { recursive: true }).reduce((n, f) => (statSync(join(dir, f)).isFile() ? n + statSync(join(dir, f)).size : n), 0)

// Some precompute archives list each cycle's bundle as cache/<id>/<branch>/<k>/bundles/<sha256>.json (content-addressed) but ship the same
// bytes as <k>/bundle.json. Point every index entry at a file that exists, and say so; a bundle that is really missing fails the build.
function repairIndexes(cacheRoot) {
  for (const f of readdirSync(cacheRoot, { recursive: true })) {
    if (!f.endsWith('index.json')) continue
    const path = join(cacheRoot, f)
    const index = JSON.parse(readFileSync(path, 'utf8'))
    let fixed = 0
    let changedHash = 0
    for (const c of index.cycles ?? []) {
      const wanted = join(dist, c.bundle)
      if (existsSync(wanted)) continue
      const sibling = join(dirname(dirname(wanted)), 'bundle.json')
      if (!existsSync(sibling)) throw new Error(`${path}: ${c.bundle} is missing and so is ${sibling}`)
      const digest = createHash('sha256').update(readFileSync(sibling)).digest('hex')
      if (!c.bundle.includes(digest)) changedHash++
      c.bundle = c.bundle.replace(/\/bundles\/[^/]+$/, '/bundle.json')
      fixed++
    }
    if (fixed) {
      writeFileSync(path, JSON.stringify(index))
      console.log(`${f}: pointed ${fixed} index entries at <cycle>/bundle.json${changedHash ? ` (${changedHash} with different content than the hash in the index)` : ' (content matches the hashes)'}`)
    }
  }
}

for (const [id, src] of Object.entries(SOURCES)) {
  const s = join(root, src.static)
  const c = join(root, src.cache)
  if (!existsSync(s) || !existsSync(c)) {
    console.log(`skip ${id}: needs ${src.static} and ${src.cache}`)
    continue
  }
  cpSync(s, join(dist, 'static', id), { recursive: true })
  cpSync(c, join(dist, 'cache', id, ...(src.branch ? [src.branch] : [])), { recursive: true })
  repairIndexes(join(dist, 'cache', id))
  console.log(`${id}: ${(size(join(dist, 'static', id)) / 1e6).toFixed(1)} MB static, ${(size(join(dist, 'cache', id)) / 1e6).toFixed(1)} MB cache`)
}
cpSync(join(dist, 'index.html'), join(dist, '404.html'))
console.log(`dist is ${(size(dist) / 1e6).toFixed(0)} MB`)
