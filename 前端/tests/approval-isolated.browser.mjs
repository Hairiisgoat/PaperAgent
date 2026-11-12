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
from types import SimpleNamespace
import json
import app.api.workflow_events as workflow_events
import app.services.planner as planner
import app.services.research as research
from app.infrastructure.glm_client import LLMResult
from app.infrastructure.crossref import ScholarlyRecord
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
workflow_events.load_llm_settings = lambda: SimpleNamespace(model='mock-test')
class FakeLLM:
    model = 'mock-test'
    planner_read_timeout_seconds = 120
    def invoke(self, messages, *, max_tokens, call_id, read_timeout_seconds, reasoning_effort=None):
        if messages[0].content.startswith('Create 3 to 8'):
            body={'tasks':[{'title':f'测试任务{i}', 'description':f'任务描述{i}', 'expected_output':'候选来源'} for i in range(3)]}
        elif messages[0].content.startswith('Create 2 to 4'):
            body={'sections':[{'title':'研究背景','objective':'介绍背景'},{'title':'研究方法','objective':'介绍方法'}]}
        elif messages[0].content.startswith('你是严格的学术论文审查员'):
            data=json.loads(messages[1].content)
            body={'summary':'请人工核验证据','issues':[{'issue_type':'evidence','severity':'high','chapter_id':data['chapter_id'], 'version_id':data['version_id'],'paragraph_index':1,'description':'测试证据不足','rationale':'摘要未支持该论述','suggestion':'缩小论断','source_ids':[data['paragraphs'][0]['citations'][0]['source_id']]}]}
        else:
            source_id=json.loads(messages[1].content)['sources'][0]['source_id']
            body={'paragraphs':[{'text':'这项研究提出了可供核验的论述。','citations':[{'source_id':source_id,'evidence':'这是用于核验引用的原始摘要文字'}]}]}
        return LLMResult(text=json.dumps(body), usage={'prompt_tokens':1,'completion_tokens':2}, call_id=call_id)
planner.LLMService.from_settings = lambda: FakeLLM()
research.search_crossref = lambda query, limit: [ScholarlyRecord(doi='10.1234/test',title='资料标题',authors=('作者',),year=2024,journal='期刊',url='https://doi.org/10.1234/test',abstract='这是用于核验引用的原始摘要文字，属于模拟测试。')]
research.title_matches_query = lambda query, title: True
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


