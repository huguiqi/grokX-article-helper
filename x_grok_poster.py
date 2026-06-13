#!/usr/bin/env python3
"""
Grok X 长文助手
- Web UI (Streamlit)
- 润色 -> 生成封面+配图 -> 发布到 X -> 保存 Markdown 归档
"""

import os
import re
import shutil
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
    publish_article_to_x,
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
    # ---- 清除 flag（在 widget 创建前检查，避免 StreamlitAPIException） ----
    if st.session_state.get("daily_clear"):
        for k in ("daily_text", "daily_bilingual", "daily_candidates",
                   "daily_selected_img", "daily_original_text", "daily_preview_show",
                   "daily_uploaded_name"):
            st.session_state.pop(k, None)
        st.session_state["daily_clear"] = False

    # ---- 发送结果提示（在 widget 创建前显示，避免被 rerun 清掉） ----
    if st.session_state.get("daily_post_ok"):
        st.success("发送成功！")
        st.session_state.pop("daily_post_ok", None)
    if st.session_state.get("daily_post_fail"):
        st.error("发送失败，请稍后重试")
        st.session_state.pop("daily_post_fail", None)

    from config import AI_API_KEY as _env_api_key
    api_key = _env_api_key
    provider_name = st.session_state.get("provider_name", "")
    style_hint = st.session_state.get("style_hint", "")

    def _apply_bilingual():
        """on_click 回调：在 widget 创建前将翻译结果写入 session_state"""
        text = st.session_state.get("daily_text", "")
        if not text:
            return
        try:
            # 保存原始中文（用于"清除翻译"恢复和重复翻译）
            st.session_state["daily_original_text"] = text
            result = translate_to_bilingual(api_key, text, provider_name)
            st.session_state["daily_bilingual"] = result
            st.session_state["daily_text"] = result
        except Exception as e:
            st.session_state["daily_error"] = str(e)

    # ---- 文本输入 ----
    text = st.text_area("写点什么", height=120, key="daily_text")
    _err = st.session_state.pop("daily_error", None)
    if _err:
        st.error(f"翻译失败：{_err}")
    char_count = len(text)
    if char_count > 150:
        st.warning(f"已输入 {char_count}/150 字，超出 150 字的部分发布时将被截断")
    else:
        st.caption(f"已输入 {char_count}/150 字")

    col1, col2 = st.columns(2)
    with col1:
        st.button("🌐 双语内容", disabled=not text or not api_key, on_click=_apply_bilingual)
    with col2:
        if st.session_state.get("daily_bilingual"):
            if st.button("清除翻译"):
                original = st.session_state.pop("daily_original_text", "")
                st.session_state.daily_bilingual = ""
                if original:
                    st.session_state["daily_text"] = original
                st.rerun()

    # ---- 配图区域 ----
    st.subheader("配图（可选）")
    img_prompt = st.text_input("图片描述", key="daily_img_prompt")

    col_gen, col_upload = st.columns(2)
    with col_gen:
        if st.button("🎨 生成配图", disabled=not img_prompt or not api_key):
            # 清除本地上传状态，避免干扰生成配图后的流程
            st.session_state.pop("daily_uploaded_name", None)
            with st.spinner("正在生成 2 张候选图..."):
                try:
                    imgs = generate_pair_images(api_key, img_prompt, style_hint, provider_name)
                    # 复制到 yyyy/img/ 持久化目录
                    img_dir = get_output_root() / str(datetime.now().year) / "img"
                    img_dir.mkdir(parents=True, exist_ok=True)
                    ts = datetime.now().strftime("%m%d_%H%M%S")
                    for i, img in enumerate(imgs):
                        src = img.get("local_path", "")
                        if src and Path(src).exists():
                            dest = img_dir / f"{ts}_{i}.png"
                            shutil.copy2(src, dest)
                            img["local_path"] = str(dest)
                    st.session_state.daily_candidates = imgs
                    st.session_state.daily_selected_img = None
                except Exception as e:
                    st.error(f"生成配图失败：{e}")
            st.rerun()
    with col_upload:
        uploaded_img = st.file_uploader("📁 选择本地图片", type=["png", "jpg", "jpeg", "webp"], accept_multiple_files=False)
        if uploaded_img:
            uploaded_name = uploaded_img.name
            if st.session_state.get("daily_uploaded_name") != uploaded_name:
                # 新上传的文件，保存到临时目录
                tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
                tmp_dir.mkdir(parents=True, exist_ok=True)
                suffix = Path(uploaded_name).suffix or ".png"
                saved_path = tmp_dir / f"daily_upload{suffix}"
                saved_path.write_bytes(uploaded_img.getbuffer())
                local_img = {"role": "local", "prompt": "", "url": "", "local_path": str(saved_path)}
                st.session_state.daily_candidates = [local_img]
                st.session_state.daily_selected_img = local_img
                st.session_state["daily_uploaded_name"] = uploaded_name
                st.rerun()
        else:
            # 用户清空了 file_uploader，也清除记录
            if st.session_state.get("daily_uploaded_name"):
                st.session_state.pop("daily_uploaded_name", None)
    if not api_key:
        st.warning("请先在侧边栏填写 API Key")
    elif not img_prompt:
        st.info("请输入图片描述后点击生成")

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

    # ---- 预览区（点击按钮后才展示文案+配图） ----
    preview_text = text
    selected_img = st.session_state.get("daily_selected_img")
    if st.button("👁 预览", key="daily_preview_btn"):
        st.session_state["daily_preview_show"] = True
        st.rerun()
    if st.session_state.get("daily_preview_show"):
        st.text_area("预览文案", value=preview_text, height=150, disabled=True, key="daily_preview")
        if selected_img:
            img_path = selected_img.get("local_path")
            if img_path and Path(img_path).exists():
                st.image(img_path, width=300)

    # ---- 发送 ----
    st.divider()
    confirmed = st.checkbox("确认发送到 X", key="daily_confirm")
    if st.button("📤 发送X", disabled=not confirmed or not text):
        final_text = preview_text[:150]
        img_paths = []
        if st.session_state.get("daily_selected_img"):
            p = st.session_state.daily_selected_img.get("local_path")
            if p and Path(p).exists():
                img_paths = [p]

        ok, url, _ = post_single_tweet(final_text, img_paths)
        if ok:
            st.session_state["daily_post_ok"] = True
            st.session_state["daily_clear"] = True
            st.rerun()
        else:
            print(f"[朋友圈发送失败] {url}")
            st.session_state["daily_post_fail"] = True
            st.rerun()


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

    uploaded = st.file_uploader("上传已有 .md 作为起点", type=["md"])
    if uploaded:
        content = uploaded.read().decode("utf-8")
        st.session_state["rough"] = content
        h1_match = re.match(r'^\s*#\s+(.+)', content)
        if h1_match:
            st.session_state["title"] = h1_match.group(1).strip()
        # 尝试在数据目录中找到原文件路径（用于后续直接发布，避免重复保存丢失图片）
        source_path = None
        for p in get_output_root().rglob(uploaded.name):
            source_path = str(p)
            break
        st.session_state["source_md_path"] = source_path

    rough = st.text_area(
        "草稿（Markdown 或纯文本都行，可以很粗）",
        value=st.session_state.get("rough", ""),
        height=220,
        placeholder="例如：我最近对比了 Claude 和 OpenClaw 写 PRD 的 token 消耗... 结论是 Claude 省但 OpenClaw 能干活...",
        key="rough",
    )

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
                st.session_state.article_editable_prompts = [p.copy() for p in st.session_state.image_prompts]
                st.session_state.images = []
                st.session_state.manual_img_count = 0
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

        # ---- 可编辑提示词列表 ----
        editable = st.session_state.get("article_editable_prompts", [])
        for i, p in enumerate(editable):
            role_label = p.get("role", f"img-{i}")
            editable[i]["prompt"] = st.text_input(
                f"**{role_label}** 提示词",
                value=p.get("prompt", ""),
                key=f"article_prompt_{i}",
            )

        if st.button("➕ 添加配图提示词", key="article_add_prompt"):
            editable.append({"role": f"illustration-{len(editable)}", "prompt": ""})
            st.session_state.article_editable_prompts = editable
            st.rerun()

        # ---- 生成 + 上传 ----
        gen_col, upload_col = st.columns(2)
        with gen_col:
            if st.button("🎨 生成封面 + 配图", disabled=not api_key):
                    try:
                        prompts = st.session_state.get("article_editable_prompts", [])
                        imgs = generate_all_images(api_key, prompts, style_hint, provider_name=provider_name)
                        st.session_state.images = imgs
                        st.success(f"已生成 {len([i for i in imgs if i['local_path']])} 张图片")
                        st.rerun()
                    except Exception as e:
                        st.error(f"生成图片失败：{e}")

        with upload_col:
            uploaded_article_img_top = st.file_uploader(
                "📁 选择本地图片",
                type=["png", "jpg", "jpeg", "webp"],
                accept_multiple_files=False,
                key="article_local_img_top"
            )
            if uploaded_article_img_top:
                upload_name = uploaded_article_img_top.name
                track_key = "article_uploaded_img_name"
                if st.session_state.get(track_key) != upload_name:
                    tmp_dir = Path(tempfile.gettempdir()) / "x_grok_poster"
                    tmp_dir.mkdir(parents=True, exist_ok=True)
                    suffix = Path(upload_name).suffix or ".png"
                    saved_path = tmp_dir / f"article_upload_{datetime.now().strftime('%H%M%S')}{suffix}"
                    saved_path.write_bytes(uploaded_article_img_top.getbuffer())

                    if "manual_img_count" not in st.session_state:
                        st.session_state.manual_img_count = 0
                    st.session_state.manual_img_count += 1
                    n = st.session_state.manual_img_count
                    local_img = {
                        "role": f"illustration-manual-{n}",
                        "prompt": "",
                        "url": "",
                        "local_path": str(saved_path)
                    }
                    st.session_state.images.append(local_img)
                    st.session_state[track_key] = upload_name
                    st.success(f"已添加本地图片 illustration-manual-{n}")
                    st.rerun()

        # ---- 已生成图片展示 + 单张重生成 ----
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

    # Step 4 — 有内容就显示（上传 MD 或 AI 润色均可）
    has_content = st.session_state.get("polished") or st.session_state.get("rough")
    if has_content:
        st.header("4. 预览 & 发布 / 存档")

        # 优先用润色后的内容，否则用原始草稿
        content_for_publish = st.session_state.get("polished") or st.session_state.get("rough", "")

        st.markdown("**最终文案预览（前 800 字）**")
        preview_text = content_for_publish[:800]
        st.code(preview_text, language="markdown")

        img_paths = [i["local_path"] for i in st.session_state.images if i.get("local_path")]

        # 检查是否已保存过（避免重复保存）
        saved_md_path = st.session_state.get("saved_md_path")
        already_saved = saved_md_path and Path(saved_md_path).exists()

        if st.button("💾 仅保存本地 Markdown + 图片（不发布）"):
            try:
                md_path = archive_to_markdown(
                    st.session_state.title,
                    content_for_publish,
                    st.session_state.images,
                    st.session_state.hashtags
                )
                st.session_state.saved_md_path = str(md_path)
                st.success(f"已保存到：{md_path}")
            except Exception as e:
                st.error(f"保存失败：{e}")

        st.divider()

        draft_clicked = st.button("📝 发长文章到X草稿箱")

        if draft_clicked:
            try:
                # 优先使用原始文件路径（从本地加载的 .md，避免重复保存丢失图片）
                source_path = st.session_state.get("source_md_path")
                if source_path and Path(source_path).exists():
                    md_path = Path(source_path)
                    st.info(f"使用原始文件：{md_path}")
                elif already_saved:
                    md_path = Path(saved_md_path)
                    st.info(f"使用已保存的文件：{md_path}")
                else:
                    md_path = archive_to_markdown(
                        st.session_state.title,
                        content_for_publish,
                        st.session_state.images,
                        st.session_state.hashtags,
                    )
                    st.session_state.saved_md_path = str(md_path)
                    st.info(f"文章已保存到：{md_path}")
            except Exception as e:
                st.error(f"保存 Markdown 失败：{e}")
                st.stop()

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

        if AI_API_KEY:
            masked = AI_API_KEY[:4] + "****" + AI_API_KEY[-4:] if len(AI_API_KEY) > 8 else "****"
            st.text_input("AI_API_KEY（来自 .env）", value=masked, disabled=True)
        else:
            st.warning("未检测到 .env 中的 AI_API_KEY，请先配置")
        api_key = AI_API_KEY
        if not api_key:
            st.warning("请填写 API Key 才能调用 AI 润色和生成图片")

        # 检查当前 provider 是否支持图片生成
        provider = get_provider(provider_name)
        try:
            provider.generate_image.__func__ is not type(provider).generate_image
        except AttributeError:
            pass

        DEFAULT_STYLE = "现代科技,插画风格，干净简洁，绿色主调，高对比度，适合技术博客和 X 平台展示"
        settings = load_settings()
        style_hint = st.text_area(
            "全局视觉风格提示（可选）",
            value=settings.get("style_hint", DEFAULT_STYLE),
            height=80
        )
        if style_hint != settings.get("style_hint", ""):
            settings["style_hint"] = style_hint
            save_settings(settings)

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
