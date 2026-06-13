"""Python HTTP 服务 — 为 Chrome 扩展提供文章 payload"""

import json
import os
import socket
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, parse_qs

from markdown_parser import build_payload

XPAGE_JS_PATH = Path(__file__).parent / "xpage.js"


def _check_port(port: int) -> bool:
    """检查端口是否被占用"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("localhost", port)) == 0


class ArticleHandler(BaseHTTPRequestHandler):
    """HTTP 请求处理器"""

    payload: dict = {}
    xpage_js: str = ""

    def log_message(self, format, *args):
        pass  # 静默日志

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
        self.send_header("Pragma", "no-cache")

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/status":
            self._handle_status()
        elif path == "/payload":
            self._handle_payload()
        elif path == "/engine":
            self._handle_engine()
        elif path == "/inject-script":
            self._handle_inject_script()
        else:
            self._handle_dashboard()

    def _handle_status(self):
        preview = (self.payload.get("plain") or "")[:200].replace("\n", " ")
        data = {
            "ready": True,
            "title": self.payload.get("title", ""),
            "textBlocks": len([b for b in self.payload.get("blocks", []) if b.get("type") in ("text", "unstyled")]),
            "imageCount": len(self.payload.get("images", [])),
            "preview": preview,
            "port": self.server.server_address[1],
        }
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_payload(self):
        body = json.dumps(self.payload, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_engine(self):
        body = self.xpage_js.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_inject_script(self):
        payload_json = json.dumps(self.payload, ensure_ascii=False)
        wrapper = r"""
