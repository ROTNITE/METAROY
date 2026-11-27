'use strict';
const byId=id=>document.getElementById(id);
const activeStates=new Set(['queued','running','stopping']);
const labels={queued:'В очереди',running:'Выполняется',stopping:'Прерывание',completed:'Завершена',failed:'Ошибка',cancelled:'Прервана',timed_out:'Тайм-аут',interrupted:'Незавершённая',passed:'Успешный',success:'Успех',time_limit:'Лимит времени',safe_stopped:'Безопасная остановка',unverified:'Не подтверждена'};
let token='',registry={controllers:[],environments:[]},series=[],selected=null,requestBusy=false,refreshBusy=false,refreshTimer=null;
const display=value=>value===undefined||value===null?'—':(labels[value]||String(value));
const appendCell=(tr,value,cls)=>{const td=document.createElement('td');td.textContent=display(value);if(cls)td.className=cls;tr.append(td);return td;};
const button=(text,handler)=>{const b=document.createElement('button');b.type='button';b.textContent=text;b.addEventListener('click',handler);return b;};
async function api(path,body){
  const options=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-M5-Token':token},body:JSON.stringify(body)};
  const res=await fetch(path,{...options,cache:'no-store'});
  const data=await res.json();
  if(!res.ok)throw new Error(data.error||('HTTP '+res.status));
  return data;
}
function item(role){return registry[role+'s'].find(x=>x.id===byId(role).value);}
function seeds(){
  const raw=byId('seeds').value.trim().split(/[\s,;]+/);
  if(!raw.length||raw.length>10||raw.some(x=>!/^\d+$/.test(x)))throw new Error('Введите 1–10 целых seed через запятую.');
  const result=raw.map(Number);
  if(new Set(result).size!==result.length||result.some(x=>!Number.isSafeInteger(x)||x>2147483647))throw new Error('Seed должны быть уникальными и от 0 до 2147483647.');
  return result;
}
function spec(){
  return {controller:byId('controller').value,environment:byId('environment').value,profile:byId('profile').value,
    mode:byId('mode').value,seeds:seeds(),timeout:Number(byId('timeout').value),
    decide_path:byId('decide-path').value,decide_profile:byId('decide-profile').value,confirm_reset:byId('confirm').checked};
}
function syncForm(){
  const c=item('controller'),e=item('environment');
  for(const [role,component] of [['controller',c],['environment',e]]){
    byId(role+'-detail').textContent=component?(component.origin||'Запуск локального компонента при испытании'):'Нет компонента';
  }
  byId('profile').disabled=!e||e.id!=='reference';
  const independent=c&&c.id==='independent';
  byId('mode').querySelectorAll('option').forEach(o=>{o.disabled=independent&&o.value!=='strict';});
  if(independent)byId('mode').value='strict';
  byId('decide-path').disabled=!(c&&c.kind==='external');
  byId('decide-profile').disabled=!(c&&c.kind==='external');
  let valid=!!c&&!!e;
  let message='';
  try{
    const n=seeds().length*(byId('mode').value==='both'?2:1);
    if(e&&e.kind==='external'&&c&&c.id!=='metarescue'){valid=false;message='Для внешней среды выберите MetaRescue.';}
    else message=n+' испытаний · search_and_rescue · AI OFF · последовательный запуск';
  }catch(error){valid=false;message=error.message;}
  byId('selection-summary').textContent=message;
  byId('start').disabled=!valid||!byId('confirm').checked||requestBusy||series.some(r=>activeStates.has(r.state));
}
function selectOptions(role){
  const select=byId(role);select.replaceChildren();
  for(const row of registry[role+'s']){const o=document.createElement('option');o.value=row.id;o.textContent=row.name;select.append(o);}
}
function renderHistory(){
  const body=byId('history');body.replaceChildren();
  if(!series.length){const tr=document.createElement('tr');const td=appendCell(tr,'Нет сохранённых серий.','empty');td.colSpan=6;body.append(tr);return;}
  for(const run of series){
    const tr=document.createElement('tr'),s=run.spec||{},rows=run.rows||[];
    appendCell(tr,s.controller+' → '+s.environment);
    appendCell(tr,s.mode);appendCell(tr,run.state,run.state==='completed'?'ok':run.state==='failed'?'bad':'');
    appendCell(tr,rows.filter(r=>r.result&&r.result.exchange==='passed').length+'/'+rows.length);
    appendCell(tr,new Date(run.created).toLocaleString('ru-RU'));
    const cell=appendCell(tr,'');cell.append(button('Открыть',()=>{selected=run.id;render();}));body.append(tr);
  }
}
function exportLink(id,run,format){
  const el=byId(id);el.classList.toggle('disabled',!run);el.setAttribute('aria-disabled',String(!run));
  if(run)el.href='/api/series/'+run.id+'/export/'+format;else el.removeAttribute('href');
}
function render(){
  const run=series.find(r=>r.id===selected)||series.find(r=>activeStates.has(r.state))||series[0];
  selected=run?run.id:null;
  const rows=run?run.rows:[],finished=rows.filter(r=>!activeStates.has(r.state)).length;
  byId('series-state').textContent=run?display(run.state):'Ожидание запуска';
  byId('cancel').disabled=!run||!activeStates.has(run.state)||run.state==='stopping';
  byId('repeat').disabled=!run||series.some(r=>activeStates.has(r.state));
  byId('progress').max=Math.max(rows.length,1);byId('progress').value=finished;
  byId('progress-text').textContent=finished+' / '+rows.length+' испытаний';
  byId('series-time').textContent=run?new Date(run.created).toLocaleTimeString('ru-RU'):'—';
  const completed=rows.filter(r=>r.result);
  byId('exchange-stat').textContent=run?completed.filter(r=>r.result.exchange==='passed').length+'/'+rows.length:'—';
  const truths=completed.map(r=>r.result.independent_truth).filter(t=>t&&Number.isInteger(t.physical_handovers));
  byId('truth-stat').textContent=truths.length?truths.reduce((sum,t)=>sum+t.physical_handovers,0):'—';
  const publics=completed.map(r=>r.result.correlated_public_target_count).filter(Number.isInteger);
  byId('public-stat').textContent=publics.length?publics.reduce((a,b)=>a+b,0):'—';
  const body=byId('results');body.replaceChildren();
  if(!rows.length){const tr=document.createElement('tr');const td=appendCell(tr,'Серия ещё не выбрана.','empty');td.colSpan=5;body.append(tr);}
  for(const row of rows){
    const tr=document.createElement('tr'),r=row.result||{},truth=r.independent_truth;
    appendCell(tr,row.seed+' / '+row.mode);
    appendCell(tr,r.exchange||row.state,r.exchange==='passed'?'ok':r.exchange==='failed'?'bad':'');
    appendCell(tr,r.status);
    appendCell(tr,truth&&Number.isInteger(truth.physical_handovers)?truth.physical_handovers+'/'+truth.target_count:'—');
    const td=appendCell(tr,'');const b=button('Детали',()=>showReport(run.id,row.index));b.disabled=!row.result;td.append(b);body.append(tr);
  }
  exportLink('export-json',run,'json');exportLink('export-csv',run,'csv');renderHistory();syncForm();
}
async function refresh(){
  if(refreshBusy)return;refreshBusy=true;
  try{series=(await api('/api/series')).series;render();}
  catch(error){byId('form-message').textContent='Лаборатория недоступна: '+error.message;}
  finally{refreshBusy=false;}
}
async function showReport(id,index){
  byId('report-title').textContent='Серия '+id.slice(0,8)+' · испытание '+(index+1);
  byId('diagnostic').textContent='Загрузка…';
  try{
    const report=await api('/api/series/'+id+'/trial/'+index);
    const first=report.first_protocol_error||{};
    const truth=report.independent_truth;
    const message=[ 'Обмен: '+display(report.exchange), 'Миссия: '+display(report.status),
      'Входы сохранены: '+(report.inputs_preserved===true?'да':report.inputs_preserved===false?'нет':'не проверено'),
      'Физический результат: '+(truth?display(truth.physical_handovers)+' / '+display(truth.target_count)+' (истина модели)':'нет независимой истины'),
      'Публичные переходы: '+display(report.correlated_public_target_count),
      'Ошибка: '+(first.message||report.error||'не зарегистрирована')];
    if(first.details)message.push('Детали: '+JSON.stringify(first.details));
    byId('diagnostic').textContent=message.join('\n');byId('report').textContent=JSON.stringify(report,null,2);
  }catch(error){byId('diagnostic').textContent=error.message;byId('report').textContent='—';}
}
byId('trial-form').addEventListener('submit',async event=>{
  event.preventDefault();requestBusy=true;syncForm();byId('form-message').textContent='';
  try{const run=await api('/api/series',spec());selected=run.id;byId('confirm').checked=false;await refresh();}
  catch(error){byId('form-message').textContent=error.message;}
  finally{requestBusy=false;syncForm();}
});
for(const id of ['controller','environment','profile','mode','seeds','timeout','confirm'])byId(id).addEventListener('input',()=>{
  if(id==='controller'||id==='environment')byId(id+'-probe').hidden=true;
  syncForm();
});
byId('single').addEventListener('click',()=>{byId('seeds').value='89725161';syncForm();});
byId('three').addEventListener('click',()=>{byId('seeds').value='89725161, 89725187, 89725213';syncForm();});
document.querySelectorAll('.probe').forEach(b=>b.addEventListener('click',async()=>{
  const role=b.dataset.role,area=byId(role+'-probe'),component=item(role);
  if(!component)return;b.disabled=true;area.hidden=false;area.textContent='Проверяем…';
  try{const value=await api('/api/probe',{role,id:component.id});area.textContent=JSON.stringify(value,null,2);}
  catch(error){area.textContent=error.message;}
  finally{b.disabled=false;}
}));
byId('cancel').addEventListener('click',async()=>{
  if(!selected)return;
  if(!window.confirm('Прервать локальный процесс? Состояние внешней миссии может остаться неизвестным.'))return;
  try{await api('/api/series/'+selected+'/cancel',{});await refresh();}
  catch(error){byId('form-message').textContent=error.message;}
});
byId('repeat').addEventListener('click',()=>{
  const run=series.find(r=>r.id===selected);if(!run)return;const s=run.spec;
  for(const [id,key] of [['controller','controller'],['environment','environment'],['profile','profile'],['mode','mode'],['timeout','timeout'],['decide-path','decide_path'],['decide-profile','decide_profile']])byId(id).value=s[key];
  byId('seeds').value=s.seeds.join(', ');byId('confirm').checked=false;syncForm();byId('controller').focus();
});
async function init(){
  try{const session=await api('/api/session');token=session.token;registry=session.registry;
    selectOptions('controller');selectOptions('environment');await refresh();refreshTimer=setInterval(refresh,1400);}
  catch(error){byId('form-message').textContent=error.message;}
}
window.addEventListener('pagehide',()=>{if(refreshTimer)clearInterval(refreshTimer);});
init();
