/** Opt-in real FastAPI + Vite + browser, using disposable SQLite only, never MySQL. */
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { spawn } from 'node:child_process'
import { mkdtempSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { createServer } from 'node:net'

const projectId = 'bcd6008f-0832-4d47-b4b2-20c903143c03'
const frontend = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const backend = resolve(frontend, '../后端')
const python = join(backend, '.venv', 'Scripts', 'python.exe')
const child = String.raw`
import os,sys
from uuid import UUID
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.infrastructure import database
from app.models import PaperProject
from app.models.base import Base
from app.main import app
import uvicorn
import pymysql
def forbid_mysql(*args, **kwargs): raise RuntimeError('MySQL forbidden in isolated test')
pymysql.connect = forbid_mysql
db, checkpoint, port = sys.argv[1:]
os.environ['LANGGRAPH_SQLITE_PATH'] = checkpoint
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
engine = create_engine('sqlite:///' + db, connect_args={'check_same_thread':False})
Base.metadata.create_all(engine)
database.SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
with database.SessionLocal() as session:
    session.add(PaperProject(id=UUID('bcd6008f-0832-4d47-b4b2-20c903143c03'), title='隔离工作流验收', topic='仅临时测试', citation_style='APA'))
    session.commit()
uvicorn.run(app,host='127.0.0.1',port=int(port),log_level='error')
`
async function port() {
  return await new Promise((resolvePort, reject) => {
    const server=createServer()
    server.on('error',reject)
    server.listen(0,'127.0.0.1',() => { const value=server.address().port; server.close(() => resolvePort(value)) })
  })
}
async function ready(url, processHandle) {
  const deadline=Date.now()+20000
  while(Date.now()<deadline) {
    if(processHandle.exitCode !== null) throw Error('isolated server exited')
    try { if((await fetch(url)).ok) return } catch {}
    await new Promise(r => setTimeout(r,150))
  }
  throw Error('isolated server not ready')
}
async function stop(processHandle, tree=false) {
  if (!processHandle || processHandle.exitCode !== null) return
  if (tree && process.platform === 'win32') {
    await new Promise(resolveStop => { const killer=spawn('taskkill.exe',['/PID',String(processHandle.pid),'/T','/F'],{stdio:'ignore',windowsHide:true}); killer.once('exit',resolveStop); killer.once('error',resolveStop) })
  } else processHandle.kill()
  await new Promise(resolveStop => { processHandle.once('exit',resolveStop); setTimeout(resolveStop,3000) })
}

test('real FastAPI, SQLite checkpoint, Vite and browser without MySQL writes', {skip:process.env.PAPERAGENT_ISOLATED_E2E !== '1', timeout:60000}, async () => {
  const root=realpathSync(tmpdir())
  const temp=mkdtempSync(join(root,'paperagent-m206-'))
  assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
  let server, vite, browser
  try {
    const backendPort=await port(), frontendPort=await port()
    assert.notEqual(backendPort,frontendPort)
    const env={...process.env, LANGSMITH_TRACING:'false', LANGCHAIN_TRACING_V2:'false'}
    server=spawn(python,['-c',child,join(temp,'business.sqlite'),join(temp,'checkpoints.sqlite'),String(backendPort)],{cwd:backend,env,stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${backendPort}/health`,server)
    vite=spawn(process.execPath,[join(frontend,'node_modules','vite','bin','vite.js'),'--host','127.0.0.1','--port',String(frontendPort),'--strictPort'],{cwd:frontend,env:{...env,VITE_API_PROXY_TARGET:`http://127.0.0.1:${backendPort}`},stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${frontendPort}/projects`,vite)
    const entry=process.env.PAPERAGENT_PLAYWRIGHT_ENTRY
    const {chromium}=await import(entry ? pathToFileURL(entry).href : 'playwright')
    browser=await chromium.launch({executablePath:process.env.PAPERAGENT_CHROME_PATH || undefined,headless:true})
    const page=await browser.newPage({viewport:{width:1366,height:768}})
    let starts=0
    page.on('response',response => { if(response.request().method()==='POST' && response.url().endsWith('/runs')) { starts++; assert.equal(response.status(),202) } })
    await page.goto(`http://127.0.0.1:${frontendPort}/projects/${projectId}`)
    await page.getByRole('heading',{name:'隔离工作流验收'}).waitFor()
    await page.getByRole('button',{name:'启动工作流'}).click()
    await page.waitForURL('**/runs/**')
    await page.getByText('等待任务审核').waitFor()
    await page.getByRole('heading',{name:'任务审核'}).waitFor()
    await page.getByText('游标 3').waitFor()
    if (process.env.PAPERAGENT_PREVIEW_DIR) {
      await page.screenshot({path:join(process.env.PAPERAGENT_PREVIEW_DIR,'paperagent-m206-desktop.png'),fullPage:true})
      await page.setViewportSize({width:390,height:844})
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),true)
      await page.screenshot({path:join(process.env.PAPERAGENT_PREVIEW_DIR,'paperagent-m206-mobile.png'),fullPage:true})
      await page.setViewportSize({width:1366,height:768})
    }
    const runUrl=page.url()
    await page.reload()
    await page.getByText('等待任务审核').waitFor()
    assert.equal(page.url(),runUrl)
    assert.equal(starts,1)
    assert.equal(await page.getByText('等待任务审核').count(),1)
    await page.close()
  } finally {
    if(browser) await browser.close()
    await stop(vite); await stop(server,true)
    assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
    rmSync(temp,{recursive:true,force:true})
  }
})
