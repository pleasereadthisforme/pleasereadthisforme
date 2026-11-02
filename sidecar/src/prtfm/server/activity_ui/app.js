(() => {
'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));
const $ = id => document.getElementById(id);
const icons = {'close': '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m6 6 12 12M18 6 6 18"/></svg>', 'next': '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m9 5 7 7-7 7"/></svg>', 'folder': '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M3 7V5h6l3 3h9v12H3Z"/></svg>'};
const prepareIcon = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M14 3H5v18h14V8l-5-5v5h5M8 12h5m-5 4h3"/><path d="m14 15 2 2 4-4"/></svg>';
const developmentMode = typeof window !== 'undefined' && new URLSearchParams(window.location.search).get('dev') === '1';
$('settings').hidden = !developmentMode;
if (document.querySelector) document.querySelector('.folder-options').hidden = !developmentMode;
function connectionHelp() {
  $('connection-status').hidden = false;
  if (developmentMode) { $('settings').open = true; $('token').focus(); return; }
  _ui($('connection-status'), 'textContent', () => (_t("연결이 필요합니다. 앱을 다시 실행하거나 로컬 서버 실행기에서 화면을 다시 열어주세요.")), false);
}
let epoch = 0, controller, timer, base, token, cursor = 0, selected = null, follow = true;
let chatting = false, pollFailures = 0;
function characterState(detail) { document.dispatchEvent?.(new CustomEvent('character-state', {detail})); }
function syncComposer() {
  $('send-chat').disabled = chatting || !$('folder').value || !$('question').value.trim();
  $('question').readOnly = chatting;
  _ui($('question'), 'placeholder', () => ($('folder').value ? _t("문서에 대해 질문하세요") : _t("먼저 폴더를 선택하세요")), false);
}
$('question').addEventListener('input', syncComposer);
document.addEventListener('folder-selected', syncComposer);
document.addEventListener('draft-restored', syncComposer);
function setChatting(value) {
  characterState({type:value ? 'start' : 'stop'});
  chatting = value;
  $('send-chat').hidden = value;
  $('cancel-chat').hidden = !value;
  syncComposer();
}
setChatting(false);
const records = new Map();
function stop() { epoch++; clearTimeout(timer); controller?.abort(); }
async function get(url, signal) {
  const response = await _fetch(url, {headers: {Authorization: `Bearer ${token}`}, signal, cache: 'no-store'});
  if (!response.ok) { const error = new Error(`Request failed (${response.status})`); error.status = response.status; throw error; }
  return response.json();
}
const snapshots = new Map();
let displayed = null;
let collapsedStep = null;
const stepButtons = new Map();
const labels = {find:'Finding files', glob:'Matching files', rg:'Searching text', grep:'Searching files', sed:'Reading lines', read_file:'Reading file', list_files:'Listing documents', search_text:'Searching documents', outline:'Reading outline', read_document:'Reading document', semantic_search:'Semantic search'};
function steps() {
  const grouped = new Map();
  for (const event of records.values()) {
    let key;
    if (event.kind.startsWith('tool_')) key = `tool:${event.call_id || event.seq}`;
    else if (event.kind.startsWith('model_request_')) key = `model:${event.request_id}`;
    else if (['done','error','cancelled','interrupted'].includes(event.kind)) key = `end:${event.seq}`;
    else continue;
    const step = grouped.get(key) || {key, seq:event.seq};
    step.event = event;
    if (['tool_started','model_request_prepared','model_request_sent'].includes(event.kind) && !step.startedAt) step.startedAt = event.timestamp;
    if (['tool_completed','model_request_completed','model_request_blocked','model_request_unfinished'].includes(event.kind)) step.finishedAt = event.timestamp;
    if (event.kind === 'tool_started') step.start = event;
    if (event.kind === 'tool_completed') step.end = event;
    grouped.set(key, step);
  }
  return [...grouped.values()];
}
function caption(step) {
  const e = step.event;
  if (e.kind.startsWith('tool_')) return labels[e.tool_name] || 'Running tool';
  if (e.kind.startsWith('model_')) return 'Preparing next step';
  return {done:'Completed',error:'Failed',cancelled:'Stopped',interrupted:'Disconnected'}[e.kind];
}
function command(name, args) {
  const parts = [name];
  for (const key of ['query','path','pattern','lines','pages','section','regular_expression','limit','max_chars']) {
    if (args[key] !== undefined && args[key] !== '') parts.push(`${key}=${JSON.stringify(args[key])}`);
  }
  if (args.cursor) parts.push('(continued)');
  return parts.join(' ');
}
function formatSourceLocation(value) {
  return value.line_start ? ` · lines ${value.line_start}–${value.line_end}` : '';
}
function resultText(name, data) {
  let value;
  try {
    const output = data?.output;
    if (typeof output !== 'string' || !output.startsWith('<tool_result>\n') || !output.endsWith('\n</tool_result>')) throw new Error();
    value = JSON.parse(output.slice(14, -15));
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error();
  } catch { return 'Result unavailable. The saved format is not supported.'; }
  if (value.argv) {
    const states = {complete:'Completed', no_match:'No matches', truncated:'Output truncated. Narrow the search to see more.', timeout:'Timed out', cancelled:'Stopped', error:'Failed'};
    return [value.stdout || '', value.stderr || '', value.error || '', states[value.status] || 'Unknown status',
      value.planned_batches > 1 ? `Batches: ${value.batch_count} / ${value.planned_batches}` : '',
      Object.keys(value.unavailable_sources || {}).length ? 'Some documents could not be extracted.' : ''].filter(Boolean).join('\n\n');
  }
  if (value.error) return 'The tool failed. This does not mean no documents exist.';
  const notes = [];
  if (value.cursor || value.truncated) notes.push('Partial results. More results are available.');
  if (value.partial_line_start || value.partial_line_end) notes.push('The first or last line is incomplete.');
  if (value.status === 'timeout') notes.push('Search timed out. Results may be incomplete.');
  if (value.status === 'cancelled') notes.push('Search stopped.');
  if (value.warnings?.length || Object.keys(value.errors || {}).length) notes.push('Some documents could not be processed.');
  let body;
  if (name === 'list_files' && Array.isArray(value.files)) body = value.files.length ? value.files.map(f => `${f.path}${f.empty ? ' · No extracted text' : ''}`).join('\n') : 'No documents found.';
  else if (['search_text','semantic_search'].includes(name) && Array.isArray(value.hits)) body = value.hits.length ? value.hits.map(h => `${h.path}${formatSourceLocation(h)}\n${h.snippet || ''}`).join('\n\n') : 'No matches found.';
  else if (name === 'outline' && Array.isArray(value.sections)) body = value.sections.length ? value.sections.map(h => `${h.title}${formatSourceLocation(h)}`).join('\n') : 'No outline available.';
  else if (name === 'read_document' && typeof value.text === 'string') body = `${value.path}${formatSourceLocation(value)}\n\n${value.text || '(Empty range)'}`;
  else body = 'Preview is not available for this tool.';
  return [body, ...notes].join('\n\n');
}
async function snapshot(event, signal) {
  if (!event?.snapshot_ref) return null;
  const key = `${base}:${event.snapshot_ref}`;
  if (!snapshots.has(key)) snapshots.set(key, get(`${base}&snapshot=${event.snapshot_ref}`, signal).catch(error => { snapshots.delete(key); throw error; }));
  return snapshots.get(key);
}
function updateNavigation() {
  const list = steps(), index = list.findIndex(s => s.key === selected);
  _ui($('step-position'), 'textContent', () => (list.length ? `${index + 1} / ${list.length}` : '0 / 0'), false);
  $('previous').disabled = index <= 0;
  $('next').disabled = index < 0 || index >= list.length - 1;
  $('latest').hidden = follow || !list.length;
  _ui($('latest'), 'textContent', () => (replaying ? 'Latest step' : 'Back to live'), false);
  _ui($('view-mode'), 'textContent', () => (replaying ? 'Saved run' : follow ? 'Live' : 'Earlier step'), false);
  $('view-mode').dataset.live = String(!replaying && follow);
}
function showOutput(text) {
  const lines = text.split('\n');
  const shortened = text.length > 6000 || lines.length > 60;
  const preview = lines.slice(0, 60).join('\n').slice(0, 6000);
  _ui($('detail'), 'textContent', () => (shortened ? preview + '\n…' : text), false);
  $('output-more').hidden = !shortened;
  _ui($('output-more'), 'textContent', () => ('Show more'), false);
  let expanded = false;
  $('output-more').onclick = () => {
    expanded = !expanded;
    _ui($('detail'), 'textContent', () => (expanded ? text : preview + '\n…'), false);
    _ui($('output-more'), 'textContent', () => (expanded ? 'Show less' : 'Show more'), false);
  };
}
async function select(step, generation) {
  selected = step.key;
  const outputPanel = $('step-output');
  const row = stepButtons.get(step.key);
  if (row?.after) row.after(outputPanel);
  outputPanel.hidden = collapsedStep === step.key;
  for (const [key, button] of stepButtons) button.setAttribute('aria-expanded', String(key === selected && !outputPanel.hidden));
  updateNavigation();
  document.querySelectorAll('#events button').forEach(b => b.setAttribute('aria-current', String(b.dataset.key === selected)));
  const event = step.event;
  const signature = JSON.stringify([generation, step.key, event.seq, step.start?.snapshot_ref, step.end?.snapshot_ref]);
  if (displayed === signature) return;
  displayed = signature;
  _ui($('title'), 'textContent', () => (caption(step)), false);
  _ui($('detail'), 'textContent', () => (''), false);
  $('output-more').hidden = true;
  _ui($('detail-status'), 'textContent', () => (''), false);
  if (!event.kind.startsWith('tool_')) {
    _ui($('detail'), 'textContent', () => (event.kind.startsWith('model_') ?
      ({model_request_completed:'Next step ready.',model_request_blocked:'Request limit reached.',model_request_unfinished:'Model request did not finish.'}[event.kind] || 'Preparing the next step or answer.') : caption(step)), false);
    return; // Model request/response snapshots are never fetched by this view.
  }
  const current = () => epoch === generation && selected === step.key && steps().find(s => s.key === step.key)?.event.seq === event.seq;
  _ui($('detail'), 'textContent', () => (`${event.tool_name} · Loading command…`), false);
  _ui($('detail-status'), 'textContent', () => (step.end ? 'Loading result' : 'Running'), false);
  try {
    const [args, output] = await Promise.all([snapshot(step.start, controller.signal), snapshot(step.end, controller.signal)]);
    if (!current()) return;
    const input = args?.command || (args ? command(event.tool_name, args) : `${event.tool_name} (No saved input)`);
    const result = step.end ? (step.end.snapshot_omitted ? 'Result omitted due to the storage limit.' : resultText(event.tool_name, output)) : 'Running…';
    showOutput(`$ ${input}\n\n${result}`);
    _ui($('detail-status'), 'textContent', () => ('Command output'), false);
    if (follow) document.dispatchEvent?.(new CustomEvent('terminal-updated'));
  } catch (error) {
    if (current() && error.name !== 'AbortError') {
      displayed = null; // Retry a snapshot that was not available yet on the next poll.
      _ui($('detail-status'), 'textContent', () => (error.message), false);
    }
  }
}
let summaryVersion = 0, replaying = false;
async function renderSummary(list, generation) {
  const version = ++summaryVersion, newest = list.at(-1);
  if (!newest) return;
  const prefix = replaying ? 'Saved run · ' : '';
  const kind = newest.event.kind;
  if (kind.startsWith('model_')) {
    _ui($('terminal-summary'), 'textContent', () => (prefix + ({model_request_blocked:'Request limit reached', model_request_unfinished:'Request unfinished'}[kind] || 'Preparing next step')), false);
    if (!list.some(step => step.event.kind.startsWith('tool_'))) return;
  }
  const tool = list.findLast(s => s.event.kind.startsWith('tool_'));
  const state = kind.startsWith('model_') ? ({model_request_blocked:'Request limit reached', model_request_unfinished:'Request unfinished'}[kind] || 'Preparing next step') : kind.startsWith('tool_') ? (tool?.end ? 'Finished' : 'Running') : caption(newest);
  _ui($('terminal-summary'), 'textContent', () => (prefix + state), false);
  if (!tool) return;
  try {
    const args = await snapshot(tool.start, controller.signal);
    if (version !== summaryVersion || generation !== epoch) return;
    const input = args?.command || (args ? command(tool.event.tool_name, args) : tool.event.tool_name);
    _ui($('terminal-summary'), 'textContent', () => (`${prefix}${input} · ${state}`), false);
  } catch (error) {
    if (version === summaryVersion && generation === epoch && error.name !== 'AbortError')
      _ui($('terminal-summary'), 'textContent', () => (`${prefix}${state} · Command unavailable`), false);
  }
}
function stepState(step, list) {
  const kind = step.event.kind;
  if (['error','model_request_blocked','model_request_unfinished'].includes(kind)) return 'failed';
  if (['cancelled','interrupted'].includes(kind)) return 'stopped';
  if (kind === 'done' || kind.endsWith('_completed')) return 'done';
  if (replaying || list.some(s => ['done','error','cancelled','interrupted'].includes(s.event.kind))) return 'stopped';
  return 'running';
}
function elapsedText(step, state) {
  if (!Number.isFinite(step.startedAt)) return '—';
  const end = step.finishedAt ?? (state === 'running' ? Date.now()/1000 : null);
  return Number.isFinite(end) ? `${Math.max(0, end-step.startedAt).toFixed(1)}s` : '—';
}
function clearStepRows() {
  // The selected output lives inside the log. Park it before removing rows so
  // its ID-addressed descendants remain attached for the next run.
  const output = $('step-output');
  if ($('events').before) $('events').before(output);
  output.hidden = true;
  $('events').replaceChildren();
  stepButtons.clear();
}
async function renderSteps(generation) {
  const list = steps();
  clearStepRows();
  for (const [index, step] of list.entries()) {
    const button = document.createElement('button'); button.dataset.key = step.key;
    button.className = 'terminal-step';
    const state = stepState(step, list);
    button.dataset.state = state;
    button.setAttribute('aria-controls', 'step-output');
    const add = (className, text) => { const span = document.createElement('span'); span.className = className; _ui(span, 'textContent', () => (text), false); button.append(span); return span; };
    add('step-arrow', '›');
    add('step-number', String(index + 1).padStart(2, '0'));
    add('step-kind', step.event.tool_name || (step.event.kind.startsWith('model_') ? 'MODEL' : 'RUN'));
    add('step-caption', caption(step));
    const mark = add('step-mark', state === 'running' ? '' : {done:'[DONE]',failed:'[FAIL]',stopped:'[STOP]'}[state]);
    _ua(mark, 'aria-label', () => (state));
    if (state === 'running') {
      mark.className += ' pixel-spinner';
      for (let n=0; n<8; n++) { const dot=document.createElement('i'); mark.append(dot); }
    }
    add('step-elapsed', elapsedText(step, state));
    button.onclick = () => { follow = false; collapsedStep = selected === step.key && collapsedStep !== step.key ? step.key : null; select(step, generation); };
    stepButtons.set(step.key, button);
    $('events').append(button);
  }
  const newest = list.at(-1);
  // A model status immediately following a tool must not hide its command/output.
  const liveTarget = newest && !newest.event.kind.startsWith('tool_') ?
    list.findLast(s => s.event.kind.startsWith('tool_')) || newest : newest;
  const target = follow ? liveTarget : list.find(s => s.key === selected) || list[0];
  await Promise.all([target ? select(target, generation) : null, renderSummary(list, generation)]);
  updateNavigation();
}
async function poll(generation) {
  try {
    const data = await get(`${base}&after=${cursor}`, controller.signal);
    if (generation !== epoch) return;
    pollFailures = 0;
    const freshEvents = [];
    for (const event of data.events) {
      if (records.has(event.seq)) continue;
      freshEvents.push(event);
      records.set(event.seq, event); cursor = Math.max(cursor, event.seq);
    }
    _ui($('status'), 'textContent', () => (data.state === 'running' ? 'Running' : `${{done:'Completed',error:'Failed',cancelled:'Stopped',interrupted:'Disconnected'}[data.state] || 'Ended'}`), false);
    if ([...records.values()].some(e => e.events_truncated)) _ui($('status'), 'textContent', () => (' · Some steps were not saved'), true);
    characterState({type:'activity', status:data.state, events:freshEvents, event:[...records.values()].at(-1), replaying});
    await renderSteps(generation);
    if (generation !== epoch) return;
    if (data.events.length === 100 || data.state === 'running') timer = setTimeout(() => poll(generation), data.events.length === 100 ? 0 : 1500);
  } catch (error) {
    if (generation === epoch && error.name !== 'AbortError') {
      const retry = ![401,403,404].includes(error.status) && ++pollFailures <= 3;
      _ui($('status'), 'textContent', () => (error.message + (retry ? ' · Reconnecting…' : ' · Restart the app to reconnect')), false);
      if (retry) timer = setTimeout(() => poll(generation), 1500);
    }
  }
}
$('connect').onsubmit = event => {
  event.preventDefault();
  if (chatting) { _ui($('status'), 'textContent', () => ('Wait for the answer before opening another run.'), false); return; }
  clearSources();
  replaying = true;
  stop(); controller = new AbortController(); token = $('token').value;
  cursor = 0; selected = null; follow = true; records.clear(); snapshots.clear(); collapsedStep = null; displayed = null; clearStepRows(); _ui($('detail'), 'textContent', () => (''), false);
  base = `/sessions/${encodeURIComponent($('session').value)}/runs/${encodeURIComponent($('run').value)}/events?folder=${encodeURIComponent($('folder').value)}`;
  poll(epoch);
};
$('stop').onclick = () => { if (chatting) { clearTimeout(timer); controller?.abort(); } else { stop(); token = ''; $('token').value = ''; } _ui($('status'), 'textContent', () => ('Updates paused. The run has not been cancelled.'), false); };
$('latest').onclick = () => { follow = true; renderSteps(epoch); };

function citationText(answer, citations) {
  const sources = new Map(citations.map(c => [c.evidence_id, c]));
  // Accept observed model wrappers, but resolve only server-provided citations.
  return answer.replace(/\[\s*(?:evidence_id\s*:\s*)?([0-9a-f]{32}:\d+)\s*\]/g, (original, id) => {
    const source = sources.get(id);
    if (!source) return _t("[출처 확인 불가]");
    const where = source.page_kind === 'physical' ? _t("원본 PDF {0}–{1}쪽" , source.page_start, source.page_end) :
      source.page_kind === 'slide' ? _t("슬라이드 {0}–{1}" , source.page_start, source.page_end) :
      source.original_line_start != null ? _t("원본 줄 {0}–{1}" , source.original_line_start, source.original_line_end) :
      _t("추출문 줄 {0}–{1}" , source.line_start, source.line_end);
    return `[${source.doc || source.path} · ${where}]`;
  });
}
function renderCitedAnswer(body, answer, citations, indexVersions = {}) {
  const valid = [...new Map(citations.filter(c => c.evidence_id && typeof c.text === 'string').map(c => [c.evidence_id, c])).values()];
  const pattern = /\[\s*(?:evidence_id\s*:\s*)?([0-9a-f]{32}:\d+)\s*\]/g;
  body.replaceChildren();
  let offset = 0;
  for (const match of answer.matchAll(pattern)) {
    const text = document.createElement('span'); text.textContent = answer.slice(offset, match.index); body.append(text);
    const index = valid.findIndex(c => c.evidence_id === match[1]);
    const chip = document.createElement(index < 0 ? 'span' : 'button');
    chip.className = 'citation-chip'; _ui(chip, 'textContent', () => (index < 0 ? _t("[출처 확인 불가]") : String(index + 1)), false);
    if (index >= 0) {
      chip.type = 'button'; _ui(chip, 'title', () => (citationText(match[0], [valid[index]])), false);
      _ua(chip, 'aria-label', () => (_t("출처 {0}: {1}" , index + 1, chip.title)));
      chip.onclick = () => {
        if (activeSourceButton?.dataset.evidenceId === valid[index].evidence_id && $('source-view').parentElement === body.parentElement && !$('source-view').hidden) {
          $('source-view').hidden = true;
          activeSourceButton.setAttribute('aria-expanded', 'false'); activeSourceButton = null;
          chip.focus(); return;
        }
        body.parentElement.append($('sources'), $('source-view'));
        renderSources(valid, indexVersions);
        $('sources').children[index].onclick();
      };
    }
    body.append(chip); offset = match.index + match[0].length;
  }
  const tail = document.createElement('span'); tail.textContent = answer.slice(offset); body.append(tail);
}
let activeSourceButton = null;
let sourceOwner = null;
function clearSources() {
  activeSourceButton = null;
  $('sources').replaceChildren();
  $('source-view').hidden = true;
  _ui($('source-title'), 'textContent', () => (''), false);
  _ui($('source-meta'), 'textContent', () => (''), false);
  _ui($('source-text'), 'textContent', () => (''), false);
}
function renderSources(citations, indexVersions = {}) {
  clearSources();
  const seen = new Set();
  for (const source of citations) {
    if (!source.evidence_id || seen.has(source.evidence_id) || typeof source.text !== 'string') continue;
    seen.add(source.evidence_id);
    const sourceNumber = seen.size;
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'source-chip';
    button.dataset.evidenceId = source.evidence_id;
    const name = (source.doc || source.path || _t("문서")).split(/[\\/]/).pop();
    _ui(button, 'textContent', () => (`${sourceNumber} · ${((source.doc || source.path || _t("문서")).split(/[\\/]/).pop())}`), false);
    _ui(button, 'title', () => (citationText(`[${source.evidence_id}]`, [source])), false);
    button.setAttribute('aria-controls', 'source-view');
    button.setAttribute('aria-expanded', 'false');
    button.onclick = () => {
      if (activeSourceButton === button && !$('source-view').hidden) {
        $('source-view').hidden = true;
        button.setAttribute('aria-expanded', 'false');
        activeSourceButton = null;
        button.focus();
        return;
      }
      activeSourceButton?.setAttribute('aria-expanded', 'false');
      activeSourceButton = button;
      button.setAttribute('aria-expanded', 'true');
      _ui($('source-title'), 'textContent', () => (source.doc || source.path), false);
      const pages = source.page_kind === 'physical' ? _t("원본 PDF {0}–{1}쪽 · " , source.page_start, source.page_end) :
        source.page_kind === 'slide' ? _t("슬라이드 {0}–{1} · " , source.page_start, source.page_end) : '';
      const original = source.original_line_start != null ? _t("원본 줄 {0}–{1} · " , source.original_line_start, source.original_line_end) : '';
      _ui($('source-meta'), 'textContent', () => (_t("{0}{1}추출문 줄 {2}–{3} · 저장 버전 {4}\n답변 당시 저장된 근거입니다. 현재 파일의 변경 여부는 확인하지 않았습니다." , (source.page_kind === 'physical' ? _t("원본 PDF {0}–{1}쪽 · " , source.page_start, source.page_end) :
        source.page_kind === 'slide' ? _t("슬라이드 {0}–{1} · " , source.page_start, source.page_end) : ''), (source.original_line_start != null ? _t("원본 줄 {0}–{1} · " , source.original_line_start, source.original_line_end) : ''), source.line_start, source.line_end, source.revision ?? _t("알 수 없음"))), false);
      const indexed = indexVersions[source.evidence_id];
      if (indexed) {
        const states = {get same() { return _t("현재 색인 버전과 동일"); },get changed() { return _t("현재 색인 버전과 다름"); },get not_indexed() { return _t("현재 색인에 없는 문서"); },get unavailable() { return _t("현재 색인 처리 상태 확인 필요"); },get unknown() { return _t("색인 버전 비교 정보 부족"); }};
        _ui($('source-meta'), 'textContent', () => (_t("\n{0}{1}\n조회 시점의 색인 기준입니다. 재색인하지 않은 원본 수정은 반영되지 않습니다." , states[indexed.state] || states.unknown, indexed.revision == null ? '' : _t(" · 현재 색인 버전 {0}", indexed.revision))), true);
      }
      _ui($('source-text'), 'textContent', () => (source.text), false);
      $('source-view').hidden = false;
      $('source-title').focus();
    };
    $('sources').append(button);
  }
}
$('source-close').onclick = () => {
  $('source-view').hidden = true;
  activeSourceButton?.setAttribute('aria-expanded', 'false');
  activeSourceButton?.focus(); activeSourceButton = null;
};
let chatController = null;
let conversationScope = '', conversationVersion = 0;
const drafts = new Map();
function draftKey() { return JSON.stringify([$('token').value, $('folder').value, $('session').value]); }
function saveDraft() {
  const key = draftKey(), value = $('question').value;
  if (value) drafts.set(key, value); else drafts.delete(key);
}
function restoreDraft() {
  $('question').value = drafts.get(draftKey()) || '';
  document.dispatchEvent?.(new CustomEvent('draft-restored'));
}

function clearConversation() {
  $('source-home').append($('sources')); $('source-home').append($('source-view'));
  conversationVersion++; conversationScope = '';
  $('conversation').replaceChildren(); $('answer').hidden = false;
  $('turns-more').hidden = true;
}
function replayAction(folder, auth, session, run, version) {
  if (!run) return null;
  return () => {
    if (chatting) { _ui($('status'), 'textContent', () => ('Wait for the answer before opening another run.'), false); return; }
    if (version !== conversationVersion || folder !== $('folder').value || auth !== $('token').value || session !== $('session').value) return;
    stop(); resetView(); controller = new AbortController(); token = auth;
    replaying = true; follow = false;
    $('terminal').open = true;
    $('run').value = run;
    base = `/sessions/${encodeURIComponent(session)}/runs/${encodeURIComponent(run)}/events?folder=${encodeURIComponent(folder)}`;
    return poll(epoch);
  };
}
function appendTurn(question, answer, openSources, placement = 'append', replay = null, existing = null) {
  const row = existing || document.createElement('section');
  if (existing) row.replaceChildren(); row.className = 'conversation-turn';
  const heading = document.createElement('h3'); heading.className = 'user-message'; _ui(heading, 'textContent', () => (question), false);
  const body = document.createElement('div'); body.className = 'turn-answer'; _ui(body, 'textContent', () => (answer), false);
  const button = document.createElement('button'); button.type = 'button'; _ui(button, 'textContent', () => (_t("이 답변의 출처 확인")), false);
  button.onclick = () => {
    historySelection++;
    if (sourceOwner === row && $('sources').children.length) {
      clearSources(); sourceOwner = null; return;
    }
    sourceOwner = row;
    clearSources(); row.append($('sources')); row.append($('source-view'));
    return openSources(body, button);
  };
  const activity = document.createElement('button'); activity.type = 'button';
  _ui(activity, 'textContent', () => (replay ? _t("작업 과정 보기") : _t("연결된 작업 기록 없음")), false);
  activity.disabled = !replay; activity.onclick = replay;
  row.append(heading); row.append(body); row.append(button); row.append(activity); if (!existing) $('conversation')[placement](row);
  return row;
}
function resetView() {
  pollFailures = 0;
  clearSources();
  summaryVersion++;
  _ui($('terminal-summary'), 'textContent', () => ('Ready'), false);
  cursor = 0; selected = null; follow = true; records.clear(); snapshots.clear();
  collapsedStep = null; displayed = null; clearStepRows();
  _ui($('detail'), 'textContent', () => (''), false); _ui($('title'), 'textContent', () => ('Ready'), false);
  $('output-more').hidden = true; _ui($('detail-status'), 'textContent', () => (''), false); _ui($('status'), 'textContent', () => ('Ready'), false);
  updateNavigation();
}
document.addEventListener('conversations-cleared', () => {
  chatController?.abort(); setChatting(false);
  historyVersion++; historySelection++;
  stop(); resetView(); clearConversation();
  $('session').value = ''; $('run').value = '';
  $('history-list').replaceChildren();
  _ui($('history-status'), 'textContent', () => (_t("저장된 대화가 없습니다.")), false);
  _ui($('chat-status'), 'textContent', () => (_t("대화 기록을 삭제했습니다.")), false);
});
function consumeFrame(frame, handle) {
  let name = 'message'; const data = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event:')) name = line.slice(6).trim();
    if (line.startsWith('data:')) data.push(line.slice(5).trimStart());
  }
  if (data.length) handle(name, JSON.parse(data.join('\n')));
}
$('chat').onsubmit = async event => {
  event.preventDefault();
  if (chatting) return;
  historyVersion++; historySelection++;
  chatController?.abort(); setChatting(false); stop(); resetView();
  controller = new AbortController(); chatController = new AbortController();
  const generation = epoch, chatSignal = chatController.signal;
  const folder = $('folder').value, auth = $('token').value;
  if ($('folder-status').dataset.preparing === 'true') { _ui($('chat-status'), 'textContent', () => (_t("문서 자동 준비가 끝나면 질문을 보내 주세요.")), false); return; }
  if (!folder || !auth || !$('question').value.trim()) { _ui($('chat-status'), 'textContent', () => (_t("서버 연결 상태를 확인하고 폴더와 질문을 선택·입력하세요.")), false); return; }
  const question = $('question').value;
  if (conversationScope !== JSON.stringify([folder, auth, $('session').value])) clearConversation();
  const pending = appendTurn(question, '', null);
  const pendingBody = pending.children[1];
  pendingBody.hidden = true;
  pending.children[2].hidden = true; pending.children[3].hidden = true;
  $('question').value = ''; drafts.delete(draftKey());
  document.dispatchEvent?.(new CustomEvent('draft-restored'));
  $('answer').hidden = true;
  $('history-more').disabled = true;
  setChatting(true); token = auth; _ui($('answer'), 'textContent', () => (''), false); _ui($('chat-status'), 'textContent', () => (_t("답변 준비 중")), false);
  if ($('session-panel').open && !$('history-list').children.length) historyRequest();
  replaying = false; _ui($('terminal-summary'), 'textContent', () => ('Preparing answer'), false);
  let sid = null, runId = null, terminal = false, linked = false, reader;
  try {
    const response = await _fetch('/chat', {method:'POST', headers:{Authorization:`Bearer ${auth}`, 'Content-Type':'application/json'},
      body:JSON.stringify({folder, question, session_id:$('session').value || null}), signal:chatSignal});
    if (!response.ok) throw new Error(_t("질문 전송 실패 ({0})" , response.status));
    reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = '';
    const handle = (name, value) => {
      if (generation !== epoch) return;
      if (name === 'session') { sid = value.session_id; $('session').value = sid; conversationScope = JSON.stringify([folder, auth, sid]); }
      if (name === 'activity') {
        if (!sid) throw new Error(_t("세션 정보가 없는 실행 기록입니다."));
        linked = true; runId = value.run_id; $('run').value = runId;
        base = `/sessions/${encodeURIComponent(sid)}/runs/${encodeURIComponent(value.run_id)}/events?folder=${encodeURIComponent(folder)}`;
        poll(generation);
      }
      if (name === 'delta') {
        characterState({type:'delta'});
        _ui($('answer'), 'textContent', () => (value.text), true);
        _ui(pendingBody, 'textContent', () => ($('answer').textContent), false); pendingBody.hidden = false;
      }
      if (name === 'done') {
        terminal = true; _ui($('answer'), 'textContent', () => (citationText(value.answer, value.citations || [])), false);
        const version = conversationVersion;
        const row = appendTurn(question, $('answer').textContent, () => {
          if (version !== conversationVersion || folder !== $('folder').value || auth !== $('token').value || chatting) return;
          renderSources(value.citations || []);
          renderCitedAnswer(row.children[1], value.answer, value.citations || []);
        }, 'append', replayAction(folder, auth, sid, runId, version), pending);
        row.append($('sources')); row.append($('source-view'));
        renderCitedAnswer(row.children[1], value.answer, value.citations || []);
        $('answer').hidden = true;
        _ui($('chat-status'), 'textContent', () => (linked ? _t("답변 완료") : _t("답변 완료 · 이 실행에는 상세 작업 기록이 없습니다.")), false);
        if (!linked) _ui($('terminal-summary'), 'textContent', () => ('Completed · No saved steps'), false);
      }
      if (name === 'error') { characterState({type:'error'}); terminal = true; _ui($('chat-status'), 'textContent', () => (value.message), false); }
    };
    while (true) {
      const {value, done} = await reader.read();
      if (generation !== epoch) { await reader.cancel(); return; }
      buffer += decoder.decode(value, {stream:!done});
      buffer = buffer.replace(/\r\n/g, '\n');
      let boundary;
      while ((boundary = buffer.indexOf('\n\n')) !== -1) {
        consumeFrame(buffer.slice(0,boundary), handle); buffer = buffer.slice(boundary+2);
      }
      if (done) { if (buffer.trim()) consumeFrame(buffer, handle); break; }
    }
    if (!terminal) throw new Error(_t("완료 응답 없이 연결이 종료됐습니다."));
  } catch (error) {
    if (generation === epoch && error.name !== 'AbortError') characterState({type:'error'});
    if (generation === epoch) _ui($('chat-status'), 'textContent', () => (error.name === 'AbortError' ? _t("질문 중단 요청") : error.message), false);
  } finally {
    reader?.releaseLock();
    if (generation === epoch) {
      if (!pendingBody.textContent && pending.children[1] === pendingBody) {
        _ui(pendingBody, 'textContent', () => ($('chat-status').textContent), false); pendingBody.hidden = false;
      }
      setChatting(false);
      $('history-more').disabled = false;
      if ($('session-panel').open) historyRequest();
    }
  }
};
$('cancel-chat').onclick = () => { chatController?.abort(); _ui($('chat-status'), 'textContent', () => (_t("질문 중단 요청 · 서버 상태는 작업 기록에서 확인하세요.")), false); };

function moveStep(offset) {
  const list = steps(), index = list.findIndex(s => s.key === selected);
  const target = list[Math.max(0, Math.min(list.length - 1, index + offset))];
  if (target) { follow = false; select(target, epoch); }
}
$('previous').onclick = () => moveStep(-1);
$('next').onclick = () => moveStep(1);

let modeBusy = false, modeContext = null;
function resetMode() {
  modeContext = null;
  $('mode-apply').disabled = true; $('mode-rollback').disabled = true;
  _ui($('mode-status'), 'textContent', () => (_t("폴더를 선택한 뒤 현재 방식을 확인하세요.")), false);
}
$('folder').addEventListener('input', resetMode);
$('token').addEventListener('input', resetMode);
async function updateMode(action) {
  if (modeBusy) return;
  if (chatting && action !== 'status') { _ui($('mode-status'), 'textContent', () => (_t("질문이 끝난 뒤 검색 방식을 변경하세요.")), false); return; }
  const folder = $('folder').value.trim(), auth = $('token').value;
  if (!folder || !auth) { resetMode(); return; }
  const key = JSON.stringify([folder, auth]);
  if (action !== 'status' && modeContext !== key) { resetMode(); return; }
  modeBusy = true; $('mode-check').disabled = true;
  $('mode-apply').disabled = true; $('mode-rollback').disabled = true;
  _ui($('mode-status'), 'textContent', () => (action === 'status' ? _t("현재 방식 확인 중…") : _t("검색 방식 변경 중…")), false);
  try {
    const response = await _fetch(action === 'status' ? `/document-mode?folder=${encodeURIComponent(folder)}` : `/document-mode/${action}`, {
      method: action === 'status' ? 'GET' : 'POST', cache:'no-store',
      headers:{Authorization:`Bearer ${auth}`, ...(action === 'status' ? {} : {'Content-Type':'application/json'})},
      ...(action === 'status' ? {} : {body:JSON.stringify({folder})})
    });
    if (JSON.stringify([$('folder').value.trim(), $('token').value]) !== key) return;
    const data = await response.json();
    if (JSON.stringify([$('folder').value.trim(), $('token').value]) !== key) return;
    if (!response.ok) {
      const messages = {get 'workspace busy'() { return _t("색인 또는 다른 변경 작업이 진행 중입니다. 완료 후 다시 확인하세요."); },get 'index changed after transition; rollback refused to preserve current data'() { return _t("전환 이후 색인이 변경되어 되돌릴 수 없습니다. 현재 데이터는 유지됩니다."); },get 'repair failed documents before switching document mode'() { return _t("처리 실패 문서를 먼저 복구한 뒤 전환하세요."); },get 'snapshot generation missing'() { return _t("추출 원문이 누락되었거나 손상되었습니다. 기존 방식에서 색인을 복구하세요."); }};
      throw new Error(response.status === 401 ? _t("앱을 다시 실행해 서버에 연결하세요.") : response.status === 404 ? _t("색인된 폴더를 선택하세요.") : messages[data.detail] || _t("변경하지 못했습니다. 폴더 상태를 다시 확인하세요."));
    }
    modeContext = key;
    _ui($('mode-status'), 'textContent', () => (data.policy === 'selected' ? _t("선택 임베딩 파일만 청킹 · {0}", data.rollback_available ? _t("이전 방식으로 되돌릴 수 있습니다.") : _t("이전 방식으로 되돌리기 불가")) : _t("전 파일 청킹 정책 · 선택 임베딩 파일만 청킹하도록 전환할 수 있습니다.")), false);
    $('mode-apply').disabled = data.policy !== 'legacy';
    $('mode-rollback').disabled = !data.rollback_available;
  } catch (error) {
    if (JSON.stringify([$('folder').value.trim(), $('token').value]) === key) {
      modeContext = null; _ui($('mode-status'), 'textContent', () => (error.message), false);
    }
  } finally { modeBusy = false; $('mode-check').disabled = false; }
}
$('mode-check').onclick = () => updateMode('status');
$('mode-apply').onclick = () => updateMode('apply');
$('mode-rollback').onclick = () => updateMode('rollback');


let browseRoot = '', browseRelative = '.', browseVersion = 0;
let folderAnchor = null;
function renderFolderTree(entries, container, root, auth, version, prefix = '') {
  for (const entry of [...entries].sort((a,b) => (a.kind !== 'folder') - (b.kind !== 'folder') || a.name.localeCompare(b.name))) {
    const path = prefix ? prefix + '/' + entry.name : entry.name;
    if (entry.kind === 'folder') {
      const group = document.createElement('details'); group.className = 'folder-tree';
      const summary = document.createElement('summary'); _ui(summary, 'textContent', () => (entry.name), false);
      const children = document.createElement('div'); children.className = 'folder-tree-children';
      const scopeButton = document.createElement('button'); scopeButton.type = 'button';
      scopeButton.className = 'tree-scope'; _ui(scopeButton, 'textContent', () => (_t("이 폴더에서 질문")), false);
      scopeButton.onclick = () => browseFolder((browseRelative === '.' ? '' : browseRelative + '/') + path, browseRoot || root);
      group.append(summary); group.append(scopeButton); group.append(children); container.append(group);
      let loaded = false, loading = false;
      group.ontoggle = async () => {
        if (!group.open || loaded || loading) return;
        loading = true; _ui(children, 'textContent', () => (_t("불러오는 중…")), false);
        try {
          const response = await _fetch(`/folders?folder=${encodeURIComponent(root)}&relative=${encodeURIComponent(path)}&metadata_only=true`,
            {headers:{Authorization:`Bearer ${auth}`},cache:'no-store'});
          if (!response.ok) throw new Error(_t("폴더를 불러오지 못했습니다. 접었다 다시 펼쳐 주세요."));
          const data = await response.json();
          if (version !== browseVersion || root !== $('folder').value || auth !== $('token').value) return;
          _ui(children, 'textContent', () => (''), false); children.replaceChildren();
          renderFolderTree(data.entries, children, root, auth, version, path); loaded = true;
          choosePreparation(false);
          document.dispatchEvent?.(new CustomEvent('semantic-folder-changed'));
        } catch (error) { if (version === browseVersion) _ui(children, 'textContent', () => (error.message), false); }
        finally { loading = false; }
      };
    } else {
      const row = document.createElement('p'); row.dataset.fileName = path;
      const label = document.createElement('button'); label.type = 'button';
      label.className = 'document-open'; _ui(label, 'textContent', () => (entry.name), false);
      _ui(label, 'title', () => (_t("기본 앱으로 열기")), false);
      label.onclick = async () => {
        if (label.disabled) return;
        label.disabled = true;
        try {
          const headers = {Authorization:`Bearer ${auth}`, 'Content-Type':'application/json'};
          const response = await _fetch('/registered-folders', {headers, cache:'no-store'});
          if (!response.ok) throw new Error(_t("연결 폴더를 확인하지 못했습니다."));
          const folders = (await response.json()).folders;
          const normalize = value => value.replaceAll('\\', '/').replace(/\/$/, '');
          const absolute = normalize(root) + '/' + path;
          const owner = folders.filter(f => absolute.startsWith(normalize(f.path) + '/'))
            .sort((a,b) => b.path.length - a.path.length)[0];
          if (!owner) throw new Error(_t("폴더를 먼저 연결해 주세요."));
          const opened = await _fetch(`/registered-folders/${encodeURIComponent(owner.id)}/open-file`, {
            method:'POST', headers, body:JSON.stringify({relative:absolute.slice(normalize(owner.path).length + 1)})
          });
          if (!opened.ok) {
            const code = (await opened.json()).detail;
            const messages = {get file_missing() { return _t("원본 파일을 찾을 수 없습니다. 폴더를 새로고침해 주세요."); },get folder_disconnected() { return _t("폴더를 다시 연결해 주세요."); },get unsupported_type() { return _t("기본 앱 열기를 지원하지 않는 파일 형식입니다."); },get opener_unavailable() { return _t("현재 실행 환경의 원본 열기 연결을 사용할 수 없습니다. 개발 실행기로 다시 시작해 주세요."); },get outside_scope() { return _t("연결된 폴더의 허용된 문서만 열 수 있습니다."); }};
            throw new Error(messages[code] || _t("기본 앱으로 열지 못했습니다. 파일 연결 설정을 확인하세요."));
          }
          _ui($('folder-status'), 'textContent', () => (_t("기본 앱에 열기를 요청했습니다.")), false);
        } catch (error) { _ui($('folder-status'), 'textContent', () => error.message, false); }
        finally { setTimeout(() => { label.disabled = false; }, 500); }
      };
      row.append(label);
      container.append(row);
    }
  }
}
async function browseFolder(relative, root = browseRoot, anchor = null) {
  if (chatting) { _ui($('folder-status'), 'textContent', () => (_t("질문 완료 후 폴더를 변경하세요.")), false); return; }
  const auth = $('token').value;
  if (!auth || !root) { _ui($('folder-status'), 'textContent', () => (_t("서버 연결 후 탐색 목록에서 폴더를 선택하세요.")), false); return; }
  if (anchor && anchor.dataset.folderPath === $('folder').value) {
    $('document-panel').open = !$('document-panel').open;
    return true;
  }
  const initialFolder = $('folder').value;
  const version = ++browseVersion;
  $('folder-path-details').hidden = true;
  _ui($('folder-status'), 'textContent', () => (_t("폴더 조회 중")), false);
  try {
    const response = await _fetch(`/folders?folder=${encodeURIComponent(root)}&relative=${encodeURIComponent(relative)}`,
      {headers:{Authorization:`Bearer ${auth}`}, cache:'no-store'});
    if (!response.ok) throw new Error(_t("폴더 조회 실패 ({0})" , response.status));
    const data = await response.json();
    if (version !== browseVersion || chatting || $('folder').value !== initialFolder || $('token').value !== auth) return;
    browseRoot = root; browseRelative = data.relative;
    $('folder-up').hidden = data.relative === '.';
    $('document-panel').open = true;
    folderAnchor = anchor;
    const changedFolder = $('folder').value !== data.folder;
    if (changedFolder) {
      saveDraft();
      clearConversation();
      stop(); resetView(); _ui($('answer'), 'textContent', () => (''), false); $('session').value = ''; $('run').value = '';
    }
    historyVersion++;
    $('history-more').hidden = true;
    $('folder').value = data.folder;
    document.dispatchEvent?.(new CustomEvent('folder-selected', {detail:{anchor:folderAnchor}}));
    if (changedFolder) restoreDraft();
    localSelection = {folder:data.folder, auth};
    $('local-connect').disabled = false;
    _ui($('local-connect'), 'textContent', () => (_t("폴더 등록")), false);
    _ui($('scope-name'), 'textContent', () => (data.folder.split(/[\\/]/).filter(Boolean).pop() || '/'), false);
    _ui($('scope-name'), 'title', () => (data.folder), false);
    $('history-list').replaceChildren(); _ui($('history-status'), 'textContent', () => (''), false);
    resetPreparationSelection();
    $('folder-list').replaceChildren();
    renderFolderTree(data.entries, $('folder-list'), data.folder, auth, version);
    $('folder-path-details').hidden = false;
    $('folder-path-details').open = false;
    _ui($('folder-path-label'), 'textContent', () => (data.folder.split(/[\\/]/).filter(Boolean).pop() || '/'), false);
    _ui($('folder-path-label'), 'title', () => (_t("전체 경로 보기 · ") + data.folder), false);
    _ui($('folder-full-path'), 'textContent', () => (data.folder), false);
    _ui($('folder-status'), 'textContent', () => (_t("하위 문서 포함 · {0}개 표시" , data.entries.length) +
      (data.limited ? _t(" · 1,000개를 초과하여 일부 목록만 표시합니다.") : '')), false);
    _ui($('chat-status'), 'textContent', () => (''), false);
    document.dispatchEvent?.(new CustomEvent('semantic-folder-changed'));
    if ($('session-panel').open) await historyRequest();
    const preparation = data.preparation;
    if (preparation && !preparation.needed) {
      excludedFiles = preparation.excluded_files || [];
      choosePreparation(false);
      $('folder-status').dataset.preparing = 'false';
      _ui($('chat-status'), 'textContent', () => (''), false);
    } else {
      void prepareIndex(null, null, true, preparation?.first_visit ?? true);
    }
    return true;
  } catch (error) { if (version === browseVersion) _ui($('folder-status'), 'textContent', () => (error.message), false); }
}
$('folder-open').onclick = () => browseFolder('.', $('folder').value);
// Native menu sends only a user-picked path; normal server scope checks still apply.
if (typeof window !== 'undefined' && window.addEventListener) window.addEventListener('prtfm-native-folder', async event => {
  if (typeof event.detail !== 'string' || !event.detail || chatting) return;
  if (await browseFolder('.', event.detail)) await registeredFolders('POST');
});

$('folder-up').onclick = () => {
  if (!browseRoot || browseRelative === '.') return;
  const parts = browseRelative.split('/'); parts.pop(); browseFolder(parts.join('/') || '.');
};
let indexContext = null, preparationVersion = 0;
let excludedFiles = [], selectedPreparation = new Set();
const preparationControls = new Map();
function resetPreparationSelection() {
  visionContext?.controller?.abort();
  visionContext = null;
  selectedPreparation.clear(); excludedFiles = []; preparationControls.clear();
  $('folder-index').hidden = true;
  $('prepare-selection').hidden = true;
  $('prepare-excluded').close();
}
function preparationCheckbox(path) {
  const check = document.createElement('input'); check.type = 'checkbox'; check.className = 'prepare-check';
  _ua(check, 'aria-label', () => (_t("{0} 문서 준비 대상으로 선택" , path)));
  check.onchange = () => { if (check.checked) selectedPreparation.add(path); else selectedPreparation.delete(path); syncPreparationSelection(); };
  return check;
}
function preparationCandidates() { return [...new Set([...excludedFiles, ...preparationControls.keys()])]; }
function syncPreparationSelection() {
  const candidates = preparationCandidates();
  for (const [path, {check}] of preparationControls) check.checked = selectedPreparation.has(path);
  $('prepare-select-all').checked = candidates.length > 0 && candidates.every(path => selectedPreparation.has(path));
  $('prepare-select-all').indeterminate = selectedPreparation.size > 0 && !$('prepare-select-all').checked;
  $('prepare-select-all').disabled = !candidates.length;
  _ui($('prepare-selected-count'), 'textContent', () => (`${selectedPreparation.size} / ${candidates.length}`), false);
  $('prepare-review').disabled = !selectedPreparation.size || !!visionContext?.running;
  $('prepare-review').hidden = !selectedPreparation.size;
  $('prepare-selection-label').hidden = !!selectedPreparation.size;
  $('prepare-selection').hidden = !candidates.length;
}
function choosePreparation(closeDialog = true) {
  if (closeDialog) $('prepare-excluded').close(); 
  $('prepare-selection').hidden = false; $('folder-index').hidden = true;
  for (const row of ($('folder-list').querySelectorAll?.('[data-file-name]') || [])) {
    const path = row.dataset.fileName;
    if (!excludedFiles.includes(path) || preparationControls.has(path)) continue;
    const check = preparationCheckbox(path); row.append(check);
    preparationControls.set(path, {check});
  }
  for (const {check} of preparationControls.values()) check.hidden = false;
  syncPreparationSelection();
}
$('prepare-select-all').onchange = () => {
  selectedPreparation = new Set($('prepare-select-all').checked ? preparationCandidates() : []);
  syncPreparationSelection();
};
$('prepare-selection-cancel').onclick = () => {
  selectedPreparation.clear(); syncPreparationSelection();
};
function showExcluded(files = excludedFiles, review = false) {
  $('prepare-execute').hidden=true;$('prepare-execute').disabled=true;$('prepare-consent-row').hidden=true;$('prepare-consent').checked=false;$('prepare-stop').hidden=true;$('prepare-quote-retry').hidden=true;_ui($('prepare-cost-note'), 'textContent', () => (''), false);
  $('prepare-excluded-list').replaceChildren();
  _ui($('prepare-excluded-title'), 'textContent', () => (review ? _t("준비 대상 확인") : _t("추가 문서 준비")), false);
  _ui($('prepare-excluded-status'), 'textContent', () => (review ? _t("{0}개 선택" , files.length) : _t("{0}개 · 자동 준비에서 제외된 파일" , files.length)), false);
  for (const path of files) {
    const row = document.createElement('div'); row.className = 'prepare-cost-row';
    const name = document.createElement('span'); _ui(name, 'textContent', () => (path), false); row.append(name);
    const cost = document.createElement('span'); _ui(cost, 'textContent', () => (_t("별도 준비 필요")), false); const target=document.createElement('span');_ui(target, 'textContent', () => ('—'), false);row.append(target);row.append(cost);
    if (review) {
      const remove = document.createElement('button'); remove.type = 'button'; _ui(remove, 'textContent', () => ('×'), false); remove.className = 'prepare-remove'; _ui(remove, 'title', () => (_t("준비 대상에서 제외")), false);
      _ua(remove, 'aria-label', () => (_t("{0} 준비 대상에서 제외" , path)));
      remove.onclick = () => { selectedPreparation.delete(path); syncPreparationSelection(); showExcluded([...selectedPreparation], true); };
      row.append(remove);
    }
    $('prepare-excluded-list').append(row);
  }
  _ui($('prepare-cost-total'), 'textContent', () => (files.length ? '' : _t("선택한 문서가 없습니다.")), false);
  if (!$('prepare-excluded').open) $('prepare-excluded').showModal();
}
let visionContext = null;
function visionCurrent(ctx) { return visionContext === ctx && ctx.folder === $('folder').value && ctx.auth === $('token').value; }
async function visionApi(ctx, path, body, signal) {
  const response = await _fetch(path, {method:body ? 'POST' : 'GET', headers:{Authorization:'Bearer '+ctx.auth, 'Content-Type':'application/json'}, ...(body ? {body:JSON.stringify({folder:ctx.folder,...body})} : {}), signal, cache:'no-store'});
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === 'string' ? data.detail : _t("문서 준비 요청에 실패했습니다."));
  return data;
}
function visionAmounts(ctx) {
  return (ctx.quote?.files || []).filter(item => selectedPreparation.has(item.path));
}
function renderVisionQuote(ctx) {
  const files = visionAmounts(ctx);
  $('prepare-excluded-list').replaceChildren();
  for (const item of files) {
    const row = document.createElement('div'); row.className='prepare-cost-row';
    const name = document.createElement('span'); _ui(name, 'textContent', () => (item.path), false);
    const target=document.createElement('span');_ui(target, 'textContent', () => (item.reuse_result?_t("재사용"):item.vision_pages+' / '+item.page_count+_t("쪽")), false);_ui(target, 'title', () => (_t("AI 처리 페이지 / 전체 페이지")), false);
    const price = document.createElement('span'); _ui(price, 'textContent', () => ('$'+item.low_usd.toFixed(5)+'–$'+item.high_usd.toFixed(5)), false);
    const remove = document.createElement('button'); remove.type='button'; remove.className='prepare-remove'; _ui(remove, 'textContent', () => ('×'), false); remove.disabled=!!ctx.running;
    _ua(remove, 'aria-label', () => (item.path+_t(" 준비 대상에서 제외")));
    remove.onclick=()=>{selectedPreparation.delete(item.path);$('prepare-consent').checked=false;syncPreparationSelection();renderVisionQuote(ctx);};
    row.append(name);row.append(target);row.append(price);row.append(remove);$('prepare-excluded-list').append(row);
  }
  const low=files.reduce((sum,f)=>sum+f.low_usd,0), high=files.reduce((sum,f)=>sum+f.high_usd,0);
  for (const path of ctx.requested || []) {
    if (!selectedPreparation.has(path) || files.some(item=>item.path===path)) continue;
    const row=document.createElement('div');row.className='prepare-cost-row';
    const name=document.createElement('span');_ui(name, 'textContent', () => (path), false);
    const target=document.createElement('span');_ui(target, 'textContent', () => ('—'), false);
    const status=document.createElement('span');
    const error=(ctx.quote.errors||[]).find(item=>item.path===path)?.error;
    if(error){_ui(status, 'textContent', () => (_t("견적 실패")), false);_ui(status, 'title', () => (error), false);const reason=document.createElement('small');_ui(reason, 'textContent', () => (error), false);name.append(reason);}
    else {const spinner=document.createElement('span');spinner.className='pixel-spinner';spinner.setAttribute('role','status');_ua(spinner, 'aria-label', () => (_t("예상 비용 계산 중")));for(let n=0;n<8;n++){const pixel=document.createElement('i');pixel.setAttribute('aria-hidden','true');spinner.append(pixel);}status.append(spinner);} 
    const remove=document.createElement('button');remove.type='button';remove.className='prepare-remove';_ui(remove, 'textContent', () => ('×'), false);
    remove.disabled=!!ctx.running;
    _ua(remove, 'aria-label', () => (path+_t(" 준비 대상에서 제외")));
    remove.onclick=()=>{selectedPreparation.delete(path);$('prepare-consent').checked=false;syncPreparationSelection();renderVisionQuote(ctx);};
    row.append(name);row.append(target);row.append(status);row.append(remove);$('prepare-excluded-list').append(row);
  }
  const unresolved=(ctx.requested||[]).some(path=>selectedPreparation.has(path)&&!files.some(item=>item.path===path));
  _ui($('prepare-cost-total'), 'textContent', () => (unresolved?_t("예상 합계 · 미확정  —"):_t("예상 합계  $")+low.toFixed(5)+'–$'+high.toFixed(5)), false);
  _ui($('prepare-cost-note'), 'textContent', () => (unresolved?_t("실패한 문서를 다시 계산하거나 제외해 주세요."):_t("예상 금액이며 실제 청구액과 다를 수 있습니다.")), false);
  $('prepare-consent-row').hidden=!ctx.finished;
  $('prepare-execute').hidden=!ctx.finished;
  $('prepare-execute').disabled=!!ctx.running || !ctx.finished || unresolved || !ctx.quote.can_execute || !files.length || !$('prepare-consent').checked;
  $('prepare-consent').disabled=!!ctx.running;
  const execute=$('prepare-execute');execute.setAttribute('aria-busy',String(!!ctx.running));execute.replaceChildren();
  if(ctx.running){const spinner=document.createElement('span');spinner.className='pixel-spinner';spinner.setAttribute('aria-hidden','true');for(let n=0;n<8;n++)spinner.append(document.createElement('i'));execute.append(spinner);}
  const label=document.createElement('span');_ui(label, 'textContent', () => (ctx.running?_t("준비 중"):_t("문서 준비")), false);execute.append(label);

}
async function streamVisionEstimate(ctx, onEvent) {
  let timer;
  const arm=()=>{clearTimeout(timer);timer=setTimeout(()=>ctx.controller.abort(),70000);};
  arm();
  let reader;
  try {
    const response=await _fetch('/vision/estimate',{method:'POST',headers:{Authorization:'Bearer '+ctx.auth,'Content-Type':'application/json'},body:JSON.stringify({folder:ctx.folder,files:ctx.requested,progress:true}),signal:ctx.controller.signal});
    if(!response.ok){const data=await response.json();throw new Error(typeof data.detail==='string'?data.detail:_t("견적 분석 요청에 실패했습니다."));}
    if(!response.body?.getReader)return await response.json();
    reader=response.body.getReader();const decoder=new TextDecoder();let buffer='',quote=null;
    while(true){
      const chunk=await reader.read();arm();
      buffer+=decoder.decode(chunk.value||new Uint8Array(),{stream:!chunk.done});
      const lines=buffer.split('\n');buffer=lines.pop();
      if(chunk.done && buffer.trim()){lines.push(buffer);buffer='';}
      for(const line of lines){if(!line.trim())continue;const event=JSON.parse(line);if(event.kind==='error')throw new Error(event.error);if(event.kind==='done')quote=event.quote;else onEvent(event);}
      if(chunk.done)break;
    }
    if(!quote)throw new Error(_t("분석 연결이 끊겼습니다. 다시 계산하세요."));
    return quote;
  } finally {clearTimeout(timer);if(reader){await reader.cancel().catch(()=>{});reader.releaseLock();}}
}
async function requestVisionQuote() {
  if (!selectedPreparation.size) return;
  visionContext?.controller?.abort();
  const ctx={folder:$('folder').value,auth:$('token').value,requested:[...selectedPreparation],finished:false,quote:{files:[],errors:[],can_execute:false}}; visionContext=ctx;
  showExcluded([...selectedPreparation],true);
  _ui($('prepare-excluded-title'), 'textContent', () => (_t("문서 준비")), false);
  _ui($('prepare-excluded-status'), 'textContent', () => (''), false);
  ctx.controller=new AbortController();
  renderVisionQuote(ctx);
  try {
    ctx.quote=await streamVisionEstimate(ctx, event=>{
      if(!visionCurrent(ctx))return;
      if(event.kind==='file')ctx.quote.files.push(event.file);
      if(event.kind==='file_error')ctx.quote.errors.push(event.file);
      if(event.kind==='file' || event.kind==='file_error')renderVisionQuote(ctx);
    });
    ctx.finished=true;
    if(visionCurrent(ctx))$('prepare-quote-retry').hidden=!ctx.quote.errors?.length;
    if(!visionCurrent(ctx)) return;
    renderVisionQuote(ctx);
    _ui($('prepare-excluded-status'), 'textContent', () => (ctx.quote.errors?.length?_t("일부 견적을 확인하지 못했습니다."):ctx.quote.can_execute?'':_t("서버 API 키를 설정한 후 다시 계산해주세요.")), false);
  } catch(error) {if(visionCurrent(ctx)) {_ui($('prepare-excluded-status'), 'textContent', () => (error.name==='AbortError'?_t("분석 응답이 지연되어 요청을 종료했습니다. 잠시 후 다시 시도하세요."):error.message), false);$('prepare-quote-retry').hidden=false;}}
  finally {if(visionCurrent(ctx))$('prepare-excluded-status').className='';}
}
$('prepare-review').onclick = requestVisionQuote;
$('prepare-quote-retry').onclick = requestVisionQuote;
$('prepare-consent').onchange=()=>{if(visionContext?.quote)renderVisionQuote(visionContext);};
$('prepare-execute').onclick=async()=>{
  const ctx=visionContext;
  if(!ctx?.quote || !ctx.finished || $('prepare-execute').disabled || ctx.running || !$('prepare-consent').checked || !visionAmounts(ctx).length) return;
  ctx.running=true;syncPreparationSelection();renderVisionQuote(ctx);$('prepare-quote-retry').hidden=true;
  try {
    await visionApi(ctx,'/vision/run',{quote_id:ctx.quote.quote_id,files:visionAmounts(ctx).map(f=>f.path),consent:true});
    if(!visionCurrent(ctx))return;
    indexContext={folder:ctx.folder,auth:ctx.auth};$('folder-cancel').disabled=false;
    $('prepare-stop').hidden=false;_ui($('prepare-excluded-status'), 'textContent', () => (_t("문서 준비 중…")), false);
    while(visionCurrent(ctx)) {
      const job=await visionApi(ctx,'/index/status?folder='+encodeURIComponent(ctx.folder));
      if(!visionCurrent(ctx))return;
      if(job.quote_id!==ctx.quote.quote_id)throw new Error(_t("작업 상태가 변경되었습니다. 폴더를 다시 열어 결과를 확인하세요."));
      _ui($('prepare-excluded-status'), 'textContent', () => (_t("문서 준비 ")+job.done+' / '+job.total+_t("쪽")), false);
      if(job.state!=='running') {
        ctx.running=false;indexContext=null;$('folder-cancel').disabled=true;
        $('prepare-stop').hidden=true;$('prepare-execute').hidden=true;$('prepare-consent-row').hidden=true;
        const completed=(job.results||[]).filter(r=>r.state==='done').map(r=>r.path);
        for(const path of completed) {const c=preparationControls.get(path);if(c){c.check.hidden=true;if(c.prepare)c.prepare.hidden=true;preparationControls.delete(path);}selectedPreparation.delete(path);}
        excludedFiles=excludedFiles.filter(path=>!completed.includes(path));syncPreparationSelection();
        _ui($('prepare-excluded-status'), 'textContent', () => ((job.state==='cancelled'?_t("중지됨"):job.state==='error'?_t("준비 실패"):_t("준비 완료"))+_t(" · 성공 ")+completed.length+_t("개 · 실패 ")+(job.results||[]).filter(r=>r.state==='failed').length+_t("개")), false);
        _ui($('prepare-cost-total'), 'textContent', () => (job.cost_usd==null?_t("실제 사용량을 확인하지 못했습니다."):_t("확인된 사용 비용 $")+job.cost_usd.toFixed(6)+(job.unknown_usage?_t(" · 일부 요청 비용 확인 불가"):'')), false);
        _ui($('prepare-cost-note'), 'textContent', () => (_t("완료된 문서는 검색에 반영됩니다. 실패·중지된 요청에서도 비용이 발생할 수 있습니다.")), false);
        if(selectedPreparation.size)$('prepare-quote-retry').hidden=false;
        document.dispatchEvent?.(new CustomEvent('semantic-folder-changed'));
        return;
      }
      await new Promise(resolve=>setTimeout(resolve,800));
    }
  } catch(error) {ctx.running=false;syncPreparationSelection();if(visionCurrent(ctx)) {_ui($('prepare-excluded-status'), 'textContent', () => (error.message), false);$('prepare-execute').hidden=true;$('prepare-quote-retry').hidden=false;$('prepare-stop').hidden=true;}}
};
$('prepare-stop').onclick=async()=>{
  const ctx=visionContext;if(!ctx?.running)return;
  $('prepare-stop').disabled=true;
  try{await visionApi(ctx,'/index/cancel',{});if(visionCurrent(ctx))_ui($('prepare-excluded-status'), 'textContent', () => (_t("중지 요청됨 · 진행 중인 응답을 정리하고 있습니다.")), false);}
  catch(error){if(visionCurrent(ctx))_ui($('prepare-excluded-status'), 'textContent', () => (error.message), false);}
  finally{$('prepare-stop').disabled=false;}
};
function closeVisionQuote(){if(!visionContext?.running){visionContext?.controller?.abort();visionContext=null;}$('prepare-excluded-status').className='';}
$('prepare-excluded-close').onclick = () => {closeVisionQuote();$('prepare-excluded').close();};
$('prepare-excluded').addEventListener('cancel',closeVisionQuote);
$('prepare-retry').onclick = () => prepareIndex(null, null, true);
$('folder-cancel').onclick = async () => {
  const context = indexContext;
  if (!context || context.folder !== $('folder').value || context.auth !== $('token').value) return;
  $('folder-cancel').disabled = true;
  const current = () => indexContext === context && context.folder === $('folder').value && context.auth === $('token').value;
  try {
    const response = await _fetch('/index/cancel', {method:'POST', headers:{Authorization:`Bearer ${context.auth}`, 'Content-Type':'application/json'}, body:JSON.stringify({folder:context.folder})});
    if (!current()) return;
    if (!response.ok) throw new Error(response.status === 409 ? _t("진행 중인 준비 작업이 없습니다. 상태 확인 중입니다.") : _t("중지 요청 실패 ({0})" , response.status));
    _ui($('folder-cancel-status'), 'textContent', () => (_t("중지를 요청했습니다. 작업 종료 상태를 확인 중입니다.")), false);
  } catch (error) {
    if (current()) { _ui($('folder-cancel-status'), 'textContent', () => (error.message), false); $('folder-cancel').disabled = false; }
  }
};
async function prepareIndex(file = null, button = null, automatic = false, showNotice = false) {
  if (chatting) { _ui($('folder-status'), 'textContent', () => (_t("질문 완료 후 검색 준비를 시작하세요.")), false); return; }
  const folder = $('folder').value, auth = $('token').value, version = browseVersion;
  if (!folder || !auth) { _ui($('folder-status'), 'textContent', () => (_t("서버 연결 후 폴더를 선택하세요.")), false); return; }
  const task = ++preparationVersion;
  excludedFiles = [];
  $('folder-index').disabled = true;
  $('prepare-retry').hidden = true;
  $('folder-status').dataset.preparing = 'true';
  _ui($('folder-status'), 'textContent', () => (_t("문서 자동 준비 중…")), false);
  if (button) { button.disabled = true; _ui(button, 'textContent', () => ('…'), false); }
  _ui($('folder-cancel-status'), 'textContent', () => (''), false);
  try {
    const response = await _fetch('/index', {method:'POST', headers:{Authorization:`Bearer ${auth}`, 'Content-Type':'application/json'}, body:JSON.stringify({folder, ...(file ? {file} : {}), ...(automatic ? {automatic:true} : {})})});
    if (!response.ok) throw new Error(response.status === 409
      ? _t("다른 문서 준비 작업이 진행 중입니다. 완료 후 자동 준비 재시도를 눌러 주세요.")
      : _t("검색 준비 시작 실패 ({0})" , response.status));
    if (task !== preparationVersion || version !== browseVersion || $('folder').value !== folder || $('token').value !== auth) return;
    indexContext = {folder, auth}; $('folder-cancel').disabled = false;
    while (version === browseVersion && $('folder').value === folder && $('token').value === auth) {
      const status = await _fetch(`/index/status?folder=${encodeURIComponent(folder)}`, {headers:{Authorization:`Bearer ${auth}`}, cache:'no-store'});
      if (!status.ok) throw new Error(_t("준비 상태 조회 실패 ({0})" , status.status));
      const data = await status.json();
      if (task !== preparationVersion || version !== browseVersion || $('folder').value !== folder || $('token').value !== auth) break;
      _ui($('folder-status'), 'textContent', () => (data.state === 'done' ? _t("자동 준비 완료 · PDF·이미지 제외") : _t("검색 준비: {0} ({1}/{2})" , {running:_t("진행 중"),cancelled:_t("중단"),error:_t("실패")}[data.state] || data.state, data.done || 0, data.total || 0)), false);
      if (data.state !== 'running') {
        if (data.report?.errors?.length) _ui($('folder-status'), 'textContent', () => (_t(" 탐색·처리 실패 {0}건." , data.report.errors.length)), true);
        if (data.failed_docs?.length) _ui($('folder-status'), 'textContent', () => (_t(" 정밀 처리 실패 {0}건." , data.failed_docs.length)), true);
        if (data.degraded_pages) _ui($('folder-status'), 'textContent', () => (_t(" 대체 추출 {0}쪽." , data.degraded_pages)), true);
        if (data.state !== 'done') $('prepare-retry').hidden = false;
        if (data.state === 'done') {
          excludedFiles = data.excluded_files || [];
          $('prepare-retry').hidden = !(data.report?.errors?.length);
          _ui($('chat-status'), 'textContent', () => (''), false);
          if (showNotice && excludedFiles.length && !document.querySelector?.('#semantic-file-dialog[open]')) showExcluded();
          await registeredFolders('POST');
        }
        break;
      }
      await new Promise(resolve => setTimeout(resolve, 1000));
    }
  } catch (error) { if (task === preparationVersion && version === browseVersion) { _ui($('folder-status'), 'textContent', () => (error.message), false); $('prepare-retry').hidden = false; } }
  finally { if (task !== preparationVersion) return; $('folder-status').dataset.preparing = 'false'; indexContext = null; $('folder-index').disabled = false; choosePreparation(false); $('folder-cancel').disabled = true;
    if (button) { button.disabled = false; button.innerHTML = prepareIcon; }
    document.dispatchEvent?.(new CustomEvent('semantic-folder-changed'));
  }
}
$('folder-index').onclick = choosePreparation;


