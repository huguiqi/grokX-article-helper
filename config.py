"""配置、常量、工具函数"""

import json as _json
import os
import re
import subprocess
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
SETTINGS_FILE = BASE_DIR / "settings.json"

# AI Provider 配置（兼容旧 XAI_ 前缀）
AI_PROVIDER = os.getenv("AI_PROVIDER", "xAI")
AI_API_KEY = os.getenv("AI_API_KEY") or os.getenv("XAI_API_KEY", "")
AI_CHAT_MODEL = os.getenv("AI_CHAT_MODEL", "")
AI_IMAGE_MODEL = os.getenv("AI_IMAGE_MODEL", "")


def load_settings() -> dict:
    if SETTINGS_FILE.exists():
        return _json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    return {}


def save_settings(settings: dict):
    SETTINGS_FILE.write_text(_json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")


def get_output_root() -> Path:
    return Path(load_settings().get("data_dir", str(BASE_DIR)))


def slugify(text: str) -> str:
    """简单 slugify，支持中文"""
    text = text.strip()
    text = re.sub(r'[^\w\s\u4e00-\u9fff-]', '', text)
    text = re.sub(r'[\s]+', '-', text)
    return text[:60] or "untitled-post"


def ensure_dir(p: Path):
    p.mkdir(parents=True, exist_ok=True)


def download_image(url: str, dest: Path) -> Path:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def check_twitter_cli() -> bool:
    try:
        result = subprocess.run(["twitter", "--help"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0 or "post" in (result.stdout + result.stderr).lower()
    except Exception:
        return False


def get_twitter_version() -> str:
    try:
        r = subprocess.run(["twitter", "--version"], capture_output=True, text=True, timeout=5)
        return r.stdout.strip() or r.stderr.strip()
    except Exception as e:
        return f"未找到 ({e})"
