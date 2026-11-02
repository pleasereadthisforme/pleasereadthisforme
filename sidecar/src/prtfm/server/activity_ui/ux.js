(() => {
'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));

const $ = id => document.getElementById(id);
const mobile = matchMedia('(max-width: 767px)');
const folders = $('folder-panel'), sessions = $('session-panel');
let active = null, opener = null, previousSessionOpen = false;
const background = [document.querySelector('.app-header'), document.querySelector('.chat-column'), folders, sessions];
function closePanel() {
  if (!active) return;
  active.classList.remove('panel-active');
  active.removeAttribute('role'); active.removeAttribute('aria-modal');
  if (active === sessions) sessions.open = previousSessionOpen;
  background.forEach(node => { node.inert = false; });
  $('panel-backdrop').hidden = true; document.body.classList.remove('panel-open');
  $('open-folders').setAttribute('aria-expanded', 'false'); $('open-sessions').setAttribute('aria-expanded', 'false');
  active = null;
  if (opener?.isConnected && mobile.matches) opener.focus();
}
function openPanel(panel, button) {
  closePanel(); if (!mobile.matches) return;
  opener = button; active = panel;
  if (panel === sessions) { previousSessionOpen = sessions.open; sessions.open = true; }
  panel.classList.add('panel-active'); panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-modal', 'true');
  _ua(panel, 'aria-label', () => (panel === folders ? _t("문서 폴더") : _t("이 폴더의 대화")));
  background.forEach(node => { node.inert = node !== panel; });
  $('panel-backdrop').hidden = false; document.body.classList.add('panel-open');
  button.setAttribute('aria-expanded', 'true'); panel.querySelector('.panel-close').focus();
}
$('open-folders').onclick = () => openPanel(folders, $('open-folders'));
$('open-sessions').onclick = () => openPanel(sessions, $('open-sessions'));
document.querySelectorAll('.panel-close').forEach(button => { button.onclick = closePanel; });
$('panel-backdrop').onclick = closePanel;
document.addEventListener('keydown', event => {
  if (event.key === 'Escape' && active) { event.preventDefault(); closePanel(); return; }
  if (!active || event.key !== 'Tab') return;
  const nodes = [...active.querySelectorAll('button, summary, input, textarea, [tabindex="0"]')]
    .filter(node => !node.disabled && node.getClientRects().length);
  const first = nodes[0], last = nodes.at(-1);
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
});
mobile.addEventListener('change', () => { closePanel(); scheduleLines(); });

function resizeQuestion() {
  $('question').style.height = '30px';
  $('question').style.height = `${Math.min(154, $('question').scrollHeight)}px`;
}
$('question').addEventListener('input', resizeQuestion);
document.addEventListener('draft-restored', resizeQuestion);
document.addEventListener('terminal-updated', () => {
  if (!$('terminal').open) return;
  const pane = document.querySelector('.terminal-body');
  const article = pane.querySelector('article');
  pane.scrollTop += article.getBoundingClientRect().top - pane.getBoundingClientRect().top - 12;
});
let composing = false;
$('question').addEventListener('compositionstart', () => { composing = true; });
$('question').addEventListener('compositionend', () => { composing = false; });
$('question').addEventListener('keydown', event => {
  if (event.key !== 'Enter' || event.shiftKey || event.isComposing || composing || event.keyCode === 229) return;
  event.preventDefault();
  if (!$('send-chat').disabled && !$('send-chat').hidden) $('chat').requestSubmit();
});
// Native details provides keyboard activation; mirror its state for assistive technology.
document.querySelectorAll('details').forEach(details => {
  const summary = details.querySelector('summary');
  const sync = () => { summary.setAttribute('aria-expanded', String(details.open)); scheduleLines(); };
  details.addEventListener('toggle', sync); summary.setAttribute('aria-expanded', String(details.open));
});

const area = document.querySelector('.conversation-area');
let follow = true, prepend = null, preserving = false;
area.addEventListener('scroll', () => {
  if (preserving) return;
  follow = area.scrollHeight - area.scrollTop - area.clientHeight < 48;
  if (follow) $('jump-latest').hidden = true;
});
$('jump-latest').onclick = () => { follow = true; area.scrollTop = area.scrollHeight; $('jump-latest').hidden = true; };
document.addEventListener('history-prepend-start', () => { prepend = {height:area.scrollHeight, top:area.scrollTop}; preserving = true; });
document.addEventListener('history-prepend-end', () => {
  if (prepend) area.scrollTop = prepend.top + area.scrollHeight - prepend.height;
  prepend = null;
  requestAnimationFrame(() => { preserving = false; });
});
document.addEventListener('conversation-restored', () => { follow = true; area.scrollTop = area.scrollHeight; });
new MutationObserver(() => {
  if (preserving) return;
  if (follow) area.scrollTop = area.scrollHeight;
  else $('jump-latest').hidden = false;
}).observe($('conversation'), {childList:true, subtree:true, characterData:true});
new MutationObserver(() => {
  if (follow) area.scrollTop = area.scrollHeight;
  else $('jump-latest').hidden = false;
}).observe($('answer'), {childList:true, subtree:true, characterData:true});
// Stream status is announced separately; avoid reading each output token aloud.
$('answer').setAttribute('aria-live', 'off');

let scheduled = false, selectedAnchor = null;
document.addEventListener('folder-selected', event => {
  selectedAnchor = event.detail.anchor;
  document.querySelectorAll('[data-folder-path]').forEach(node => node.setAttribute('aria-current', String(node === selectedAnchor || (!selectedAnchor && node.dataset.folderPath === $('folder').value))));
  scheduleLines();
});
function scheduleLines() {
  if (scheduled) return;
  scheduled = true; requestAnimationFrame(drawLines);
}
function drawLines() {
  scheduled = false;
  document.querySelector('.chat-column').classList.toggle('folder-selected', Boolean($('folder').value));
  const svg = $('folder-links'); svg.replaceChildren();
  if (mobile.matches || !document.querySelector('.documents').open) return;
  const anchor = selectedAnchor?.isConnected && selectedAnchor.dataset.folderPath === $('folder').value ? selectedAnchor :
    [...document.querySelectorAll('[data-folder-path]')].find(node => node.dataset.folderPath === $('folder').value);
  if (!anchor) return;
  const origin = anchor.getBoundingClientRect();
  const rail = $('folder-panel').getBoundingClientRect();
  if (origin.bottom <= rail.top || origin.top >= rail.bottom) return;
  const clip = $('folder-list').getBoundingClientRect();
  const container = document.querySelector('.documents').getBoundingClientRect();
  if (!clip.width || origin.bottom < 0 || origin.top > innerHeight || !$('folder').value) return;
  svg.setAttribute('viewBox', `0 0 ${innerWidth} ${innerHeight}`);
  for (const row of [...$('folder-list').children].slice(0, 1000)) {
    const rect = row.getBoundingClientRect(), y = rect.top + rect.height / 2;
    if (y < Math.max(clip.top, container.top) || y > Math.min(clip.bottom, container.bottom) || y < 0 || y > innerHeight) continue;
    const icon = anchor.classList.contains('connected-folder');
    const x1 = icon ? origin.left + 56 : origin.right + 2, y1 = icon ? origin.top + 28 : origin.top + origin.height / 2, x2 = rect.left - 6;
    if (x2 <= x1) continue;
    const mid = (x1 + x2) / 2;
    const path = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', `M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y}, ${x2} ${y}`); svg.append(path);
    for (const [cx, cy] of [[x1,y1],[x2,y]]) {
      const dot = document.createElementNS('http://www.w3.org/2000/svg', 'circle');
      dot.setAttribute('cx',cx); dot.setAttribute('cy',cy); dot.setAttribute('r','2'); svg.append(dot);
    }
  }
}
document.addEventListener('scroll', scheduleLines, true);
window.addEventListener('resize', scheduleLines);
new ResizeObserver(scheduleLines).observe(document.querySelector('.workspace'));
new MutationObserver(scheduleLines).observe($('folder-list'), {childList:true});
new MutationObserver(scheduleLines).observe($('scope-name'), {childList:true});
new MutationObserver(scheduleLines).observe($('registered-folders'), {childList:true});
new MutationObserver(scheduleLines).observe($('local-folders'), {childList:true});
new ResizeObserver(scheduleLines).observe($('folder-list'));
// Keep connector endpoints attached while the document column slides open/closed.
let lineMotionUntil = 0, lineMotionFrame = null;
document.querySelector('.chat-column').addEventListener('transitionrun', event => {
  if (!['grid-template-columns', 'padding-left', 'column-gap'].includes(event.propertyName)) return;
  lineMotionUntil = performance.now() + 220;
  if (lineMotionFrame !== null) return;
  const track = () => {
    scheduleLines();
    lineMotionFrame = performance.now() < lineMotionUntil ? requestAnimationFrame(track) : null;
  };
  lineMotionFrame = requestAnimationFrame(track);
});

// Dismiss native dialogs only when both press and release are on the backdrop.
// Dispatch cancel first so existing in-progress operation guards remain effective.
let backdropDialog = null;
function outsideDialog(event, dialog) {
  const rect = dialog.getBoundingClientRect();
  return event.clientX < rect.left || event.clientX > rect.right ||
    event.clientY < rect.top || event.clientY > rect.bottom;
}
document.addEventListener('pointerdown', event => {
  const target = event.target;
  backdropDialog = event.button === 0 && target.tagName === 'DIALOG' && target.open &&
    outsideDialog(event, target) ? target : null;
});
document.addEventListener('pointercancel', () => { backdropDialog = null; });
document.addEventListener('click', event => {
  const dialog = backdropDialog;
  backdropDialog = null;
  if (dialog && dialog.open && event.target === dialog && outsideDialog(event, dialog)) {
    if (dialog.dispatchEvent(new Event('cancel', {cancelable:true}))) dialog.close();
  }
  for (const popup of document.querySelectorAll('#startup-recovery[open], .data-notice[open]')) {
    if (!popup.contains(event.target)) popup.open = false;
  }
});
// Stacked mode starts collapsed; remember explicit choices across resizing.
const chatFrame = document.querySelector('.chat-frame');
const composer = document.querySelector('.composer');
const terminal = $('terminal');
const terminalBody = terminal.querySelector('.terminal-body');
const terminalBot = terminal.querySelector('.bot-character');
const terminalRow = document.createElement('div');
terminalRow.className = 'terminal-row';
composer.insertBefore(terminalRow, document.querySelector('.user-character') || $('chat'));
let docked = null, stackedOpen = false;
terminal.addEventListener('toggle', () => { if (docked === false) stackedOpen = terminal.open; });
function dockTerminal(width) {
  const next = width >= 1000;
  if (next === docked) return;
  if (docked === false) stackedOpen = terminal.open;
  docked = next;
  if (docked) {
    terminal.open = true;
    chatFrame.append(terminal);
    if (terminalBot) { terminalBody.append(terminalBot); terminalBot.dataset.outside = 'false'; }
    terminalRow.hidden = true;
    terminal.setAttribute('role', 'region');
    _ua(terminal, 'aria-label', () => (_t("실행 과정")));
  } else {
    terminalRow.hidden = false;
    terminalRow.append(terminal);
    if (terminalBot) { terminalRow.append(terminalBot); terminalBot.dataset.outside = 'true'; }
    terminal.open = stackedOpen;
    terminal.removeAttribute('role');
    terminal.removeAttribute('aria-label');
  }
  chatFrame.classList.toggle('terminal-docked', docked);
  document.dispatchEvent(new Event('character-layout'));
}
new ResizeObserver(entries => dockTerminal(entries[0].borderBoxSize?.[0]?.inlineSize || chatFrame.getBoundingClientRect().width)).observe(chatFrame);
dockTerminal(chatFrame.getBoundingClientRect().width);


function viewportSize() {
  document.documentElement.style.setProperty('--viewport-height', `${window.visualViewport?.height || innerHeight}px`);
  document.documentElement.style.setProperty('--header-height', `${document.querySelector('.app-header').getBoundingClientRect().height}px`);
  scheduleLines();
}
window.visualViewport?.addEventListener('resize', viewportSize);
window.addEventListener('resize', viewportSize);
new ResizeObserver(viewportSize).observe(document.querySelector('.app-header'));
viewportSize();
})();