let catalogVersion = 0;
$('semantic-library-close').onclick = () => { catalogVersion++; $('semantic-library-dialog').close(); };
$('semantic-library').onclick = async () => {
  const version = ++catalogVersion, auth = $('token').value;
  const dialog = $('semantic-library-dialog');
  dialog.showModal(); $('semantic-library-list').replaceChildren();
  _ui($('semantic-library-status'), 'textContent', () => (_t("등록 파일을 불러오는 중…")), false);
  try {
    const response = await _fetch('/semantic/catalog', {headers:{Authorization:`Bearer ${auth}`},cache:'no-store'});
    if (!response.ok) throw new Error(_t("목록 조회 실패 ({0})" , response.status));
    const data = await response.json();
    if (version !== catalogVersion || auth !== $('token').value || !dialog.open) return;
    _ui($('semantic-library-status'), 'textContent', () => (_t("{0}개 등록 · {1}" , data.files.length, data.root) +
      (data.unavailable_workspaces ? _t(" · {0}개 저장소 조회 실패" , data.unavailable_workspaces) : '') +
      (data.files.length ? '' : _t(" · 임베딩 등록 파일이 없습니다."))), false);
    for (const file of data.files) {
      const button = document.createElement('button'); button.type = 'button';
      const ready = file.enabled && file.embedded && file.state === 'ready';
      const status = !file.available ? _t("원본 파일 없음") : ready ? _t("준비 완료") :
        ({delete_failed:_t("삭제 실패"),failed:_t("실패"),error:_t("실패"),running:_t("처리 중"),embedding:_t("처리 중")}[file.state] || _t("준비 또는 갱신 필요"));
      _ui(button, 'textContent', () => (`${file.relative} · ${(!file.available ? _t("원본 파일 없음") : ready ? _t("준비 완료") :
        ({delete_failed:_t("삭제 실패"),failed:_t("실패"),error:_t("실패"),running:_t("처리 중"),embedding:_t("처리 중")}[file.state] || _t("준비 또는 갱신 필요")))}`), false);
      const scope = document.createElement('small'); _ui(scope, 'textContent', () => (_t("연결 폴더: {0}" , file.folder)), false); button.append(scope);
      _ui(button, 'title', () => (_t("이 임베딩이 등록된 폴더 열기")), false);
      button.onclick = async () => {
        if (chatting) { _ui($('semantic-library-status'), 'textContent', () => (_t("답변이 끝난 뒤 폴더를 열어 주세요.")), false); return; }
        const opened = await browseFolder('.',file.folder);
        if (opened) { catalogVersion++; dialog.close(); }
        else _ui($('semantic-library-status'), 'textContent', () => (_t("폴더를 열 수 없습니다. 원본 위치와 연결 상태를 확인하세요.")), false);
      };
      $('semantic-library-list').append(button);
    }
  } catch (error) {
    if (version === catalogVersion) _ui($('semantic-library-status'), 'textContent', () => (error.message), false);
  }
};

