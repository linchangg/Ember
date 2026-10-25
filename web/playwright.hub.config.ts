import { defineConfig } from '@playwright/test'
export default defineConfig({
  testDir: './e2e-hub',
  workers: 1,
  timeout: 120000,
  use: {
    viewport: { width: 1440, height: 1000 },
    trace: 'off',
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
    command: 'npm run dev -- --host 127.0.0.1 --port 5190',
    url: 'http://127.0.0.1:5190/dashboard',
    reuseExistingServer: false,
    env: {
      VITE_DATA_SOURCE: 'stdb',
      VITE_STDB_URI: process.env.STDB_URI ?? 'http://127.0.0.1:3017',
      VITE_STDB_DB: process.env.STDB_DB ?? 'phase-e-demo',
      VITE_SCENARIO_ID: process.env.EMBER_SCENARIO_ID ?? 'rust-fixture',
      VITE_ASSET_BASE:
        process.env.EMBER_ASSET_BASE ?? 'http://127.0.0.1:8796/',
    },
  },
})
