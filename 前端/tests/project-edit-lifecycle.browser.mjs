/** All writes are intercepted; no project test data reaches the real backend. */
import assert from 'node:assert/strict'
import { test, before, after } from 'node:test'
import { pathToFileURL } from 'node:url'

const source = process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
const { chromium } = await import(source ? pathToFileURL(source).href : 'playwright')
const base = process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:15173'
const executablePath = process.env.PAPERAGENT_CHROME_PATH || undefined
const id = '5f18db05-578b-44d0-9f29-61e38d034eaa'
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const project = (changes = {}) => ({ id, user_id: null, title: '模拟项目', paper_type: 'course_paper', topic: '测试研究主题', requirements: { preserved: 'value' }, citation_style: 'APA', lifecycle_status: 'active', progress_status: 'not_started', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-02T04:05:00Z', archived_at: null, deleted_at: null, ...changes })
let browser
before(async () => { browser = await chromium.launch({ executablePath, headless: true }) })
after(async () => { await browser.close() })
async function withPage(run) { const page = await browser.newPage({ viewport: { width: 1366, height: 768 } }); const errors=[]; page.on('pageerror', e => errors.push(e.message)); try { await run(page); assert.deepEqual(errors, []) } finally { await page.close() } }

function mockApi(page, handlers) {
  return page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

    const req = route.request(); const path = new URL(req.url()).pathname
    const fn = handlers[`${req.method()} ${path}`]
    if (!fn) throw Error(`Unexpected API call ${req.method()} ${path}`)
    return fn(route, req)
  })
}
const detailPath = `/api/projects/${id}`
const emptyPage = { items: [], total: 0, page: 1, page_size: 20 }

