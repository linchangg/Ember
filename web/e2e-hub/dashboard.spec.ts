import { test, expect } from '@playwright/test'
import { readFile } from 'node:fs/promises'
const credentials = JSON.parse(
  await readFile(process.env.EMBER_HUB_CREDENTIALS!, 'utf8')
)
test('two browser clients share a committed forecast and approval; reload preserves operator state', async ({
  page,
  browser,
}) => {
  const second = await browser.newContext({
      viewport: { width: 1440, height: 1000 },
    }),
    other = await second.newPage()
  const errors: string[] = []
  for (const p of [page, other])
    p.on('pageerror', (e) => errors.push(e.message))
  try {
    for (const p of [page, other]) {
      await p.goto('http://127.0.0.1:5190/dashboard')
      await expect(p.getByTestId('forecast-map')).toHaveAttribute(
        'data-rendered',
        'ready'
      )
      await expect(p.getByText('Shared hub ·')).toBeVisible()
    }
    const id = await page
      .getByTestId('forecast-map')
      .getAttribute('data-cycle-id')
    await expect(other.getByTestId('forecast-map')).toHaveAttribute(
      'data-cycle-id',
      id!
    )
    await page.getByLabel('Forecast lead').selectOption('120')
    await expect(page.getByTestId('zone-risk').first()).toContainText('%')
    await page
      .getByLabel('Operator PIN', { exact: true })
      .fill(credentials.OPERATOR_PIN)
    const proposal = page
      .locator('[data-alert-id]')
      .filter({
        has: page.getByRole('button', { name: 'Approve on hub', exact: true }),
      })
      .first()
    const alert = await proposal.getAttribute('data-alert-id')
    await proposal
      .getByRole('button', { name: 'Approve on hub', exact: true })
      .click()
    for (const p of [page, other])
      await expect(p.locator(`[data-alert-id="${alert}"]`)).toContainText(
        'approved'
      )
    await other.reload()
    await expect(other.getByTestId('forecast-map')).toHaveAttribute(
      'data-rendered',
      'ready'
    )
    await expect(other.locator(`[data-alert-id="${alert}"]`)).toContainText(
      'approved'
    )
    await expect(other.getByTestId('forecast-map')).toHaveAttribute(
      'data-cycle-id',
      id!
    )
    expect(errors).toEqual([])
    await page.screenshot({
      path: 'test-results/phase-e-hub.png',
      fullPage: true,
    })
  } finally {
    await second.close()
  }
})
