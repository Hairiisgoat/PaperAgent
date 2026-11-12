/** All writes in this suite are intercepted in the browser; no test data reaches MySQL. */
import assert from 'node:assert/strict'
import { test, before, after } from 'node:test'
import { pathToFileURL } from 'node:url'

const source = process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
const { chromium } = await import(source ? pathToFileURL(source).href : 'playwright')
const base = process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:15173'
const executablePath = process.env.PAPERAGENT_CHROME_PATH || undefined
const id = '5f18db05-578b-44d0-9f29-61e38d034eaa'
const nextId = '32f2078c-2205-42c4-a155-44f046aa180d'
const pause = ms => new Promise(resolve => setTimeout(resolve, ms))
const project = (changes = {}) => ({ id, user_id: null, title: '模拟项目', paper_type: 'course_paper', topic: '测试研究主题', requirements: null, citation_style: 'APA', lifecycle_status: 'active', progress_status: 'not_started', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T04:05:00Z', archived_at: null, deleted_at: null, ...changes })
let browser
before(async () => { browser = await chromium.launch({ executablePath, headless: true }) })
after(async () => { await browser.close() })

async function withPage(run, viewport = { width: 1366, height: 768 }) {
  const page = await browser.newPage({ viewport })
  const errors = []
  page.on('pageerror', e => errors.push(e.message))
  try { await run(page); assert.deepEqual(errors, []) } finally { await page.close() }
}
async function fillForm(page) {
  await page.locator('#title').fill('  模拟项目  ')
  await page.locator('#topic').fill('  测试研究主题  ')
  await page.locator('#citation-style').fill(' APA ')
}

// The server-side validation fixture contains hostile text to prove it is not displayed.
test('create validates fields, suppresses duplicate submit and routes on success', async () => withPage(async page => {
  let posts = 0
  let captured
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    const req = route.request()
    const url = new URL(req.url())
    if (req.method() === 'POST' && url.pathname === '/api/projects') {
      posts++; captured = req.postDataJSON(); await pause(450)
      return route.fulfill({ status: 201, json: project() })
    }
    if (req.method() === 'GET' && url.pathname === `/api/projects/${id}`) return route.fulfill({ json: project() })
    throw Error('Unexpected API request: ' + req.method() + ' ' + url.pathname)
  })
  await page.goto(base + '/projects/new')
  await page.getByRole('button', { name: '创建项目' }).click()
  assert.equal(await page.getByText('请填写此项').count(), 3)
  assert.equal(posts, 0)
  await page.locator('#title').fill('😀'.repeat(201))
  await page.locator('#topic').fill('研究')
  await page.locator('#citation-style').fill('APA')
  await page.getByRole('button', { name: '创建项目' }).click()
  assert.equal(await page.getByText('项目名称不能超过 200 个字符').count(), 1)
  await page.locator('#title').fill('😀'.repeat(200))
  await page.locator('#topic').fill('中'.repeat(21846))
  await page.getByRole('button', { name: '创建项目' }).click()
  assert.equal(await page.getByText('论文主题不能超过 65,535 UTF-8 字节').count(), 1)
  assert.equal(posts, 0)
  await page.locator('#topic').fill('研究')
  await page.locator('#citation-style').fill('字'.repeat(41))
  await page.getByRole('button', { name: '创建项目' }).click()
  assert.equal(await page.getByText('引用格式不能超过 40 个字符').count(), 1)
  assert.equal(posts, 0)
  await fillForm(page)
  await page.getByRole('button', { name: '创建项目' }).click()
  await page.locator('form').evaluate(form => form.requestSubmit())
  assert.equal(await page.getByRole('button', { name: '正在创建' }).isDisabled(), true)
  await page.waitForURL(`**/projects/${id}`)
  await page.getByRole('heading', { name: '模拟项目' }).waitFor()
  assert.equal(posts, 1)
  assert.deepEqual(captured, { title: '模拟项目', topic: '测试研究主题', citation_style: 'APA', paper_type: 'course_paper' })
  assert.equal(Object.hasOwn(captured, 'requirements'), false)
}))