let disconnectTarget = null, disconnectBusy = false;
function openDisconnect(folder) {
  if (disconnectBusy) return;
  disconnectTarget = folder;
  _ui($('disconnect-title'), 'textContent', () => (folder.workspaceKey ? _t("폴더 앱 데이터 삭제") : _t("폴더 연결 해제")), false);
  $('disconnect-only').hidden = Boolean(folder.workspaceKey);
  _ui($('disconnect-path'), 'textContent', () => (folder.path), false);
  _ui($('disconnect-status'), 'textContent', () => (''), false);
  $('disconnect-consent').checked = false;
  $('disconnect-delete').disabled = true;
  $('folder-disconnect').showModal();
}
$('disconnect-consent').onchange = () => { $('disconnect-delete').disabled = disconnectBusy || !$('disconnect-consent').checked; };
$('disconnect-cancel').onclick = () => { if (!disconnectBusy) $('folder-disconnect').close(); };
$('folder-disconnect').addEventListener('cancel', event => { if (disconnectBusy) event.preventDefault(); });
async function disconnectFolder(deleteData) {
  if (!disconnectTarget || disconnectBusy || (deleteData && !$('disconnect-consent').checked)) return;
  const target = disconnectTarget, auth = $('token').value;
  disconnectBusy = true;
  for (const id of ['disconnect-only','disconnect-delete','disconnect-cancel','disconnect-consent']) $(id).disabled = true;
  _ui($('disconnect-status'), 'textContent', () => (deleteData ? _t("앱 데이터 정리 중…") : _t("연결 해제 중…")), false);
  try {
    const url = target.workspaceKey ? `/storage/workspaces/${encodeURIComponent(target.workspaceKey)}/delete` : `/registered-folders/${encodeURIComponent(target.id)}/disconnect`;
    const response = await _fetch(url, {
      method:'POST',headers:{Authorization:`Bearer ${auth}`,'Content-Type':'application/json'},
      body:JSON.stringify({mode:deleteData?'delete_data':'disconnect',confirmed:true})});
    if (!response.ok) throw new Error(response.status === 404 ? _t("서버 재시작 후 다시 시도하세요.") : _t("작업 중이거나 정리를 완료하지 못했습니다. 작업 종료 후 다시 시도하세요. 일부 데이터가 정리되었을 수 있습니다."));
    if ($('folder').value === target.path) {
      stop(); resetView(); clearConversation();
      $('folder').value = ''; $('session').value = ''; $('run').value = '';
      $('folder-list').replaceChildren(); $('document-panel').open = false;
      _ui($('chat-status'), 'textContent', () => (''), false); _ui($('scope-name'), 'textContent', () => (_t("폴더 미선택")), false);
      browseRoot = ''; browseRelative = '.'; browseVersion++;
      resetPreparationSelection();
      document.dispatchEvent?.(new CustomEvent('folder-selected', {detail:{anchor:null}}));
    }
    $('folder-disconnect').close();
    document.dispatchEvent?.(new CustomEvent('prtfm-storage-changed'));
    await registeredFolders();
    if ($('session-panel').open) await historyRequest();
  } catch (error) { _ui($('disconnect-status'), 'textContent', () => (error.message), false); }
  finally {
    disconnectBusy = false;
    for (const id of ['disconnect-only','disconnect-cancel','disconnect-consent']) $(id).disabled = false;
    $('disconnect-delete').disabled = !$('disconnect-consent').checked;
  }
}
document.addEventListener('prtfm-storage-delete-folder', event => openDisconnect(event.detail));
$('disconnect-only').onclick = () => disconnectFolder(false);
$('disconnect-delete').onclick = () => disconnectFolder(true);

