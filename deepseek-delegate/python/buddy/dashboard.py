"""Private, read-only local dashboard for the Python board service.

The page is served on an ephemeral loopback port behind a private token, refuses a
foreign ``Host``/``Origin``, only answers GET, and renders every value as text.
Closing it never changes task ownership or lifetime.
"""
from __future__ import annotations

import json
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .store import BoardStore

MAX_PAGE_BYTES = 1024 * 1024


def page(nonce: str) -> str:
    return (
        "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Buddy 本地任务</title>\n"
        f"<style nonce=\"{nonce}\">:root{{font:16px system-ui,sans-serif;color:#20332e;background:#f4f7f5}}"
        "body{max-width:1000px;margin:40px auto;padding:0 24px}h1{font-size:28px}p{color:#52665e}"
        "button{display:block;text-align:left;width:100%;border:1px solid #cad7cf;border-radius:10px;"
        "background:white;color:inherit;padding:16px;margin:12px 0;cursor:pointer;font:inherit;"
        "white-space:pre-wrap;overflow-wrap:anywhere}button:hover,button:focus{border-color:#277354}"
        "pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:20px;border-radius:10px;"
        "line-height:1.6}#error{color:#9c302c}small{color:#52665e}</style>\n"
        "<h1>Buddy 本地任务</h1><p>每 3 秒刷新任务状态。关闭页面后，任务继续运行。</p>"
        "<small id=\"count\"></small><p id=\"error\" role=\"status\"></p><main id=\"runs\"></main>"
        "<section><h2>任务结果</h2><pre id=\"detail\">选择任务查看结果。</pre>"
        "<details><summary>日志路径与完整记录</summary><pre id=\"raw-detail\">选择任务后显示。</pre></details></section>\n"
        f"<script nonce=\"{nonce}\">\n"
        "const base=location.pathname.replace(/\\/$/,'');\n"
        "const byId=id=>document.getElementById(id);\n"
        "const labels={queued:'排队中',running:'运行中',cancelling:'正在取消',completed:'执行成功',"
        "cancelled:'已取消',failed:'执行失败','reconciliation-needed':'需要人工核对'};\n"
        "let selected=null;\n"
        "async function read(path){const response=await fetch(base+'/api'+path,{cache:'no-store',credentials:'omit'});"
        "if(!response.ok)throw new Error('读取失败（'+response.status+'）');return response.json();}\n"
        "async function detail(id){const run=await read('/'+encodeURIComponent(id));if(selected!==id)return;"
        "const attempt=run.selectedAttempt||{};const result=attempt.result||{};"
        "const finalText=result.finalText||result.runner&&result.runner.finalText;"
        "byId('detail').textContent=finalText||((labels[run.status]||run.status)+'。'+"
        "(run.acceptedAt?'结果已验收。':'结果尚未验收。'));"
        "byId('raw-detail').textContent=JSON.stringify(run,null,2);}\n"
        "async function refresh(){try{const data=await read('');"
        "byId('count').textContent='共 '+data.total+' 个任务，显示最近 '+data.runs.length+' 个';"
        "const focusedId=document.activeElement&&document.activeElement.dataset?document.activeElement.dataset.runId:null;"
        "const nodes=data.runs.map(run=>{const button=document.createElement('button');button.type='button';"
        "button.dataset.runId=run.runId;button.textContent=(labels[run.status]||run.status)+' · '+"
        "(run.acceptedAt?'已验收':'待验收')+'\\n'+run.cwd+'\\n'+run.createdAt+' · '+run.runId;"
        "button.onclick=()=>{selected=run.runId;detail(selected).catch(showError);};return button;});"
        "if(!nodes.length){const empty=document.createElement('p');empty.textContent='还没有委派任务';nodes.push(empty);}"
        "byId('runs').replaceChildren(...nodes);"
        "nodes.find(node=>focusedId&&node.dataset.runId===focusedId)&&"
        "nodes.find(node=>focusedId&&node.dataset.runId===focusedId).focus({preventScroll:true});"
        "if(selected)await detail(selected);byId('error').textContent='';}catch(error){showError(error);}}\n"
        "function showError(error){byId('error').textContent=error.message;}\n"
        "async function poll(){await refresh();setTimeout(poll,3000);}poll();\n"
        "</script></html>"
    )


class Dashboard:
    """One loopback dashboard instance owned by the service."""

    def __init__(self, store: BoardStore, *, list_tasks: Callable[[dict], dict] | None = None):
        self.store = store
        self.token = secrets.token_hex(32)
        self.nonce = secrets.token_urlsafe(24)
        self._list_tasks = list_tasks or (lambda params: store.task_list(params))
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.url: str | None = None
        self.origin: str | None = None

    def start(self) -> dict:
        if self._server is not None:
            return {"url": self.url, "readOnly": True, "alreadyRunning": True}
        handler = self._handler()
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        server.daemon_threads = True
        self._server = server
        self.origin = f"http://127.0.0.1:{server.server_address[1]}"
        self.url = f"{self.origin}/{self.token}/"
        self._thread = threading.Thread(target=server.serve_forever, name="buddy-dashboard", daemon=True)
        self._thread.start()
        return {"url": self.url, "readOnly": True, "alreadyRunning": False}

    def close(self) -> dict:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        return {"closed": True}

    def status(self) -> dict:
        return {"url": self.url, "readOnly": True, "running": self._server is not None}

    def _handler(self):
        dashboard = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"
            server_version = "buddy-dashboard"

            def log_message(self, *_args):  # noqa: D102 - silence access logging
                return

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header(
                    "Content-Security-Policy",
                    f"default-src 'none'; script-src 'nonce-{dashboard.nonce}'; style-src 'nonce-{dashboard.nonce}'; "
                    "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
                )
                self.end_headers()
                self.wfile.write(body)

            def _json(self, status: int, value: dict) -> None:
                self._send(status, json.dumps(value, ensure_ascii=False).encode(), "application/json; charset=utf-8")

            def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler API
                host = self.headers.get("Host")
                origin = self.headers.get("Origin")
                if host != f"127.0.0.1:{dashboard._server.server_address[1]}" or (
                    origin is not None and origin != dashboard.origin
                ):
                    return self._json(403, {"error": "Forbidden origin"})
                path = self.path
                if path == f"/{dashboard.token}/":
                    body = page(dashboard.nonce).encode()
                    return self._send(200, body, "text/html; charset=utf-8")
                if path == f"/{dashboard.token}/api":
                    return self._json(200, dashboard._list_tasks({"limit": 100, "offset": 0}))
                prefix = f"/{dashboard.token}/api/"
                task_id = path[len(prefix):] if path.startswith(prefix) else ""
                if not _looks_like_id(task_id):
                    return self._json(404, {"error": "Not found"})
                try:
                    view = dashboard.store.task_get({"runId": task_id})["task"]
                    if view.get("resultAvailable"):
                        view = {**view, **dashboard.store.task_result({"runId": task_id})}
                except Exception as error:  # noqa: BLE001 - the dashboard answers with a status
                    code = getattr(error, "code", None)
                    return self._json(404 if code == "NOT_FOUND" else 500, {"error": "Unknown task" if code == "NOT_FOUND" else "Unable to read task"})
                return self._json(200, view)

            def do_POST(self):  # noqa: N802 - BaseHTTPRequestHandler API
                self.send_header("Allow", "GET") if False else None
                self._json(405, {"error": "Read-only endpoint"})

        return Handler


def _looks_like_id(value: str) -> bool:
    import re

    return bool(re.match(r"^[A-Za-z0-9._:-]{1,128}$", value))
