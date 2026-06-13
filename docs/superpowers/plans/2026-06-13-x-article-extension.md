# X 文章发布 - Chrome 扩展模式实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将「发长文章到X草稿箱」按钮从 claude-code-sdk 方案替换为 Python HTTP 服务 + Chrome 扩展方案

**Architecture:** Python HTTP 服务（端口 8765）解析 Markdown 生成 payload，Chrome 扩展从 localhost:8765 拉取 payload 并通过 xpage.js 注入 X Articles 编辑器

**Tech Stack:** Python http.server, PIL, Chrome Extension Manifest V3, xpage.js (Draft.js + React Fiber)

---

## Task 1: markdown_parser.py — Markdown 解析与 payload 生成

**Files:**
- Create: `markdown_parser.py`

- [ ] **Step 1: 创建 markdown_parser.py — 基础结构与 frontmatter 解析**

```python
"""Markdown → X Articles payload 解析器（Python 重写 shared.js）"""

import base64
import io
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image


# ── Frontmatter ──────────────────────────────────────────

def parse_frontmatter(markdown: str) -> Tuple[str, dict]:
    """解析 YAML frontmatter，返回 (body, meta_dict)"""
    text = markdown.replace("\r\n", "\n")
    m = re.match(r'^---\n([\s\S]*?)\n---\n*', text)
    if not m:
        return text.strip(), {}
    meta = {}
    for line in m.group(1).split("\n"):
        idx = line.find(":")
        if idx < 0:
            continue
        key = line[:idx].strip()
        val = line[idx + 1:].strip().strip("\"'")
        if key:
            meta[key] = val
    body = text[m.end():].strip()
    return body, meta


PLACEHOLDER_TITLES = {"待定", "暂定", "未定", "TBD", "tbd", "TBA", "tba", "WIP", "wip"}


def extract_title(meta: dict, segments: list, md_path: str) -> Optional[str]:
    """从 frontmatter / h1 / 文件名提取标题"""
    raw = meta.get("title") or meta.get("Title") or meta.get("标题") or ""
    raw = raw.strip()
    if raw and raw not in PLACEHOLDER_TITLES:
        return raw
    # 从 segments 找第一个 h1
    for i, seg in enumerate(segments):
        if seg.get("type") == "text" and seg.get("kind") == "header-one":
            title = seg.get("text", "").strip()
            if title:
                return title
    # 从文件名
    stem = Path(md_path).stem
    return stem if stem else None


def extract_cover(meta: dict, segments: list) -> Optional[str]:
    """从 frontmatter 或第一张图片提取封面路径"""
    raw = meta.get("cover") or meta.get("Cover") or meta.get("封面") or ""
    if raw:
        return re.sub(r'^!\[\[|\]\]$', '', raw).strip()
    for seg in segments:
        if seg.get("type") == "image" and seg.get("source"):
            return seg["source"]
    return None
```

- [ ] **Step 2: 添加 Markdown 块级解析**