let registryVersion = 0;
async function registeredFolders(method = 'GET', id = '') {
  const auth = $('token').value;
  const version = ++registryVersion;
  $('registered-folders').replaceChildren();
  if (!auth) { _ui($('registered-status'), 'textContent', () => (_t("앱을 다시 실행해 서버에 연결하세요.")), false); return; }
  _ui($('registered-status'), 'textContent', () => (_t("등록 폴더 조회 중")), false);
  try {
    const headers = {Authorization:`Bearer ${auth}`, 'Content-Type':'application/json'};
    if (method !== 'GET') {
      const response = await _fetch('/registered-folders' + (id ? '/' + encodeURIComponent(id) : ''),
        {method, headers, ...(method === 'POST' ? {body:JSON.stringify({folder:$('folder').value})} : {})});
      if (!response.ok) throw new Error(_t("등록 목록 변경 실패 ({0})" , response.status));
    }
    const response = await _fetch('/registered-folders', {headers, cache:'no-store'});
    if (!response.ok) throw new Error(_t("등록 목록 조회 실패 ({0})" , response.status));
    const data = await response.json();
    if (version !== registryVersion || auth !== $('token').value) return;
    for (const folder of data.folders) {
      const row = document.createElement('div');
      const open = document.createElement('button'); open.type = 'button';
      open.className = 'connected-folder';
      _ui(open, 'title', () => (folder.path), false);
      _ui(open, 'textContent', () => ((folder.path.split(/[\\/]/).filter(Boolean).pop() || '/') + (folder.available ? '' : _t(" · 폴더 없음/접근 불가"))), false);
      const folderIcon = document.createElement('span'); folderIcon.className = 'folder-symbol';
      folderIcon.innerHTML = icons.folder; open.prepend(folderIcon);
      open.disabled = !folder.available;
      open.dataset.folderPath = folder.path;
      open.onclick = () => browseFolder('.', folder.path, open);
      const remove = document.createElement('button'); remove.type = 'button';
      remove.innerHTML = icons.close; remove.className = 'remove-folder';
      _ua(remove, 'aria-label', () => (_t("{0} 연결 목록에서 제거" , folder.path)));
      remove.onclick = () => openDisconnect(folder);
      row.append(open); row.append(remove); $('registered-folders').append(row);
    }
    _ui($('registered-status'), 'textContent', () => (data.folders.length ? '' : _t("연결한 폴더가 없습니다.")), false);
    const connected = data.folders.some(folder => folder.path === $('folder').value);
    _ui($('local-connect'), 'textContent', () => (connected ? _t("등록된 폴더") : _t("폴더 등록")), false);
    $('local-connect').disabled = connected || !$('folder').value;
    document.dispatchEvent?.(new CustomEvent('folder-selected', {detail:{anchor:null}}));
  } catch (error) {
    if (version === registryVersion && auth === $('token').value) _ui($('registered-status'), 'textContent', () => (error.message), false);
  }
}
$('folders-refresh').onclick = () => registeredFolders();
$('folder-register').onclick = () => registeredFolders('POST');