test('edit loads project, sends only changed field and returns to detail', async () => withPage(async page => {
  let current = project(); const patches=[]
  await mockApi(page, {
    [`GET ${detailPath}`]: route => route.fulfill({ json: current }),
    [`PATCH ${detailPath}`]: (route, req) => { patches.push(req.postDataJSON()); current = { ...current, ...req.postDataJSON() }; return route.fulfill({ json: current }) },
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').waitFor()
  assert.equal(await page.locator('#edit-title-field').inputValue(), '模拟项目')
  assert.equal(await page.locator('#edit-paper-type').isEditable(), false)
  assert.equal(await page.getByRole('button', { name: '保存修改' }).isDisabled(), true)
  await page.locator('#edit-title-field').fill('修改后的项目')
  await page.getByRole('button', { name: '保存修改' }).click()
  await page.waitForURL(`**/projects/${id}`)
  await page.getByRole('heading', { name: '修改后的项目' }).waitFor()
  assert.deepEqual(patches, [{ title: '修改后的项目' }])
  assert.deepEqual(current.requirements, { preserved: 'value' })
}))

test('archived project is read-only and cannot be patched', async () => withPage(async page => {
  let patches=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => route.fulfill({ json: project({ lifecycle_status: 'archived' }) }),
    [`PATCH ${detailPath}`]: route => { patches++; return route.fulfill({ status: 409 }) },
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.getByText('项目已归档', { exact: false }).waitFor()
  assert.equal(await page.locator('#edit-title-field').isEditable(), false)
  assert.equal(await page.getByRole('button', { name: '保存修改' }).isDisabled(), true)
  assert.equal(patches, 0)
}))

test('unsaved changes require confirmation on route leave', async () => withPage(async page => {
  await mockApi(page, { [`GET ${detailPath}`]: route => route.fulfill({ json: project() }) })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').fill('未保存草稿')
  page.once('dialog', dialog => dialog.dismiss())
  await page.getByRole('button', { name: '取消', exact: true }).click()
  assert.ok(page.url().endsWith('/edit'))
  assert.equal(await page.locator('#edit-title-field').inputValue(), '未保存草稿')
  page.once('dialog', dialog => dialog.accept())
  await page.getByRole('button', { name: '取消', exact: true }).click()
  await page.waitForURL(`**/projects/${id}`)
}))

test('422 field errors are sanitized and retain edit input', async () => withPage(async page => {
  await mockApi(page, {
    [`GET ${detailPath}`]: route => route.fulfill({ json: project() }),
    [`PATCH ${detailPath}`]: route => route.fulfill({ status: 422, json: { detail: [{ loc: ['body','title'], type: 'string_too_long', msg: 'private-detail', input: 'private-input' }] } }),
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').fill('保留的草稿')
  await page.getByRole('button', { name: '保存修改' }).click()
  await page.getByText('输入内容过长').waitFor()
  assert.equal(await page.locator('#edit-title-field').inputValue(), '保留的草稿')
  const text = await page.locator('body').innerText()
  assert.equal(text.includes('private-detail') || text.includes('private-input'), false)
}))

for (const failure of ['409', '503', 'network', 'timeout']) {
  test(`edit ${failure} reconciles by GET, preserves draft, and does not auto retry`, async () => withPage(async page => {
    let patches=0, gets=0
    await mockApi(page, {
      [`GET ${detailPath}`]: route => { gets++; return route.fulfill({ json: project() }) },
      [`PATCH ${detailPath}`]: route => { patches++; if (failure === 'network') return route.abort('failed'); return route.fulfill({ status: Number(failure), json: { detail: 'private-server' } }) },
    })
    await page.goto(base + `/projects/${id}/edit`)
    await page.locator('#edit-title-field').fill('保留草稿')
    if (failure === 'timeout') await page.evaluate(async () => {
      const { http } = await import('/src/services/http.ts')
      http.defaults.adapter = async config => {
        if (config.method === 'patch') throw { isAxiosError: true, code: 'ECONNABORTED' }
        const response = await fetch('/api/projects/5f18db05-578b-44d0-9f29-61e38d034eaa')
        return { data: await response.json(), status: response.status, statusText: 'OK', headers: {}, config }
      }
    })
    await page.getByRole('button', { name: '保存修改' }).click()
    await page.getByText('已读取最新项目', { exact: false }).waitFor()
    assert.equal(await page.locator('#edit-title-field').inputValue(), '保留草稿')
    assert.equal(await page.getByRole('button', { name: '保存修改' }).isDisabled(), true)
    assert.ok(gets >= 2)
    assert.equal(patches, failure === 'timeout' ? 0 : 1)
    assert.equal((await page.locator('body').innerText()).includes('private-server'), false)
    await page.getByRole('button', { name: '已检查，继续编辑' }).click()
    assert.equal(await page.getByRole('button', { name: '保存修改' }).isEnabled(), true)
  }))
}

test('archive, restore, cancel delete and soft delete use confirmation and refresh', async () => withPage(async page => {
  let current=project(), deleted=false; const writes=[]; let details=0, lists=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { details++; return deleted ? route.fulfill({ status: 404, json: { detail: 'not found' } }) : route.fulfill({ json: current }) },
    'GET /api/projects': route => { lists++; return route.fulfill({ json: emptyPage }) },
    [`POST ${detailPath}/archive`]: async route => { writes.push('archive'); await delay(300); current=project({ lifecycle_status: 'archived', archived_at: '2026-01-02T00:00:00Z' }); return route.fulfill({ json: current }) },
    [`POST ${detailPath}/restore`]: route => { writes.push('restore'); current=project(); return route.fulfill({ json: current }) },
    [`DELETE ${detailPath}`]: route => { writes.push('delete'); deleted=true; return route.fulfill({ status: 204, body: '' }) },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '归档', exact: true }).click()
  let dialog=page.getByRole('dialog')
  await dialog.getByText('归档后项目将变为只读', { exact: false }).waitFor()
  await dialog.getByRole('button', { name: '确认归档' }).evaluate(el => { el.click(); el.click() })
  await page.getByText('项目已归档。').waitFor()
  assert.deepEqual(writes, ['archive'])
  assert.equal(await page.getByRole('button', { name: '编辑项目' }).count(), 0)
  assert.ok(details >= 2 && lists >= 1)
  await page.getByRole('button', { name: '恢复', exact: true }).click()
  dialog=page.getByRole('dialog'); await dialog.getByRole('button', { name: '确认恢复' }).click()
  await page.getByText('项目已恢复。').waitFor()
  assert.equal(await page.getByRole('button', { name: '编辑项目' }).count(), 1)
  assert.ok(details >= 3 && lists >= 2)
  await page.getByRole('button', { name: '删除', exact: true }).click()
  dialog=page.getByRole('dialog'); await dialog.getByText('模拟项目').waitFor()
  await dialog.getByText('软删除', { exact: false }).waitFor()
  await dialog.getByRole('button', { name: '取消' }).click()
  assert.deepEqual(writes, ['archive','restore'])
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认删除' }).click()
  await page.waitForURL('**/projects')
  await page.getByText('还没有论文项目').waitFor()
  assert.deepEqual(writes, ['archive','restore','delete'])
  await page.goto(base + `/projects/${id}`)
  await page.getByText('项目不存在或已删除').waitFor()
}))

test('lifecycle conflict refreshes server state and performs no retry', async () => withPage(async page => {
  let details=0, writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { details++; return route.fulfill({ json: project({ lifecycle_status: details === 1 ? 'active' : 'archived' }) }) },
    'GET /api/projects': route => route.fulfill({ json: emptyPage }),
    [`POST ${detailPath}/archive`]: route => { writes++; return route.fulfill({ status: 409, json: { detail: 'internal-secret' } }) },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '归档', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认归档' }).click()
  await page.getByText('项目状态已变化', { exact: false }).waitFor()
  assert.equal(await page.getByRole('button', { name: '恢复', exact: true }).count(), 1)
  assert.equal(await page.getByRole('button', { name: '编辑项目' }).count(), 0)
  assert.equal(writes, 1); assert.ok(details >= 2)
  assert.equal((await page.locator('body').innerText()).includes('internal-secret'), false)
}))

test('uncertain lifecycle result re-reads and never repeats the write', async () => withPage(async page => {
  let details=0, writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { details++; return route.fulfill({ json: project({ lifecycle_status: details === 1 ? 'active' : 'archived' }) }) },
    'GET /api/projects': route => route.fulfill({ json: emptyPage }),
    [`POST ${detailPath}/archive`]: route => { writes++; return route.abort('failed') },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '归档', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认归档' }).click()
  await page.getByText('操作结果未确认', { exact: false }).waitFor()
  assert.equal(writes, 1); assert.ok(details >= 2)
  assert.equal(await page.getByRole('button', { name: '恢复', exact: true }).count(), 1)
}))


test('edit 409 into archived state preserves draft and blocks saving', async () => withPage(async page => {
  let gets=0, writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { gets++; return route.fulfill({ json: project({ lifecycle_status: gets === 1 ? 'active' : 'archived' }) }) },
    [`PATCH ${detailPath}`]: route => { writes++; return route.fulfill({ status: 409, json: { detail: 'conflict' } }) },
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').fill('保留未提交草稿')
  await page.getByRole('button', { name: '保存修改' }).click()
  await page.getByText('项目已归档', { exact: false }).waitFor()
  assert.equal(await page.locator('#edit-title-field').inputValue(), '保留未提交草稿')
  assert.equal(await page.locator('#edit-title-field').isEditable(), false)
  assert.equal(await page.getByRole('button', { name: '保存修改' }).isDisabled(), true)
  assert.equal(writes, 1)
}))

test('edit 404 retains draft and does not allow another write', async () => withPage(async page => {
  let gets=0, writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { gets++; return gets === 1 ? route.fulfill({ json: project() }) : route.fulfill({ status: 404, json: { detail: 'not found' } }) },
    [`PATCH ${detailPath}`]: route => { writes++; return route.fulfill({ status: 404, json: { detail: 'not found' } }) },
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').fill('未提交草稿')
  await page.getByRole('button', { name: '保存修改' }).click()
  await page.getByText('项目不存在或已删除', { exact: false }).waitFor()
  assert.equal(await page.locator('#edit-title-field').inputValue(), '未提交草稿')
  assert.equal(await page.getByRole('button', { name: '保存修改' }).isDisabled(), true)
  assert.equal(writes, 1)
}))

test('repeated edit submit sends a single PATCH and beforeunload guards drafts', async () => withPage(async page => {
  let writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => route.fulfill({ json: project() }),
    [`PATCH ${detailPath}`]: async route => { writes++; await delay(800); return route.fulfill({ json: project({ title: '新名称' }) }) },
  })
  await page.goto(base + `/projects/${id}/edit`)
  await page.locator('#edit-title-field').fill('新名称')
  assert.equal(await page.evaluate(() => { const e=new Event('beforeunload',{cancelable:true}); window.dispatchEvent(e); return e.defaultPrevented }), true)
  await page.locator('form').evaluate(form => { form.requestSubmit(); form.requestSubmit() })
  await page.getByRole('button', { name: '保存修改' }).waitFor()
  assert.equal(await page.locator('#edit-title-field').isEditable(), false)
  await page.waitForURL(`**/projects/${id}`)
  await page.getByRole('heading', { name: '模拟项目' }).waitFor()
  assert.equal(writes, 1)
}))

