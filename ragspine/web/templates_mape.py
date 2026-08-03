"""HTML za /ui/mape — poveži mrežne mape (NAS/Windows) i dodijeli im uloge.
Reuse design-system shell iz templates_ui. Svi podaci iz API-ja preko textContent
(XSS-safe); bez vanjskih resursa."""
from ragspine.web.templates_ui import page_shell

_MAPE_JS = """
function $(id){ return document.getElementById(id); }
var curPath = null;

function showErr(msg){ var e=$('mape-error'); e.textContent=msg; e.style.display='block'; }
function clearErr(){ $('mape-error').style.display='none'; }

function mkBtn(text, cls, onclick){
  var b=document.createElement('button'); b.type='button'; b.className=cls;
  b.textContent=text; b.addEventListener('click', onclick); return b;
}

async function loadBrowse(path){
  clearErr();
  var url = '/folders/browse' + (path ? ('?path=' + encodeURIComponent(path)) : '');
  var res = await fetch(url, {credentials:'same-origin'});
  if(!res.ok){ var e=await res.json().catch(function(){return{};}); showErr('Greška: '+(e.detail||res.status)); return; }
  var d = await res.json();
  curPath = d.path;
  $('cur-path').textContent = d.path || 'Korijeni (odaberi disk/mapu)';
  var list = $('dir-list'); list.textContent='';
  if(!d.path){
    if(!d.roots.length){ var p=document.createElement('p'); p.className='meta';
      p.textContent='Nema konfiguriranih korijena (RAGSPINE_MOUNT_ROOTS).'; list.appendChild(p); }
    d.roots.forEach(function(r){
      list.appendChild(mkBtn('\\uD83D\\uDCC1 '+r, 'btn btn-ghost', function(){ loadBrowse(r); }));
    });
  } else {
    list.appendChild(mkBtn('\\u2190 natrag', 'btn btn-ghost', function(){ loadBrowse(d.parent); }));
    d.dirs.forEach(function(name){
      list.appendChild(mkBtn('\\uD83D\\uDCC1 '+name, 'btn btn-ghost',
        function(){ loadBrowse(d.path + '/' + name); }));
    });
    if(!d.dirs.length){ var p=document.createElement('span'); p.className='meta';
      p.textContent=' (nema podmapa)'; list.appendChild(p); }
  }
  $('assign').style.display = d.path ? 'block' : 'none';
  if(d.path){ $('f-label').value = d.path.split('/').pop() || d.path; }
}

async function loadRegistered(){
  var res = await fetch('/folders', {credentials:'same-origin'});
  if(!res.ok) return;
  var rows = await res.json();
  var body = $('reg-body'); body.textContent='';
  if(!rows.length){ var tr=document.createElement('tr'); var td=document.createElement('td');
    td.colSpan=5; td.className='meta'; td.textContent='Nijedna mapa još nije dodana.';
    tr.appendChild(td); body.appendChild(tr); return; }
  rows.forEach(function(r){
    var tr=document.createElement('tr');
    var tdP=document.createElement('td'); tdP.style.fontFamily='var(--font-mono)';
    tdP.style.fontSize='.8rem'; tdP.textContent=r.path; tr.appendChild(tdP);
    var tdR=document.createElement('td'); var chip=document.createElement('span');
    chip.className='chip'; chip.textContent=r.role; tdR.appendChild(chip); tr.appendChild(tdR);
    var tdS=document.createElement('td'); tdS.className='meta'; tdS.style.fontSize='.75rem';
    tdS.textContent=r.last_synced ? r.last_synced.slice(0,16) : '—'; tr.appendChild(tdS);
    var tdE=document.createElement('td');
    tdE.appendChild(mkBtn(r.enabled ? 'DA' : 'NE', 'btn btn-ghost', function(){
      fetch('/folders/'+r.id, {method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'}, body:JSON.stringify({enabled: r.enabled?0:1})})
        .then(loadRegistered); })); tr.appendChild(tdE);
    var tdX=document.createElement('td');
    tdX.appendChild(mkBtn('makni', 'btn btn-danger', function(){
      fetch('/folders/'+r.id, {method:'DELETE', credentials:'same-origin'}).then(loadRegistered); }));
    tr.appendChild(tdX);
    body.appendChild(tr);
  });
}

$('f-add').addEventListener('click', async function(){
  if(!curPath){ showErr('Prvo uđi u mapu.'); return; }
  var res = await fetch('/folders', {method:'POST', credentials:'same-origin',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({path: curPath, role: $('f-role').value, label: $('f-label').value})});
  if(!res.ok){ var e=await res.json().catch(function(){return{};}); showErr('Greška: '+(e.detail||res.status)); return; }
  clearErr(); loadRegistered();
});

$('sync-now').addEventListener('click', async function(){
  var msg=$('sync-msg'); msg.textContent='Sinkroniziram…'; this.disabled=true;
  try{
    var res=await fetch('/folders/sync', {method:'POST', credentials:'same-origin'});
    if(!res.ok){ msg.textContent='Greška: '+res.status; return; }
    var d=await res.json();
    msg.textContent='Gotovo: '+d.ingested+' novih, '+d.superseded+' zamijenjenih, '+d.skipped+' preskočeno'
      + (d.errors&&d.errors.length ? (' · '+d.errors.length+' grešaka') : '');
    loadRegistered();
  }catch(e){ msg.textContent='Greška u komunikaciji.'; }
  finally{ this.disabled=false; }
});

loadBrowse(null);
loadRegistered();
"""


