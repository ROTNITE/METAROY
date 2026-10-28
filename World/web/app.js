'use strict';
// Фронтенд ничего не решает за мир: только показывает серверные JSON-снимки.
const $ = (id) => document.getElementById(id);
const COLOR = { A:'#56bdff', B:'#fb757e', C:'#c5a1ff' };
const NAMES = { A:'Альфа', B:'Бета', C:'Гамма' };
const canvas = $('world-canvas');
const chartCanvas = $('chart');
const tip = $('hover-tip');
let state = null, running = false, working = false, speed = 1;
let selectedId = 'A-01', hoveredId = null, lastPos = new Map(), animations = new Map();
let animStart = performance.now(), animDuration = 300, fatalError = null;

function escapeHtml(str) { return String(str ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
async function api(path, payload) {
  const opts = payload === undefined ? {} : { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload) };
  const r = await fetch(path, opts);
  if (!r.ok) throw new Error(`${r.status}: ${(await r.json()).error || 'Network error'}`);
  return r.json();
}
function setStatus() {
  if (!state) return;
  $('tick-counter').innerHTML = `${state.tick} <small>/ ${state.limit_ticks}</small>`;
  $('total-counter').textContent = state.agents.length;
  $('seed-label').textContent = state.seed;
  $('world-size').textContent = `${state.width}×${state.height}`;
  let finished = state.status !== 'running';
  $('status-pill').classList.toggle('finished', finished);
  $('status-pill').innerHTML = finished ? `● ${state.winner ? 'ПОБЕДИЛ '+state.winner : 'НИЧЬЯ'}` : '<span class="live-dot"></span> СИМУЛЯЦИЯ';
  $('btn-play').disabled = finished;
  $('btn-step').disabled = finished || working;
  $('play-icon').textContent = running ? 'Ⅱ' : '▶';
  $('play-text').textContent = running ? 'Пауза' : 'Запустить';
}
function speciesPanel() {
  let html = '';
  for (const sp of ['A','B','C']) {
    const n=state.counts[sp], team=state.agents.filter(a=>a.species===sp);
    const food=state.stores[sp].food+team.reduce((s,a)=>s+a.food,0);
    const material=state.stores[sp].material+team.reduce((s,a)=>s+a.material,0);
    const percent = Math.max(0, Math.min(100, n / 8 * 100));
    html += `<div class="species-row"><div class="species-header"><span class="species-symbol" style="background:${COLOR[sp]}22;color:${COLOR[sp]}">${sp}</span><div><strong>Вид ${NAMES[sp]}</strong><small>Колония ${sp}</small></div><div class="species-count" style="color:${COLOR[sp]}">${n}</div></div><div class="progress"><div style="width:${percent}%;background:${COLOR[sp]}"></div></div><div class="species-meta"><span>✳ Еда ${food}</span><span>◆ Материал ${material}</span></div></div>`;
  }
  $('species-panel').innerHTML=html;
}
function paintChart() {
  const b=chartCanvas.getBoundingClientRect(), dpr=window.devicePixelRatio || 1;
  if (b.width<1) return;
  chartCanvas.width=Math.round(b.width*dpr);chartCanvas.height=Math.round(b.height*dpr);
  const ctx=chartCanvas.getContext('2d');ctx.scale(dpr,dpr);
  const w=b.width,h=b.height, history=state.history;
  ctx.clearRect(0,0,w,h);
  ctx.strokeStyle='#27354a';ctx.lineWidth=1;
  for (let i=0;i<4;i++){let y=12+i*(h-24)/3;ctx.beginPath();ctx.moveTo(0,y);ctx.lineTo(w,y);ctx.stroke();}
  if (history.length<2) return;
  const max=Math.max(6, ...history.flatMap(item=>[item.A,item.B,item.C]));
  for (const sp of ['A','B','C']) {
    ctx.beginPath(); ctx.lineWidth=2.2;ctx.strokeStyle=COLOR[sp];
    history.forEach((p,i)=>{const x=4+(w-8)*i/(history.length-1),y=h-10-(h-21)*p[sp]/max;i?ctx.lineTo(x,y):ctx.moveTo(x,y);});
    ctx.stroke();
  }
}
function renderInspector() {
  const target=state.agents.find(a=>a.id===selectedId);
  if (!target) { $('inspector').innerHTML='<div class="empty-state">Выбери агента на карте. Наведи курсор, чтобы увидеть текущую мысль.</div>'; return; }
  const hp=Math.max(0,100*target.hp/target.max_hp);
  $('inspector').innerHTML=`<h2 class="avatar-name" style="color:${COLOR[target.species]}">◉ ${escapeHtml(target.id)}</h2><div class="avatar-species">Вид ${NAMES[target.species]} · (${target.x}, ${target.y}) · возраст ${target.age}</div>
  <div class="hp-label"><span>Здоровье</span><b>${target.hp} / ${target.max_hp}</b></div><div class="progress" style="height:7px"><div style="width:${hp}%;background:${hp<30?'#fb757e':'#6bd59c'}"></div></div>
  <div class="stat-pills"><div class="stat-pill"><span>Еда</span><strong>${target.food}</strong></div><div class="stat-pill"><span>Материал</span><strong>${target.material}</strong></div><div class="stat-pill"><span>Последнее действие</span><strong>${escapeHtml(target.last_action)}</strong></div><div class="stat-pill"><span>Расход еды (X)</span><strong>${target.genome.x}</strong></div></div>
  <div class="small-tags"><span>Y · ${target.genome.y}</span><span>Z · ${target.genome.z}</span><span>K · ${target.genome.k}</span></div>
  <div class="thought-box"><b>◈ ТЕКУЩАЯ МЫСЛЬ / ПЛАН</b>${escapeHtml(target.thought)}</div>`;
}
function renderEvents() {
  const feed=$('event-list'); feed.replaceChildren();
  let events=state.events.slice().reverse().slice(0,20);
  $('event-count').textContent=state.events.length+'+';
  for (const ev of events) {
    const item=document.createElement('div');item.className='event-item '+ev.type;
    const number=document.createElement('span');number.className='event-tick';number.textContent='#'+ev.tick;
    const text=document.createElement('span');text.className='event-text';text.textContent=ev.text;
    item.append(number,text);feed.appendChild(item);
  }
}
function cellAtMouse(e) {
  if (!state) return null;
  const rect=canvas.getBoundingClientRect();
  const x=Math.floor((e.clientX-rect.left)/rect.width*state.width);
  const y=Math.floor((e.clientY-rect.top)/rect.height*state.height);
  return (x>=0&&x<state.width&&y>=0&&y<state.height)? {x,y}:null;
}
function showTooltip(e) {
  const cell=cellAtMouse(e);
  const agent=cell && state.agents.find(a=>a.x===cell.x&&a.y===cell.y);
  hoveredId=agent?.id || null;
  if (!agent) {tip.classList.add('hidden');return;}
  tip.innerHTML=`<div class="tip-head" style="color:${COLOR[agent.species]}">● ${escapeHtml(agent.id)} · вид ${agent.species}</div><div>HP: ${agent.hp}/${agent.max_hp} &nbsp; Еда: ${agent.food}</div><div>Действие: <b>${escapeHtml(agent.last_action)}</b></div><div class="tip-thought">«${escapeHtml(agent.thought)}»</div>`;
  tip.classList.remove('hidden');
  const parent=$('map-container').getBoundingClientRect();
  const tipW=235; let px=e.clientX-parent.left+14,py=e.clientY-parent.top+12;
  if(px+tipW>parent.width) px=Math.max(8,px-tipW-25);
  if(py+150>parent.height)py=Math.max(8,py-155);
  tip.style.left=px+'px';tip.style.top=py+'px';
}
canvas.addEventListener('mousemove',showTooltip);
canvas.addEventListener('mouseleave',()=>{hoveredId=null;tip.classList.add('hidden');});
canvas.addEventListener('click',e=>{const cell=cellAtMouse(e);const a=cell&&state.agents.find(a=>a.x===cell.x&&a.y===cell.y);if(a){selectedId=a.id;renderInspector();}});

function drawMap(now) {
  if (!state) { requestAnimationFrame(drawMap);return; }
  const box=canvas.getBoundingClientRect(),dpr=Math.min(2,window.devicePixelRatio||1);
  const newW=Math.round(box.width*dpr),newH=Math.round(box.height*dpr);
  if (newW && (canvas.width!==newW || canvas.height!==newH)) {canvas.width=newW;canvas.height=newH;}
  const ctx=canvas.getContext('2d'); ctx.setTransform(dpr,0,0,dpr,0,0);
  const w=box.width,h=box.height, cw=w/state.width,ch=h/state.height;
  ctx.clearRect(0,0,w,h);ctx.fillStyle='#132226';ctx.fillRect(0,0,w,h);
  const blocks=new Map();
  for (let y=0;y<state.height;y++) {
    for(let x=0;x<state.width;x++) {
      const c=state.cells[y][x],px=x*cw,py=y*ch;
      ctx.fillStyle=(x+y)%2?'#182a2a':'#172728';ctx.fillRect(px,py,cw,ch);
      ctx.strokeStyle='#ffffff0b';ctx.lineWidth=.7;ctx.strokeRect(px+.35,py+.35,cw-.7,ch-.7);
      if(c.block_hp>0){
        ctx.fillStyle='#617488';ctx.fillRect(px+1,py+1,cw-2,ch-2);
        ctx.fillStyle='#8491a3';ctx.fillRect(px+2,py+2,cw-4,Math.max(1,ch*.18));
        ctx.strokeStyle='#aeb9c3a0';ctx.strokeRect(px+1,py+1,cw-2,ch-2);
      } else {
        if(c.food>0){ctx.fillStyle='#65d990';ctx.beginPath();ctx.arc(px+cw*.35,py+ch*.38,Math.min(cw,ch)*.18,0,Math.PI*2);ctx.fill();}
        if(c.material>0){ctx.fillStyle='#edb95f';ctx.save();ctx.translate(px+cw*.71,py+ch*.68);ctx.rotate(Math.PI/4);let r=Math.min(cw,ch)*.17;ctx.fillRect(-r,-r,r*2,r*2);ctx.restore();}
      }
    }
  }
  for(const [sp,xy] of Object.entries(state.bases)){
    const x=xy[0],y=xy[1];
    ctx.fillStyle=COLOR[sp]+'18';ctx.fillRect((x-1)*cw,(y-1)*ch,3*cw,3*ch);
    ctx.strokeStyle=COLOR[sp]+'8c';ctx.lineWidth=2;ctx.strokeRect((x-1)*cw+1,(y-1)*ch+1,3*cw-2,3*ch-2);
    ctx.fillStyle=COLOR[sp]+'a8';ctx.font=`bold ${Math.min(11,cw*.55)}px sans-serif`;ctx.textAlign='center';ctx.fillText('⌂',x*cw+cw/2,y*ch+ch*.75);
  }
  const ratio=Math.min(1, (now-animStart)/animDuration);
  for(const agent of state.agents){
    let ix=agent.x,iy=agent.y,from=animations.get(agent.id);
    if(from&&ratio<1){const ease=1-Math.pow(1-ratio,3);ix=from.x+(agent.x-from.x)*ease;iy=from.y+(agent.y-from.y)*ease;}
    const px=(ix+.5)*cw,py=(iy+.5)*ch,r=Math.min(cw,ch)*.38;
    ctx.shadowColor=COLOR[agent.species];ctx.shadowBlur=agent.id===selectedId?18:9;
    ctx.fillStyle=COLOR[agent.species];ctx.beginPath();ctx.arc(px,py,r,0,Math.PI*2);ctx.fill();ctx.shadowBlur=0;
    ctx.strokeStyle='#0c1523';ctx.lineWidth=1.5;ctx.stroke();
    ctx.fillStyle='#0a1c27';ctx.font=`bold ${Math.max(8,Math.min(14,cw*.49))}px sans-serif`;ctx.textAlign='center';ctx.textBaseline='middle';ctx.fillText(agent.species,px,py+.5);
    if(agent.id===selectedId || agent.id===hoveredId){ctx.strokeStyle='#fff';ctx.lineWidth=1.7;ctx.beginPath();ctx.arc(px,py,r+3,0,2*Math.PI);ctx.stroke();}
    ctx.fillStyle='#18242f';ctx.fillRect(px-r,py+r+2,r*2,2.5);
    ctx.fillStyle='#7cea95';ctx.fillRect(px-r,py+r+2,r*2*Math.max(0,agent.hp/agent.max_hp),2.5);
  }
  requestAnimationFrame(drawMap);
}
function applySnapshot(fresh) {
  if(state) {
    lastPos=new Map(state.agents.map(a=>[a.id,{x:a.x,y:a.y}]));
    animations=new Map(fresh.agents.filter(a=>lastPos.has(a.id)).map(a=>[a.id,lastPos.get(a.id)]));
  }
  animStart=performance.now();
  animDuration=speed===1?550:speed===5?170:speed===20?45:0;
  state=fresh;
  if(!state.agents.some(a=>a.id===selectedId)) selectedId=state.agents[0]?.id || '';
  setStatus();speciesPanel();paintChart();renderInspector();renderEvents();
}
async function stepOnce() {
  if(working || !state || state.status!=='running') return;
  working=true;
  try{ applySnapshot(await api('/api/step', {})); }
  catch(e){fatalError=e.message;running=false;alert('Ошибка симуляции: '+fatalError);}
  finally{working=false;setStatus();}
}
async function loop() {
  while(running && state && state.status==='running') {
    const begin=performance.now();await stepOnce();
    const desired=speed===1?850:speed===5?170:speed===20?48:0;
    await sleep(Math.max(0,desired-(performance.now()-begin)));
  }
  running=false;setStatus();
}
$('btn-play').addEventListener('click',()=>{if(!state || state.status!=='running')return;running=!running;setStatus();if(running)loop();});
$('btn-step').addEventListener('click',()=>{running=false;setStatus();stepOnce();});
$('btn-reset').addEventListener('click',async()=>{running=false;setStatus();while(working)await sleep(30);applySnapshot(await api('/api/reset',{}));});
document.querySelectorAll('[data-speed]').forEach(btn=>btn.addEventListener('click',()=>{
  speed=btn.dataset.speed==='max'?'max':Number(btn.dataset.speed);
  document.querySelectorAll('[data-speed]').forEach(b=>b.classList.toggle('active',b===btn));
}));
window.addEventListener('resize',()=>{if(state)paintChart();});
requestAnimationFrame(drawMap);
api('/api/state').then(applySnapshot).catch(e=>{document.body.insertAdjacentHTML('afterbegin',`<div style="background:#953c45;padding:20px;color:white">Ошибка подключения: ${escapeHtml(e.message)}</div>`);});
