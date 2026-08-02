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
    td.colSpan=4; td.className='meta'; td.textContent='Nijedna mapa još nije dodana.';
    tr.appendChild(td); body.appendChild(tr); return; }
  rows.forEach(function(r){
    var tr=document.createElement('tr');
    var tdP=document.createElement('td'); tdP.style.fontFamily='var(--font-mono)';
    tdP.style.fontSize='.8rem'; tdP.textContent=r.path; tr.appendChild(tdP);
    var tdR=document.createElement('td'); var chip=document.createElement('span');
    chip.className='chip'; chip.textContent=r.role; tdR.appendChild(chip); tr.appendChild(tdR);
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

loadBrowse(null);
loadRegistered();
"""


def mape_page() -> str:
    body = f"""<h1>Mape</h1>
<p class="meta">Poveži mrežne mape (NAS / Windows share). Uđi u mapu pa joj dodijeli ulogu
(zakoni, klijenti…). RAGSPINE ih čita samo za čitanje.</p>
<div id="mape-error" class="chip bad" style="display:none;margin-bottom:1rem"></div>
<div class="grid">
  <div class="card">
    <h2>Pregled mrežnih mapa</h2>
    <div id="cur-path" class="meta" style="font-family:var(--font-mono)">Učitavanje…</div>
    <div id="dir-list" style="margin:.6rem 0;display:flex;flex-direction:column;gap:.35rem;align-items:flex-start"></div>
    <div id="assign" style="display:none;border-top:1px solid var(--border);padding-top:.75rem">
      <label for="f-role">Uloga ove mape</label>
      <select id="f-role">
        <option value="zakoni">zakoni (propisi)</option>
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
    <table class="ledger">
      <thead><tr><th>Putanja</th><th>Uloga</th><th>Uklj.</th><th></th></tr></thead>
      <tbody id="reg-body"><tr><td colspan="4" class="meta">Učitavanje…</td></tr></tbody>
    </table>
  </div>
</div>
<script>{_MAPE_JS}</script>"""
    return page_shell("Mape", body, active="mape")
