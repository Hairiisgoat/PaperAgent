/** Browser routes intercept every write; no test run reaches MySQL. */
import assert from 'node:assert/strict'
import { test, before, after } from 'node:test'
import { pathToFileURL } from 'node:url'

const entry = process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
const { chromium } = await import(entry ? pathToFileURL(entry).href : 'playwright')
const base = process.env.PAPERAGENT_FRONTEND_URL || 'http://127.0.0.1:15173'
const executablePath = process.env.PAPERAGENT_CHROME_PATH || undefined
const projectId = '5f18db05-578b-44d0-9f29-61e38d034eaa'
const runId = '9c6c0d89-e05d-4a6f-a343-1b2a124d6faa'
const threadId = '45607c29-0e5f-43d5-bc04-68066a38fb19'
const secondRun = '6c939b3e-f050-4caf-a3e4-530dc517bf59'
const ref = '6713e124-5e13-4dea-9473-f52c624cab38'
const project = { id:projectId, user_id:null, title:'模拟论文', paper_type:'course_paper', topic:'测试', requirements:null, citation_style:'APA', lifecycle_status:'active', progress_status:'not_started', created_at:'2026-01-01T00:00:00Z', updated_at:'2026-01-01T00:00:00Z', archived_at:null, deleted_at:null }
const detail = (status='running', id=runId) => ({ run_id:id, thread_id:threadId, project_id:projectId, attempt_no:1, status, current_stage:'planning', started_at:'2026-01-01T00:00:00Z', finished_at:null, created_at:'2026-01-01T00:00:00Z', updated_at:'2026-01-01T00:00:00Z', error_code:null, retryable:null })
const event = (kind, seq, payload, status='running') => ({ event_id:ref, sequence:seq, run_id:runId, project_id:projectId, event_type:kind, stage:'planning', agent_name:null, status, timestamp:'2026-01-01T00:00:00Z', payload })
const progress = n => event('progress', n, { stage:'planning', agent_name:null, status:'running', message:'Workflow running' })
const approval = n => event('approval_required', n, { checkpoint_id:ref, approval_type:'task_approval', payload_ref:runId }, 'waiting_human')
const heartbeat = () => event('heartbeat', null, { timestamp:'2026-01-01T00:00:00Z' })
const sse = value => `${value.sequence === null ? '' : 'id: ' + value.sequence + '\n'}event: ${value.event_type}\ndata: ${JSON.stringify(value)}\n\n`
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
let browser
before(async () => { browser = await chromium.launch({ executablePath, headless:true }) })
after(async () => { await browser.close() })

test('SSE parser handles split UTF-8, CRLF, comments, multiline, heartbeat and incomplete EOF', async () => {
  const page = await browser.newPage()
  try {
    await page.goto(base + '/projects')
    const result = await page.evaluate(async () => {
      const { SSEParser } = await import('/src/services/sseParser.ts')
      const frames = []
      const parser = new SSEParser(frame => frames.push(frame))
      const input = ': comment\r\nid: 12\r\nevent: progress\r\ndata: 第一行\r\ndata: 第二行\r\n\r\nevent: heartbeat\ndata: {}\n\nevent: text_delta\ndata: 中文\n\nevent: error\ndata: incomplete'
      const bytes = new TextEncoder().encode(input)
      for (const byte of bytes) parser.push(Uint8Array.of(byte))
      parser.finish()
      return frames
    })
    assert.deepEqual(result, [
      { id:'12', event:'progress', data:'第一行\n第二行' },
      { event:'heartbeat', data:'{}' },
      { event:'text_delta', data:'中文' },
    ])
  } finally { await page.close() }
})

