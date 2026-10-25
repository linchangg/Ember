// After `vite build`: puts the scenario files where the app looks for them (static/<id>/, cache/<id>/main/) inside dist/,
// and copies index.html to 404.html so deep links such as /dashboard work on GitHub Pages.
// Scenarios: the synthetic fixture always; the real Eaton run too if its files are in the repo.
import { cpSync, existsSync, readdirSync, rmSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = fileURLToPath(new URL('../..', import.meta.url))
const dist = fileURLToPath(new URL('../dist', import.meta.url))
const SOURCES = {
  fixture: { static: 'fixtures/static/fixture', cache: 'fixtures/cache/fixture/main' },
  eaton: { static: 'data/scenarios/eaton/web', cache: 'cache/eaton/main' },
}
// vite-plugin-cesium writes its files under dist/<base>/cesium; the page asks for <base>cesium/, which is dist/cesium on Pages
const nested = join(dist, (process.env.VITE_BASE ?? '/').replace(/^\/|\/$/g, ''))
if ((process.env.VITE_BASE ?? '/') !== '/' && nested !== dist && existsSync(nested)) {
  cpSync(nested, dist, { recursive: true })
  rmSync(nested, { recursive: true })
}
const size = (dir) => readdirSync(dir, { recursive: true }).reduce((n, f) => (statSync(join(dir, f)).isFile() ? n + statSync(join(dir, f)).size : n), 0)

for (const [id, src] of Object.entries(SOURCES)) {
  const s = join(root, src.static)
  const c = join(root, src.cache)
  if (!existsSync(s) || !existsSync(c)) {
    console.log(`skip ${id}: needs ${src.static} and ${src.cache}`)
    continue
  }
  cpSync(s, join(dist, 'static', id), { recursive: true })
  cpSync(c, join(dist, 'cache', id, 'main'), { recursive: true })
  console.log(`${id}: ${(size(join(dist, 'static', id)) / 1e6).toFixed(1)} MB static, ${(size(join(dist, 'cache', id)) / 1e6).toFixed(1)} MB cache`)
}
cpSync(join(dist, 'index.html'), join(dist, '404.html'))
console.log(`dist is ${(size(dist) / 1e6).toFixed(0)} MB`)
