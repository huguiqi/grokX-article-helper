"""AI 调用调度层 — 通过可插拔 Provider 调用各 AI 服务"""

import asyncio
import json
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional

import streamlit as st

from config import AI_API_KEY, AI_CHAT_MODEL, AI_IMAGE_MODEL, download_image
from providers import get_provider


# ====================== 翻译 / 双语 ======================

TRANSLATE_SYSTEM = """你是一位专业的中英互译专家。你的任务是将用户输入的中文内容翻译成自然流畅的英文。

翻译要求：
- 保持原文的语气和风格（口语化/正式/幽默等）
- 保留 emoji 不翻译
- 专有名词保持原文（如产品名、品牌名）
- #hashtag 中的中文也需要翻译为英文（如 #蓝V互关 → #BlueVFollowBack）
- 只返回翻译结果，不要解释

输出格式（严格遵守）：
{chinese_text}

{english_translation}

即：中文原文 + 空行 + 英文翻译，中间空一行。"""


def translate_to_bilingual(api_key: str, chinese_text: str, provider_name: str = None) -> str:
    """调用 AI 将中文翻译为英文，返回双语格式文本"""
    provider = get_provider(provider_name)
    model = AI_CHAT_MODEL or provider.default_chat_model() or None
    messages = [
        {"role": "system", "content": TRANSLATE_SYSTEM},
        {"role": "user", "content": chinese_text}
    ]
    result = provider.chat_completion(api_key, messages, model=model)
    return result


def format_bilingual(chinese: str, english: str) -> str:
    """固定双语输出模板"""
    return f"{chinese.strip()}\n\n{english.strip()}"


# ====================== 图片回退（claude-agent-sdk） ======================

def _extract_search_keywords(prompt: str, style_hint: str = "") -> str:
    """从图片提示词提取英文搜索关键词"""
    words = re.findall(r'[a-zA-Z]{3,}', style_hint or prompt)
    return " ".join(words[:3]) if words else "technology abstract modern"