test('isolated real browser approval workflow without MySQL', {skip:process.env.PAPERAGENT_ISOLATED_E2E !== '1', timeout:90000}, async () => {
  const root=realpathSync(tmpdir()), temp=mkdtempSync(join(root,'paperagent-approval-'))
  assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
  let server,vite,chrome,ws
  try {
    const backendPort=await port(), frontendPort=await port(), chromePort=await port()
    const env={...process.env,LANGSMITH_TRACING:'false',LANGCHAIN_TRACING_V2:'false'}
    server=spawn(python,['-c',child,join(temp,'business.sqlite'),join(temp,'checkpoints.sqlite'),String(backendPort)],{cwd:backend,env,stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${backendPort}/health`,server)
    vite=spawn(process.execPath,[join(frontend,'node_modules','vite','bin','vite.js'),'--host','127.0.0.1','--port',String(frontendPort),'--strictPort'],{cwd:frontend,env:{...env,VITE_API_PROXY_TARGET:`http://127.0.0.1:${backendPort}`},stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${frontendPort}/projects`,vite)
    chrome=spawn(process.env.PAPERAGENT_CHROME_PATH || 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe', ['--headless=new',`--remote-debugging-port=${chromePort}`,`--user-data-dir=${join(temp,'chrome')}`,'--no-first-run','--no-default-browser-check','about:blank'],{stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${chromePort}/json/version`,chrome)
    const targets=await (await fetch(`http://127.0.0.1:${chromePort}/json`)).json()
    ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
    await new Promise((resolveOpen,reject)=>{ws.onopen=resolveOpen;ws.onerror=reject})
    let serial=0;const pending=new Map()
    ws.onmessage=({data})=>{const msg=JSON.parse(data);if(msg.id && pending.has(msg.id)){const [ok,fail]=pending.get(msg.id);pending.delete(msg.id);msg.error?fail(Error(msg.error.message)):ok(msg.result)}}
    const call=(method,params={})=>new Promise((ok,fail)=>{const id=++serial;pending.set(id,[ok,fail]);ws.send(JSON.stringify({id,method,params}))})
    const evaluate=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
    const until=async expression=>{const end=Date.now()+15000;while(Date.now()<end){if(await evaluate(expression))return;await new Promise(r=>setTimeout(r,100))}throw Error('condition timed out: '+expression+'; '+await evaluate('document.body?.innerText'))}
    const clickText=async text=>{const expr=`[...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)} && !x.disabled)?.click()`;await until(`!![...document.querySelectorAll('button')].find(x=>x.textContent.trim()===${JSON.stringify(text)} && !x.disabled)`);await evaluate(expr)}
    await call('Page.enable');await call('Runtime.enable')
    await call('Emulation.setDeviceMetricsOverride',{width:1366,height:768,deviceScaleFactor:1,mobile:false})
    await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}`})
    await until("document.body.innerText.includes('隔离工作流验收')")
    await clickText('启动模拟工作流')
    await until("document.body.innerText.includes('任务审核') && document.querySelectorAll('input[type=checkbox]').length===2")
    const first=await evaluate('location.pathname')
    await evaluate("document.querySelector('input[type=checkbox]').click()")
    await clickText('批准所选')
    await until(`location.pathname!==${JSON.stringify(first)} && document.body.innerText.includes('资料审核') && document.querySelectorAll('input[type=checkbox]').length===1`)
    const second=await evaluate('location.pathname')
    await call('Page.reload')
    await until("document.body.innerText.includes('资料审核') && document.querySelectorAll('input[type=checkbox]').length===1")
    assert.equal(await evaluate('location.pathname'),second)
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false})
    assert.equal(await evaluate('document.documentElement.scrollWidth <= innerWidth'),true)
    await clickText('全选')
    await clickText('批准所选')
    await until(`location.pathname!==${JSON.stringify(second)} && document.body.innerText.includes('已完成')`)
    assert.notEqual(await evaluate('location.pathname'),second)
    await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}`})
    await until("document.body.innerText.includes('隔离工作流验收')")
    await clickText('启动模拟工作流')
    await until("document.body.innerText.includes('任务审核') && document.querySelectorAll('input[type=checkbox]').length===2")
    const rejectedFrom = await evaluate('location.pathname')
    await clickText('拒绝全部')
    await until("document.body.innerText.includes('确定拒绝全部审核对象')")
    await clickText('确认拒绝')
    await until(`location.pathname!==${JSON.stringify(rejectedFrom)} && document.body.innerText.includes('已取消')`)
    await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}`})
    await until("document.body.innerText.includes('隔离工作流验收')")
    await clickText('启动真实 Planner')
    await clickText('确认启动')
    await until("document.body.innerText.includes('测试任务0') && document.querySelectorAll('input[type=checkbox]').length===3")
    await evaluate("document.querySelector('input[type=checkbox]').click()")
    await clickText('批准并启动资料检索')
    await until("document.body.innerText.includes('资料标题') && document.body.innerText.includes('任务0')")
    assert.equal(await evaluate("document.body.innerText.includes('作者') && document.body.innerText.includes('期刊') && document.body.innerText.includes('摘要')"),true)
    await clickText('全选')
    assert.equal(await evaluate("document.querySelector('input[type=checkbox]').checked"),true)
    await clickText('批准并生成初稿')
    await until("document.body.innerText.includes('论文初稿') && document.body.innerText.includes('研究背景') && document.body.innerText.includes('原文摘录')")
    assert.equal(await evaluate("document.body.innerText.includes('这项研究提出了可供核验的论述') && document.body.innerText.includes('资料标题')"),true)
    await until("document.body.innerText.includes('已完成') && document.body.innerText.includes('修改与历史版本')")
    await until("document.body.innerText.includes('测试证据不足') && document.body.innerText.includes('定位正文')")
    await clickText('定位正文')
    assert.equal(await evaluate("!!document.querySelector('[id^=paragraph-]')"),true)
    await clickText('修改与历史版本')
    await until("document.body.innerText.includes('第 1 版') && !!document.querySelector('article[id^=chapter-] textarea') && !!document.querySelector('article[id^=chapter-]')?.querySelector('textarea')")
    await evaluate("(()=>{const t=document.querySelector('article[id^=chapter-] textarea');t.value='扩充论述并核对证据';t.dispatchEvent(new Event('input',{bubbles:true}))})()")
    await clickText('提交章节修改')
    await until("document.body.innerText.includes('章节修改完成') && document.body.innerText.includes('第 2 版')")
    await evaluate("(()=>{const s=[...document.querySelectorAll('select')].find(e=>[...e.options].some(o=>o.textContent.includes('第 1 版')));s.value=[...s.options].find(o=>o.textContent.includes('第 1 版')).value;s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    await clickText('恢复所选版本')
    await until("document.body.innerText.includes('历史版本已恢复') && document.body.innerText.includes('第 3 版')")
    await until("document.body.innerText.includes('本报告的全文上下文属于历史快照')")
    await evaluate("(()=>{const label=[...document.querySelectorAll('label')].find(x=>x.textContent.includes('审查范围'));const s=label.querySelector('select');s.value=[...s.options].find(o=>o.textContent==='研究背景').value;s.dispatchEvent(new Event('change',{bubbles:true}))})()")
    await clickText('开始审查或重试相同请求')
    await until("[...document.querySelectorAll('label')].find(x=>x.textContent.includes('报告'))?.querySelector('select')?.options.length===2")
    await until("document.body.innerText.includes('审查状态：completed')")
  } finally {
    if(ws)ws.close()
    await stop(chrome,true);await stop(vite);await stop(server,true)
    assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase())
    rmSync(temp,{recursive:true,force:true})
  }
})

test('browser mock approval validation, uncertain retry, rejection and errors', {timeout:90000}, async () => {
  const {createServer: httpServer}=await import('node:http')
  const root=realpathSync(tmpdir()),temp=mkdtempSync(join(root,'paperagent-mock-'))
  const run='9c6c0d89-e05d-4a6f-a343-1b2a124d6faa', next='6c939b3e-f050-4caf-a3e4-530dc517bf59', thread='45607c29-0e5f-43d5-bc04-68066a38fb19'
  const ids=['6713e124-5e13-4dea-9473-f52c624cab38','6713e124-5e13-4dea-9473-f52c624cab39']
  let vite,chrome,ws,mock,mode='network',posts=[],streams=[]
  try {
    const backendPort=await port(), frontendPort=await port(), chromePort=await port()
    mock=httpServer((req,res)=>{
      const path=new URL(req.url,'http://localhost').pathname
      const json=(code,value)=>{res.writeHead(code,{'content-type':'application/json'});res.end(JSON.stringify(value))}
      if(path.endsWith('/events')){streams.push({path,cursor:req.headers['last-event-id']});res.writeHead(200,{'content-type':'text/event-stream'});res.end('');return}
      if(path.endsWith('/approval') && req.method==='GET') {
        if(mode==='missing')return json(404,{detail:'private secret'})
        if(mode==='unavailable')return json(503,{detail:'private secret'})
        if(mode==='sources')return json(200,{project_id:projectId,run_id:run,thread_id:thread,approval_type:'source_approval',checkpoint_id:ids[0],current_stage:'research',item_ids:ids,sources:[{source_id:ids[0],title:'真实资料测试标题',authors:['研究者'],year:2024,journal:'测试期刊',doi:'10.1234/example',url:'https://doi.org/10.1234/example',abstract:'测试摘要',task_ids:[ids[1]],tasks:[{task_id:ids[1],title:'关联任务名称'}],relevance_hint:'some',same_title_count:2},{source_id:ids[1],title:'真实资料测试标题',authors:['研究者'],year:2025,journal:'测试期刊',doi:'10.1234/other',url:'https://doi.org/10.1234/other',task_ids:[ids[1]],tasks:[{task_id:ids[1],title:'关联任务名称'}],relevance_hint:'more',same_title_count:2}]})
        return json(200,{project_id:projectId,run_id:run,thread_id:thread,approval_type:'task_approval',checkpoint_id:ids[0],current_stage:'planning',item_ids:mode==='empty'?[]:ids})
      }
      if(path.endsWith('/approval') && req.method==='POST') {
        let body='';req.on('data',c=>body+=c);req.on('end',()=>{posts.push(JSON.parse(body));if(mode==='network'){req.socket.destroy();return}if(mode==='conflict')return json(409,{detail:'private secret'});if(mode==='validation')return json(422,{detail:'private secret'});if(mode==='post503')return json(503,{detail:'private secret'});if(mode==='missingwriting')return json(503,{detail:'safe',code:'writing_storage_missing'});return json(202,{project_id:projectId,previous_run_id:run,run_id:next,thread_id:thread,attempt_no:2,status:'queued'})});return
      }
      if(path.includes('/workflow-runs/')) {
        if(mode==='run404')return json(404,{detail:'private secret'})
        return json(200,{project_id:projectId,run_id:path.endsWith(next)?next:run,thread_id:thread,attempt_no:1,status:'waiting_human',current_stage:'planning',started_at:null,finished_at:null,created_at:'2026-01-01T00:00:00Z',updated_at:'2026-01-01T00:00:00Z',error_code:null,retryable:null})
      }
      return json(404,{})
    });await new Promise(r=>mock.listen(backendPort,'127.0.0.1',r))
    vite=spawn(process.execPath,[join(frontend,'node_modules','vite','bin','vite.js'),'--host','127.0.0.1','--port',String(frontendPort),'--strictPort'],{cwd:frontend,env:{...process.env,VITE_API_PROXY_TARGET:`http://127.0.0.1:${backendPort}`},stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${frontendPort}/projects`,vite)
    chrome=spawn('C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',['--headless=new',`--remote-debugging-port=${chromePort}`,`--user-data-dir=${join(temp,'chrome')}`,'--no-first-run','about:blank'],{stdio:'ignore',windowsHide:true})
    await ready(`http://127.0.0.1:${chromePort}/json/version`,chrome)
    const targets=await (await fetch(`http://127.0.0.1:${chromePort}/json`)).json();ws=new WebSocket(targets.find(t=>t.type==='page').webSocketDebuggerUrl)
    await new Promise((ok,fail)=>{ws.onopen=ok;ws.onerror=fail})
    let serial=0;const pending=new Map();ws.onmessage=({data})=>{const m=JSON.parse(data);if(m.id && pending.has(m.id)){const [ok,fail]=pending.get(m.id);pending.delete(m.id);m.error?fail(Error(m.error.message)):ok(m.result)}}
    const call=(method,params={})=>new Promise((ok,fail)=>{const id=++serial;pending.set(id,[ok,fail]);ws.send(JSON.stringify({id,method,params}))})
    const ev=async expression=>{const r=await call('Runtime.evaluate',{expression,returnByValue:true,awaitPromise:true});if(r.exceptionDetails)throw Error(JSON.stringify(r.exceptionDetails));return r.result.value}
    const until=async expr=>{const end=Date.now()+10000;while(Date.now()<end){if(await ev(expr))return;await new Promise(r=>setTimeout(r,80))}throw Error('timed out '+expr)}
    const click=async label=>{await until(`!![...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(label)} && !e.disabled)`);await ev(`[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(label)} && !e.disabled).click()`)}
    const navigate=async()=>{await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}/runs/${run}`});await until("document.body.innerText.includes('任务审核')")}
    await call('Page.enable');await call('Runtime.enable');await navigate()
    await until("document.querySelectorAll('input[type=checkbox]').length===2")
    const cached={event_id:ids[1],sequence:1,run_id:run,project_id:projectId,event_type:'progress',stage:'planning',agent_name:null,status:'waiting_human',timestamp:'2026-01-01T00:00:00Z',payload:{stage:'planning',agent_name:null,status:'waiting_human',message:'Waiting for approval'}}
    await ev(`sessionStorage.setItem(${JSON.stringify('paperagent.events.'+projectId+'.'+run)},${JSON.stringify(JSON.stringify([cached]))})`)
    assert.equal(await ev("[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='批准所选').disabled"),true)
    await ev("document.querySelector('input[type=checkbox]').click()")
    await ev("(()=>{const b=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()==='批准所选');b.click();b.click()})()")
    await until("document.body.innerText.includes('审核结果尚未确认')")
    assert.deepEqual(posts,[{decision:'approve',approved_ids:[ids[0]]}])
    assert.equal(await ev("document.querySelector('input[type=checkbox]').disabled"),true)
    await call('Page.reload');await until("document.body.innerText.includes('重试相同决定')")
    mode='success';await click('重试相同决定');await until(`location.pathname.endsWith(${JSON.stringify(next)})`)
    await until('document.body.innerText.includes("游标")')
    const waitStream=Date.now()+5000;while(!streams.some(x=>x.path.includes(next)) && Date.now()<waitStream) await new Promise(r=>setTimeout(r,80))
    assert.ok(streams.some(x=>x.path.includes(next) && !x.cursor))
    assert.deepEqual(posts[1],posts[0])
    await ev('sessionStorage.clear()');mode='conflict';await navigate();await click('全选');await click('批准所选');await until("document.body.innerText.includes('审核状态或决定发生冲突')")
    assert.deepEqual(posts.at(-1),{decision:'approve',approved_ids:ids})
    await ev('sessionStorage.clear()');mode='success';await navigate();await click('拒绝全部');await until("document.body.innerText.includes('确定拒绝全部审核对象')");await click('确认拒绝');await until(`location.pathname.endsWith(${JSON.stringify(next)})`)
    assert.deepEqual(posts.at(-1),{decision:'reject'})
    for(const [scenario,message] of [['validation','审核请求不符合要求'],['post503','审核结果尚未确认'],['missingwriting','写作数据库尚未部署']]) {await ev('sessionStorage.clear()');mode=scenario;await navigate();await click('全选');await click('批准所选');await until(`document.body.innerText.includes(${JSON.stringify(message)})`);assert.equal(await ev("document.body.innerText.includes('private secret')"),false);if(scenario==='missingwriting')assert.equal(await ev("document.querySelector('input[type=checkbox]').disabled"),true)}
    await ev('sessionStorage.clear()');mode='success';await navigate();await click('全选')
    await ev("(async()=>{let m=await import('/src/services/http.ts');m.http.defaults.adapter=async()=>{throw {isAxiosError:true,code:'ECONNABORTED'}}})()")
    await click('批准所选');await until("document.body.innerText.includes('审核结果尚未确认')");assert.equal(await ev("document.querySelector('input[type=checkbox]').disabled"),true)
    for(const [scenario,message] of [['missing','项目或运行不存在'],['unavailable','服务暂不可用'],['empty','当前没有待审核对象']]) {await ev('sessionStorage.clear()');mode=scenario;await navigate();await until(`document.body.innerText.includes(${JSON.stringify(message)})`);assert.equal(await ev("document.body.innerText.includes('private secret')"),false)}
    await ev('sessionStorage.clear()');mode='sources';await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}/runs/${run}`});await until("document.body.innerText.includes('真实资料测试标题')")
    assert.equal(await ev("document.body.innerText.includes('研究者') && document.body.innerText.includes('测试期刊') && document.body.innerText.includes('测试摘要') && document.body.innerText.includes('关联任务名称')"),true)
    assert.equal(await ev("document.body.innerText.includes('词面匹配较少') && document.body.innerText.includes('词面匹配较多') && document.body.innerText.includes('疑似同标题不同 DOI（2 条）')"),true)
    assert.equal(await ev("document.querySelectorAll('input[type=checkbox]').length"),2)
    assert.equal(await ev("document.querySelector('a[href=\"https://doi.org/10.1234/example\"]') !== null"),true)
    await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:false})
    assert.equal(await ev('document.documentElement.scrollWidth <= innerWidth'),true)
    await click('全选'); assert.equal(await ev("document.querySelector('input[type=checkbox]').checked"),true)
    await ev('sessionStorage.clear()');mode='run404';await call('Page.navigate',{url:`http://127.0.0.1:${frontendPort}/projects/${projectId}/runs/${run}`});await until("document.body.innerText.includes('项目或运行不存在')")
  } finally {if(ws)ws.close();await stop(chrome,true);await stop(vite);if(mock)await new Promise(r=>mock.close(r));assert.equal(dirname(realpathSync(temp)).toLowerCase(),root.toLowerCase());rmSync(temp,{recursive:true,force:true})}
})