for (const scenario of ['422', '409', '503', 'network', 'timeout']) {
  test(`create ${scenario} shows a safe error and preserves inputs`, async () => withPage(async page => {
    let posts = 0
    await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req = route.request(); const url = new URL(req.url())
      if (req.method() !== 'POST' || url.pathname !== '/api/projects') throw Error('Unexpected API request')
      posts++
      if (scenario === 'network') return route.abort('failed')
      if (scenario === '422') return route.fulfill({ status: 422, json: { detail: [{ loc: ['body', 'title'], type: 'string_too_long', msg: 'private-server-message', input: 'private-input' }, { loc: ['body', '__proto__'], type: 'value_error', msg: 'private-unknown' }] } })
      return route.fulfill({ status: Number(scenario), json: { detail: 'private-server-message' } })
    })
    await page.goto(base + '/projects/new')
    if (scenario === 'timeout') await page.evaluate(async () => {
      const { http } = await import('/src/services/http.ts')
      http.defaults.adapter = async () => { throw { isAxiosError: true, code: 'ECONNABORTED' } }
    })
    await fillForm(page)
    await page.getByRole('button', { name: '创建项目' }).click()
    const expected = scenario === '422' ? '部分输入未通过验证' : scenario === '409' ? '项目状态发生冲突' : scenario === '503' ? '服务暂不可用' : '连接中断或超时'
    await page.getByText(expected, { exact: false }).waitFor()
    assert.equal(await page.locator('#title').inputValue(), '  模拟项目  ')
    assert.equal(await page.locator('#topic').inputValue(), '  测试研究主题  ')
    assert.equal(await page.locator('#citation-style').inputValue(), ' APA ')
    if (scenario === '422') assert.equal(await page.getByText('输入内容过长').count(), 1)
    const text = await page.locator('body').innerText()
    for (const secret of ['private-server-message', 'private-input', 'private-unknown']) assert.equal(text.includes(secret), false)
    assert.equal(posts, scenario === 'timeout' ? 0 : 1)
  }))
}

test('detail handles loading, requirements, unknown values and refresh', async () => withPage(async page => {
  let payload = project({ requirements: { word_count: 2000, note: '<script>window.bad=1</script>' }, lifecycle_status: 'future_state', progress_status: 'future_progress', paper_type: 'future_type', archived_at: '2026-01-02T00:00:00Z' })
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    assert.equal(route.request().method(), 'GET')
    await pause(250)
    return route.fulfill({ json: payload })
  })
  await page.goto(base + `/projects/${id}`, { waitUntil: 'domcontentloaded' })
  await page.getByText('正在加载项目…').waitFor()
  await page.getByRole('heading', { name: '模拟项目' }).waitFor()
  for (const label of ['未知类型', '未知状态', '未知进度']) assert.ok(await page.getByText(label).count())
  assert.ok((await page.locator('pre').innerText()).includes('word_count'))
  assert.equal(await page.evaluate(() => window.bad), undefined)
  assert.equal(await page.getByText('归档时间（本地时间）').count(), 1)
  assert.equal(await page.getByRole('button', { name: /编辑|归档|删除|恢复/ }).count(), 0)
  payload = project({ requirements: {} })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByText('暂无配置').waitFor()
  payload = project({ requirements: null })
  await page.reload({ waitUntil: 'networkidle' })
  await page.getByText('暂无配置').waitFor()
  assert.equal(await page.getByRole('link', { name: '返回项目列表' }).count(), 1)
}))

for (const scenario of ['404', '422', '503', 'network', 'empty']) {
  test(`detail ${scenario} state`, async () => withPage(async page => {
    await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      assert.equal(route.request().method(), 'GET')
      if (scenario === 'network') return route.abort('failed')
      if (scenario === 'empty') return route.fulfill({ status: 200, contentType: 'application/json', body: 'null' })
      return route.fulfill({ status: Number(scenario), json: { detail: 'private-detail' } })
    })
    await page.goto(base + `/projects/${id}`)
    const label = scenario === '404' ? '项目不存在或已删除' : scenario === '422' ? '项目地址无效' : scenario === '503' ? '服务暂时不可用' : scenario === 'empty' ? '暂无项目数据' : '无法加载项目'
    await page.getByText(label).waitFor()
    assert.equal((await page.locator('body').innerText()).includes('private-detail'), false)
  }))
}

