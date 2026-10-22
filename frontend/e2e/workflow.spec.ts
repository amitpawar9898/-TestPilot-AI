import { expect, test } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import path from 'node:path'

test('login, sample analysis, findings, tests, verified fix downloads, report, logout', async ({ page }) => {
  const suffix = Date.now().toString(36)
  const username = `browser_${suffix}`
  const password = 'BrowserPass123!'
  const consoleErrors: string[] = []
  const failedRequests: string[] = []
  let trackRequests = false

  page.on('console', message => { if (trackRequests && message.type() === 'error') consoleErrors.push(message.text()) })
  page.on('pageerror', error => { if (trackRequests) consoleErrors.push(error.message) })
  page.on('requestfailed', request => { if (trackRequests) failedRequests.push(`${request.method()} ${request.url()} ${request.failure()?.errorText}`) })
  page.on('response', response => {
    if (!trackRequests) return
    if (response.status() >= 400) failedRequests.push(`${response.status()} ${response.request().method()} ${response.url()}`)
  })

  await page.goto('/dashboard')
  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByRole('heading', { name: 'Sign in or create an account' })).toBeVisible()
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  trackRequests = true
  await page.getByRole('button', { name: 'Create account' }).click()
  await expect(page.getByText(username, { exact: true }).first()).toBeVisible()
  // The account page intentionally makes unauthenticated requests after sign-out.
  trackRequests = false
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/account$/)
  await page.getByLabel('Username').fill(username)
  await page.getByLabel('Password').fill(password)
  trackRequests = true
  await page.getByRole('button', { name: 'Sign in' }).click()
  await expect(page.getByText(username, { exact: true }).first()).toBeVisible()

  await page.getByRole('link', { name: 'Fix my code' }).click()
  await page.getByRole('button', { name: /Load sample with planted bugs/ }).click()
  await expect(page.locator('textarea').nth(0)).toContainText('apply_discount')
  await page.getByRole('button', { name: /Run analysis/ }).click()
  await expect(page.locator('.status-line b').first()).toHaveText('completed', { timeout: 90_000 })
  await expect(page.getByRole('heading', { name: 'Review your code' })).toBeVisible()
  for (const tab of ['Original', 'Fixed', 'Improved']) {
    await page.getByRole('button', { name: tab, exact: true }).click()
    await expect(page.locator('.code-block').last()).not.toBeEmpty()
  }
  await expect(page.locator('.table-wrap tbody tr').first()).toBeVisible()
  for (const buttonName of ['Download fixed.py', 'Download improved.py', 'Download original → fixed diff', 'Download fixed → improved diff']) {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: buttonName }).click(),
    ])
    expect(download.suggestedFilename()).toMatch(/\.py$|\.diff$/)
  }

  await mkdir(path.resolve(process.cwd(), '../docs/screenshots'), { recursive: true })
  await page.screenshot({ path: path.resolve(process.cwd(), '../docs/screenshots/fix_my_code_sample.png'), fullPage: true })

  await page.getByRole('link', { name: 'Bug Reports' }).click()
  await expect(page.getByRole('heading', { name: 'Findings' })).toBeVisible()
  await expect(page.locator('.data-item').first()).toBeVisible()
  await page.screenshot({ path: path.resolve(process.cwd(), '../docs/screenshots/bug_reports.png'), fullPage: true })

  await page.getByRole('link', { name: 'Test Generator' }).click()
  await expect(page.getByRole('heading', { name: /\d+ test cases/ })).toBeVisible()
  await expect(page.locator('.case-row').first()).toBeVisible()

  await page.getByRole('link', { name: 'Auto-Fix' }).click()
  await expect(page.getByRole('heading', { name: 'Auto-Fix proposal' })).toBeVisible()
  await expect(page.locator('.code-block')).toBeVisible()
  for (const buttonName of ['Download fixed file', 'Download diff']) {
    const [download] = await Promise.all([
      page.waitForEvent('download'),
      page.getByRole('button', { name: buttonName }).click(),
    ])
    expect(download.suggestedFilename()).toMatch(/\.py$|\.diff$/)
  }

  await page.getByRole('link', { name: 'Run Report' }).click()
  await expect(page.getByRole('heading', { name: 'Analysis report' })).toBeVisible()
  await expect(page.getByRole('button', { name: 'JSON' })).toBeVisible()
  await page.screenshot({ path: path.resolve(process.cwd(), '../docs/screenshots/run_report.png'), fullPage: true })

  await page.getByRole('link', { name: 'Agent Monitor' }).click()
  await expect(page.getByRole('heading', { name: 'Agent decisions and transitions' })).toBeVisible()
  await expect(page.getByText(/Planner/i).first()).toBeVisible()
  await page.screenshot({ path: path.resolve(process.cwd(), '../docs/screenshots/agent_monitor.png'), fullPage: true })

  // Logout returns to a page that correctly receives 401s for protected data.
  trackRequests = false
  await page.getByRole('button', { name: 'Sign out' }).click()
  await expect(page).toHaveURL(/\/account$/)
  await expect(page.getByRole('heading', { name: 'Sign in or create an account' })).toBeVisible()
  expect(consoleErrors).toEqual([])
  expect(failedRequests).toEqual([])
})
