(() => {
'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));

const $ = id => document.getElementById(id);
const dialog = document.createElement('dialog');
dialog.className = 'semantic-dialog';
dialog.id = 'semantic-file-dialog';
dialog.innerHTML = "<h2 id=\"semantic-heading\"></h2><p id=\"semantic-file\"></p>\n<p id=\"semantic-description\"></p>\n<details class=\"semantic-help\"><summary aria-label=\"어떤 파일에 유용한가요?\">ⓘ 어떤 파일에 유용한가요?</summary><div class=\"semantic-help-body\">\n<p>정확한 용어를 모르거나, 질문과 다른 표현으로 설명된 내용을 찾을 때 도움이 될 수 있습니다. 예를 들어 “계약을 중간에 끝내면?”이라는 질문에서 “중도 해지” 관련 내용을 찾는 데 활용할 수 있습니다.</p>\n<p>규칙명·오류 코드처럼 정확한 검색어를 알고 있다면 기본 탐색만으로 충분할 수 있습니다. 의미 검색을 준비해도 매 질문마다 사용되는 것은 아니며, 더 정확한 답변을 항상 보장하지는 않습니다.</p>\n<p>별표 없는 파일도 기본 탐색 대상입니다. 의미 검색으로 찾은 내용은 원문을 읽고 확인한 뒤 답변 근거로 사용합니다.</p></div></details>\n<p id=\"semantic-result\" role=\"status\"></p>\n<p>질문 중 의미 검색을 실제로 사용하면, 검색어가 임베딩 제공자에 전송되며 추가 비용이 발생할 수 있습니다.</p><div class=\"semantic-actions\"><button id=\"semantic-action\" type=\"button\"></button><button id=\"semantic-dismiss\" type=\"button\">취소</button></div>";
globalThis.window?.PRTFMI18n?.translateInitialMarkup(dialog);
dialog.setAttribute('aria-labelledby','semantic-heading');
document.body.append(dialog);
let refreshing = false, context = null, busy = false, jobEndpoint = null;
function starIcon(button, state) {
  button.dataset.state = state;
  button.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="m12 3 2.78 5.63 6.22.91-4.5 4.39 1.06 6.2L12 17.2l-5.56 2.93 1.06-6.2L3 9.54l6.22-.91Z"/></svg>';
  button.classList.toggle('pending', state === 'pending');
}

const key = () => JSON.stringify([$('folder').value,$('token').value]);
const ready = file => !!(file.enabled && file.embedded && file.state === 'ready');
async function api(ctx, endpoint, body) {
  const response = await _fetch(endpoint + (body === undefined ? '?folder='+encodeURIComponent(ctx.folder) : ''), {
    method:body === undefined?'GET':'POST', cache:'no-store',
    headers:{Authorization:'Bearer '+ctx.auth,'Content-Type':'application/json'},
    ...(body === undefined?{}:{body:JSON.stringify({folder:ctx.folder,...body})})
  });
  if(!response.ok) {
    const e = new Error(response.status===409?_t("다른 준비 작업이 진행 중입니다. 완료 후 다시 시도하세요."):
      response.status===401?_t("연결이 만료되었습니다. 앱을 다시 실행하세요."):
      response.status===404?_t("먼저 이 폴더 검색 준비를 진행하세요."):_t("요청 실패 ({0})" , response.status));
    e.status=response.status; throw e;
  }
  return response.json();
}
async function refresh() {
  if(refreshing || !$('folder').value || !$('token').value) return;
  refreshing=true;
  const ctx={folder:$('folder').value,auth:$('token').value,key:key()};
  try {
    const result=await api(ctx,'/semantic/status');
    if(ctx.key!==key()) return;
    for(const row of $('folder-list').querySelectorAll('[data-file-name]')) {
      let star=row.querySelector('.semantic-star');
      if(!star) {
        star=document.createElement('button');star.type='button';star.className='semantic-star';
        row.append(star);
      }
      const file=result.files.find(f=>f.path===row.dataset.fileName);
      starIcon(star,file && ready(file)?'ready':result.job?.state==='running' && file?.enabled?'pending':'off');
      star.classList.toggle('ready',!!file && ready(file));
      const pending=file?.enabled && !ready(file);
      _ui(star, 'title', () => (file?.state==='delete_failed'?_t("의미 검색 데이터 삭제 재시도"):file && ready(file)?_t("의미 검색 데이터 삭제"):pending?_t("의미 검색 준비 또는 갱신 필요"):_t("의미 검색 켜기")), false);
      _ua(star, 'aria-label', () => (row.dataset.fileName+' · '+star.title));
      star.onclick=()=>open(ctx,file,row.dataset.fileName);
    }
  } catch(error) {
    if(ctx.key===key()) {
      for(const row of $('folder-list').querySelectorAll('[data-file-name]')) {
        let star=row.querySelector('.semantic-star');
        if(!star) star=document.createElement('button');star.type='button';star.className='semantic-star';starIcon(star,'off');star.classList.remove('ready');
        _ua(star, 'aria-label', () => (row.dataset.fileName+_t(" · 의미 검색 준비")));
        star.onclick=()=>open(ctx,null,row.dataset.fileName);row.append(star);
      }
    }
  } finally {refreshing=false;}
}
function open(ctx,file,name) {
  if(ctx.key!==key() || busy) return;
  context={...ctx,file,name,quote:null,remove:!!file && (ready(file) || file.state==='delete_failed')};
  _ui($('semantic-heading'), 'textContent', () => (context.remove?_t("의미 검색 데이터를 삭제할까요?"):_t("의미 검색 준비")), false);
  _ui($('semantic-file'), 'textContent', () => (name), false);
  _ui($('semantic-description'), 'textContent', () => (context.remove?
    _t("이 폴더의 의미 검색을 해제합니다. 다른 폴더에서 사용하는 임베딩은 유지되며, 마지막 사용 폴더에서 해제하면 실제 벡터를 삭제합니다. 원본·기본 탐색·과거 인용은 유지됩니다. 다시 등록할 때 재사용 가능한 임베딩이 없으면 비용이 발생할 수 있습니다."):
    _t("이 파일을 의미 검색에도 사용할 수 있도록 준비합니다. 기본 문서 탐색은 그대로 유지되며, 답변 근거가 부족할 때 의미 검색으로 보완합니다. 준비 과정에서 파일의 추출 텍스트가 설정된 임베딩 제공자에 전송됩니다. 먼저 예상 비용을 확인한 뒤 진행 여부를 선택하세요.")), false);
  _ui($('semantic-result'), 'textContent', () => (''), false);
  _ui($('semantic-action'), 'textContent', () => (context.remove?_t("삭제"):_t("준비 및 예상 비용 확인")), false);
  $('semantic-action').disabled=false;
  $('semantic-action').hidden=false;
  _ui($('semantic-dismiss'), 'textContent', () => (_t("취소")), false);
  if (!context.remove && /\.(pdf|png|jpe?g|webp|gif|bmp|tiff?)$/i.test(name) && !file) {
    _ui($('semantic-result'), 'textContent', () => (_t("먼저 파일 옆 체크박스로 문서 준비를 완료한 뒤 임베딩을 생성하세요.")), false);
    $('semantic-action').disabled=true;
  }
  dialog.showModal();
}
async function waitJob(ctx) {
  while(true) {
    await new Promise(resolve=>setTimeout(resolve,800));
    const job=await api(ctx,'/index/status');
    if(job.state!=='running') {
      if(job.error==='quote_unavailable') {
        ctx.quote=null;
        _ui($('semantic-action'), 'textContent', () => (_t("준비 및 예상 비용 확인")), false);
        throw new Error(_t("견적이 만료되었거나 이미 실행되었습니다. 비용을 다시 확인하세요."));
      }
      if(job.state!=='done') throw new Error(job.state==='cancelled'?_t("작업을 취소했습니다. 이미 발생한 비용은 반환되지 않습니다."):_t("작업을 완료하지 못했습니다. 상태를 확인 후 다시 시도하세요."));
      return;
    }
  }
}
$('semantic-action').onclick=async()=>{
  if(busy || !context) return;
  const ctx=context;busy=true;$('semantic-action').disabled=true;
  $('semantic-result').classList.add('is-loading');
  _ui($('semantic-result'), 'textContent', () => ('Loading…'), false);
  try {
    if(ctx.remove) {
      await api(ctx,'/semantic/files/'+ctx.file.id+'/delete',{});
      _ui($('semantic-result'), 'textContent', () => (_t("의미 검색 데이터를 삭제했습니다.")), false);
      $('semantic-action').hidden=true;
    } else if(ctx.quote) {
      const budget=String(Math.max(0.000001,ctx.quote.estimated_usd));
      await api(ctx,'/semantic/run',{quote_id:ctx.quote.quote_id,budget_usd:budget});
      ctx.quote=null;
      _ui($('semantic-action'), 'textContent', () => (_t("준비 및 예상 비용 확인")), false);
      jobEndpoint='/semantic/cancel';
      _ui($('semantic-dismiss'), 'textContent', () => (_t("진행 중지")), false);
      await waitJob(ctx); jobEndpoint=null;
      _ui($('semantic-result'), 'textContent', () => (_t("완료했습니다. 이제 질문할 때 필요에 따라 의미 검색을 함께 사용합니다.")), false);
      ctx.quote=null;$('semantic-action').hidden=true;
    } else {
      if(!ctx.file) throw new Error(_t("팝업을 닫고 폴더의 자동 준비가 끝난 뒤 다시 선택하세요."));
      await api(ctx,'/semantic/files/'+ctx.file.id,{enabled:true});
      let quote=await api(ctx,'/semantic/estimate',{doc_ids:[ctx.file.id]});
      if(quote.excluded.some(e=>e.reason==='scan_required')) {
        await api(ctx,'/index',{automatic:true});
        jobEndpoint='/index/cancel';_ui($('semantic-dismiss'), 'textContent', () => (_t("준비 중지")), false);
        await waitJob(ctx);jobEndpoint=null;
        quote=await api(ctx,'/semantic/estimate',{doc_ids:[ctx.file.id]});
      }
      if(!quote.runnable) {
        const reasons=new Set((quote.excluded||[]).map(item=>item.reason));
        if(reasons.has('pdf_not_refined')) throw new Error(_t("PDF 문서 준비가 아직 완료되지 않았습니다. 검색 준비 완료 후 다시 시도하세요."));
        if(reasons.size) throw new Error(_t("이 파일의 검색 준비를 완료하지 못했습니다. 폴더 검색 준비 후 다시 시도하세요."));
        if(quote.total_active_chunks>10000) throw new Error(_t("이 폴더의 의미 검색 저장 용량을 초과했습니다. 사용하지 않는 파일의 별표를 해제한 후 다시 시도하세요."));
        throw new Error(_t("예상 비용을 계산하지 못했습니다. 앱을 다시 실행한 뒤 재시도하세요."));
      }
      ctx.quote=quote;
      _ui($('semantic-result'), 'textContent', () => (_t("예상 비용: ${0} · {1} 토큰. 재사용 가능한 임베딩은 연결하고, 나머지만 생성합니다. 견적은 30분간 유효합니다." , quote.estimated_usd, quote.tokens)), false);
      _ui($('semantic-action'), 'textContent', () => (_t("임베딩 진행")), false);
    }
  } catch(error) { _ui($('semantic-result'), 'textContent', () => (error.message), false); }
  finally {
    $('semantic-result').classList.remove('is-loading');
    jobEndpoint=null;busy=false;$('semantic-action').disabled=false;_ui($('semantic-dismiss'), 'textContent', () => (_t("닫기")), false);
    await refresh();
  }
};
$('semantic-dismiss').onclick=async()=>{
  if(busy) {
    if(jobEndpoint) {
      try {await api(context,jobEndpoint,{});_ui($('semantic-result'), 'textContent', () => (_t("중지 요청을 보냈습니다.")), false);}
      catch(error){_ui($('semantic-result'), 'textContent', () => (error.message), false);}
    }
    return;
  }
  dialog.close();
};
dialog.addEventListener('cancel',event=>{if(busy)event.preventDefault();});
dialog.addEventListener('close',()=>{if(!dialog.open) context=null;});
document.addEventListener('semantic-folder-changed',refresh);
setInterval(refresh,4000);
refresh();
})();
