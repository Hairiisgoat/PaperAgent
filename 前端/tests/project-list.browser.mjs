/** Browser checks use simulated GET responses; opt-in real GET never writes data. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'

const source = process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
const { chromium } = await import(source ? pathToFileURL(source).href : 'playwright')
const base = process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:15173'
const executablePath = process.env.PAPERAGENT_CHROME_PATH || undefined
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const row = (title, paperType = 'course_paper', lifecycle = 'active', progress = 'not_started') => ({
  id: '5f18db05-578b-44d0-9f29-61e38d034eaa', user_id: null, title, paper_type: paperType,
  topic: '测试主题', requirements: null, citation_style: 'APA', lifecycle_status: lifecycle,
  progress_status: progress, created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T04:05:00Z',
  archived_at: null, deleted_at: null,
})

test('project list states, parameters, cancellation and navigation', async () => {
  const browser = await chromium.launch({ executablePath, headless: true })
  const page = await browser.newPage({ viewport: { width: 1366, height: 768 } })
  const requests = []
  const pageErrors = []
  const cancelledRequests = []
  page.on('requestfailed', request => {
    if (request.url().includes('/api/projects') && request.failure()?.errorText.includes('ABORTED')) cancelledRequests.push(request.url())
  })
  let mode = 'slow'
  page.on('pageerror', error => pageErrors.push(error.message))
  try {
    await page.route('**/api/projects**', async route => {
      const request = route.request()
      assert.equal(request.method(), 'GET')
      const url = new URL(request.url())
      const filter = url.searchParams.get('lifecycle_status')
      const pageNumber = Number(url.searchParams.get('page'))
      const currentMode = mode
      requests.push({ page: pageNumber, size: url.searchParams.get('page_size'), filter, mode: currentMode })
      if (currentMode === 'network') return route.abort('failed')
      if (currentMode === '503') return route.fulfill({ status: 503, contentType: 'application/json', body: JSON.stringify({ detail: 'internal-secret' }) })
      if (currentMode === 'race') await delay(filter === 'active' ? 900 : 60)
      else if (currentMode === 'slow') await delay(400)
      let items = [], total = 0
      if (currentMode === 'data' || currentMode === 'slow') { items = [row(pageNumber === 1 ? '第一页项目' : '第二页项目')]; total = 21 }
      if (currentMode === 'race') { items = [row(filter === 'active' ? '过期项目' : '归档项目', 'course_paper', filter === 'active' ? 'active' : 'archived', 'completed')]; total = 1 }
      if (currentMode === 'unknown') { items = [row('未知值项目', 'future_type', 'future_state', 'future_progress')]; total = 1 }
      try {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ items, total, page: pageNumber, page_size: 20 }) })
      } catch (error) {
        // A cancelled request may no longer have a live route to fulfill.
        if (!String(error).includes('handled') && !String(error).includes('closed')) throw error
      }
    })
    await page.goto(base + '/', { waitUntil: 'domcontentloaded' })
    await page.getByText('正在加载项目…').waitFor()
    await page.getByText('第一页项目').waitFor()
    assert.equal(new URL(page.url()).pathname, '/projects')
    assert.deepEqual(requests[0], { page: 1, size: '20', filter: null, mode: 'slow' })
    assert.equal(await page.getByRole('button', { name: '新建项目' }).isEnabled(), true)
    mode = 'data'
    await page.getByRole('button', { name: '下一页' }).click()
    await page.getByText('第二页项目').waitFor()
    assert.equal(requests.at(-1).page, 2)
    await page.getByRole('button', { name: '进行中', exact: true }).click()
    await page.getByText('第一页项目').waitFor()
    assert.equal(requests.at(-1).page, 1)
    assert.equal(requests.at(-1).filter, 'active')
    mode = 'race'
    await page.getByRole('button', { name: '全部项目' }).click()
    await page.getByRole('button', { name: '进行中', exact: true }).click()
    await page.getByRole('button', { name: '进行中', exact: true }).click()
    await page.getByRole('button', { name: '已归档', exact: true }).click()
    await page.getByText('归档项目').waitFor()
    await delay(1200)
    assert.equal(await page.getByText('过期项目').count(), 0)
    assert.equal(requests.filter(r => r.mode === 'race' && r.filter === 'active').length, 1)
    assert.ok(cancelledRequests.some(url => new URL(url).searchParams.get('lifecycle_status') === 'active'))
    assert.equal(requests.at(-1).filter, 'archived')
    mode = 'empty'
    await page.getByRole('button', { name: '刷新项目列表' }).click()
    await page.getByText('当前状态下暂无项目').waitFor()
    await page.getByRole('button', { name: '查看全部项目' }).click()
    await page.getByText('还没有论文项目').waitFor()
    mode = '503'
    await page.getByRole('button', { name: '刷新项目列表' }).click()
    await page.getByRole('alert').waitFor()
    assert.equal(await page.getByText('服务暂时不可用').count(), 1)
    assert.equal((await page.locator('body').innerText()).includes('internal-secret'), false)
    mode = 'network'
    await page.getByRole('button', { name: '重试' }).click()
    await page.getByText('无法加载项目').waitFor()
    mode = 'unknown'
    await page.getByRole('button', { name: '重试' }).click()
    await page.getByText('未知值项目').waitFor()
    for (const label of ['未知类型', '未知状态', '未知进度']) assert.equal(await page.getByText(label).count(), 1)
    await page.reload({ waitUntil: 'networkidle' })
    await page.getByText('未知值项目').waitFor()
    assert.deepEqual(pageErrors, [])
  } finally {
    await browser.close()
  }
})

test('real proxy GET and list state', { skip: process.env.PAPERAGENT_REAL_BACKEND !== '1' }, async () => {
  const browser = await chromium.launch({ executablePath, headless: true })
  try {
    const page = await browser.newPage()
    let apiResponse
    const responsePromise = page.waitForResponse(response => response.url().includes('/api/projects?') && response.request().method() === 'GET')
    await page.goto(base + '/projects', { waitUntil: 'domcontentloaded' })
    apiResponse = await responsePromise
    assert.equal(apiResponse.status(), 200)
    const data = await apiResponse.json()
    if (data.total === 0) await page.getByText('还没有论文项目').waitFor()
    else await page.locator('.el-table').waitFor()
    assert.equal(await page.getByRole('button', { name: '新建项目' }).isEnabled(), true)
  } finally {
    await browser.close()
  }
})
