import assert from 'node:assert/strict'
import { test } from 'node:test'
import { pathToFileURL } from 'node:url'
const project=process.env.PAPERAGENT_TEST_PROJECT_ID, run=process.env.PAPERAGENT_TEST_RUN_ID, chapter=process.env.PAPERAGENT_TEST_CHAPTER_ID
test('real browser revision and restore interactions with intercepted writes', {skip:process.env.PAPERAGENT_WORKSPACE_LIVE_TEST!=='1',timeout:60000}, async () => {
 assert.ok(project && run && chapter, 'Set PAPERAGENT_TEST_PROJECT_ID, PAPERAGENT_TEST_RUN_ID and PAPERAGENT_TEST_CHAPTER_ID')
 const entry=process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
 const { chromium } = await import(entry ? pathToFileURL(entry).href : 'playwright')
 const browser=await chromium.launch({executablePath:process.env.PAPERAGENT_CHROME_PATH,headless:true})
 const page=await browser.newPage({viewport:{width:1440,height:900}})
 let revision, posts=[]
 await page.route('**/api/**', async route => {
  const req=route.request(), url=new URL(req.url()), path=url.pathname
  if(req.method()==='POST') {
   posts.push(path)
   if(path.endsWith('/revisions')) {
    const data=req.postDataJSON(); revision={revision_id:data.revision_id,chapter_id:chapter,base_version_id:data.base_version_id,result_version_id:null,mode:data.mode,status:'failed',error_code:'invalid_output',created_at:'2026-01-01T00:00:00Z'}
    return route.fulfill({status:202,json:revision})
   }
   if(path.endsWith('/restore')) return route.fulfill({status:200,json:{}})
   throw Error('Unexpected write blocked: '+path)
  }
  if(req.method()==='GET' && /\/revisions\/[^/]+$/.test(path)) return route.fulfill({json:revision})
  return route.continue()
 })
 page.on('dialog',dialog=>dialog.accept())
 try {
  await page.goto(`http://127.0.0.1:5173/projects/${project}/runs/${run}`)
  await page.locator('.chapter-link').first().waitFor()
  const text=await page.locator('.paragraph').first().innerText()
  await page.getByRole('button',{name:'修改与历史版本'}).first().click()
  await page.waitForFunction(()=>document.querySelector('.edit-tools select')?.options.length >= 2)
  const selector=page.locator('.edit-tools select').first();const old=await selector.locator('option').nth(1).getAttribute('value');assert.ok(old)
  await selector.selectOption(old);await page.getByText('对比当前版本').waitFor()
  await page.getByRole('button',{name:'恢复所选版本'}).click()
  await page.getByText('历史版本已恢复，其他版本仍然保留。').waitFor()
  await page.getByLabel('章节修改要求').first().fill('请改善论证衔接。')
  await page.getByRole('button',{name:'提交章节修改'}).first().click()
  await page.getByText('修改未成功；原版本保持不变。').waitFor()
  assert.equal(await page.locator('.paragraph').first().innerText(),text)
  assert.equal(posts.length,2)
 } finally {await browser.close()}
})
