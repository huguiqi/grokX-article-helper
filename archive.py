"""Markdown 归档逻辑"""

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import List, Dict

from config import get_output_root, slugify, ensure_dir


def archive_to_markdown(
    title: str,
    polished_md: str,
    images: List[Dict],
    hashtags: List[str],
    tweet_urls: List[str] = None,
) -> Path:
    """Archive polished markdown + images to date-based folder.

    Args:
        tweet_urls: If provided, append tweet links to the archive.
                    The last URL is marked as thread entry.
    """
    now = datetime.now()
    date_folder = get_output_root() / f"{now.year}" / f"{now.month}.{now.day}"
    ensure_dir(date_folder)

    slug = slugify(title)
    md_path = date_folder / f"{slug}.md"

    image_map = {}
    for img in images:
        if not img.get("local_path") or not Path(img["local_path"]).exists():
            continue
        role = img["role"]
        ext = Path(img["local_path"]).suffix or ".png"
        final_name = f"{slug}_{role}{ext}"
        final_path = date_folder / final_name
        shutil.copy2(img["local_path"], final_path)
        image_map[role] = final_name

    final_md = polished_md
    for role, fname in image_map.items():
        final_md = re.sub(
            rf'!\[[^\]]*\]\(\s*{role}\.png\s*\)',
            f'![{role}]({fname})',
            final_md
        )
        final_md = re.sub(
            rf'!\[[^\]]*\]\(\s*{role}\s*\)',
            f'![{role}]({fname})',
            final_md
        )

    if hashtags:
        tag_line = " ".join(hashtags)
        if tag_line not in final_md:
            final_md = final_md.rstrip() + f"\n\n{tag_line}\n"

    # Append tweet links if provided
    if tweet_urls:
        final_md = final_md.rstrip() + "\n\n---\n## 推文链接\n"
        for i, url in enumerate(tweet_urls):
            if i == len(tweet_urls) - 1 and len(tweet_urls) > 1:
                final_md += f"- **线程入口**：{url}\n"
            else:
                final_md += f"- {url}\n"

    md_path.write_text(final_md, encoding="utf-8")
    return md_path
