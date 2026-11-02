(() => {
  'use strict';
const _fetch = globalThis.window?.PRTFMI18n?.request || globalThis.fetch;
const _ui = globalThis.window?.PRTFMI18n?.bind || ((node, key, render, append = false) => node[key] = (append ? node[key] : '') + render());
const _ua = globalThis.window?.PRTFMI18n?.attribute || ((node, key, render) => node.setAttribute(key, render()));

const _t = globalThis.window?.PRTFMI18n?.t || ((key, ...values) => key.replace(/\{(\d+)\}/g, (match, i) => i < values.length ? String(values[i]) : match));

  const get = id => document.getElementById(id);
  const dialog = get('preferences-dialog');
  const desktop = window.__prtfmDesktop === true;
  const storageKey = 'prtfm-agent-name';
  let name = desktop ? window.__prtfmAgentName : '';
  if (!desktop) { try { name = localStorage.getItem(storageKey) || ''; } catch {} }
  const displayName = () => name || 'agent';
  function apply() {
    get('agent-name').value = displayName();
    _ui(get('preference-name-value'), 'textContent', () => (displayName()), false);
    document.querySelectorAll('.connected-folder').forEach(button => {
      _ua(button, 'aria-label', () => (_t("{0} · 질문할 폴더 선택" , button.dataset.folderPath || '')));
      _ui(button, 'title', () => (button.dataset.folderPath || ''), false);
    });
    const folder = get('folder').value;
    _ui(get('agent-scope'), 'textContent', () => (folder ? _t("{0}가 살펴볼 폴더: {1}" , displayName(), folder.split(/[\\/]/).filter(Boolean).pop()) : _t("{0}와 연결할 폴더를 선택하세요." , displayName())), false);
    _ui(get('agent-scope'), 'title', () => (folder), false);
  }
  function page(value = '') {
    get('key-delete-confirmation').hidden = true;
    get('key-management').hidden = false;
    get('preferences-list').hidden = Boolean(value);
    get('preferences-back').hidden = !value;
    _ui(get('preferences-message'), 'textContent', () => (''), false);
    const titles = {get language() { return _t("언어"); },get name() { return _t("에이전트 이름"); },get keys() { return _t("API 키"); },get backup() { return _t("백업"); },get privacy() { return _t("개인정보·이용 안내"); }};
    _ui(get('preferences-title'), 'textContent', () => (titles[value] || _t("설정")), false);
    document.querySelectorAll('[data-preference-panel]').forEach(panel => { panel.hidden = panel.dataset.preferencePanel !== value; });
  }
  function openSettings() { apply(); page(); if (!dialog.open) dialog.showModal(); get('preferences-title').focus(); }
  get('preferences-open').onclick = openSettings;
  get('preferences-back').onclick = () => page();
  get('storage-back').onclick = () => { get('storage-close').click(); openSettings(); };
  get('preferences-close').onclick = () => dialog.close();
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  document.querySelectorAll('[data-preference]').forEach(button => { button.onclick = () => {
    const value = button.dataset.preference;
    if (value === 'data') {
      dialog.close(); get('storage-back').hidden = false; get('storage-open').click();
    } else { page(value); if (value === 'keys' && !desktop) { refreshDevelopmentKey(); } if (value === 'keys' && desktop) { get('preferences-keys').disabled = true; get('preferences-key-delete').disabled = true; native('key-status'); } }
  }; });
  function native(action) { window.location.href = `/__desktop/${action}`; }
  const languageEnglish = get('ui-language-en');
  const languageKorean = get('ui-language-ko');
  const selectLanguage = value => { languageEnglish.checked = value === 'en'; languageKorean.checked = value === 'ko'; };
  if (languageEnglish && languageKorean) {
    selectLanguage(window.PRTFMI18n?.language || 'ko');
    _ui(get('preference-language-value'), 'textContent', () => (window.PRTFMI18n?.language !== 'en' ? '한국어' : 'English'), false);
    const languageResult = (ok, value) => {
      if (ok) {
        window.PRTFMI18n.setLanguage(value);
        selectLanguage(value);
        get('preference-language-value').textContent = value === 'ko' ? '한국어' : 'English';
      }
      _ui(get('preferences-message'), 'textContent', () => ok ? _t('언어를 적용했습니다.') : _t('언어를 저장하지 못했습니다. 다시 시도하세요.'));
    };
    window.addEventListener('language-saved', event => languageResult(event.detail?.ok === true, event.detail?.language));
    get('language-form').onsubmit = event => {
      event.preventDefault();
      const value = languageEnglish.checked ? 'en' : languageKorean.checked ? 'ko' : '';
      if (!['en', 'ko'].includes(value)) return;
      if (desktop) native(`language?value=${value}`);
      else { try { localStorage.setItem('prtfm-language', value); languageResult(true, value); } catch { languageResult(false); } }
    };
  }
  let keyBusy = false;
  function keyAction(action) {
    if (keyBusy) return;
    keyBusy = true;
    get('preferences-keys').disabled = true;
    get('preferences-key-delete').disabled = true;
    native(action);
  }
  get('preferences-keys').onclick = () => keyAction('key-save');
  get('preferences-key-delete').onclick = () => { get('key-delete-confirmation').hidden = false; get('key-management').hidden = true; get('key-delete-cancel').focus(); };
  get('key-delete-cancel').onclick = () => { get('key-delete-confirmation').hidden = true; get('key-management').hidden = false; get('preferences-key-delete').focus(); };
  get('key-delete-confirm').onclick = () => { get('key-delete-confirmation').hidden = true; get('key-management').hidden = false; keyAction('key-delete'); };
  window.addEventListener('key-state', event => {
    keyBusy = false;
    get('preferences-keys').disabled = !desktop;
    get('preferences-key-delete').disabled = !desktop;
    const state = event.detail || {};
    if (typeof state.configured === 'boolean') {
      keyBadge(state.configured);
      _ui(get('key-registration'), 'textContent', () => (state.configured ? _t("등록 완료") : _t("등록된 API 키가 없습니다.")), false);
      _ui(get('preferences-keys'), 'textContent', () => (state.configured ? _t("교체") : _t("＋ 등록")), false);
      get('preferences-key-delete').hidden = !state.configured;
    }
    _ui(get('preferences-message'), 'textContent', () => (state.message || ''), false);
  });
  get('preferences-backup').onclick = () => { dialog.close(); native('storage-backup'); };
  function keyBadge(configured) {
    const label = configured === true ? _t("등록됨") : configured === false ? _t("미등록") : _t("확인 불가");
    _ui(get('key-state-badge'), 'textContent', () => ((configured === true ? _t("등록됨") : configured === false ? _t("미등록") : _t("확인 불가"))), false);
    _ui(get('preference-keys-value'), 'textContent', () => ((configured === true ? _t("등록됨") : configured === false ? _t("미등록") : _t("확인 불가"))), false);
  }
  async function refreshDevelopmentKey() {
    _ui(get('key-registration'), 'textContent', () => (_t("개발 서버의 키 설정을 확인하는 중…")), false);
    try {
      const response = await _fetch('/credentials/status', {headers:{Authorization:`Bearer ${get('token').value}`},cache:'no-store'});
      if (!response.ok) throw new Error('status unavailable');
      const state = await response.json();
      keyBadge(state.configured);
      _ui(get('key-registration'), 'textContent', () => (state.configured === true
        ? _t("개발 서버에 API 키가 설정되어 있습니다. 키 유효성·잔액은 확인하지 않았습니다.")
        : _t("개발 서버에 API 키가 없습니다. .env에 OPENAI_API_KEY를 설정한 뒤 개발 서버를 재시작하세요.")), false);
    } catch {
      keyBadge(null);
      _ui(get('key-registration'), 'textContent', () => (_t("서버 연결을 확인해 주세요. 키 등록 상태를 확인하지 못했습니다.")), false);
    }
  }
  if (!desktop) {
    _ui(get('key-registration'), 'textContent', () => (_t("개발 서버의 API 키를 사용합니다.")), false);
    get('preferences-keys').hidden = true;
    get('preferences-keys').disabled = true;
    get('preferences-key-delete').hidden = true;
    _ui(get('key-input-hint'), 'textContent', () => (_t("개발 웹은 저장소 루트 .env의 OPENAI_API_KEY를 서버 시작 시 불러옵니다. 변경 후 서버를 재시작하세요. Windows 설치형 앱의 키와는 별도이며, 키를 이 화면에 표시하거나 입력받지 않습니다.")), false);
    get('preferences-backup').disabled = true;
    const backupHint = document.createElement('span');
    backupHint.className = 'backup-platform-hint';
    _ui(backupHint, 'textContent', () => _t(" Windows 설치형 앱에서 관리합니다."));
    get('preferences-backup').after(backupHint);
  }
  get('storage-open').addEventListener('click', () => dialog.close());
  function saved(value) { name = value; apply(); _ui(get('preferences-message'), 'textContent', () => (_t("이름을 저장했습니다.")), false); }
  window.addEventListener('agent-name-saved', event => {
    if (event.detail?.ok) saved(event.detail.name);
    else _ui(get('preferences-message'), 'textContent', () => (_t("이름을 저장하지 못했습니다. 다시 시도하세요.")), false);
  });
  get('agent-name-form').onsubmit = event => {
    event.preventDefault();
    const value = get('agent-name').value.trim();
    if (!value || [...value].length > 40 || /[\x00-\x1f\x7f]/.test(value)) { _ui(get('preferences-message'), 'textContent', () => (_t("이름을 1~40자로 입력하세요.")), false); return; }
    if (desktop) native(`agent-name?name=${encodeURIComponent(value)}`);
    else { try { localStorage.setItem(storageKey, value); saved(value); } catch { _ui(get('preferences-message'), 'textContent', () => (_t("브라우저 저장 공간을 사용할 수 없습니다.")), false); } }
  };
  new MutationObserver(apply).observe(get('registered-folders'), {childList:true});
  document.addEventListener('folder-selected', apply);
  page(); apply();
  if (!name) {
    page('name'); dialog.showModal(); get('preferences-title').focus();
    // Persist the default on first presentation, including when the user dismisses it.
    if (desktop) native('agent-name?name=agent');
    else { try { localStorage.setItem(storageKey, 'agent'); name = 'agent'; } catch {} }
  }
})();