test('switching project IDs ignores obsolete detail responses', async () => withPage(async page => {
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    assert.equal(route.request().method(), 'GET')
    const stale = new URL(route.request().url()).pathname.endsWith(id)
    await pause(stale ? 700 : 40)
    try { await route.fulfill({ json: project({ id: stale ? id : nextId, title: stale ? '过期详情' : '当前详情' }) }) } catch (e) { if (!String(e).includes('handled') && !String(e).includes('closed')) throw e }
  })
  await page.goto(base + `/projects/${id}`, { waitUntil: 'domcontentloaded' })
  await page.getByText('正在加载项目…').waitFor()
  await page.evaluate(async target => { await document.querySelector('#app').__vue_app__.config.globalProperties.$router.push(`/projects/${target}`) }, nextId)
  await page.getByRole('heading', { name: '当前详情' }).waitFor()
  await pause(900)
  assert.equal(await page.getByText('过期详情').count(), 0)
}))

test('narrow viewport renders the create form without page overflow', async () => withPage(async page => {
  await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})
 throw Error('Unexpected API request') })
  await page.goto(base + '/projects/new')
  assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth), true)
  assert.equal(await page.getByRole('heading', { name: '创建论文项目' }).count(), 1)
}, { width: 390, height: 844 }))

test('real backend detail not found uses GET only', { skip: process.env.PAPERAGENT_REAL_BACKEND !== '1' }, async () => withPage(async page => {
  page.on('request', req => { if (req.url().includes('/api/')) assert.equal(req.method(), 'GET') })
  await page.goto(base + `/projects/${id}`)
  await page.getByText('项目不存在或已删除').waitFor()
}))


test('list links open the new and detail views without writes', async () => withPage(async page => {
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    assert.equal(route.request().method(), 'GET')
    const path = new URL(route.request().url()).pathname
    if (path === '/api/projects') return route.fulfill({ json: { items: [project()], total: 1, page: 1, page_size: 20 } })
    if (path === `/api/projects/${id}`) return route.fulfill({ json: project() })
    throw Error('Unexpected API path')
  })
  await page.goto(base + '/projects')
  await page.getByRole('link', { name: '模拟项目' }).click()
  await page.waitForURL(`**/projects/${id}`)
  await page.getByRole('heading', { name: '模拟项目' }).waitFor()
  assert.ok((await page.locator('.nav-link').getAttribute('class')).includes('nav-link-active'))
  await page.getByRole('link', { name: '返回项目列表' }).click()
  await page.getByRole('button', { name: '新建项目' }).click()
  await page.getByRole('heading', { name: '创建论文项目' }).waitFor()
  assert.ok((await page.locator('.nav-link').getAttribute('class')).includes('nav-link-active'))
}))

test('Unicode whitespace follows backend validation without stripping BOM', async () => withPage(async page => {
  let captured
  await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    const req = route.request()
    const path = new URL(req.url()).pathname
    if (req.method() === 'POST' && path === '/api/projects') {
      captured = req.postDataJSON()
      return route.fulfill({ status: 201, json: project({ title: '\ufeff' }) })
    }
    if (req.method() === 'GET' && path === `/api/projects/${id}`) return route.fulfill({ json: project({ title: '\ufeff' }) })
    throw Error('Unexpected API request')
  })
  await page.goto(base + '/projects/new')
  await page.locator('#title').fill('\u0085')
  await page.locator('#topic').fill('研究')
  await page.locator('#citation-style').fill('APA')
  await page.getByRole('button', { name: '创建项目' }).click()
  assert.equal(await page.getByText('请填写此项').count(), 1)
  assert.equal(captured, undefined)
  await page.locator('#title').fill('\ufeff')
  await page.getByRole('button', { name: '创建项目' }).click()
  await page.waitForURL(`**/projects/${id}`)
  assert.equal(captured.title, '\ufeff')
}))
