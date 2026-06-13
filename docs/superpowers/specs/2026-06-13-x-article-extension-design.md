# X 文章发布 - Chrome 扩展模式改造设计

## 目标

将「发长文章到X草稿箱」按钮的后端从 claude-code-sdk + playwright 方案，改造为 Python HTTP 服务 + Chrome 扩展方案。借鉴 [x-article-publisher](https://github.com/punk2898/x-article-publisher) 的架构，用 Python 重写服务端，保留其 Chrome 扩展和 xpage.js 注入引擎。

**硬约束**：仅改造「发长文章到X草稿箱」按钮的后台实现，不影响其他功能（朋友圈、润色、图片生成、线程发布、本地保存等）。

## 架构

```
Streamlit (8501)              Python HTTP Server (8765)
   │                                  │
   │  点击按钮                         │  /status       — 预览信息
   │  ──→ 保存 MD                     │  /payload      — 完整 JSON
   │  ──→ 启动服务 ──→                │  /engine       — xpage.js
   │                                  │  /inject-script — 引擎+payload 合一
   │                                  │
   │                             Chrome 扩展 (content.js)
   │                                  │
   │                             X 编辑器 (xpage.js 注入)
   │                                  │
   │                             用户手动点 Publish
```

## 新增文件

### article_server.py

Python HTTP 服务，端口 8765。

```python
class ArticleServer:
    def __init__(self, md_path: str, port: int = 8765): ...
    def start(self): ...   # 解析 MD → 构建 payload → 启动后台线程
    def stop(self): ...    # 停止服务

def start_article_server(md_path: str, port: int = 8765) -> ArticleServer: ...
def stop_article_server(): ...
```

端点：

| 端点 | 返回 |
|------|------|
| `/` | Dashboard HTML（手动复制备用） |
| `/status` | `{ready, title, textBlocks, imageCount, preview, port}` |
| `/payload` | 完整文章 JSON |
| `/engine` | xpage.js 源码 |
| `/inject-script` | 引擎 + payload 合一脚本 |

安全：CORS 仅允许 localhost，`Cache-Control: no-store`，daemon 线程自动清理。

### markdown_parser.py

Python 重写 x-article-publisher 的 shared.js + payload.js。

```python
def build_payload(md_path: str) -> dict:
    """解析 .md 文件，生成 X Articles 注入 payload"""
```

解析规则：

| Markdown | Draft.js Block Type |
|---|---|
| `# 标题` | `header-one` |
| `## 小标题` | `header-two` |
| `### 三级` | `header-three` |
| `> 引用` | `blockquote` |
| `- 列表` | `unordered-list-item` |
| `1. 有序` | `ordered-list-item` |
| `` ```代码块``` `` | `code-block` |
| `---` | atomic (divider) |
| 其他段落 | `unstyled` |
| `![alt](src)` | marker 占位 + image plan |

内联样式：`**粗体**` → BOLD, `*斜体*` → ITALIC, `` `代码` `` → CODE, `~~删除~~` → STRIKETHROUGH, `[文字](url)` → LINK entity。

图片处理：PIL 压缩（长边 <=1280, JPEG 82%）→ base64 编码。

### xpage.js

从 x-article-publisher 直接复制，不做修改。负责 React Fiber 攀爬、Draft.js 写入、图片上传（onFilesAdded）、marker 清理、GraphQL 标题/封面设置。

### extension/

Chrome 扩展，重写自 x-article-publisher。

**manifest.json**：Manifest V3，匹配 `x.com/compose/articles*` 和 `x.com/*/articles/edit/*`。

**content.js**：
1. 检测编辑器页面（contenteditable 元素）→ 显示「📥 载入文章」浮动按钮
2. 点击 → fetch `localhost:8765/status` 确认就绪
3. 注入 `<script src="localhost:8765/inject-script?t={timestamp}">`
4. 等待结果 meta 标签 → 显示成功/失败提示

**background.js**：点击扩展图标打开 localhost:8765 dashboard。

## 修改文件

### xai_api.py

删除 `_publish_article_via_sdk()` 和旧 `publish_article_to_x()`。

新实现：

```python
def publish_article_to_x(md_path) -> tuple:
    from article_server import start_article_server
    try:
        server = start_article_server(str(md_path))
        return True, f"文章服务已启动：http://localhost:{server.port}"
    except Exception as e:
        return False, f"启动服务失败：{e}"
```

### x_grok_poster.py

仅改动 `article_ui()` 中 Step 4 的「📝 发长文章到X草稿箱」按钮点击后的提示文案（约 519-529 行）。按钮之前的保存逻辑、Step 1/2/3、朋友圈 tab、线程发布等全部不动。

改造后提示：

```
st.success("文章服务已启动")
st.info("👉 在 Chrome 打开 x.com/compose/articles/new，点击右上角「📥 载入文章」按钮")
```

## 依赖

无新 pip 依赖。`markdown_parser.py` 用标准库 `re` + `json` + `base64`，图片压缩用 PIL（项目已有 Pillow）。`xpage.js` 作为静态文件读取。

## 用户操作流程

1. 在 Streamlit 中完成文章润色、图片生成、保存
2. 点击「📝 发长文章到X草稿箱」
3. 服务自动启动，UI 显示服务地址
4. 在 Chrome 打开 `x.com/compose/articles/new`
5. 点击「写文章」进入编辑器
6. 点击右上角「📥 载入文章」按钮
7. 文章自动注入编辑器
8. 检查内容 → 手动点 Publish