test('start uses one POST, opens real route and shows task approval controls', async () => {
  const page = await browser.newPage({ viewport:{width:1366,height:768} })
  let posts = 0, streams = 0
  try {
    await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req=route.request(), path=new URL(req.url()).pathname
      if (req.method()==='GET' && path===`/api/projects/${projectId}`) return route.fulfill({json:project})
      if (req.method()==='POST' && path===`/api/projects/${projectId}/runs`) { posts++; await delay(300); return route.fulfill({status:202,json:{project_id:projectId,run_id:runId,thread_id:threadId,status:'queued'}}) }
      if (req.method()==='GET' && path===`/api/projects/${projectId}/workflow-runs/${runId}`) return route.fulfill({json:detail('waiting_human')})
      if (req.method()==='GET' && path.endsWith('/approval')) return route.fulfill({json:{project_id:projectId,run_id:runId,thread_id:threadId,approval_type:'task_approval',checkpoint_id:ref,current_stage:'planning',item_ids:[ref]}})
      if (req.method()==='GET' && path.endsWith('/events')) { streams++; return route.fulfill({status:200,contentType:'text/event-stream',body:sse(progress(1))+sse(approval(2))}) }
      throw Error('unexpected API request')
    })
    await page.goto(base+`/projects/${projectId}`)
    await page.getByRole('heading',{name:'模拟论文'}).waitFor()
    await page.getByRole('button',{name:'启动模拟工作流'}).evaluate(el => { el.click(); el.click() })
    await page.waitForURL(`**/projects/${projectId}/runs/${runId}`)
    await page.getByRole('heading',{name:'任务审核'}).waitFor()
    assert.equal(posts,1)
    assert.equal(await page.getByRole('button',{name:'批准所选'}).count(),1)
    assert.equal(await page.getByText('等待任务审核').count(),1)
    await delay(800)
    assert.equal(streams, 1)
    await page.goto(base+`/projects/${projectId}`)
    await page.getByText('当前状态').waitFor()
    assert.equal(await page.getByText('等待人工审核').count(),1)
    assert.equal(await page.getByRole('link',{name:/查看运行详情/}).count(),1)
  } finally { await page.close() }
})

for (const scenario of ['403','404','409','422','503','network','timeout']) {
  test(`start ${scenario} is safe and never retries POST`, async () => {
    const page = await browser.newPage(); let posts=0
    try {
      await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

        const req=route.request(), path=new URL(req.url()).pathname
        if(req.method()==='GET' && path===`/api/projects/${projectId}`) return route.fulfill({json:project})
        if(req.method()==='POST' && path.endsWith('/runs')) { posts++; return scenario==='network' ? route.abort('failed') : route.fulfill({status:Number(scenario),json:{detail:'private-database-secret'}}) }
        throw Error('unexpected API request')
      })
      await page.goto(base+`/projects/${projectId}`)
      await page.getByRole('heading',{name:'模拟论文'}).waitFor()
      if(scenario==='timeout') await page.evaluate(async () => {
        const {http}=await import('/src/services/http.ts')
        http.defaults.adapter=async () => { throw {isAxiosError:true,code:'ECONNABORTED'} }
      })
      await page.getByRole('button',{name:'启动模拟工作流'}).click()
      await page.locator('.workflow-card [role=alert]').waitFor()
      assert.equal((await page.locator('body').innerText()).includes('private-database-secret'),false)
      await delay(800)
      assert.equal(posts,scenario==='timeout'?0:1)
    } finally { await page.close() }
  })
}

test('stream events render safely, deduplicate sequence and never infer status from events', async () => {
  const page = await browser.newPage()
  const delta=event('text_delta',null,{chapter_id:ref,delta:'<script>bad()</script> 中文',delta_sequence:1})
  const complete=event('completed',3,{result_ref:ref,finished_at:'2026-01-01T00:00:00Z'},'completed')
  const failure=event('error',4,{error_code:'timeout',safe_message:'Workflow execution failed',retryable:true},'failed')
  try {
    await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req=route.request(), path=new URL(req.url()).pathname
      if(req.method()==='GET' && path===`/api/projects/${projectId}/workflow-runs/${runId}`) return route.fulfill({json:detail('running')})
      if(req.method()==='GET' && path.endsWith('/events')) return route.fulfill({contentType:'text/event-stream',body:sse(progress(1))+sse(progress(1))+sse(approval(2))+sse(delta)+sse(complete)+sse(failure)+sse(heartbeat())})
      throw Error('unexpected API request')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.getByText('运行错误').first().waitFor()
    assert.equal(await page.getByText('工作流正在运行',{exact:true}).count(),1)
    assert.equal(await page.getByText('运行完成事件').count(),1)
    assert.equal(await page.getByText('运行错误').count(),1)
    assert.equal(await page.locator('pre').first().innerText(),'<script>bad()</script> 中文')
    assert.equal(await page.locator('pre script').count(),0)
    assert.equal(await page.getByText('连接正常',{exact:true}).count(),0)
    assert.equal(await page.locator('.metric strong').first().innerText(),'运行中')
  } finally { await page.close() }
})