```python
# ── Block-level parsing ──────────────────────────────────

HEADER_MAP = {
    1: "header-one", 2: "header-two", 3: "header-three",
    4: "header-four", 5: "header-five", 6: "header-six",
}


def parse_inline(source: str) -> dict:
    """解析内联样式（粗体/斜体/删除线/代码/链接），返回 Draft.js block"""
    result = {"type": "text", "kind": "unstyled", "text": "", "inlineStyleRanges": [], "links": []}
    cursor = 0

    def append_styled(text: str, styles: list):
        offset = len(result["text"])
        result["text"] += text
        for s in styles:
            result["inlineStyleRanges"].append({"offset": offset, "length": len(text), "style": s})

    while cursor < len(source):
        ch = source[cursor]

        # 链接 [text](url)
        if ch == "[":
            m = re.match(r'\[([^\]]+)\]\(([^)]+)\)', source[cursor:])
            if m:
                offset = len(result["text"])
                result["text"] += m.group(1)
                result["links"].append({"offset": offset, "length": len(m.group(1)), "url": m.group(2)})
                cursor += m.end()
                continue

        # 粗斜体 *** / 粗体 ** / 删除线 ~~
        for marker, styles in [("***", ["Bold", "Italic"]), ("**", ["Bold"]), ("~~", ["Strikethrough"])]:
            if source[cursor:cursor + len(marker)] == marker:
                end = source.find(marker, cursor + len(marker))
                if end > cursor:
                    append_styled(source[cursor + len(marker):end], styles)
                    cursor = end + len(marker)
                    break
        else:
            # 斜体 *text*（非 **）
            if ch in ("*",) and source[cursor:cursor + 2] != "**":
                end = source.find(ch, cursor + 1)
                if end > cursor and source[end:end + 2] != "**":
                    append_styled(source[cursor + 1:end], ["Italic"])
                    cursor = end + 1
                    continue
            # 行内代码 `code`
            if ch == "`":
                end = source.find("`", cursor + 1)
                if end > cursor:
                    append_styled(source[cursor + 1:end], ["Code"])
                    cursor = end + 1
                    continue
            # 普通字符
            result["text"] += ch
            cursor += 1

    return result


def parse_text_blocks(text: str) -> list:
    """将文本段落解析为 Draft.js blocks"""
    lines = text.split("\n")
    segments = []
    paragraph = []

    def flush():
        val = "\n".join(paragraph).strip()
        if val:
            segments.append(parse_inline(val))
        paragraph.clear()

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            flush()
            continue
        # 标题
        m = re.match(r'^(#{1,6})\s+(.+)$', trimmed)
        if m:
            flush()
            kind = HEADER_MAP[len(m.group(1))]
            seg = parse_inline(m.group(2).strip())
            seg["kind"] = kind
            segments.append(seg)
            continue
        # 引用
        m = re.match(r'^>\s+(.+)$', trimmed)
        if m:
            flush()
            seg = parse_inline(m.group(1).strip())
            seg["kind"] = "blockquote"
            segments.append(seg)
            continue
        # 无序列表
        m = re.match(r'^[-*+]\s+(.+)$', trimmed)
        if m:
            flush()
            seg = parse_inline(m.group(1).strip())
            seg["kind"] = "unordered-list-item"
            segments.append(seg)
            continue
        # 有序列表
        m = re.match(r'^\d+\.\s+(.+)$', trimmed)
        if m:
            flush()
            seg = parse_inline(m.group(1).strip())
            seg["kind"] = "ordered-list-item"
            segments.append(seg)
            continue
        paragraph.append(trimmed)

    flush()
    return segments
```

- [ ] **Step 3: 添加特殊块检测（代码块、分割线、图片）**

```python
# ── Special block detection ──────────────────────────────

def find_special_blocks(body: str) -> list:
    """查找代码块、分割线、图片等特殊块，返回按位置排序的 span 列表"""
    spans = []

    # 围栏代码块 ```...```
    for m in re.finditer(r'```([^\n`]*)\n([\s\S]*?)```', body):
        spans.append({
            "start": m.start(), "end": m.end(),
            "segment": {"type": "code", "language": m.group(1).strip(), "code": m.group(2).rstrip("\n")}
        })

    # 分割线 --- / *** / ___
    for m in re.finditer(r'^(?: {0,3})(?:-{3,}|\*{3,}|_{3,})(?:[ \t]*)$', body, re.MULTILINE):
        if any(m.start() >= s["start"] and m.start() < s["end"] for s in spans):
            continue
        spans.append({"start": m.start(), "end": m.end(), "segment": {"type": "divider"}})

    # Markdown 图片 ![alt](src)
    for m in re.finditer(r'!\[([^\]]*)\]\(([^)]+)\)', body):
        if any(m.start() >= s["start"] and m.start() < s["end"] for s in spans):
            continue
        spans.append({
            "start": m.start(), "end": m.end(),
            "segment": {"type": "image", "source": m.group(2).strip(), "alt": m.group(1).strip()}
        })

    # Obsidian 图片 ![[file.png]]
    for m in re.finditer(r'!\[\[([^\]]+)\]\]', body):
        if any(m.start() >= s["start"] and m.start() < s["end"] for s in spans):
            continue
        spans.append({
            "start": m.start(), "end": m.end(),
            "segment": {"type": "image", "source": m.group(1).strip(), "alt": ""}
        })

    spans.sort(key=lambda s: s["start"])
    return spans


