/* global process, console, document, window */
// Live browser smoke for a disposable instance. No API routes are mocked.
// WEB_BASE_URL=http://127.0.0.1:18841 PLAYWRIGHT_MODULE=/path/to/playwright/index.js node scripts/qualify-management.mjs
import assert from 'node:assert/strict'
import { mkdir, writeFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { createServer } from 'node:http'
import { setTimeout as wait } from 'node:timers/promises'
import { fileURLToPath, URL } from 'node:url'

const require = createRequire(import.meta.url)
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')
const base = process.env.WEB_BASE_URL
assert(base && new URL(base).hostname === '127.0.0.1', 'Set WEB_BASE_URL to an isolated loopback QA instance')
const outputDir = process.env.OUTPUT_DIR || fileURLToPath(new URL('../../output/playwright/management', import.meta.url))
await mkdir(outputDir, { recursive: true })
const browser = await chromium.launch({ channel: process.env.BROWSER_CHANNEL || 'msedge', headless: true, args: ['--no-proxy-server', '--disable-gpu'] })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })
const errors = []
const evidence = []
let probeServer
page.on('pageerror', (error) => errors.push(error.message))
const json = async (path) => {
  const response = await page.request.get(`${base}${path}`)
  assert(response.ok(), `${path} must be readable`)
  return response.json()
}
const change = async (path, method, action) => {
  const responsePromise = page.waitForResponse((response) => new URL(response.url()).pathname === path && response.request().method() === method)
  await action()
  const response = await responsePromise
  assert(response.ok(), `${method} ${path}: ${response.status()}`)
  return response.json()
}
const snapshot = async (name) => {
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.screenshot({ path: `${outputDir}/${name}-desktop.png` })
  await page.setViewportSize({ width: 390, height: 844 })
  await page.evaluate(() => window.scrollTo(0, 0))
  await page.evaluate(() => new Promise((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve))))
  await page.screenshot({ path: `${outputDir}/${name}-mobile.png` })
  assert(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), `${name} must fit the mobile viewport`)
  await page.setViewportSize({ width: 1440, height: 1000 })
  await page.evaluate(() => new Promise((resolve) => window.requestAnimationFrame(() => window.requestAnimationFrame(resolve))))
}

