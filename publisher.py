"""Twitter 发布逻辑 + 线程安全发布队列 + 队列持久化"""

import json
import re
import subprocess
import threading
import random
import time
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Tuple, Optional

from config import get_output_root, slugify


# ====================== 单条推文发布 ======================

def post_single_tweet(text: str, imgs: List[str], reply_to_id: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    """Post one tweet. Returns (ok, url_or_error, tweet_id)."""
    cmd = ["twitter", "post", text]
    for img in imgs[:4]:
        if img and Path(img).exists():
            cmd += ["-i", img]
    if reply_to_id:
        cmd += ["--reply-to", reply_to_id]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        out = (r.stdout + r.stderr).strip()
        if r.returncode != 0:
            return False, out, None
        url_match = re.search(r'https://x\.com/[^/\s]+/status/(\d+)', out)
        if url_match:
            return True, url_match.group(0), url_match.group(1)
        id_match = re.search(r'status/(\d+)', out)
        if id_match:
            return True, f"https://x.com/i/web/status/{id_match.group(1)}", id_match.group(1)
        return True, out, None
    except Exception as e:
        return False, str(e), None


# ====================== 文本处理 ======================

def strip_markdown(text: str) -> str:
    """Remove Markdown formatting, keep plain text for tweet posting."""
    text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)       # images
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)   # [link](url) -> link
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)  # # headers
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)           # **bold**
    text = re.sub(r'\*(.+?)\*', r'\1', text)                # *italic*
    text = re.sub(r'__(.+?)__', r'\1', text)                # __bold__
    text = re.sub(r'_(.+?)_', r'\1', text)                  # _italic_
    text = re.sub(r'~~(.+?)~~', r'\1', text)                # ~~strikethrough~~
    text = re.sub(r'`([^`]+)`', r'\1', text)                # `code`
    text = re.sub(r'```[\s\S]*?```', '', text)              # code blocks
    text = re.sub(r'^>\s?', '', text, flags=re.MULTILINE)   # > blockquote
    text = re.sub(r'^[-*+]\s+', '· ', text, flags=re.MULTILINE)  # bullet -> ·
    text = re.sub(r'^\d+\.\s+', '', text, flags=re.MULTILINE)    # numbered list
    text = re.sub(r'^---+$', '', text, flags=re.MULTILINE)  # horizontal rule
    text = re.sub(r'\n{3,}', '\n\n', text)                  # excessive newlines
    return text.strip()


def split_into_chunks(polished_md: str, image_roles: List[str]) -> List[Tuple[str, List[str]]]:
    """Split polished markdown into tweet-sized chunks with image assignment."""
    clean_text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', polished_md)
    parts = re.split(r'\n(?=## )', clean_text.strip())

    prefix_reserve = 10

    chunks = []
    current = ""
    for p in parts:
        if len(current) + len(p) > (150 - prefix_reserve) and current:
            chunks.append(current.strip())
            current = p
        else:
            current += "\n\n" + p if current else p
    if current.strip():
        chunks.append(current.strip())

    if not chunks:
        chunks = [polished_md[:(150 - prefix_reserve)]]

    result = []
    img_idx = 0
    for i, ch in enumerate(chunks):
        assigned = []
        if i == 0 and image_roles:
            assigned.append(image_roles[0])
            img_idx = 1
        while img_idx < len(image_roles) and len(assigned) < 2:
            assigned.append(image_roles[img_idx])
            img_idx += 1

        plain = strip_markdown(ch)
        if len(chunks) > 1:
            prefix = f"【{i+1}/{len(chunks)}】 "
            result.append((prefix + plain, assigned))
        else:
            result.append((plain, assigned))
    return result


# ====================== 队列持久化 ======================

def _queue_path(archive_dir: Path, title: str) -> Path:
    slug = slugify(title)
    return archive_dir / f"{slug}_queue.json"


