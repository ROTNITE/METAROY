'use strict';
const byId=id=>document.getElementById(id);
const activeStates=new Set(['queued','running','stopping']);
const labels={queued:'В очереди',running:'Выполняется',stopping:'Прерывание',completed:'Завершена',failed:'Ошибка',cancelled:'Прервана',timed_out:'Тайм-аут',interrupted:'Незавершённая',passed:'Успешный',success:'Успех',time_limit:'Лимит времени',safe_stopped:'Безопасная остановка',unverified:'Не подтверждена'};
let token='',registry={controllers:[],environments:[]},series=[],selected=null,selectedRun=null,activeId=null,offset=0,total=0,requestBusy=false,refreshBusy=false,refreshTimer=null,lastHistory='',lastRender='';
let matrixOffset=0,matrixRequest=0,lastRevision=null,refreshAgain=false,reportRequest=0;
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
  byId('start').disabled=!valid||!byId('confirm').checked||requestBusy||!!activeId;
}
function selectOptions(role){
  const select=byId(role);select.replaceChildren();
  for(const row of registry[role+'s']){const o=document.createElement('option');o.value=row.id;o.textContent=row.name;select.append(o);}
}
function renderHistory(){
  const signature=JSON.stringify([series,offset,total,selected]);
  if(signature===lastHistory)return;
  lastHistory=signature;
  byId('page-label').textContent=total?String(offset+1)+'–'+Math.min(offset+series.length,total)+' из '+total:'0 серий';
  byId('prev-page').disabled=offset===0;
  byId('next-page').disabled=offset+series.length>=total;
  const body=byId('history');body.replaceChildren();
  if(!series.length){const tr=document.createElement('tr');const td=appendCell(tr,'Нет сохранённых серий.','empty');td.colSpan=6;body.append(tr);return;}
  for(const run of series){
    const tr=document.createElement('tr'),s=run.spec||{};
    appendCell(tr,s.controller+' → '+s.environment);
    appendCell(tr,s.mode);appendCell(tr,run.state,run.state==='completed'?'ok':run.state==='failed'?'bad':'');
    appendCell(tr,run.passed_count+'/'+run.trial_count);
    appendCell(tr,new Date(run.created).toLocaleString('ru-RU'));
    const cell=appendCell(tr,'');cell.append(button('Открыть',async()=>{selected=run.id;await refresh();}));body.append(tr);
  }
}
function exportLink(id,run,format){
  const el=byId(id);el.classList.toggle('disabled',!run);el.setAttribute('aria-disabled',String(!run));
  if(run)el.href='/api/series/'+run.id+'/export/'+format;else el.removeAttribute('href');
}
function render(){
  const run=selectedRun;
  const signature=JSON.stringify([run,activeId]);
  if(signature===lastRender){renderHistory();syncForm();return;}
  lastRender=signature;
  const rows=run?run.rows:[],finished=rows.filter(r=>!activeStates.has(r.state)).length;
  byId('series-state').textContent=run?display(run.state):'Ожидание запуска';
  byId('cancel').disabled=!run||!activeStates.has(run.state)||run.state==='stopping';
  byId('repeat').disabled=!run||!!activeId;
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
    const td=appendCell(tr,'');const b=button('Детали',()=>showReport(run.id,row.index));b.disabled=!row.result;td.append(b,button('Журнал',()=>selectTimeline(run.id,row.index)));body.append(tr);
  }
  exportLink('export-json',run,'json');exportLink('export-csv',run,'csv');exportLink('export-zip',run&&!activeStates.has(run.state)?run:null,'zip');renderHistory();syncForm();
}
async function refresh(){
  if(refreshBusy){refreshAgain=true;return;}refreshBusy=true;
  try{
    const page=await api('/api/series?offset='+offset+'&limit=30');
    series=page.series;total=page.total;activeId=page.active_id;
    if(!selected)selected=activeId||(series[0]&&series[0].id)||null;
    const revisionChanged=page.revision!==lastRevision;
    const requested=selected;
    if(!requested)selectedRun=null;
    else if(revisionChanged||!selectedRun||selectedRun.id!==requested){
      const run=await api('/api/series/'+requested);
      if(selected===requested)selectedRun=run;
    }
    lastRevision=page.revision;
    render();
    if(revisionChanged)void refreshMatrix();
    if(timelineTrial&&activeId===timelineTrial.id)void pollTimeline();
  }
  catch(error){byId('form-message').textContent='Лаборатория недоступна: '+error.message;}
  finally{refreshBusy=false;if(refreshAgain){refreshAgain=false;void refresh();}}
}
async function refreshMatrix(){
  const request=++matrixRequest;
  try{
    const params=new URLSearchParams({offset:String(matrixOffset),limit:'30',mode:byId('matrix-mode').value,profile:byId('matrix-profile').value});
    const page=await api('/api/matrix?'+params);
    if(request!==matrixRequest)return;
    const body=byId('matrix');body.replaceChildren();
    byId('matrix-page').textContent=page.total?String(matrixOffset+1)+'–'+Math.min(matrixOffset+page.cells.length,page.total)+' из '+page.total:'0 конфигураций';
    byId('matrix-prev').disabled=matrixOffset===0;
    byId('matrix-next').disabled=matrixOffset+page.cells.length>=page.total;
    byId('matrix-note').textContent='Диагностика, не оценка жюри. Одинаковые настройки не доказывают идентичность версий компонентов.'+(page.excluded_trials?' Неполные старые записи: '+page.excluded_trials+'.':'');
    if(!page.cells.length){const tr=document.createElement('tr');const td=appendCell(tr,'Нет испытаний по фильтру.','empty');td.colSpan=8;body.append(tr);}
    for(const cell of page.cells){
      const tr=document.createElement('tr');
      const pair=appendCell(tr,cell.controller+' → '+cell.environment);
      pair.title=[cell.controller_origin,cell.environment_origin,cell.decide_path,cell.decide_profile].filter(Boolean).join(' · ');
      appendCell(tr,(cell.profile||'—')+' / '+cell.mode+' / '+cell.seed+' / '+(cell.timeout??'—')+' с');
      appendCell(tr,cell.finished+'/'+cell.attempts);
      appendCell(tr,cell.exchange_passed+'/'+cell.finished);
      appendCell(tr,cell.mission_success+'/'+cell.finished);
      appendCell(tr,cell.truth_trials?cell.all_evacuated+'/'+cell.truth_trials:'—');
      appendCell(tr,cell.inputs_preserved+'/'+cell.finished);
      appendCell(tr,'').append(button('Серия',async()=>{selected=cell.latest_series_id;await refresh();}));body.append(tr);
    }
  }catch(error){if(request===matrixRequest)byId('matrix-page').textContent=error.message;}
}
let timelineTrial=null,timelineCursor=0,timelineRows=[],timelineGeneration=0,timelineBusy=false;
function selectTimeline(id,index){
  timelineTrial={id,index};timelineCursor=0;timelineRows=[];timelineGeneration++;timelineBusy=false;
  byId('timeline').replaceChildren();byId('timeline-reset').disabled=false;byId('timeline-next').disabled=false;
  void pollTimeline();
}
async function pollTimeline(){
  if(!timelineTrial||timelineBusy)return;
  const generation=timelineGeneration;const trial=timelineTrial;timelineBusy=true;
  try{
    const page=await api('/api/series/'+trial.id+'/trial/'+trial.index+'/progress?cursor='+timelineCursor+'&limit=50');
    if(generation!==timelineGeneration)return;
    timelineCursor=page.cursor;timelineRows=timelineRows.concat(page.rows).slice(-100);
    const body=byId('timeline');body.replaceChildren();
    for(const row of timelineRows){
      const tr=document.createElement('tr');
      appendCell(tr,row.wall_s??'—');appendCell(tr,row.kind+' · '+row.source);
      appendCell(tr,row.time??row.model_time??'—');appendCell(tr,row.status??row.operation??row.code??'—');
      appendCell(tr,(row.commands??'—')+' / '+(row.command_results??'—'));
      appendCell(tr,row.decision_id??row.request_id??'—');body.append(tr);
    }
    byId('timeline-status').textContent='Испытание '+(trial.index+1)+' · '+(page.available?(timelineRows.length+' последних записей; курсор '+timelineCursor+(page.more?' · есть следующие записи':'')):'Журнал ещё не создан или недоступен для этой архивной версии.');
  }catch(error){if(generation===timelineGeneration)byId('timeline-status').textContent=error.message;}
  finally{if(generation===timelineGeneration)timelineBusy=false;}
}
byId('timeline-reset').addEventListener('click',()=>{if(timelineTrial)selectTimeline(timelineTrial.id,timelineTrial.index);});
byId('timeline-next').addEventListener('click',()=>pollTimeline());
async function showReport(id,index){
  const request=++reportRequest;
  byId('report-title').textContent='Серия '+id.slice(0,8)+' · испытание '+(index+1);
  byId('diagnostic').textContent='Загрузка…';
  try{
    const report=await api('/api/series/'+id+'/trial/'+index);
    if(request!==reportRequest)return;
    const first=report.first_protocol_error||{};
    const truth=report.independent_truth;
    const message=[ 'Обмен: '+display(report.exchange), 'Миссия: '+display(report.status),
      'Входы сохранены: '+(report.inputs_preserved===true?'да':report.inputs_preserved===false?'нет':'не проверено'),
      'Физический результат: '+(truth?display(truth.physical_handovers)+' / '+display(truth.target_count)+' (истина модели)':'нет независимой истины'),
      'Публичные переходы: '+display(report.correlated_public_target_count),
      'Ошибка: '+(first.message||report.error||'не зарегистрирована')];
    if(first.details)message.push('Детали: '+JSON.stringify(first.details));
    byId('diagnostic').textContent=message.join('\n');byId('report').textContent=JSON.stringify(report,null,2);
  }catch(error){if(request===reportRequest){byId('diagnostic').textContent=error.message;byId('report').textContent='—';}}
}
byId('trial-form').addEventListener('submit',async event=>{
  event.preventDefault();requestBusy=true;syncForm();byId('form-message').textContent='';
  try{const run=await api('/api/series',spec());selected=run.id;offset=0;byId('confirm').checked=false;await refresh();}
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
  const run=selectedRun;if(!run)return;const s=run.spec;
  for(const [id,key] of [['controller','controller'],['environment','environment'],['profile','profile'],['mode','mode'],['timeout','timeout'],['decide-path','decide_path'],['decide-profile','decide_profile']])byId(id).value=s[key];
  byId('seeds').value=s.seeds.join(', ');byId('confirm').checked=false;syncForm();byId('controller').focus();
});
byId('prev-page').addEventListener('click',()=>{offset=Math.max(0,offset-30);refresh();});
byId('next-page').addEventListener('click',()=>{offset+=30;refresh();});
byId('matrix-refresh').addEventListener('click',()=>refreshMatrix());
byId('matrix-prev').addEventListener('click',()=>{matrixOffset=Math.max(0,matrixOffset-30);refreshMatrix();});
byId('matrix-next').addEventListener('click',()=>{matrixOffset+=30;refreshMatrix();});
for(const id of ['matrix-mode','matrix-profile'])byId(id).addEventListener('change',()=>{matrixOffset=0;refreshMatrix();});
async function init(){
  try{const session=await api('/api/session');token=session.token;registry=session.registry;
    const assessment=session.assessed_m5;
    if(assessment){
      byId('assessed-score').textContent=assessment.value===null?
        'Q: оценено '+assessment.assessed_pairs+' из '+assessment.required_pairs+' внешних пар.':
        'Расчёт из предоставленных Q: '+assessment.value.toFixed(4)+' ('+assessment.numerator.toFixed(3)+' / '+assessment.denominator+'). Не оценка жюри.';
    }
    selectOptions('controller');selectOptions('environment');await refresh();refreshTimer=setInterval(refresh,1400);}
  catch(error){byId('form-message').textContent=error.message;}
}
window.addEventListener('pagehide',()=>{if(refreshTimer)clearInterval(refreshTimer);});
init();
