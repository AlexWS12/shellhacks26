(function(){
const TODAY=new Date('2026-09-26');
const ALL=DATA.projects, CHECKS=DATA.checks, TESTS=DATA.tests;
const byRef={};ALL.forEach(p=>{if(p.ref_id)byRef[p.ref_id]=p});
const REFPAIRS=new Set(TESTS.map(t=>byRef[t.a].id+'|'+byRef[t.b].id));
const NAME={DESC:'Dominion Energy SC',GPC:'Georgia'};
const $=s=>document.querySelector(s);
const esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt$=n=>n==null?'redacted':'$'+(n>=1e6?(n/1e6).toFixed(1)+'M':Math.round(n/1e3)+'K');
const d0=s=>new Date(s+'T00:00:00');
const fmtD=s=>d0(s).toLocaleDateString('en-US',{month:'short',day:'numeric',year:'numeric'});
const title=s=>s===s.toUpperCase()?s.toLowerCase().replace(/\b([a-z])/g,m=>m.toUpperCase()).replace(/(\d)kv\b/gi,'$1kV').replace(/\bkv\b/gi,'kV').replace(/\b(Gtc|Sav|Meag|Du|Cc|Usa|Gpc)\b/g,m=>m.toUpperCase()):s;
ALL.forEach(p=>p.label=title(p.name));
const CONF={verified:'Verified location',partial:'Partly verified',town:'Town-level',unlocated:'No location'};

/* ---------- map ---------- */
const W=1000,H=700;
const svg=d3.select('#map').attr('viewBox',`0 0 ${W} ${H}`).attr('preserveAspectRatio','xMidYMid meet');
const g=svg.append('g');
const proj=d3.geoAlbersUsa().scale(1350).translate([W/2,H/2]);
const path=d3.geoPath(proj);
const states=topojson.feature(STATES,STATES.objects.states).features;
const focus=states.filter(s=>s.properties.name==='Georgia'||s.properties.name==='South Carolina');
const counties=topojson.feature(COUNTIES,COUNTIES.objects.counties).features;
g.append('g').selectAll('path').data(states).join('path').attr('d',path).attr('fill',d=>focus.includes(d)?'var(--focus)':'var(--land)');
g.append('g').selectAll('path').data(counties).join('path').attr('d',path).attr('fill','none').attr('stroke','var(--county)').attr('stroke-width',0.4).attr('vector-effect','non-scaling-stroke');
g.append('path').datum(topojson.mesh(STATES,STATES.objects.states,(a,b)=>a!==b)).attr('d',path).attr('fill','none').attr('stroke','var(--muted)').attr('stroke-width',0.8).attr('vector-effect','non-scaling-stroke');
const stLbl=g.append('g').selectAll('text').data(focus).join('text').attr('x',d=>path.centroid(d)[0]).attr('y',d=>path.centroid(d)[1]).attr('text-anchor','middle').attr('fill','var(--muted)').style('font-family','Barlow Condensed,sans-serif').text(d=>d.properties.name);
const CITIES=[['Atlanta',-84.39,33.75],['Augusta',-81.97,33.47],['Savannah',-81.09,32.08],['Columbia',-81.03,34.0],['Charleston',-79.93,32.78],['Macon',-83.63,32.84]];
const cityG=g.append('g');
CITIES.forEach(c=>{const xy=proj([c[1],c[2]]);cityG.append('circle').attr('cx',xy[0]).attr('cy',xy[1]).attr('class','cdot').attr('fill','var(--muted)');cityG.append('text').attr('x',xy[0]).attr('y',xy[1]).attr('class','clbl').attr('fill','var(--muted)').text(c[0])});
const zG=g.append('g'),lnG=g.append('g'),pinG=g.append('g'),lblG=g.append('g');
let k=1;
function sizes(){
  pinG.selectAll('circle').attr('r',4.2/k).attr('stroke-width',d=>(d.conf==='verified'?1:1.8)/k);
  lblG.selectAll('text').style('font-size',(11/k)+'px').attr('dy',-7/k).attr('stroke-width',3/k).style('display',k>9?null:'none');
  stLbl.style('font-size',Math.max(2.5,16/k)+'px');
  cityG.style('display',k>4?null:'none');cityG.selectAll('.cdot').attr('r',1.8/k);cityG.selectAll('.clbl').style('font-size',(10/k)+'px').attr('dx',4/k).attr('dy',12/k);
  zG.selectAll('text').style('font-size',(12/k)+'px').attr('stroke-width',3/k).style('display',k>12?null:'none');
}
const zoom=d3.zoom().scaleExtent([1,120]).on('zoom',e=>{k=e.transform.k;g.attr('transform',e.transform);sizes()});
svg.call(zoom);
function fitGeo(obj,pad,dur){const b=path.bounds(obj);const dx=b[1][0]-b[0][0],dy=b[1][1]-b[0][1];const s=Math.min(120,pad/Math.max(dx/W,dy/H));svg.transition().duration(dur||1200).ease(d3.easeCubicInOut).call(zoom.transform,d3.zoomIdentity.translate(W/2,H/2).scale(s).translate(-(b[0][0]+b[1][0])/2,-(b[0][1]+b[1][1])/2))}
const SE={type:'FeatureCollection',features:focus};
$('#zUS').onclick=()=>svg.transition().duration(1000).call(zoom.transform,d3.zoomIdentity);
$('#zSE').onclick=()=>fitGeo(SE,0.92);
sizes();

/* ---------- rules ---------- */
function miles(a,b){return d3.geoDistance([a.lon,a.lat],[b.lon,b.lat])*3958.8}
function gapDays(a,b){return Math.round(Math.abs(d0(a.isd)-d0(b.isd))/864e5)}
const F={all:false,town:true,past:false};
function visible(p){if(p.lat==null)return false;if(p.u==='GPC'&&!F.all&&!['GPC','SAV'].includes(p.sponsor))return false;
  if(!F.town&&p.conf!=='verified')return false;if(F.past&&d0(p.isd)<TODAY)return false;return true}
function overlaps(){const A=ALL.filter(p=>p.u==='DESC'&&visible(p)),B=ALL.filter(p=>p.u==='GPC'&&visible(p));const out=[];
  A.forEach(a=>B.forEach(b=>{const m=miles(a,b);if(m<25)out.push({a,b,mi:m,gap:gapDays(a,b),ref:REFPAIRS.has(a.id+'|'+b.id),conf:[a.conf,b.conf].includes('town')?'town':([a.conf,b.conf].includes('partial')?'partial':'verified')})}));
  return out}
let sortBy='mi';
function ranked(){const o=overlaps();o.sort(sortBy==='mi'?(x,y)=>x.mi-y.mi||x.gap-y.gap:(x,y)=>x.gap-y.gap||x.mi-y.mi);o.forEach((x,i)=>x.rank=i+1);return o}

/* ---------- drawing ---------- */
function drawProjects(list,animate){
  const pts=list.filter(p=>p.lat!=null);
  const lines=pts.filter(p=>p.eps.filter(e=>e.lat!=null).length===2);
  lnG.selectAll('path').data(lines,d=>d.id).join(en=>en.append('path').attr('fill','none').attr('stroke-linecap','round').attr('vector-effect','non-scaling-stroke').attr('stroke-width',2.2).attr('stroke',d=>`var(--${d.u.toLowerCase()})`).attr('stroke-opacity',d=>d.conf==='verified'?0.9:0.45)
    .attr('d',d=>{const e=d.eps.filter(x=>x.lat!=null);return path({type:'LineString',coordinates:e.map(x=>[x.lon,x.lat])})}).style('cursor','pointer').on('click',(ev,d)=>showProject(d)),up=>up,ex=>ex);
  pinG.selectAll('circle').data(pts,d=>d.id).join(en=>{const c=en.append('circle').attr('cx',d=>proj([d.lon,d.lat])[0]).attr('cy',d=>proj([d.lon,d.lat])[1])
      .attr('fill',d=>d.conf==='verified'?`var(--${d.u.toLowerCase()})`:'var(--panel)').attr('stroke',d=>d.conf==='verified'?'var(--panel)':`var(--${d.u.toLowerCase()})`)
      .style('cursor','pointer').on('click',(ev,d)=>showProject(d));c.append('title').text(d=>d.label);
      if(animate)c.attr('r',0).transition().duration(350).attr('r',4.2/k);return c},up=>up,ex=>ex);
  lblG.selectAll('text').data(pts,d=>d.id).join(en=>en.append('text').attr('x',d=>proj([d.lon,d.lat])[0]).attr('y',d=>proj([d.lon,d.lat])[1]).attr('text-anchor','middle').attr('fill','var(--ink)').style('paint-order','stroke').attr('stroke','var(--focus)').text(d=>d.label.length>38?d.label.slice(0,36)+'…':d.label),up=>up,ex=>ex);
  sizes();
}
function applyVisibility(){pinG.selectAll('circle').style('display',d=>visible(d)?null:'none');lnG.selectAll('path').style('display',d=>visible(d)?null:'none');lblG.selectAll('text').style('display',d=>visible(d)&&k>9?null:'none')}
function drawOverlaps(list,animate){
  const sel=zG.selectAll('g.ov').data(list,d=>d.a.id+'|'+d.b.id);sel.exit().remove();
  const en=sel.enter().append('g').attr('class','ov').style('cursor','pointer').on('click',(ev,d)=>openOpp(d));
  en.append('path').attr('class','ring').attr('fill','var(--zone)').attr('fill-opacity',0.08).attr('stroke','var(--zone)').attr('stroke-dasharray','4 3').attr('vector-effect','non-scaling-stroke').style('display','none')
    .attr('d',d=>path(d3.geoCircle().center([d.a.lon,d.a.lat]).radius(25*1.609/111.2)()));
  en.append('path').attr('fill','none').attr('stroke','var(--zone-ink)').attr('stroke-width',1.6).attr('stroke-dasharray','3 2').attr('vector-effect','non-scaling-stroke')
    .attr('d',d=>path({type:'LineString',coordinates:[[d.a.lon,d.a.lat],[d.b.lon,d.b.lat]]}));
  en.append('text').attr('text-anchor','middle').attr('fill','var(--zone-ink)').style('font-weight',600).style('paint-order','stroke').attr('stroke','var(--focus)')
    .attr('x',d=>(proj([d.a.lon,d.a.lat])[0]+proj([d.b.lon,d.b.lat])[0])/2).attr('y',d=>(proj([d.a.lon,d.a.lat])[1]+proj([d.b.lon,d.b.lat])[1])/2).text(d=>d.mi.toFixed(1)+' mi');
  if(animate)en.style('opacity',0).transition().delay((d,i)=>i*120).duration(400).style('opacity',1);
  sizes();
}

/* ---------- pipeline panel ---------- */
const AGENTS=[
{id:'exD',n:'Extractor, Dominion',r:'Reads the 44-project PDF'},
{id:'exG',n:'Extractor, Georgia',r:'Reads the ten-year plan table and project pages'},
{id:'geo',n:'Geocoder',r:'Matches endpoints to known coordinates or towns'},
{id:'val',n:'Validator',r:'Checks dates, costs, and locations'},
{id:'eng',n:'Overlap engine',r:'Center-to-center miles and day gaps. Plain code',tool:1},
{id:'refc',n:'Reference checker',r:"Re-derives the sponsor's six overlaps",tool:1},
{id:'ana',n:'Analyst',r:'Ranks and writes each pair up'}];
const agentEl={};
function renderAgents(){const ul=$('#agents');ul.innerHTML='';AGENTS.forEach(a=>{const li=document.createElement('li');if(a.tool)li.classList.add('tool');li.innerHTML=`<i class="st"></i><span>${a.n}<span class="role">${a.r}</span></span><span class="ct"></span>`;ul.appendChild(li);agentEl[a.id]={li,c:0}})}
function agent(id,state,set){const a=agentEl[id];a.li.classList.remove('work','done');if(state)a.li.classList.add(state);if(set!=null){a.li.querySelector('.ct').textContent=set}}
const nD=ALL.filter(p=>p.u==='DESC').length,nG=ALL.filter(p=>p.u==='GPC').length;
function renderSources(){$('#srcs').innerHTML=`
  <div class="srcrow"><b>Dominion Energy SC</b><span>Planned transmission projects $2M+, 2024–2028. <span id="cD">0</span> of ${nD} read</span><div class="bar"><i id="bD"></i></div></div>
  <div class="srcrow"><b>Georgia Power IRP, Vol. 3</b><span>2024 GA ITS Ten-Year Plan, 2025–2034. <span id="cG">0</span> of ${nG} read</span><div class="bar"><i id="bG"></i></div></div>
  <div class="srcrow"><b>Sperry sample</b><span>Projects_Overlaps.xlsx: 10 projects, 6 overlaps with coordinates</span></div>`}
function log(t){const l=$('#log');const d=document.createElement('div');d.textContent=t;l.prepend(d)}
function renderChecks(n){const c=$('#checks');c.classList.remove('empty');c.innerHTML=CHECKS.slice(0,n).map(x=>`<div class="chk ${x.level}"><b>${esc(x.title)}</b>${esc(x.detail)}<span>${esc(x.src)}</span></div>`).join('')}
function renderRef(){const r=$('#ref');r.classList.remove('empty');const pass=TESTS.filter(t=>t.ok).length;
  r.innerHTML=`<p style="margin:0 0 6px;font-size:13px"><span class="pass">${pass} of ${TESTS.length} match</span> the sponsor's distances and day gaps exactly.</p><table class="reft"><tr><th>Pair</th><th>Sponsor</th><th>Tandem</th><th>Days</th></tr>${TESTS.map(t=>`<tr><td>${t.a} / ${t.b}</td><td>${t.exp_d} mi</td><td>${t.got_d} mi</td><td>${t.got_g}</td></tr>`).join('')}</table>`}
function renderUnloc(){const u=ALL.filter(p=>p.lat==null&&(p.u==='DESC'||F.all||['GPC','SAV'].includes(p.sponsor)));const el=$('#unloc');el.classList.remove('empty');
  el.innerHTML=`<div class="unl"><b style="color:var(--ink)">${u.length} projects</b> need a location lookup (OpenStreetMap Overpass in the full build). For example: ${u.slice(0,8).map(p=>esc(p.label)).join('; ')}.</div>`}

/* ---------- run ---------- */
let running=false,timers=[],done=false,sel=null;
function later(ms,fn){timers.push(setTimeout(fn,ms))}
function reset(){timers.forEach(clearTimeout);timers=[];zG.selectAll('*').remove();lnG.selectAll('*').remove();pinG.selectAll('*').remove();lblG.selectAll('*').remove();
  renderAgents();renderSources();$('#log').innerHTML='';$('#checks').innerHTML='';$('#ref').innerHTML='';$('#unloc').innerHTML='';$('#ticker').textContent='';done=false;sel=null;renderList()}
function finish(){timers.forEach(clearTimeout);timers=[];
  renderSources();$('#cD').textContent=nD;$('#cG').textContent=nG;$('#bD').style.width='100%';$('#bG').style.width='100%';
  AGENTS.forEach(a=>agent(a.id,'done'));agent('exD','done',nD);agent('exG','done',nG);agent('geo','done',ALL.filter(p=>p.lat!=null).length);agent('val','done',CHECKS.length);agent('refc','done',TESTS.filter(t=>t.ok).length+'/'+TESTS.length);
  drawProjects(ALL,false);applyVisibility();const o=ranked();drawOverlaps(o,false);agent('eng','done',o.length);agent('ana','done',o.length);
  renderChecks(CHECKS.length);renderRef();renderUnloc();$('#ticker').textContent='All sources read.';
  running=false;done=true;$('#run').textContent='Run again';$('#skip').hidden=true;renderList();$('#overlay').style.display='none'}
function run(){if(running)return;running=true;reset();$('#overlay').style.display='none';$('#run').textContent='Running…';$('#skip').hidden=false;
  fitGeo(SE,0.92,1400);log('Opened both filings and the sponsor sample.');
  let t=1400;const D=ALL.filter(p=>p.u==='DESC'),G=ALL.filter(p=>p.u==='GPC');
  agent('exD','work');
  D.forEach((p,i)=>later(t+i*70,()=>{$('#cD').textContent=i+1;$('#bD').style.width=((i+1)/nD*100)+'%';$('#ticker').textContent=`${p.src}: ${p.label}`;agent('geo','work');if(visible(p))drawProjects([p],true)}));
  t+=D.length*70;later(t,()=>{agent('exD','done',nD);agent('exG','work');log(`Extractor, Dominion: ${nD} projects with IDs, dates, yearly costs, and descriptions.`)});
  G.forEach((p,i)=>later(t+i*22,()=>{$('#cG').textContent=i+1;$('#bG').style.width=((i+1)/nG*100)+'%';$('#ticker').textContent=`${p.src}: ${p.label}`;if(visible(p))drawProjects([p],true)}));
  t+=G.length*22;
  later(t,()=>{agent('exG','done',nG);agent('geo','done',ALL.filter(p=>p.lat!=null).length);applyVisibility();renderUnloc();
    log(`Extractor, Georgia: ${nG} projects with start dates, need dates, and sponsors.`);log(`Geocoder: ${ALL.filter(p=>p.conf==='verified').length} placed with sponsor coordinates, ${ALL.filter(p=>p.conf==='town'||p.conf==='partial').length} at town level, ${ALL.filter(p=>p.lat==null).length} still unlocated.`);agent('val','work')});
  CHECKS.forEach((c,i)=>later(t+300+i*260,()=>{renderChecks(i+1);agent('val','work',i+1);log(`Validator: ${c.title.toLowerCase()}.`)}));
  t+=300+CHECKS.length*260;
  later(t,()=>{agent('val','done');agent('eng','work');log('Overlap engine: comparing every Dominion project with every Georgia project, center to center.')});
  t+=500;
  later(t,()=>{const o=ranked();agent('eng','done',o.length);drawOverlaps(o,true);log(`Overlap engine: ${o.length} pairs under 25 miles.`);agent('refc','work')});
  t+=1200;
  later(t,()=>{renderRef();agent('refc','done',TESTS.filter(x=>x.ok).length+'/'+TESTS.length);log("Reference checker: all six of the sponsor's overlaps reproduced exactly.");agent('ana','work')});
  t+=900;
  later(t,()=>{finish();log('Done. Pick a pair to see the details.')});
}
$('#run').onclick=run;$('#run2').onclick=run;$('#skip').onclick=()=>{finish();log('Skipped to results.')};

/* ---------- right panel ---------- */
function renderList(){const R=$('#right');
  if(!done){R.innerHTML=`<h2>Coordination opportunities</h2><p class="empty">${running?'Reading filings…':'Run the pipeline to find Dominion and Georgia projects planned under 25 miles apart.'}</p>`;return}
  const o=ranked();drawOverlaps(o,false);zG.selectAll('g.ov').style('opacity',1).select('.ring').style('display','none');
  const shown=ALL.filter(visible).length;
  R.innerHTML=`<h2>Coordination opportunities</h2>
  <div class="stats"><div><b>${nD+nG}</b>projects read</div><div><b>${shown}</b>on the map</div><div><b>${o.length}</b>under 25 mi</div></div>
  <div class="sort">Sort <button id="sMi" aria-pressed="${sortBy==='mi'}">Closest</button><button id="sGap" aria-pressed="${sortBy==='gap'}">Nearest in time</button></div>
  <div id="list"></div><button id="csv" style="margin-top:10px">Copy overlap table (sponsor format)</button><div id="csvOut"></div>`;
  $('#sMi').onclick=()=>{sortBy='mi';renderList()};$('#sGap').onclick=()=>{sortBy='gap';renderList()};
  const L=$('#list');if(!o.length)L.innerHTML='<p class="empty">No pairs under 25 miles with these filters.</p>';
  o.forEach(x=>{const d=document.createElement('div');d.className='opp';d.tabIndex=0;d.setAttribute('role','button');
    d.innerHTML=`<span class="rk">${x.rank}</span><span><span class="t">${esc(x.a.label)}<br>${esc(x.b.label)}</span><br><span class="m"><span class="pill">${x.mi.toFixed(2)} mi</span><span class="pill time">${x.gap.toLocaleString()} days apart</span>${x.conf!=='verified'?`<span class="pill conf">${CONF[x.conf]}</span>`:''}${x.ref?'<span class="pill ref">In sponsor sample</span>':''}</span></span>`;
    d.onclick=()=>openOpp(x);d.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();openOpp(x)}};L.appendChild(d)});
  $('#csv').onclick=()=>{const rows=[['overlap_id','distance_mi','time_gap (day)','utility_a','project_id_a','project_name_a','utility_b','project_id_b','project_name_b','location_confidence']].concat(o.map((x,i)=>['OVL_'+(i+1),x.mi.toFixed(2),x.gap,'Dominion Energy South Carolina',x.a.id,x.a.name,'Georgia Power',x.b.id,x.b.name,CONF[x.conf]]));
    const csv=rows.map(r=>r.map(v=>`"${String(v).replace(/"/g,'""')}"`).join(',')).join('\n');
    const ok=()=>{$('#csvOut').innerHTML='<p class="note">Copied.</p>'};const fb=()=>{$('#csvOut').innerHTML=`<p class="note">Copy it from here:</p><textarea style="width:100%;height:120px;font-size:12px" readonly>${esc(csv)}</textarea>`};
    try{navigator.clipboard.writeText(csv).then(ok,fb)}catch(e){fb()}};
}
function windowOf(p){const end=d0(p.isd);let start=p.start?d0(p.start):null;return{start,end}}
function gantt(a,b){const wa=windowOf(a),wb=windowOf(b);const all=[wa.start,wa.end,wb.start,wb.end].filter(Boolean);
  const y0=Math.min(...all.map(d=>d.getFullYear())),y1=Math.max(...all.map(d=>d.getFullYear()))+1;const w=320,h=78,x=d3.scaleTime([new Date(y0,0,1),new Date(y1,0,1)],[70,w-6]);
  let s=`<svg class="gantt" viewBox="0 0 ${w} ${h}" role="img" aria-label="Build windows">`;const step=Math.max(1,Math.ceil((y1-y0)/5));
  for(let yy=y0;yy<=y1;yy+=step)s+=`<line x1="${x(new Date(yy,0,1))}" x2="${x(new Date(yy,0,1))}" y1="4" y2="58" stroke="var(--line)"/><text x="${x(new Date(yy,0,1))}" y="72" text-anchor="middle" font-size="11" fill="var(--muted)">${yy}</text>`;
  const lo=new Date(Math.max(wa.start||wa.end,wb.start||wb.end)),hi=new Date(Math.min(wa.end,wb.end));
  if(wa.start&&wb.start&&hi>lo)s+=`<rect x="${x(lo)}" y="4" width="${x(hi)-x(lo)}" height="54" fill="var(--zone)" fill-opacity=".25"/>`;
  const bar=(wd,y,c)=>wd.start?`<rect x="${x(wd.start)}" y="${y}" width="${Math.max(3,x(wd.end)-x(wd.start))}" height="14" rx="3" fill="${c}"/>`:`<rect x="${x(wd.end)-3}" y="${y}" width="6" height="14" rx="2" fill="${c}"/>`;
  s+=`<text x="0" y="22" font-size="12" fill="var(--ink)">Dominion</text>${bar(wa,12,'var(--desc)')}<text x="0" y="48" font-size="12" fill="var(--ink)">Georgia</text>${bar(wb,38,'var(--gpc)')}</svg>`;
  const overlap=wa.start&&wb.start&&hi>lo;return{svg:s,overlap}}
