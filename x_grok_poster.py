#!/usr/bin/env python3
"""
Grok X 长文助手
- Web UI (Streamlit)
- 润色 -> 生成封面+配图 -> 发布到 X -> 保存 Markdown 归档
"""

import os
import re
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path

import streamlit as st


def pick_folder():
    """打开 macOS 原生目录选择器（延迟导入 tkinter）"""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except ImportError:
        st.error("当前 Python 环境不支持 tkinter，请手动输入路径")
        return ""
    root = tk.Tk()
    root.withdraw()
    root.wm_attributes('-topmost', 1)
    folder = filedialog.askdirectory(master=root)
    root.destroy()
    return folder

from config import (
    check_twitter_cli, get_twitter_version, download_image,
    get_output_root, save_settings, load_settings,
    AI_API_KEY, AI_PROVIDER, AI_CHAT_MODEL, AI_IMAGE_MODEL,
)
from providers import get_provider, SUPPORTED_PROVIDERS
from xai_api import (
    xai_chat_completion, xai_generate_image,
    polish_with_grok, generate_all_images,
    translate_to_bilingual, generate_pair_images,
)
from publisher import (
    post_single_tweet, split_into_chunks,
    post_queue, scan_incomplete_queues, delete_queue_file,
)
from archive import archive_to_markdown

# ====================== 页面配置 ======================

st.set_page_config(
    page_title="Grok X 长文助手",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)


# ====================== 发布队列 Fragment ======================

@st.fragment(run_every=10)
def _posting_progress_fragment():
    """局部刷新的发布进度展示，不触发全页面重跑。"""
    s = post_queue.get()
    if s["status"] not in ("posting",):
        st.rerun()
        return
    with st.spinner(f"正在发布线程… 第 {s['step']}/{s['total']} 条已发送，下一条等待 {s['wait_min']} 分钟"):
        time.sleep(8)


# ====================== UI ======================

def daily_moments_ui():
    """日常朋友圈：短推文 + 双语 + 配图 + 直接发推"""
    api_key = st.session_state.get("api_key", "")
    provider_name = st.session_state.get("provider_name", "")
    style_hint = st.session_state.get("style_hint", "")

    # ---- 文本输入 ----
    text = st.text_area("写点什么（≤140 中文字）", height=120, max_chars=140, key="daily_text")
    char_count = len(text)
    st.caption(f"已输入 {char_count}/140 字")

    col1, col2 = st.columns(2)
    with col1:
        if st.button("🌐 双语内容", disabled=not text):
            with st.spinner("翻译中..."):
                try:
                    result = translate_to_bilingual(api_key, text, provider_name)
                    st.session_state.daily_bilingual = result
                except Exception as e:
                    st.error(f"翻译失败：{e}")
            st.rerun()
    with col2:
        if st.session_state.get("daily_bilingual"):
            if st.button("清除翻译"):
                st.session_state.daily_bilingual = ""
                st.rerun()

    # ---- 配图区域 ----
    st.subheader("配图（可选）")
    img_prompt = st.text_input("图片描述", key="daily_img_prompt")

    if st.button("🎨 生成配图", disabled=not img_prompt or not api_key):
        with st.spinner("正在生成 2 张候选图..."):
            try:
                imgs = generate_pair_images(api_key, img_prompt, style_hint, provider_name)
                st.session_state.daily_candidates = imgs
                st.session_state.daily_selected_img = None
            except Exception as e:
                st.error(f"生成配图失败：{e}")
        st.rerun()

    if st.session_state.get("daily_candidates"):
        st.markdown("**选择一张配图：**")
        cols = st.columns(2)
        for i, img in enumerate(st.session_state.daily_candidates):
            with cols[i]:
                if img.get("local_path") and Path(img["local_path"]).exists():
                    st.image(img["local_path"], use_column_width=True)
                    is_selected = (
                        st.session_state.get("daily_selected_img")
                        and st.session_state.daily_selected_img.get("local_path") == img["local_path"]
                    )
                    label = f"✅ 已选图片 {i+1}" if is_selected else f"选择图片 {i+1}"
                    if st.button(label, key=f"daily_select_{i}"):
                        st.session_state.daily_selected_img = img
                        st.rerun()
                else:
                    st.warning(f"图片 {i+1} 生成失败")

    # ---- 预览区 ----
    st.subheader("预览")
    preview_text = text
    if st.session_state.get("daily_bilingual"):
        preview_text = f"{text}\n\n{st.session_state.daily_bilingual}"

    st.text_area("预览文案", value=preview_text, height=150, disabled=True, key="daily_preview")

    if st.session_state.get("daily_selected_img"):
        img_path = st.session_state.daily_selected_img.get("local_path")
        if img_path and Path(img_path).exists():
            st.image(img_path, width=300)

    # ---- 发送 ----
    st.divider()
    confirmed = st.checkbox("确认发送到 X", key="daily_confirm")
    if st.button("📤 发送X", disabled=not confirmed or not text):
        final_text = preview_text
        img_paths = []
        if st.session_state.get("daily_selected_img"):
            p = st.session_state.daily_selected_img.get("local_path")
            if p and Path(p).exists():
                img_paths = [p]

        ok, url, _ = post_single_tweet(final_text, img_paths)
        if ok:
            st.success(f"发送成功！{url}")
            # 清空状态
            st.session_state.daily_text = ""
            st.session_state.daily_bilingual = ""
            st.session_state.daily_candidates = []
            st.session_state.daily_selected_img = None
        else:
            st.error(f"发送失败：{url}")