test('archived project can be soft-deleted after confirmation', async () => withPage(async page => {
  let deletes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => route.fulfill({ json: project({ lifecycle_status: 'archived' }) }),
    'GET /api/projects': route => route.fulfill({ json: emptyPage }),
    [`DELETE ${detailPath}`]: route => { deletes++; return route.fulfill({ status: 204, body: '' }) },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认删除' }).click()
  await page.waitForURL('**/projects')
  assert.equal(deletes, 1)
}))

test('uncertain delete result re-reads 404 without claiming success', async () => withPage(async page => {
  let gets=0, deletes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { gets++; return gets === 1 ? route.fulfill({ json: project() }) : route.fulfill({ status: 404, json: { detail: 'not found' } }) },
    'GET /api/projects': route => route.fulfill({ json: emptyPage }),
    [`DELETE ${detailPath}`]: route => { deletes++; return route.abort('failed') },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '删除', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认删除' }).click()
  await page.getByText('项目不存在或已删除').waitFor()
  await page.getByText('操作结果未确认', { exact: false }).waitFor()
  assert.equal(deletes, 1); assert.ok(gets >= 2)
  assert.ok(page.url().endsWith(id))
}))


test('lifecycle 503 with failed reconciliation exposes read retry but no second write', async () => withPage(async page => {
  let gets=0, writes=0
  await mockApi(page, {
    [`GET ${detailPath}`]: route => { gets++; return gets === 1 ? route.fulfill({ json: project() }) : route.fulfill({ status: 503, json: { detail: 'private' } }) },
    'GET /api/projects': route => route.fulfill({ json: emptyPage }),
    [`POST ${detailPath}/archive`]: route => { writes++; return route.fulfill({ status: 503, json: { detail: 'private' } }) },
  })
  await page.goto(base + `/projects/${id}`)
  await page.getByRole('button', { name: '归档', exact: true }).click()
  await page.getByRole('dialog').getByRole('button', { name: '确认归档' }).click()
  await page.getByText('操作结果未确认', { exact: false }).waitFor()
  await page.getByRole('heading', { name: '服务暂时不可用' }).waitFor()
  assert.equal(await page.getByRole('button', { name: '归档', exact: true }).count(), 0)
  assert.equal(await page.getByRole('button', { name: '重试' }).count(), 1)
  assert.equal(writes, 1); assert.ok(gets >= 2)
  assert.equal((await page.locator('body').innerText()).includes('private'), false)
}))

for (const scenario of ['404', '422', '503', 'network']) {
  test(`initial edit ${scenario} has a safe error`, async () => withPage(async page => {
    await mockApi(page, { [`GET ${detailPath}`]: route => scenario === 'network' ? route.abort('failed') : route.fulfill({ status: Number(scenario), json: { detail: 'private-server' } }) })
    await page.goto(base + `/projects/${id}/edit`)
    const expected = scenario === '404' ? '项目不存在或已删除' : scenario === '422' ? '项目地址无效' : scenario === '503' ? '服务暂时不可用' : '无法加载项目'
    await page.getByText(expected, { exact: false }).waitFor()
    assert.equal((await page.locator('body').innerText()).includes('private-server'), false)
  }))
}