def parse_markdown_to_segments(markdown: str) -> Tuple[list, dict]:
    """完整解析：frontmatter + 块级 + 特殊块 → segments 列表"""
    body, meta = parse_frontmatter(markdown)
    spans = find_special_blocks(body)

    segments = []
    cursor = 0
    for span in spans:
        if span["start"] > cursor:
            segments.extend(parse_text_blocks(body[cursor:span["start"]]))
        segments.append(span["segment"])
        cursor = span["end"]
    if cursor < len(body):
        segments.extend(parse_text_blocks(body[cursor:]))

    return segments, meta
```

- [ ] **Step 4: 添加图片压缩与 base64 编码**

```python
# ── Image encoding ───────────────────────────────────────

IMG_MAX_LONG_EDGE = 1280
IMG_JPEG_QUALITY = 82
IMG_COMPRESS_MIN_BYTES = 150 * 1024  # 150KB

MIME_MAP = {
    ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".gif": "image/gif", ".webp": "image/webp",
}


def compress_image(img_path: str) -> Tuple[bytes, str]:
    """压缩图片，返回 (bytes, mime)。小图/不支持格式返回原样。"""
    path = Path(img_path)
    raw = path.read_bytes()
    ext = path.suffix.lower()
    mime = MIME_MAP.get(ext, "image/png")

    if len(raw) < IMG_COMPRESS_MIN_BYTES:
        return raw, mime
    if ext not in (".png", ".jpg", ".jpeg"):
        return raw, mime

    try:
        img = Image.open(io.BytesIO(raw))
        long_edge = max(img.size)
        target = min(IMG_MAX_LONG_EDGE, long_edge)
        if long_edge > target:
            ratio = target / long_edge
            new_size = (int(img.width * ratio), int(img.height * ratio))
            img = img.resize(new_size, Image.LANCZOS)
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
        compressed = buf.getvalue()
        if len(compressed) < len(raw):
            return compressed, "image/jpeg"
        return raw, mime
    except Exception:
        return raw, mime


def encode_image(img_path: str) -> Optional[dict]:
    """读取并压缩图片，返回 {base64, mime, fileName, bytes} 或 None"""
    path = Path(img_path)
    if not path.exists():
        return None
    data, mime = compress_image(img_path)
    fname = path.stem + (".jpg" if mime == "image/jpeg" else path.suffix)
    return {
        "base64": base64.b64encode(data).decode("ascii"),
        "mime": mime,
        "fileName": fname,
        "bytes": len(data),
    }
```

- [ ] **Step 5: 添加 buildPastePlan 与 build_payload**

```python
# ── Paste plan builder ───────────────────────────────────

BLOCK_TAGS = {
    "header-one": "h1", "header-two": "h2", "header-three": "h3",
    "blockquote": "blockquote", "unstyled": "p",
}
STYLE_TAGS = {"Bold": "strong", "Italic": "em", "Strikethrough": "s", "Code": "code"}


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_inline_html(segment: dict) -> str:
    """将 inlineStyleRanges + links 渲染为 HTML"""
    text = segment.get("text", "")
    opens = [[] for _ in range(len(text) + 1)]
    closes = [[] for _ in range(len(text) + 1)]

    for r in segment.get("inlineStyleRanges", []):
        tag = STYLE_TAGS.get(r["style"])
        if tag:
            opens[r["offset"]].append(f"<{tag}>")
            closes[r["offset"] + r["length"]].insert(0, f"</{tag}>")
    for link in segment.get("links", []):
        href = escape_html(link["url"])
        opens[link["offset"]].append(f'<a href="{href}">')
        closes[link["offset"] + link["length"]].insert(0, "</a>")

    out = ""
    for i in range(len(text)):
        out += "".join(closes[i]) + "".join(opens[i]) + escape_html(text[i])
    out += "".join(closes[len(text)])
    return out