function insight(x,ov){const yrs=x.gap/365;
  if(ov)return `Both projects are under construction at the same time, ${x.mi.toFixed(1)} miles apart. This is the strongest case for sharing crews, equipment, staging yards, and outage planning.`;
  if(yrs<2)return `Their in-service dates are ${x.gap} days apart. A modest schedule shift could put both crews in the area at once.`;
  return `Their in-service dates are about ${Math.round(yrs)} years apart, so crews won't overlap. The value here is shared information: surveys, right-of-way records, and designing the later project around the earlier one.`}
function costBlock(x,ov){const a=x.a;if(a.cost==null)return '';const lo=a.cost*0.03,hi=a.cost*0.06;
  return `<table class="cost"><tr><td>Dominion project cost (public)</td><td>${fmt$(a.cost)}</td></tr><tr><td>Georgia project cost</td><td>Redacted in the filing</td></tr>
  ${ov?`<tr><td>Possible savings from shared mobilization and staging</td><td>${fmt$(lo)}–${fmt$(hi)}</td></tr>`:''}</table>
  <p class="note">${ov?"Assumes mobilization and staging are 3–6% of construction cost and one mobilization is avoided. That range is a placeholder until the cost agent cites an industry source. Dominion's side only, because Georgia's costs are redacted.":"No overlapping build window, so no crew or staging savings are claimed."}</p>`}
