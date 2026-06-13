"""
markdown_parser.py -- Markdown 解析与 X Articles payload 生成

把 Markdown 文件解析为 Draft.js 可消费的 payload 结构，
供 X Articles 编辑器通过 paste 注入使用。

移植自 x-article-publisher 的 shared.js + payload.js。
"""

import base64
import io
import json
import os
import random
import re
import string
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ──────────────────────────── 常量 ────────────────────────────

STYLE_TAGS = {
    "Bold": "strong",
    "Italic": "em",
    "Strikethrough": "s",
    "Code": "code",
}

BLOCK_TAGS = {
    "header-one": "h1",
    "header-two": "h2",
    "header-three": "h3",
    "header-four": "h4",
    "header-five": "h5",
    "header-six": "h6",
    "blockquote": "blockquote",
    "unstyled": "p",
}

IMG_MAX_LONG_EDGE = 1280
IMG_JPEG_QUALITY = 82
IMG_COMPRESS_MIN_BYTES = 150 * 1024  # 150 KB

PLACEHOLDER_TITLES = {"待定", "暂定", "未定", "TBD", "tbd", "TBA", "tba", "WIP", "wip"}

# ──────────────────────────── 工具函数 ────────────────────────────


def escape_html(value: str) -> str:
    """HTML 转义。"""
    return (
        str(value or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _random_marker_prefix() -> str:
    """生成 5 位随机标记前缀。"""
    chars = string.ascii_lowercase + string.digits
    return "__XPOSTER_" + "".join(random.choices(chars, k=5)) + "_"


def _is_escaped(text: str, index: int) -> bool:
    """检查 index 位置的字符是否被反斜杠转义。"""
    count = 0
    i = index - 1
    while i >= 0 and text[i] == "\\":
        count += 1
        i -= 1
    return count % 2 == 1


def _overlaps(spans: List[dict], index: int) -> bool:
    """检查 index 是否落在已有 span 范围内。"""
    return any(s["start"] <= index < s["end"] for s in spans)


def _guess_file_name(source: str, fallback: str = "image") -> str:
    """从图片路径/URL 中猜测文件名。"""
    if not isinstance(source, str) or source.startswith("data:"):
        return f"{fallback}.png"
    # 尝试从 URL 或路径中提取最后一段
    path_part = source.split("?")[0].split("#")[0]
    parts = [p for p in re.split(r"[/\\]", path_part) if p]
    if parts:
        name = parts[-1]
        if re.search(r"\.[a-z0-9]{2,5}$", name, re.I):
            return name
    return f"{fallback}.png"


def _image_fallback_markdown(segment: dict) -> str:
    """图片加载失败时的回退文本。"""
    raw_alt = re.sub(r"[\]\r\n]+", " ", str(segment.get("alt") or _guess_file_name(segment.get("source", "")) or "image")).strip()
    alt = raw_alt or "image"
    source = str(segment.get("source", "")).strip()
    if not source or source.startswith("data:"):
        return f"[image unavailable: {alt}]"
    return f"![{alt}]({source})"


def _image_sources_match(left: str, right: str) -> bool:
    """比较两个图片来源是否指向同一张图（忽略 fragment）。"""
    l = str(left or "").strip()
    r = str(right or "").strip()
    if not l or not r:
        return False
    if l == r:
        return True
    return l.split("#")[0] == r.split("#")[0]


# ──────────────────────────── 1. Frontmatter 解析 ────────────────────────────


def parse_frontmatter(markdown: str) -> Tuple[str, Dict[str, str]]:
    """
    解析 YAML frontmatter（简单 key: value 格式）。

    Returns:
        (body, meta_dict) -- body 去掉 frontmatter 后的正文，meta 为字典。
    """
    normalized = markdown.replace("\r\n", "\n")
    match = re.match(r"^---\n([\s\S]*?)\n---\n*", normalized)
    if not match:
        return normalized.strip(), {}

    meta: Dict[str, str] = {}
    for line in match.group(1).split("\n"):
        idx = line.find(":")
        if idx < 0:
            continue
        key = line[:idx].strip()
        value = line[idx + 1 :].strip().strip("\"'")
        if key:
            # 解码 YAML 双引号字符串中的 unicode 转义
            value = _decode_unicode_escapes(value)
            meta[key] = value

    body = normalized[match.end() :].strip()
    return body, meta


def _decode_unicode_escapes(value: Optional[str]) -> Optional[str]:
    """解码 YAML 字面量中的 \\U / \\u 转义。"""
    if value is None:
        return value
    s = str(value)
    if "\\" not in s:
        return s
    # \U0001F680 (8位)
    s = re.sub(
        r"\\U([0-9a-fA-F]{8})",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )
    # \u{1F680} (可变长)
    s = re.sub(
        r"\\u\{([0-9a-fA-F]+)\}",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )
    # \uXXXX (4位)
    s = re.sub(
        r"\\u([0-9a-fA-F]{4})",
        lambda m: chr(int(m.group(1), 16)),
        s,
    )
    return s


def extract_title(meta: Dict[str, str], segments: List[dict], md_path: str = "") -> Optional[str]:
    """
    从 frontmatter > h1 > 文件名 提取标题。

    同时会把 segments 中匹配的 h1 移除（避免正文重复）。
    """
    # 1. frontmatter
    raw = _decode_unicode_escapes(
        meta.get("title") or meta.get("Title") or meta.get("标题")
    )
    title_from_meta = raw.strip() if raw and raw.strip() not in PLACEHOLDER_TITLES else None

    if title_from_meta:
        # 如果正文首个 H1 和 frontmatter 标题一样，删掉它
        for i, seg in enumerate(segments):
            if seg.get("type") == "text" and seg.get("kind") == "header-one":
                if seg.get("text", "").strip() == title_from_meta:
                    segments.pop(i)
                break
        return title_from_meta

    # 2. 正文 h1
    for i, seg in enumerate(segments):
        if seg.get("type") == "text" and seg.get("kind") == "header-one":
            t = (seg.get("text") or "").strip()
            if t:
                segments.pop(i)
                return t

    # 3. 文件名
    if md_path:
        name = Path(md_path).stem
        name = re.sub(r"\.(md|markdown|mdown|mkd|txt)$", "", name, flags=re.I)
        name = re.sub(r"\s+", " ", name).strip()
        if name:
            return name

    return None


def extract_cover(meta: Dict[str, str], segments: List[dict]) -> Optional[str]:
    """
    从 frontmatter > 第一张图片 提取封面来源。
    """
    raw = meta.get("cover") or meta.get("Cover") or meta.get("封面")
    if raw:
        cover = raw.strip()
        # 去掉 ![[...]] 或 ![alt](...) 包装
        cover = re.sub(r"^!\[\[|\]\]$", "", cover)
        m = re.match(r"^!\[[^\]]*\]\(([^)]+)\)$", cover)
        if m:
            cover = m.group(1)
        cover = cover.strip()
        if cover:
            return cover

    # fallback: 第一张图片 segment
    for seg in segments:
        if seg.get("type") == "image" and seg.get("source"):
            return seg["source"]

    return None


# ──────────────────────────── 2. 行内解析 ────────────────────────────


def parse_inline(source: str) -> dict:
    """
    解析 Markdown 行内元素：**粗体**、*斜体*、~~删除线~~、`code`、[链接](url)。

    返回 Draft.js 风格的 block dict（type=text, 含 inlineStyleRanges / links）。
    """
    result: dict = {
        "type": "text",
        "kind": "unstyled",
        "text": "",
        "inlineStyleRanges": [],
        "links": [],
    }
    return _parse_inline_into(result, source)


def _parse_inline_into(result: dict, source: str) -> dict:
    """将行内 Markdown 解析结果填充到 result 中。"""
    cursor = 0

    def append_styled(text: str, styles: List[str]):
        offset = len(result["text"])
        result["text"] += text
        for style in styles:
            result["inlineStyleRanges"].append(
                {"offset": offset, "length": len(text), "style": style}
            )

    while cursor < len(source):
        char = source[cursor]

        # ── 链接 [text](url) ──
        if char == "[":
            link_match = re.match(r"\[([^\]]+)\]\(([^)]+)\)", source[cursor:])
            if link_match:
                offset = len(result["text"])
                result["text"] += link_match.group(1)
                result["links"].append(
                    {
                        "offset": offset,
                        "length": len(link_match.group(1)),
                        "url": link_match.group(2),
                    }
                )
                cursor += link_match.end()
                continue

        # ── 粗斜体 *** / 粗体 ** / 删除线 ~~ ──
        inline_rules = [
            ("***", ["Bold", "Italic"]),
            ("**", ["Bold"]),
            ("~~", ["Strikethrough"]),
        ]
        matched = False
        for marker, styles in inline_rules:
            if source[cursor : cursor + len(marker)] == marker:
                end = source.find(marker, cursor + len(marker))
                if end > cursor:
                    append_styled(source[cursor + len(marker) : end], styles)
                    cursor = end + len(marker)
                    matched = True
                    break
        if matched:
            continue

        # ── 斜体 *text* 或 _text_ ──
        if char in ("*", "_") and cursor + 1 < len(source) and source[cursor + 1] != char:
            end = source.find(char, cursor + 1)
            if end > cursor and (end + 1 >= len(source) or source[end + 1] != char):
                append_styled(source[cursor + 1 : end], ["Italic"])
                cursor = end + 1
                continue

        # ── 行内代码 `code` ──
        if char == "`":
            end = source.find("`", cursor + 1)
            if end > cursor:
                append_styled(source[cursor + 1 : end], ["Code"])
                cursor = end + 1
                continue

        # ── 普通字符 ──
        result["text"] += char
        cursor += 1

    return result


# ──────────────────────────── 3. Block 解析 ────────────────────────────


def _find_inline_code_ranges(markdown: str) -> List[Tuple[int, int]]:
    """找出行内代码区间（成对的单/双反引号），用于跳过其中的图片语法。"""
    ranges = []
    runs = []
    for m in re.finditer(r"`{1,2}", markdown):
        runs.append((m.start(), len(m.group())))
    used = [False] * len(runs)
    for i in range(len(runs)):
        if used[i]:
            continue
        for j in range(i + 1, len(runs)):
            if used[j] or runs[j][1] != runs[i][1]:
                continue
            ranges.append((runs[i][0], runs[j][0] + runs[j][1]))
            used[i] = used[j] = True
            break
    return ranges


def _find_closing_bracket(text: str, start: int) -> int:
    """找到未转义的 ] 位置。"""
    for i in range(start, len(text)):
        if text[i] == "]" and not _is_escaped(text, i):
            return i
    return -1


def _find_closing_paren(text: str, start: int) -> int:
    """找到未转义的 ) 位置，支持嵌套括号。"""
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if _is_escaped(text, i):
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            if depth == 0:
                return i
            depth -= 1
    return -1


def _find_markdown_image_spans(markdown: str) -> List[dict]:
    """找出所有 ![alt](src) 图片 span。"""
    spans = []
    inline_code = _find_inline_code_ranges(markdown)
    inside_inline_code = lambda pos: any(s < pos < e for s, e in inline_code)

    cursor = 0
    while cursor < len(markdown):
        start = markdown.find("![", cursor)
        if start < 0:
            break
        if inside_inline_code(start):
            cursor = start + 2
            continue
        alt_end = _find_closing_bracket(markdown, start + 2)
        if alt_end < 0 or alt_end + 1 >= len(markdown) or markdown[alt_end + 1] != "(":
            cursor = start + 2
            continue
        source_start = alt_end + 2
        source_end = _find_closing_paren(markdown, source_start)
        if source_end < 0:
            cursor = alt_end + 1
            continue
        spans.append(
            {
                "start": start,
                "end": source_end + 1,
                "alt": markdown[start + 2 : alt_end],
                "source": markdown[source_start:source_end].strip(),
            }
        )
        cursor = source_end + 1
    return spans


def find_special_blocks(body: str) -> List[dict]:
    """
    找出 body 中的特殊 block：围栏代码块、分割线、图片、Obsidian 图片、推文链接。

    返回按 start 排序的 span 列表，每个包含 start/end/segment。
    """
    spans: List[dict] = []

    # ── 围栏代码块 ``` ──
    for m in re.finditer(r"```([^\n`]*)\n([\s\S]*?)```", body):
        spans.append(
            {
                "start": m.start(),
                "end": m.end(),
                "segment": {
                    "type": "code",
                    "language": (m.group(1) or "").strip(),
                    "code": (m.group(2) or "").rstrip("\n"),
                },
            }
        )

    # ── 分割线 --- / *** / ___ ──
    for m in re.finditer(r"^(?: {0,3})(?:-{3,}|\*{3,}|_{3,})(?:[ \t]*)$", body, re.M):
        if not _overlaps(spans, m.start()):
            spans.append(
                {
                    "start": m.start(),
                    "end": m.end(),
                    "segment": {"type": "divider"},
                }
            )

    # ── 推文链接（裸 URL）──
    for m in re.finditer(
        r"^(?: {0,3})https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/(\d+)(?:[?#][^\s]*)?\s*$",
        body,
        re.M,
    ):
        if not _overlaps(spans, m.start()):
            spans.append(
                {
                    "start": m.start(),
                    "end": m.end(),
                    "segment": {"type": "tweet", "tweetId": m.group(1)},
                }
            )

    # ── Markdown 图片 ![alt](src) ──
    for img in _find_markdown_image_spans(body):
        if _overlaps(spans, img["start"]):
            continue
        source = img["source"]
        tweet_match = re.match(
            r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/(\d+)",
            source,
        )
        if tweet_match:
            seg = {"type": "tweet", "tweetId": tweet_match.group(1)}
        else:
            seg = {"type": "image", "source": source, "alt": img["alt"].strip()}
        spans.append({"start": img["start"], "end": img["end"], "segment": seg})

    # ── 推文链接（Markdown 链接格式）──
    for m in re.finditer(
        r"^[ \t]*\[([^\]]*)\]\(([^)]+)\)[ \t]*$", body, re.M
    ):
        if _overlaps(spans, m.start()):
            continue
        tweet_match = re.match(
            r"https?://(?:www\.)?(?:x\.com|twitter\.com)/[A-Za-z0-9_]+/status/(\d+)",
            m.group(2).strip(),
        )
        if tweet_match:
            spans.append(
                {
                    "start": m.start(),
                    "end": m.end(),
                    "segment": {"type": "tweet", "tweetId": tweet_match.group(1)},
                }
            )

    # ── Obsidian 图片 ![[file.png]] ──
    for m in re.finditer(r"^[ \t]*!\[\[([^\]]+)\]\][ \t]*$", body, re.M):
        if not _overlaps(spans, m.start()):
            spans.append(
                {
                    "start": m.start(),
                    "end": m.end(),
                    "segment": {"type": "image", "source": m.group(1).strip(), "alt": ""},
                }
            )

    spans.sort(key=lambda s: s["start"])
    return spans


def parse_text_blocks(text: str) -> List[dict]:
    """
    把纯文本段落解析为 Draft.js block 列表。

    处理 # 标题、> 引用、- 列表、1. 有序列表、段落。
    """
    lines = text.split("\n")
    segments: List[dict] = []
    paragraph: List[str] = []

    def flush():
        value = "\n".join(paragraph).strip()
        if value:
            seg = {"type": "text", "kind": "unstyled", "text": "", "inlineStyleRanges": [], "links": []}
            _parse_inline_into(seg, value)
            segments.append(seg)
        paragraph.clear()

    for line in lines:
        trimmed = line.strip()
        if not trimmed:
            flush()
            continue

        # 标题
        m = re.match(r"^(#{1,6})\s+(.+)$", trimmed)
        if m:
            flush()
            level = len(m.group(1))
            kind = ["", "header-one", "header-two", "header-three", "header-four", "header-five", "header-six"][level]
            seg = {"type": "text", "kind": kind, "text": "", "inlineStyleRanges": [], "links": []}
            _parse_inline_into(seg, m.group(2).strip())
            segments.append(seg)
            continue

        # 引用
        m = re.match(r"^>\s+(.+)$", trimmed)
        if m:
            flush()
            seg = {"type": "text", "kind": "blockquote", "text": "", "inlineStyleRanges": [], "links": []}
            _parse_inline_into(seg, m.group(1).strip())
            segments.append(seg)
            continue

        # 无序列表
        m = re.match(r"^[-*+]\s+(.+)$", trimmed)
        if m:
            flush()
            seg = {"type": "text", "kind": "unordered-list-item", "text": "", "inlineStyleRanges": [], "links": []}
            _parse_inline_into(seg, m.group(1).strip())
            segments.append(seg)
            continue

        # 有序列表
        m = re.match(r"^\d+\.\s+(.+)$", trimmed)
        if m:
            flush()
            seg = {"type": "text", "kind": "ordered-list-item", "text": "", "inlineStyleRanges": [], "links": []}
            _parse_inline_into(seg, m.group(1).strip())
            segments.append(seg)
            continue

        paragraph.append(trimmed)

    flush()
    return segments


def parse_markdown_to_segments(markdown: str) -> Tuple[List[dict], Dict[str, str]]:
    """
    完整解析 Markdown，返回 (segments, meta)。

    segments 为统一的 segment 列表（type: text/image/code/divider/tweet）。
    """
    body, meta = parse_frontmatter(markdown)

    # 找出特殊 block（代码块、分割线、图片等）
    spans = find_special_blocks(body)

    # 合并：文本段用 parse_text_blocks，特殊段直接插入
    segments: List[dict] = []
    cursor = 0
    for span in spans:
        if span["start"] > cursor:
            segments.extend(parse_text_blocks(body[cursor : span["start"]]))
        segments.append(span["segment"])
        cursor = span["end"]
    if cursor < len(body):
        segments.extend(parse_text_blocks(body[cursor:]))

    return segments, meta


# ──────────────────────────── 4. 图片编码 ────────────────────────────


def compress_image(img_path: str) -> Optional[Tuple[bytes, str]]:
    """
    使用 PIL 压缩图片：长边 <= 1280px，JPEG 82%，小于 150KB 跳过。

    Returns:
        (bytes, mime) 压缩成功；None 表示无需压缩或压缩失败。
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        file_size = os.path.getsize(img_path)
    except OSError:
        return None

    if file_size < IMG_COMPRESS_MIN_BYTES:
        return None

    ext = Path(img_path).suffix.lower()
    if ext not in (".png", ".jpg", ".jpeg"):
        return None

    try:
        img = Image.open(img_path)
        # 只缩不放
        w, h = img.size
        long_edge = max(w, h)
        target = min(IMG_MAX_LONG_EDGE, long_edge)
        if long_edge > target:
            ratio = target / long_edge
            img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)

        # 转为 RGB（PNG 可能带 alpha）
        if img.mode in ("RGBA", "LA", "P"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            if img.mode == "P":
                img = img.convert("RGBA")
            background.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
            img = background
        elif img.mode != "RGB":
            img = img.convert("RGB")

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=IMG_JPEG_QUALITY, optimize=True)
        compressed = buf.getvalue()

        if len(compressed) > 0 and len(compressed) < file_size:
            return compressed, "image/jpeg"
        return None
    except Exception:
        return None


def encode_image(img_path: str) -> Optional[dict]:
    """
    编码本地图片为 base64 payload。

    Returns:
        {base64, mime, fileName, bytes} 或 None。
    """
    if not os.path.isfile(img_path):
        return None

    ext = Path(img_path).suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }
    original_mime = mime_map.get(ext, "image/png")
    original_name = Path(img_path).name

    # 尝试压缩
    compressed = compress_image(img_path)
    if compressed:
        data, mime = compressed
        # 换扩展名为 .jpg
        name = re.sub(r"\.(png|jpg|jpeg)$", ".jpg", original_name, flags=re.I)
        return {
            "base64": base64.b64encode(data).decode("ascii"),
            "mime": mime,
            "fileName": name,
            "bytes": len(data),
        }

    # 原样读取
    try:
        with open(img_path, "rb") as f:
            data = f.read()
        return {
            "base64": base64.b64encode(data).decode("ascii"),
            "mime": original_mime,
            "fileName": original_name,
            "bytes": len(data),
        }
    except OSError:
        return None


# ──────────────────────────── 5. 行内 HTML 渲染 ────────────────────────────


def _render_inline_html(segment: dict) -> str:
    """把 segment 的行内样式渲染为 HTML 字符串。"""
    text = segment.get("text", "")
    n = len(text)
    open_at: List[List[str]] = [[] for _ in range(n + 1)]
    close_at: List[List[str]] = [[] for _ in range(n + 1)]

    for rng in segment.get("inlineStyleRanges", []):
        tag = STYLE_TAGS.get(rng["style"])
        if not tag:
            continue
        off = rng["offset"]
        end = off + rng["length"]
        if 0 <= off <= n:
            open_at[off].append(f"<{tag}>")
        if 0 <= end <= n:
            close_at[end].insert(0, f"</{tag}>")

    for link in segment.get("links", []):
        href = escape_html(link["url"])
        off = link["offset"]
        end = off + link["length"]
        if 0 <= off <= n:
            open_at[off].append(f'<a href="{href}">')
        if 0 <= end <= n:
            close_at[end].insert(0, "</a>")

    output = ""
    for i in range(n):
        output += "".join(close_at[i])
        output += "".join(open_at[i])
        output += escape_html(text[i])
    output += "".join(close_at[n])
    return output


# ──────────────────────────── 6. Paste Plan 构建 ────────────────────────────


def build_paste_plan(
    segments: List[dict],
    image_results: Dict[int, dict],
    cover_source: Optional[str] = None,
) -> dict:
    """
    把 segments 构建成 paste plan：HTML + plain text + Draft.js blocks + 操作计划。

    image_results: {segment_index: {ok, base64, mime, fileName, bytes}} 映射。
    cover_source: 封面图片的 source 标识。

    Returns:
        {html, plain, blocks, plan, markerPrefix}
    """
    prefix = _random_marker_prefix()
    idx_counter = [0]  # 用 list 让闭包可修改

    def next_marker(typ: str) -> str:
        i = idx_counter[0]
        idx_counter[0] += 1
        return f"{prefix}{typ}_{i}__"

    html_parts: List[str] = []
    blocks: List[dict] = []
    plan: List[dict] = []

    # 列表聚合
    list_tag: Optional[str] = None
    list_items: List[str] = []

    def flush_list():
        nonlocal list_tag
        if not list_tag:
            return
        items_html = "".join(f"<li>{item}</li>" for item in list_items)
        html_parts.append(f"<{list_tag}>{items_html}</{list_tag}>")
        list_tag = None
        list_items.clear()

    def add_block(block_type: str, text: str, segment: dict = None):
        seg = segment or {}
        blocks.append(
            {
                "type": block_type or "unstyled",
                "text": re.sub(r"\n+", " ", str(text or "")),
                "inlineStyleRanges": [
                    dict(r) for r in seg.get("inlineStyleRanges", [])
                ],
                "links": [dict(l) for l in seg.get("links", [])],
            }
        )

    def add_image_operation(segment: dict, result: dict, *, marker_type: str = "IMAGE", cover_only: bool = False):
        marker_id = next_marker(marker_type)
        html_parts.append(f"<p>{marker_id}</p>")
        add_block("unstyled", marker_id)
        plan.append(
            {
                "marker": marker_id,
                "op": {
                    "type": "image",
                    "file": {
                        "base64": result.get("base64"),
                        "mime": result.get("mime"),
                        "fileName": result.get("fileName"),
                        "alt": segment.get("alt", ""),
                    },
                    "source": segment.get("source"),
                    "fallbackText": "" if cover_only else _image_fallback_markdown(segment),
                    "coverOnly": cover_only,
                },
            }
        )

    for i, segment in enumerate(segments):
        # ── 文本 ──
        if segment["type"] == "text":
            rendered = _render_inline_html(segment) or "<br>"
            add_block(segment.get("kind", "unstyled"), segment.get("text", ""), segment)

            if segment.get("kind") in ("unordered-list-item", "ordered-list-item"):
                next_tag = "ul" if segment["kind"] == "unordered-list-item" else "ol"
                if list_tag and list_tag != next_tag:
                    flush_list()
                list_tag = next_tag
                list_items.append(rendered)
                continue

            flush_list()
            tag = BLOCK_TAGS.get(segment.get("kind"), "p")
            html_parts.append(f"<{tag}>{rendered}</{tag}>")
            continue

        flush_list()

        # ── 分割线 ──
        if segment["type"] == "divider":
            mid = next_marker("DIVIDER")
            html_parts.append(f"<p>{mid}</p>")
            add_block("unstyled", mid)
            plan.append(
                {
                    "marker": mid,
                    "op": {
                        "type": "atomic",
                        "entityType": "DIVIDER",
                        "data": {},
                        "mutability": "IMMUTABLE",
                    },
                }
            )
            continue

        # ── 代码块 ──
        if segment["type"] == "code":
            mid = next_marker("CODE")
            md = f"```{segment.get('language', '')}\n{segment.get('code', '')}\n```"
            html_parts.append(f"<p>{mid}</p>")
            add_block("unstyled", mid)
            plan.append(
                {
                    "marker": mid,
                    "op": {
                        "type": "atomic",
                        "entityType": "MARKDOWN",
                        "data": {"markdown": md},
                        "mutability": "MUTABLE",
                    },
                }
            )
            continue

        # ── 推文 ──
        if segment["type"] == "tweet":
            mid = next_marker("TWEET")
            url = f"https://twitter.com/i/web/status/{segment['tweetId']}"
            html_parts.append(f"<p>{mid}</p>")
            add_block("unstyled", mid)
            plan.append(
                {
                    "marker": mid,
                    "op": {
                        "type": "atomic",
                        "entityType": "TWEET",
                        "data": {"url": url, "tweetId": segment["tweetId"]},
                        "mutability": "IMMUTABLE",
                    },
                }
            )
            continue

        # ── 图片 ──
        if segment["type"] == "image":
            result = image_results.get(segment.get("source", "")) or image_results.get(i)
            if result and result.get("ok"):
                is_cover = cover_source and _image_sources_match(segment.get("source"), cover_source)
                add_image_operation(
                    segment,
                    result,
                    marker_type="COVER" if is_cover else "IMAGE",
                    cover_only=is_cover,
                )
            else:
                fallback = _image_fallback_markdown(segment)
                html_parts.append(f"<p>{escape_html(fallback)}</p>")
                add_block("unstyled", fallback)
            continue

        # ── 表格（回退为纯文本）──
        if segment["type"] == "table":
            fallback = _table_to_markdown(segment)
            html_parts.append(f"<pre><code>{escape_html(fallback)}</code></pre>")
            add_block("code-block", fallback)
            continue

    flush_list()

    plain = "\n\n".join(b.get("text", "").strip() for b in blocks if b.get("text", "").strip())

    return {
        "html": "".join(html_parts),
        "plain": plain,
        "blocks": blocks,
        "plan": plan,
        "markerPrefix": prefix,
    }


def _table_to_markdown(table: dict) -> str:
    """把 table segment 转回 Markdown 表格文本。"""
    lines = [f"| {' | '.join(table.get('headers', []))} |"]
    aligns = table.get("alignments", [])
    sep = []
    for a in aligns:
        if a == "center":
            sep.append(":---:")
        elif a == "right":
            sep.append("---:")
        else:
            sep.append(":---")
    lines.append(f"| {' | '.join(sep)} |")
    for row in table.get("rows", []):
        lines.append(f"| {' | '.join(row)} |")
    return "\n".join(lines)


# ──────────────────────────── 7. 入口函数 ────────────────────────────


def build_payload(md_path: str) -> dict:
    """
    解析 Markdown 文件并生成完整的 X Articles payload。

    Returns:
        {title, cover, html, plain, blocks, plan, markerPrefix, images, articleId}
    """
    md_path = str(Path(md_path).resolve())
    with open(md_path, "r", encoding="utf-8") as f:
        markdown = f.read()
    md_dir = str(Path(md_path).parent)

    # 解析
    segments, meta = parse_markdown_to_segments(markdown)

    # 提取标题
    title = extract_title(meta, segments, md_path)

    # 提取封面
    cover = extract_cover(meta, segments)

    # 编码图片（以 src 路径为键，与 build_paste_plan 的查找方式一致）
    image_results: Dict[str, dict] = {}
    for seg in segments:
        if seg.get("type") != "image":
            continue
        src = seg.get("source", "")
        if not src or src in image_results:
            continue
        try:
            if src.startswith("data:"):
                # data URI
                m = re.match(r"^data:([^;,]+)?(;base64)?,(.*)$", src, re.S)
                if m:
                    mime = (m.group(1) or "image/png").lower()
                    if m.group(2):
                        b64_data = re.sub(r"\s+", "", m.group(3))
                        image_results[src] = {
                            "ok": True,
                            "base64": b64_data,
                            "mime": mime,
                            "fileName": _guess_file_name(src),
                            "bytes": len(base64.b64decode(b64_data)),
                        }
            elif src.startswith("http"):
                # 远程图片 — 不支持（标记为失败）
                image_results[src] = {"ok": False, "error": "Remote images unsupported"}
            else:
                # 本地图片
                full_path = src if src.startswith("/") else os.path.join(md_dir, src)
                if os.path.isfile(full_path):
                    result = encode_image(full_path)
                    if result:
                        image_results[src] = {"ok": True, **result}
                    else:
                        image_results[src] = {"ok": False, "error": f"Failed to encode: {full_path}"}
                else:
                    image_results[src] = {"ok": False, "error": f"Not found: {full_path}"}
        except Exception as e:
            image_results[src] = {"ok": False, "error": str(e)}

    # 构建 paste plan
    paste_plan = build_paste_plan(segments, image_results, cover_source=cover)

    # 提取图片 payload 列表
    image_payloads = []
    for op in paste_plan["plan"]:
        op_data = op.get("op", {})
        if op_data.get("type") == "image" and op_data.get("file", {}).get("base64"):
            image_payloads.append(
                {
                    "marker": op["marker"],
                    "base64": op_data["file"]["base64"],
                    "fileName": op_data["file"]["fileName"],
                    "mime": op_data["file"]["mime"],
                    "alt": op_data["file"].get("alt", ""),
                    "coverOnly": bool(op_data.get("coverOnly")),
                    "fallbackText": op_data.get("fallbackText", ""),
                    "source": op_data.get("source"),
                }
            )

    return {
        "title": title or "",
        "cover": cover or "",
        "html": paste_plan["html"],
        "plain": paste_plan["plain"],
        "blocks": paste_plan["blocks"],
        "plan": paste_plan["plan"],
        "markerPrefix": paste_plan["markerPrefix"],
        "images": image_payloads,
        "articleId": None,
    }


# ──────────────────────────── CLI 测试 ────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        path = sys.argv[1]
    else:
        # 找第一个 .md 文件
        candidates = [
            p
            for p in Path(".").rglob("*.md")
            if "node_modules" not in str(p)
            and ".claude" not in str(p)
            and "docs" not in str(p)
            and "CLAUDE" not in str(p)
        ]
        path = str(candidates[0]) if candidates else None

    if path:
        p = build_payload(path)
        print(f"Title: {p['title']}")
        print(f"Blocks: {len(p['blocks'])}")
        print(f"Images: {len(p['images'])}")
        print(f"Plan items: {len(p['plan'])}")
        print(f"HTML length: {len(p['html'])}")
    else:
        print("No .md files found")