def build_paste_plan(segments: list, image_results: dict, cover_source: str = "") -> dict:
    """构建注入计划：blocks + html + plan + markerPrefix"""
    prefix = f"__XPOSTER_{os.urandom(3).hex()}_"
    idx = 0
    html_parts = []
    blocks = []
    plan = []
    list_tag = None
    list_items = []

    def marker(mtype: str) -> str:
        nonlocal idx
        token = f"{prefix}{mtype}_{idx}__"
        idx += 1
        return token

    def add_block(btype: str, text: str, seg: dict = None):
        blocks.append({
            "type": btype or "unstyled",
            "text": re.sub(r'\n+', ' ', str(text or "")),
            "inlineStyleRanges": [dict(r) for r in (seg or {}).get("inlineStyleRanges", [])],
            "links": [dict(l) for l in (seg or {}).get("links", [])],
        })

    def flush_list():
        nonlocal list_tag, list_items
        if not list_tag:
            return
        inner = "".join(f"<li>{item}</li>" for item in list_items)
        html_parts.append(f"<{list_tag}>{inner}</{list_tag}>")
        list_tag = None
        list_items = []

    def add_image_op(seg: dict, result: dict, cover_only: bool = False):
        mid = marker("COVER" if cover_only else "IMAGE")
        html_parts.append(f"<p>{mid}</p>")
        add_block("unstyled", mid)
        plan.append({
            "marker": mid,
            "op": {
                "type": "image",
                "file": {
                    "base64": result["base64"],
                    "mime": result["mime"],
                    "fileName": result["fileName"],
                    "alt": seg.get("alt", ""),
                },
                "source": seg.get("source", ""),
                "fallbackText": "" if cover_only else f'![{seg.get("alt", "")}]({seg.get("source", "")})',
                "coverOnly": cover_only,
            }
        })

    for seg in segments:
        stype = seg.get("type", "text")

        if stype == "text":
            rendered = render_inline_html(seg) or "<br>"
            kind = seg.get("kind", "unstyled")
            add_block(kind, seg.get("text", ""), seg)
            if kind in ("unordered-list-item", "ordered-list-item"):
                next_tag = "ul" if kind == "unordered-list-item" else "ol"
                if list_tag and list_tag != next_tag:
                    flush_list()
                list_tag = next_tag
                list_items.append(rendered)
                continue
            flush_list()
            tag = BLOCK_TAGS.get(kind, "p")
            html_parts.append(f"<{tag}>{rendered}</{tag}>")
            continue

        flush_list()

        if stype == "divider":
            mid = marker("DIVIDER")
            html_parts.append(f"<p>{mid}</p>")
            add_block("unstyled", mid)
            plan.append({"marker": mid, "op": {"type": "atomic", "entityType": "DIVIDER", "data": {}, "mutability": "IMMUTABLE"}})
            continue

        if stype == "code":
            mid = marker("CODE")
            md = f'```{seg.get("language", "")}\n{seg.get("code", "")}\n```'
            html_parts.append(f"<p>{mid}</p>")
            add_block("unstyled", mid)
            plan.append({"marker": mid, "op": {"type": "atomic", "entityType": "MARKDOWN", "data": {"markdown": md}, "mutability": "MUTABLE"}})
            continue

        if stype == "image":
            src = seg.get("source", "")
            result = image_results.get(src)
            if result and result.get("ok"):
                is_cover = cover_source and _sources_match(src, cover_source)
                add_image_op(seg, result, cover_only=is_cover)
            else:
                fallback = f'![{seg.get("alt", "")}]({src})'
                html_parts.append(f"<p>{escape_html(fallback)}</p>")
                add_block("unstyled", fallback)

    flush_list()

    plain = "\n\n".join(b["text"] for b in blocks if b["text"].strip())
    return {"html": "".join(html_parts), "plain": plain, "blocks": blocks, "plan": plan, "markerPrefix": prefix}


