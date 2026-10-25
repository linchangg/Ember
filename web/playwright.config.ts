import { defineConfig } from '@playwright/test'
import { readFileSync, existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const root =
  process.env.EMBERLINE_FIXTURE_DIR ??
  fileURLToPath(new URL('../fixtures/', import.meta.url))
export default defineConfig({
  testDir: './e2e',
  timeout: 120000,
  globalTimeout: 600000,
  workers: 1,
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:5189',
    viewport: { width: 1440, height: 1000 },
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    launchOptions: {
      args: [
        '--use-gl=angle',
        '--use-angle=swiftshader',
        '--enable-unsafe-swiftshader',
      ],
    },
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  webServer: {
    command: 'npm run dev -- --host 127.0.0.1 --port 5189',
    url: 'http://127.0.0.1:5189/dashboard',
    reuseExistingServer: false,
    env: {
      VITE_DATA_SOURCE: 'file',
      VITE_SCENARIO_ID: process.env.EMBERLINE_SCENARIO_ID ?? 'fixture',
      VITE_ASSET_BASE: 'http://fixtures.test/',
      VITE_BRANCHES_JSON: existsSync(path.join(root, 'branches.json'))
        ? readFileSync(path.join(root, 'branches.json'), 'utf8')
        : '',
    },
  },
})