let localVersion = 0, localSelection = null;
async function browseLocal(relative = '.') {
  const auth = $('token').value, version = ++localVersion;
  $('local-up').disabled = true;
  localSelection = null; $('picker-connect').disabled = true;
  $('local-folders').replaceChildren();
  if (!auth) { _ui($('local-status'), 'textContent', () => (_t("앱을 다시 실행해 서버에 연결하세요.")), false); return; }
  _ui($('local-status'), 'textContent', () => (_t("폴더 조회 중")), false);
  try {
    const response = await _fetch(`/local-folders?relative=${encodeURIComponent(relative)}`,
      {headers:{Authorization:`Bearer ${auth}`}, cache:'no-store'});
    if (!response.ok) throw new Error(_t("폴더 조회 실패 ({0})" , response.status));
    const data = await response.json();
    if (version !== localVersion || auth !== $('token').value) return;
    localSelection = {folder:data.folder, auth};
    _ui($('picker-path'), 'textContent', () => (data.folder), false);
    $('picker-connect').disabled = false;
    _ui($('local-status'), 'textContent', () => (data.limited ? _t("일부 폴더만 표시됨") : ''), false);
    _ui($('local-refresh'), 'title', () => (_t("탐색 시작 폴더로 이동 · {0}" , data.display_path)), false);
    $('local-up').disabled = data.parent == null;
    _ui($('local-up'), 'title', () => (data.parent == null ? _t("탐색 시작 위치입니다") : _t("뒤로가기")), false);
    $('local-up').onclick = () => {
      if (data.parent == null) return;
      return browseLocal(data.parent);
    };
    for (const entry of data.entries) {
      const button = document.createElement('button'); button.type = 'button';
      _ui(button, 'textContent', () => (entry.name), false);
      button.dataset.folderPath = data.folder.replace(/\/$/, '') + '/' + entry.name;
      button.onclick = async () => {
        if ($('folder-picker').open) return browseLocal(entry.relative);
        const selectionVersion = ++localVersion;
        _ui($('local-status'), 'textContent', () => (_t("{0} 여는 중…" , entry.name)), false);
        button.setAttribute('aria-busy', 'true');
        try {
          const opened = await browseFolder('.', button.dataset.folderPath, button);
          if (selectionVersion !== localVersion) return;
          _ui($('local-status'), 'textContent', () => (opened ? '' : $('folder-status').textContent), false);
          if (opened) localSelection = {folder:button.dataset.folderPath, auth};
        } finally { button.setAttribute('aria-busy', 'false'); }
      };
      const row = document.createElement('div'); row.className = 'local-folder-row';
      row.append(button); $('local-folders').append(row);
    }
  } catch (error) { if (version === localVersion) _ui($('local-status'), 'textContent', () => (error.message), false); }
}
$('local-refresh').onclick = () => browseLocal();
$('folder-add').onclick = () => {
  $('folder-picker').showModal();
  _ui($('picker-status'), 'textContent', () => (''), false);
  browseLocal();
};
$('picker-close').onclick = () => $('folder-picker').close();
$('picker-connect').onclick = async () => {
  const choice = localSelection;
  if (!choice || choice.auth !== $('token').value || chatting) return;
  $('picker-connect').disabled = true;
  try {
    const response = await _fetch('/registered-folders', {method:'POST',
      headers:{Authorization:`Bearer ${choice.auth}`,'Content-Type':'application/json'},
      body:JSON.stringify({folder:choice.folder})});
    if (!response.ok) throw new Error(_t("폴더 연결 실패 ({0})" , response.status));
    $('folder-picker').close();
    await registeredFolders();
    await browseFolder('.', choice.folder);
  } catch (error) { _ui($('picker-status'), 'textContent', () => (error.message), false); }
  finally { $('picker-connect').disabled = false; }
};