def article_ui():
    """文章长文：原有 Step 1-4 + 发布队列检测"""
    api_key = st.session_state.get("api_key", "")
    provider_name = st.session_state.get("provider_name", "")
    style_hint = st.session_state.get("style_hint", "")

    # ---- 检测未完成的发布队列 ----
    incomplete = scan_incomplete_queues()
    if incomplete:
        for qi, q in enumerate(incomplete):
            st.warning(
                f"检测到未完成的发布任务：**{q['title']}**（已发 {q['sent']}/{q['total']} 条）"
            )
            c1, c2 = st.columns(2)
            with c1:
                if st.button("▶️ 继续发布", key=f"resume_{qi}"):
                    from publisher import load_queue_json
                    queue_data = load_queue_json(Path(q["path"]))
                    if queue_data:
                        archive_dir = Path(q["path"]).parent
                        post_queue.resume(queue_data, archive_dir)
                        st.rerun()
            with c2:
                if st.button("🗑️ 丢弃", key=f"discard_{qi}"):
                    delete_queue_file(q["path"])
                    st.rerun()

    # Session init
    if "polished" not in st.session_state:
        st.session_state.polished = None
    if "images" not in st.session_state:
        st.session_state.images = []
    if "title" not in st.session_state:
        st.session_state.title = ""
    if "hashtags" not in st.session_state:
        st.session_state.hashtags = []
    if "posted_urls" not in st.session_state:
        st.session_state.posted_urls = []
    if "image_prompts" not in st.session_state:
        st.session_state.image_prompts = []

    # Step 1
    st.header("1. 输入文章大致内容")

    rough = st.text_area(
        "草稿（Markdown 或纯文本都行，可以很粗）",
        value=st.session_state.get("rough", ""),
        height=220,
        placeholder="例如：我最近对比了 Claude 和 OpenClaw 写 PRD 的 token 消耗... 结论是 Claude 省但 OpenClaw 能干活...",
        key="rough",
    )

    uploaded = st.file_uploader("上传已有 .md 作为起点", type=["md"])
    if uploaded:
        content = uploaded.read().decode("utf-8")
        st.session_state.rough = content
        h1_match = re.match(r'^\s*#\s+(.+)', content)
        if h1_match:
            st.session_state.title = h1_match.group(1).strip()

    title_hint = st.text_input("标题提示（可选）", value=st.session_state.get("title", ""))

    # 润色
    if st.button("✨ 使用 AI 润色改写", type="primary", disabled=not api_key):
        if not rough:
            st.error("请先输入草稿")
        else:
            try:
                with st.spinner(f"{provider_name} 正在润色..."):
                    result = polish_with_grok(
                        api_key,
                        rough,
                        title_hint,
                        style_hint,
                        provider_name=provider_name,
                    )
                st.session_state.polished = result.get("polished_md", "")
                st.session_state.title = result.get("title", title_hint)
                st.session_state.hashtags = result.get("suggested_hashtags", [])
                st.session_state.image_prompts = result.get("image_prompts", [])
                st.session_state.images = []
                st.success("润色完成！请在下方编辑并继续生成图片。")
                st.rerun()
            except Exception as e:
                st.error(f"润色失败：{e}")

    # Step 2
    if st.session_state.get("polished"):
        st.header("2. 润色结果（可编辑）")

        new_title = st.text_input("标题", value=st.session_state.title)
        st.session_state.title = new_title

        edited = st.text_area(
            "正文（支持 Markdown，可直接修改）",
            value=st.session_state.polished,
            height=300
        )
        st.session_state.polished = edited

        tags_str = st.text_input(
            "Hashtags（空格分隔）",
            value=" ".join(st.session_state.hashtags)
        )
        st.session_state.hashtags = [t for t in tags_str.split() if t.startswith("#")]

        st.markdown("**预览**")
        st.markdown(st.session_state.polished[:2000] + ("..." if len(st.session_state.polished) > 2000 else ""))

    # Step 3
    if st.session_state.get("polished") and st.session_state.get("image_prompts"):
        st.header("3. 生成封面和配图")

        # 检查 provider 是否支持图片生成
        provider = get_provider(provider_name)
        from providers.base import AIProvider
        supports_image = type(provider).generate_image is not AIProvider.generate_image

        if not supports_image:
            try:
                import claude_agent_sdk
                has_sdk = True
            except ImportError:
                has_sdk = False
            if has_sdk:
                st.info(f"{provider_name} 不支持图片生成，将自动通过 Claude Agent SDK 搜索免费图库获取配图")
            else:
                st.warning(f"{provider_name} 不支持图片生成。安装 claude-code-sdk 可自动获取配图，或切换到 xAI/chatGPT/miniMax")

        if st.button("🎨 生成封面 + 配图", disabled=not api_key):
                try:
                    prompts = st.session_state.get("image_prompts", [])
                    imgs = generate_all_images(api_key, prompts, style_hint, provider_name=provider_name)
                    st.session_state.images = imgs
                    st.success(f"已生成 {len([i for i in imgs if i['local_path']])} 张图片")
                    st.rerun()
                except Exception as e:
                    st.error(f"生成图片失败：{e}")

        if st.session_state.get("images"):
            st.subheader("已生成的图片（可修改提示词后单张重生成）")
            cols = st.columns(min(4, len(st.session_state.images)))
            for idx, img in enumerate(st.session_state.images):
                with cols[idx % 4]:
                    role = img.get("role", f"img{idx}")
                    st.markdown(f"**{role}**")
                    if img.get("local_path") and Path(img["local_path"]).exists():
                        st.image(img["local_path"], use_column_width=True)
                    else:
                        st.warning("生成失败")

                    new_prompt = st.text_input(
                        f"提示词 {idx+1}",
                        value=img.get("prompt", ""),
                        key=f"prompt_{idx}"
                    )
                    if st.button(f"🔄 重生成 {role}", key=f"regen_{idx}"):
                        try:
                            aspect = "16:9" if "cover" in role else "4:3"
                            url = xai_generate_image(api_key, new_prompt, aspect_ratio=aspect)
                            if url and url.startswith("http"):
                                tmp = Path(tempfile.gettempdir()) / "x_grok_poster" / f"{role}_regen.png"
                                download_image(url, tmp)
                                st.session_state.images[idx]["prompt"] = new_prompt
                                st.session_state.images[idx]["url"] = url
                                st.session_state.images[idx]["local_path"] = str(tmp)
                                st.rerun()
                        except Exception as e:
                            st.error(f"重生成失败: {e}")

    # Step 4
    if st.session_state.get("polished") and st.session_state.get("images"):
        st.header("4. 预览 & 发布 / 存档")

        use_thread = st.checkbox("发布为线程（推荐，图片分散在各条推文）", value=True)

        st.markdown("**最终文案预览（前 800 字）**")
        preview_text = st.session_state.polished[:800]
        st.code(preview_text, language="markdown")

        img_paths = [i["local_path"] for i in st.session_state.images if i.get("local_path")]

        if st.button("💾 仅保存本地 Markdown + 图片（不发布）"):
            try:
                md_path = archive_to_markdown(
                    st.session_state.title,
                    st.session_state.polished,
                    st.session_state.images,
                    st.session_state.hashtags
                )
                st.success(f"已保存到：{md_path}")
                if st.button("📂 打开文件夹"):
                    subprocess.call(["open", str(md_path.parent)])
            except Exception as e:
                st.error(f"保存失败：{e}")

        st.divider()

        confirmed = st.checkbox("✅ 我已仔细预览文案和图片，确认要用我的 X 账号发布（不可撤销）")
        queue_state = post_queue.get()
        is_posting = queue_state["status"] == "posting"

        # 两个按钮并排
        btn_col1, btn_col2 = st.columns(2)
        with btn_col1:
            publish_clicked = st.button("📤 分段发布X并存档", disabled=not confirmed or not check_twitter_cli() or is_posting)
        with btn_col2:
            draft_clicked = st.button("📝 发长文章到X草稿箱")

        if draft_clicked:
            st.info("敬请期待！！")

        if publish_clicked:
            try:
                full_text = st.session_state.polished
                if st.session_state.hashtags:
                    full_text += "\n\n" + " ".join(st.session_state.hashtags)

                if use_thread:
                    chunks = split_into_chunks(full_text, [i["role"] for i in st.session_state.images])
                    role_to_path = {i["role"]: i["local_path"] for i in st.session_state.images}
                    chunk_with_paths = []
                    for txt, roles in chunks:
                        paths = [role_to_path.get(r, "") for r in roles]
                        chunk_with_paths.append((txt, paths))

                    # Compute archive dir for persistence
                    now = datetime.now()
                    archive_dir = get_output_root() / f"{now.year}" / f"{now.month}.{now.day}"
                    archive_dir.mkdir(parents=True, exist_ok=True)

                    post_queue.start(chunk_with_paths, archive_dir=archive_dir, title=st.session_state.title)
                    st.rerun()
                else:
                    ok, url, _ = post_single_tweet(full_text, img_paths)
                    if ok:
                        st.session_state.posted_urls = [url]
                        st.success("发布成功！")
                        st.markdown(f"- {url}")
                        md_path = archive_to_markdown(
                            st.session_state.title, st.session_state.polished,
                            st.session_state.images, st.session_state.hashtags,
                            tweet_urls=[url],
                        )
                        st.info(f"已自动归档到：{md_path}")
                    else:
                        st.error(f"发布失败：{url}")
            except Exception as e:
                st.error(f"发布过程出错：{e}")

        # 发布队列状态
        if queue_state["status"] == "posting":
            _posting_progress_fragment()
        elif queue_state["status"] == "done":
            urls = queue_state["urls"]
            st.session_state.posted_urls = urls
            st.success(f"线程发布完成！共 {len(urls)} 条")
            for u in urls:
                st.markdown(f"- {u}")
            if urls:
                st.info(f"线程入口（从这里看完整线程）：{urls[-1]}")
            md_path = archive_to_markdown(
                st.session_state.title, st.session_state.polished,
                st.session_state.images, st.session_state.hashtags,
                tweet_urls=urls,
            )
            st.info(f"已自动归档到：{md_path}")
            if st.button("📂 打开归档文件夹"):
                subprocess.call(["open", str(md_path.parent)])
            # Reset queue state
            post_queue._update(status=None)
        elif queue_state["status"] == "failed":
            err = queue_state.get("error", "未知错误")
            urls = queue_state.get("urls", [])
            st.error(f"发布中断：{err}")
            if urls:
                st.warning(f"已成功发送 {len(urls)} 条：")
                for u in urls:
                    st.markdown(f"- {u}")
            if st.button("🔄 重置发布状态"):
                post_queue._update(status=None)
                st.rerun()

    if st.session_state.get("posted_urls"):
        st.success("上次发布链接：")
        for u in st.session_state.posted_urls:
            st.markdown(f"• {u}")