def _sources_match(a: str, b: str) -> bool:
    a, b = a.strip().split("#")[0], b.strip().split("#")[0]
    return a == b
```

- [ ] **Step 6: 添加 build_payload 入口函数**

```python
# ── Public API ───────────────────────────────────────────

def build_payload(md_path: str) -> dict:
    """解析 .md 文件，生成 X Articles 注入 payload"""
    markdown = Path(md_path).read_text(encoding="utf-8")
    md_dir = str(Path(md_path).resolve().parent)

    segments, meta = parse_markdown_to_segments(markdown)
    title = extract_title(meta, segments, md_path)

    # 移除正文中的 h1 标题（如果已提取）
    if title:
        for i, seg in enumerate(segments):
            if seg.get("type") == "text" and seg.get("kind") == "header-one":
                if seg.get("text", "").strip() == title.strip():
                    segments.pop(i)
                    break

    cover = extract_cover(meta, segments)

    # 编码图片
    image_results = {}
    for seg in segments:
        if seg.get("type") != "image":
            continue
        src = seg.get("source", "")
        if src.startswith("http"):
            continue  # 远程图片跳过
        # 解析本地路径
        img_path = src if os.path.isabs(src) else os.path.join(md_dir, src)
        result = encode_image(img_path)
        if result:
            result["ok"] = True
            image_results[src] = result

    paste = build_paste_plan(segments, image_results, cover or "")

    # 构造 images 数组
    image_payloads = []
    for op in paste["plan"]:
        if op["op"].get("type") == "image" and op["op"].get("file", {}).get("base64"):
            image_payloads.append({
                "marker": op["marker"],
                "base64": op["op"]["file"]["base64"],
                "fileName": op["op"]["file"]["fileName"],
                "mime": op["op"]["file"]["mime"],
                "alt": op["op"]["file"].get("alt", ""),
                "coverOnly": op["op"].get("coverOnly", False),
                "fallbackText": op["op"].get("fallbackText", ""),
                "source": op["op"].get("source", ""),
            })

    return {
        "title": title or "",
        "cover": cover or "",
        "html": paste["html"],
        "plain": paste["plain"],
        "blocks": paste["blocks"],
        "plan": paste["plan"],
        "markerPrefix": paste["markerPrefix"],
        "images": image_payloads,
        "articleId": None,
    }
```

- [ ] **Step 7: 验证 — 用现有文章测试解析**

```bash
cd /Users/huguiqi/Documents/grok/推文
python -c "
from markdown_parser import build_payload
import json
# 找一个已归档的 .md 文件测试
from pathlib import Path
mds = list(Path('.').rglob('*.md'))
mds = [p for p in mds if 'node_modules' not in str(p) and '.claude' not in str(p) and 'docs' not in str(p)]
if mds:
    p = build_payload(str(mds[0]))
    print(f'Title: {p[\"title\"]}')
    print(f'Blocks: {len(p[\"blocks\"])}')
    print(f'Images: {len(p[\"images\"])}')
    print(f'Plan items: {len(p[\"plan\"])}')
else:
    print('No .md files found')
"
```

---

## Task 2: article_server.py — Python HTTP 服务

**Files:**
- Create: `article_server.py`

- [ ] **Step 1: 创建 article_server.py**

```python
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
            "textBlocks": len([b for b in self.payload.get("blocks", []) if b.get("type") == "text" or b.get("type") == "unstyled"]),
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
        script = (
            f'(async function __hermesMainInject(){{"use strict";'
            f'{self.xpage_js};'
            f'const payload={payload_json};'
            f'console.log("[Hermes:MAIN] Injecting: "+payload.title);'
            f'const result=await window.__xArticleWrite(payload);'
            f'console.log("[Hermes:MAIN]",JSON.stringify(result,null,2));'
            f'const el=document.createElement("meta");'
            f'el.setAttribute("data-hermes-result",JSON.stringify(result));'
            f'document.head.appendChild(el);'
            f'return result;}})();'
        )
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
```

- [ ] **Step 2: 验证服务启动**

```bash
cd /Users/huguiqi/Documents/grok/推文
python -c "
from article_server import start_article_server, stop_article_server
from pathlib import Path