function provLine(p){const e=p.eps.map(e=>`${esc(title(e.name))}${e.lat!=null?` (${e.lat.toFixed(4)}, ${e.lon.toFixed(4)}${e.how==='town'?`, town-level: ${esc(e.town)}`:e.how==='verified'?', sponsor coordinates':''})`:' (not located)'}`).join('; ');
  return `<div><b style="color:var(--${p.u.toLowerCase()})">${esc(p.label)}</b><br>${esc(p.src)}. Endpoints: ${e}.</div>`}
function openOpp(x){if(!done)return;sel=x;const R=$('#right');const G=gantt(x.a,x.b);
  R.innerHTML=`<button class="back" id="back">← All opportunities</button>
  <h2>Opportunity ${x.rank}</h2>
  <div class="pair"><div class="pj" style="border-color:var(--desc)"><b>${esc(x.a.label)}</b><span>Dominion Energy SC. ${esc(x.a.status)}. In service ${fmtD(x.a.isd)}</span></div><div class="pj" style="border-color:var(--gpc)"><b>${esc(x.b.label)}</b><span>Georgia, sponsor ${esc(x.b.sponsor)}. Need date ${fmtD(x.b.isd)}</span></div></div>
  <div><span class="big">${x.mi.toFixed(2)} mi</span> center to center</div>
  <div class="m" style="font-size:13px;color:var(--muted)">${x.gap.toLocaleString()} days between in-service dates. ${CONF[x.conf]}.${x.ref?' Matches the sponsor sample.':''}</div>
  <h3>Build windows</h3>${G.svg}<p class="note">Dominion bars start at the first year with budgeted spending. Georgia bars use the start date on the project's page.</p>
  <h3>What it means</h3><p style="font-size:13.5px;margin:0">${insight(x,G.overlap)}</p>
  <h3>Cost</h3>${costBlock(x,G.overlap)}
  <h3>From the filings</h3><div class="quote">${esc(x.a.desc)}</div><div class="quote">${esc(x.b.desc)}</div>
  <h3>Where this came from</h3><div class="prov">${provLine(x.a)}${provLine(x.b)}</div>`;
  $('#back').onclick=()=>{sel=null;renderList();fitGeo(SE,0.92,900)};
  zG.selectAll('g.ov').style('opacity',z=>z===x?1:0.2).select('.ring').style('display',z=>z===x?null:'none');
  fitGeo(d3.geoCircle().center([(x.a.lon+x.b.lon)/2,(x.a.lat+x.b.lat)/2]).radius(Math.max(x.mi*1.609,6)/111.2*1.4)(),0.8,1000);
}
function showProject(p){if(!done)return;const R=$('#right');const o=ranked().filter(x=>x.a===p||x.b===p);sel=null;
  R.innerHTML=`<button class="back" id="back">← All opportunities</button><h2>${esc(p.label)}</h2>
  <p class="prov">${NAME[p.u]}${p.u==='GPC'?`, sponsor ${esc(p.sponsor)}`:''}. ${p.u==='DESC'?'In service':'Need date'} ${fmtD(p.isd)}${p.start?`, starts ${fmtD(p.start)}`:''}. ${CONF[p.conf]}.${p.cost!=null?` Cost ${fmt$(p.cost)}.`:''}</p>
  <div class="quote">${esc(p.desc)}</div><div class="prov">${provLine(p)}</div>
  <h3>Pairs under 25 mi</h3>${o.length?o.map(x=>`<div class="opp" data-r="${x.rank}"><span class="rk">${x.rank}</span><span><span class="t">${esc((x.a===p?x.b:x.a).label)}</span><br><span class="m">${x.mi.toFixed(2)} mi, ${x.gap.toLocaleString()} days apart</span></span></div>`).join(''):'<p class="empty">Nothing from the other utility within 25 miles. Most projects look like this.</p>'}`;
  $('#back').onclick=()=>renderList();R.querySelectorAll('.opp').forEach(el=>el.onclick=()=>openOpp(ranked()[+el.dataset.r-1]))}

function refilter(){if(!done)return;applyVisibility();renderUnloc();renderList();agent('eng','done',ranked().length)}
$('#fSp').onchange=e=>{F.all=e.target.checked;refilter()};
$('#fTown').onchange=e=>{F.town=e.target.checked;refilter()};
$('#fPast').onchange=e=>{F.past=e.target.checked;refilter()};
reset();
})();
