/** Explicit opt-in: creates exactly one real project, then soft-deletes it. No mocks. */
import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { execFileSync } from 'node:child_process'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { dirname, resolve } from 'node:path'
import { test } from 'node:test'

const enabled = process.env.PAPERAGENT_REAL_WRITE_E2E === '1'
const base = 'http://127.0.0.1:15173'
const backendDir = resolve(dirname(fileURLToPath(import.meta.url)), '../../后端')
const python = resolve(backendDir, '.venv/Scripts/python.exe')
const sqlRead = String.raw`
import json, sys
sys.stdout.reconfigure(encoding='utf-8')
from sqlalchemy import select, func, text
from app.core.config import get_database_settings
from app.infrastructure.database import get_engine
from app.models import PaperProject
from uuid import UUID
s = get_database_settings()
assert (s.host,s.port,s.database,s.user) == ('127.0.0.1',3306,'paper_agent','paper_agent_app')
with get_engine().connect() as c:
 assert c.execute(text('SELECT DATABASE()')).scalar_one() == 'paper_agent'
 assert c.execute(text('SELECT CURRENT_USER()')).scalar_one() == 'paper_agent_app@127.0.0.1'
 counts = dict(c.execute(select(PaperProject.lifecycle_status, func.count()).group_by(PaperProject.lifecycle_status)).all())
 row = None
 if len(sys.argv) > 1:
  result = c.execute(select(PaperProject.__table__).where(PaperProject.id == UUID(sys.argv[1]))).mappings().one_or_none()
  row = dict(result) if result else None
 print(json.dumps({'counts': counts, 'total': sum(counts.values()), 'project': row}, default=str, ensure_ascii=False))
`
function readDatabase(id) {
  return JSON.parse(execFileSync(python, ['-c', sqlRead, ...(id ? [id] : [])], { cwd: backendDir, encoding: 'utf8' }))
}