async def _search_via_sdk(query: str) -> str:
    """通过 claude-code-sdk 搜索 Pexels，返回图片 URL"""
    try:
        from claude_code_sdk import query as sdk_query, ClaudeCodeOptions, AssistantMessage, ResultMessage, TextBlock
    except ImportError:
        return ""

    prompt = (
        f"Search pexels.com for '{query}' images. "
        f"Return ONLY a JSON array of 3 direct image URLs from images.pexels.com, "
        f"like: [\"https://images.pexels.com/photos/123/pexels-photo-123.jpeg?w=1200\"]. "
        f"No explanation, just the JSON array."
    )
    try:
        import os
        os.environ.pop("CLAUDECODE", None)
        options = ClaudeCodeOptions(
            allowed_tools=["WebSearch", "WebFetch", "Read"],
            permission_mode="bypassPermissions",
            max_turns=10,
        )
        output_text = ""
        async for message in sdk_query(prompt=prompt, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        output_text += block.text
            elif isinstance(message, ResultMessage) and message.result:
                output_text += message.result
        # 提取第一个图片 URL
        url_match = re.search(r'https://images\.pexels\.com/photos/[^"\s\]]+', output_text)
        if url_match:
            return url_match.group(0)
    except Exception:
        pass
    return ""


def _search_via_sdk_sync(query: str) -> str:
    """同步包装"""
    try:
        return asyncio.run(_search_via_sdk(query))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(_search_via_sdk(query))
        finally:
            loop.close()


def fallback_search_image(prompt: str, style_hint: str = "") -> dict:
    """通过 claude-agent-sdk 搜索图片，返回 {url, source} 或空 dict"""
    query = _extract_search_keywords(prompt, style_hint)
    url = _search_via_sdk_sync(query)
    if url:
        return {"url": url, "source": "claude-agent-sdk"}
    return {}


# ====================== 聊天补全（兼容旧接口） ======================

def xai_chat_completion(api_key: str, messages: List[Dict], model: str = None, temperature: float = 0.7) -> str:
    """兼容旧接口：调用当前 provider 的 chat completion"""
    provider = get_provider()
    return provider.chat_completion(api_key, messages, model=model or AI_CHAT_MODEL or None, temperature=temperature)


def xai_generate_image(api_key: str, prompt: str, model: str = None, aspect_ratio: str = "16:9") -> str:
    """兼容旧接口：调用当前 provider 的图片生成"""
    provider = get_provider()
    return provider.generate_image(api_key, prompt, model=model or AI_IMAGE_MODEL or None, aspect_ratio=aspect_ratio)


# ====================== Grok 润色 ======================

POLISH_SYSTEM = """你是 X（Twitter）长文内容专家，专为中文 AI/技术/生产力创作者服务。
你的风格参考以下真实案例（保持第一人称、个人经验、实用结论、列表/表格、讨论引导）：

案例1（结论先行 + 对比 + 推荐用法）：
"## 结论先说 😂 ... **最终结论**：**Claude 更省 token，OpenClaw 功能更强但也更能吃**！ ... 我目前的推荐用法 - **快速写文档** → **直接用 Claude** ... 欢迎讨论：你们平时是怎么搭配..."

案例2（工具对比 + 个人方案 + 总结）：
"我最近在搭建自己的 Agent 团队... 核心需求：**我扔一个任务 → 多个 Agent 自动分工 + 并行执行**。 ... 我目前采用的最佳方案 **主力框架**：**CrewAI** ... 没有完美的工具，只有最适合自己 workflow 的组合。"

要求：
- 保留用户原始意思、数据、个人语气，不要过度美化或改变事实
- 增加强 hook（前2句抓住注意力）、小标题、加粗关键点、emoji 适度使用
- 结尾加"欢迎讨论"式互动问题 + 3-8 个相关 #hashtag
- 输出结构清晰的 Markdown
- 建议 1 个宽屏封面 + 2-3 个配图位置，使用固定占位文件名：cover.png、illustration-1.png、illustration-2.png
- 图片提示词要详细、适合 AI 图片生成（现代科技感、干净、适合 X 展示，避免文字过多）
"""


def polish_with_grok(api_key: str, rough: str, title_hint: str, style_hint: str, provider_name: str = None) -> Dict:
    """调用 AI 润色，返回结构化结果"""
    user_prompt = f"""请根据下面"原始草稿"进行适度润色改写，输出 JSON。

原始草稿：
{rough}

标题提示：{title_hint or "（无）"}

全局视觉风格提示（请融入所有图片提示词）：{style_hint or "现代科技插画风格，干净简洁，蓝紫色主调，高对比度，适合技术博客和 X 平台展示"}

严格只返回一个 JSON 对象，不要多余解释：
{{
  "title": "润色后的标题（中文为主）",
  "polished_md": "完整润色后的 Markdown（包含 # 标题、## 小节、图片占位 ![封面](cover.png) 等，保留原意）",
  "suggested_hashtags": ["#AI", "#OpenClaw", "..."],
  "image_prompts": [
    {{"role": "cover", "prompt": "详细的封面图片提示词（宽屏 16:9，突出主题，科技感）"}},
    {{"role": "illustration-1", "prompt": "配图1提示词..."}},
    {{"role": "illustration-2", "prompt": "配图2提示词..."}}
  ]
}}
"""
    messages = [
        {"role": "system", "content": POLISH_SYSTEM},
        {"role": "user", "content": user_prompt}
    ]
    provider = get_provider(provider_name)
    model = AI_CHAT_MODEL or provider.default_chat_model() or None
    content = provider.chat_completion(api_key, messages, model=model)

    # 尝试提取 JSON
    json_match = re.search(r'\{[\s\S]*\}', content)
    if json_match:
        try:
            data = json.loads(json_match.group(0))
            if "polished_md" in data and "image_prompts" in data:
                return data
        except Exception:
            pass

    # 回退
    st.warning("AI 返回格式不是标准 JSON，已做降级处理。请手动调整。")
    return {
        "title": title_hint or "我的 X 长文",
        "polished_md": content[:8000],
        "suggested_hashtags": ["#AI", "#生产力", "#Agent"],
        "image_prompts": [
            {"role": "cover", "prompt": f"{style_hint or ''} 科技主题宽屏封面，现代简洁，适合 X 平台".strip()},
            {"role": "illustration-1", "prompt": f"{style_hint or ''} 技术架构或流程示意配图".strip()},
        ]
    }


# ====================== 图片生成 ======================

def generate_all_images(api_key: str, prompts: List[Dict], style_hint: str, provider_name: str = None) -> List[Dict]:
    """批量生成，返回 [{role, prompt, local_path, url}, ...]"""
    provider = get_provider(provider_name)
    results = []
    aspects = {
        "cover": "16:9",
        "illustration-1": "4:3",
        "illustration-2": "4:3",
        "illustration-3": "1:1",
    }
    for i, p in enumerate(prompts[:4]):
        role = p.get("role", f"img-{i}")
        base_prompt = p.get("prompt", "")
        full_prompt = f"{base_prompt}. {style_hint}".strip() if style_hint else base_prompt
        aspect = aspects.get(role, "4:3")

        with st.spinner(f"正在生成 {role} ..."):
            try:
                model = AI_IMAGE_MODEL or provider.default_image_model() or None
                url = provider.generate_image(api_key, full_prompt, model=model, aspect_ratio=aspect)
                if url and url.startswith("http"):
                    tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
                    tmp_dir.mkdir(exist_ok=True)
                    local = tmp_dir / f"{role}_{datetime.now().strftime('%H%M%S')}.png"
                    download_image(url, local)
                    results.append({
                        "role": role,
                        "prompt": full_prompt,
                        "url": url,
                        "local_path": str(local)
                    })
                else:
                    results.append({"role": role, "prompt": full_prompt, "url": "", "local_path": ""})
            except NotImplementedError:
                # Provider 不支持图片生成，通过 claude-agent-sdk 搜索免费图库
                with st.spinner(f"正在通过备用方案获取 {role} ..."):
                    fb = fallback_search_image(full_prompt, style_hint)
                if fb.get("url"):
                    tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
                    tmp_dir.mkdir(exist_ok=True)
                    local = tmp_dir / f"{role}_{datetime.now().strftime('%H%M%S')}.jpg"
                    download_image(fb["url"], local)
                    results.append({"role": role, "prompt": full_prompt, "url": fb["url"], "local_path": str(local)})
                    st.info(f"{provider.name} 不支持图片生成，已通过 {fb['source']} 获取 {role}")
                else:
                    st.warning(f"{provider.name} 不支持图片生成，备用方案也失败，跳过 {role}")
                    results.append({"role": role, "prompt": full_prompt, "url": "", "local_path": ""})
            except Exception as e:
                st.error(f"生成 {role} 失败: {e}")
                results.append({"role": role, "prompt": full_prompt, "url": "", "local_path": ""})
    return results


def generate_pair_images(api_key: str, prompt: str, style_hint: str, provider_name: str = None) -> List[Dict]:
    """生成 2 张候选图片，返回 [{role, prompt, local_path, url}, ...]"""
    provider = get_provider(provider_name)
    results = []
    full_prompt = f"{prompt}. {style_hint}".strip() if style_hint else prompt
    for i in range(2):
        try:
            model = AI_IMAGE_MODEL or provider.default_image_model() or None
            url = provider.generate_image(api_key, full_prompt, model=model, aspect_ratio="1:1")
            if url and url.startswith("http"):
                tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
                tmp_dir.mkdir(exist_ok=True)
                local = tmp_dir / f"daily_{i}_{datetime.now().strftime('%H%M%S')}.png"
                download_image(url, local)
                results.append({"role": f"candidate-{i+1}", "prompt": full_prompt, "url": url, "local_path": str(local)})
                continue
        except NotImplementedError:
            pass
        except Exception:
            pass
        # 回退
        fb = fallback_search_image(full_prompt, style_hint)
        if fb.get("url"):
            tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
            tmp_dir.mkdir(exist_ok=True)
            local = tmp_dir / f"daily_{i}_{datetime.now().strftime('%H%M%S')}.jpg"
            download_image(fb["url"], local)
            results.append({"role": f"candidate-{i+1}", "prompt": full_prompt, "url": fb["url"], "local_path": str(local)})
        else:
            results.append({"role": f"candidate-{i+1}", "prompt": full_prompt, "url": "", "local_path": ""})
    return results