def main():
    st.title("🚀 Grok X 长文助手")
    st.caption("输入草稿 → AI 润色 → 生成封面&配图 → 发布 X → 自动归档 Markdown（兼容你的历史推文格式）")

    # Sidebar（两个 tab 共享配置）
    with st.sidebar:
        st.header("⚙️ 配置")

        # ---- 数据目录 ----
        current_dir = str(get_output_root())
        col_input, col_btn = st.columns([4, 1])
        with col_input:
            data_dir = st.text_input("数据目录", value=current_dir, help="归档文件保存位置")
        with col_btn:
            st.write("")
            st.write("")
            if st.button("📂"):
                picked = pick_folder()
                if picked:
                    settings = load_settings()
                    settings["data_dir"] = picked
                    save_settings(settings)
                    st.rerun()
        if data_dir != current_dir:
            settings = load_settings()
            settings["data_dir"] = data_dir
            save_settings(settings)
            st.rerun()

        st.divider()

        # ---- AI Provider 选择 ----
        provider_name = st.selectbox(
            "AI Provider",
            options=SUPPORTED_PROVIDERS,
            index=SUPPORTED_PROVIDERS.index(AI_PROVIDER) if AI_PROVIDER in SUPPORTED_PROVIDERS else 0,
            help="选择 AI 服务提供商"
        )

        api_key = st.text_input(
            "AI_API_KEY",
            value=AI_API_KEY,
            type="password",
            help="各 provider 的 API Key，兼容旧 XAI_API_KEY"
        )
        if not api_key:
            st.warning("请填写 API Key 才能调用 AI 润色和生成图片")

        # 检查当前 provider 是否支持图片生成
        provider = get_provider(provider_name)
        try:
            provider.generate_image.__func__ is not type(provider).generate_image
        except AttributeError:
            pass

        style_hint = st.text_area(
            "全局视觉风格提示（可选）",
            value="现代科技插画风格，干净简洁，蓝紫色主调，高对比度，适合技术博客和 X 平台展示",
            height=80
        )

        st.divider()
        st.subheader("环境检查")
        if st.button("检查 twitter-cli"):
            ok = check_twitter_cli()
            ver = get_twitter_version()
            if ok:
                st.success(f"twitter-cli 可用：{ver}")
            else:
                st.error(f"未检测到 twitter-cli：{ver}\n请先 pipx install twitter-cli 并配置 Cookie")

        if st.button("测试 AI 连接"):
            if api_key:
                try:
                    content = provider.chat_completion(api_key, [{"role": "user", "content": "只回复两个字母 OK"}])
                    st.success(f"连接成功：{content[:40]}")
                except Exception as e:
                    st.error(f"连接失败：{e}")
            else:
                st.error("缺少 API Key")

        if st.button("检查图片备用工具"):
            try:
                import claude_agent_sdk
                st.success("Claude Agent SDK 可用，图片回退功能就绪")
            except ImportError:
                st.warning("未安装 claude-code-sdk，不支持图片的 Provider 将无法自动获取配图")

        st.divider()
        st.markdown("**使用提示**")
        st.markdown("- 发布操作会真实发到你的 X 账号，请先预览")
        st.markdown("- 图片最多 4 张（X 单帖限制）")
        st.markdown("- 线程模式会自动加 1/N 序号")
        st.markdown("- 线程发布完成后，从**最后一条推文**查看完整线程")

    # 将 sidebar 配置存入 session_state 供 tab 函数使用
    st.session_state.api_key = api_key
    st.session_state.style_hint = style_hint
    st.session_state.provider_name = provider_name

    # ---- Tab 布局 ----
    tab_daily, tab_article = st.tabs(["💬 日常朋友圈", "📝 文章长文"])

    with tab_daily:
        daily_moments_ui()

    with tab_article:
        article_ui()

    st.divider()
    st.caption("工具仅在本地运行，API Key 和推文内容不会上传到任何第三方。数据只保存在你的数据目录下。")


if __name__ == "__main__":
    main()