console.log("[GrokX] inject-script loaded");
(async function __grokxMain(){"use strict";
""" + self.xpage_js + r"""
;
const payload=""" + payload_json + r""";
console.log("[GrokX] Payload:",payload.title,"| images:",payload.images.length);
for(let i=0;i<payload.images.length;i++){console.log("[GrokX] Image "+i+":",payload.images[i].fileName,"coverOnly="+payload.images[i].coverOnly,"b64len="+payload.images[i].base64.length);}
console.log("[GrokX] Calling __xArticleWrite...");
const result=await window.__xArticleWrite(payload);
console.log("[GrokX] Result:",JSON.stringify(result,null,2));
if(result.ok && result.summary){
  console.log("[GrokX] Some images failed, attempting fallback upload...");
  const sel='[data-contents="true"] [contenteditable="true"],[contenteditable="true"][role="textbox"]';
  const editor=document.querySelector(sel);
  if(editor){
    const fiberKey=Object.keys(editor).find(k=>k.startsWith("__reactFiber$"));
    if(fiberKey){
      let fiber=editor[fiberKey];
      for(let d=0;d<80&&fiber;d++){
        if(fiber.stateNode?.props?.editorState){
          const draftNode=fiber.stateNode;
          const cs=draftNode.props.editorState.getCurrentContent();
          let atomicCount=0;
          cs.getBlockMap().forEach(b=>{if(b.getType()==="atomic")atomicCount++;});
          const bodyImgCount=payload.images.filter(x=>!x.coverOnly).length;
          console.log("[GrokX] Editor atomic blocks:",atomicCount,"expected body images:",bodyImgCount);
          if(atomicCount<bodyImgCount){
            console.log("[GrokX] No atomic blocks found, uploading body images via fallback...");
            for(const img of payload.images){
              if(img.coverOnly)continue;
              try{
                const bin=atob(img.base64);
                const bytes=new Uint8Array(bin.length);
                for(let j=0;j<bin.length;j++)bytes[j]=bin.charCodeAt(j);
                const file=new File([bytes],img.fileName,{type:img.mime});
                editor.focus();
                document.execCommand("selectAll",false);
                document.execCommand("moveToEndOfDocument",false);
                const fiberKey2=Object.keys(editor).find(k=>k.startsWith("__reactFiber$"));
                let f2=editor[fiberKey2];
                let onFilesAdded=null;
                for(let dd=0;dd<160&&f2;dd++){
                  const props=f2.memoizedProps||f2.stateNode?.props;
                  if(typeof props?.onFilesAdded==="function"){onFilesAdded=props.onFilesAdded;break;}
                  let child=f2.child;
                  for(let cd=0;cd<8&&child;cd++){
                    const cp=child.memoizedProps||child.stateNode?.props;
                    if(typeof cp?.onFilesAdded==="function"){onFilesAdded=cp.onFilesAdded;break;}
                    child=child.child;
                  }
                  if(onFilesAdded)break;
                  f2=f2.return;
                }
                if(onFilesAdded){
                  console.log("[GrokX] Fallback uploading:",img.fileName);
                  onFilesAdded([file]);
                  await new Promise(r=>setTimeout(r,3000));
                  console.log("[GrokX] Fallback upload done:",img.fileName);
                }
              }catch(e){console.error("[GrokX] Fallback upload error:",e);}
            }
          }
          break;
        }
        fiber=fiber.return;
      }
    }
  }
}
const el=document.createElement("meta");
el.setAttribute("data-hermes-result",JSON.stringify(result));
document.head.appendChild(el);
return result;})();
"""
        script = wrapper
        body = script.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _handle_dashboard(self):
        title = self.payload.get("title", "(untitled)")
        text_count = len([b for b in self.payload.get("blocks", []) if b.get("type") in ("text", "unstyled")])
        img_count = len(self.payload.get("images", []))
        port = self.server.server_address[1]
        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>X Article Publisher</title>
<style>
*{{box-sizing:border-box}}body{{font-family:-apple-system,sans-serif;max-width:760px;margin:20px auto;padding:16px;background:#15202b;color:#e1e8ed}}
h1{{color:#1d9bf0;font-size:20px}}.card{{background:#1e2732;border-radius:12px;padding:16px 20px;margin:12px 0}}
.card h2{{color:#1d9bf0;font-size:16px;margin:0 0 8px}}.card p{{color:#e1e8ed;font-size:14px;margin:6px 0}}
a{{color:#1d9bf0}}.steps .step{{margin:10px 0;padding-left:12px;border-left:3px solid #1d9bf0;font-size:14px}}
kbd{{background:#38444d;padding:2px 6px;border-radius:4px;font-size:12px}}
</style></head><body>
<h1>Grok X Article Publisher</h1>
<div class="card"><h2>{title}</h2>
<p>text blocks: {text_count} | images: {img_count}</p></div>
<div class="card"><h2>使用步骤</h2>
<div class="steps">
<div class="step"><strong>1.</strong> <a href="https://x.com/compose/articles/new" target="_blank">打开 x.com/compose/articles/new</a></div>
<div class="step"><strong>2.</strong> 点击「写文章」进入编辑器</div>
<div class="step"><strong>3.</strong> 点击右上角「📥 载入文章」按钮</div>
<div class="step"><strong>4.</strong> 检查内容后点 Publish</div>
</div></div>
<p style="color:#71767b;font-size:12px">Port {port} | {title}</p>
</body></html>"""
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self._cors()
        self.end_headers()
        self.wfile.write(body)


class ArticleServer:
    """文章 HTTP 服务"""

    def __init__(self, md_path: str, port: int = 8765):
        self.md_path = md_path
        self.port = port
        self.payload: Optional[dict] = None
        self.server: Optional[HTTPServer] = None
        self.thread: Optional[threading.Thread] = None

    def start(self):
        """解析 MD → 构建 payload → 启动 HTTP 服务"""
        self.payload = build_payload(self.md_path)

        # 加载 xpage.js
        xpage_js = ""
        if XPAGE_JS_PATH.exists():
            xpage_js = XPAGE_JS_PATH.read_text(encoding="utf-8")

        # 设置 handler 类属性
        ArticleHandler.payload = self.payload
        ArticleHandler.xpage_js = xpage_js

        self.server = HTTPServer(("0.0.0.0", self.port), ArticleHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server = None


# ── 模块级便捷函数 ──────────────────────────────────────

_article_server: Optional[ArticleServer] = None


def start_article_server(md_path: str, port: int = 8765) -> ArticleServer:
    """启动文章服务。会先停掉旧服务。"""
    global _article_server
    if _article_server:
        _article_server.stop()
    if _check_port(port):
        raise RuntimeError(f"端口 {port} 已被占用，请先关闭占用该端口的进程")
    _article_server = ArticleServer(md_path, port)
    _article_server.start()
    return _article_server


def stop_article_server():
    global _article_server
    if _article_server:
        _article_server.stop()
        _article_server = None
