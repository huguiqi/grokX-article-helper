<div align="center">

# 🚀 Grok X 长文助手

**借助 Grok (xAI) 快速润色、生成封面+配图、发布到 X 并自动归档的 Web 工具**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![Streamlit](https://img.shields.io/badge/Streamlit-1.35+-FF4B4B.svg)](https://streamlit.io)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

</div>

---

## ✨ 功能亮点

| 功能 | 说明 |
|------|------|
| 💬 日常朋友圈 | 短推文（≤150字），支持中英双语翻译 + 配图，一键发送 |
| 📝 文章长文 | 草稿输入 → AI 润色 → 生成封面+配图 → 发布为线程 → 自动归档 |
| 📰 X Articles 草稿 | 一键将 Markdown 文章推送到 X Articles 编辑器草稿箱（需安装 Chrome 扩展） |
| 🎨 智能配图 | 支持 Grok Imagine / ChatGPT / DeepSeek 等多种 AI 生成图片 |
| 📦 自动归档 | 发布后自动保存 Markdown + 图片到本地，格式兼容历史推文 |
| 🔄 多 Provider | 支持 xAI / ChatGPT / DeepSeek / Anthropic / MiniMax |

---

## 📸 界面预览

### 📝 文章长文
![文章长文](img/preview-article.png)

### 💬 日常朋友圈
![日常朋友圈](img/preview-moment.png)

### 🎨 润色 + 配图
![润色配图](img/preview-polish.png)

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- macOS / Linux / Windows

### 安装

```bash
# 1. 克隆仓库
git clone https://github.com/你的用户名/grokX-article-helper.git
cd grokX-article-helper

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置 API Key
cp .env.example .env
# 编辑 .env 填入你的 AI_API_KEY
```

### 配置 twitter-cli（用于发帖）

```bash
# 安装
pipx install twitter-cli

# 配置认证（Cookie 方式）
# 1. 用 Cookie-Editor 浏览器插件导出 x.com 的 TWITTER_AUTH_TOKEN 和 TWITTER_CT0
# 2. 设为环境变量（写到 ~/.zshrc 或 ~/.bashrc）
export TWITTER_AUTH_TOKEN=你的值
export TWITTER_CT0=你的值

# 验证
twitter feed -n 1 --yaml
```

### 安装 Chrome 扩展（用于 X Articles 草稿推送）

```bash
# 扩展文件在项目 extension/ 目录下
# 1. Chrome 打开 chrome://extensions
# 2. 开启右上角「开发者模式」
# 3. 点击「加载已解压的扩展程序」
# 4. 选择项目下的 extension/ 目录
```

### 启动

```bash
streamlit run x_grok_poster.py
```

浏览器自动打开 http://localhost:8501

---

## 📖 使用流程

### 日常朋友圈（短推文）

1. 切换到 **💬 日常朋友圈** tab
2. 输入中文内容（≤150字，超出部分发送时自动截断）
3. （可选）点击「🌐 双语内容」生成中英双语版本（翻译会替换输入框内容，点击「清除翻译」可恢复原文）
4. （可选）输入图片描述，生成 2 张候选配图，选择 1 张
5. 点击「👁 预览」查看文案+配图 → 确认 → 点击「📤 发送X」

### 文章长文

1. 切换到 **📝 文章长文** tab
2. 粘贴草稿内容（支持 Markdown 或纯文本，也可从本地上传 .md 文件）
3. 点击「✨ 使用 AI 润色改写」→ 编辑润色结果
4. 点击「🎨 生成封面 + 配图」→ 可修改提示词重生成
5. 选择发布方式：
   - **发推文线程**：预览 → 确认发布 → 自动归档到 `2026/MM.DD/` 目录
   - **发到 X 草稿箱**：点击「📝 发长文章到X草稿箱」→ 在 Chrome 打开 x.com/compose/articles/new → 点击右上角「📥 载入文章」按钮 → 检查内容后手动 Publish

---

## 🏗️ 项目结构

```
.
├── x_grok_poster.py       # Streamlit UI 入口
├── xai_api.py             # AI 调用层（润色 + 翻译 + 图片生成 + 文章发布）
├── publisher.py            # Twitter 发布 + 线程队列
├── archive.py              # Markdown 归档
├── config.py               # 配置加载 + 工具函数
├── markdown_parser.py      # Markdown → X Articles payload 解析器
├── article_server.py       # X Articles HTTP 服务（端口 8765）
├── xpage.js                # X 页面注入引擎（Draft.js + React Fiber）
├── providers/              # AI Provider 抽象层
│   ├── base.py             # 基类
│   ├── xai.py              # xAI (Grok)
│   ├── chatgpt.py          # OpenAI ChatGPT
│   ├── deepseek.py         # DeepSeek
│   ├── anthropic.py        # Anthropic Claude
│   └── minimax.py          # MiniMax
├── extension/              # Chrome 扩展（X Articles 草稿推送）
│   ├── manifest.json       # 扩展清单
│   ├── content.js          # 内容脚本（注入按钮 + 拉取 payload）
│   └── background.js       # 后台脚本
├── img/                    # 项目图片资源
├── .env.example            # 环境变量模板
├── requirements.txt        # Python 依赖
└── LICENSE                 # MIT License
```

---

## ⚙️ 支持的 AI Provider

| Provider | 文字润色 | 图片生成 | 备注 |
|----------|---------|---------|------|
| xAI (Grok) | ✅ | ✅ | 默认，推荐 |
| ChatGPT | ✅ | ✅ | |
| DeepSeek | ✅ | ❌（自动回退） | 回退使用 Claude Agent SDK 搜图 |
| Anthropic | ✅ | ❌（自动回退） | 同上 |
| MiniMax | ✅ | ✅ | |

---

## ❓ 常见问题

- **`twitter: command not found`** → 确认 `pipx install twitter-cli` 成功，且执行了 `pipx ensurepath`
- **图片生成慢或失败** → 检查 API Key 额度 / 网络 / prompt 是否触发安全过滤
- **发帖报错** → 先单独运行 `twitter post "测试"` 确认认证有效
- **想换图片风格** → 在侧边栏修改「全局视觉风格提示」
- **Provider 不支持图片** → 自动通过 Claude Agent SDK 搜索免费图库获取配图
- **X Articles 扩展按钮不出现** → 确认在 `x.com/compose/articles` 或 `x.com/*/articles/edit/` 页面，刷新扩展
- **X Articles 提示无法连接** → 确认已点击「发长文章到X草稿箱」启动服务（端口 8765）

---

## 🙏 支持作者

如果这个工具对你有帮助，欢迎请作者喝杯咖啡 ☕

<div align="center">
  <img src="img/wechat-pay.png" alt="微信收款码" width="200">
  <p><em>微信扫码，请作者喝杯咖啡</em></p>
</div>

---

## 💬 交流群

欢迎加入微信交流群，一起讨论使用心得和功能建议：

<div align="center">
  <img src="img/wechat-group.png" alt="微信交流群" width="200">
  <p><em>微信扫码加入交流群</em></p>
</div>

---

## 📄 License

[MIT License](LICENSE) - 欢迎自由使用和修改
