(() => {
'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));

const $ = id => document.getElementById(id);
const dialog = $('storage-dialog');
const cleanup = $('storage-cleanup');
const labels = {get processed_documents() { return _t("문서 처리 결과"); },get embeddings() { return _t("임베딩"); },get records() { return _t("기록·메타데이터"); },get analysis_cache() { return _t("분석 캐시"); },get temporary() { return _t("임시 작업 데이터"); },get other() { return _t("기타"); }};
let request = 0, controller, selected, selectedDocument = null, catalogRequest = 0, cleaning = false;
const descriptions = {get processed_documents() { return _t("삭제할 문서를 선택하세요. 선택한 연결 폴더의 처리 결과와 임베딩 연결을 함께 삭제합니다. 다른 폴더에서 사용하는 공유 데이터와 저장된 대화·인용 내용은 보존됩니다. 다시 준비하거나 임베딩하면 비용이 발생할 수 있습니다. 모델 없이 처리하는 문서는 다음 자동 준비 때 다시 생성될 수 있습니다."); },get embeddings() { return _t("등록 파일 목록을 열어 파일을 선택한 뒤 검은 별로 임베딩을 해제하세요. 다른 폴더에서 사용 중인 공유 벡터는 보존됩니다. 다시 임베딩하면 비용이 발생할 수 있습니다."); },get records() { return _t("연결 해제된 폴더를 포함한 전체 앱 저장소의 모든 대화, 저장된 출처, 작업 로그와 대화별 비용 기록을 삭제합니다. 복구할 수 없습니다. 원본 파일·등록 폴더·문서 처리 결과·임베딩은 유지합니다. 백업 파일은 변경하지 않습니다."); },get analysis_cache() { return _t("페이지 분석 결과 중 재계산 가능한 캐시를 정리합니다. 문서 처리 결과·임베딩·대화는 보존됩니다. 다음 견적 분석은 다시 계산하므로 시간이 더 걸릴 수 있습니다."); },get temporary() { return _t("사용이 끝난 검색 임시 데이터만 정리합니다. 실행 중·소유권 불명·지원하지 않는 임시 데이터는 보존됩니다."); }};
function review(key) {
 if (cleaning) return;
 selected = key; selectedDocument = null; catalogRequest++;
 $('storage-processed-list').replaceChildren();
 if (!cleanup.open) cleanup.showModal();
 _ui($('storage-cleanup-cancel'), 'textContent', () => (_t("취소")), false);
 _ui($('storage-cleanup-title'), 'textContent', () => (key === 'records' ? _t("모든 대화 기록을 삭제할까요?") : labels[key] + _t(" 정리")), false);
 _ui($('storage-cleanup-description'), 'textContent', () => (descriptions[key]), false);
 _ui($('storage-cleanup-status'), 'textContent', () => (''), false);
 const button = $('storage-cleanup-confirm');
 button.hidden = key === 'processed_documents';
 _ui(button, 'textContent', () => (key === 'embeddings' ? _t("임베딩 파일 열기") : key === 'records' ? _t("대화 기록 전체 삭제") : _t("정리 실행")), false);
 $('storage-cleanup-title').focus();
 if (key === 'processed_documents') loadProcessed();
}
$('storage-cleanup-cancel').onclick = () => { if (!cleaning) cleanup.close(); };
cleanup.addEventListener('cancel', event => { if (cleaning) event.preventDefault(); });
cleanup.addEventListener('close', () => { catalogRequest++; selectedDocument = null; });
$('storage-cleanup-confirm').onclick = async () => {
 if (cleaning) return;
 if (selected === 'embeddings') {
   cleanup.close();
   dialog.close();
   $('semantic-library').click();
   return;
 }
 if (selected === 'processed_documents' && !selectedDocument) return;
 if (!['analysis_cache','temporary','processed_documents','records'].includes(selected)) return;
 cleaning = true;
 $('storage-cleanup-confirm').disabled = true;
 $('storage-cleanup-cancel').disabled = true;
 _ui($('storage-cleanup-status'), 'textContent', () => (_t("정리 중…")), false);
 try {
   const target = selectedDocument;
   const url = selected === 'processed_documents' ? `/storage/processed/${target.doc_id}/delete` : '/storage/cleanup';
   const body = selected === 'processed_documents' ? {folder:target.folder,revision:target.revision,confirmed:true} : {category:selected === 'records' ? 'conversations' : selected,confirmed:true};
   const response = await _fetch(url, {method:'POST', headers:{Authorization:`Bearer ${$('token').value.trim()}`,'Content-Type':'application/json'},body:JSON.stringify(body)});
   if (!response.ok) throw new Error(response.status === 404 ? _t("서버 재시작 후 정리 기능을 사용할 수 있습니다.") : _t("안전하게 정리하지 못했습니다. 진행 중인 작업을 마친 뒤 다시 시도하세요."));
   const result = await response.json();
   if (selected === 'records') document.dispatchEvent(new CustomEvent('conversations-cleared'));
   _ui($('storage-cleanup-status'), 'textContent', () => (selected === 'records' ? _t("{0}개 대화 삭제 완료. 원본·등록 폴더·처리 결과·임베딩은 유지했습니다. DB 파일 크기는 즉시 줄지 않을 수 있습니다." , result.removed) : selected === 'processed_documents' ? _t("선택한 폴더의 처리 결과·임베딩 연결을 삭제했습니다. 원본과 대화 기록은 보존했습니다.") : result.supported ? _t("{0}개 정리 · {1}개 보존. 원본 파일은 변경하지 않았습니다." , result.removed, result.skipped) : _t("이 환경에서는 검색 임시 데이터 수동 정리를 지원하지 않습니다. 데이터는 삭제하지 않았습니다.")), false);
   if (selected === 'processed_documents') { document.dispatchEvent(new CustomEvent('prtfm-processing-removed', {detail:{folder:target.folder}})); selectedDocument = null; $('storage-cleanup-confirm').hidden = true; $('storage-processed-list').replaceChildren(); }
   $('storage-cleanup-confirm').hidden = true;
   _ui($('storage-cleanup-cancel'), 'textContent', () => (_t("닫기")), false);
   if (dialog.open) await load();
 } catch (error) { _ui($('storage-cleanup-status'), 'textContent', () => (error.message), false); }
 finally { cleaning = false; $('storage-cleanup-confirm').disabled = false; $('storage-cleanup-cancel').disabled = false; }
};

async function loadProcessed() {
 const version = catalogRequest;
 _ui($('storage-cleanup-status'), 'textContent', () => (_t("처리 결과 목록을 불러오는 중…")), false);
 try {
   const response = await _fetch('/storage/processed', {headers:{Authorization:`Bearer ${$('token').value.trim()}`},cache:'no-store'});
   if (!response.ok) throw new Error(_t("목록을 불러오지 못했습니다. 새 기능 적용을 위해 서버를 재시작해 주세요."));
   const data = await response.json();
   if (version !== catalogRequest || !dialog.open || !cleanup.open) return;
   _ui($('storage-cleanup-status'), 'textContent', () => (_t("{0}개 · 조회하지 못한 저장소 {1}개" , data.files.length, data.unavailable)), false);
   for (const file of data.files) {
     const button = document.createElement('button'); button.type = 'button';
     _ui(button, 'textContent', () => (`${file.path} · ${size(file.bytes)} · ${file.folder}`), false);
     button.onclick = () => {
       if (cleaning) return;
       selectedDocument = file;
       _ui($('storage-cleanup-status'), 'textContent', () => (_t("{0} — 연결 폴더: {1}. 이 폴더의 처리 결과와 임베딩 연결을 삭제합니다." , file.path, file.folder)), false);
       $('storage-cleanup-confirm').hidden = false;
       _ui($('storage-cleanup-confirm'), 'textContent', () => (_t("선택 문서 처리 결과 삭제")), false);
     };
     $('storage-processed-list').append(button);
   }
 } catch (error) { if (version === catalogRequest && dialog.open && cleanup.open) _ui($('storage-cleanup-status'), 'textContent', () => (error.message), false); }
}

function size(bytes) {
  if (bytes < 1024) return `${bytes} B`;
  const unit = Math.min(3, Math.floor(Math.log(bytes) / Math.log(1024)));
  return `${(bytes / 1024 ** unit).toFixed(1)} ${['B','KiB','MiB','GiB'][unit]}`;
}
function cancel() { request++; catalogRequest++; controller?.abort(); }
function renderMeter(data) {
 const keys = $('storage-meter-keys'), bar = $('storage-meter-bar');
 keys.replaceChildren(); bar.replaceChildren();
 $('storage-meter-detail').hidden = true;
 $('storage-size').replaceChildren();
 const amount = data.unique_bytes == null || data.status === 'unavailable' ? '—' : size(data.unique_bytes);
 const [number, unit] = amount.split(' ');
 const figure = document.createElement('span'); _ui(figure, 'textContent', () => (number), false); $('storage-size').append(figure);
 if (unit) { const suffix=document.createElement('small'); _ui(suffix, 'textContent', () => (unit), false); $('storage-size').append(suffix); }
 const values = Object.entries(labels).map(([key, label]) => ({key, get label() { return labels[key]; }, bytes:data.categories?.[key]?.logical_bytes})).filter(v => Number.isFinite(v.bytes) && v.bytes > 0);
 const total = values.reduce((sum, v) => sum + v.bytes, 0);
 $('storage-meter').hidden = data.status === 'unavailable' || !total;
 const buttons = [];
 let selectedKey = null;
 for (const [index, value] of values.entries()) {
   const choose = () => {
     selectedKey = selectedKey === value.key ? null : value.key;
     $('storage-meter-detail').hidden = !selectedKey;
     _ui($('storage-meter-detail'), 'textContent', () => (value.label + ' · ' + (descriptions[value.key] || _t("기타 앱 내부 데이터입니다. 일괄 삭제하지 않습니다."))), false);
     for (const button of buttons) button.setAttribute('aria-pressed', String(button.dataset.category === selectedKey));
   };
   const segment = document.createElement('button'); segment.type = 'button';
   segment.dataset.category = value.key;
   segment.className = `storage-tone-${index % 6}`;
   segment.style.flexGrow = String(value.bytes);
   _ua(segment, 'aria-label', () => (`${value.label}, ${size(value.bytes)}, ${(value.bytes / total * 100).toFixed(1)}%`));
   segment.setAttribute('aria-pressed', 'false'); segment.onclick = choose; bar.append(segment); buttons.push(segment);
   const key = document.createElement('button'); key.type = 'button'; key.dataset.category = value.key;
   key.className = `storage-tone-${index % 6}`;
   _ui(key, 'textContent', () => (`${value.label} ${size(value.bytes)}`), false); key.setAttribute('aria-pressed', 'false'); key.onclick = choose;
   keys.append(key); buttons.push(key);
 }
}
async function load() {
  cancel();
  const current = request;
  controller = new AbortController();
  _ui($('storage-status'), 'textContent', () => (_t("저장소 현황을 확인하는 중…")), false);
  $('storage-content').hidden = true;
  dialog.setAttribute('aria-busy', 'true');
  try {
    const token = $('token').value.trim();
    if (!token) throw new Error(_t("서버 연결 후 다시 시도하세요."));
    const response = await _fetch('/storage/summary', {headers:{Authorization:`Bearer ${token}`},signal:controller.signal,cache:'no-store'});
    if (!response.ok) throw new Error(response.status === 503 ? _t("다른 조회가 진행 중입니다. 잠시 후 새로고침하세요.") : _t("저장소를 조회하지 못했습니다. 연결 상태를 확인하고 다시 시도하세요."));
    const data = await response.json();
    if (current !== request || !dialog.open) return;
    renderMeter(data);
    _ui($('storage-path'), 'textContent', () => (data.path), false);
    _ui($('storage-location'), 'textContent', () => (data.custom_location ? _t("별도 지정된 저장 위치") : _t("앱 데이터 저장 위치")), false);
    $('storage-rows').replaceChildren();
    if (data.status !== 'unavailable') {
      for (const [key,label] of Object.entries(labels)) {
        const value = data.categories[key];
        const row = document.createElement('tr');
        for (const text of [() => labels[key], () => _t("{0}개" , value.files), () => size(value.logical_bytes)]) {
          const cell = document.createElement('td'); _ui(cell, 'textContent', () => (typeof text === 'function' ? text() : text), false); row.append(cell);
        }
        const action = document.createElement('td');
        if (key !== 'other') {
          const button = document.createElement('button'); button.type = 'button';
          button.className = 'storage-cleanup-button';
          _ua(button, 'aria-label', () => (_t("{0} 정리 방법 확인" , labels[key])));
          _ui(button, 'title', () => (_t("{0} 정리" , labels[key])), false);
          button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 10v7m4-7v7"/></svg>';
          button.onclick = () => review(key); action.append(button);
        }
        row.append(action);
        $('storage-rows').append(row);
      }
    }
    $('storage-table').hidden = data.status === 'unavailable';
    $('storage-folder-rows').replaceChildren();
    $('storage-folders-table').hidden = data.status === 'unavailable';
    _ui($('storage-folders-note'), 'textContent', () => (!data.workspaces ? _t("폴더별 조회는 서버 재시작 후 사용할 수 있습니다.") :
      [data.status === 'partial' ? _t("일부 집계입니다. 표시되지 않은 데이터가 있을 수 있습니다.") : '',
       data.folder_mapping_complete === false ? _t("일부 연결 정보를 읽지 못했습니다.") : '',
       _t("연결 해제됨에는 연결 목록에 추가하지 않고 준비했던 폴더도 포함됩니다.")].filter(Boolean).join(' ')), false);
    for (const item of [...(data.workspaces || []), ...(data.common ? [{...data.common,folder:_t("공통·공유 데이터"),state:'shared'}] : [])]) {
      const row = document.createElement('tr');
      const name = document.createElement('td');
      const label = document.createElement('span'); _ui(label, 'textContent', () => (item.state === 'shared' ? _t("공통·공유 데이터") : item.folder ? item.folder.split(/[\\/]/).filter(Boolean).pop() || item.folder : _t("이름 확인 불가")), false); name.append(label);
      if (item.state !== 'shared') {
        const details = document.createElement('details'); details.className = 'storage-folder-path';
        const summary = document.createElement('summary'); _ui(summary, 'textContent', () => (item.folder ? _t("경로 보기") : _t("저장소 ID")), false);
        const path = document.createElement('small'); _ui(path, 'textContent', () => (item.folder || _t("저장소 ID: {0}" , item.key)), false);
        details.append(summary); details.append(path); name.append(details);
      }
      row.append(name);
      for (const text of [() => ({connected:_t("연결됨"),disconnected:_t("연결 해제됨"),unknown:_t("확인 불가"),shared:_t("공통")})[item.state], () => _t("{0}개" , item.files), () => size(item.logical_bytes)]) {
        const cell = document.createElement('td'); _ui(cell, 'textContent', () => (typeof text === 'function' ? text() : text), false); row.append(cell);
      }
      const action = document.createElement('td');
      if (item.key && item.folder && ['connected','disconnected'].includes(item.state)) {
        const button = document.createElement('button'); button.type = 'button';
        button.className = 'storage-cleanup-button';
        _ui(button, 'title', () => (_t("{0} 앱 데이터 삭제" , label.textContent)), false);
        _ua(button, 'aria-label', () => (button.title));
        button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 10v7m4-7v7"/></svg>';
        button.onclick = () => {
          if (!cleaning) document.dispatchEvent(new CustomEvent('prtfm-storage-delete-folder', {detail:{workspaceKey:item.key,path:item.folder}}));
        };
        action.append(button);
      }
      row.append(action);
      $('storage-folder-rows').append(row);
    }

    _ui($('storage-status'), 'textContent', () => (data.status === 'unavailable' ? _t("저장 위치에 접근할 수 없습니다. 권한과 경로를 확인하세요.") : !data.exists ? _t("아직 저장된 데이터가 없습니다.") : data.status === 'partial' ? _t("일부 항목만 집계했습니다. 접근 오류 {0}건 · 제외 {1}건{2}" , data.errors, data.skipped_links + data.skipped_special, data.limits_reached.length ? _t(" · 조회 상한 도달") : '') : _t("조회가 완료되었습니다.")), false);
    _ui($('storage-observed'), 'textContent', () => (_t("조회 시각: {0}" , new Date(data.observed_at).toLocaleString())), false);
    _ui($('storage-total'), 'textContent', () => (data.unique_bytes == null ? _t("공유 파일의 중복을 제외한 전체 크기는 이번 조회에서 확인하지 못했습니다.") : _t("공유 파일 중복 제외: {0}" , size(data.unique_bytes))), false);
    $('storage-content').hidden = false;
  } catch (error) {
    if (current === request && dialog.open) _ui($('storage-status'), 'textContent', () => (error.message), false);
  } finally {
    if (current === request) dialog.setAttribute('aria-busy', 'false');
  }
}
function open() { if (!dialog.open) dialog.showModal(); load(); }
$('storage-open').onclick = open;
$('storage-refresh').onclick = load;
$('storage-close').onclick = () => { cancel(); dialog.close(); };
dialog.addEventListener('cancel', cancel);
dialog.addEventListener('close', () => { cancel(); if (cleanup.open) cleanup.close(); });
window.addEventListener('prtfm-storage-open', open);
document.addEventListener('prtfm-storage-changed', () => { if (dialog.open) load(); });
})();