$('token').addEventListener('change', () => { registeredFolders(); if ($('session-panel').open) historyRequest(); });
$('local-connect').onclick = async () => {
  const selection = {folder:$('folder').value, auth:$('token').value};
  if (!selection.folder || !selection.auth || selection.auth !== $('token').value || chatting) return;
  const opened = $('folder').value === selection.folder || await browseFolder('.', selection.folder);
  if (opened && $('folder').value === selection.folder && selection.auth === $('token').value) await registeredFolders('POST');
};

let historyVersion = 0, historySelection = 0;
async function historyRequest(session = null, before = null) {
  if (chatting && session) return;
  const version = ++historyVersion, folder = $('folder').value, auth = $('token').value;
  $('history-connect').hidden = true;
  if (!auth.trim()) {
    $('history-list').replaceChildren(); $('history-more').hidden = true;
    _ui($('history-status'), 'textContent', () => (_t("서버 연결이 필요합니다. 연결 방법을 확인하세요. 폴더는 선택하지 않아도 됩니다.")), false);
    $('history-connect').hidden = false;
    return;
  }
  const current = () => version === historyVersion && folder === $('folder').value && auth === $('token').value && (!session || !chatting);
  const query = folder ? `?folder=${encodeURIComponent(folder)}` : '?';
  const more = $(session ? 'turns-more' : 'history-more');
  more.disabled = true;
  const request = async url => {
    const response = await _fetch(url, {headers:{Authorization:`Bearer ${auth}`},cache:'no-store'});
    if (response.status === 401) {
      const error = new Error(_t("서버 연결이 만료되었습니다. 앱을 다시 실행해 연결하세요."));
      error.status = 401; throw error;
    }
    if (!response.ok) throw new Error(!folder && response.status === 422 ? _t("현재 서버는 전체 대화 조회를 지원하지 않습니다. 앱을 다시 실행해 접속하세요.") : _t("대화 조회 실패 ({0})" , response.status));
    return response.json();
  };
  _ui($('history-status'), 'textContent', () => (_t("저장 대화 조회 중")), false);
  try {
    const suffix = before === null ? '' : `&before=${encodeURIComponent(before)}`;
    const data = await request((session ? `/sessions/${encodeURIComponent(session)}/turns${query}` : `/sessions${query}`) + suffix);
    if (!current()) return;
    if (before === null && !session) $('history-list').replaceChildren();
    const entries = session ? data.turns : data.sessions;
    if (session) {
      if (before === null) {
        saveDraft();
        stop(); resetView(); clearConversation();
        $('session').value = session; $('run').value = ''; _ui($('answer'), 'textContent', () => (''), false);
        restoreDraft();
        conversationScope = JSON.stringify([folder, auth, session]);
      }
      const viewVersion = conversationVersion;
      if (before !== null) document.dispatchEvent?.(new CustomEvent('history-prepend-start'));
      for (const entry of before === null ? entries : [...entries].reverse()) {
        appendTurn(entry.question, citationText(entry.answer, []).replaceAll(_t("[출처 확인 불가]"), _t("[출처 조회 전]")), async (body, button) => {
          const selection = ++historySelection;
          const valid = () => viewVersion === conversationVersion && folder === $('folder').value && auth === $('token').value && !chatting;
          if (!valid()) return;
          _ui(button, 'textContent', () => (_t("출처 조회 중")), false);
          try {
            const sources = await request(`/sessions/${encodeURIComponent(session)}/turns/${entry.id}/citations${query}`);
            if (!valid() || selection !== historySelection) { _ui(button, 'textContent', () => (_t("이 답변의 출처 확인")), false); return; }
            renderCitedAnswer(body, entry.answer, sources.citations || [], sources.index_versions || {});
            renderSources(sources.citations || [], sources.index_versions || {});
            _ui(button, 'textContent', () => (sources.citations?.length ? _t("이 답변의 출처 확인") : _t("저장된 출처 없음")), false);
          } catch (error) { if (valid()) _ui(button, 'textContent', () => (_t("{0} · 다시 시도" , error.message)), false); }
        }, before === null ? 'append' : 'prepend', replayAction(folder, auth, session, entry.run_id, viewVersion));
      }
      document.dispatchEvent?.(new CustomEvent(before === null ? 'conversation-restored' : 'history-prepend-end'));
      _ui($('chat-status'), 'textContent', () => (_t("저장된 대화입니다. 다음 질문은 이 대화에 이어집니다.")), false);
      _ui($('history-status'), 'textContent', () => (_t("질문·답변을 시간순으로 표시합니다.") + (data.next_cursor ? _t(" 이전 질문을 더 불러올 수 있습니다.") : _t(" 처음 질문까지 불러왔습니다."))), false);
      more.hidden = !data.next_cursor;
      more.onclick = () => {
        if (viewVersion === conversationVersion && folder === $('folder').value && auth === $('token').value && $('session').value === session)
          return historyRequest(session, data.next_cursor);
      };
      return;
    }
    for (const entry of entries) {
      const button = document.createElement('button'); button.type = 'button';
      _ui(button, 'textContent', () => (entry.title || entry.question || _t("(완료된 질문 없음)")), false);
      _ui(button, 'title', () => (entry.folder || folder), false);
      if (!folder && entry.folder) _ui(button, 'textContent', () => (` · ${entry.folder.split(/[\\/]/).filter(Boolean).pop()}`), true);
      if (Number.isFinite(entry.created)) {
        const date = document.createElement('time');
        date.className = 'history-date';
        date.dateTime = new Date(entry.created * 1000).toISOString();
        _ui(date, 'textContent', () => (new Date(entry.created * 1000).toLocaleDateString(window.PRTFMI18n?.language === 'en' ? 'en-US' : 'ko-KR')), false);
        button.append(date);
      }
      button.onclick = async () => {
        if (folder !== $('folder').value || auth !== $('token').value || chatting) return;
        if (!folder && entry.folder) {
          const opened = await browseFolder('.', entry.folder);
          if (!opened || auth !== $('token').value || $('folder').value !== entry.folder) return;
          await registeredFolders('POST');
          if (auth !== $('token').value || $('folder').value !== entry.folder || chatting) return;
        }
        return historyRequest(entry.id);
      };
      const row = document.createElement('div'); row.className = 'history-row';
      const remove = document.createElement('button'); remove.type = 'button';
      remove.className = 'history-delete'; remove.innerHTML = icons.close;
      _ui(remove, 'title', () => (_t("대화 삭제")), false); _ua(remove, 'aria-label', () => (_t("{0} 삭제" , entry.title || entry.question || _t("대화"))));
      remove.onclick = async () => {
        const valid = () => folder === $('folder').value && auth === $('token').value && !chatting;
        if (!valid()) return;
        if (!confirm(_t("이 대화와 저장된 출처·작업 기록을 삭제할까요? 원본 문서와 임베딩은 유지됩니다."))) return;
        remove.disabled = true;
        try {
          const response = await _fetch(`/sessions/${encodeURIComponent(entry.id)}?folder=${encodeURIComponent(entry.folder || folder)}`,
            {method:'DELETE', headers:{Authorization:`Bearer ${auth}`}});
          if (!response.ok) throw new Error(response.status === 409 ? _t("답변 생성이 끝난 후 삭제하세요.") : _t("대화 삭제 실패 ({0})" , response.status));
          if (!valid()) return;
          if ($('session').value === entry.id) {
            clearConversation(); stop(); resetView(); $('session').value = ''; $('run').value = '';
            _ui($('answer'), 'textContent', () => (''), false); restoreDraft();
            _ui($('chat-status'), 'textContent', () => (_t("대화를 삭제했습니다. 새 질문을 입력하세요.")), false);
          }
          await historyRequest();
          await refreshRecovery();
        } catch (error) { if (valid()) _ui($('history-status'), 'textContent', () => (error.message), false); }
        finally { remove.disabled = false; }
      };
      row.append(button); row.append(remove); $('history-list').append(row);
    }
    _ui($('history-status'), 'textContent', () => (entries.length ? _t("이전 대화를 선택하세요.") : _t("저장된 대화가 없습니다.")), false);
    more.hidden = !data.next_cursor;
    more.onclick = () => { if (current()) return historyRequest(null, data.next_cursor); };
  } catch (error) {
    if (current()) {
      _ui($('history-status'), 'textContent', () => (error.message), false);
      $('history-connect').hidden = error.status !== 401;
      if (before !== null) more.onclick = () => { if (current()) return historyRequest(session, before); };
    }
  }
  finally { more.disabled = false; }
}
$('history-connect').onclick = connectionHelp;
$('session-panel').ontoggle = () => { if ($('session-panel').open) return historyRequest(); };
$('history-new').onclick = () => {
  if (chatting) return;
  saveDraft();
  clearConversation();
  historyVersion++; stop(); resetView(); $('session').value = ''; $('run').value = ''; _ui($('answer'), 'textContent', () => (''), false);
  restoreDraft();
  _ui($('chat-status'), 'textContent', () => (_t("질문을 입력하세요.")), false);
  if ($('session-panel').open) return historyRequest();
};
// Browser handoff: exchange a short-lived, single-use code; never retain it in the URL.
let recoveryVersion = 0;
const recoveryGroups = {get chat() { return _t("중단된 대화"); },get embedding() { return _t("미완료 임베딩 작업"); },get embedding_cost() { return _t("비용 미확정 임베딩 요청"); },get vision() { return _t("미완료 문서 준비"); },get vision_cost() { return _t("비용 미확정 문서 준비"); }};
async function refreshRecovery() {
  const auth = $('token').value, version = ++recoveryVersion;
  if (!auth) return;
  const current = () => auth === $('token').value && version === recoveryVersion;
  $('startup-recovery').hidden = false;
  _ui($('startup-recovery-counts'), 'textContent', () => _t("기록 조회 중…"), false);
  try {
    const response = await _fetch('/desktop/recovery/items', {headers:{Authorization:`Bearer ${auth}`},cache:'no-store'});
    if (!response.ok) throw new Error(_t("이전 작업 기록을 확인하지 못했습니다. 새로고침해 주세요."));
    const report = await response.json();
    if (!current()) return;
    const list = $('recovery-items'); list.replaceChildren();
    const items = report.items || [];
    _ui($('recovery-state'), 'textContent', () => (report.unreadable || report.limited ? _t("[ 일부 조회 ]") : items.length ? _t("[ 확인 필요 ]") : _t("[ 이상 없음 ]")), false);
    _ui($('startup-recovery-counts'), 'textContent', () => (report.unreadable || report.limited
      ? _t("표시 {0}건 · 읽지 못한 저장소 {1}개{2}" , items.length, report.unreadable || 0, report.limited ? _t(" · 조회 상한 도달") : '')
      : items.length ? _t("기록을 열거나 중단된 작업을 정리할 수 있습니다.") : _t("확인이 필요한 이전 기록이 없습니다.")), false);
    for (const [kind, label] of Object.entries(recoveryGroups)) {
      const members = items.filter(item => item.kind === kind);
      if (!members.length) continue;
      const group = document.createElement('details'); group.className = 'recovery-group';
      const summary = document.createElement('summary');
      const name = document.createElement('span'); _ui(name, 'textContent', () => (recoveryGroups[kind]), false);
      const count = document.createElement('span'); count.className = 'recovery-count'; _ui(count, 'textContent', () => (_t("{0}건" , members.length)), false);
      summary.append(name,count); group.append(summary);
      for (const item of members) {
        const row = document.createElement('div'); row.className = 'recovery-item';
        const info = document.createElement('div'), title = document.createElement('strong'), folder = document.createElement('small');
        _ui(title, 'textContent', () => (item.title), false); _ui(folder, 'textContent', () => (item.folder ? item.folder.split(/[\\/]/).filter(Boolean).pop() : _t("폴더 이름 확인 불가")), false); _ui(folder, 'title', () => (item.folder || item.workspace), false);
        info.append(title,folder); row.append(info);
        const actions = document.createElement('div'); actions.className = 'recovery-actions';
        const open = document.createElement('button'); open.type = 'button'; _ui(open, 'textContent', () => (_t("열기 ↗")), false); _ui(open, 'title', () => (kind === 'chat' ? _t("대화 열기") : _t("해당 폴더로 이동")), false); _ua(open, 'aria-label', () => (open.title)); open.disabled = !item.folder;
        open.onclick = async () => {
          if (chatting) { _ui($('recovery-message'), 'textContent', () => (_t("답변 생성이 끝난 후 열어 주세요.")), false); return; }
          try {
            if (!await browseFolder('.',item.folder)) throw new Error(_t("폴더를 열지 못했습니다. 원본 폴더 위치를 확인하세요."));
            if (auth !== $('token').value) return;
            if (kind === 'chat') await historyRequest(item.id);
            $('startup-recovery').open = false;
          } catch(error) { _ui($('recovery-message'), 'textContent', () => (error.message), false); }
        };
        actions.append(open);
        if (['chat','embedding','vision'].includes(kind)) {
          const remove = document.createElement('button'); remove.type = 'button';
          remove.innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 6h16M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7m4-7v7"/></svg>';
          _ui(remove, 'title', () => (kind === 'chat' ? _t("대화 삭제") : _t("미완료 작업 정리")), false); _ua(remove, 'aria-label', () => (`${item.title} · ${remove.title}`));
          remove.onclick = async () => {
            if (chatting) { _ui($('recovery-message'), 'textContent', () => (_t("답변 생성이 끝난 후 정리해 주세요.")), false); return; }
            const warning = kind === 'chat'
              ? _t("이 대화와 저장된 출처·실행 로그·대화별 비용 기록을 삭제할까요? 원본·문서 처리 결과·임베딩은 유지됩니다. 삭제한 대화는 복구할 수 없습니다.")
              : _t("이 미완료 작업을 정리할까요? 원본·완료된 처리 결과·임베딩·비용 기록은 유지됩니다. 문서 준비는 취소 상태로 정리하며, 재처리는 새 견적이 필요합니다.");
            if (!confirm(warning)) return;
            remove.disabled = true;
            try {
              const response = await _fetch('/desktop/recovery/remove', {method:'POST',headers:{Authorization:`Bearer ${auth}`,'Content-Type':'application/json'},body:JSON.stringify({workspace:item.workspace,kind,id:item.id,confirmed:true})});
              if (!response.ok) throw new Error(response.status === 409 ? _t("작업이 실행 중이거나 기록이 변경되었습니다. 새로고침 후 확인하세요.") : _t("정리 실패 ({0})" , response.status));
              if (auth !== $('token').value) return;
              if (kind === 'chat' && $('session').value === item.id && $('folder').value === item.folder) {
                clearConversation(); stop(); resetView(); $('session').value = ''; $('run').value = ''; _ui($('answer'), 'textContent', () => (''), false); restoreDraft();
              }
              await refreshRecovery();
              if (kind === 'chat') await historyRequest();
              _ui($('recovery-message'), 'textContent', () => (_t("정리했습니다. 비용 미확정 기록은 별도로 유지됩니다.")), false);
            } catch(error) { _ui($('recovery-message'), 'textContent', () => (error.message), false); }
            finally { remove.disabled = false; }
          };
          actions.append(remove);
        }
        row.append(actions); group.append(row);
      }
      if (kind.endsWith('_cost')) { const hint=document.createElement('p'); hint.className='hint'; _ui(hint, 'textContent', () => (_t("실제 청구 내역은 제공자에서 확인하세요. 작업을 정리해도 비용 기록은 남습니다.")), false); group.append(hint); }
      list.append(group);
    }
  } catch(error) {
    if (current()) { _ui($('recovery-state'), 'textContent', () => (_t("[ 조회 실패 ]")), false); _ui($('startup-recovery-counts'), 'textContent', () => (error.message), false); $('recovery-items').replaceChildren(); }
  }
}
$('recovery-refresh').onclick = () => { _ui($('recovery-message'), 'textContent', () => (''), false); refreshRecovery(); };
$('recovery-close').onclick = () => { $('startup-recovery').open=false; $('startup-recovery-toggle').focus(); };
document.addEventListener('keydown', event => { if (event.key === 'Escape' && $('startup-recovery').open) { $('startup-recovery').open=false; $('startup-recovery-toggle').focus(); } });
$('startup-recovery').addEventListener('toggle', () => { if ($('startup-recovery').open) refreshRecovery(); });
document.addEventListener('conversations-cleared', refreshRecovery);

