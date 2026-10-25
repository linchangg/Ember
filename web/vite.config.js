import react from '@vitejs/plugin-react'
import cesium from 'vite-plugin-cesium'
import { defineConfig } from 'vite'
import { createReadStream, existsSync, statSync } from 'node:fs'
import { extname, join, normalize } from 'node:path'
import { fileURLToPath } from 'node:url'

// Serves Ember's scenario files at the URLs the contracts define (spec/00 section 4), in file mode:
//   /static/{scn}/{file} -> fixtures/static/{scn}/{file}  or  data/scenarios/{scn}/web/{file}
//   /cache/{scn}/{branch}/{k}/{file} -> fixtures/cache/{scn}/main/...  or  cache/{scn}/{branch}/...
const ROOT = fileURLToPath(new URL('..', import.meta.url))
const MIME = { '.json': 'application/json', '.geojson': 'application/geo+json', '.bin': 'application/octet-stream', '.png': 'image/png', '.jpg': 'image/jpeg' }
function scenarioFiles() {
  const candidates = (url) => {
    const m = url.match(/^\/(static|cache)\/([^/]+)\/(.+)$/)
    if (!m) return []
    const [, kind, scn, rest] = m
    if (kind === 'static') return [join(ROOT, 'fixtures/static', scn, rest), join(ROOT, 'data/scenarios', scn, 'web', rest)]
    const [branch, ...tail] = rest.split('/')
    return [join(ROOT, 'fixtures/cache', scn, 'main', ...tail), join(ROOT, 'cache', scn, branch, ...tail)]
  }
  return {
    name: 'ember-scenario-files',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const url = decodeURIComponent((req.url ?? '').split('?')[0])
        for (const f of candidates(url)) {
          const file = normalize(f)
          if (file.startsWith(ROOT) && existsSync(file) && statSync(file).isFile()) {
            res.setHeader('Content-Type', MIME[extname(file)] ?? 'application/octet-stream')
            createReadStream(file).pipe(res)
            return
          }
        }
        next()
      })
    },
  }
}

export default defineConfig({
  // GitHub Pages serves the site from /<repo>/, so the Pages workflow sets VITE_BASE
  base: process.env.VITE_BASE ?? '/',
  plugins: [react(), cesium(), scenarioFiles()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    allowedHosts: true,
    proxy: process.env.EMBER_DEV_SERVICE_PROXY ? { '/api': { target: process.env.EMBER_DEV_SERVICE_PROXY, changeOrigin: true } } : undefined,
  },
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
  worker: {
    format: 'es',
  },
})