test('real browser to FastAPI to dedicated MySQL: project lifecycle', { skip: !enabled, timeout: 150000 }, async () => {
  const before = readDatabase()
  const source = process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
  const { chromium } = await import(source ? pathToFileURL(source).href : 'playwright')
  const browser = await chromium.launch({ executablePath: process.env.PAPERAGENT_CHROME_PATH || undefined, headless: true })
  const context = await browser.newContext({ viewport: { width: 1366, height: 900 } })
  const page = await context.newPage()
  const title = `M1-06-06-INTEGRATION-TEST-${Date.now()}-${randomUUID().slice(0, 8)}`
  const editedTitle = `${title}-EDITED`
  let id, createdAttempted = false, deleteAttempted = false
  const statuses = []
  console.log('E2E BEFORE', JSON.stringify(before), 'TEST_TITLE', title)
  const responseFor = (method, suffix) => page.waitForResponse(r => r.request().method() === method && new URL(r.url()).pathname === suffix, { timeout: 20000 })
  async function findByTitle() {
    const found = []
    for (let number = 1; ; number++) {
      const response = await context.request.get(`${base}/api/projects?page=${number}&page_size=100`)
      assert.equal(response.status(), 200)
      const result = await response.json()
      found.push(...result.items.filter(item => item.title === title))
      if (number * result.page_size >= result.total) break
    }
    assert.ok(found.length <= 1, 'Ambiguous test title; refusing cleanup')
    return found[0]?.id
  }
  async function listContains(filter, expected) {
    await page.goto(`${base}/projects`)
    if (filter) await page.getByRole('button', { name: filter, exact: true }).click()
    const label = filter === '进行中' ? 'active' : filter === '已归档' ? 'archived' : undefined
    const result = await context.request.get(`${base}/api/projects?page=1&page_size=100${label ? `&lifecycle_status=${label}` : ''}`)
    assert.equal(result.status(), 200)
    assert.equal((await result.json()).items.some(item => item.id === id), expected)
    if (expected) await page.getByRole('link', { name: editedTitle }).waitFor()
    else assert.equal(await page.getByRole('link', { name: editedTitle }).count(), 0)
  }
  try {
    const direct = await context.request.get('http://127.0.0.1:8000/openapi.json')
    const spec = await direct.json()
    assert.equal(direct.status(), 200)
    assert.ok(spec.paths['/api/projects/{project_id}/archive'])
    await page.goto(`${base}/projects/new`)
    await page.locator('#title').fill(title)
    await page.locator('#topic').fill('真实联调初始主题')
    await page.locator('#citation-style').fill('APA')
    const creating = responseFor('POST', '/api/projects')
    createdAttempted = true
    await page.getByRole('button', { name: '创建项目', exact: true }).click()
    const created = await creating
    statuses.push(['create', created.status()])
    assert.equal(created.status(), 201)
    const initial = await created.json()
    id = initial.id
    assert.match(id, /^[0-9a-f-]{36}$/i)
    console.log('E2E CREATED UUID', id)
    await page.waitForURL(`**/projects/${id}`)
    await page.getByRole('heading', { name: title }).waitFor()
    const detail = await context.request.get(`${base}/api/projects/${id}`)
    statuses.push(['detail', detail.status()]); assert.equal(detail.status(), 200)
    const dbCreated = readDatabase(id).project
    assert.equal(dbCreated.title, title); assert.equal(dbCreated.topic, initial.topic)
    assert.equal(dbCreated.citation_style, initial.citation_style)

    await page.getByRole('button', { name: '编辑项目' }).click()
    await page.locator('#edit-title-field').fill(editedTitle)
    await page.locator('#edit-topic').fill('真实联调修改后的主题')
    await page.locator('#edit-citation').fill('GB/T 7714')
    const patching = responseFor('PATCH', `/api/projects/${id}`)
    await page.getByRole('button', { name: '保存修改' }).click()
    const patch = await patching
    statuses.push(['edit', patch.status()]); assert.equal(patch.status(), 200)
    assert.deepEqual(Object.keys(patch.request().postDataJSON()).sort(), ['citation_style', 'title', 'topic'])
    await page.waitForURL(`**/projects/${id}`)
    await page.reload(); await page.getByRole('heading', { name: editedTitle }).waitFor()
    await page.getByText('真实联调修改后的主题', { exact: true }).waitFor()
    await page.getByText('GB/T 7714', { exact: true }).waitFor()
    let saved = readDatabase(id).project
    for (const key of ['paper_type', 'requirements', 'user_id', 'progress_status']) assert.deepEqual(saved[key], dbCreated[key])
    assert.equal(saved.title, editedTitle); assert.equal(saved.topic, '真实联调修改后的主题'); assert.equal(saved.citation_style, 'GB/T 7714')

    await page.getByRole('button', { name: '归档', exact: true }).click()
    const archiving = responseFor('POST', `/api/projects/${id}/archive`)
    await page.getByRole('dialog').getByRole('button', { name: '确认归档' }).click()
    const archived = await archiving
    statuses.push(['archive', archived.status()]); assert.equal(archived.status(), 200)
    assert.equal((await archived.json()).lifecycle_status, 'archived')
    await page.getByText('项目已归档。').waitFor()
    assert.equal(await page.getByRole('button', { name: '编辑项目' }).count(), 0)
    const rejected = await context.request.patch(`${base}/api/projects/${id}`, { data: { title: `${title}-MUST-NOT-SAVE` } })
    statuses.push(['archived patch', rejected.status()]); assert.equal(rejected.status(), 409)
    assert.equal(readDatabase(id).project.title, editedTitle)
    await listContains('已归档', true)
    await listContains('进行中', false)

    await page.goto(`${base}/projects/${id}`)
    await page.getByRole('button', { name: '恢复', exact: true }).click()
    const restoring = responseFor('POST', `/api/projects/${id}/restore`)
    await page.getByRole('dialog').getByRole('button', { name: '确认恢复' }).click()
    const restored = await restoring
    statuses.push(['restore', restored.status()]); assert.equal(restored.status(), 200)
    assert.equal((await restored.json()).lifecycle_status, 'active')
    await page.getByText('项目已恢复。').waitFor()
    await page.reload(); await page.getByRole('button', { name: '编辑项目' }).click()
    await page.locator('#edit-title-field').waitFor()
    assert.equal(await page.locator('#edit-title-field').isEditable(), true)
    assert.equal(readDatabase(id).project.lifecycle_status, 'active')
    assert.equal(readDatabase(id).project.archived_at, null)
    await page.getByRole('button', { name: '取消', exact: true }).click()
    await listContains('进行中', true)
    await listContains('已归档', false)

    await page.goto(`${base}/projects/${id}`)
    await page.getByRole('button', { name: '删除', exact: true }).click()
    const dialog = page.getByRole('dialog')
    await dialog.getByText(editedTitle).waitFor()
    await dialog.getByText('软删除', { exact: false }).waitFor()
    const deleting = responseFor('DELETE', `/api/projects/${id}`)
    deleteAttempted = true
    await dialog.getByRole('button', { name: '确认删除' }).click()
    const deleted = await deleting
    statuses.push(['delete', deleted.status()]); assert.equal(deleted.status(), 204)
    await page.waitForURL('**/projects')
    await listContains(null, false)
    const absent = await context.request.get(`${base}/api/projects/${id}`)
    statuses.push(['deleted detail', absent.status()]); assert.equal(absent.status(), 404)
    await page.goto(`${base}/projects/${id}`)
    await page.getByText('项目不存在或已删除').waitFor()
    saved = readDatabase(id).project
    assert.equal(saved.lifecycle_status, 'deleted'); assert.ok(saved.deleted_at)
    assert.equal(saved.title, editedTitle)
    console.log('E2E STATUSES', JSON.stringify(statuses))
  } finally {
    try {
      if (!id && createdAttempted) id = await findByTitle()
      if (id) {
        const current = await context.request.get(`${base}/api/projects/${id}`)
        if (current.status() === 200 && !deleteAttempted) {
          deleteAttempted = true
          const cleanup = await context.request.delete(`${base}/api/projects/${id}`)
          console.log('E2E CLEANUP DELETE', cleanup.status())
          assert.equal(cleanup.status(), 204)
        } else if (current.status() !== 404) {
          throw new Error(`Cleanup cannot safely proceed: GET ${current.status()}, deleteAttempted=${deleteAttempted}`)
        }
        assert.equal((await context.request.get(`${base}/api/projects/${id}`)).status(), 404)
        const after = readDatabase(id)
        console.log('E2E AFTER', JSON.stringify({ total: after.total, counts: after.counts, id, lifecycle_status: after.project?.lifecycle_status, deleted_at: after.project?.deleted_at }))
        assert.equal(after.project?.lifecycle_status, 'deleted')
        assert.equal(after.total, before.total + 1)
      } else if (createdAttempted) {
        console.log('E2E CLEANUP: no matching visible project found; inspect server and database if creation result was uncertain')
      }
    } finally { await context.close(); await browser.close() }
  }
})
