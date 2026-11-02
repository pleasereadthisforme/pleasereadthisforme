'use strict';
const byId=id=>document.getElementById(id);
const configured=window.__keyConfigured === true;
byId('status').textContent=configured?'API 키가 등록되어 있습니다.':'등록된 API 키가 없습니다.';
byId('save').textContent=configured?'키 교체':'API 키 등록';
byId('save').hidden=false;
byId('delete').hidden=!configured;
function send(action){
  document.querySelectorAll('button').forEach(button=>{button.disabled=true;});
  location.href=`https://prtfm-key-action.invalid/${action}`;
}
byId('save').onclick=()=>send('save');
byId('close').onclick=()=>send('cancel');
byId('delete').onclick=()=>{
  byId('actions').hidden=true;byId('notice').hidden=true;
  byId('confirmation').hidden=false;byId('cancel-delete').focus();
};
byId('cancel-delete').onclick=()=>{
  byId('confirmation').hidden=true;byId('notice').hidden=false;
  byId('actions').hidden=false;byId('delete').focus();
};
byId('confirm-delete').onclick=()=>{if(configured)send('delete');};
document.addEventListener('keydown',event=>{if(event.key==='Escape')send('cancel');});