async function autoConnect() {
  const key = 'prtfm-server-token';
  const fragment = new URLSearchParams(window.location.hash.slice(1));
  const code = fragment.get('connect');
  if (code) window.history.replaceState(null, '', window.location.pathname + window.location.search);
  let credential = null;
  try {
    if (code) {
      _ui($('connection-status'), 'textContent', () => (_t("서버 연결 중…")), false);
      const response = await _fetch('/activity-ui/connect', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({code}), cache:'no-store'});
      if (!response.ok) throw new Error(_t("연결 코드가 만료되었거나 이미 사용되었습니다. 앱을 다시 실행하세요."));
      credential = (await response.json()).token;
    } else {
      try { credential = window.sessionStorage.getItem(key); } catch {}
    }
    if (!credential) { connectionHelp(); return; }
    const health = await _fetch('/health', {headers:{Authorization:`Bearer ${credential}`},cache:'no-store'});
    if (!health.ok) {
      try { window.sessionStorage.removeItem(key); } catch {}
      throw new Error(_t("서버 연결이 만료되었습니다. 앱을 다시 실행하세요."));
    }
    $('token').value = credential;
    try { window.sessionStorage.setItem(key, credential); } catch {}
    _ui($('connection-status'), 'textContent', () => (_t("연결됨")), false);
    $('connection-status').hidden = true;
    $('token').dispatchEvent(new Event('change'));
    await refreshRecovery();
  } catch (error) {
    $('connection-status').hidden = false;
    _ui($('connection-status'), 'textContent', () => (error.message), false);
    if (developmentMode) $('settings').open = true;
  }
}
if (typeof window !== 'undefined') autoConnect();

// Refresh the visible state after explicit removal without automatically preparing it again.
document.addEventListener('prtfm-processing-removed', async event => {
  const folder = $('folder').value, auth = $('token').value;
  if (folder !== event.detail.folder) return;
  try {
    const response = await _fetch(`/folders?folder=${encodeURIComponent(folder)}&relative=.`,
      {headers:{Authorization:`Bearer ${auth}`},cache:'no-store'});
    if (!response.ok) return;
    const data = await response.json();
    if ($('folder').value !== folder || $('token').value !== auth) return;
    const version = ++browseVersion;
    resetPreparationSelection();
    $('folder-list').replaceChildren();
    renderFolderTree(data.entries, $('folder-list'), folder, auth, version);
    excludedFiles = data.preparation?.excluded_files || [];
    choosePreparation(false);
    _ui($('folder-status'), 'textContent', () => (_t("처리 결과를 삭제했습니다. 필요한 문서는 다시 준비하세요.")), false);
    document.dispatchEvent(new CustomEvent('semantic-folder-changed'));
  } catch { _ui($('folder-status'), 'textContent', () => (_t("처리 결과가 변경되었습니다. 폴더를 다시 열어 확인하세요.")), false); }
});

})();

