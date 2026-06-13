/**
 * background.js — Grok X 文章导入 service worker
 */
chrome.action && chrome.action.onClicked && chrome.action.onClicked.addListener(function () {
  chrome.tabs.create({ url: 'http://localhost:8765' });
});
console.log('[GrokX] Service worker ready');