# 找一个已归档的 .md 文件测试
mds = list(Path('.').rglob('*.md'))
mds = [p for p in mds if 'node_modules' not in str(p) and '.claude' not in str(p) and 'docs' not in str(p)]
if mds:
    srv = start_article_server(str(mds[0]))
    print(f'Server started on port {srv.port}')
    print(f'Title: {srv.payload[\"title\"]}')
    stop_article_server()
    print('Server stopped')
else:
    print('No .md files found')
"
```

---

## Task 3: 复制 xpage.js 并创建 Chrome 扩展

**Files:**
- Create: `xpage.js` (从 x-article-publisher 复制)
- Create: `extension/manifest.json`
- Create: `extension/content.js`
- Create: `extension/background.js`

- [ ] **Step 1: 复制 xpage.js**

```bash
cp /Users/huguiqi/Public/openSource/github/x-article-publisher-payload/xpage.js /Users/huguiqi/Documents/grok/推文/xpage.js
```

- [ ] **Step 2: 创建 extension/manifest.json**

```json
{
  "manifest_version": 3,
  "name": "Grok X 文章导入",
  "version": "1.0",
  "description": "从本地助手导入 Markdown 文章到 X Articles 编辑器",
  "permissions": ["scripting", "activeTab"],
  "host_permissions": [
    "https://x.com/*",
    "http://localhost:8765/*"
  ],
  "background": {
    "service_worker": "background.js"
  },
  "content_scripts": [
    {
      "matches": [
        "https://x.com/*/articles/edit/*",
        "https://x.com/compose/articles*"
      ],
      "js": ["content.js"],
      "run_at": "document_idle"
    }
  ]
}
```

- [ ] **Step 3: 创建 extension/content.js**

```javascript
/**
 * content.js — Grok X 文章导入
 * 在 X 文章编辑页显示「📥 载入文章」浮动按钮
 * 点击后从 localhost:8765 拉取 payload 并注入编辑器
 */