def save_queue_json(archive_dir: Path, title: str, chunks_state: List[Dict]):
    """Save queue state to disk for crash recovery."""
    data = {
        "title": title,
        "created_at": datetime.now().isoformat(),
        "chunks": chunks_state,
    }
    path = _queue_path(archive_dir, title)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_queue_json(path: Path) -> Optional[Dict]:
    """Load queue state from disk."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def scan_incomplete_queues(root: Path = None) -> List[Dict]:
    """Scan for incomplete queue files. Returns list of {path, title, sent, total}."""
    if root is None:
        root = get_output_root()
    results = []
    for qf in root.rglob("*_queue.json"):
        data = load_queue_json(qf)
        if not data or "chunks" not in data:
            continue
        chunks = data["chunks"]
        sent = sum(1 for c in chunks if c.get("status") == "sent")
        pending = sum(1 for c in chunks if c.get("status") in ("pending", "sending"))
        if pending > 0:
            results.append({
                "path": str(qf),
                "title": data.get("title", "未知"),
                "sent": sent,
                "total": len(chunks),
            })
    return results


def delete_queue_file(path: str):
    """Delete a completed queue file."""
    p = Path(path)
    if p.exists():
        p.unlink()


# ====================== 线程安全发布队列 ======================

class PostQueue:
    """Thread-safe posting queue with disk persistence."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {
            "status": None,      # None | "posting" | "done" | "failed"
            "step": 0,
            "total": 0,
            "wait_min": 0,
            "urls": [],
            "error": None,
        }
        self._archive_dir: Optional[Path] = None
        self._title: str = ""
        self._chunks_state: List[Dict] = []

    def get(self) -> dict:
        with self._lock:
            return dict(self._state)

    def _update(self, **kw):
        with self._lock:
            self._state.update(kw)

    def start(self, chunks: List[Tuple[str, List[str]]], archive_dir: Optional[Path] = None, title: str = ""):
        """Start background posting. archive_dir enables persistence."""
        self._archive_dir = archive_dir
        self._title = title
        # Initialize chunks_state for persistence
        self._chunks_state = [
            {"text": t, "imgs": imgs, "status": "pending"}
            for t, imgs in chunks
        ]
        self._update(status="posting", step=0, total=len(chunks), urls=[], error=None)
        if archive_dir:
            save_queue_json(archive_dir, title, self._chunks_state)
        t = threading.Thread(target=self._worker, args=(chunks,), daemon=True)
        t.start()

    def resume(self, queue_data: Dict, archive_dir: Path):
        """Resume from a saved queue file."""
        self._archive_dir = archive_dir
        self._title = queue_data.get("title", "")
        self._chunks_state = queue_data["chunks"]

        # Find the last sent tweet_id to use as parent_id
        parent_id = None
        for c in self._chunks_state:
            if c.get("status") == "sent" and c.get("tweet_id"):
                parent_id = c["tweet_id"]

        # Build chunks list from pending items, preserving original order
        chunks_to_post = []
        start_idx = None
        for i, c in enumerate(self._chunks_state):
            if c["status"] in ("pending", "sending"):
                if start_idx is None:
                    start_idx = i
                chunks_to_post.append((c["text"], c["imgs"]))

        if not chunks_to_post:
            return

        total = len(self._chunks_state)
        self._update(status="posting", step=start_idx or 0, total=total, urls=[], error=None)

        # Collect already-sent URLs
        sent_urls = [c["url"] for c in self._chunks_state if c.get("status") == "sent" and c.get("url")]
        with self._lock:
            self._state["urls"] = sent_urls

        t = threading.Thread(target=self._worker, args=(chunks_to_post, parent_id, start_idx), daemon=True)
        t.start()

    def _worker(self, chunks: List[Tuple[str, List[str]]], parent_id: Optional[str] = None, start_idx: int = 0):
        """Background worker: post chunks sequentially with random delays."""
        for idx_offset, (text, imgs) in enumerate(chunks):
            real_idx = start_idx + idx_offset
            self._update(step=real_idx + 1)

            # Mark as sending
            self._chunks_state[real_idx]["status"] = "sending"
            if self._archive_dir:
                save_queue_json(self._archive_dir, self._title, self._chunks_state)

            ok, result, tweet_id = post_single_tweet(text, imgs, parent_id)
            if ok:
                with self._lock:
                    self._state["urls"].append(result)
                parent_id = tweet_id
                self._chunks_state[real_idx].update(
                    status="sent", tweet_id=tweet_id, url=result
                )
            else:
                self._chunks_state[real_idx]["status"] = "failed"
                self._chunks_state[real_idx]["error"] = result
                self._update(status="failed", error=f"第{real_idx+1}条失败: {result}")
                if self._archive_dir:
                    save_queue_json(self._archive_dir, self._title, self._chunks_state)
                return

            if self._archive_dir:
                save_queue_json(self._archive_dir, self._title, self._chunks_state)

            # Random delay between posts (skip after last)
            if idx_offset < len(chunks) - 1:
                wait = random.randint(1, 10)
                self._update(wait_min=wait)
                time.sleep(wait * 60)

        self._update(status="done", step=len(self._chunks_state))
        # Delete queue file on completion
        if self._archive_dir:
            qpath = _queue_path(self._archive_dir, self._title)
            if qpath.exists():
                qpath.unlink()


# 模块级单例
post_queue = PostQueue()