try {
  await page.goto(base)
  const initial = await json('/api/setup/state')
  if (initial.status === 'setup_required') {
    await page.getByRole('heading', { name: 'Set up your workspace' }).waitFor()
    const primary = page.locator('section').filter({ has: page.getByRole('heading', { name: 'Primary model', exact: true }) })
    await primary.getByLabel('Name', { exact: true }).fill('fixture-model')
    await primary.getByLabel('Base Url', { exact: true }).fill('http://127.0.0.1:1/v1')
    await primary.getByLabel('Api Key Secret', { exact: true }).fill('QA_MODEL_KEY')
    await page.getByLabel('QA_MODEL_KEY', { exact: true }).fill('fixture-key')
    const probe = await change('/api/setup/test-model', 'POST', () => page.getByRole('button', { name: 'Test connection', exact: true }).click())
    assert.equal(probe.ok, false)
    assert(!JSON.stringify(probe).includes('fixture-key'))
    assert.equal(await page.getByLabel('QA_MODEL_KEY', { exact: true }).inputValue(), 'fixture-key')
    let receiveProbe
    const receivedProbe = new Promise((resolve) => { receiveProbe = resolve })
    probeServer = createServer((_request, response) => { receiveProbe(); response.on('error', () => {}) })
    await new Promise((resolve) => probeServer.listen(0, '127.0.0.1', resolve))
    await primary.getByLabel('Base Url', { exact: true }).fill(`http://127.0.0.1:${probeServer.address().port}/v1`)
    await page.getByRole('button', { name: 'Test connection', exact: true }).click()
    await Promise.race([receivedProbe, wait(5000, undefined, { ref: false }).then(() => { throw new Error('The QA backend must bypass its HTTP proxy for the waiting loopback provider') })])
    await page.getByRole('button', { name: 'Cancel test', exact: true }).click()
    await page.getByText('Connection test canceled. Your settings have not changed.', { exact: true }).waitFor()
    assert.equal(await page.getByLabel('QA_MODEL_KEY', { exact: true }).inputValue(), 'fixture-key')
    probeServer.closeAllConnections()
    await new Promise((resolve) => probeServer.close(resolve))
    probeServer = undefined
    await primary.getByLabel('Base Url', { exact: true }).fill('http://127.0.0.1:1/v1')
    const retry = await change('/api/setup/test-model', 'POST', () => page.getByRole('button', { name: 'Test connection', exact: true }).click())
    assert.equal(retry.ok, false)
    assert.equal(await page.getByLabel('QA_MODEL_KEY', { exact: true }).inputValue(), 'fixture-key')
    evidence.push('Connection test failed, then a real request reached a deliberately waiting loopback provider and was canceled in the UI; retry failed with all drafts retained.')
    await primary.getByLabel('Base Url', { exact: true }).fill('https://models.example.test/v1')
    await page.getByRole('button', { name: 'Apply primary settings to all roles', exact: true }).click()
    await snapshot('setup')
    await page.getByRole('button', { name: 'Save and open InvestOrch', exact: true }).click()
    await page.getByRole('link', { name: 'Settings', exact: true }).waitFor()
    assert.equal((await json('/api/setup/state')).status, 'ready')
    evidence.push('Fresh Setup completed through the real UI in the same server process; failed model probe retained inputs without echoing its key.')
  }
  const configured = await json('/api/config')
  assert.equal(configured.values.models.main.base_url, 'https://models.example.test/v1', 'This script may only mutate the disposable fixture instance')
  await page.getByRole('button', { name: 'New', exact: true }).click()
  await page.waitForURL('**/c/*')
  await page.getByRole('textbox', { name: 'Message InvestOrch Agent' }).waitFor()
  evidence.push('The real application Host creates a Session after Setup; no provider inference claimed for fixture credentials.')

  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await page.getByRole('heading', { name: 'Settings', exact: true }).waitFor()
  const timezone = page.getByLabel('Default Timezone', { exact: true })
  await timezone.fill('UTC')
  await change('/api/config', 'PATCH', () => page.getByRole('button', { name: 'Save changes', exact: true }).click())
  assert.equal((await json('/api/config')).values.runtime.default_timezone, 'UTC')
  await page.getByRole('button', { name: 'Save changes', exact: true }).waitFor({ state: 'visible' })
  await change('/api/config/reset', 'POST', () => timezone.locator('..').getByRole('button', { name: 'Reset', exact: true }).click())
  assert.equal((await json('/api/config')).values.runtime.default_timezone, 'Asia/Shanghai')
  await timezone.fill('UTC')
  await page.getByRole('link', { name: 'Skills', exact: true }).click()
  await page.getByRole('dialog', { name: 'Discard unsaved changes?' }).waitFor()
  await page.getByRole('button', { name: 'Keep editing', exact: true }).click()
  assert.equal(await timezone.inputValue(), 'UTC')
  await page.getByRole('link', { name: 'Skills', exact: true }).click()
  await page.getByRole('button', { name: 'Discard and leave', exact: true }).click()
  await page.getByRole('heading', { name: 'Skills', exact: true }).waitFor()
  evidence.push('Settings saves persist; reset removes the override; navigation warns and supports keeping or discarding draft edits.')

  const skills = (await json('/api/skills')).skills
  assert.equal(skills.filter((skill) => skill.source.type === 'builtin').length, 6)
  await snapshot('skills')
  const name = 'investorch-configuration'
  await page.getByRole('link', { name, exact: true }).click()
  await page.getByRole('heading', { name, exact: true }).waitFor()
  if (!await page.getByLabel('Enabled', { exact: true }).isChecked()) {
    await change(`/api/skills/${name}`, 'PATCH', () => page.getByLabel('Enabled', { exact: true }).click())
    await page.getByText('Available', { exact: true }).waitFor()
  }
  await page.getByRole('button', { name: `Actions for ${name}`, exact: true }).click()
  await page.getByRole('menuitem', { name: 'Fork with Agent', exact: true }).waitFor()
  assert.equal(await page.getByRole('menuitem', { name: 'Edit with Agent', exact: true }).count(), 0)
  assert.equal(await page.getByRole('menuitem', { name: 'Remove Skill', exact: true }).count(), 0)
  await page.keyboard.press('Escape')
  await change(`/api/skills/${name}`, 'PATCH', () => page.getByLabel('Enabled', { exact: true }).click())
  await page.getByText('Saved. Restart InvestOrch to use the updated Skill catalog.', { exact: true }).waitFor()
  assert.equal((await json(`/api/skills/${name}`)).status, 'disabled')
  await page.getByRole('heading', { name: 'Read-only preview', exact: true }).waitFor()
  await snapshot('skill-detail-disabled')
  await page.getByLabel('File', { exact: true }).selectOption('references/configuration.md')
  await page.locator('pre').filter({ hasText: 'Configuration' }).first().waitFor()
  await change(`/api/skills/${name}`, 'PATCH', () => page.getByLabel('Enabled', { exact: true }).click())
  evidence.push('Six built-ins are listed; built-in menu offers only Fork; disabled Skills retain content/resource previews; enabled state persists with restart guidance.')

  await page.getByRole('link', { name: 'Settings', exact: true }).click()
  await page.getByRole('button', { name: 'Models', exact: true }).click()
  const main = page.getByRole('group', { name: 'Main', exact: true })
  await main.getByLabel('Reasoning Effort', { exact: true }).selectOption('low')
  await page.getByLabel('QA_MODEL_KEY', { exact: true }).fill('fixture-replacement-key')
  const saved = await change('/api/config', 'PATCH', () => page.getByRole('button', { name: 'Save changes', exact: true }).click())
  assert.equal(saved.requires_restart, true)
  await page.waitForFunction(() => [...document.querySelectorAll('input[type=password]')].every((input) => input.value === ''))
  const masked = await json('/api/config')
  assert.equal(masked.values.models.main.reasoning_effort, 'low')
  assert.equal(masked.secrets.QA_MODEL_KEY.configured, true)
  assert(!JSON.stringify(masked).includes('fixture-replacement-key'))
  await snapshot('settings-models')
  evidence.push('Role-specific model edits report restart; replacement keys are absent from reads and inputs clear after successful save.')
  await page.getByLabel('QA_MODEL_KEY', { exact: true }).fill('fixture-secret-only-replacement')
  const secretOnly = await change('/api/config', 'PATCH', () => page.getByRole('button', { name: 'Save changes', exact: true }).click())
  assert.equal(secretOnly.requires_restart, true)
  await page.waitForFunction(() => [...document.querySelectorAll('input[type=password]')].every((input) => input.value === ''))
  assert(!(JSON.stringify(await json('/api/config'))).includes('fixture-secret-only-replacement'))
  evidence.push('A replacement key saved without any model edit independently requires restart and is never echoed.')

  await page.getByRole('button', { name: 'Integrations', exact: true }).click()
  await page.getByRole('button', { name: 'Add server', exact: true }).click()
  const dialog = page.getByRole('dialog', { name: 'Add MCP server', exact: true })
  await dialog.getByLabel('Server name', { exact: true }).fill('qa-mcp')
  await dialog.getByLabel('Endpoint URL', { exact: true }).fill('https://mcp.example.test/mcp?token=fixture-mcp-token')
  await dialog.getByLabel('Replacement headers (JSON)', { exact: true }).fill('{"Authorization":"fixture-header-token"}')
  const mcp = await change('/api/integrations/mcp', 'POST', () => dialog.getByRole('button', { name: 'Save server', exact: true }).click())
  assert(!JSON.stringify(mcp).includes('fixture-mcp-token'))
  assert(!JSON.stringify(mcp).includes('fixture-header-token'))
  await page.getByRole('heading', { name: 'qa-mcp', exact: true }).waitFor()
  await snapshot('settings-integrations')
  await change('/api/integrations/mcp/qa-mcp', 'PATCH', () => page.getByLabel('Enable server', { exact: true }).click())
  await page.getByRole('button', { name: 'Edit qa-mcp', exact: true }).click()
  const editor = page.getByRole('dialog', { name: 'Edit MCP server', exact: true })
  assert.equal(await editor.getByLabel('Replace endpoint URL', { exact: true }).inputValue(), '')
  assert.equal(await editor.getByLabel('Replacement headers (JSON)', { exact: true }).inputValue(), '')
  await editor.getByLabel('Replacement headers (JSON)', { exact: true }).fill('{}')
  await change('/api/integrations/mcp/qa-mcp', 'PATCH', () => editor.getByRole('button', { name: 'Save server', exact: true }).click())
  await page.getByRole('button', { name: 'Remove qa-mcp', exact: true }).click()
  await change('/api/integrations/mcp/qa-mcp', 'DELETE', () => page.getByRole('dialog', { name: 'Remove MCP server?' }).getByRole('button', { name: 'Remove', exact: true }).click())
  evidence.push('MCP create/toggle/edit/confirmed remove use actual HTTP persistence; masked endpoints and headers never populate replacement inputs.')

  await page.getByRole('link', { name: 'System Status', exact: true }).click()
  await page.getByRole('heading', { name: 'System Status', exact: true }).waitFor()
  await change('/api/system', 'GET', () => page.getByRole('button', { name: 'Refresh', exact: true }).click())
  await snapshot('system')
  await page.setViewportSize({ width: 390, height: 844 })
  const sidebar = page.getByRole('button', { name: 'Open session sidebar', exact: true })
  await sidebar.click()
  await page.getByRole('button', { name: 'Close session sidebar', exact: true }).waitFor()
  await page.keyboard.press('Escape')
  await page.evaluate(() => new Promise((resolve) => window.requestAnimationFrame(resolve)))
  assert(await sidebar.evaluate((element) => element === document.activeElement))
  evidence.push('System Status refresh reads the local checks; all captured pages fit 390px and the mobile sidebar supports Escape/focus.')
  assert.deepEqual(errors, [], 'No browser runtime errors')
  await writeFile(`${outputDir}/evidence.json`, JSON.stringify({ checkedAt: new Date().toISOString(), base, browser: 'Chromium / Microsoft Edge', viewports: [{ width: 1440, height: 1000 }, { width: 390, height: 844 }], simulatedProvider: true, evidence, browserErrors: errors }, null, 2))
  console.log(JSON.stringify({ outputDir, evidence, browserErrors: errors }, null, 2))
} catch (error) {
  await page.screenshot({ path: `${outputDir}/failure.png`, fullPage: true })
  console.error(await page.locator('body').innerText())
  throw error
} finally {
  if (probeServer) { probeServer.closeAllConnections(); probeServer.close() }
  await browser.close()
}