(async function () {
  'use strict';

  const LOG = '[GrokX]';
  const SERVER = 'http://localhost:8765';
  let injecting = false;

  function showBanner(text, color, duration) {
    color = color || '#1d9bf0';
    duration = duration || 6000;
    var b = document.createElement('div');
    b.style.cssText = 'position:fixed;top:54px;right:12px;background:' + color + ';color:#fff;padding:10px 18px;border-radius:8px;font-family:-apple-system,sans-serif;font-size:14px;z-index:999999;box-shadow:0 4px 12px rgba(0,0,0,0.3);transition:opacity 0.3s;max-width:380px;word-wrap:break-word';
    b.textContent = text;
    document.body.appendChild(b);
    setTimeout(function () { b.style.opacity = '0'; setTimeout(function () { b.remove(); }, 300); }, duration);
  }

  function editorReady() {
    var sels = [
      '[data-contents="true"] [contenteditable="true"]',
      '[contenteditable="true"][role="textbox"]',
      '[contenteditable="true"].public-DraftEditor-content'
    ];
    for (var si = 0; si < sels.length; si++) {
      var els = document.querySelectorAll(sels[si]);
      for (var ei = 0; ei < els.length; ei++) {
        var r = els[ei].getBoundingClientRect();
        if (r.width > 200 && r.height > 80) return true;
      }
    }
    return false;
  }

  function makeButton() {
    var btn = document.createElement('div');
    btn.id = 'grokx-import-btn';
    btn.innerHTML = '📥 载入文章';
    btn.setAttribute('role', 'button');
    btn.style.cssText = 'position:fixed;top:12px;right:12px;z-index:99998;background:linear-gradient(135deg,#1d9bf0,#0d8bd8);color:#fff;padding:8px 16px;border-radius:20px;font-family:-apple-system,sans-serif;font-size:13px;font-weight:700;cursor:pointer;box-shadow:0 2px 8px rgba(29,155,240,0.4);transition:transform 0.15s,box-shadow 0.15s;user-select:none';
    btn.onmouseenter = function () { btn.style.transform = 'scale(1.05)'; btn.style.boxShadow = '0 4px 12px rgba(29,155,240,0.5)'; };
    btn.onmouseleave = function () { btn.style.transform = 'scale(1)'; btn.style.boxShadow = '0 2px 8px rgba(29,155,240,0.4)'; };
    btn.addEventListener('click', function (e) {
      e.preventDefault();
      e.stopPropagation();
      console.log(LOG, '载入文章被点击');
      handleImport();
    }, true);
    return btn;
  }

  function syncButton() {
    var btn = document.getElementById('grokx-import-btn');
    if (editorReady()) {
      if (!btn) {
        document.body.appendChild(makeButton());
        console.log(LOG, '载入按钮已显示');
      }
    } else if (btn) {
      btn.remove();
    }
  }

  async function handleImport() {
    if (injecting) { showBanner('正在载入中...', '#1d9bf0', 3000); return; }
    injecting = true;
    try {
      var status;
      try {
        var resp = await fetch(SERVER + '/status');
        status = await resp.json();
      } catch (e) {
        showBanner('无法连接 Grok 助手 (localhost:8765) — 请先在助手中点击「发长文章到X草稿箱」', '#f4212e');
        return;
      }
      if (!status || !status.ready) {
        showBanner('助手服务未就绪 — 请先在助手中点击「发长文章到X草稿箱」', '#f4212e');
        return;
      }
      if (!editorReady()) {
        showBanner('未检测到编辑器 — 请在文章编辑页使用', '#f4212e');
        return;
      }
      await doInject();
    } catch (e) {
      console.error(LOG, 'handleImport error:', e && e.message);
      showBanner('载入出错: ' + (e && e.message), '#f4212e', 6000);
    } finally {
      injecting = false;
    }
  }

  async function doInject() {
    showBanner('正在载入文章...', '#1d9bf0');

    var script = document.createElement('script');
    script.src = SERVER + '/inject-script?t=' + Date.now();
    (document.head || document.documentElement).appendChild(script);

    await new Promise(function (r) { setTimeout(r, 5000); });

    var resultEl = document.querySelector('[data-hermes-result]');
    if (resultEl) {
      try {
        var result = JSON.parse(resultEl.getAttribute('data-hermes-result'));
        if (result.ok) {
          showBanner('文章已载入！检查内容后点 Publish 发布', '#00ba7c', 8000);
        } else {
          showBanner('载入失败: ' + (result.error || '未知错误'), '#f4212e', 6000);
        }
      } catch (e) {
        showBanner('载入可能成功，请检查编辑器', '#ffa500', 5000);
      }
      resultEl.remove();
    } else {
      showBanner('未收到确认信号，请检查编辑器内容', '#ffa500', 5000);
    }
  }

  setTimeout(syncButton, 1000);
  setInterval(syncButton, 800);

  console.log(LOG, 'Content script ready — 仅编辑器页显示载入按钮');
})();
```

- [ ] **Step 4: 创建 extension/background.js**

```javascript
/**
 * background.js — Grok X 文章导入 service worker
 */
