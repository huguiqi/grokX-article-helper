/**
 * content.js — Grok X 文章导入
 * 在 X 文章编辑页显示「📥 载入文章」浮动按钮
 * 点击后从 localhost:8765 拉取 payload 并注入编辑器
 */
(async function () {
  'use strict';

  var LOG = '[GrokX]';
  var SERVER = 'http://localhost:8765';
  var injecting = false;

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
