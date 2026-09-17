import { createServer } from 'node:http';
import { randomBytes } from 'node:crypto';

function page(nonce) {
  return String.raw`<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Buddy 本地任务</title>
<style nonce="${nonce}">:root{font:16px system-ui,sans-serif;color:#20332e;background:#f4f7f5}body{max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:28px}p{color:#52665e}button{display:block;text-align:left;width:100%;border:1px solid #cad7cf;border-radius:10px;background:white;color:inherit;padding:16px;margin:12px 0;cursor:pointer;font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}button:hover,button:focus{border-color:#277354}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:20px;border-radius:10px;line-height:1.6}#error{color:#9c302c}small{color:#52665e}</style>
<h1>Buddy 本地任务</h1><p>每 3 秒刷新任务状态。关闭页面后，任务继续运行。</p><small id="count"></small><p id="error" role="status"></p><main id="runs"></main><section><h2>任务结果</h2><pre id="detail">选择任务查看结果。</pre><details><summary>日志路径与完整记录</summary><pre id="raw-detail">选择任务后显示。</pre></details></section>
<script nonce="${nonce}">
const base=location.pathname.replace(/\/$/,'');
const byId=id=>document.getElementById(id);
const labels={running:'运行中',cancelling:'正在取消',completing:'正在收集结果',completed:'执行成功',cancelled:'已取消',failed:'执行失败',interrupted:'运行中断'};
let selected=null;
async function read(path){const response=await fetch(base+'/api'+path,{cache:'no-store',credentials:'omit'});if(!response.ok)throw new Error('读取失败（'+response.status+'）');return response.json();}
async function detail(id){const run=await read('/'+encodeURIComponent(id));if(selected!==id)return;const finalText=run.result?.finalText||run.result?.runner?.finalText;byId('detail').textContent=finalText||((labels[run.status]||run.status)+'。'+(run.acceptedAt?'结果已验收。':'结果尚未验收。'));byId('raw-detail').textContent=JSON.stringify(run,null,2);}
async function refresh(){try{const data=await read('');byId('count').textContent='共 '+data.total+' 个任务，显示最近 '+data.runs.length+' 个';const focusedId=document.activeElement?.dataset?.runId;const nodes=data.runs.map(run=>{const button=document.createElement('button');button.type='button';button.dataset.runId=run.runId;button.textContent=(labels[run.status]||run.status)+' · '+(run.acceptedAt?'已验收':'待验收')+'\n'+run.cwd+'\n'+run.createdAt+' · '+run.runId;button.onclick=()=>{selected=run.runId;detail(selected).catch(showError);};return button;});if(!nodes.length){const empty=document.createElement('p');empty.textContent='还没有委派任务';nodes.push(empty);}byId('runs').replaceChildren(...nodes);nodes.find(node=>focusedId&&node.dataset.runId===focusedId)?.focus({preventScroll:true});if(selected)await detail(selected);byId('error').textContent='';}catch(error){showError(error);}}
function showError(error){byId('error').textContent=error.message;}
async function poll(){await refresh();setTimeout(poll,3000);}poll();
</script></html>`;
}

/** Read-only loopback dashboard. Closing it never changes job ownership or lifetime. */
export async function startDashboard(manager) {
  const token = randomBytes(32).toString('hex');
  const nonce = randomBytes(24).toString('base64');
  let origin;
  const server = createServer(async (request, response) => {
    response.setHeader('Cache-Control', 'no-store');
    response.setHeader('Referrer-Policy', 'no-referrer');
    response.setHeader('X-Content-Type-Options', 'nosniff');
    response.setHeader('Content-Security-Policy', `default-src 'none'; script-src 'nonce-${nonce}'; style-src 'nonce-${nonce}'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'`);
    const send = (status, body, type = 'application/json; charset=utf-8') => {
      response.writeHead(status, { 'Content-Type': type });
      response.end(type.startsWith('application/json') ? JSON.stringify(body) : body);
    };
    if (request.headers.host !== new URL(origin).host || (request.headers.origin && request.headers.origin !== origin)) return send(403, { error: 'Forbidden origin' });
    if (request.method !== 'GET') { response.setHeader('Allow', 'GET'); return send(405, { error: 'Read-only endpoint' }); }
    const path = request.url;
    if (path === `/${token}/`) return send(200, page(nonce), 'text/html; charset=utf-8');
    try {
      if (path === `/${token}/api`) return send(200, await manager.dispatch('list', { limit: 100, offset: 0 }));
      const prefix = `/${token}/api/`;
      const runId = path?.startsWith(prefix) ? path.slice(prefix.length) : '';
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(runId)) return send(404, { error: 'Not found' });
      const status = await manager.dispatch('status', { runId });
      return send(200, status.resultAvailable ? await manager.dispatch('result', { runId }) : status);
    } catch (error) {
      return send(error.code === 'NOT_FOUND' ? 404 : 500, { error: error.code === 'NOT_FOUND' ? 'Unknown run' : 'Unable to read task' });
    }
  });
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => { server.removeListener('error', reject); resolve(); });
  });
  origin = `http://127.0.0.1:${server.address().port}`;
  return {
    url: `${origin}/${token}/`,
    close: () => new Promise((resolve, reject) => {
      server.close(error => error ? reject(error) : resolve());
      server.closeIdleConnections();
    }),
  };
}