chrome.action && chrome.action.onClicked && chrome.action.onClicked.addListener(function () {
  chrome.tabs.create({ url: 'http://localhost:8765' });
});
console.log('[GrokX] Service worker ready');
```

- [ ] **Step 5: 验证文件完整性**

```bash
cd /Users/huguiqi/Documents/grok/推文
ls -la xpage.js extension/
python -c "from pathlib import Path; assert Path('xpage.js').exists(), 'xpage.js missing'; assert Path('extension/manifest.json').exists(), 'manifest missing'; assert Path('extension/content.js').exists(), 'content.js missing'; print('All files present')"
```

---

## Task 4: 改造 xai_api.py — 替换 publish_article_to_x

**Files:**
- Modify: `xai_api.py:325-428`

- [ ] **Step 1: 删除旧的 claude-sdk 实现**

删除 `xai_api.py` 中第 325-428 行的 `_publish_article_via_sdk` 和 `publish_article_to_x` 函数。

- [ ] **Step 2: 添加新的 publish_article_to_x**

在 `xai_api.py` 末尾替换为：

```python
# ====================== 发布长文到 X Articles ======================

def publish_article_to_x(md_path) -> tuple:
    """启动文章 HTTP 服务，返回 (success, message)"""
    from article_server import start_article_server
    try:
        server = start_article_server(str(md_path))
        return True, (
            f"文章服务已启动：http://localhost:{server.port}\n"
            f"文章：{server.payload.get('title', '(untitled)')}\n"
            f"文本块：{len(server.payload.get('blocks', []))} | 图片：{len(server.payload.get('images', []))}"
        )
    except Exception as e:
        return False, f"启动服务失败：{e}"
```

- [ ] **Step 3: 验证 import 正常**

```bash
cd /Users/huguiqi/Documents/grok/推文
python -c "from xai_api import publish_article_to_x; print('import OK')"
```

---

## Task 5: 微调 x_grok_poster.py — 按钮反馈

**Files:**
- Modify: `x_grok_poster.py:519-529`

- [ ] **Step 1: 修改按钮点击后的提示文案**

将 `x_grok_poster.py` 第 519-529 行的：

```python
            with st.spinner("正在通过 Claude Code 发布到 X 草稿箱，请勿关闭浏览器..."):
                success, msg = publish_article_to_x(md_path)

            if success:
                st.success("文章已保存到 X 草稿箱！请到 X 手动检查并发布。")
                with st.expander("发布详情"):
                    st.code(msg, language="text")
            else:
                st.error("发布到 X 草稿箱失败")
                with st.expander("错误详情"):
                    st.code(msg, language="text")
```

替换为：

```python
            success, msg = publish_article_to_x(md_path)

            if success:
                st.success("文章服务已启动！")
                st.info(
                    "👉 在 Chrome 打开 [x.com/compose/articles/new](https://x.com/compose/articles/new)，"
                    "点击右上角「📥 载入文章」按钮导入文章"
                )
                with st.expander("服务详情"):
                    st.code(msg, language="text")
            else:
                st.error(msg)
```

- [ ] **Step 2: 验证 Streamlit 启动正常**

```bash
cd /Users/huguiqi/Documents/grok/推文
python -c "import x_grok_poster; print('import OK')"
```

---

## Task 6: 端到端验证

- [ ] **Step 1: 安装 Chrome 扩展**

1. 打开 Chrome → `chrome://extensions`
2. 开启「开发者模式」
3. 点击「加载已解压的扩展程序」
4. 选择 `/Users/huguiqi/Documents/grok/推文/extension/` 目录
5. 确认扩展出现在列表中

- [ ] **Step 2: 启动 Streamlit 并测试完整流程**

```bash
cd /Users/huguiqi/Documents/grok/推文
streamlit run x_grok_poster.py
```

1. 在浏览器打开 Streamlit 页面
2. 切换到「📝 文章长文」tab
3. 输入草稿 → 润色 → 生成图片 → 保存
4. 点击「📝 发长文章到X草稿箱」
5. 确认看到「文章服务已启动」提示

- [ ] **Step 3: 测试扩展注入**

1. 在 Chrome 打开 `x.com/compose/articles/new`
2. 点击「写文章」进入编辑器
3. 确认右上角出现「📥 载入文章」按钮
4. 点击按钮
5. 确认文章内容出现在编辑器中
6. 检查图片是否正确上传
7. 手动点 Publish
