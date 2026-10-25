import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
import { existsSync } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
const root = path.resolve(
  process.env.EMBERLINE_FIXTURE_DIR ??
    fileURLToPath(new URL('../../fixtures/', import.meta.url))
)
const scenario = process.env.EMBERLINE_SCENARIO_ID ?? 'fixture'
const index = JSON.parse(
  await readFile(path.join(root, `cache/${scenario}/main/index.json`), 'utf8')
)
const latest = index.cycles.at(-1)
const bundle = JSON.parse(
  await readFile(path.join(root, latest.bundle), 'utf8')
)
const bytes = await readFile(path.join(root, bundle.cycle.assets.arrival_q))
const manifest = JSON.parse(
  await readFile(path.join(root, `static/${scenario}/manifest.json`), 'utf8')
)
let missing = false
let errors: string[] = []
test.beforeEach(async ({ page }) => {
  missing = false
  errors = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.route('http://fixtures.test/**', async (route) => {
    const relative = new URL(route.request().url()).pathname.slice(1),
      file = path.resolve(root, relative)
    if (!file.startsWith(root + path.sep)) return route.fulfill({ status: 403 })
    const headers = {
      'access-control-allow-origin': '*',
      'content-type': relative.endsWith('.bin')
        ? 'application/octet-stream'
        : 'application/json',
    }
    if (missing && relative.endsWith('/arrival_q.bin'))
      return route.fulfill({
        status: 404,
        headers,
        body: 'missing for acceptance test',
      })
    try {
      await route.fulfill({ headers, body: await readFile(file) })
    } catch {
      await route.fulfill({ status: 404, headers, body: 'missing' })
    }
  })
  await page.goto('/dashboard')
  await expect(
    page.getByText('No forecast yet.', { exact: true })
  ).toBeVisible()
})
test('all 39 replay entries commit coherent map and panel identities', async ({
  page,
}) => {
  const slider = page.getByRole('slider', { name: 'Replay issue time' })
  await slider.press('Home')
  for (let i = 0; i < index.cycles.length; i++) {
    if (i) await slider.press('ArrowRight')
    await expect(slider).toHaveValue(String(index.cycles[i].knowledge_sim_ms))
    const b = JSON.parse(
      await readFile(path.join(root, index.cycles[i].bundle), 'utf8')
    )
    if (!b.cycle) {
      await expect(page.getByTestId('incident-panels')).toHaveAttribute(
        'data-cycle-id',
        ''
      )
      continue
    }
    await expect(page.getByTestId('forecast-map')).toHaveAttribute(
      'data-rendered',
      'ready'
    )
    await expect(page.getByTestId('forecast-map')).toHaveAttribute(
      'data-cycle-id',
      b.cycle.cycle_id
    )
    await expect(page.getByTestId('incident-panels')).toHaveAttribute(
      'data-cycle-id',
      b.cycle.cycle_id
    )
    await expect(page.getByTestId('zone-risk').first()).toHaveAttribute(
      'data-cycle-id',
      b.cycle.cycle_id
    )
  }
  expect(errors).toEqual([])
  await page.screenshot({
    path: 'test-results/phase-d-main.png',
    fullPage: true,
  })
})
test('five quantile and probability masks agree with raw fixture counts at 30/120/360 minutes', async ({
  page,
}) => {
  await page.getByRole('button', { name: 'Latest forecast' }).click()
  const map = page.getByTestId('forecast-map'),
    n = manifest.grid.nx * manifest.grid.ny
  for (const lead of [30, 120, 360])
    for (const mode of ['probability', '0', '1', '2', '3', '4']) {
      await page.getByLabel('Forecast lead').selectOption(String(lead))
      await page.getByLabel('Forecast display').selectOption(mode)
      let count = 0
      for (let cell = 0; cell < n; cell++) {
        const reached = (q: number) =>
          bytes[q * n + cell] !== 255 && bytes[q * n + cell] * 2 <= lead
        if (
          mode === 'probability'
            ? [0, 1, 2, 3, 4].some(reached)
            : reached(Number(mode))
        )
          count++
      }
      await expect(map).toHaveAttribute('data-rendered', 'ready')
      await expect(map).toHaveAttribute('data-reached-cells', String(count))
      const risk = bundle.zone_risk
        .slice()
        .sort((a: any, b: any) => a.rank - b.rank)[0]
      await expect(page.getByTestId('zone-risk').first()).toContainText(
        `${(risk.p_by_lead[lead / 30 - 1] * 100).toLocaleString('en-US', { maximumFractionDigits: 1 })}%`
      )
    }
  expect(errors).toEqual([])
})
test('asset errors clear panels, retry recovers, and route exit leaves no lifecycle exception', async ({
  page,
}) => {
  missing = true
  await page.getByRole('button', { name: 'Latest forecast' }).click()
  await expect(page.getByTestId('incident-panels')).toHaveAttribute(
    'data-cycle-id',
    ''
  )
  await expect(page.getByTestId('incident-panels')).toContainText(
    'Unavailable: Asset HTTP 404'
  )
  await expect(page.getByTestId('forecast-map')).toHaveAttribute(
    'data-rendered',
    'empty'
  )
  missing = false
  await page.getByRole('button', { name: 'Latest forecast' }).click()
  await expect(page.getByTestId('forecast-map')).toHaveAttribute(
    'data-rendered',
    'ready'
  )
  await page.getByRole('link', { name: 'Globe bake-off' }).click()
  await expect(page).toHaveURL(/\/compare$/)
  await page.goBack()
  await expect(
    page.getByText('No forecast yet.', { exact: true })
  ).toBeVisible()
  expect(errors).toEqual([])
})
test(
  'native parent/child review is matched in time and local approval sends no command',
  { tag: '@native' },
  async ({ page }) => {
    test.skip(
      !existsSync(path.join(root, 'branches.json')),
      'Paired Rust fixture required'
    )
    const posts: string[] = []
    page.on('request', (r) => {
      if (r.method() === 'POST') posts.push(r.url())
    })
    await page.getByRole('button', { name: 'Latest forecast' }).click()
    await page.getByLabel('Replay branch').selectOption('report-demo')
    await expect(page.getByTestId('branch-comparison')).toHaveAttribute(
      'data-issue-ms',
      String(latest.knowledge_sim_ms)
    )
    await expect(page.getByTestId('incident-panels')).toHaveAttribute(
      'data-branch-id',
      'report-demo'
    )
    await page.getByText(/Observations ·/).click()
    await expect(
      page.locator('[data-obs-id]').filter({ hasText: 'ground_report' })
    ).toContainText('simulated')
    const article = page
      .locator('[data-alert-id]')
      .filter({ has: page.getByRole('button', { name: 'Approve locally' }) })
      .first()
    const id = await article.getAttribute('data-alert-id')
    await article.getByRole('button', { name: 'Approve locally' }).click()
    await expect(page.locator(`[data-alert-id="${id}"]`)).toContainText(
      'approved'
    )
    expect(posts).toEqual([])
    expect(errors).toEqual([])
    await page.screenshot({
      path: 'test-results/phase-d-branch.png',
      fullPage: true,
    })
  }
)