test('reconnect and refresh restore durable cursor without posting or duplicates', async () => {
  const page = await browser.newPage(); const headers=[]; let streamCount=0, posts=0
  try {
    await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req=route.request(), path=new URL(req.url()).pathname
      if(req.method()==='POST') { posts++; throw Error('write forbidden') }
      if(path===`/api/projects/${projectId}/workflow-runs/${runId}`) return route.fulfill({json:detail('running')})
      if(path.endsWith('/events')) {
        streamCount++; headers.push(req.headers()['last-event-id'] || '')
        const body = streamCount===1 ? sse(progress(1)) : streamCount===2 ? sse(progress(1))+sse(approval(2)) : sse(heartbeat())
        return route.fulfill({contentType:'text/event-stream',body})
      }
      throw Error('unexpected request')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.getByText('等待任务审核').waitFor()
    assert.deepEqual(headers.slice(0,2),['','1'])
    await page.reload()
    await page.getByText('等待任务审核').waitFor()
    await page.waitForFunction(() => document.body.innerText.includes('游标 2'))
    await page.waitForTimeout(300)
    assert.ok(headers.includes('2'))
    assert.equal(await page.getByText('工作流正在运行',{exact:true}).count(),1)
    assert.equal(posts,0)
  } finally { await page.close() }
})

test('4xx stops reconnect and narrow layout fits', async () => {
  const page=await browser.newPage({viewport:{width:390,height:844}}); let streams=0
  try {
    await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const path=new URL(route.request().url()).pathname
      if(path.endsWith('/events')) { streams++; return route.fulfill({status:404}) }
      if(path.endsWith(runId)) return route.fulfill({json:detail('running')})
      throw Error('unexpected')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.getByText('订阅被拒绝', {exact:false}).waitFor()
    await delay(800); assert.equal(streams,1)
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true)
  } finally { await page.close() }
})

test('invalid saved cursor stops until user explicitly replays', async () => {
  const page=await browser.newPage(); const headers=[]
  try {
    const cached = progress(99)
    await page.addInitScript(({projectId,runId,cached}) => {
      sessionStorage.setItem(`paperagent.events.${projectId}.${runId}`, JSON.stringify([cached]))
    }, {projectId,runId,cached})
    await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req=route.request(), path=new URL(req.url()).pathname
      if(path.endsWith('/events')) {
        const cursor=req.headers()['last-event-id'] || ''; headers.push(cursor)
        return cursor==='99' ? route.fulfill({status:422}) : route.fulfill({contentType:'text/event-stream',body:sse(progress(1))})
      }
      if(path.endsWith(runId)) return route.fulfill({json:detail('running')})
      throw Error('unexpected')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.getByText('事件游标无效', {exact:false}).waitFor()
    await delay(700); assert.deepEqual(headers,['99'])
    await page.getByRole('button',{name:'从头加载事件'}).click()
    await page.getByText('工作流正在运行',{exact:true}).waitFor()
    assert.equal(headers[1], '')
  } finally { await page.close() }
})

test('leaving monitoring aborts the SSE subscription only', async () => {
  const page=await browser.newPage()
  try {
    await page.addInitScript(() => {
      const nativeFetch=window.fetch.bind(window)
      window.fetch = (input, init) => {
        if (String(input).endsWith('/events')) {
          const stream=new ReadableStream({start(controller) {
            init.signal.addEventListener('abort', () => { window.streamAborted=true; controller.close() }, {once:true})
          }})
          return Promise.resolve(new Response(stream,{headers:{'Content-Type':'text/event-stream'}}))
        }
        return nativeFetch(input,init)
      }
    })
    await page.route('**/api/**', route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const req=route.request(), path=new URL(req.url()).pathname
      if(req.method()!=='GET') throw Error('unexpected write')
      if(path.endsWith(runId)) return route.fulfill({json:detail('running')})
      if(path===`/api/projects/${projectId}`) return route.fulfill({json:project})
      throw Error('unexpected request')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.getByText('实时连接中').waitFor()
    await page.getByRole('link',{name:'返回项目详情'}).click()
    await page.getByRole('heading',{name:'模拟论文'}).waitFor()
    assert.equal(await page.evaluate(() => window.streamAborted),true)
  } finally { await page.close() }
})

test('old run response cannot replace newer route', async () => {
  const page=await browser.newPage()
  try {
    await page.route('**/api/**', async route => {
    if (route.request().method() === 'GET' && new URL(route.request().url()).pathname.endsWith('/workflow-runs')) return route.fulfill({json:[]})

      const path=new URL(route.request().url()).pathname
      if(path.endsWith('/events')) return route.fulfill({status:404})
      if(path.endsWith(runId)) { await delay(600); try { await route.fulfill({json:detail('failed')}) } catch {} ; return }
      if(path.endsWith(secondRun)) return route.fulfill({json:detail('running',secondRun)})
      throw Error('unexpected')
    })
    await page.goto(base+`/projects/${projectId}/runs/${runId}`)
    await page.goto(base+`/projects/${projectId}/runs/${secondRun}`)
    await page.getByText(secondRun).waitFor()
    await delay(700)
    assert.equal(await page.locator('.metric strong').first().innerText(),'运行中')
  } finally { await page.close() }
})