def mape_page() -> str:
    body = f"""<h1>Mrežne mape</h1>
<p class="meta"><a href="/ui/postavke">← Postavke</a> · Poveži NAS / Windows share, uđi u mapu pa joj dodijeli ulogu. RAGSPINE ih čita samo za čitanje.</p>
<p class="meta" style="background:var(--surface-2);border:1px solid var(--border);border-radius:8px;padding:.6rem .8rem">
📌 Za <b>propise</b> dodaj <b>jednu glavnu mapu</b> (npr. <code>Propisi</code>). Unutar nje neka su podmape po vrsti — <code>Zakoni</code>, <code>Pravilnici</code>, <code>Uredbe</code>, <code>Mišljenja</code>, <code>NN</code> — RAGSPINE prepozna vrstu i autoritet po nazivu podmape.</p>
<div id="mape-error" class="chip bad" style="display:none;margin-bottom:1rem"></div>
<div class="grid">
  <div class="card">
    <h2>Pregled mrežnih mapa</h2>
    <div id="cur-path" class="meta" style="font-family:var(--font-mono)">Učitavanje…</div>
    <div id="dir-list" style="margin:.6rem 0;display:flex;flex-direction:column;gap:.35rem;align-items:flex-start"></div>
    <div id="assign" style="display:none;border-top:1px solid var(--border);padding-top:.75rem">
      <label for="f-role">Uloga ove mape</label>
      <select id="f-role">
        <option value="propisi">propisi (zakoni / pravilnici / uredbe)</option>
        <option value="klijenti">klijenti</option>
        <option value="ostalo">ostalo</option>
      </select>
      <label for="f-label" style="display:block;margin-top:.4rem">Naziv</label>
      <input id="f-label" type="text">
      <button type="button" class="btn" id="f-add" style="margin-top:.5rem">Dodijeli ulogu ovoj mapi</button>
    </div>
  </div>
  <div class="card">
    <h2>Registrirane mape</h2>
    <div style="margin-bottom:.6rem;display:flex;gap:.5rem;align-items:center">
      <button type="button" class="btn" id="sync-now">Sinkroniziraj sad</button>
      <span id="sync-msg" class="meta"></span>
    </div>
    <table class="ledger">
      <thead><tr><th>Putanja</th><th>Uloga</th><th>Sinkr.</th><th>Uklj.</th><th></th></tr></thead>
      <tbody id="reg-body"><tr><td colspan="5" class="meta">Učitavanje…</td></tr></tbody>
    </table>
  </div>
</div>
<script>{_MAPE_JS}</script>"""
    return page_shell("Mrežne mape", body, active="postavke")
