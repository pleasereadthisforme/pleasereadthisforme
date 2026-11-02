(() => {
'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));

const key = 'prtfm-theme';
let preference = null;
try { const saved = window.localStorage.getItem(key); if (saved === 'light' || saved === 'dark') preference = saved; } catch {}
function apply() {
  const dark = preference === 'dark';
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const button = document.getElementById('theme-toggle');
  if (button) {
    button.setAttribute('aria-pressed', String(dark));
    _ua(button, 'aria-label', () => (_t("다크 모드")));
    _ui(button, 'title', () => (dark ? _t("라이트 모드로 전환") : _t("다크 모드로 전환")), false);
  }
}
apply();
window.addEventListener('storage', event => {
  if (event.key !== key && event.key !== null) return;
  preference = event.newValue === 'light' || event.newValue === 'dark' ? event.newValue : null;
  apply();
});
document.addEventListener('DOMContentLoaded', () => {
  apply();
  document.getElementById('theme-toggle').addEventListener('click', () => {
    preference = document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark';
    try { window.localStorage.setItem(key, preference); } catch {}
    apply();
  });
});
})();
